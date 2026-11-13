"""
llm.py — Gọi LLM qua 9router API (tương thích chuẩn OpenAI).

Cấu hình qua config.json / pineline.config.json (LLM_* / TRANSLATE_MODEL / REVIEW_MODEL: file config thắng env):
    LLM_BASE_URL   mặc định http://192.168.31.247:20128/v1
    LLM_API_KEY    (chỉ đặt trong pineline.config.json — không ghi key vào code/.bat)
    LLM_MODEL      ollama/gemma4:31b-cloud
    TRANSLATE_MODEL ollama/gemma4:31b-cloud
"""
from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
import json
import os
import sys
import time

# Đảm bảo stdout/stderr luôn UTF-8 trên Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

try:
    from core.config import get as _cfg_get
except Exception:  # noqa: BLE001
    def _cfg_get(key: str, default: str | None = None) -> str | None:
        return os.getenv(key) or default

# Tự động nạp config.json & pineline.config.json vào os.environ nếu chạy trực tiếp từ CLI
try:
    import core.config as _config_boot
    _config_boot.inject_env()
except Exception:  # noqa: BLE001
    pass

BASE_URL = os.getenv("LLM_BASE_URL", "http://192.168.31.247:20128/v1").rstrip("/")
API_KEY = os.getenv("LLM_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "ollama/gemma4:31b-cloud")

# LLM LOCAL:
# 1. Ollama :11434 (tiền tố "local/")
LOCAL_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
LOCAL_PREFIX = "local/"

# 2. llama-server :8083 (OpenAI-compatible siêu tốc GGUF)
LLAMA_BASE_URL = _machine.llama_base_url()  # machine-config: LLAMA_BASE_URL > LLM_BASE_URL(localhost) > :8083


class LLMUnavailable(RuntimeError):
    """Không gọi được LLM (mất mạng / sai key / 429-5xx kéo dài). Caller phải DỪNG stage,
    không được coi như câu rỗng rồi chia lô / để trống."""


def _settings() -> tuple[str, str, str]:
    """(base, key, model) đọc LÚC GỌI (file config trước env) — sửa pineline.config.json
    có hiệu lực ngay, không bị đóng băng ở giá trị lúc import."""
    try:
        import core.config as _c
        s = _c.llm_settings()
        return s["base"], s["key"], s["model"]
    except Exception:  # noqa: BLE001
        return BASE_URL, API_KEY, MODEL


def _llama_base() -> str:
    try:
        import core.config as _c
        return _machine.llama_base_url()
    except Exception:
        return LLAMA_BASE_URL


def get_llama_models(llama_base: str | None = None) -> list[str]:
    """Lấy danh sách các model đang load trên llama-server (:8083)."""
    base = (llama_base or _llama_base()).rstrip("/")
    try:
        r = requests.get(f"{base}/models", headers={"Authorization": "Bearer sk-local"}, timeout=2)
        if r.ok:
            d = r.json()
            models = []
            for item in d.get("data", []) or d.get("models", []):
                mid = item.get("id") or item.get("name")
                if mid and mid not in models:
                    models.append(mid)
            return models
    except Exception:
        pass
    return []


def get_ollama_models(ollama_base: str | None = None) -> list[str]:
    """Lấy danh sách model trên Ollama (:11434)."""
    base = (ollama_base or LOCAL_BASE_URL).rstrip("/")
    api_url = f"{base[:-3]}/api/tags" if base.endswith("/v1") else f"{base}/api/tags"
    try:
        r = requests.get(api_url, timeout=2)
        if r.ok:
            return [m["name"] for m in r.json().get("models", []) if "name" in m]
    except Exception:
        pass
    return []


def list_available_models() -> dict:
    """Trả về toàn bộ model khả dụng (llama-server 8083, ollama 11434, config)."""
    llama_models = get_llama_models()
    ollama_models = get_ollama_models()
    _, _, def_model = _settings()
    out = []
    # 1. Các model đang chạy trên llama-server (:8083)
    for m in llama_models:
        out.append({
            "id": m,
            "label": f"{m} (:8083 ⭐ Local)",
            "source": "llama",
        })
    # Dự phòng các model phổ biến của llama-server nếu server chưa bật hoặc load GGUF
    for fallback_m in ("gemma4-12b-qat-mtp-fast", "gemma4-12b", "gemma4-26b-qat-uncen-mtp", "qwen36-tq3"):
        if not any(x["id"] == fallback_m for x in out):
            out.append({
                "id": fallback_m,
                "label": f"{fallback_m} (:8083)",
                "source": "llama",
            })
    # 2. Các model Ollama
    for m in ollama_models:
        out.append({
            "id": f"local/{m}",
            "label": f"{m} (Ollama)",
            "source": "ollama",
        })
    if not ollama_models:
        for om in ("gemma4", "gemma3:12b"):
            out.append({
                "id": f"local/{om}",
                "label": f"{om} (Ollama)",
                "source": "ollama",
            })
    # 3. Model config
    if def_model and not any(x["id"] == def_model for x in out):
        out.append({
            "id": def_model,
            "label": f"{def_model} (từ config)",
            "source": "config",
        })
    return {
        "models": out,
        "default": def_model,
        "llama_running": bool(llama_models),
        "llama_base": _llama_base(),
    }


def _is_loopback_base(url: str) -> bool:
    u = (url or "").lower()
    return "127.0.0.1" in u or "localhost" in u


def is_local(model: str | None) -> bool:
    return bool(model) and model.startswith(LOCAL_PREFIX)


CHATGPT_PREFIXES = ("chatgpt/", "aurora/")


def is_chatgpt(model: str | None) -> bool:
    """Model route qua Aurora / Chat2Api (pool account Free). Tiền tố chatgpt/ hoặc aurora/."""
    m = (model or "").strip().lower()
    return bool(m) and any(m.startswith(p) for p in CHATGPT_PREFIXES)


def _chatgpt_settings() -> tuple[list[str], str, str]:
    """(bases, key, default_model) cho Aurora pool."""
    # machine-config: AURORA_BASE_URL, CHATGPT_BASE_URL, :3377 local, AURORA_FALLBACK_URLS (LAN)
    bases: list[str] = list(_machine.aurora_base_urls())
    try:
        key = (_cfg_get("CHATGPT_API_KEY") or _cfg_get("CHATGPT_ACCESS_TOKEN") or "").strip()
    except Exception:
        key = (os.getenv("CHATGPT_API_KEY") or os.getenv("CHATGPT_ACCESS_TOKEN") or "").strip()
    if not key:
        tok = _chatgpt_token_file()
        if tok:
            key = tok
    try:
        default_m = (_cfg_get("CHATGPT_MODEL") or "gpt-5-6-mini").strip()
    except Exception:
        default_m = (os.getenv("CHATGPT_MODEL") or "gpt-5-6-mini").strip()
    return bases, key, default_m


def _jwt_expired(token: str) -> bool:
    """True nếu JWT có claim exp đã qua (token local hết hạn không được đè Aurora pool)."""
    try:
        import base64, json as _json
        parts = (token or "").split(".")
        if len(parts) < 2:
            return False
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(pad.encode("ascii")))
        exp = int(payload.get("exp") or 0)
        return bool(exp) and exp < int(time.time()) - 30
    except Exception:
        return False


