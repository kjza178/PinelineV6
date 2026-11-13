#!/usr/bin/env python3
"""check_env.py — "Doctor" kiểm tra máy trước khi chạy Pineline (máy mới / máy khác cấu hình).

Chạy:   python scripts/check_env.py            (đầy đủ: gọi thử LLM/Aurora/Hongguo, timeout ngắn)
        python scripts/check_env.py --offline  (KHÔNG gọi mạng — chỉ kiểm file/thư mục/chương trình)
        python scripts/check_env.py --no-torch (bỏ kiểm torch+CUDA, nhanh hơn)
        python scripts/check_env.py --show     (in thêm giá trị hiệu lực của các key máy)

Kết quả: bảng  OK / THIẾU / TUỲ CHỌN / CẢNH BÁO  kèm cách sửa. Exit code 1 nếu có mục THIẾU.
Chỉ dùng stdlib. Không in API key.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == "scripts" else HERE
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

OK, MISS, OPT, WARN, SKIP = "OK", "THIẾU", "TUỲ CHỌN", "CẢNH BÁO", "BỎ QUA"
ROWS: list[tuple[str, str, str, str]] = []


def row(item: str, status: str, detail: str = "", fix: str = "") -> None:
    ROWS.append((item, status, detail, fix))


def _load_machine():
    try:
        try:
            from core import config as cfg  # V5/V6
        except ImportError:
            import config as cfg  # type: ignore  # V4
        try:
            cfg.inject_env()  # config.json + pineline.config.json → env của tiến trình doctor
        except Exception:  # noqa: BLE001
            pass
        try:
            from core import machine as m
        except ImportError:
            import machine as m  # type: ignore
        return cfg, m
    except Exception as e:  # noqa: BLE001
        row("Nạp core.config/machine", MISS, str(e)[:120], "Chạy từ thư mục gốc dự án; kiểm tra file config.py")
        return None, None


def _http_get(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer sk-local"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        # có trả lời = dịch vụ đang chạy (401/404 vẫn là "sống")
        return e.code < 500, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:80]


def check_python() -> None:
    v = sys.version_info
    s = f"{v.major}.{v.minor}.{v.micro} ({sys.executable})"
    if (v.major, v.minor) in ((3, 11), (3, 12)):
        row("Python", OK, s)
    elif (v.major, v.minor) >= (3, 10):
        row("Python", WARN, s, "Khuyên dùng Python 3.11 hoặc 3.12 (install.py tạo venv)")
    else:
        row("Python", MISS, s, "Cài Python 3.11/3.12 (tick 'Add to PATH')")


def check_programs() -> None:
    for exe in ("ffmpeg", "ffprobe"):
        p = shutil.which(exe)
        row(exe, OK if p else MISS, p or "không có trong PATH",
            "" if p else "Cài FFmpeg (bản full, có nvenc) và thêm thư mục bin vào PATH")
    smi = shutil.which("nvidia-smi")
    if not smi:
        row("GPU NVIDIA (nvidia-smi)", MISS, "không thấy nvidia-smi",
            "Cài driver NVIDIA mới. Không có GPU: đặt RENDER_ENCODER=x264, VIENEU_DEVICE/OMNI_DEVICE/MT_ZH_VI_DEVICE=cpu (rất chậm)")
        return
    try:
        out = subprocess.run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=15).stdout.strip()
        row("GPU NVIDIA (nvidia-smi)", OK if out else WARN, out.replace("\n", " | ")[:120] or "không đọc được")
    except Exception as e:  # noqa: BLE001
        row("GPU NVIDIA (nvidia-smi)", WARN, str(e)[:80])


def check_torch(skip: bool) -> None:
    if skip:
        row("torch + CUDA", SKIP, "--no-torch")
        return
    if importlib.util.find_spec("torch") is None:
        row("torch + CUDA", WARN, "python này không có torch",
            "Chạy doctor bằng python của venv app (pineline_env) hoặc: python install.py --gpu")
        return
    code = "import torch;print(torch.__version__, torch.cuda.is_available(), torch.version.cuda)"
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        txt = (out.stdout or out.stderr).strip().splitlines()[-1:] or [""]
        cuda_ok = " True " in f" {txt[0]} "
        row("torch + CUDA", OK if cuda_ok else WARN, txt[0][:100],
            "" if cuda_ok else "Cài torch bản CUDA (install.py --gpu) — torch CPU chạy ASR/TTS rất chậm")
    except Exception as e:  # noqa: BLE001
        row("torch + CUDA", WARN, str(e)[:80])


def check_config_files() -> None:
    for name, need in (("pineline.config.json", True), ("config.json", False)):
        p = ROOT / name
        if not p.exists():
            row(name, MISS if need else OPT, "chưa có",
                "Chạy install.py hoặc chép config/pineline.config.example.json → pineline.config.json rồi sửa"
                if need else "Không bắt buộc")
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
            row(name, OK, str(p))
        except Exception as e:  # noqa: BLE001
            row(name, MISS, f"JSON lỗi: {str(e)[:80]}", "Sửa cú pháp JSON (dấu phẩy, ngoặc kép, \\\\ trong đường dẫn)")


def _models_using(m, prefixes: tuple[str, ...]) -> list[str]:
    keys = ("LLM_MODEL", "TRANSLATE_MODEL", "REVIEW_MODEL", "REVIEW_PASS1_MODEL", "PLAN_MODEL",
            "AURORA_REVIEW_MODEL", "AGENT_MODEL", "NAME_MODEL", "PRONOUN_MODEL")
    out = []
    for k in keys:
        v = m.setting(k, "").lower()
        if v and (v.startswith(prefixes) or (prefixes == ("ollama/",) and v.endswith("-cloud"))):
            out.append(f"{k}={m.setting(k, '')}")
    return out


def check_llm(m, offline: bool) -> None:
    base = m.setting("LLM_BASE_URL").rstrip("/")
    if offline:
        row("LLM_BASE_URL", SKIP, f"{base} (offline)", "")
    else:
        ok, d = _http_get(base + "/models")
        row("LLM_BASE_URL", OK if ok else MISS, f"{base} → {d}",
            "" if ok else "Bật llama-server/LLM, hoặc sửa LLM_BASE_URL (+LLM_API_KEY, LLM_MODEL) trong pineline.config.json")
    lb = m.llama_base_url()
    if lb.rstrip("/") != base:
        if offline:
            row("llama-server (LLAMA_BASE_URL)", SKIP, f"{lb} (offline)")
        else:
            ok, d = _http_get(m.llama_root_url() + "/health")
            row("llama-server (LLAMA_BASE_URL)", OK if ok else OPT, f"{lb} → {d}",
                "" if ok else "Chỉ cần khi dùng model 'local'/'llama/...'/TRANSLATE_PRESET. Sửa LLAMA_BASE_URL nếu llama chạy cổng khác")
    why = m.preset_switch_unavailable_reason()
    if not why:
        row("Đổi preset llama", OK, " ".join(m.model_start_cmd("<preset>"))[:110])
    else:
        row("Đổi preset llama", OPT, why,
            "Không có presets: pipeline dùng model đang chạy. Muốn đổi tự động: cài llama.cpp + GGUF, đặt "
            "MODEL_PRESETS_DIR/MODEL_START_SCRIPT (hoặc MODEL_START_CMD); không muốn: AUTO_SWITCH_PRESETS=0")


def check_aurora(m, offline: bool) -> None:
    used = _models_using(m, ("chatgpt/", "aurora/"))
    bases = m.aurora_base_urls()
    need = MISS if used else OPT
    if offline:
        row("Aurora/ChatGPT proxy", OPT if not used else WARN,
            f"{bases[0]} (offline) — dùng bởi: {', '.join(used) or 'không key nào'}",
            "Cần Aurora đang chạy, HOẶC đổi các key trên sang model khác (vd 'local')" if used else "")
        return
    for b in bases:
        ok, d = _http_get(b + "/models")
        if ok:
            row("Aurora/ChatGPT proxy", OK, f"{b} → {d}")
            return
    row("Aurora/ChatGPT proxy", need, f"không tới được {', '.join(bases)}; dùng bởi: {', '.join(used) or '-'}",
        "Bật Aurora/sửa AURORA_BASE_URL, HOẶC đổi PLAN_MODEL/REVIEW_MODEL/REVIEW_PASS1_MODEL/AGENT_MODEL sang model khác"
        if used else "Không key nào dùng chatgpt/... — bỏ qua được")


def check_cloud(m, offline: bool) -> None:
    used = _models_using(m, ("ollama/",))
    if not used:
        return
    b = m.cloud_llm_base_url()
    has_key = bool(m.setting("CLOUD_LLM_API_KEY", "") or "20128" in m.setting("LLM_BASE_URL", ""))
    if offline:
        row("9router/cloud LLM", WARN if not has_key else SKIP, f"{b} (offline) — dùng bởi {', '.join(used)}",
            "" if has_key else "Thiếu CLOUD_LLM_API_KEY")
        return
    ok, d = _http_get(b + "/models")
    row("9router/cloud LLM", OK if ok else MISS, f"{b} → {d}; dùng bởi {', '.join(used)}",
        "" if ok else "Sửa CLOUD_LLM_BASE_URL/CLOUD_LLM_API_KEY hoặc đổi các model ollama/...-cloud")


def check_hongguo(m, offline: bool) -> None:
    b = m.hongguo_base_url()
    if offline:
        row("Hongguo Downloader", OPT, f"{b} (offline) — chỉ cần khi tải Hongguo")
        return
    ok, d = _http_get(b + "/", timeout=2.0)
    row("Hongguo Downloader", OK if ok else OPT, f"{b} → {d}",
        "" if ok else "Chỉ cần khi dùng tải Hongguo: mở Hongguo Downloader hoặc sửa HONGGUO_BASE_URL")


def _dir_row(item: str, p: Path | None, status_if_missing: str, fix: str, want_file: str = "") -> None:
    if p is None or str(p) in ("", "."):
        row(item, status_if_missing, "chưa đặt", fix)
        return
    try:
        is_dir, is_file = p.is_dir(), p.is_file()
    except OSError as e:  # junction/ổ mạng không đọc được
        row(item, WARN, f"{p} không đọc được ({e.strerror or e.__class__.__name__}) — junction/ổ chưa gắn?", fix)
        return
    if is_dir:
        try:
            has_want = (not want_file) or (p / want_file).exists()
        except OSError:
            has_want = False
        if not has_want:
            row(item, WARN, f"{p} (thiếu {want_file})", fix)
        else:
            row(item, OK, str(p))
    elif is_file:
        row(item, OK, str(p))
    else:
        row(item, status_if_missing, f"{p} không tồn tại", fix)


def check_dirs(m) -> None:
    copy = "Chép tay từ máy cũ (không có trong git)"
    _dir_row("voices/", ROOT / "voices", MISS, copy)
    _dir_row("voice_samples/", ROOT / "voice_samples", OPT, copy)
    _dir_row("models/ (cdx23, …)", ROOT / "models", OPT, copy + " — cần cho tách nhạc nền/âm thanh")
    _dir_row("cookies/", ROOT / "cookies", OPT, copy + " — cần khi tải Douyin/Bilibili/YouTube")
    w = ROOT / "work"
    wd = m.setting("WORK_DIR", "")
    if wd:
        _dir_row("WORK_DIR", Path(wd), MISS, "Tạo thư mục WORK_DIR hoặc sửa key")
    else:
        _dir_row("work/", w, MISS, "Tạo thư mục work (hoặc junction: mklink /J work E:\\pineline_work)")
    mt_on = m.flag("MT_ZH_VI", "0")
    mt_local = m.setting("MT_ZH_VI_LOCAL", "")
    _dir_row("MT model (MT_MODEL_DIR)", Path(mt_local) if mt_local else m.mt_model_dir(), WARN if mt_on else OPT,
             "Chép thư mục model MT (HachimiMT ct2) hoặc để tự tải từ HuggingFace lần đầu; sửa MT_MODEL_DIR/MT_ZH_VI_LOCAL")
    fr = m.setting("FIRERED_ROOT", "")
    _dir_row("FIRERED_ROOT", Path(fr) if fr else ROOT / "vendor" / "FireRedASR2S", OPT,
             "Chỉ cần khi dùng ASR FireRed: chép FireRedASR2S, sửa FIRERED_ROOT")
    fm = m.setting("FIRERED_MODELS", "")
    if fm:
        _dir_row("FIRERED_MODELS", Path(fm), OPT, "python scripts/download_firered_models.py hoặc chép tay")
    vp = m.setting("VIENEU_PY", "")
    if vp:
        _dir_row("VIENEU_PY", Path(vp), MISS, "python install.py --gpu (tạo vieneu_env) hoặc sửa VIENEU_PY")
    else:
        row("VIENEU_PY", OPT, "chưa đặt — dùng ../vieneu_env", "python install.py --gpu")
    hf = m.setting("HF_HOME", "")
    if hf:
        _dir_row("HF_HOME", Path(hf), OPT, "Thư mục cache HuggingFace — tự tạo khi tải model")
    _dir_row("LIBRARY_DIR", m.library_dir(), OPT, "Chỉ cần với thư viện Hongguo/nguồn — sửa LIBRARY_DIR")
    _dir_row("YT_UPLOADER_DIR", m.yt_uploader_dir(), OPT,
             "Chỉ cần khi upload YouTube: chép yt_uploader + client_secret.json, sửa YT_UPLOADER_DIR",
             want_file="client_secret.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kiểm tra môi trường máy cho Pineline")
    ap.add_argument("--offline", action="store_true", help="không gọi mạng/cổng dịch vụ")
    ap.add_argument("--no-torch", action="store_true", help="bỏ kiểm torch+CUDA")
    ap.add_argument("--show", action="store_true", help="in giá trị hiệu lực các key máy")
    a = ap.parse_args(argv)

    print(f"Pineline doctor — {ROOT}")
    check_python()
    check_programs()
    check_torch(a.no_torch)
    check_config_files()
    cfg, m = _load_machine()
    if m is not None:
        check_llm(m, a.offline)
        check_aurora(m, a.offline)
        check_cloud(m, a.offline)
        check_hongguo(m, a.offline)
        check_dirs(m)

    w1 = max(len(r[0]) for r in ROWS) + 2
    w2 = max(len(r[1]) for r in ROWS) + 2
    print()
    for item, st, detail, fix in ROWS:
        print(f"{item:<{w1}}{st:<{w2}}{detail}")
        if fix and st not in (OK, SKIP):
            print(f"{'':<{w1}}{'':<{w2}}→ {fix}")
    n_miss = sum(1 for r in ROWS if r[1] == MISS)
    n_warn = sum(1 for r in ROWS if r[1] == WARN)
    print(f"\nTổng: {len(ROWS)} mục — THIẾU {n_miss}, CẢNH BÁO {n_warn}.")
    if a.show and m is not None:
        print("\nGiá trị hiệu lực (key máy):")
        for k, v in m.summary().items():
            print(f"  {k} = {v}")
    if a.offline:
        print("(--offline: chưa gọi thử LLM/Aurora/Hongguo — chạy lại không có --offline trên máy thật.)")
    return 1 if n_miss else 0


if __name__ == "__main__":
    sys.exit(main())
