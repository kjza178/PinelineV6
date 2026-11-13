"""config.py — Cấu hình/đường dẫn PORTABLE cho Pineline.

Thứ tự ưu tiên khi lấy 1 giá trị: biến môi trường  >  pineline.config.json  >  mặc định.
install.py sẽ ghi pineline.config.json với đường dẫn thật của máy (venv, HF cache...).
Nhờ vậy code KHÔNG hardcode D:/... nữa — chạy được trên bất kỳ máy nào.
"""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_CFG: dict = {}
_cfg_file = ROOT / "pineline.config.json"
_cfg_mtime: float = 0.0


def _load_cfg(force: bool = False) -> None:
    """Nạp lại pineline.config.json khi file đổi (tránh setdefault env khóa giá trị cũ)."""
    global _CFG, _cfg_mtime
    if not _cfg_file.exists():
        _CFG = {}
        _cfg_mtime = 0.0
        return
    try:
        mt = _cfg_file.stat().st_mtime
    except OSError:
        return
    if not force and _CFG and mt == _cfg_mtime:
        return
    try:
        _CFG = json.loads(_cfg_file.read_text(encoding="utf-8"))
        _cfg_mtime = mt
    except Exception:  # noqa: BLE001
        if not _CFG:
            _CFG = {}


_load_cfg(force=True)

IS_WIN = platform.system() == "Windows"

# FS không hỗ trợ symlink -> HF copy thay vì symlink (tránh WinError 1 khi tải model).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


# Key layout phụ đề / encoder: ưu tiên file JSON (tránh server setdefault khóa giá trị cũ).
_FILE_FIRST_PREFIXES = ("SUB_", "HARDSUB_")
# Model LLM của job: file config là nguồn duy nhất — env kế thừa từ shell khởi động server
# từng giữ model đã bỏ (deepseek) suốt phiên.
LLM_FILE_FIRST_KEYS = frozenset({
    "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "TRANSLATE_MODEL", "REVIEW_MODEL",
    "NAME_MODEL", "PRONOUN_MODEL", "CHATGPT_BASE_URL", "CHATGPT_MODEL", "CHATGPT_API_KEY",
    "AURORA_BASE_URL", "CLOUD_LLM_BASE_URL", "CLOUD_LLM_API_KEY", "PRONOUN_ANALYZE", "PRONOUN_ANALYZE_MODEL", "PRONOUN_ANALYZE_CHUNK",
    # Fan-out REVIEW Aurora: sửa file có hiệu lực ngay (không cần restart)
    "REVIEW_PARALLEL", "REVIEW_PARALLEL_DEFAULT", "REVIEW_PARALLEL_MAX",
    "REVIEW_BACKUP", "REVIEW_PASS1_MAX_UPLOAD_BYTES", "REVIEW_PASS1_CHUNK_CUES",
    "REVIEW_PASS1_ALLOW_CHAT_FALLBACK",
})
# Agent (server chat, sidecar Telegram, feeder) cùng một nguồn: file trước env.
AGENT_FILE_FIRST_KEYS = frozenset({"AGENT_BASE_URL", "AGENT_API_KEY", "AGENT_MODEL", "AGENT_FALLBACK_MODEL"})
_FILE_FIRST_KEYS = frozenset({"RENDER_ENCODER", "RENDER_DUB_VOL", "RENDER_BGM_VOL"}) | LLM_FILE_FIRST_KEYS | AGENT_FILE_FIRST_KEYS


# Key mà inject_env() chép từ file vào env (env chưa có) — get() vẫn đọc file trước cho các key này,
# nếu không sửa pineline.config.json sẽ bị giá trị chép lúc start đè tới khi restart.
FROM_FILE_ENV = "PINELINE_CFG_KEYS"


def _from_file_keys() -> set[str]:
    return {k for k in (os.environ.get(FROM_FILE_ENV) or "").split(",") if k}


def inject_env() -> None:
    """Chép pineline.config.json (rồi config.json) vào os.environ để tiến trình con kế thừa.
    Key LLM: file ghi đè env. Key khác: chỉ đặt khi env chưa có, và đánh dấu là "từ file"."""
    forced: set[str] = set()
    marked = _from_file_keys()
    for name in ("pineline.config.json", "config.json"):
        p = ROOT / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for k, v in data.items():
            k, sv = str(k), str(v)
            if k.startswith("_"):
                continue
            if k in LLM_FILE_FIRST_KEYS and sv.strip() and k not in forced:
                os.environ[k] = sv
                forced.add(k)
            elif k not in os.environ:
                os.environ[k] = sv
                marked.add(k)
    os.environ[FROM_FILE_ENV] = ",".join(sorted(marked))


