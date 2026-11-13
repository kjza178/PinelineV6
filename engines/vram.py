"""vram.py — Nhả VRAM trước MỖI bước dùng GPU.

Ollama (Gemma ctx 128k), FunASR, Demucs, TTS, NVENC tranh nhau 12GB.
Model Ollama sống dai sau bước dịch; torch cache sống dai trong cùng process.
Gọi free_gpu_vram() ở đầu mọi run_* GPU — kể cả khi chạy tay / subprocess.
"""
from __future__ import annotations

import gc
import subprocess
import time


def _ollama_loaded() -> list[str]:
    try:
        r = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001
        return []
    names: list[str] = []
    for ln in (r.stdout or "").splitlines()[1:]:
        tok = (ln.split() or [""])[0].strip()
        if tok and tok.upper() != "NAME":
            names.append(tok)
    return names


def _stop_ollama() -> list[str]:
    stopped: list[str] = []
    for name in _ollama_loaded():
        try:
            subprocess.run(["ollama", "stop", name], capture_output=True, timeout=60)
            stopped.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[vram] stop {name} lỗi: {e}", flush=True)
    return stopped


def _empty_torch() -> None:
    """Xả cache CUDA — KHÔNG synchronize (dễ treo vô hạn khi GPU kẹt)."""
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            try:
                torch.cuda.ipc_collect()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def free_gpu_vram(reason: str = "") -> None:
    """Stop Ollama + xả torch cache. An toàn gọi nhiều lần, kể cả không có GPU.

    Chạy trong thread + timeout — tránh kẹt cả hàng _gpu (render stuck 0%).
    """
    import os
    if os.getenv("SKIP_VRAM_FREE", "").strip().lower() in ("1", "true", "yes"):
        print(f"[vram] skip (SKIP_VRAM_FREE){(' · '+reason) if reason else ''}", flush=True)
        return
    tag = f" ({reason})" if reason else ""
    done = {"ok": False}

    def _run() -> None:
        try:
            stopped = _stop_ollama()
            if stopped:
                print(f"[vram] stop Ollama {', '.join(stopped)}{tag}", flush=True)
                time.sleep(0.8)
            _empty_torch()
            left = _ollama_loaded()
            if left:
                print(f"[vram] Ollama còn giữ: {', '.join(left)}{tag}", flush=True)
            elif not stopped:
                print(f"[vram] GPU sẵn sàng{tag}", flush=True)
            done["ok"] = True
        except Exception as e:  # noqa: BLE001
            print(f"[vram] lỗi{tag}: {e}", flush=True)

    import threading
    t = threading.Thread(target=_run, name="free-vram", daemon=True)
    t.start()
    t.join(timeout=25)
    if t.is_alive():
        print(f"[vram] TIMEOUT 25s — bỏ qua, tiếp tục{tag}", flush=True)