def _chatgpt_token_file() -> str:
    """Đọc access token tùy chọn từ data/chatgpt_token.txt (bỏ qua nếu JWT hết hạn)."""
    try:
        from pathlib import Path
        p = Path(__file__).resolve().parents[1] / "data" / "chatgpt_token.txt"
        if p.is_file():
            tok = p.read_text(encoding="utf-8").strip()
            if tok and _jwt_expired(tok):
                print(
                    "[llm] bỏ qua data/chatgpt_token.txt (JWT hết hạn) — dùng Aurora pool",
                    flush=True,
                )
                return ""
            return tok
    except Exception:
        pass
    return ""


def _chatgpt_chat(
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    retries: int,
    timeout: int,
) -> str:
    """Gọi Aurora/Chat2Api (OpenAI-compatible). Pool Free xoay phía server Aurora."""
    bases, key, default_m = _chatgpt_settings()
    m = (model or "").strip()
    for pfx in CHATGPT_PREFIXES:
        if m.lower().startswith(pfx):
            m = m[len(pfx):].strip()
            break
    if not m or m.lower() in ("auto", "default", "chatgpt", "aurora"):
        m = default_m or "gpt-5-6-mini"
    # fallback chain nếu model cụ thể fail
    candidates = [m]
    for alt in (default_m, "gpt-5-6-mini", "gpt-5-6", "auto"):
        if alt and alt not in candidates:
            candidates.append(alt)

    last_err: Exception | None = None
    for chosen in candidates:
        payload: dict = {
            "model": chosen,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        for base in bases:
            url = f"{base.rstrip('/')}/chat/completions"
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            for attempt in range(max(1, min(retries, 3))):
                try:
                    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
                    if r.status_code in (401, 403) and key:
                        # thử lại không token (Aurora pool nội bộ)
                        headers.pop("Authorization", None)
                        key = ""
                        continue
                    # 413/400/422 = payload quá lớn hoặc request không hợp lệ — retry cùng body vô ích
                    if r.status_code in (400, 413, 422):
                        raise LLMUnavailable(
                            f"Aurora/ChatGPT {r.status_code} (no-retry): {r.text[:300]}"
                        )
                    if r.status_code in (429, 500, 502, 503, 504):
                        raise requests.HTTPError(f"{r.status_code} {r.text[:200]}")
                    r.raise_for_status()
                    msg = (r.json().get("choices") or [{}])[0].get("message") or {}
                    content = (msg.get("content") or "").strip()
                    if content:
                        print(f"[llm] chatgpt/{chosen} via {base}", flush=True)
                        return content
                    payload.pop("response_format", None)
                    time.sleep(0.4)
                except LLMUnavailable:
                    raise
                except Exception as e:  # noqa: BLE001
                    # HTTPError/Response với status client-error → không retry cùng payload
                    sc = getattr(getattr(e, "response", None), "status_code", None)
                    if sc in (400, 413, 422):
                        raise LLMUnavailable(f"Aurora/ChatGPT {sc} (no-retry): {e}") from e
                    last_err = e
                    wait = min(2 ** attempt, 6)
                    print(f"[llm] chatgpt lỗi {base} {chosen} ({attempt + 1}): {e} — {wait}s", flush=True)
                    time.sleep(wait)
    raise LLMUnavailable(f"Aurora/ChatGPT thất bại: {last_err} — {_machine.aurora_missing_hint()}")


def aurora_upload_file(path: str | Path, purpose: str = "assistants") -> dict:
    """Upload file lên Aurora `POST /v1/files` (cần ChatGPT access_token thật).

    Docs: https://github.com/aurora-develop/aurora/blob/main/API.md
    - Session/chat pool sống ≠ upload file được (cần OAuth access_token còn hạn).
    - UUID free / token hết hạn → 401. Ưu tiên để Aurora tự lấy pool (không gửi token local),
      rồi mới thử CHATGPT_ACCESS_TOKEN / data/chatgpt_token.txt.
    """
    from pathlib import Path as _P
    path = _P(path)
    if not path.is_file():
        raise LLMUnavailable(f"Aurora upload: không thấy file {path}")
    bases, key, _ = _chatgpt_settings()
    # Thứ tự auth: (1) để Aurora pool tự chọn — không gắn Bearer
    # (2) API key dịch vụ nếu có (3) access_token client (có thể đã expire)
    auth_candidates: list[tuple[str, str | None]] = [("aurora-pool", None)]
    force_local = (_cfg_get("CHATGPT_FORCE_TOKEN") or os.getenv("CHATGPT_FORCE_TOKEN") or "").strip() in (
        "1", "true", "yes",
    )
    if key and key != "sk-local" and (force_local or not key.startswith("eyJ")):
        # key dạng service (không phải JWT ChatGPT) — thử trước JWT local
        auth_candidates.append(("service-key", key))
    if key and key.startswith("eyJ"):
        auth_candidates.append(("local-access-token", key))
    elif key and key != "sk-local" and ("service-key", key) not in auth_candidates:
        auth_candidates.append(("config-key", key))

    last_err: Exception | None = None
    for base in bases:
        root = base.rstrip("/")
        url = f"{root}/files" if root.endswith("/v1") else f"{root}/v1/files"
        for label, auth in auth_candidates:
            headers = {}
            if auth:
                headers["Authorization"] = f"Bearer {auth}"
            try:
                with path.open("rb") as f:
                    r = requests.post(
                        url,
                        headers=headers or None,
                        files={"file": (path.name, f, "application/octet-stream")},
                        data={"purpose": purpose},
                        timeout=180,
                    )
                if r.status_code >= 400:
                    raise requests.HTTPError(f"{r.status_code} {r.text[:300]}")
                data = r.json() if r.content else {}
                fid = data.get("id") or data.get("file_id")
                if not fid:
                    raise LLMUnavailable(f"Aurora upload không trả file id: {str(data)[:200]}")
                acct = (
                    r.headers.get("X-Aurora-Account-Id")
                    or r.headers.get("x-aurora-account-id")
                    or data.get("account_id")
                    or ""
                )
                print(
                    f"[llm] aurora upload {path.name} -> {fid} via {root} ({label})"
                    + (f" acct={acct[:8]}…" if acct else ""),
                    flush=True,
                )
                return {
                    "id": fid,
                    "raw": data,
                    "base": root,
                    "auth": label,
                    "account_id": acct,
                }
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[llm] aurora upload lỗi {base} [{label}]: {e}", flush=True)
    raise LLMUnavailable(
        f"Aurora upload thất bại (pool session sống vẫn có thể thiếu OAuth file): {last_err}"
    )


def _file_unread_response(text: str) -> bool:
    """GPT trả JSON/text báo không đọc được file đã upload (thường do lệch account pool)."""
    t = (text or "").lower()
    needles = (
        "không đọc được",
        "khong doc duoc",
        "không thể truy cập",
        "khong the truy cap",
        "cannot access",
        "can't access",
        "could not read",
        "unable to read",
        "file not found",
        "tải lại file",
        "tai lai file",
        "upload again",
    )
    return any(n in t for n in needles)


def chatgpt_chat_with_file(
    file_id: str,
    prompt: str,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    json_mode: bool = True,
    timeout: int = 900,
    base: str | None = None,
    account_id: str | None = None,
) -> str:
    """Hỏi GPT kèm file đã upload (`type: input_file` + `file_id`) — 1 lần đọc file.

    BẮT BUỘC cùng host Aurora với bước upload. Gửi X-Aurora-Account-Id nếu có
    (Aurora sticky cùng session đã upload — nếu không GPT sẽ báo không đọc được file).
    """
    try:
        cfg_t = int(_cfg_get("CHATGPT_TIMEOUT", "900") or 900)
    except Exception:
        cfg_t = 900
    cfg_t = max(180, min(cfg_t, 1800))
    timeout = max(int(timeout or 0), cfg_t)
    timeout = max(180, min(timeout, 1800))
    bases, key, default_m = _chatgpt_settings()
    if base:
        b = base.rstrip("/")
        bases = [b] + [x for x in bases if x.rstrip("/") != b]
    if key and key.startswith("eyJ") and _jwt_expired(key):
        key = ""
    force_local = (_cfg_get("CHATGPT_FORCE_TOKEN") or os.getenv("CHATGPT_FORCE_TOKEN") or "").strip() in (
        "1", "true", "yes",
    )
    auth_order: list[str | None] = [None]
    if key and key != "sk-local" and (force_local or not key.startswith("eyJ") or not _jwt_expired(key)):
        if key not in auth_order:
            auth_order.append(key)

    m = (model or "").strip()
    for pfx in CHATGPT_PREFIXES:
        if m.lower().startswith(pfx):
            m = m[len(pfx):].strip()
            break
    if not m or m.lower() in ("auto", "default", "chatgpt", "aurora"):
        m = default_m or "auto"
    content = [
        {"type": "input_file", "file_id": file_id},
        {"type": "text", "text": prompt},
    ]
    last_err: Exception | None = None
    unread_hits = 0
    for chosen in (m, "auto", "gpt-5-6-mini", "gpt-5-6"):
        if not chosen:
            continue
        payload: dict = {
            "model": chosen,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        for b in bases:
            url = f"{b.rstrip('/')}/chat/completions"
            for auth in auth_order:
                headers = {"Content-Type": "application/json"}
                if auth:
                    headers["Authorization"] = f"Bearer {auth}"
                if account_id:
                    headers["X-Aurora-Account-Id"] = account_id
                try:
                    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
                    if r.status_code in (401, 403) and auth:
                        continue
                    if r.status_code >= 400:
                        raise requests.HTTPError(f"{r.status_code} {r.text[:300]}")
                    msg = (r.json().get("choices") or [{}])[0].get("message") or {}
                    text = (msg.get("content") or "").strip()
                    if text and _file_unread_response(text):
                        unread_hits += 1
                        print(
                            f"[llm] chatgpt+file/{chosen} file_id={file_id} "
                            f"UNREAD (lệch account pool?) via {b}",
                            flush=True,
                        )
                        last_err = RuntimeError("Aurora file unread — cần sticky account upload↔chat")
                        continue
                    if text:
                        label = "pool" if not auth else "bearer"
                        print(
                            f"[llm] chatgpt+file/{chosen} file_id={file_id} ({label}) via {b}",
                            flush=True,
                        )
                        return text
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    print(f"[llm] chatgpt+file lỗi {chosen}: {e}", flush=True)
                    time.sleep(0.5)
    if unread_hits:
        raise LLMUnavailable(
            f"Aurora nhận file_id nhưng GPT không đọc được (upload/chat lệch account). "
            f"Cần Aurora sticky X-Aurora-Account-Id. last={last_err}"
        )
    raise LLMUnavailable(f"Aurora file-QA thất bại: {last_err}")


_pulled: set = set()   # cache trong tiến trình: model đã kiểm/pull rồi


def ensure_ollama_model(name: str) -> None:
    """Model Ollama chưa pull -> TỰ pull (lần đầu, có thể vài GB). Auto cho bước dịch."""
    if name in _pulled:
        return
    import subprocess
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=15)
        tokens = [ln.split()[0] for ln in (r.stdout or "").splitlines() if ln.split()]
        if name in tokens or (":" not in name and f"{name}:latest" in tokens):
            _pulled.add(name)
            return
    except Exception:  # noqa: BLE001
        return   # ollama chưa chạy/không có -> để chat() báo lỗi kết nối rõ hơn
    print(f"[ollama] Model '{name}' chưa có -> đang PULL lần đầu (có thể vài GB, chờ chút)...",
          flush=True)
    try:
        subprocess.run(["ollama", "pull", name], timeout=7200)   # stream tiến độ ra stdout
        _pulled.add(name)
    except Exception as e:  # noqa: BLE001
        print(f"[ollama] pull '{name}' lỗi: {e}. Chạy tay: ollama pull {name}", flush=True)


def free_ollama_vram(reason: str = "") -> None:
    """Alias — nhả VRAM (Ollama + torch) trước/sau bước GPU."""
    from engines.vram import free_gpu_vram
    free_gpu_vram(reason)


def unload_local(model: str | None) -> None:
    """Nhả model Ollama khỏi VRAM (RTX 3060 12GB không chứa nổi LLM + TTS cùng
    lúc). Gọi sau khi dịch xong, trước bước lồng tiếng. Model cloud thì bỏ qua."""
    if not is_local(model):
        free_ollama_vram("sau bước LLM")
        return
    free_ollama_vram(f"nhả {model}")


def _ollama_num_ctx() -> int:
    """Gemma3/4 mặc định context 131072. Cấp KV 128k trên RTX 3060 12GB -> tràn
    VRAM, Ollama offload ~nửa model sang CPU -> CPU 100% + chậm/timeout.
    Dịch lô chỉ cần ~4–8k token. Env LLM_NUM_CTX để chỉnh."""
    try:
        return max(2048, int(os.getenv("LLM_NUM_CTX") or "8192"))
    except ValueError:
        return 8192


def _ollama_chat(model, messages, temperature, max_tokens, json_mode, retries, timeout) -> str:
    """Gọi Ollama qua endpoint GỐC /api/chat + TẮT thinking (think=False).
    LÝ DO: gemma4/qwen3... bật 'thinking' -> ngốn hết token cho phần suy nghĩ ->
    content JSON RỖNG (nhất là lô lớn). Endpoint OpenAI KHÔNG nhận 'think' nên phải native."""
    base = LOCAL_BASE_URL[:-3] if LOCAL_BASE_URL.endswith("/v1") else LOCAL_BASE_URL
    url = f"{base}/api/chat"
    num_ctx = _ollama_num_ctx()
    # num_predict: trần sinh. 16384 trên lô lớn + ctx 128k = hàng phút/lô.
    predict = min(int(max_tokens or 2048), 8192)
    body = {
        "model": model, "messages": messages, "think": False, "stream": False,
        "keep_alive": "5m",
        "options": {
            "temperature": temperature,
            "num_predict": predict,
            "num_ctx": num_ctx,
        },
    }
    if json_mode:
        body["format"] = "json"
    empty = 0
    last_err: Exception | None = None
    # Timeout = lô quá to / ctx quá lớn. Retry cùng payload 4 lần = 20 phút đốt CPU.
    # 1 lần timeout -> trả "" để translate splitter chia đôi lô.
    tries = min(retries, 2)
    for attempt in range(tries):
        try:
            r = requests.post(url, json=body, timeout=(15, timeout))
            r.raise_for_status()
            content = (r.json().get("message", {}).get("content") or "")
            if content.strip():
                return content
            empty += 1
            body.pop("format", None)              # rỗng -> bỏ ép json (thử lại)
            if empty >= 2:
                return ""
            print("[llm] (ollama) rỗng — thử lại (bỏ json)")
            time.sleep(0.5)
        except (requests.Timeout, requests.exceptions.ReadTimeout) as e:
            print(f"[llm] (ollama) timeout {timeout}s (ctx={num_ctx}, predict={predict}) "
                  f"— dừng retry, chia lô nhỏ hơn. {e}")
            return ""
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[llm] (ollama) lỗi (thử {attempt + 1}/{tries}): {e}")
            time.sleep(min(2 ** attempt, 8))
    if last_err is not None:
        # Mất kết nối ≠ lô quá to: trả "" sẽ làm splitter chia nhỏ mãi rồi để trống câu
        raise LLMUnavailable(f"Ollama lỗi sau {tries} lần: {last_err}")
    return ""


def chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    json_mode: bool = False,
    retries: int = 4,
    timeout: int = 300,
    **kwargs,
) -> str:
    """Gửi messages, trả về text của assistant. Tự retry khi lỗi mạng/429/5xx.

    Model tiền tố 'local/' -> Ollama GỐC (tắt thinking, không mạng, không key)."""
    # alias cũ: json_mode / max_tokens
    if "json_mode" in kwargs:
        json_mode = bool(kwargs.pop("json_mode"))
    if "max_tokens" in kwargs:
        max_tokens = int(kwargs.pop("max_tokens"))
    base, key, default_model = _settings()
    llama_base = _llama_base()
    m = (model or default_model or "").strip()
    if is_local(m):
        m = m[len(LOCAL_PREFIX):]
        ensure_ollama_model(m)                    # chưa pull -> tự pull (lần đầu)
        return _ollama_chat(m, messages, temperature, max_tokens, json_mode, retries, timeout)

    if is_chatgpt(m):
        # Local :3377 có thể 5–15 phút/lô — không cắt cứng 120s (client abort → proxy wsasend).
        try:
            cfg_t = int(_cfg_get("CHATGPT_TIMEOUT", "900") or 900)
        except Exception:
            try:
                cfg_t = int(os.getenv("CHATGPT_TIMEOUT") or "900")
            except Exception:
                cfg_t = 900
        cfg_t = max(180, min(cfg_t, 1800))
        t_chat = max(int(timeout or 0), cfg_t)
        t_chat = max(180, min(t_chat, 1800))
        return _chatgpt_chat(
            m, messages, temperature, max_tokens, json_mode,
            retries=min(retries, 3), timeout=t_chat,
        )

    # Nhận diện tự động nếu gọi vào llama-server :8083
    target_base = base
    target_key = key
    is_llama = False
    is_cloud = False

    # ollama/*-cloud (vd gemma4:31b-cloud) → 9router, KHÔNG nuốt vào :8083 dù LLM_BASE=local
    ml = m.lower()
    if ml.startswith("ollama/") or "-cloud" in ml or ml.endswith(":cloud"):
        is_cloud = True
        cloud_base = (_cfg_get("CLOUD_LLM_BASE_URL") or "").strip().rstrip("/")
        cloud_key = (_cfg_get("CLOUD_LLM_API_KEY") or "").strip()
        if not cloud_base:
            cloud_base = _machine.cloud_llm_base_url()
        if not cloud_key or cloud_key == "sk-local":
            # fallback: LLM_* nếu đang trỏ 9router
            if "20128" in (base or "") and key and key != "sk-local":
                cloud_base, cloud_key = base, key
            else:
                raise LLMUnavailable(
                    "REVIEW/cloud model cần CLOUD_LLM_API_KEY (9router) trong pineline.config.json"
                )
        target_base, target_key = cloud_base, cloud_key
        print(f"[llm] cloud {m} via {target_base}", flush=True)

    llama_active = get_llama_models(llama_base)

    if is_cloud:
        pass  # đã set target_*
    elif m.lower() in ("local", "llama", "auto", "default", "llama/auto"):
        is_llama = True
        m = llama_active[0] if llama_active else "local"
    elif m.startswith("llama/"):
        m = m[len("llama/"):]
        is_llama = True
    elif "(:8083" in m:
        m = m.split()[0]
        is_llama = True
    elif _is_loopback_base(base):
        is_llama = True
        if llama_active and (not m or m.lower() in ("local", "auto", "default")):
            m = llama_active[0]
    elif (llama_active and m in llama_active) or any(
        p in m.lower() for p in ("gemma4-12b", "gemma4-26b", "qwen36", "qat", "mtp")
    ):
        is_llama = True

    if is_llama:
        target_base = llama_base
        target_key = "sk-local"
        if llama_active and (not m or m.lower() in ("local", "auto", "default", "llama")):
            m = llama_active[0]

    # llama-server / OpenAI-compatible local thường không cần key thật
    if not target_key:
        if _is_loopback_base(target_base):
            target_key = "sk-local"
        else:
            raise LLMUnavailable("Chưa set LLM_API_KEY (pineline.config.json).")

    url = f"{target_base}/chat/completions"
    headers = {"Authorization": f"Bearer {target_key}", "Content-Type": "application/json"}
    # Gemma4/Qwen3 trên llama-server hay đốt hết max_tokens vào reasoning_content
    # -> content rỗng. Tắt thinking mặc định (có thể bật lại: LLM_ENABLE_THINKING=1).
    enable_think = (os.getenv("LLM_ENABLE_THINKING") or "").strip() in ("1", "true", "yes")
    payload: dict = {
        "model": m,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # Một số proxy OpenAI-compatible MẶC ĐỊNH trả streaming (SSE). Ta đọc
        # nguyên response bằng r.json() nên phải TẮT stream, không thì vỡ.
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": enable_think},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_err = None
    empty_streak = False
    empty_seen = 0
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} {r.text[:200]}")
            r.raise_for_status()
            data = r.json()
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            content = (msg.get("content") or "").strip()
            if not content:
                # fallback hiếm: một số bản chỉ nhét vào reasoning
                content = (msg.get("reasoning_content") or "").strip()
            if content:
                return content
            # RỖNG: retry TỐI ĐA 2 lần (bắt ca ngẫu nhiên). Rỗng dai (lô quá to) -> trả ""
            # NGAY để splitter chia nhỏ (né retry vô ích làm chậm).
            empty_streak = True
            empty_seen += 1
            payload.pop("response_format", None)   # tắt json_mode: nhiều model rỗng khi bị ép json
            if empty_seen >= 2:
                return ""
            print(f"[llm] rỗng — thử lại 1 lần (tắt json_mode)")
            time.sleep(0.5)
            continue
        except Exception as e:  # noqa: BLE001
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (400, 413, 422):
                # payload bị từ chối (thường lô quá dài) — retry y nguyên vô ích; splitter chia nhỏ
                raise RuntimeError(f"LLM từ chối payload ({status}): {str(e)[:200]}") from e
            empty_streak = False
            last_err = e
            wait = min(2 ** attempt, 8)
            print(f"[llm] lỗi (thử {attempt + 1}/{retries}): {e} — chờ {wait}s")
            time.sleep(wait)
    if empty_streak:
        return ""          # vẫn rỗng -> trả "" để caller (splitter) chia nhỏ/để trống
    raise LLMUnavailable(f"Gọi LLM thất bại sau {retries} lần: {last_err}")