def get(key: str, default: str | None = None) -> str | None:
    """env var  >  config.json  >  default.

    Riêng SUB_*/HARDSUB_*/RENDER_ENCODER/model LLM/AGENT_* và key do inject_env chép từ file:
    config.json  >  env  >  default.
    """
    _load_cfg()
    if (key in _FILE_FIRST_KEYS or any(key.startswith(p) for p in _FILE_FIRST_PREFIXES)
            or key in _from_file_keys()):
        v = _CFG.get(key)
        if v is not None and str(v).strip() != "":
            return str(v)
        v = os.getenv(key)
        if v:
            return v
        return default
    v = os.getenv(key)
    if v:
        return v
    v = _CFG.get(key)
    if v:
        return str(v)
    return default


def llm_settings() -> dict:
    """LLM cho job (dịch/review) — đọc lúc gọi để sửa pineline.config.json có hiệu lực không cần restart."""
    return {
        "base": (get("LLM_BASE_URL") or "http://192.168.31.247:20128/v1").rstrip("/"),
        "key": get("LLM_API_KEY") or "",
        "model": get("LLM_MODEL") or "ollama/gemma4:31b-cloud",
        "translate_model": get("TRANSLATE_MODEL") or None,
        "review_model": get("REVIEW_MODEL") or None,
        "name_model": get("NAME_MODEL") or None,
        "pronoun_model": get("PRONOUN_MODEL") or None,
        "chatgpt_base": (get("AURORA_BASE_URL") or get("CHATGPT_BASE_URL") or "").rstrip("/"),
        "chatgpt_model": get("CHATGPT_MODEL") or "gpt-5-6-mini",
    }


def pass_llm_model(kind: str = "pronoun") -> str | None:
    """Model cho pass ngắn (name/pronoun). Ưu tiên NAME_MODEL/PRONOUN_MODEL → CHATGPT_* → REVIEW → LLM."""
    kind = (kind or "").strip().lower()
    if kind == "name":
        m = get("NAME_MODEL")
    else:
        m = get("PRONOUN_MODEL")
    if m and str(m).strip():
        return str(m).strip()
    cg = get("CHATGPT_MODEL")
    if cg and str(cg).strip():
        raw = str(cg).strip()
        return raw if raw.startswith(("chatgpt/", "aurora/")) else f"chatgpt/{raw}"
    return get("REVIEW_MODEL") or get("LLM_MODEL")


def agent_llm() -> dict:
    """LLM cho agent chat / feeder: AGENT_* rồi LLM_* (cả hai file trước env)."""
    base = (get("AGENT_BASE_URL") or get("LLM_BASE_URL") or "http://127.0.0.1:8083/v1").rstrip("/")
    model = get("AGENT_MODEL") or get("LLM_MODEL") or "ollama/gemma4:31b-cloud"
    fb = (get("AGENT_FALLBACK_MODEL") or "").strip()
    return {
        "base": base,
        "key": get("AGENT_API_KEY") or get("LLM_API_KEY") or "sk-local",
        "model": model,
        "fallback": fb if fb and fb != model else "",
    }


def venv_python(venv_dir: str | Path) -> str:
    """Đường dẫn python trong venv, đúng theo OS."""
    d = Path(venv_dir)
    return str(d / ("Scripts/python.exe" if IS_WIN else "bin/python"))


# --- Đường dẫn mặc định: venv nằm CẠNH thư mục project (../vieneu_env) cho máy mới ---
_default_vieneu_env = str(ROOT.parent / "vieneu_env")
_default_omni_env = str(ROOT.parent / "OmniVoiceStudio-v2" / (".venv"))

VIENEU_PY = get("VIENEU_PY", venv_python(get("PINELINE_VIENEU_ENV", _default_vieneu_env)))
OMNI_PY = get("OMNI_PY", venv_python(get("PINELINE_OMNI_ENV", _default_omni_env)))
OMNI_CWD = get("OMNI_CWD", str(Path(OMNI_PY).parent.parent.parent))   # thư mục repo OmniVoice
HF_HOME = get("HF_HOME", str(ROOT / ".cache" / "hf"))
OMNI_MODEL = get("OMNI_MODEL", "splendor1811/omnivoice-vietnamese")
VIENEU_DEVICE = get("VIENEU_DEVICE", "cuda")
OMNI_DEVICE = get("OMNI_DEVICE", "cuda")
