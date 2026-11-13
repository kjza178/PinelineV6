"""So sánh ASR FireRed trên audio gốc (ASR_USE_DEMUCS=0) và trên giọng tách Demucs (=1).

Fix V2 2026-11-12 asr-demucs-default đổi mặc định sang =1. Script này đo thực tế trên vài job
đã có asr.json trong D:\\PinelineV2_work (CHỈ ĐỌC), chạy trên BẢN SAO ở
D:\\PinelineV6_work\\asr_demucs_cmp\\<job>\\{off,on}\\ bằng đúng code V2 (firered_asr.py ->
asr.resolve_asr_source -> separate.run_separate), như pipeline thật.

CẢNH BÁO: dùng GPU (Demucs ~4-6GB + FireRed). CHỈ chạy khi V2 RẢNH (hàng đợi :8801 trống).
Không gọi HTTP cổng nào, không kill/đổi preset :8000/:8083. Mặc định SKIP_VRAM_FREE=1
(không stop model Ollama) — nếu GPU thiếu chỗ Demucs sẽ lỗi và báo cáo ghi rõ "FALLBACK".

Cách dùng (xem asr_demucs_compare.bat):
  python scripts/asr_demucs_compare.py                    # dry-run: in job sẽ chọn
  python scripts/asr_demucs_compare.py run [--jobs a,b] [--minutes 12] [--ocr] [--yes]
  python scripts/asr_demucs_compare.py report             # chấm lại từ kết quả đã chạy
  python scripts/asr_demucs_compare.py golden [--yes]     # vocals cho 4 cửa sổ golden (CAM++)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
V6 = HERE.parent
DEF_V2 = Path(r"Z:\PinelineV2")
DEF_SRC = Path(r"D:\PinelineV2_work")
DEF_OUT = Path(r"D:\PinelineV6_work\asr_demucs_cmp")
DEF_REPORT = V6 / "docs" / "reviews" / "2026-11-12-asr-demucs-compare.md"
VOICE_EXP = V6 / "scratch" / "voice_exp"
GOLDEN_JOBS = ["32da3d8f225b", "9dedde16e35b", "d1e00b9e7393", "f0834acf0cc0"]
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".flv", ".ts"}
_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


# ───────────────────────────── tiện ích ─────────────────────────────

def log(msg: str) -> None:
    print(msg, flush=True)


def jload(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def find_video(job_dir: Path) -> Path | None:
    m = jload(job_dir / "manifest.json", {}) or {}
    vp = ((m.get("stages") or {}).get("download") or {}).get("video_path")
    if vp and Path(vp).is_file():
        return Path(vp)
    for c in sorted(job_dir.glob("source.*")):
        if c.suffix.lower() in VIDEO_EXT:
            return c
    return None


def warn_gpu_and_busy(src_work: Path, ignore_busy: bool) -> None:
    log("=" * 78)
    log("CẢNH BÁO: script dùng GPU (Demucs + FireRed ASR). CHỈ chạy khi V2 RẢNH.")
    log("Không gọi HTTP, không kill/đổi preset llama-server :8083 / Hongguo :8000.")
    log("=" * 78)
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total",
                            "--format=csv,noheader"], capture_output=True, text=True, timeout=20)
        if r.stdout.strip():
            log(f"[gpu] {r.stdout.strip()}")
    except Exception:  # noqa: BLE001
        pass
    busy = []
    now = time.time()
    for mf in src_work.glob("*/manifest.json"):
        st = ((jload(mf, {}) or {}).get("stages") or {})
        for name, s in st.items():
            if isinstance(s, dict) and s.get("status") == "running":
                upd = s.get("updated_at")
                try:
                    ts = float(upd) if upd is not None else mf.stat().st_mtime
                except (TypeError, ValueError, OSError):
                    ts = now
                recent = (now - ts) < 20 * 60   # running mồ côi lâu không cập nhật -> bỏ qua
                if recent:
                    busy.append(f"{mf.parent.name}:{name}")
    if busy:
        log(f"[busy] V2 có stage đang chạy: {', '.join(busy[:8])}")
        if not ignore_busy:
            raise SystemExit("V2 đang bận -> dừng. (Chắc chắn rảnh/stage mồ côi: thêm --ignore-busy)")


def confirm(yes: bool, what: str) -> None:
    if yes:
        return
    ans = input(f"{what}\nGõ YES để chạy: ").strip()
    if ans != "YES":
        raise SystemExit("Huỷ.")


def norm_text(s: str) -> str:
    return _PUNCT.sub("", s or "")


def lev(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def cer(hyp: str, ref: str) -> float | None:
    ref = norm_text(ref)
    if not ref:
        return None
    return lev(norm_text(hyp), ref) / len(ref)


def rows_in(rows: list, sec: float) -> list:
    return [r for r in (rows or []) if isinstance(r, dict) and float(r.get("start") or 0) < sec]


def text_of(rows: list, key: str = "text") -> str:
    return "".join((r.get(key) or r.get("zh") or r.get("text") or "") for r in rows)


def match_rows(a: list, b: list, min_iou: float = 0.3) -> list[tuple[dict, dict, float]]:
    out = []
    used = set()
    for ra in a:
        s0, e0 = float(ra["start"]), float(ra["end"])
        best, bi, biou = None, None, 0.0
        for k, rb in enumerate(b):
            s1, e1 = float(rb["start"]), float(rb["end"])
            inter = min(e0, e1) - max(s0, s1)
            if inter <= 0:
                continue
            iou = inter / (max(e0, e1) - min(s0, s1))
            if iou > biou:
                best, bi, biou = rb, k, iou
        if best is not None and biou >= min_iou and bi not in used:
            used.add(bi)
            out.append((ra, best, biou))
    return out


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


# ───────────────────────────── chọn job ─────────────────────────────

def pick_jobs(src_work: Path, want: list[str] | None, n: int) -> list[Path]:
    cands = []
    for d in sorted(src_work.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        if want and d.name not in want:
            continue
        asr = jload(d / "asr.json")
        if not isinstance(asr, list) or len(asr) < 20:
            continue
        v = find_video(d)
        if not v:
            continue
        cands.append(d)
    if want:
        return cands
    # ưu tiên: 1 part _pNN + các job khác nhau (bỏ part trùng gốc), mới nhất trước
    cands.sort(key=lambda d: (d / "asr.json").stat().st_mtime, reverse=True)
    parts = [d for d in cands if re.search(r"_p\d\d$", d.name)]
    whole = [d for d in cands if not re.search(r"_p\d\d$", d.name)]
    picked = (parts[:1] + whole)[:n]
    return picked


# ───────────────────────────── chạy ─────────────────────────────

def prepare_copy(job: Path, out: Path, minutes: float) -> tuple[Path, float]:
    """Cắt N phút đầu (stream copy, hình+tiếng) -> out/<job>/src/source.mp4. Trả (video, giây)."""
    video = find_video(job)
    dst = out / job.name / "src"
    dst.mkdir(parents=True, exist_ok=True)
    sv = dst / "source.mp4"
    sec = minutes * 60.0
    if not sv.exists() or sv.stat().st_size < 10000:
        log(f"[copy] {job.name}: cắt {minutes:g} phút đầu từ {video} (chỉ đọc)")
        part = dst / "source.partial.mp4"
        ffmpeg("-i", str(video), "-t", f"{sec:.3f}", "-map", "0:v:0?", "-map", "0:a:0",
               "-c", "copy", "-movflags", "+faststart", str(part))
        os.replace(part, sv)
    return sv, sec


def new_variant(out: Path, job: str, var: str, video: Path, sec: float, fresh: bool) -> Path:
    wd = out / job / var
    if fresh and wd.exists():
        shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True, exist_ok=True)
    mf = wd / "manifest.json"
    if not mf.exists():
        mf.write_text(json.dumps({"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "stages": {
            "download": {"status": "done", "video_path": str(video), "duration_sec": sec}}},
            ensure_ascii=False, indent=2), encoding="utf-8")
    return wd


def run_asr(v2: Path, wd: Path, demucs: bool, device: str, skip_vram: bool) -> dict:
    if (wd / "asr.json").exists() and ((jload(wd / "manifest.json", {}) or {}).get("stages", {})
                                       .get("asr", {}).get("status") == "done"):
        log(f"[asr] {wd}: đã có asr.json -> dùng lại")
        return jload(wd / "_timing.json", {}) or {}
    env = dict(os.environ, ASR_USE_DEMUCS="1" if demucs else "0", PYTHONIOENCODING="utf-8",
               PYTHONUTF8="1", AGENT_BRAIN="0",
               PYTHONPATH=os.pathsep.join(x for x in (str(v2), os.environ.get("PYTHONPATH", "")) if x))
    if skip_vram:
        env["SKIP_VRAM_FREE"] = "1"
    t0 = time.time()
    log(f"[asr] FireRed ({'Demucs vocals' if demucs else 'audio gốc'}) -> {wd}")
    r = subprocess.run([sys.executable, str(v2 / "firered_asr.py"), str(wd), "--device", device],
                       cwd=str(v2), env=env)
    timing = {"sec": round(time.time() - t0, 1), "rc": r.returncode}
    (wd / "_timing.json").write_text(json.dumps(timing), encoding="utf-8")
    if r.returncode != 0:
        log(f"[asr] LỖI mã {r.returncode} ở {wd}")
    return timing


def run_ocr(v2: Path, wd: Path, sec: float) -> None:
    if (wd / "hardsub_cues.json").exists():
        return
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", AGENT_BRAIN="0", PYTHONPATH=str(v2))
    log(f"[ocr] hardsub OCR {sec:.0f}s đầu -> {wd} (không --apply)")
    subprocess.run([sys.executable, str(v2 / "hardsub_asr.py"), str(wd), "--dur", f"{sec:.0f}"],
                   cwd=str(v2), env=env)


def cmd_run(a) -> None:
    v2, src, out = Path(a.v2), Path(a.src_work), Path(a.out)
    jobs = pick_jobs(src, a.jobs, a.n)
    log(f"[plan] {len(jobs)} job: {', '.join(j.name for j in jobs)} · {a.minutes:g} phút đầu · bản sao ở {out}")
    if a.cmd != "run":
        log("(dry-run) thêm 'run' để chạy thật.")
        return
    warn_gpu_and_busy(src, a.ignore_busy)
    confirm(a.yes, f"Sẽ chạy Demucs + FireRed x2 trên {len(jobs)} job (GPU).")
    out.mkdir(parents=True, exist_ok=True)
    for j in jobs:
        video, sec = prepare_copy(j, out, a.minutes)
        for var, dm in (("off", False), ("on", True)):
            wd = new_variant(out, j.name, var, video, sec, a.fresh)
            run_asr(v2, wd, dm, a.device, not a.free_vram)
        if a.ocr:
            run_ocr(v2, out / j.name / "off", sec)
        meta = {"job": j.name, "video": str(find_video(j)), "sec": sec}
        (out / j.name / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    cmd_report(a)


# ───────────────────────────── báo cáo ─────────────────────────────

def clip(src: Path, dst: Path, s: float, e: float) -> None:
    try:
        ffmpeg("-ss", f"{max(0.0, s - 0.5):.3f}", "-i", str(src), "-t", f"{e - s + 1.0:.3f}",
               "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst))
    except Exception as ex:  # noqa: BLE001
        log(f"[clip] {dst.name}: {ex}")


def stats(xs: list[float]) -> str:
    if not xs:
        return "—"
    xs = sorted(xs)
    p90 = xs[min(len(xs) - 1, int(0.9 * len(xs)))]
    return f"TB {statistics.mean(xs):.3f}s · trung vị {statistics.median(xs):.3f}s · p90 {p90:.3f}s"


def cmd_report(a) -> None:
    src, out = Path(a.src_work), Path(a.out)
    lines = ["# So sánh ASR FireRed: audio gốc vs giọng tách Demucs (2026-11-12)", "",
             "Sinh bởi `scripts/asr_demucs_compare.py` (V6). Bản sao ở `" + str(out) + "`; V2 work chỉ đọc.",
             "OFF = `ASR_USE_DEMUCS=0` (audio gốc) · ON = `ASR_USE_DEMUCS=1` (vocals.wav, mặc định mới V2).",
             "CER bỏ dấu câu/khoảng trắng. Câu khớp = chồng thời gian IoU ≥ 0.3.", ""]
    n_jobs = 0
    for jd in sorted(p for p in out.iterdir() if p.is_dir() and (p / "meta.json").exists()):
        meta = jload(jd / "meta.json", {})
        sec = float(meta.get("sec") or 0)
        off = jload(jd / "off" / "asr.json")
        on = jload(jd / "on" / "asr.json")
        lines += [f"## {jd.name} — {sec / 60:.1f} phút đầu", ""]
        if not isinstance(off, list) or not isinstance(on, list):
            lines += ["_Thiếu asr.json OFF/ON — chạy chưa xong/lỗi._", ""]
            continue
        n_jobs += 1
        on_m = (jload(jd / "on" / "manifest.json", {}) or {}).get("stages", {})
        fb = (on_m.get("asr") or {}).get("demucs_fallback")
        sep_ok = (on_m.get("separate") or {}).get("status") == "done" and (jd / "on" / "vocals.wav").exists()
        a_marker = (jload(jd / "on" / "audio.wav.json", {}) or {}).get("src")
        if fb or not sep_ok or a_marker != "vocals.wav":
            lines += [f"**⚠ ON không chạy trên vocals** (separate={ (on_m.get('separate') or {}).get('status') }, "
                      f"audio.wav từ `{a_marker}`, fallback=`{fb}`) — so sánh không có giá trị.", ""]
        prod = rows_in(jload(src / jd.name / "asr.json", []) or [], sec)
        t_off = jload(jd / "off" / "_timing.json", {}) or {}
        t_on = jload(jd / "on" / "_timing.json", {}) or {}
        txt_off, txt_on, txt_prod = text_of(off), text_of(on), text_of(prod)
        pairs = match_rows(off, on)
        ds = [abs(float(x["start"]) - float(y["start"])) for x, y, _ in pairs]
        de = [abs(float(x["end"]) - float(y["end"])) for x, y, _ in pairs]
        lines += [
            "| | OFF (gốc) | ON (Demucs) | V2 production (cùng cửa sổ) |",
            "|---|---:|---:|---:|",
            f"| Số câu | {len(off)} | {len(on)} | {len(prod)} |",
            f"| Tổng ký tự (bỏ dấu) | {len(norm_text(txt_off))} | {len(norm_text(txt_on))} | {len(norm_text(txt_prod))} |",
            f"| Thời gian chạy | {t_off.get('sec', '—')}s | {t_on.get('sec', '—')}s (gồm Demucs) | — |",
            "",
            f"- Câu khớp: **{len(pairs)}** / OFF {len(off)} / ON {len(on)}",
            f"- Lệch mốc đầu |Δstart|: {stats(ds)}",
            f"- Lệch mốc cuối |Δend|: {stats(de)}",
        ]
        c1 = cer(txt_on, txt_off)
        lines.append(f"- CER(ON so với OFF): **{c1:.2%}**" if c1 is not None else "- CER(ON/OFF): —")
        c0 = cer(txt_off, txt_prod)
        if c0 is not None:
            lines.append(f"- CER(OFF so với production — kiểm tái lập): {c0:.2%}")
        cues = jload(jd / "off" / "hardsub_cues.json")
        if isinstance(cues, list) and cues:
            ref = "".join((c.get("zh") or c.get("text") or "") for c in cues if float(c.get("start") or 0) < sec)
            co, cn = cer(txt_off, ref), cer(txt_on, ref)
            lines.append(f"- CER so với hardsub OCR ({len(cues)} cue): OFF **{co:.2%}** · ON **{cn:.2%}** "
                         f"(thấp hơn = tốt hơn; OCR cũng có lỗi)" if co is not None and cn is not None
                         else "- CER so với OCR: —")
        else:
            lines.append("- CER so với hardsub OCR: _không có_ (chạy với `--ocr` nếu phim có hardsub)")
        # câu khác nhau nhiều nhất + clip nghe
        scored = []
        for x, y, iou in pairs:
            cx = cer(y.get("text", ""), x.get("text", ""))
            if cx is not None and cx > 0:
                scored.append((cx, x, y))
        scored.sort(key=lambda t: -t[0])
        only_off = [r for r in off if all(r is not x for x, _, _ in pairs)]
        on_ids = {id(y) for _, y, _ in pairs}
        only_on = [r for r in on if id(r) not in on_ids]
        cdir = jd / "clips"
        cdir.mkdir(exist_ok=True)
        lines += ["", f"### Câu khác nhau nhiều nhất (top {a.top}) — clip nghe ở `{cdir}`", "",
                  "| # | Thời điểm | CER | OFF (gốc) | ON (Demucs) | clip |", "|---:|---|---:|---|---|---|"]
        for k, (cx, x, y) in enumerate(scored[: a.top], 1):
            s, e = float(x["start"]), float(x["end"])
            base = f"{k:02d}_{s:07.1f}"
            if not (cdir / f"{base}_off.wav").exists():
                clip(jd / "off" / "audio.wav", cdir / f"{base}_off.wav", s, e)
                clip(jd / "on" / "audio.wav", cdir / f"{base}_on.wav", s, e)
            lines.append(f"| {k} | {s:.1f}–{e:.1f}s | {cx:.0%} | {x.get('text', '')} | {y.get('text', '')} | `{base}_*.wav` |")
        if only_off or only_on:
            lines += ["", f"Câu không khớp: chỉ OFF {len(only_off)} · chỉ ON {len(only_on)}"]
            for r in only_off[:8]:
                lines.append(f"- OFF {float(r['start']):.1f}s: {r.get('text', '')}")
            for r in only_on[:8]:
                lines.append(f"- ON  {float(r['start']):.1f}s: {r.get('text', '')}")
        lines.append("")
    lines += ["## Kết luận", "",
              "_Điền sau khi nghe clip: ON tốt hơn / ngang / kém hơn? Nếu kém -> rollback `ASR_USE_DEMUCS=0` "
              "(xem V2 docs/fixes/2026-11-12-asr-demucs-default.md)._", ""]
    rp = Path(a.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text("\n".join(lines), encoding="utf-8")
    log(f"[report] {n_jobs} job -> {rp}")


# ───────────────────────────── golden vocals (CAM++) ─────────────────────────────

def cmd_golden(a) -> None:
    """Vocals 16k mono cho 4 cửa sổ golden -> scratch/voice_exp/data/<job>.vocals.wav.
    Cùng trục với prep.py (audio.wav V2 trích bằng AUDIO_TS_FIX): trích audio gốc 44.1k của CẢ
    video bằng separate._extract_orig_wav, cắt cửa sổ [t0,t1] theo mẫu, Demucs trên cửa sổ."""
    v2, src, out = Path(a.v2), Path(a.src_work), Path(a.out) / "golden"
    data = Path(a.voice_exp) / "data"
    warn_gpu_and_busy(src, a.ignore_busy)
    confirm(a.yes, f"Sẽ chạy Demucs trên {len(GOLDEN_JOBS)} cửa sổ golden (GPU).")
    sys.path.insert(0, str(v2))
    if a.skip_vram_free_env:
        os.environ["SKIP_VRAM_FREE"] = "1"
    import separate  # noqa: E402  (V2)
    for j in GOLDEN_JOBS:
        mj = jload(data / f"{j}.json")
        if not mj:
            log(f"[golden] {j}: thiếu {data / (j + '.json')} (chạy prep.py trước) -> bỏ")
            continue
        dst_wav = data / f"{j}.vocals.wav"
        if dst_wav.exists() and not a.fresh:
            log(f"[golden] {j}: đã có {dst_wav.name}")
            continue
        video = find_video(src / j)
        if not video:
            log(f"[golden] {j}: không thấy video -> bỏ")
            continue
        wd = out / j
        wd.mkdir(parents=True, exist_ok=True)
        full = wd / "_full44k.wav"
        if not full.exists():
            log(f"[golden] {j}: trích audio gốc 44.1k của cả video (chỉ đọc nguồn)…")
            separate._extract_orig_wav(video, full)
        t0, t1 = float(mj["t0"]), float(mj["t1"])
        win = wd / "window.wav"
        separate.write_wav_slice(full, win, int(round(t0 * separate.SR)), int(round((t1 - t0) * separate.SR)))
        (wd / "manifest.json").write_text(json.dumps({"stages": {"download": {
            "status": "done", "video_path": str(win), "duration_sec": t1 - t0}}}), encoding="utf-8")
        separate.run_separate(wd, device=a.device)
        ffmpeg("-i", str(wd / "vocals.wav"), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst_wav))
        full.unlink(missing_ok=True)
        log(f"[golden] {j}: {t1 - t0:.0f}s -> {dst_wav}")
    log("Xong. Tiếp: cd scratch\\voice_exp && python embed_campplus.py --vocals && python exp.py "
        "&& python check_confound.py cam_voc")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="dry", choices=["dry", "run", "report", "golden"])
    ap.add_argument("--v2", default=str(DEF_V2))
    ap.add_argument("--src-work", default=str(DEF_SRC))
    ap.add_argument("--out", default=str(DEF_OUT))
    ap.add_argument("--report", default=str(DEF_REPORT))
    ap.add_argument("--jobs", type=lambda s: [x for x in s.split(",") if x], default=None)
    ap.add_argument("--n", type=int, default=3, help="số job tự chọn (mặc định 3)")
    ap.add_argument("--minutes", type=float, default=12.0, help="chỉ cắt N phút đầu (mặc định 12)")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--ocr", action="store_true", help="chạy hardsub OCR V2 trên bản sao để làm tham chiếu")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--voice-exp", default=str(VOICE_EXP), help="thư mục scratch voice_exp (golden)")
    ap.add_argument("--fresh", action="store_true", help="xoá kết quả bản sao cũ, chạy lại")
    ap.add_argument("--free-vram", action="store_true", help="cho phép V2 free_gpu_vram stop Ollama")
    ap.add_argument("--ignore-busy", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    a.skip_vram_free_env = not a.free_vram
    if a.cmd == "report":
        cmd_report(a)
    elif a.cmd == "golden":
        cmd_golden(a)
    else:
        cmd_run(a)


if __name__ == "__main__":
    main()
