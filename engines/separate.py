"""
separate.py — Tách giọng/nền bằng Demucs (htdemucs --two-stems=vocals).

Đầu vào : work/<job>/source.* (video)
Đầu ra  : work/<job>/vocals.wav     (giọng Trung sạch — nguồn cho ASR, mặc định từ 2026-11-12)
          work/<job>/no_vocals.wav  (nhạc/SFX — render demucs_keep/clean, duck dùng thay [0:a])

Dùng ở 2 nơi, chạy MỘT lần/job rồi dùng lại:
  • ASR: asr.resolve_asr_source() gọi trước khi nhận diện (ASR_USE_DEMUCS mặc định 1).
  • Render: chế độ demucs_keep/demucs_clean (server tự gọi nếu chưa có no_vocals.wav).

Chạy Demucs qua SUBPROCESS -> model nạp ở tiến trình con, xong là tự nhả VRAM
(không đụng model chính). htdemucs xử theo đoạn nên VRAM bị chặn (~4-6GB).

Chia khúc (fix 2026-11-12 asr-demucs-default — bỏ kiểu ffmpeg -f segment FLAC cắt cứng 60'):
  RAM của Demucs tỉ lệ với độ dài file (toàn bộ tensor vào + 4 stem ra ở RAM CPU,
  ước ~6-13 GB/giờ audio 44.1k stereo) -> part 2.5h chạy 1 lượt có thể cần 15-30 GB.
  Nên audio dài hơn DEMUCS_LONG_THRESHOLD_SEC vẫn chia khúc, nhưng:
   (1) điểm chia dời vào KHOẢNG LẶNG gần nhất (split_parts.find_best_cut_point);
   (2) cắt theo SỐ MẪU trên WAV PCM (không re-encode, không segment FLAC), mỗi khúc
       đưa Demucs thêm DEMUCS_CHUNK_PAD_SEC ngữ cảnh 2 bên rồi cắt bỏ phần đệm;
   (3) ghép lại -> vocals.wav / no_vocals.wav dài ĐÚNG bằng audio gốc tới từng mẫu.

Env:
  DEMUCS_CHUNK_SEC          (1800) độ dài mục tiêu mỗi khúc
  DEMUCS_LONG_THRESHOLD_SEC (2400) audio dài hơn mới chia khúc
  DEMUCS_CHUNK_PAD_SEC      (5)    ngữ cảnh đệm 2 bên mỗi khúc (bị cắt bỏ sau Demucs)
  DEMUCS_CUT_SEARCH_SEC     (60)   bán kính tìm khoảng lặng quanh điểm chia

Chạy tay:
    python separate.py work/<job>
    python separate.py work/<job> --device cpu     # nếu GPU bận
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
from engines.vram import free_gpu_vram

STAGE = "separate"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return float(default)


# Đọc env MỖI lần chạy (server importlib.reload separate trước khi gọi, CLI cũng vậy).
def _cfg() -> dict:
    return {
        "chunk_sec": max(60.0, _env_float("DEMUCS_CHUNK_SEC", 1800)),
        "threshold_sec": max(60.0, _env_float("DEMUCS_LONG_THRESHOLD_SEC", 2400)),
        "pad_sec": max(0.0, _env_float("DEMUCS_CHUNK_PAD_SEC", 5)),
        "search_sec": max(0.0, _env_float("DEMUCS_CUT_SEARCH_SEC", 60)),
    }


SR = 44100          # tần số audio đưa Demucs (htdemucs làm việc ở 44.1k)
CHANNELS = 2
_DUR_TOL_SEC = 2.0  # lệch tối đa giữa độ dài stem và video khi kiểm kết quả CŨ (chưa ghi n_frames)
_IO_BLOCK = 4 * 1024 * 1024


# ───────────────────────────── WAV PCM (RIFF/RF64) theo mẫu ─────────────────────────────

class WavInfo:
    __slots__ = ("path", "fmt_raw", "channels", "rate", "block_align", "data_offset", "n_frames")

    def __init__(self, path, fmt_raw, channels, rate, block_align, data_offset, n_frames):
        self.path = Path(path)
        self.fmt_raw = fmt_raw
        self.channels = channels
        self.rate = rate
        self.block_align = block_align
        self.data_offset = data_offset
        self.n_frames = n_frames

    @property
    def seconds(self) -> float:
        return self.n_frames / float(self.rate or 1)


def wav_info(path: str | Path) -> WavInfo:
    """Đọc header WAV/RF64 (PCM/float/extensible). Không giải mã — chỉ vị trí data + số mẫu."""
    p = Path(path)
    size = p.stat().st_size
    with open(p, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[8:12] != b"WAVE" or head[:4] not in (b"RIFF", b"RF64", b"BW64"):
            raise ValueError(f"{p.name}: không phải WAV")
        is64 = head[:4] != b"RIFF"
        ds64_data = None
        fmt_raw = None
        pos = 12
        while pos + 8 <= size:
            f.seek(pos)
            cid, csz = struct.unpack("<4sI", f.read(8))
            body = pos + 8
            if cid == b"ds64":
                raw = f.read(min(csz, 28))
                if len(raw) >= 16:
                    ds64_data = struct.unpack("<Q", raw[8:16])[0]
            elif cid == b"fmt ":
                fmt_raw = f.read(csz)
            elif cid == b"data":
                if fmt_raw is None or len(fmt_raw) < 16:
                    raise ValueError(f"{p.name}: thiếu chunk fmt")
                _tag, ch, rate, _bps, align, _bits = struct.unpack("<HHIIHH", fmt_raw[:16])
                if is64 and csz == 0xFFFFFFFF and ds64_data is not None:
                    dsz = ds64_data
                else:
                    dsz = csz
                # Header ghi dở / ffmpeg pipe (0 hoặc 0xFFFFFFFF) -> lấy theo kích thước file
                avail = size - body
                if dsz in (0, 0xFFFFFFFF) or dsz > avail:
                    dsz = avail
                if not align:
                    raise ValueError(f"{p.name}: block_align=0")
                return WavInfo(p, fmt_raw, ch, rate, align, body, dsz // align)
            pos = body + csz + (csz & 1)
    raise ValueError(f"{p.name}: không thấy chunk data")


def _write_header(f, fmt_raw: bytes, data_bytes: int) -> None:
    """Header RIFF chuẩn; data ≥ ~4GB -> RF64 (ffmpeg/soundfile đọc được)."""
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt_raw)) + fmt_raw + (b"\x00" if len(fmt_raw) & 1 else b"")
    if data_bytes + 200 < 0xFFFFFFFF:
        riff_size = 4 + len(fmt_chunk) + 8 + data_bytes
        f.write(b"RIFF" + struct.pack("<I", riff_size) + b"WAVE")
        f.write(fmt_chunk)
        f.write(b"data" + struct.pack("<I", data_bytes))
    else:
        riff_size = 4 + (8 + 28) + len(fmt_chunk) + 8 + data_bytes
        f.write(b"RF64" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE")
        f.write(b"ds64" + struct.pack("<I", 28) + struct.pack("<QQQI", riff_size, data_bytes, 0, 0))
        f.write(fmt_chunk)
        f.write(b"data" + struct.pack("<I", 0xFFFFFFFF))


def _copy_frames(src: WavInfo, fsrc, start: int, count: int, fdst) -> None:
    """Chép `count` frame từ vị trí `start` của src; thiếu (vượt cuối file) -> đệm 0."""
    ba = src.block_align
    start = max(0, start)
    have = max(0, min(count, src.n_frames - start))
    fsrc.seek(src.data_offset + start * ba)
    left = have * ba
    while left > 0:
        buf = fsrc.read(min(_IO_BLOCK, left))
        if not buf:
            break
        fdst.write(buf)
        left -= len(buf)
    missing = count * ba - (have * ba - left)
    while missing > 0:
        n = min(_IO_BLOCK, missing)
        fdst.write(b"\x00" * n)
        missing -= n


def write_wav_slice(src_path: str | Path, out_path: str | Path, start: int, count: int) -> int:
    """Ghi out = frames [start, start+count) của src (đệm 0 nếu src ngắn). Atomic (.partial)."""
    src = wav_info(src_path)
    out = Path(out_path)
    part = out.with_name(out.stem + ".partial" + out.suffix)
    try:
        with open(src.path, "rb") as fs, open(part, "wb") as fd:
            _write_header(fd, src.fmt_raw, count * src.block_align)
            _copy_frames(src, fs, start, count, fd)
        os.replace(part, out)
    finally:
        part.unlink(missing_ok=True)
    return count


def concat_wavs(parts: list[str | Path], out_path: str | Path, expect_frames: int | None = None) -> int:
    """Ghép WAV cùng định dạng theo mẫu (không re-encode). Kiểm tổng frame nếu có expect_frames."""
    infos = [wav_info(p) for p in parts]
    if not infos:
        raise RuntimeError("concat_wavs: không có khúc nào")
    f0 = infos[0]
    for i in infos[1:]:
        if (i.channels, i.rate, i.block_align, i.fmt_raw[:16]) != (f0.channels, f0.rate, f0.block_align, f0.fmt_raw[:16]):
            raise RuntimeError(f"concat_wavs: {i.path.name} khác định dạng {f0.path.name}")
    total = sum(i.n_frames for i in infos)
    if expect_frames is not None and total != expect_frames:
        raise RuntimeError(f"concat_wavs: tổng {total} mẫu ≠ gốc {expect_frames}")
    out = Path(out_path)
    part = out.with_name(out.stem + ".partial" + out.suffix)
    try:
        with open(part, "wb") as fd:
            _write_header(fd, f0.fmt_raw, total * f0.block_align)
            for i in infos:
                with open(i.path, "rb") as fs:
                    _copy_frames(i, fs, 0, i.n_frames, fd)
        os.replace(part, out)
    finally:
        part.unlink(missing_ok=True)
    return total


def _frames_or_none(p: Path) -> int | None:
    try:
        return wav_info(p).n_frames
    except Exception:  # noqa: BLE001
        return None


# ───────────────────────────── tiện ích job ─────────────────────────────

def _find_video(work_dir: Path) -> Path | None:
    # agentK 2026-11-12: 1 hàm resolve nguồn (preflight chờ → chạy; nguồn chuẩn hoá > gốc)
    from engines.source_preflight import stage_source_video
    return stage_source_video(work_dir)


def _probe_duration(path: Path) -> float:
    """Lấy thời lượng file media (giây) qua ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return float((r.stdout or "0").strip() or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _update_sep_progress(m: Manifest, pct: float | None, cur_text: str) -> None:
    """Tiến độ stage 'separate' + đồng bộ sang 'asr' nếu ASR đang chờ tách giọng.
    Ghi qua Manifest.update (đọc lại đĩa trong khoá) — không đè tiến độ tiến trình khác."""
    now = time.time()

    def _fn(data: dict):
        stages = data.setdefault("stages", {})
        st = stages.setdefault(STAGE, {})
        st.update({"status": "running", "progress": pct, "cur_text": cur_text, "updated_at": now})
        asr_st = stages.get("asr")
        if isinstance(asr_st, dict) and asr_st.get("status") == "running":
            asr_st["cur_text"] = cur_text
            asr_st["phase"] = "separate"
            if pct is not None:
                asr_st["progress"] = pct
            asr_st["updated_at"] = now

    try:
        js.mutate(m.work_dir, _fn)
    except Exception:  # noqa: BLE001
        pass


def _extract_orig_wav(video: Path, out_wav: Path) -> None:
    """Audio GỐC 44.1k stereo PCM s16 (RF64 khi >4GB) — chính xác theo mẫu để cắt/ghép.
    Cùng AUDIO_TS_FIX như audio.wav của ASR -> cùng trục thời gian với hình.
    AAC hỏng giữa file → extract_pcm_from_video (salvage + chunk + silence)."""
    from engines.media_ts import extract_pcm_from_video
    extract_pcm_from_video(
        video, out_wav, ac=CHANNELS, ar=SR, rf64=True,
    )


def plan_chunks(n_frames: int, rate: int, cfg: dict, cut_finder=None) -> list[tuple[int, int]]:
    """[(start, end)] theo mẫu, phủ kín [0, n_frames). Audio ≤ ngưỡng -> 1 khúc.

    Điểm chia mục tiêu chia đều; cut_finder(target_sec) -> giây (khoảng lặng gần nhất).
    Điểm chia phải tăng dần và mỗi khúc ≥ 25% chunk_sec, nếu không -> dùng điểm mục tiêu.
    """
    dur = n_frames / float(rate)
    if n_frames <= 0:
        return []
    if dur <= cfg["threshold_sec"]:
        return [(0, n_frames)]
    n = max(2, int(-(-dur // cfg["chunk_sec"])))   # ceil
    min_len = int(0.25 * cfg["chunk_sec"] * rate)
    cuts: list[int] = []
    prev = 0
    for k in range(1, n):
        target = dur * k / n
        sec = target
        if cut_finder is not None:
            try:
                sec = float(cut_finder(target))
            except Exception as e:  # noqa: BLE001
                print(f"[sep] tìm khoảng lặng quanh {target:.0f}s lỗi ({e}) -> cắt tại mục tiêu", flush=True)
                sec = target
        c = int(round(sec * rate))
        nxt_target = int(round(dur * (k + 1) / n * rate)) if k + 1 < n else n_frames
        if c - prev < min_len or nxt_target - c < min_len or c >= n_frames:
            c = int(round(target * rate))
        cuts.append(c)
        prev = c
    bounds = [0, *cuts, n_frames]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def _run_demucs_on_file(
    src_audio: Path,
    tmp_out: Path,
    device: str,
    work_dir: Path,
    m: Manifest,
    chunk_idx: int = 0,
    n_chunks: int = 1,
) -> tuple[Path, Path]:
    """Chạy Demucs trên 1 file audio (ngắn hoặc 1 khúc) và trả về (no_vocals_wav, vocals_wav)."""
    if tmp_out.exists():
        shutil.rmtree(tmp_out, ignore_errors=True)
    tmp_out.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "demucs", "--two-stems=vocals",
        "--device", device, "-o", str(tmp_out), str(src_audio),
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )

    last = 0.0
    pat = re.compile(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)")
    tail_lines: list[str] = []
    for line in proc.stdout:
        if (work_dir / ".stop").exists():
            try:
                proc.terminate()
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError("Đã dừng tách nền theo yêu cầu (.stop)")
        s_line = line.strip()
        if s_line:
            tail_lines.append(s_line)
            if len(tail_lines) > 15:
                tail_lines.pop(0)
        mo = pat.search(line)
        now = time.time()
        if now - last > 1.5:
            last = now
            chunk_pct = None
            if mo:
                cur, tot = float(mo.group(1)), float(mo.group(2))
                chunk_pct = min(cur / tot * 100.0, 99.9) if tot else None
            if chunk_pct is not None:
                if n_chunks > 1:
                    overall_pct = round(((chunk_idx + chunk_pct / 100.0) / n_chunks) * 100.0, 1)
                    label = f"Tách giọng Demucs (khúc {chunk_idx + 1}/{n_chunks}): {overall_pct}%"
                else:
                    overall_pct = round(chunk_pct, 1)
                    label = f"Tách giọng Demucs: {overall_pct}%"
                _update_sep_progress(m, overall_pct, label)

    proc.wait()
    if proc.returncode != 0:
        err_tail = " | ".join(tail_lines[-4:]) if tail_lines else ""
        raise RuntimeError(f"Demucs lỗi (mã {proc.returncode})" + (f": {err_tail[:240]}" if err_tail else ""))

    nv = next(tmp_out.rglob("no_vocals.wav"), None)
    if not nv:
        raise RuntimeError("Không thấy no_vocals.wav sau Demucs")
    voc = next(tmp_out.rglob("vocals.wav"), None)
    if not voc:
        raise RuntimeError("Không thấy vocals.wav sau Demucs")
    return nv, voc


def outputs_valid(work_dir: Path, video: Path | None = None, st: dict | None = None) -> tuple[bool, str]:
    """vocals.wav + no_vocals.wav đủ dùng lại? (kích thước + độ dài khớp gốc/video)."""
    work_dir = Path(work_dir)
    voc, nv = work_dir / "vocals.wav", work_dir / "no_vocals.wav"
    for p in (voc, nv):
        if not p.exists() or p.stat().st_size <= 1000:
            return False, f"thiếu/rỗng {p.name}"
    fv, fn = _frames_or_none(voc), _frames_or_none(nv)
    st = st or {}
    want = st.get("n_frames")
    if want:
        if fv != int(want) or fn != int(want):
            return False, f"số mẫu {fv}/{fn} ≠ gốc {want}"
        return True, "khớp số mẫu gốc"
    # Kết quả cũ (trước 2026-11-12, chưa ghi n_frames): so độ dài với nhau + với video
    if fv is not None and fn is not None:
        dv = fv / float(wav_info(voc).rate or SR)
        dn = fn / float(wav_info(nv).rate or SR)
    else:
        dv, dn = _probe_duration(voc), _probe_duration(nv)
    if not dv or not dn or abs(dv - dn) > 0.05:
        return False, f"vocals {dv:.2f}s ≠ no_vocals {dn:.2f}s"
    if video is not None and Path(video).exists():
        vd = float(st.get("video_dur") or 0) or _probe_duration(Path(video))
        if vd and abs(dv - vd) > max(_DUR_TOL_SEC, 0.002 * vd):
            return False, f"stem {dv:.1f}s lệch video {vd:.1f}s"
    return True, "độ dài khớp video"


def _mark_done(m: Manifest, work_dir: Path, device: str, **extra) -> dict:
    return m.mark_done(
        STAGE,
        no_vocals_path=str(work_dir / "no_vocals.wav"),
        vocals_path=str(work_dir / "vocals.wav"),
        device=device,
        **extra,
    )


def run_separate(work_dir: str | Path, device: str = "cuda", force: bool = False) -> dict:
    work_dir = Path(work_dir)
    m = Manifest(work_dir)
    video = _find_video(work_dir)
    st_prev = m.stage(STAGE) or {}

    # Đã có kết quả hợp lệ -> dùng lại (Demucs chạy MỘT lần/job). Không ghi audio.wav ở đây:
    # audio.wav do bước ASR quản (asr.ensure_audio_wav theo marker nguồn).
    if not force:
        ok, why = outputs_valid(work_dir, video, st_prev if m.is_done(STAGE) else None)
        if ok:
            if not m.is_done(STAGE):
                st_prev = _mark_done(m, work_dir, st_prev.get("device") or device, reused=True)
            print(f"[skip] Tách giọng/nền đã có ({why}) -> dùng lại vocals.wav / no_vocals.wav", flush=True)
            return m.stage(STAGE) or st_prev
        if (work_dir / "vocals.wav").exists() or (work_dir / "no_vocals.wav").exists():
            print(f"[sep] kết quả cũ không dùng được ({why}) -> tách lại", flush=True)

    if not video:
        raise RuntimeError("Chưa có video (chạy download trước).")

    cfg = _cfg()
    m.mark_running(STAGE)
    _update_sep_progress(m, 0.0, "Đang chuẩn bị tách giọng (Demucs)…")
    free_gpu_vram("trước tách nền Demucs")   # chỉ stop Ollama + xả torch cache (không đụng :8000/:8083)
    chunks_dir = work_dir / "_demucs_chunks"
    tmp_out = work_dir / "_demucs"
    dest_nv = work_dir / "no_vocals.wav"
    dest_voc = work_dir / "vocals.wav"
    try:
        # Dọn tàn dư kiểu cũ (FLAC nguyên cục / segment FLAC).
        for stale in (work_dir / "orig_audio.flac",):
            stale.unlink(missing_ok=True)
        if force and chunks_dir.exists():
            shutil.rmtree(chunks_dir, ignore_errors=True)
        chunks_dir.mkdir(parents=True, exist_ok=True)

        orig = chunks_dir / "orig.wav"
        meta_file = chunks_dir / "plan.json"
        v_stat = video.stat()
        sig = {"video": video.name, "size": v_stat.st_size, "mtime": int(v_stat.st_mtime),
               "sr": SR, "ch": CHANNELS, **{k: cfg[k] for k in ("chunk_sec", "threshold_sec", "pad_sec")}}
        plan = None
        if meta_file.is_file() and orig.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if meta.get("sig") == sig and _frames_or_none(orig) == meta.get("n_frames"):
                    plan = [tuple(x) for x in meta["chunks"]]
                    n_frames = int(meta["n_frames"])
                    print(f"[sep] resume: dùng lại audio gốc + kế hoạch {len(plan)} khúc", flush=True)
            except Exception:  # noqa: BLE001
                plan = None
        if plan is None:
            shutil.rmtree(chunks_dir, ignore_errors=True)
            chunks_dir.mkdir(parents=True, exist_ok=True)
            print("[sep] Tách audio gốc WAV PCM 44.1k stereo (chính xác theo mẫu)…", flush=True)
            _update_sep_progress(m, 0.5, "Đang tách audio gốc (WAV) từ video…")
            _extract_orig_wav(video, orig)
            n_frames = wav_info(orig).n_frames
            if n_frames <= 0:
                raise RuntimeError("Audio gốc rỗng — không tách giọng được")
            finder = None
            if n_frames / SR > cfg["threshold_sec"] and cfg["search_sec"] > 0:
                from engines.split_parts import find_best_cut_point

                def finder(t, _o=orig, _r=cfg["search_sec"]):
                    return find_best_cut_point(_o, t, search_radius_sec=_r)
            plan = plan_chunks(n_frames, SR, cfg, cut_finder=finder)
            meta_file.write_text(json.dumps({"sig": sig, "n_frames": n_frames, "chunks": plan},
                                            ensure_ascii=False, indent=1), encoding="utf-8")
            if len(plan) > 1:
                cuts = ", ".join(f"{s / SR:.1f}s" for s, _ in plan[1:])
                print(f"[sep] Audio {n_frames / SR / 3600:.2f}h -> {len(plan)} khúc, chia tại khoảng lặng: {cuts}",
                      flush=True)

        n_chunks = len(plan)
        pad = int(round(cfg["pad_sec"] * SR)) if n_chunks > 1 else 0
        voc_parts: list[Path] = []
        nv_parts: list[Path] = []
        for idx, (s, e) in enumerate(plan):
            c_voc = chunks_dir / f"vocals_{idx:03d}.wav"
            c_nv = chunks_dir / f"no_vocals_{idx:03d}.wav"
            want = e - s
            if _frames_or_none(c_voc) == want and _frames_or_none(c_nv) == want:
                print(f"[sep] Khúc {idx + 1}/{n_chunks} đã tách xong từ trước -> dùng lại (resume).", flush=True)
                pct = round((idx + 1) / n_chunks * 100.0, 1)
                _update_sep_progress(m, pct, f"Tách giọng Demucs (khúc {idx + 1}/{n_chunks}): {pct}%")
            else:
                ps, pe = max(0, s - pad), min(n_frames, e + pad)
                if n_chunks == 1:
                    c_in = orig
                else:
                    c_in = chunks_dir / f"in_{idx:03d}.wav"
                    write_wav_slice(orig, c_in, ps, pe - ps)
                base = round(idx / n_chunks * 100.0, 1)
                lbl = (f"Tách giọng Demucs (khúc {idx + 1}/{n_chunks}): {base}%" if n_chunks > 1
                       else "Tách giọng Demucs: 0%")
                print(f"[sep] Demucs ({device}) khúc {idx + 1}/{n_chunks}: "
                      f"{s / SR:.1f}s→{e / SR:.1f}s (+đệm {(s - ps) / SR:.1f}/{(pe - e) / SR:.1f}s)…", flush=True)
                _update_sep_progress(m, base, lbl)
                nv_tmp, voc_tmp = _run_demucs_on_file(c_in, tmp_out, device, work_dir, m,
                                                      chunk_idx=idx, n_chunks=n_chunks)
                for src_p, dst_p in ((voc_tmp, c_voc), (nv_tmp, c_nv)):
                    wi = wav_info(src_p)
                    if wi.rate != SR:
                        raise RuntimeError(f"Demucs trả {wi.rate}Hz ≠ {SR}Hz ({dst_p.name}) — không ghép theo mẫu được")
                    got = wi.n_frames
                    if got != pe - ps:
                        print(f"[sep] cảnh báo: Demucs trả {got} mẫu ≠ vào {pe - ps} ({dst_p.name}) — đệm/cắt về đúng",
                              flush=True)
                    write_wav_slice(src_p, dst_p, s - ps, want)
                shutil.rmtree(tmp_out, ignore_errors=True)
                if c_in is not orig:
                    c_in.unlink(missing_ok=True)
            voc_parts.append(c_voc)
            nv_parts.append(c_nv)

        print(f"[sep] Ghép {n_chunks} khúc -> vocals.wav & no_vocals.wav ({n_frames} mẫu)…", flush=True)
        _update_sep_progress(m, 99.0, "Đang ghép các khúc âm thanh sau Demucs…")
        concat_wavs(voc_parts, dest_voc, expect_frames=n_frames)
        concat_wavs(nv_parts, dest_nv, expect_frames=n_frames)
        for p, name in ((dest_voc, "vocals"), (dest_nv, "no_vocals")):
            got = wav_info(p).n_frames
            if got != n_frames:
                raise RuntimeError(f"{name}.wav {got} mẫu ≠ audio gốc {n_frames}")
        shutil.rmtree(chunks_dir, ignore_errors=True)
        shutil.rmtree(tmp_out, ignore_errors=True)
        (work_dir / "orig_audio.wav").unlink(missing_ok=True)   # tàn dư WAV kiểu cũ (nếu có)
    except Exception as e:  # noqa: BLE001
        m.mark_failed(STAGE, e)
        raise

    vd = float((m.stage("download") or {}).get("duration_sec") or 0.0)
    result = _mark_done(
        m, work_dir, device, n_frames=n_frames, sample_rate=SR, n_chunks=n_chunks,
        cut_points_sec=[round(s / SR, 3) for s, _ in plan[1:]],
        **({"video_dur": vd} if vd else {}),
    )
    print(f"[ok] Tách giọng/nền xong ({n_frames / SR:.1f}s, {n_chunks} khúc) -> "
          f"{result['no_vocals_path']} | {result['vocals_path']}", flush=True)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stage tách nền (Demucs).")
    ap.add_argument("work_dir")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    run_separate(args.work_dir, device=args.device, force=args.force)


if __name__ == "__main__":
    main()
