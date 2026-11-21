"""machine.py — Cấu hình PHỤ THUỘC MÁY (cổng dịch vụ ngoài, thư mục model/thư viện, lệnh đổi preset).

Mục đích (2026-11-11 machine-config): chạy cùng codebase trên máy khác cấu hình mà KHÔNG sửa code.
Mọi giá trị đọc qua `config.get()` (env > pineline.config.json > mặc định; một số key "file-first"
theo luật của config.py) ĐỌC LÚC GỌI — sửa file có hiệu lực không cần restart (trừ chỗ gán lúc import).

MẶC ĐỊNH = giá trị cứng cũ của máy gốc → máy hiện tại không đổi hành vi.

Dịch vụ TUỲ CHỌN (thiếu thì tính năng đó báo lỗi rõ / bỏ qua, pipeline không sập):
  - llama-server + presets (đổi model GGUF bằng script)      → AUTO_SWITCH_PRESETS, MODEL_PRESETS_DIR...
  - Aurora / ChatGPT proxy (model `chatgpt/...`)             → AURORA_BASE_URL / CHATGPT_BASE_URL
  - Hongguo 2 (tải phim — engine RPC, agents/hongguo_rpc)    → HG2_* / tự dò thư mục cài
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
    "LLAMA_BASE_URL": ("", "llama-server /v1 (AI trên máy, model «local»). Trống = http://127.0.0.1:8083/v1"),
    "AUTO_SWITCH_PRESETS": ("1", "1 = tự đổi preset llama (TRANSLATE_PRESET/REVIEW_PRESET) bằng script; 0 = không bao giờ đổi, dùng model đang chạy"),
    "MODEL_PRESETS_DIR": (r"E:\models\presets", "Thư mục presets llama.cpp (active.json, models.json, script khởi động). Không tồn tại = bỏ qua đổi preset"),
    "MODEL_START_SCRIPT": ("start-model.ps1", "Script đổi preset (tương đối theo MODEL_PRESETS_DIR hoặc đường dẫn tuyệt đối)"),
    "MODEL_START_CMD": ("", "Lệnh đổi preset tuỳ ý; placeholder {script} {preset} {presets_dir} {host} {port} {base_url}. Trống = powershell -ExecutionPolicy Bypass -File {script} {preset}"),
    "AURORA_BASE_URL": ("", "Aurora/ChatGPT proxy cho model chatgpt/... (ưu tiên trước CHATGPT_BASE_URL)"),
    "CHATGPT_BASE_URL": ("http://127.0.0.1:3377/v1", "Proxy ChatGPT local (plan/review/agent dùng model chatgpt/...)"),
    "AURORA_FALLBACK_URLS": ("", "Aurora dự phòng (phân cách dấu phẩy). Trống/'off' = không dùng. Địa chỉ LAN chỉ dùng khi ALLOW_LAN_LLM=1"),
    "ALLOW_LAN_LLM": ("0", "1 = cho phép gọi LLM/Aurora ở địa chỉ LAN (192.168.x, 10.x, 172.16–31.x). Mặc định 0: bỏ qua, không làm dự phòng"),
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
    """URL /v1 của llama-server (AI trên máy): LLAMA_BASE_URL > :8083.
    2026-11-21: V6 chỉ còn 3 loại AI — nhà cung cấp API (deepseek/…, alibaba/…), Aurora (chatgpt/…), AI trên máy
    (local). LLM_BASE_URL / AGENT_BASE_URL / CLOUD_LLM_* (9router LAN cũ) KHÔNG còn được đọc."""
    v = no_lan("LLAMA_BASE_URL", setting("LLAMA_BASE_URL", ""))      # LAN → bỏ (ALLOW_LAN_LLM=1 mở)
    if not v:
        v = DEFAULT_LLAMA_BASE_URL
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


# ── Địa chỉ LAN (V6 2026-11-17: không gọi máy khác trong mạng nội bộ làm dự phòng) ──────────────
_LAN_WARNED: set[tuple[str, str]] = set()


def is_lan_url(url: str) -> bool:
    """True nếu URL trỏ IP mạng nội bộ (192.168.x, 10.x, 172.16–31.x, 169.254.x) — KHÔNG tính loopback."""
    import ipaddress
    try:
        host = urlsplit((url or "").strip()).hostname or ""
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (ip.is_private or ip.is_link_local) and not ip.is_loopback


def lan_llm_allowed() -> bool:
    return setting("ALLOW_LAN_LLM", "0").strip().lower() in ("1", "true", "yes", "on")


def _warn_lan_once(key: str, url: str) -> None:
    if (key, url) in _LAN_WARNED:
        return
    _LAN_WARNED.add((key, url))
    print(f"[machine] bỏ qua {key}={url} (địa chỉ LAN — V6 không gọi máy LAN làm dự phòng; "
          f"đặt ALLOW_LAN_LLM=1 nếu thật sự muốn)", flush=True)


def no_lan(key: str, url: str, fallback: str = "") -> str:
    """Trả url nếu không phải LAN (hoặc ALLOW_LAN_LLM=1); không thì `fallback` (+ cảnh báo 1 lần)."""
    if url and is_lan_url(url) and not lan_llm_allowed():
        _warn_lan_once(key, url)
        return fallback
    return url


# ── Aurora / ChatGPT proxy ───────────────────────────────────────────────────
def aurora_base_urls() -> list[str]:
    """Thứ tự thử: AURORA_BASE_URL, CHATGPT_BASE_URL, :3377 local, AURORA_FALLBACK_URLS."""
    out: list[str] = []
    for k in ("AURORA_BASE_URL", "CHATGPT_BASE_URL"):
        v = no_lan(k, setting(k, "").rstrip("/"))       # 2026-11-19: cả URL chính LAN cũng bỏ (ALLOW_LAN_LLM=1 mở)
        if v and v not in out:
            out.append(v)
    dflt = MACHINE_KEYS["CHATGPT_BASE_URL"][0]
    if dflt not in out:
        out.append(dflt)
    fb = setting("AURORA_FALLBACK_URLS")
    if fb.strip().lower() not in _OFF:
        for u in fb.split(","):
            u = u.strip().rstrip("/")
            if not u or u in out:
                continue
            if is_lan_url(u) and not lan_llm_allowed():
                _warn_lan_once("AURORA_FALLBACK_URLS", u)
                continue
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
            f"sang model khác (deepseek/…, alibaba/… hoặc 'local' = AI trên máy) trong Cài đặt → AI.")


# ── Dịch vụ / thư mục khác ───────────────────────────────────────────────────
def cloud_llm_base_url() -> str:
    """2026-11-21: router cloud (9router) đã bỏ hoàn toàn — luôn trống (giữ hàm cho code cũ gọi)."""
    return ""


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
        "LLAMA_BASE_URL(effective)": llama_base_url(),
        "AUTO_SWITCH_PRESETS": setting("AUTO_SWITCH_PRESETS"),
        "MODEL_PRESETS_DIR": str(presets_dir()),
        "MODEL_START_SCRIPT(effective)": str(model_start_script()),
        "MODEL_START_CMD": setting("MODEL_START_CMD", "") or "(PowerShell mặc định)",
        "preset_switch": preset_switch_unavailable_reason() or "OK",
        "AURORA(bases)": ", ".join(aurora_base_urls()),
        "MT_MODEL_DIR": str(mt_model_dir()),
        "LIBRARY_DIR": str(library_dir()),
        "YT_UPLOADER_DIR": str(yt_uploader_dir()),
    }


# ── Thư mục máy sửa được từ giao diện (Cài đặt → Thư mục, /api/v6/paths) ──
# 2026-11-15: thay tab «Thư Viện & Ổ Đĩa» trong hộp «Cài Đặt Hệ Thống V4» của trang Phim cũ (hộp đó còn ghi đè khoá AI).
PATH_KEYS = ("LIBRARY_DIR", "YT_UPLOADER_DIR", "LIB_SAMPLE_DIR")


def paths_snapshot() -> dict:
    out = {}
    for k in PATH_KEYS:
        default, desc = MACHINE_KEYS[k]
        v = setting(k, default)
        out[k] = {"value": v, "default": default, "desc": desc, "exists": bool(v) and Path(v).is_dir()}
    return out


def save_paths(patch: dict) -> list[str]:
    """{khoá: đường dẫn | ""}. "" = về mặc định. Khoá ngoài PATH_KEYS → ValueError, không ghi gì."""
    if not isinstance(patch, dict):
        raise ValueError("paths phải là object")
    bad = [k for k in patch if k not in PATH_KEYS]
    if bad:
        raise ValueError(f"khoá thư mục lạ: {', '.join(bad)}")
    clean = {k: (str(v).strip().strip('"') if v is not None else "") for k, v in patch.items()}
    if clean:
        _config.save_config(clean)
    return sorted(clean)


# ── FireRedASR2S (ASR «FireRed») — một nguồn đường dẫn cho engines/asr/firered_asr + Cài đặt + check_env ──
DEFAULT_FIRERED_MODELS = r"D:\AI_CACHE\firered"      # nơi scripts/download_firered_models.py tải về khi chưa đặt
FIRERED_MODEL_DIRS = ("FireRedASR2-AED", "FireRedVAD", "FireRedLID", "FireRedPunc")


_PATH_WARNED: set[str] = set()


def _safe_is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError as e:                 # ổ rớt / ổ mạng chưa gắn → coi như không có (ghi lý do)
        _warn_once(f"isdir:{p}", f"không đọc được {p}: {e}")
        return False


def _warn_once(key: str, msg: str) -> None:
    if key in _PATH_WARNED:
        return
    _PATH_WARNED.add(key)
    print(f"[machine] {msg}", flush=True)


def firered_paths() -> tuple[Path, Path]:
    """(mã nguồn FireRedASR2S, thư mục model). Env > pineline.config.json > mặc định.
    Model chưa đặt: <root>/pretrained_models nếu có, không thì D:\\AI_CACHE\\firered (chỗ script cài tải về —
    fix 2026-11-16: script cài tải về đó nhưng không ghi FIRERED_MODELS → ASR báo thiếu model)."""
    vendor = Path(__file__).resolve().parents[1] / "vendor" / "FireRedASR2S"
    root = Path(os.getenv("FIRERED_ROOT") or setting("FIRERED_ROOT", "") or str(vendor))
    # 2026-11-19 portable: config chép từ máy khác còn trỏ cây cũ (không tồn tại) → bản vendor trong cây
    if root != vendor and not _safe_is_dir(root) and _safe_is_dir(vendor):
        _warn_once("FIRERED_ROOT", f"{root} không tồn tại → dùng {vendor}")
        root = vendor
    m = os.getenv("FIRERED_MODELS") or setting("FIRERED_MODELS", "")
    if m and (_safe_is_dir(Path(m)) or not _safe_is_dir(root / "pretrained_models")):
        return root, Path(m)
    if m:
        _warn_once("FIRERED_MODELS", f"{m} không tồn tại → dùng {root / 'pretrained_models'}")
        return root, root / "pretrained_models"
    pre = root / "pretrained_models"
    if (pre / FIRERED_MODEL_DIRS[0]).exists() or not Path(DEFAULT_FIRERED_MODELS).is_dir():
        return root, pre
    return root, Path(DEFAULT_FIRERED_MODELS)


def firered_status() -> dict:
    """{ok, why, root, models} — dùng cho Cài đặt (cảnh báo trước khi chạy) và thông báo lỗi."""
    root, models = firered_paths()
    if not (root / "fireredasr2s").is_dir():
        return {"ok": False, "root": str(root), "models": str(models),
                "why": f"chưa cài mã nguồn FireRedASR2S ở {root} — chạy scripts\\install_firered_asr.bat"
                       + (" (FIRERED_ROOT đang trỏ máy khác?)" if setting("FIRERED_ROOT", "") else "")}
    broken = [sub for sub in ("fireredasr2", "fireredlid", "fireredpunc")
              if not (root / "fireredasr2s" / sub / "models" / "__init__.py").is_file()
              and not any((root / "fireredasr2s" / sub / "models").glob("*.py"))]
    if broken:      # fix 2026-11-16: bản vendor trong git từng thiếu thư mục code models/ (.gitignore «models/» trần)
        return {"ok": False, "root": str(root), "models": str(models),
                "why": f"mã nguồn FireRed thiếu code {', '.join(b + '/models' for b in broken)} ở {root} — git pull bản mới, "
                       "hoặc xoá thư mục đó rồi chạy scripts\\install_firered_asr.bat"}
    miss = [d for d in FIRERED_MODEL_DIRS if not (models / d).exists()]
    if miss:
        return {"ok": False, "root": str(root), "models": str(models),
                "why": f"thiếu model {', '.join(miss)} trong {models} — chạy scripts\\install_firered_asr.bat"}
    return {"ok": True, "root": str(root), "models": str(models), "why": ""}