def chat_with_meta(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    json_mode: bool = False,
    timeout: int = 300,
) -> tuple[str, dict]:
    """Giống chat() OpenAI-path nhưng trả (content, meta) gồm usage/timings/TPS.

    Chỉ dùng cho OpenAI-compatible (llama-server :8083). Ollama local/ -> meta rỗng.
    """
    base, key, default_model = _settings()
    m = model or default_model
    if is_local(m):
        return chat(messages, model=m, temperature=temperature, max_tokens=max_tokens,
                    json_mode=json_mode, timeout=timeout, retries=2), {}
    key = key or ("sk-local" if _is_loopback_base(base) else "")
    if not key:
        raise RuntimeError("Chưa set LLM_API_KEY")
    enable_think = (os.getenv("LLM_ENABLE_THINKING") or "").strip() in ("1", "true", "yes")
    payload = {
        "model": m[len(LOCAL_PREFIX):] if is_local(m) else m,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": enable_think},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    r = requests.post(
        f"{base.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    content = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
    usage = data.get("usage") or {}
    timings = data.get("timings") or {}
    meta = {
        "finish_reason": (data.get("choices") or [{}])[0].get("finish_reason"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "prompt_tps": timings.get("prompt_per_second"),
        "gen_tps": timings.get("predicted_per_second"),
        "prompt_ms": timings.get("prompt_ms"),
        "gen_ms": timings.get("predicted_ms"),
        "predicted_n": timings.get("predicted_n"),
    }
    return content, meta


def parse_json(text: str):
    """Bóc JSON kể cả khi model bọc trong ```json ... ```, trailing commas hoặc kèm chữ thừa."""
    t = (text or "").strip()
    if not t:
        raise ValueError(
            "LLM trả về RỖNG — thường do model 'reasoning' hết token trước khi "
            "xuất JSON. Tăng max_tokens hoặc đổi model."
        )
    # Bóc markdown code block nếu có
    if "```" in t:
        import re
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t)
        if m:
            t = m.group(1).strip()
        elif t.startswith("```"):
            t = t.split("```", 2)[1]
            if t.startswith("json"):
                t = t[4:]
            t = t.strip()

    def _clean_json_str(s: str) -> str:
        import re
        # Xoá trailing comma trước } hoặc ]
        s = re.sub(r",\s*([\}\]])", r"\1", s)
        return s

    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass

    # Thử clean trailing commas
    try:
        return json.loads(_clean_json_str(t))
    except json.JSONDecodeError:
        pass

    # Vớt cụm {...} hoặc [...] đầu tiên
    for op, cl in (("{", "}"), ("[", "]")):
        i, j = t.find(op), t.rfind(cl)
        if i != -1 and j != -1 and j > i:
            sub = t[i : j + 1]
            try:
                return json.loads(sub)
            except json.JSONDecodeError:
                try:
                    return json.loads(_clean_json_str(sub))
                except json.JSONDecodeError:
                    pass
    raise ValueError(f"Không thể parse JSON từ phản hồi LLM (độ dài: {len(text)})")

