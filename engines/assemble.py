"""
assemble.py — Ghép clip TTS thành MỘT track + kiểm khớp SRT.

Hai chế độ:
  - assemble()      : NAIVE, đặt clip đúng mốc SRT (có thể ĐÈ). Chủ yếu để ĐO độ
                      khớp (số câu đè, tổng lố). Báo cáo tính từ metadata nên
                      chạy cho MỌI độ dài; chỉ dựng file audio khi video ngắn.
  - assemble_fit()  : Đủ câu (không cắt) + ghép CẠNH khi dài + bắt kịp ở khoảng
                      trống (mặc định nén thích ứng ≤1.12–1.30×). Sub = tts_fit.json.

"Khớp SRT": slot = end-start; dur = độ dài đọc; over_slot = dur-slot;
overlap_next = (start+dur) - start_câu_kế.
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
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

# Naive chỉ dựng file audio khi tổng <= mốc này (tránh OOM). Dài hơn -> chỉ báo cáo.
NAIVE_MAX_SEC = 1800


def _read_mono(path: str, target_sr: int) -> np.ndarray:
    """Đọc 1 clip về mono float32 @ target_sr (bounded — chỉ 1 clip trong RAM)."""
    wav, sr = sf.read(path, dtype="float32", always_2d=False)
    if getattr(wav, "ndim", 1) > 1:
        wav = wav.mean(axis=1)
    wav = np.asarray(wav, dtype=np.float32)
    if sr != target_sr and len(wav) > 1:
        try:
            import soxr                                   # resample chất lượng cao (chống rè/vỡ)
            wav = soxr.resample(wav, sr, target_sr, quality="VHQ").astype(np.float32)
        except Exception:  # noqa: BLE001  (không có soxr -> nội suy tuyến tính tạm)
            n = int(round(len(wav) * target_sr / sr))
            wav = np.interp(np.linspace(0, len(wav) - 1, n, dtype=np.float64),
                            np.arange(len(wav)), wav).astype(np.float32)
    # chuẩn hoá độ to (voiced RMS ~ -15.1 dBFS) giữa các câu để không bị câu to câu nhỏ
    voiced = wav[np.abs(wav) > 0.015]
    if len(voiced) >= int(target_sr * 0.04):
        rms = float(np.sqrt(np.mean(voiced * voiced)))
        if rms > 1e-4:
            gain = max(0.70, min(1.65, 0.175 / rms))
            wav = (wav * gain).astype(np.float32)
    # chống clipping: nếu đỉnh vượt ngưỡng -> hạ nhẹ để chừa headroom khi mix
    peak = float(np.max(np.abs(wav))) if len(wav) else 0.0
    if peak > 0.90:
        wav = (wav * (0.90 / peak)).astype(np.float32)
    # micro-fade 6ms đầu/cuối mỗi clip chống bụp/tách ở ranh giới ghép câu
    fade_n = min(int(target_sr * 0.006), len(wav) // 4)
    if fade_n >= 4:
        ramp = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
        wav[:fade_n] *= ramp
        wav[-fade_n:] *= ramp[::-1]
    return wav


def _compress_to_file(in_path: str, ratio: float) -> str:
    """Nén 'ratio'× bằng rubberband giữ formant -> file tạm. ratio<=1 trả nguyên in_path."""
    if ratio <= 1.001:
        return in_path
    fd, out = tempfile.mkstemp(suffix=".wav", prefix="fit_")
    os.close(fd)
    for filt in (
        f"rubberband=tempo={ratio:.3f}:formant=preserved:pitchq=quality:transients=crisp",
        f"rubberband=tempo={ratio:.3f}:formant=preserved",
        f"rubberband=tempo={ratio:.3f}",
        f"atempo={min(ratio, 2.0):.3f}",
    ):
        try:
            subprocess.run(["ffmpeg", "-y", "-i", in_path, "-filter:a", filt, out],
                           check=True, capture_output=True)
            return out
        except Exception:  # noqa: BLE001 -> thử filter kế
            continue
    return in_path


def _load_clips(work_dir: Path) -> list[dict]:
    clips = [c for c in json.loads((work_dir / "tts.json").read_text(encoding="utf-8"))
             if c.get("audio")]
    clips.sort(key=lambda c: c["start"])
    if not clips:
        raise RuntimeError("tts.json rỗng.")
    return clips


def _slot_sec(c: dict) -> float:
    return max(float(c.get("end") or 0) - float(c.get("start") or 0), 0.15)


def _natural_max_sec(vi: str, slot: float) -> float:
    """Trần độ dài đọc hợp lý (không phải câu TTS runaway)."""
    n = len((vi or "").strip()) or 1
    return max(slot * 1.85, n * 0.22 + 0.5, 0.45)


def _runaway_cap_sec(c: dict, dur: float) -> float | None:
    """VieNeu đôi khi hú 15–23s cho câu 1–2 tiếng. Cắt trần, không đẩy cả film.

    Câu dài thật (nhiều chữ, wav ~ vài giây lố slot) vẫn giữ nguyên — ghép cạnh.
    """
    if dur <= 2.0:
        return None
    slot = _slot_sec(c)
    cap = _natural_max_sec(c.get("vi") or "", slot)
    if dur > max(cap * 2.2, slot + 1.2):
        # Cắt theo slot hình, không theo trần đọc — wav runaway không phải lời thật.
        return float(max(min(slot, cap), 0.4))
    return None


def is_runaway_tts(c: dict, dur: float) -> bool:
    """True nếu wav quá dài bất thường so với slot/chữ (VieNeu hú)."""
    return _runaway_cap_sec(c, float(dur or 0)) is not None


def _resolve_max_speed(explicit: float | None) -> float:
    """UI/API thắng. Env ASSEMBLE_MAX_SPEED chỉ khi không truyền (None)."""
    if explicit is not None:
        return float(explicit)
    return float(os.getenv("ASSEMBLE_MAX_SPEED") or 1.0)


# Thích ứng: câu lố khe nén ≤1.10× (gần như không nghe ra); giọng đã trễ thì trần
# tăng theo độ trễ tới 1.20× để kéo về kịp hình mà không làm méo/rè giọng đã qua TTS speed.
# Câu TTS phình / mốc ASR dồn ≤1.35×.
FIT_BASE, FIT_TOP, FIT_GAIN, FIT_LEAD, FIT_BLOAT = 1.10, 1.20, 0.45, 0.15, 1.35
FIT_STAMP = ".fit_mode"   # dub_fit.wav không có stamp = ghép bằng code cũ (1.0× cứng)


def _fit_mode(max_speed: float) -> str:
    """fixed (max_speed>1 từ UI/API) | adaptive (mặc định) | off (ASSEMBLE_FIT_MODE=off)."""
    if float(max_speed or 1.0) > 1.01:
        return "fixed"
    m = (os.getenv("ASSEMBLE_FIT_MODE") or "adaptive").strip().lower()
    return "off" if m in ("off", "0", "none", "1.0") else "adaptive"


def _is_bloated(c: dict, dur: float) -> bool:
    """TTS kéo dài bất thường so với số chữ, hoặc ASR dồn mốc (khe ≤0.25s mà đọc >0.6s)."""
    n = len((c.get("vi") or "").strip()) or 1
    return dur > n * 0.11 + 0.9 or (_slot_sec(c) <= 0.25 and dur > 0.6)


def _place(c: dict, dur: float, orig: float, nxt: float, cursor: float, gap: float,
           mode: str, max_speed: float) -> tuple[float, float]:
    """(start, ratio) của một câu theo chế độ khớp."""
    start = max(orig, cursor)
    if mode == "off":
        return start, 1.0
    if mode == "adaptive" and cursor < orig and dur > nxt - orig - gap:
        start = max(orig - FIT_LEAD, cursor)
    avail = max(nxt - start - gap, 0.25)
    if dur <= avail + 0.05:
        return start, 1.0
    if mode == "fixed":
        cap = float(max_speed)
    elif _is_bloated(c, dur):
        cap = FIT_BLOAT
    else:
        cap = min(FIT_TOP, FIT_BASE + FIT_GAIN * max(0.0, start - orig))
    return start, max(1.0, min(dur / avail, cap))


def compute_fit_placements(clips: list[dict], max_speed: float = 1.0,
                           gap: float = 0.06) -> list[dict]:
    """Đủ câu + khớp hình theo khoảng trống (KHÔNG cắt, nén theo _place).

    - Đọc đủ / sub đủ: câu vừa khe giữ nguyên dur TTS; câu lố khe nén theo _fit_mode.
    - Câu dài: ghép CẠNH câu trước (start = max(mốc_hình, hết_câu_trước)), không cắt.
    - Lệch nhẹ đoạn dày OK; khoảng trống sau + câu kế kịp mốc thì tự về khớp hình.
    """
    placed: list[dict] = []
    cursor = 0.0
    mode = _fit_mode(max_speed)
    for idx, c in enumerate(clips):
        dur = float(c.get("dur") or 0.0)
        orig_dur = dur
        cap = _runaway_cap_sec(c, dur)
        truncated = False
        if cap is not None:
            dur = cap
            truncated = True
        orig = float(c["start"])
        nxt = float(clips[idx + 1]["start"]) if idx + 1 < len(clips) else float(
            c.get("end") or (orig + max(dur, 0.3)))
        # Ghép cạnh nếu câu trước còn kéo dài; nếu đã qua mốc hình thì đặt đúng mốc
        start, ratio = _place(c, dur, orig, nxt, cursor, gap, mode, max_speed)
        drift = start - orig
        fit_dur = (dur / ratio) if ratio > 1.001 and dur > 0 else dur
        if fit_dur <= 0.05:
            fit_dur = max(dur, 0.15)
        end = start + fit_dur
        placed.append({
            "i": c["i"],
            "start": round(start, 3),
            "end": round(end, 3),
            "dur": round(fit_dur, 3),
            "orig_start": round(orig, 3),
            "orig_end": round(float(c.get("end") or orig), 3),
            "orig_dur": round(orig_dur, 3),
            "ratio": round(ratio, 3),
            "drift": round(drift, 3),
            "truncated": truncated,
            "vi": c.get("vi") or "",
        })
        cursor = end + gap
    return placed


def write_fit_timings(work_dir: str | Path, max_speed: float | None = None,
                      gap: float = 0.06) -> Path:
    """Ghi tts_fit.json từ tts.json (không cần ghép lại audio)."""
    work_dir = Path(work_dir)
    max_speed = _resolve_max_speed(max_speed)
    clips = _load_clips(work_dir)
    placed = compute_fit_placements(clips, max_speed=max_speed, gap=gap)
    out = work_dir / "tts_fit.json"
    out.write_text(json.dumps(placed, ensure_ascii=False), encoding="utf-8")
    n_fast = sum(1 for p in placed if float(p.get("ratio") or 1) > 1.05)
    n_drift = sum(1 for p in placed if float(p.get("drift") or 0) > 0.5)
    max_d = max((float(p.get("drift") or 0) for p in placed), default=0.0)
    print(f"[fit-timing] {len(placed)} câu -> {out.name} · nén {n_fast} · "
          f"lệch>0.5s {n_drift} · max {max_d:.2f}s · max_speed={max_speed} ({_fit_mode(max_speed)}) "
          f"(đủ câu, ghép cạnh)", flush=True)
    return out


# ---------------------------------------------------------------------------
def assemble(work_dir: str | Path, sr: int = 48000, out_name: str = "dub_tts.wav") -> dict:
    """NAIVE: báo cáo độ khớp (từ metadata, mọi độ dài). Dựng file chỉ khi ngắn."""
    work_dir = Path(work_dir)
    clips = _load_clips(work_dir)

    report, n_overflow, n_overlap, overflow_total = [], 0, 0, 0.0
    for idx, c in enumerate(clips):
        dur = c.get("dur") or 0.0
        slot = c["end"] - c["start"]
        over_slot = dur - slot
        nxt = clips[idx + 1]["start"] if idx + 1 < len(clips) else None
        overlap_next = (c["start"] + dur - nxt) if nxt is not None else 0.0
        if over_slot > 0.05:
            n_overflow += 1
            overflow_total += over_slot
        if overlap_next > 0.05:
            n_overlap += 1
        report.append({"i": c["i"], "start": round(c["start"], 2), "slot": round(slot, 2),
                       "dur": round(dur, 2), "over_slot": round(over_slot, 2),
                       "overlap_next": round(overlap_next, 2), "vi": c.get("vi", "")})

    total = max((c["start"] + (c.get("dur") or 0)) for c in clips)
    out_path = None
    if total <= NAIVE_MAX_SEC:                       # đủ ngắn -> dựng file để nghe
        track = np.zeros(int(total * sr) + sr, dtype=np.float32)
        for c in clips:
            wav = _read_mono(c["audio"], sr)
            pos = int(c["start"] * sr)
            end = pos + len(wav)
            if end > len(track):
                track = np.concatenate([track, np.zeros(end - len(track), dtype=np.float32)])
            track[pos:end] += wav
        peak = float(np.max(np.abs(track))) or 1.0
        track = (track / peak * 0.95).astype(np.float32)
        out = work_dir / out_name
        sf.write(str(out), track, sr)
        out_path = str(out)

    worst = sorted(report, key=lambda r: r["overlap_next"], reverse=True)[:8]
    return {"out": out_path, "track_dur": round(total, 2), "n_clips": len(clips),
            "n_overflow": n_overflow, "n_overlap": n_overlap,
            "overflow_total": round(overflow_total, 1), "worst": worst,
            "note": None if out_path else f"video >{NAIVE_MAX_SEC}s: chỉ báo cáo, dùng 'Ghép khớp time'"}


# ---------------------------------------------------------------------------
def assemble_fit(work_dir: str | Path, sr: int = 48000, max_speed: float | None = None,
                 gap: float = 0.06, out_name: str = "dub_fit.wav") -> dict:
    """Đủ câu + ghép cạnh + bắt kịp khoảng trống (đúng yêu cầu user).

    1) Đọc đủ / không cắt đuôi — sub theo tts_fit đủ câu.
    2) Câu dài: đặt CẠNH câu trước (start=max(mốc_hình, hết_câu_trước)).
    3) max_speed ≤1: nén thích ứng (_place); >1: trần cố định. ASSEMBLE_FIT_MODE=off = không nén.
    4) Khoảng trống sau: câu kế đặt lại đúng mốc hình khi kịp.
    """
    work_dir = Path(work_dir)
    max_speed = _resolve_max_speed(max_speed)
    mode = _fit_mode(max_speed)
    print(f"[assemble_fit] max_speed={max_speed} mode={mode}", flush=True)
    clips = _load_clips(work_dir)
    out = work_dir / out_name
    # Ghi .partial rồi replace; gỡ stamp trước — crash giữa chừng thì lần sau ghép lại.
    part = out.with_name(out.stem + ".partial" + out.suffix)
    try:
        (work_dir / FIT_STAMP).unlink(missing_ok=True)
    except OSError:
        pass

    n_compressed = 0
    n_truncated = 0
    max_ratio = 1.0
    n_overlap = 0
    written = 0
    silence = np.zeros(sr, dtype=np.float32)
    placed: list[dict] = []
    cursor = 0.0

    with sf.SoundFile(str(part), "w", samplerate=sr, channels=1, subtype="PCM_24") as w:
        for idx, c in enumerate(clips):
            dur = float(c.get("dur") or 0.0)
            orig_dur = dur
            cap = _runaway_cap_sec(c, dur)
            truncated = False
            if cap is not None:
                dur = cap
                truncated = True
                n_truncated += 1
            orig = float(c["start"])
            nxt = float(clips[idx + 1]["start"]) if idx + 1 < len(clips) else float(
                c.get("end") or (orig + max(dur, 0.3)))
            start, ratio = _place(c, dur, orig, nxt, cursor, gap, mode, max_speed)

            src = _compress_to_file(c["audio"], ratio) if ratio > 1.001 else c["audio"]
            wav = _read_mono(src, sr)
            if src != c["audio"]:
                n_compressed += 1
                max_ratio = max(max_ratio, ratio)
                try:
                    os.remove(src)
                except Exception:  # noqa: BLE001
                    pass
            if truncated and len(wav):
                # wav đã nén theo ratio → trần cũng phải chia ratio (khớp compute_fit_placements)
                cut = dur / ratio if ratio > 1.001 else dur
                wav = wav[: max(1, int(round(cut * sr)))]

            start_s = int(round(start * sr))
            if start_s < written:
                start_s = written
            gap_s = start_s - written
            while gap_s > 0:
                n = min(gap_s, len(silence))
                w.write(silence[:n])
                gap_s -= n
            written = start_s
            fit_start = written / float(sr)
            if len(wav):
                w.write(wav)  # đủ câu thường; runaway TTS thì đã cắt trần
                written += len(wav)
            fit_end = written / float(sr)

            placed.append({
                "i": c["i"],
                "start": round(fit_start, 3),
                "end": round(fit_end, 3),
                "dur": round(fit_end - fit_start, 3),
                "orig_start": round(orig, 3),
                "orig_end": round(float(c.get("end") or orig), 3),
                "orig_dur": round(orig_dur, 3),
                "ratio": round(float(ratio), 3),
                "drift": round(fit_start - orig, 3),
                "truncated": truncated,
                "vi": c.get("vi") or "",
            })
            cursor = fit_end + gap

    for i in range(20):
        try:
            os.replace(part, out)
            break
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.1 * (i + 1))
    fit_path = work_dir / "tts_fit.json"
    fit_path.write_text(json.dumps(placed, ensure_ascii=False), encoding="utf-8")
    (work_dir / FIT_STAMP).write_text(f"{mode}:{max_speed}", encoding="utf-8")
    max_drift = max((float(p["drift"]) for p in placed), default=0.0)
    n_drift = sum(1 for p in placed if float(p["drift"]) > 0.5)

    return {"out": str(out), "track_dur": round(written / sr, 2),
            "video_dur": round(float(clips[-1]["end"]), 2), "n_clips": len(clips),
            "n_compressed": n_compressed, "n_truncated": n_truncated, "n_overlap": n_overlap,
            "n_drift_gt_0_5": n_drift,
            "max_speed_used": round(max_ratio, 2), "max_drift": round(max_drift, 3),
            "fit_timings": str(fit_path)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ghép clip TTS + kiểm khớp SRT.")
    ap.add_argument("work_dir")
    ap.add_argument("--sr", type=int, default=48000)
    ap.add_argument("--fit", action="store_true",
                    help="Đủ câu, ghép cạnh, bắt kịp khoảng trống (mặc định nén thích ứng)")
    ap.add_argument("--max-speed", type=float, default=1.0,
                    help=">1 mới nén; mặc định 1.0 = tốc độ gốc")
    args = ap.parse_args(argv)
    if args.fit:
        r = assemble_fit(args.work_dir, sr=args.sr, max_speed=args.max_speed)
        print(f"[fit] {r['out']} | track {r['track_dur']}s (video ~{r['video_dur']}s) | "
              f"nén {r['n_compressed']}/{r['n_clips']} | cắt runaway {r.get('n_truncated', 0)} | "
              f"lệch>0.5s {r.get('n_drift_gt_0_5', 0)} | drift max {r['max_drift']}s")
        return
    r = assemble(args.work_dir, sr=args.sr)
    print(f"[naive] {r['n_clips']} câu | đè {r['n_overlap']} | lố ô {r['n_overflow']} | "
          f"tổng lố {r['overflow_total']}s | file: {r['out'] or r['note']}")


if __name__ == "__main__":
    main()
