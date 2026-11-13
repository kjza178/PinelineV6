"""machine.py — Cấu hình PHỤ THUỘC MÁY (cổng dịch vụ ngoài, thư mục model/thư viện, lệnh đổi preset).

Mục đích (2026-11-11 machine-config): chạy cùng codebase trên máy khác cấu hình mà KHÔNG sửa code.
Mọi giá trị đọc qua `config.get()` (env > pineline.config.json > mặc định; một số key "file-first"
theo luật của config.py) ĐỌC LÚC GỌI — sửa file có hiệu lực không cần restart (trừ chỗ gán lúc import).

MẶC ĐỊNH = giá trị cứng cũ của máy gốc → máy hiện tại không đổi hành vi.

Dịch vụ TUỲ CHỌN (thiếu thì tính năng đó báo lỗi rõ / bỏ qua, pipeline không sập):
  - llama-server + presets (đổi model GGUF bằng script)      → AUTO_SWITCH_PRESETS, MODEL_PRESETS_DIR...
  - Aurora / ChatGPT proxy (model `chatgpt/...`)             → AURORA_BASE_URL / CHATGPT_BASE_URL
  - Hongguo Downloader (tải phim Hongguo)                    → HONGGUO_BASE_URL
  - 9router / cloud LLM (model `ollama/...-cloud`)           → CLOUD_LLM_BASE_URL

Chỉ dùng stdlib (scripts/check_env.py import được khi chưa cài requirements).
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path
from urllib.parse import urlsplit

try:  # V5/V6: package core/ ; V4: module phẳng config.py
    from core import config as _config  # type: ignore
except ImportError:  # pragma: no cover - V4
    import config as _config  # type: ignore


# Key máy → (mặc định, mô tả ngắn). Dùng cho config.example.json + doctor.
MACHINE_KEYS: dict[str, tuple[str, str]] = {
    "LLM_BASE_URL": ("http://192.168.31.247:20128/v1", "LLM chính (OpenAI-compatible) cho dịch/review: llama-server, 9router, Ollama /v1, OpenAI..."),
    "LLAMA_BASE_URL": ("", "llama-server /v1 (GGUF local). Trống = LLM_BASE_URL nếu trỏ localhost, không thì http://127.0.0.1:8083/v1"),
    "AUTO_SWITCH_PRESETS": ("1", "1 = tự đổi preset llama (TRANSLATE_PRESET/REVIEW_PRESET) bằng script; 0 = không bao giờ đổi, dùng model đang chạy"),
    "MODEL_PRESETS_DIR": (r"E:\models\presets", "Thư mục presets llama.cpp (active.json, models.json, script khởi động). Không tồn tại = bỏ qua đổi preset"),
    "MODEL_START_SCRIPT": ("start-model.ps1", "Script đổi preset (tương đối theo MODEL_PRESETS_DIR hoặc đường dẫn tuyệt đối)"),
    "MODEL_START_CMD": ("", "Lệnh đổi preset tuỳ ý; placeholder {script} {preset} {presets_dir} {host} {port} {base_url}. Trống = powershell -ExecutionPolicy Bypass -File {script} {preset}"),
    "AURORA_BASE_URL": ("", "Aurora/ChatGPT proxy cho model chatgpt/... (ưu tiên trước CHATGPT_BASE_URL)"),
    "CHATGPT_BASE_URL": ("http://127.0.0.1:3377/v1", "Proxy ChatGPT local (plan/review/agent dùng model chatgpt/...)"),
    "AURORA_FALLBACK_URLS": ("http://192.168.31.247:5005/v1,http://192.168.31.244:5005/v1", "Aurora dự phòng (phân cách dấu phẩy). 'off' = không dùng"),
    "AGENT_BASE_URL": ("", "LLM cho agent chat/feeder; trống = LLM_BASE_URL"),
    "CLOUD_LLM_BASE_URL": ("http://192.168.31.247:20128/v1", "9router cho model ollama/...-cloud (cần CLOUD_LLM_API_KEY)"),
    "HONGGUO_BASE_URL": ("http://127.0.0.1:8000", "Hongguo Downloader HTTP API — chỉ cần khi dùng tải Hongguo"),
    "MT_MODEL_DIR": (r"E:\models\mt", "Thư mục model MT zh→vi (HachimiMT ct2); MT_ZH_VI_LOCAL = trỏ thẳng 1 model"),
    "LIBRARY_DIR": (r"D:\Lib", "Thư viện phim tải về (Hongguo output / nguồn job)"),
    "YT_UPLOADER_DIR": (r"Z:\yt_uploader", "Thư mục tool yt_uploader (accounts, client_secret.json, queue_state.json)"),
    "LIB_SAMPLE_DIR": ("", "Thư mục mẫu cắt thử bản quyền (lib_check); trống = mặc định cũ ghi trong lib_check.py"),
    "VIENEU_PY": ("", "python.exe của venv VieNeu-TTS (install.py tự ghi)"),
    "FIRERED_ROOT": ("", "Mã nguồn FireRedASR2S; trống = vendor/FireRedASR2S"),
    "FIRERED_MODELS": ("", "Model FireRed; trống = FIRERED_ROOT/pretrained_models"),
    "HF_HOME": ("", "Cache HuggingFace"),
}

_OFF = ("0", "false", "no", "off", "none", "-")
_LOOPBACK = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def setting(key: str, default: str | None = None) -> str:
    """Giá trị key máy: config.get(key) → default truyền vào → mặc định MACHINE_KEYS."""
    if default is None:
        default = MACHINE_KEYS.get(key, ("", ""))[0]
    try:
        v = _config.get(key)
    except Exception:  # noqa: BLE001
        v = os.getenv(key)
    v = "" if v is None else str(v).strip()
    return v if v else (default or "")


def flag(key: str, default: str = "1") -> bool:
    return setting(key, default).strip().lower() not in _OFF


def _strip_v1(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    return u[:-3] if u.endswith("/v1") else u


def is_loopback_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOOPBACK or host.startswith("127.")


# ── llama-server ──────────────────────────────────────────────────────────────
DEFAULT_LLAMA_BASE_URL = "http://127.0.0.1:8083/v1"


def llama_base_url() -> str:
    """URL /v1 của llama-server: LLAMA_BASE_URL > LLM_BASE_URL (chỉ khi localhost) > :8083 cũ."""
    v = setting("LLAMA_BASE_URL", "")
    if not v:
        llm = setting("LLM_BASE_URL", "")
        v = llm if (llm and is_loopback_url(llm)) else DEFAULT_LLAMA_BASE_URL
    v = v.rstrip("/")
    return v if v.endswith("/v1") else v + "/v1"


def llama_root_url() -> str:
    """URL gốc (không /v1) — /health, /v1/models."""
    return _strip_v1(llama_base_url())


def llama_host_port() -> tuple[str, int]:
    p = urlsplit(llama_root_url())
    port = p.port or (443 if p.scheme == "https" else 80)
    return (p.hostname or "127.0.0.1"), int(port)


# ── presets llama.cpp ────────────────────────────────────────────────────────
def presets_dir() -> Path:
    return Path(os.path.expandvars(os.path.expanduser(setting("MODEL_PRESETS_DIR"))))


def presets_active_json() -> Path:
    return presets_dir() / "active.json"


def presets_models_json() -> Path:
    return presets_dir() / "models.json"


def model_start_script() -> Path:
    s = Path(os.path.expandvars(os.path.expanduser(setting("MODEL_START_SCRIPT"))))
    return s if s.is_absolute() else presets_dir() / s


def _split(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError:
        return cmd.split()


def model_start_cmd(preset_id: str) -> list[str]:
    """Lệnh đổi preset (list argv). MODEL_START_CMD là template; trống = PowerShell như cũ."""
    script = model_start_script()
    tpl = setting("MODEL_START_CMD", "")
    if not tpl:
        return ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script), preset_id]
    host, port = llama_host_port()
    vals = {
        "script": str(script), "preset": preset_id, "presets_dir": str(presets_dir()),
        "host": host, "port": str(port), "base_url": llama_base_url(),
    }
    out = []
    for tok in _split(tpl):
        tok = tok.strip('"') if os.name == "nt" else tok
        try:
            out.append(tok.format(**vals))
        except (KeyError, IndexError, ValueError):
            out.append(tok)
    return out


def preset_switch_unavailable_reason() -> str:
    """'' = đổi preset được; ngược lại lý do bỏ qua (KHÔNG phải lỗi)."""
    if not flag("AUTO_SWITCH_PRESETS", "1"):
        return "AUTO_SWITCH_PRESETS=0"
    if setting("MODEL_START_CMD", ""):
        return ""  # lệnh tuỳ ý — không bắt buộc có thư mục presets
    d = presets_dir()
    if not d.is_dir():
        return f"không có thư mục presets {d} (MODEL_PRESETS_DIR)"
    s = model_start_script()
    if not s.is_file():
        return f"không có script đổi preset {s} (MODEL_START_SCRIPT)"
    return ""


# ── Aurora / ChatGPT proxy ───────────────────────────────────────────────────
def aurora_base_urls() -> list[str]:
    """Thứ tự thử: AURORA_BASE_URL, CHATGPT_BASE_URL, :3377 local, AURORA_FALLBACK_URLS."""
    out: list[str] = []
    for k in ("AURORA_BASE_URL", "CHATGPT_BASE_URL"):
        v = setting(k, "").rstrip("/")
        if v and v not in out:
            out.append(v)
    dflt = MACHINE_KEYS["CHATGPT_BASE_URL"][0]
    if dflt not in out:
        out.append(dflt)
    fb = setting("AURORA_FALLBACK_URLS")
    if fb.strip().lower() not in _OFF:
        for u in fb.split(","):
            u = u.strip().rstrip("/")
            if u and u not in out:
                out.append(u)
    return out


def aurora_base_url() -> str:
    return aurora_base_urls()[0]


AURORA_MODEL_KEYS = ("PLAN_MODEL", "REVIEW_MODEL", "REVIEW_PASS1_MODEL", "AURORA_REVIEW_MODEL",
                     "AGENT_MODEL", "CHATGPT_MODEL", "NAME_MODEL", "PRONOUN_MODEL")


def aurora_missing_hint() -> str:
    """Thông điệp khi gọi model chatgpt/... mà không tới được Aurora."""
    used = [k for k in AURORA_MODEL_KEYS
            if setting(k, "").lower().startswith(("chatgpt/", "aurora/"))]
    keys = ", ".join(used) or "PLAN_MODEL/REVIEW_MODEL/REVIEW_PASS1_MODEL"
    return (f"Không tới được Aurora/ChatGPT ({', '.join(aurora_base_urls())}). "
            f"Máy này không có Aurora → đặt AURORA_BASE_URL/CHATGPT_BASE_URL đúng, HOẶC đổi {keys} "
            f"sang model khác (vd 'local' = llama-server, hoặc model của LLM_BASE_URL) trong pineline.config.json.")


# ── Dịch vụ / thư mục khác ───────────────────────────────────────────────────
def cloud_llm_base_url() -> str:
    return setting("CLOUD_LLM_BASE_URL").rstrip("/")


def hongguo_base_url() -> str:
    return setting("HONGGUO_BASE_URL").rstrip("/")


def mt_model_dir() -> Path:
    return Path(setting("MT_MODEL_DIR"))


def library_dir() -> Path:
    return Path(setting("LIBRARY_DIR"))


def in_library(path: str) -> bool:
    """Đường dẫn nằm trong LIBRARY_DIR? (so chuỗi như code cũ 'D:\\Lib' in p, chấp nhận / và \\)."""
    lp = str(library_dir()).rstrip("\\/")
    p = str(path or "")
    return bool(lp) and (lp in p or lp.replace("\\", "/") in p)


def yt_uploader_dir() -> Path:
    return Path(setting("YT_UPLOADER_DIR"))


def summary() -> dict:
    """Giá trị hiệu lực (doctor / debug). Không trả key bí mật."""
    return {
        "LLM_BASE_URL": setting("LLM_BASE_URL"),
        "LLAMA_BASE_URL(effective)": llama_base_url(),
        "AUTO_SWITCH_PRESETS": setting("AUTO_SWITCH_PRESETS"),
        "MODEL_PRESETS_DIR": str(presets_dir()),
        "MODEL_START_SCRIPT(effective)": str(model_start_script()),
        "MODEL_START_CMD": setting("MODEL_START_CMD", "") or "(PowerShell mặc định)",
        "preset_switch": preset_switch_unavailable_reason() or "OK",
        "AURORA(bases)": ", ".join(aurora_base_urls()),
        "CLOUD_LLM_BASE_URL": cloud_llm_base_url(),
        "HONGGUO_BASE_URL": hongguo_base_url(),
        "MT_MODEL_DIR": str(mt_model_dir()),
        "LIBRARY_DIR": str(library_dir()),
        "YT_UPLOADER_DIR": str(yt_uploader_dir()),
    }
