"""
nano_fa_asr.py — SOTA ASR: Fun-ASR-Nano (Chữ chuẩn nhất) + Direct Qwen3-ForcedAligner (Mốc chuẩn từng chữ).

Kiến trúc sạch, chuẩn công nghiệp:
  1) Fun-ASR-Nano nhận diện văn bản (hiểu ngữ cảnh, ít sai chữ, chống lặp qua VAD)
  2) Qwen3-ForcedAligner ghim mốc trực tiếp (bắt dính audio vào từng từ, không dùng Paraformer)
  3) KHÔNG dùng DTW (không lo lệch trôi dạt)
  4) KHÔNG có bước Repair lấp lỗ (triệt tiêu 100% rủi ro đúp câu / câu ma)

Chạy:
  python nano_fa_asr.py work/<job>
  python nano_fa_asr.py work/<job> --force
  python nano_fa_asr.py work/<job> --device cuda:0
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[2])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import json
import os
import shutil
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
from engines.asr.fun_asr_nano import run_asr_nano, _wav_dur
from engines.subs import to_srt, split_long_segments, sub_split_cfg
from engines.vram import free_gpu_vram

STAGE = "asr"
ENGINE = "nano_fa"
MODEL_ID = "Fun-ASR-Nano + Qwen3-ForcedAligner"
FA_MODEL = os.environ.get("QWEN3_FORCED_ALIGNER", "Qwen/Qwen3-ForcedAligner-0.6B")
DEFAULT_PAD_S = float(os.environ.get("ASR_FA_PAD_S", "0.35"))


def _merge_all_to_windows(
    rows: list[dict],
    gap_s: float = 0.8,
    max_win_s: float = 24.0,
) -> list[dict]:
    """Gom các câu thoại gần nhau thành cửa sổ nhỏ (<=24s) để Forced Aligner xử lý tối ưu."""
    if not rows:
        return []
    windows: list[dict] = []
    cur_indices: list[int] = [0]
    win_st = float(rows[0]["start"])

    for i in range(1, len(rows)):
        r = rows[i]
        st = float(r["start"])
        en = float(r["end"])
        prev_en = float(rows[cur_indices[-1]]["end"])

        # Nếu khoảng trống lớn hoặc tổng thời lượng cửa sổ vượt quá max_win_s -> ngắt cửa sổ mới
        if (st - prev_en > gap_s) or (en - win_st > max_win_s):
            w_st = min(float(rows[idx]["start"]) for idx in cur_indices)
            w_en = max(float(rows[idx]["end"]) for idx in cur_indices)
            windows.append({"indices": cur_indices, "start": w_st, "end": w_en})
            cur_indices = [i]
            win_st = st
        else:
            cur_indices.append(i)

    if cur_indices:
        w_st = min(float(rows[idx]["start"]) for idx in cur_indices)
        w_en = max(float(rows[idx]["end"]) for idx in cur_indices)
        windows.append({"indices": cur_indices, "start": w_st, "end": w_en})

    return windows


def align_nano_with_fa(
    rows: list[dict],
    wav: Path,
    device: str = "cuda:0",
    pad_s: float = DEFAULT_PAD_S,
    on_progress=None,
) -> tuple[list[dict], dict]:
    """Căn mốc trực tiếp từng câu của Nano bằng Qwen3ForcedAligner."""
    import numpy as np
    import soundfile as sf
    import torch

    try:
        from qwen_asr import Qwen3ForcedAligner
    except ImportError:
        print("[nano_fa] Thiếu thư viện qwen_asr → giữ mốc VAD gốc của Nano", flush=True)
        return rows, {"n_fa_ok": 0, "n_fa_fail": len(rows), "error": "missing qwen_asr"}

    windows = _merge_all_to_windows(rows)
    meta = {"n_windows": len(windows), "n_fa_ok": 0, "n_fa_fail": 0, "model": FA_MODEL}
    if not windows:
        return rows, meta

    total_dur = _wav_dur(wav)
    free_gpu_vram("trước Qwen3 ForcedAligner")
    print(f"[{ENGINE}] Nạp Forced Aligner {FA_MODEL} trên {device} · {len(windows)} cửa sổ…", flush=True)

    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    try:
        aligner = Qwen3ForcedAligner.from_pretrained(
            FA_MODEL, dtype=dtype, device_map=device,
        )
    except Exception as e:
        print(f"[{ENGINE}] Lỗi nạp FA model: {e} → fallback mốc VAD Nano", flush=True)
        return rows, {"n_fa_ok": 0, "n_fa_fail": len(windows), "error": str(e)}

    out = [dict(r) for r in rows]
    with sf.SoundFile(str(wav), "r") as f:
        sr = int(f.samplerate)
        n_win = len(windows)
        for wi, w in enumerate(windows):
            st, en = float(w["start"]), float(w["end"])
            ss = max(0.0, st - pad_s)
            ee = min(total_dur, en + pad_s) if total_dur > 0 else en + pad_s
            a0, a1 = int(ss * sr), int(ee * sr)

            f.seek(max(a0, 0))
            clip = f.read(max(a1 - a0, 0), dtype="float32")
            if clip is None or len(clip) < 160:
                meta["n_fa_fail"] += 1
                continue
            if getattr(clip, "ndim", 1) > 1:
                clip = clip.mean(axis=1)

            # Ghép văn bản của tất cả các câu trong cửa sổ này
            parts = [out[i] for i in w["indices"]]
            text = "".join((p.get("text") or "").strip() for p in parts)
            if not text:
                meta["n_fa_fail"] += 1
                continue

            try:
                results = aligner.align(
                    audio=(np.asarray(clip, dtype=np.float32), sr),
                    text=text,
                    language="Chinese",
                )
            except Exception as e:
                meta["n_fa_fail"] += 1
                continue

            if not results or not results[0].items:
                meta["n_fa_fail"] += 1
                continue

            items = results[0].items
            flat = []
            for it in items:
                t = (it.text or "").replace(" ", "")
                for ch in t:
                    flat.append((ch, float(it.start_time) + ss, float(it.end_time) + ss))

            if not flat:
                meta["n_fa_fail"] += 1
                continue

            # Phân bổ timestamp chi tiết cho từng câu trong window
            cursor = 0
            for ii in w["indices"]:
                raw = (out[ii].get("text") or "").replace(" ", "")
                nch = max(len(raw), 1)
                chunk = flat[cursor: cursor + nch]
                if len(chunk) < nch and cursor < len(flat):
                    chunk = flat[cursor:]
                cursor += nch
                if not chunk:
                    continue

                out[ii]["start"] = round(chunk[0][1], 3)
                out[ii]["end"] = round(max(chunk[-1][2], out[ii]["start"] + 0.15), 3)
                out[ii]["align"] = "forced_align"

            meta["n_fa_ok"] += 1
            if on_progress and (wi % 10 == 0 or wi + 1 == n_win):
                pct = 60 + int(35 * (wi + 1) / max(n_win, 1))
                on_progress(min(pct, 95), f"ForcedAlign {wi+1}/{n_win} ({meta['n_fa_ok']} ok)", len(out), en)

            if (wi + 1) % 25 == 0 or wi + 1 == n_win:
                print(f"[{ENGINE}] FA {wi+1}/{n_win} · ok={meta['n_fa_ok']} fail={meta['n_fa_fail']} · @{en:.0f}s", flush=True)

    # Chuẩn hoá monotonic (không để câu sau đè lên câu trước)
    out.sort(key=lambda r: float(r["start"]))
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["end"]:
            mid = (out[i - 1]["end"] + out[i]["start"]) / 2
            out[i - 1]["end"] = round(mid, 3)
            out[i]["start"] = round(mid, 3)
        if out[i]["end"] <= out[i]["start"]:
            out[i]["end"] = round(out[i]["start"] + 0.2, 3)

    return out, meta


def run_nano_fa(
    work_dir: str | Path,
    device: str = "auto",
    force: bool = False,
    hub_nano: str = "hf",
    sidecar: str | None = None,
) -> dict:
    work_dir = Path(work_dir)
    m = Manifest(work_dir)
    tag = (sidecar or "").strip().replace(" ", "_")
    is_side = bool(tag)

    if not is_side and m.is_done(STAGE) and not force:
        st = m.stage(STAGE)
        if st and Path(st.get("asr_path", "")).exists():
            print(f"[skip] ASR đã xong: {st['asr_path']}")
            return st

    asr_path = work_dir / (f"asr.{tag}.json" if is_side else "asr.json")
    srt_path = work_dir / (f"sub.zh.{tag}.srt" if is_side else "sub.zh.srt")
    wav = work_dir / "audio.wav"

    if not is_side:
        m.mark_running(STAGE)

    def _prog(pct, text, n_seg=0, done_sec=0):
        st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
        st.update({
            "engine": ENGINE, "asr_engine": ENGINE, "model": MODEL_ID,
            "status": "running", "progress": int(pct), "cur_text": text,
            "n_segments": n_seg, "done_sec": float(done_sec or 0),
            "updated_at": time.time(),
        })
        m.data["stages"][STAGE] = st
        js.persist(m)

    t0 = time.time()
    try:
        # Bước 1: Fun-ASR-Nano nhận diện văn bản sạch
        nano_sidecar = work_dir / "asr.fun_nano.json"
        need_nano = force or not nano_sidecar.exists()
        if need_nano:
            if not is_side:
                _prog(10, "Bước 1/2: Fun-ASR-Nano nhận diện văn bản…")
            free_gpu_vram("trước Nano")
            run_asr_nano(work_dir, device=device, force=force, hub=hub_nano, sidecar="fun_nano")

        if not nano_sidecar.exists():
            raise RuntimeError(f"Thiếu file kết quả Fun-ASR-Nano: {nano_sidecar}")

        raw_nano = json.loads(nano_sidecar.read_text(encoding="utf-8"))
        print(f"[{ENGINE}] Đã nạp {len(raw_nano)} câu từ Fun-ASR-Nano. Bắt đầu ForcedAlign…", flush=True)

        # Bước 2: Direct Forced Alignment ghim mốc trực tiếp
        if not is_side:
            _prog(55, f"Bước 2/2: Qwen3-ForcedAligner ghim mốc {len(raw_nano)} câu…")

        dev_fa = device if device != "auto" else ("cuda:0" if _torch_has_cuda() else "cpu")
        aligned_rows, fa_meta = align_nano_with_fa(
            raw_nano, wav=wav, device=dev_fa,
            on_progress=_prog if not is_side else None,
        )

        # Cắt câu quá dài nếu có
        max_zh, _, max_dur = sub_split_cfg()
        rows = split_long_segments(aligned_rows, max_chars=max_zh, key="text", max_dur=max_dur)

        # Lọc spam / lặp ảo
        try:
            from engines.asr.asr_spam import sanitize_asr_rows
            rows, spam_info = sanitize_asr_rows(rows, work_dir=work_dir, source_path=asr_path, heal=False)
            if spam_info.get("n_cleaned"):
                print(f"[{ENGINE}] spam cleaned: {spam_info['n_cleaned']}", flush=True)
        except Exception as e:
            print(f"[{ENGINE}] asr_spam: {e}", flush=True)

        for i, r in enumerate(rows):
            r["i"] = i

        # Lưu file kết quả
        asr_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        srt_path.write_text(to_srt(rows, key="text"), encoding="utf-8")

        # Lưu bản backup nano_fa sidecar
        side_json = work_dir / "asr.nano_fa.json"
        side_srt = work_dir / "sub.zh.nano_fa.srt"
        shutil.copy2(asr_path, side_json)
        shutil.copy2(srt_path, side_srt)

        n = len(rows)
        dur_sec = round(time.time() - t0, 1)
        if is_side:
            result = {
                "status": "done", "engine": ENGINE, "model": MODEL_ID,
                "asr_path": str(asr_path), "srt_path": str(srt_path),
                "n_segments": n, "language": "zh", "sidecar": tag, "sec": dur_sec,
            }
        else:
            result = m.mark_done(
                STAGE, asr_path=str(asr_path), audio_path=str(wav),
                n_segments=n, language="zh", model=MODEL_ID, engine=ENGINE,
                asr_engine=ENGINE, fa_ok=fa_meta.get("n_fa_ok", 0),
                sec=dur_sec,
            )
        print(f"[ok] {ENGINE}: Hoàn tất {n} câu (FA ok={fa_meta.get('n_fa_ok', 0)}/{fa_meta.get('n_windows', 0)}) trong {dur_sec}s → {asr_path}")
        return result

    except Exception as e:
        if not is_side:
            st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
            st.update({"status": "failed", "error": str(e), "updated_at": time.time()})
            m.data["stages"][STAGE] = st
            js.persist(m)
        raise


def _torch_has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="ASR SOTA: Fun-ASR-Nano + Direct Qwen3 Forced Aligner")
    ap.add_argument("work_dir")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sidecar", default=None)
    ap.add_argument("--hub-nano", default="hf", choices=("hf", "ms"))
    args = ap.parse_args(argv)
    run_nano_fa(args.work_dir, device=args.device, force=args.force,
                hub_nano=args.hub_nano, sidecar=args.sidecar)
    os._exit(0)


if __name__ == "__main__":
    main()

