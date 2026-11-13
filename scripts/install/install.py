#!/usr/bin/env python3
"""install.py — Bộ cài Pineline TỰ DETECT máy + dựng môi trường trên BẤT KỲ máy nào.

Chạy:  python scripts/install/install.py            (tự detect GPU/CPU)
       python scripts/install/install.py --cpu      (ép CPU, không CUDA)
       python scripts/install/install.py --gpu --cuda cu121
       python scripts/install/install.py --skip-vieneu

Nó sẽ:
  1. Nhận diện OS (Windows/Linux/macOS) + có GPU NVIDIA hay không.
  2. Tạo 2 venv:  pineline_env (app chính)  +  vieneu_env (TTS lồng tiếng).
  3. Cài torch/torchaudio đúng bản (CUDA nếu có GPU, không thì CPU) + requirements.
  4. Ghi pineline.config.json (đường dẫn thật của máy) để app không hardcode.
  5. Kiểm ffmpeg / Ollama, báo nếu thiếu.
Model (VieNeu, Whisper, RapidOCR...) TỰ TẢI từ HuggingFace lần chạy đầu — cần mạng.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parents[1]  # PinelineV5 (scripts/install/ → repo root)
INSTALL = _HERE / "install"
IS_WIN = platform.system() == "Windows"
GREEN, YEL, RED, RST = ("\033[92m", "\033[93m", "\033[91m", "\033[0m") if not IS_WIN else ("", "", "", "")


def say(msg, c=""):
    print(f"{c}{msg}{RST}", flush=True)


def run(cmd, **kw):
    say(f"  $ {' '.join(str(c) for c in cmd)}", YEL)
    return subprocess.run([str(c) for c in cmd], **kw)


# TEMP + pip-cache đặt CÙNG Ổ với venv (tránh đầy ổ C:). Tắt cache để khỏi giữ ~2.5GB torch.
_TMP = ROOT / ".pip_tmp"


def _pip_env():
    _TMP.mkdir(parents=True, exist_ok=True)
    e = dict(os.environ)
    e.update({"TMPDIR": str(_TMP), "TEMP": str(_TMP), "TMP": str(_TMP),
              "PIP_NO_CACHE_DIR": "1", "PIP_CACHE_DIR": str(_TMP / "pipcache")})
    return e


def free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(str(path)).free / 1e9
    except Exception:  # noqa: BLE001
        return 999.0


def venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if IS_WIN else "bin/python")


def have(exe: str) -> bool:
    return shutil.which(exe) is not None


def detect_gpu() -> bool:
    if have("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi"], capture_output=True)
            return r.returncode == 0
        except Exception:  # noqa: BLE001
            return False
    return False


def create_venv(path: Path) -> Path:
    py = venv_python(path)
    if py.exists():
        say(f"  venv đã có: {path}", GREEN)
        return py
    say(f"  Tạo venv: {path}")
    run([sys.executable, "-m", "venv", str(path)], check=True)
    run([str(py), "-m", "pip", "install", "-U", "pip", "-q"])
    return py


def pip_install(py: Path, *args, index_url: str | None = None):
    cmd = [str(py), "-m", "pip", "install", "--no-cache-dir", *args]
    if index_url:
        cmd += ["--index-url", index_url]
    run(cmd, check=True, env=_pip_env())   # cache tắt + TEMP cùng ổ -> đỡ đầy C:


def torch_index(gpu: bool, cuda: str) -> str:
    return f"https://download.pytorch.org/whl/{cuda if gpu else 'cpu'}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpu", action="store_true", help="ép dùng GPU (CUDA)")
    ap.add_argument("--cpu", action="store_true", help="ép dùng CPU (không CUDA)")
    ap.add_argument("--cuda", default="cu121", help="build CUDA cho torch (mặc định cu121)")
    ap.add_argument("--skip-vieneu", action="store_true", help="bỏ qua env VieNeu")
    ap.add_argument("--skip-ollama-pull", action="store_true", help="không tự pull model Ollama")
    ap.add_argument("--ollama-models", default="gemma4,gemma3:12b",
                    help="model Ollama cần pull (ngăn bằng phẩy)")
    ap.add_argument("--envs-dir", default=str(ROOT.parent),
                    help="nơi đặt venv (mặc định: cạnh thư mục Pineline)")
    args = ap.parse_args()

    gpu = True if args.gpu else (False if args.cpu else detect_gpu())
    envs = Path(args.envs_dir)
    say("=" * 64)
    say(f" PINELINE INSTALLER", GREEN)
    say(f"  OS        : {platform.system()} {platform.machine()}")
    say(f"  Python    : {sys.version.split()[0]} ({sys.executable})")
    say(f"  GPU NVIDIA: {'CÓ -> cài CUDA (' + args.cuda + ')' if gpu else 'KHÔNG -> cài CPU'}")
    say(f"  Đặt venv  : {envs}")
    say("=" * 64)
    if sys.version_info < (3, 10):
        say("[LỖI] Cần Python >= 3.10.", RED); sys.exit(1)

    # Kiểm dung lượng: torch CUDA ~2.5GB/env + requirements -> cần dư ~10GB (GPU) / 4GB (CPU).
    need = 10 if gpu else 4
    fr = free_gb(envs)
    say(f"  Trống ở {envs.drive or envs}: {fr:.1f} GB (cần ~{need} GB)")
    if fr < need:
        say(f"[CẢNH BÁO] Ổ gần đầy! Dễ lỗi 'No space left'. Hãy dọn ổ, đổi --envs-dir sang ổ"
            f" khác, hoặc dùng --cpu (torch CPU nhẹ hơn nhiều).", RED)

    tindex = torch_index(gpu, args.cuda)
    config = {"_note": "install.py tự ghi. Sửa tay nếu dời venv/cache."}

    def _install_torch(py, extra=()):
        try:
            pip_install(py, "torch", "torchaudio", *extra, index_url=tindex)
        except subprocess.CalledProcessError:
            say("[LỖI] Cài torch thất bại. Nguyên nhân hay gặp:", RED)
            say(f"  - HẾT Ổ (No space): dọn ổ hoặc chạy lại với --envs-dir sang ổ nhiều chỗ,\n"
                f"    hoặc --cpu (torch CPU chỉ ~200MB thay vì ~2.5GB).", YEL)
            say(f"  - Sai build CUDA: thử --cuda cu124 / cu128 khớp máy (xem nvidia-smi).", YEL)
            sys.exit(1)

    # 1) ENV CHÍNH (app) --------------------------------------------------
    say("\n[1/3] ENV CHÍNH (app: server/asr/ocr/demucs/render)...", GREEN)
    main_env = envs / "pineline_env"
    mpy = create_venv(main_env)
    _install_torch(mpy)
    pip_install(mpy, "-r", str(INSTALL / "requirements-main.txt"))

    # 2) ENV VIENEU (TTS) -------------------------------------------------
    if not args.skip_vieneu:
        say("\n[2/3] ENV VIENEU (TTS lồng tiếng)...", GREEN)
        vieneu_env = envs / "vieneu_env"
        vpy = create_venv(vieneu_env)
        try:
            # torch cho VieNeu: dùng bản MỚI NHẤT theo index CUDA đã chọn (KHÔNG ghim 2.5.1
            # — GPU mới như RTX 50-series/Blackwell cần torch >=2.7 + cu128). Cài xong test cuda.
            pip_install(vpy, "torch", "torchaudio", index_url=tindex)
            pip_install(vpy, "-r", str(INSTALL / "requirements-vieneu.txt"))
        except subprocess.CalledProcessError:
            say("[LỖI] Cài env VieNeu thất bại (hết ổ / sai CUDA). Xem gợi ý ở trên.", RED)
            sys.exit(1)
        config["VIENEU_PY"] = str(venv_python(vieneu_env))
        config["VIENEU_DEVICE"] = "cuda" if gpu else "cpu"

    # 3) CONFIG + KIỂM NGOÀI ---------------------------------------------
    say("\n[3/3] Ghi config + kiểm công cụ ngoài...", GREEN)
    config["HF_HOME"] = str(ROOT / ".cache" / "hf")
    # OmniVoice là TÙY CHỌN (repo riêng OmniVoiceStudio) — chỉ ghi gợi ý.
    config.setdefault("OMNI_PY", "")
    config.setdefault("OMNI_CWD", "")
    config["OMNI_MODEL"] = "splendor1811/omnivoice-vietnamese"
    config["OMNI_DEVICE"] = "cuda" if gpu else "cpu"
    (ROOT / "pineline.config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    say(f"  -> pineline.config.json", GREEN)

    # Launcher trỏ ĐÚNG python của pineline_env (tránh chạy nhầm python hệ thống).
    mpy_abs = venv_python(main_env)
    (ROOT / "start.bat").write_text(
        f'@echo off\r\n"{mpy_abs}" "%~dp0app.py" %*\r\npause\r\n', encoding="utf-8")
    sh = ROOT / "start.sh"
    sh.write_text(f'#!/bin/sh\nexec "{mpy_abs}" "$(dirname "$0")/app.py" "$@"\n', encoding="utf-8")
    try:
        os.chmod(sh, 0o755)
    except Exception:  # noqa: BLE001
        pass
    say("  -> start.bat / start.sh (mở app đúng venv)", GREEN)

    say("\n  Công cụ ngoài:")
    if not have("ffmpeg"):
        say("    ffmpeg THIẾU -> thử tự cài...", YEL)
        try:
            if IS_WIN and have("winget"):
                run(["winget", "install", "-e", "--id", "Gyan.FFmpeg",
                     "--accept-source-agreements", "--accept-package-agreements"])
            elif platform.system() == "Linux" and have("apt-get"):
                run(["sudo", "apt-get", "install", "-y", "ffmpeg"])
            elif platform.system() == "Darwin" and have("brew"):
                run(["brew", "install", "ffmpeg"])
        except Exception as e:  # noqa: BLE001
            say(f"    tự cài lỗi: {e}", YEL)
    ok_ff = have("ffmpeg")
    say(f"    ffmpeg : {'OK' if ok_ff else 'VẪN THIẾU -> cài tay + MỞ LẠI terminal'}",
        GREEN if ok_ff else RED)
    if not ok_ff:
        say("      Windows: winget install Gyan.FFmpeg  (rồi mở lại cmd). Hoặc tải gyan.dev + thêm PATH.", YEL)
    say(f"    ollama : {'OK' if have('ollama') else 'THIẾU (cần cho DỊCH)'}",
        GREEN if have("ollama") else YEL)

    # Auto PULL model Ollama cho bước DỊCH (gemma4 nháp + gemma3:12b rà văn phong).
    if have("ollama") and not args.skip_ollama_pull:
        for mdl in [x.strip() for x in args.ollama_models.split(",") if x.strip()]:
            say(f"\n  Pull model Ollama '{mdl}' (có thể vài GB, lần đầu)...", GREEN)
            try:
                run(["ollama", "pull", mdl])
            except Exception as e:  # noqa: BLE001
                say(f"    [!] pull '{mdl}' lỗi: {e}. Chạy tay sau: ollama pull {mdl}", YEL)
    elif not have("ollama"):
        say("  (bỏ qua pull model — chưa có Ollama. Cài xong chạy: ollama pull gemma4 gemma3:12b)", YEL)

    say("\n" + "=" * 64)
    say(" XONG! Bước tiếp:", GREEN)
    if not have("ffmpeg"):
        say("  - Cài ffmpeg + thêm vào PATH (https://ffmpeg.org).", YEL)
    if not have("ollama"):
        say("  - Cài Ollama cho bước dịch (https://ollama.com), rồi: ollama pull gemma4", YEL)
    say("  - (Tùy chọn) OmniVoice: clone repo OmniVoiceStudio, tạo venv, rồi điền")
    say("    OMNI_PY / OMNI_CWD trong pineline.config.json. Không có cũng chạy được (dùng VieNeu).")
    say(f"  - CHẠY APP:  nháy đúp start.bat   (Linux/macOS: ./start.sh)", GREEN)
    say(f"    (hoặc thủ công: \"{venv_python(main_env)}\" app.py)")
    say("=" * 64)


if __name__ == "__main__":
    main()
