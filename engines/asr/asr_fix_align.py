"""
asr_fix_align.py — Phương án 3: fix monotonic_fix

Bước 1 (fallback): chỗ timing bịa/đảo → trả start/end về Nano VAD
Bước 2 (forced align): Qwen3-ForcedAligner chỉ trên các đoạn vừa fallback
         → mốc mịn hơn, vẫn giữ chữ Nano

Đầu vào (sidecar):
  asr.aligned.json + asr.fun_nano.json + audio.wav

Đầu ra:
  asr.aligned_fix.json / sub.zh.aligned_fix.srt     (sau bước 1)
  asr.aligned_fa.json  / sub.zh.aligned_fa.srt       (sau bước 1+2)
  asr.aligned_fa.meta.json

Chạy:
  python asr_fix_align.py work/<job>                 # cả 1+2
  python asr_fix_align.py work/<job> --step1-only
  python asr_fix_align.py work/<job> --step2-only   # cần aligned_fix sẵn
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
import time
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from engines.asr.fun_asr_nano import _wav_dur
from engines.subs import to_srt, split_long_segments, sub_split_cfg
from engines.vram import free_gpu_vram

FA_MODEL = os.environ.get("QWEN3_FORCED_ALIGNER", "Qwen/Qwen3-ForcedAligner-0.6B")
DEFAULT_PAD_S = float(os.environ.get("ASR_FA_PAD_S", "0.40"))


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _save_asr(work: Path, tag: str, rows: list[dict]) -> tuple[Path, Path]:
    for i, r in enumerate(rows):
        r["i"] = i
    asr = work / f"asr.{tag}.json"
    srt = work / f"sub.zh.{tag}.srt"
    asr.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    srt.write_text(to_srt(rows, key="text"), encoding="utf-8")
    return asr, srt


def step1_nano_fallback(
    aligned: list[dict],
    nano: list[dict],
) -> tuple[list[dict], list[int]]:
    """
    Chỗ monotonic_fix (và đảo thời gian): lấy lại timing Nano theo chữ.
    Trả (rows, list index đã fallback).
    """
    bucket: dict[str, list[dict]] = defaultdict(list)
    for n in nano:
        t = (n.get("text") or "").strip()
        if t:
            bucket[t].append(n)

    used = defaultdict(int)
    out = [dict(r) for r in aligned]
    fixed_idx: list[int] = []

    # Chỉ sửa đúng monotonic_fix gốc — không cascade theo hàng đã đổi timing
    for i, r in enumerate(out):
        if r.get("align") != "monotonic_fix":
            continue
        text = (r.get("text") or "").strip()
        cands = bucket.get(text) or []
        k = used[text]
        if k < len(cands):
            n = cands[k]
            used[text] = k + 1
            r["start"] = round(float(n["start"]), 3)
            r["end"] = round(float(n["end"]), 3)
            r["align"] = "nano_fallback"
            fixed_idx.append(i)
        else:
            r["align"] = "nano_fallback_unmatched"
            fixed_idx.append(i)

    # làm sạch overlap nhẹ sau khi trả Nano
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["end"]:
            mid = (out[i - 1]["end"] + out[i]["start"]) / 2.0
            if out[i]["start"] >= out[i - 1]["start"]:
                out[i - 1]["end"] = round(mid, 3)
                out[i]["start"] = round(mid, 3)
            else:
                out[i]["start"] = out[i - 1]["end"]
        if out[i]["end"] <= out[i]["start"]:
            out[i]["end"] = round(out[i]["start"] + 0.25, 3)
    return out, fixed_idx


def _merge_indices_to_windows(
    rows: list[dict],
    indices: list[int],
    gap_s: float = 1.0,
) -> list[dict]:
    """Gộp index gần nhau thành cửa sổ FA."""
    if not indices:
        return []
    idxs = sorted(set(indices))
    groups: list[list[int]] = [[idxs[0]]]
    for i in idxs[1:]:
        prev = groups[-1][-1]
        if i <= prev + 2 or float(rows[i]["start"]) <= float(rows[prev]["end"]) + gap_s:
            groups[-1].append(i)
        else:
            groups.append([i])
    windows = []
    for g in groups:
        st = min(float(rows[i]["start"]) for i in g)
        en = max(float(rows[i]["end"]) for i in g)
        windows.append({"indices": g, "start": st, "end": en})
    return windows


def step2_forced_align(
    rows: list[dict],
    wav: Path,
    indices: list[int],
    device: str = "cuda:0",
    pad_s: float = DEFAULT_PAD_S,
    max_windows: int = 200,
) -> tuple[list[dict], dict]:
    """Forced-align từng cửa sổ chứa các câu nano_fallback."""
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_asr import Qwen3ForcedAligner

    windows = _merge_indices_to_windows(rows, indices)[:max_windows]
    meta = {"n_windows": len(windows), "n_fa_ok": 0, "n_fa_fail": 0, "model": FA_MODEL}
    if not windows:
        return rows, meta

    total = _wav_dur(wav)
    free_gpu_vram("trước Qwen3 ForcedAligner")
    print(f"[fa] load {FA_MODEL} trên {device} · {len(windows)} windows…", flush=True)
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    aligner = Qwen3ForcedAligner.from_pretrained(
        FA_MODEL, dtype=dtype, device_map=device,
    )

    out = [dict(r) for r in rows]
    with sf.SoundFile(str(wav), "r") as f:
        sr = int(f.samplerate)
        for wi, w in enumerate(windows):
            st, en = float(w["start"]), float(w["end"])
            ss = max(0.0, st - pad_s)
            ee = min(total, en + pad_s) if total > 0 else en + pad_s
            a0, a1 = int(ss * sr), int(ee * sr)
            f.seek(max(a0, 0))
            clip = f.read(max(a1 - a0, 0), dtype="float32")
            if clip is None or len(clip) < 160:
                meta["n_fa_fail"] += 1
                continue
            if getattr(clip, "ndim", 1) > 1:
                clip = clip.mean(axis=1)
            # text ghép các câu trong cửa sổ
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
            except Exception as e:  # noqa: BLE001
                print(f"[fa] fail @{st:.1f}s: {e}", flush=True)
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

            cursor = 0
            for ii in w["indices"]:
                raw = (out[ii].get("text") or "").replace(" ", "")
                nch = max(len([c for c in raw if c.strip()]), 1)
                # dùng độ dài ký tự hiển thị (kể cả punct) để chia
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
            if (wi + 1) % 10 == 0 or wi + 1 == len(windows):
                print(f"[fa] {wi+1}/{len(windows)} ok={meta['n_fa_ok']} fail={meta['n_fa_fail']}",
                      flush=True)

    # monotonic clean
    out.sort(key=lambda r: float(r["start"]))
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["end"]:
            mid = (out[i - 1]["end"] + out[i]["start"]) / 2
            out[i - 1]["end"] = round(mid, 3)
            out[i]["start"] = round(mid, 3)
        if out[i]["end"] <= out[i]["start"]:
            out[i]["end"] = round(out[i]["start"] + 0.2, 3)

    max_zh, _, max_dur = sub_split_cfg()
    out = split_long_segments(out, max_chars=max_zh, key="text", max_dur=max_dur)
    return out, meta


def run_fix(
    work_dir: str | Path,
    aligned_name: str = "asr.aligned.json",
    nano_name: str = "asr.fun_nano.json",
    device: str = "cuda:0",
    step1_only: bool = False,
    step2_only: bool = False,
    max_windows: int = 200,
) -> dict:
    work = Path(work_dir)
    wav = work / "audio.wav"
    t0 = time.time()

    if step2_only:
        fix_path = work / "asr.aligned_fix.json"
        if not fix_path.exists():
            raise FileNotFoundError(fix_path)
        rows = _load(fix_path)
        indices = [i for i, r in enumerate(rows) if r.get("align") in (
            "nano_fallback", "nano_fallback_unmatched", "monotonic_fix",
        )]
        print(f"[fix] step2-only · {len(indices)} câu cần FA", flush=True)
        rows2, fa_meta = step2_forced_align(
            rows, wav, indices, device=device, max_windows=max_windows,
        )
        asr, srt = _save_asr(work, "aligned_fa", rows2)
        modes = dict(Counter(r.get("align") for r in rows2))
        meta = {
            "status": "done", "steps": ["fa"], "sec": round(time.time() - t0, 1),
            "n_segments": len(rows2), "align_modes": modes,
            "fa": fa_meta, "asr_path": str(asr), "srt_path": str(srt),
        }
        (work / "asr.aligned_fa.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ok] FA → {asr.name} modes={modes}", flush=True)
        return meta

    aligned = _load(work / aligned_name)
    nano = _load(work / nano_name)
    print(f"[fix] step1 nano-fallback · aligned={len(aligned)} nano={len(nano)}", flush=True)
    rows1, fixed_idx = step1_nano_fallback(aligned, nano)
    asr1, srt1 = _save_asr(work, "aligned_fix", rows1)
    modes1 = dict(Counter(r.get("align") for r in rows1))
    print(f"[fix] step1 done · fixed={len(fixed_idx)} modes={modes1} → {asr1.name}", flush=True)

    meta = {
        "status": "done", "steps": ["fallback"],
        "n_fixed": len(fixed_idx), "align_modes_fix": modes1,
        "asr_fix": str(asr1), "srt_fix": str(srt1),
    }
    if step1_only:
        meta["sec"] = round(time.time() - t0, 1)
        (work / "asr.aligned_fix.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    print(f"[fix] step2 forced-align · {len(fixed_idx)} câu", flush=True)
    rows2, fa_meta = step2_forced_align(
        rows1, wav, fixed_idx, device=device, max_windows=max_windows,
    )
    asr2, srt2 = _save_asr(work, "aligned_fa", rows2)
    modes2 = dict(Counter(r.get("align") for r in rows2))
    meta.update({
        "steps": ["fallback", "fa"],
        "fa": fa_meta,
        "align_modes_fa": modes2,
        "asr_fa": str(asr2),
        "srt_fa": str(srt2),
        "n_segments": len(rows2),
        "sec": round(time.time() - t0, 1),
    })
    (work / "asr.aligned_fa.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] FA → {asr2.name} modes={modes2} ({meta['sec']}s)", flush=True)
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fix aligned: Nano fallback + ForcedAligner")
    ap.add_argument("work_dir")
    ap.add_argument("--aligned", default="asr.aligned.json")
    ap.add_argument("--nano", default="asr.fun_nano.json")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--step1-only", action="store_true")
    ap.add_argument("--step2-only", action="store_true")
    ap.add_argument("--max-windows", type=int, default=200)
    args = ap.parse_args(argv)
    run_fix(
        args.work_dir, aligned_name=args.aligned, nano_name=args.nano,
        device=args.device, step1_only=args.step1_only, step2_only=args.step2_only,
        max_windows=args.max_windows,
    )


if __name__ == "__main__":
    main()
