"""hardsub_asr.py — OCR hardsub chỉ để RÀ SOÁT / SỬA sub ZH (asr · srt).

Một nhiệm vụ:
  RapidOCR đọc dải hardsub → so với asr.json / sub.zh → báo thiếu·sai → vá chữ ZH
  (giữ timing ASR). KHÔNG dùng OCR để “hiểu cảnh” hay thay bước dịch.

Dịch thuật vẫn là trọng tâm Pineline (translate + glossary/style_lock).

  python hardsub_asr.py work/<job> --dur 180          # sinh asr.hardsub.json (xem trước)
  python hardsub_asr.py work/<job> --apply            # vá asr.json (+ sync script.zh)
  python hardsub_asr.py work/<job> --merge-only
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[2])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import json
import re
import shutil
import subprocess
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from core.manifest import Manifest

STAGE = "hardsub_asr"
_VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov"}


def _find_video(work_dir: Path) -> Path | None:
    # agentK 2026-11-12: 1 hàm resolve nguồn (preflight chờ → chạy; nguồn chuẩn hoá > gốc)
    from engines.source_preflight import stage_source_video
    return stage_source_video(work_dir)


def _probe_size(video: Path) -> tuple[int, int]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video)],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        w, h = out.split("x")[:2]
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return 1280, 720


def _norm_zh(s: str) -> str:
    t = (s or "").strip()
    t = re.sub(r"[\s。．，、！？!?,.…·\-—~～\"'“”‘’（）()【】\[\]<>《》：:；;]+", "", t)
    return t


def zh_sim(a: str, b: str) -> float:
    """Độ giống 0..1 (chuỗi đã chuẩn hóa)."""
    na, nb = _norm_zh(a), _norm_zh(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.92
    return SequenceMatcher(None, na, nb).ratio()


def _band_y_frac(work_dir: Path) -> tuple[float, float]:
    """(y0, y1) tỷ lệ chiều cao — ưu tiên hardsub_band.json."""
    cache = work_dir / "hardsub_band.json"
    if cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            band = d.get("band") or d
            if isinstance(band, (list, tuple)) and len(band) >= 2:
                y0, y1 = float(band[0]), float(band[1])
                if 0 <= y0 < y1 <= 1.0:
                    # nới nhẹ để OCR không cắt chữ
                    pad = 0.02
                    return max(0.0, y0 - pad), min(1.0, y1 + pad)
        except Exception:  # noqa: BLE001
            pass
    try:
        from engines.render import detect_hardsub_band
        band = detect_hardsub_band(work_dir)
        if band and len(band) >= 2:
            y0, y1 = float(band[0]), float(band[1])
            pad = 0.02
            return max(0.0, y0 - pad), min(1.0, y1 + pad)
    except Exception:  # noqa: BLE001
        pass
    return 0.72, 0.98


def extract_hardsub_cues(
    work_dir: Path,
    *,
    dur: float | None = None,
    fps: float = 2.0,
    conf_min: float = 0.5,
) -> list[dict]:
    """OCR crop đáy → list {start,end,zh,n,source}."""
    from rapidocr_onnxruntime import RapidOCR

    video = _find_video(work_dir)
    if not video:
        raise RuntimeError("Chưa có video")
    W, H = _probe_size(video)
    y0, y1 = _band_y_frac(work_dir)
    crop_y = int(H * y0)
    crop_h = max(32, int(H * (y1 - y0)))
    print(f"[hardsub] band y={y0:.3f}-{y1:.3f} → crop {W}x{crop_h} @y={crop_y} · fps={fps}")

    frame_dir = work_dir / "_hardsub_frames"
    if frame_dir.exists():
        shutil.rmtree(frame_dir, ignore_errors=True)
    frame_dir.mkdir(parents=True, exist_ok=True)

    tlim = ["-t", str(dur)] if dur else []
    # fps cố định + crop đáy — nhanh hơn full-frame keyframe
    vf = f"fps={fps},crop={W}:{crop_h}:0:{crop_y}"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), *tlim, "-vf", vf,
         "-q:v", "3", str(frame_dir / "hs_%06d.jpg"), "-loglevel", "error"],
        check=True,
    )
    imgs = sorted(frame_dir.glob("hs_*.jpg"))
    if not imgs:
        raise RuntimeError("Không trích được frame hardsub")

    ocr = RapidOCR()
    # (t, text_joined)
    samples: list[tuple[float, str]] = []
    for i, img in enumerate(imgs):
        t = i / max(fps, 0.1)
        res, _ = ocr(str(img))
        parts: list[str] = []
        for _box, txt, conf in (res or []):
            tx = (txt or "").strip()
            if not tx or conf < conf_min:
                continue
            # bỏ watermark ngắn / latin
            if sum(1 for c in tx if "\u4e00" <= c <= "\u9fff") < 1:
                continue
            parts.append(tx)
        if parts:
            # ghép trái→phải đã có trong OCR order
            line = "".join(parts) if len(parts) > 1 and all(len(p) <= 4 for p in parts) else " ".join(parts)
            line = re.sub(r"\s+", "", line)  # hardsub TQ thường không space
            samples.append((round(t, 3), line))
        if (i + 1) % 40 == 0:
            print(f"  ...OCR {i + 1}/{len(imgs)}", flush=True)

    cues = _samples_to_cues(samples, gap=1.0 / fps + 0.15)
    # dọn frame
    shutil.rmtree(frame_dir, ignore_errors=True)
    print(f"[hardsub] {len(samples)} frame có chữ → {len(cues)} cue")
    return cues


def _samples_to_cues(samples: list[tuple[float, str]], gap: float = 0.6) -> list[dict]:
    """Gộp frame liên tiếp cùng/gần text thành cue."""
    if not samples:
        return []
    cues: list[dict] = []
    cur_t0, cur_text = samples[0]
    cur_t1 = cur_t0
    n = 1

    def flush():
        nonlocal cur_t0, cur_t1, cur_text, n
        if not cur_text:
            return
        cues.append({
            "start": round(cur_t0, 3),
            "end": round(cur_t1 + gap, 3),
            "zh": cur_text,
            "n": n,
            "source": "hardsub_ocr",
        })

    for t, text in samples[1:]:
        sim = zh_sim(text, cur_text)
        if sim >= 0.82 and (t - cur_t1) <= gap * 2.5:
            # cùng câu — giữ bản dài hơn / xuất hiện nhiều hơn
            if len(_norm_zh(text)) > len(_norm_zh(cur_text)):
                cur_text = text
            cur_t1 = t
            n += 1
        else:
            flush()
            cur_t0, cur_t1, cur_text, n = t, t, text, 1
    flush()
    return cues


def merge_hardsub_into_asr(
    asr: list[dict],
    hs_cues: list[dict],
    *,
    min_sim: float = 0.55,
) -> tuple[list[dict], dict]:
    """Giữ timing ASR; chỉ thay text khi hardsub KHỚP chữ (tránh dính câu bên cạnh).

    Trả (asr_new, stats).
    """
    stats = {"replaced": 0, "kept": 0, "no_overlap": 0, "low_sim": 0}
    out: list[dict] = []
    for seg in asr:
        if not isinstance(seg, dict):
            continue
        row = dict(seg)
        s = float(row.get("start") or 0)
        e = float(row.get("end") or s)
        mid = (s + e) / 2
        asr_zh = (row.get("text") or row.get("zh") or "").strip()
        best = None
        best_score = -1.0
        best_sim = 0.0
        best_cover = 0.0
        for hs in hs_cues:
            hs_s, hs_e = float(hs["start"]), float(hs["end"])
            overlap = max(0.0, min(e, hs_e) - max(s, hs_s))
            mid_hit = hs_s <= mid <= hs_e
            if overlap <= 0 and not mid_hit:
                continue
            dur = max(0.05, e - s)
            cover = overlap / dur if overlap > 0 else (0.5 if mid_hit else 0.0)
            sim = zh_sim(asr_zh, hs.get("zh") or "")
            # ưu tiên giống chữ; cover chỉ là tie-break
            score = sim * 0.75 + cover * 0.25
            if score > best_score:
                best = hs
                best_score = score
                best_sim = sim
                best_cover = cover
        if best is None:
            stats["no_overlap"] += 1
            stats["kept"] += 1
            row["hardsub"] = "none"
            out.append(row)
            continue
        hs_zh = (best.get("zh") or "").strip()
        use = best_sim >= min_sim and bool(hs_zh)
        # cho phép sim hơi thấp nếu cover cao VÀ chung ≥50% ký tự (chống câu bên cạnh)
        if not use and best_cover >= 0.7 and best_sim >= 0.38:
            na, nb = set(_norm_zh(asr_zh)), set(_norm_zh(hs_zh))
            if na and nb and len(na & nb) / max(len(na), len(nb)) >= 0.5:
                use = True
        if use and _norm_zh(hs_zh) != _norm_zh(asr_zh):
            na, nb = _norm_zh(asr_zh), _norm_zh(hs_zh)
            # HS chỉ là mảnh của ASR đã đúng → giữ ASR (tránh cắt câu gộp)
            if nb in na and len(nb) <= len(na) * 0.85:
                use = False
            # HS ngắn hơn rõ mà không phải sửa lỗi → bỏ
            elif len(nb) + 2 < len(na) * 0.55 and best_sim < 0.9:
                use = False
            # HS dài hơn vì dính câu kế (OCR gộp) — bỏ nếu ASR là prefix
            elif na in nb and len(na) <= len(nb) * 0.75 and best_cover < 0.35:
                use = False
            # bỏ dấu ... OCR rác
            elif hs_zh.rstrip(".").endswith("...") or "..." in hs_zh:
                if best_sim < 0.85:
                    use = False
        if use and _norm_zh(hs_zh) != _norm_zh(asr_zh):
            row["text_asr"] = asr_zh
            row["text"] = hs_zh
            row["align"] = "hardsub_ocr"
            row["hardsub"] = f"sim={best_sim:.2f}|cover={best_cover:.2f}"
            stats["replaced"] += 1
        else:
            if best_sim < min_sim:
                stats["low_sim"] += 1
            stats["kept"] += 1
            row["hardsub"] = f"keep_sim={best_sim:.2f}"
        out.append(row)
    return out, stats


def _to_srt(rows: list[dict], key: str = "text") -> str:
    def ts(sec: float) -> str:
        ms = int(round(max(0.0, sec) * 1000))
        h, ms = divmod(ms, 3600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines: list[str] = []
    n = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        t = (r.get(key) or r.get("zh") or r.get("text") or "").strip()
        if not t:
            continue
        n += 1
        lines.append(str(n))
        lines.append(f"{ts(float(r.get('start') or 0))} --> {ts(float(r.get('end') or 0))}")
        lines.append(t)
        lines.append("")
    return "\n".join(lines)


def sync_script_zh(work_dir: Path, asr_rows: list[dict]) -> int:
    """Cập nhật script.json zh theo asr (khớp i hoặc time)."""
    sp = work_dir / "script.json"
    if not sp.exists():
        return 0
    script = json.loads(sp.read_text(encoding="utf-8"))
    by_i = {
        int(r["i"]): (r.get("text") or "").strip()
        for r in asr_rows
        if isinstance(r, dict) and r.get("i") is not None and (r.get("text") or "").strip()
    }
    # fallback time
    by_time: list[tuple[float, float, str]] = [
        (float(r.get("start") or 0), float(r.get("end") or 0), (r.get("text") or "").strip())
        for r in asr_rows
        if isinstance(r, dict) and (r.get("text") or "").strip()
    ]
    n = 0
    for row in script:
        if not isinstance(row, dict):
            continue
        new_zh = None
        if row.get("i") is not None and int(row["i"]) in by_i:
            new_zh = by_i[int(row["i"])]
        else:
            mid = (float(row.get("start") or 0) + float(row.get("end") or 0)) / 2
            for s, e, t in by_time:
                if s <= mid <= e:
                    new_zh = t
                    break
        if new_zh and _norm_zh(new_zh) != _norm_zh(row.get("zh") or ""):
            row["zh_asr"] = row.get("zh")
            row["zh"] = new_zh
            n += 1
    if n:
        bak = work_dir / "script.json.before_hardsub.bak"
        if not bak.exists():
            shutil.copy2(sp, bak)
        sp.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


def run_hardsub_asr(
    work_dir: str | Path,
    *,
    dur: float | None = None,
    fps: float = 2.0,
    apply: bool = False,
    merge_only: bool = False,
    force: bool = False,
) -> dict:
    work_dir = Path(work_dir)
    m = Manifest(work_dir)
    asr_path = work_dir / "asr.json"
    if not asr_path.exists():
        raise RuntimeError("Chưa có asr.json")

    cues_path = work_dir / "hardsub_cues.json"
    m.mark_running(STAGE)
    try:
        if merge_only and cues_path.exists():
            hs_cues = json.loads(cues_path.read_text(encoding="utf-8"))
            print(f"[hardsub] merge-only · {len(hs_cues)} cues có sẵn")
        else:
            if cues_path.exists() and not force and dur is None:
                hs_cues = json.loads(cues_path.read_text(encoding="utf-8"))
                print(f"[hardsub] dùng hardsub_cues.json có sẵn ({len(hs_cues)})")
            else:
                hs_cues = extract_hardsub_cues(work_dir, dur=dur, fps=fps)
                cues_path.write_text(
                    json.dumps(hs_cues, ensure_ascii=False, indent=2), encoding="utf-8"
                )

        asr = json.loads(asr_path.read_text(encoding="utf-8"))
        # nếu OCR chỉ đoạn đầu (dur), chỉ merge ASR trong cửa sổ đó
        if dur:
            head = [r for r in asr if float(r.get("start") or 0) < dur]
            tail = [r for r in asr if float(r.get("start") or 0) >= dur]
            merged_head, stats = merge_hardsub_into_asr(head, hs_cues)
            merged = merged_head + tail
        else:
            merged, stats = merge_hardsub_into_asr(asr, hs_cues)

        out_asr = work_dir / "asr.hardsub.json"
        out_asr.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        out_srt = work_dir / "sub.zh.hardsub.srt"
        out_srt.write_text(_to_srt(merged, key="text"), encoding="utf-8")

        if apply:
            bak = work_dir / "asr.json.before_hardsub.bak"
            if not bak.exists():
                shutil.copy2(asr_path, bak)
            asr_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            n_script = sync_script_zh(work_dir, merged)
            print(f"[hardsub] --apply: asr.json đã thay · script zh sửa {n_script}")
        else:
            n_script = 0

        result = m.mark_done(
            STAGE,
            hardsub_cues=str(cues_path),
            asr_hardsub=str(out_asr),
            srt_hardsub=str(out_srt),
            n_cues=len(hs_cues),
            n_replaced=stats.get("replaced", 0),
            n_kept=stats.get("kept", 0),
            applied=bool(apply),
            script_zh_updated=n_script,
            dur=dur,
            fps=fps,
            updated_at=time.time(),
        )
        print(
            f"[ok] hardsub: cues={len(hs_cues)} replaced={stats['replaced']} "
            f"kept={stats['kept']} → {out_asr.name}",
            flush=True,
        )
        return result
    except Exception as e:  # noqa: BLE001
        m.mark_failed(STAGE, e)
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description="OCR hardsub → ghép ASR timing")
    ap.add_argument("work_dir")
    ap.add_argument("--dur", type=float, default=None, help="Chỉ N giây đầu (test)")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--apply", action="store_true", help="Ghi đè asr.json (+ sync script zh)")
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--force", action="store_true", help="OCR lại (bỏ cues cũ)")
    args = ap.parse_args(argv)
    run_hardsub_asr(
        args.work_dir,
        dur=args.dur,
        fps=args.fps,
        apply=args.apply,
        merge_only=args.merge_only,
        force=args.force,
    )


if __name__ == "__main__":
    main()
