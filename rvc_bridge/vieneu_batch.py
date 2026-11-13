# -*- coding: utf-8 -*-
"""Worker VieNeu-TTS v3-Turbo (chạy trong D:/vieneu_env). Đọc job json:
   {out_dir, ref, text, device, batch_size, lines:[[i,"vi"],...]}
-> mỗi câu ghi seg_XXXX.wav (48kHz). In "prog <done>/<total>" cho orchestrator.

Tối ưu tốc độ:
  - Enroll giọng 1 lần (add_voice), không encode lại mỗi lô
  - infer_batch gộp nhiều câu/forward trên GPU
  - CPU threads: mặc định vừa đủ (không khóa 2 như chống-treo)

Máy dễ treo (14900K): set VIENEU_CPU_THREADS=2  VIENEU_LOW_PRIORITY=1
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Windows venv: torch/onnx có thể spawn lại bằng base C:\Python312 — ép giữ python venv.
try:
    import multiprocessing as _mp
    _mp.set_executable(sys.executable)
except Exception:
    pass
os.environ.setdefault("PYTHONEXECUTABLE", sys.executable)


def _cap_cpu_threads() -> int:
    # Mặc định: ~1/4 số luồng, kẹp 4..8 — nhanh hơn khóa cứng 2, vẫn không chiếm hết CPU.
    auto = max(4, min(8, (os.cpu_count() or 8) // 4))
    raw = os.getenv("VIENEU_CPU_THREADS", str(auto))
    try:
        n = max(1, int(raw))
    except ValueError:
        n = auto
    for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
              "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(k, str(n))
    return n


_CPU_THREADS = _cap_cpu_threads()


def _lower_priority() -> None:
    """Chỉ bật khi VIENEU_LOW_PRIORITY=1 (máy dễ treo). Mặc định TẮT để giữ tốc độ."""
    if os.getenv("VIENEU_LOW_PRIORITY", "0") != "1":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL
        print("process priority=below_normal", flush=True)
    except Exception:  # noqa: BLE001
        pass


_lower_priority()

import soundfile as sf
from vieneu import Vieneu


def _write_one(out_dir: Path, i: int, audio, sr: int) -> None:
    sf.write(str(out_dir / f"seg_{i:04d}.wav"), audio, sr)


def _infer_group(tts, texts: list[str], voice_name: str, batch_size: int):
    """Dùng voice đã enroll — không truyền ref_audio (tránh encode lại mỗi lô)."""
    if len(texts) == 1 or batch_size <= 1 or not hasattr(tts, "infer_batch"):
        return [
            tts.infer(t, voice=voice_name, apply_watermark=False, batch_size=1)
            for t in texts
        ]
    try:
        return tts.infer_batch(
            texts, voice=voice_name, apply_watermark=False, batch_size=batch_size
        )
    except Exception as e:  # noqa: BLE001
        print(f"WARN infer_batch fail ({repr(e)[:120]}) -> fallback 1-by-1", flush=True)
        out = []
        for t in texts:
            try:
                out.append(tts.infer(t, voice=voice_name, apply_watermark=False, batch_size=1))
            except Exception as e2:  # noqa: BLE001
                print(f"ERR fallback: {repr(e2)[:160]}", flush=True)
                out.append(None)
        return out


def main(job_path: str) -> None:
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    out_dir = Path(job["out_dir"]); out_dir.mkdir(parents=True, exist_ok=True)
    ref = job["ref"]
    device = job.get("device", "cpu")
    lines = job["lines"]
    total = len(lines)

    want_cuda = "cuda" in str(device).lower()
    raw_bs = job.get("batch_size", os.getenv("VIENEU_BATCH", "24" if want_cuda else "1"))
    try:
        batch_size = max(1, int(raw_bs))
    except (TypeError, ValueError):
        batch_size = 24 if want_cuda else 1
    if not want_cuda:
        batch_size = 1

    try:
        import torch
        torch.set_num_threads(_CPU_THREADS)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        print(f"cpu_threads={_CPU_THREADS}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"cpu_threads set skip: {e!r}", flush=True)

    kw = {"mode": "v3turbo", "device": device, "max_batch_size": batch_size}
    if want_cuda:
        kw["backend"] = "pytorch"

    print(
        f"loading vieneu device={device} backend={kw.get('backend', 'auto')} "
        f"batch_size={batch_size}",
        flush=True,
    )
    tts = Vieneu(**kw)
    backend = getattr(tts, "backend", "?")
    eng = getattr(tts, "engine", None)
    eng_dev = getattr(eng, "device", None)
    print(f"vieneu ready backend={backend} engine.device={eng_dev}", flush=True)
    if want_cuda and str(backend).lower() != "pytorch":
        print(f"ERR backend={backend} (expected pytorch). Dang chay CPU/ONNX.", flush=True)
        batch_size = 1

    # Enroll 1 lần — trước đây mỗi lô gọi lại prepare_reference (rất chậm).
    voice_name = "_pineline_job"
    print(f"enroll voice from {ref}", flush=True)
    tts.add_voice(voice_name, ref, denoise=True, use_ref_codes=True, save=False)
    print("enroll done", flush=True)

    try:
        import torch
        if torch.cuda.is_available():
            print(
                f"cuda mem_alloc_mb={round(torch.cuda.memory_allocated() / 1e6, 1)} "
                f"mem_reserved_mb={round(torch.cuda.memory_reserved() / 1e6, 1)}",
                flush=True,
            )
            if want_cuda and torch.cuda.memory_allocated() < 1e6:
                print("ERR model khong nam tren GPU (VRAM ~0 sau load).", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"cuda mem check skip: {e!r}", flush=True)

    sr = int(getattr(tts, "sampling_rate", None) or getattr(tts, "sample_rate", 48000))

    done = 0
    for start in range(0, total, batch_size):
        group = lines[start : start + batch_size]
        idxs = [i for i, _ in group]
        texts = [vi for _, vi in group]
        try:
            wavs = _infer_group(tts, texts, voice_name, batch_size)
        except Exception as e:  # noqa: BLE001
            print(f"ERR group@{start}: {repr(e)[:160]}", flush=True)
            wavs = [None] * len(texts)
        for i, audio in zip(idxs, wavs):
            if audio is None:
                print(f"ERR {i}: no audio", flush=True)
            else:
                try:
                    _write_one(out_dir, i, audio, sr)
                except Exception as e:  # noqa: BLE001
                    print(f"ERR {i}: write {repr(e)[:120]}", flush=True)
            done += 1
            print(f"prog {done}/{total}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
