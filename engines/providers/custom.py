"""Nhà cung cấp TÙY CHỈNH — người dùng tự khai trong Cài đặt → AI, không cần sửa code (2026-11-28).

Mọi dịch vụ có API chuẩn OpenAI (`POST {địa chỉ}/chat/completions`, thường có `GET {địa chỉ}/models`) đều dùng được:
OpenAI, Google Gemini, OpenRouter, Groq, Mistral, xAI, Moonshot (Kimi), Anthropic (Claude), máy chủ riêng của người
khác / công ty… Model gọi dạng `<tên>/<model>` như DeepSeek: `openrouter/meta-llama/llama-3.3-70b-instruct`.

Lưu trong pineline.config.json (file riêng của máy, không lên git):
  CUSTOM_PROVIDERS            JSON: [{"name": "openrouter", "label": "OpenRouter", "base_url": "https://…/v1",
                                      "max_tokens": 8192}, …]
  CUSTOM_<TÊN>_API_KEY        API key của nhà đó
  CUSTOM_<TÊN>_BASE_URL       (tuỳ chọn) đổi địa chỉ mà không sửa danh sách
  CUSTOM_<TÊN>_MAX_TOKENS / CUSTOM_<TÊN>_CONCURRENCY   như DEEPSEEK_*
Khác biệt tham số giữa các nhà (JSON mode, max_completion_tokens, temperature) tự xử lý ở `_openai._autofix_400`.
"""
from __future__ import annotations

import json
import re

from ._openai import Provider, _cfg

CFG_KEY = "CUSTOM_PROVIDERS"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
# Tiền tố đã có nghĩa trong Pineline (hoặc từng có) — không cho đặt trùng.
RESERVED = {"deepseek", "alibaba", "qwen", "chatgpt", "aurora", "llama", "local", "ollama", "auto", "default",
            "llm", "custom", "simple"}

# Mẫu điền sẵn (chỉ là gợi ý: người dùng đổi tên / địa chỉ thoải mái). note = điều cần biết khi dùng.
TEMPLATES: list[dict] = [
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1",
     "note": "Key ở platform.openai.com → API keys."},
    {"id": "gemini", "label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "note": "Key ở aistudio.google.com → Get API key."},
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
     "note": "Một key dùng được rất nhiều model (Claude, GPT, Llama, Qwen…). Tên model dạng hang/model."},
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1", "note": "Rất nhanh, giới hạn lượt gọi thấp."},
    {"id": "mistral", "label": "Mistral", "base_url": "https://api.mistral.ai/v1", "note": ""},
    {"id": "xai", "label": "xAI (Grok)", "base_url": "https://api.x.ai/v1", "note": ""},
    {"id": "moonshot", "label": "Moonshot (Kimi)", "base_url": "https://api.moonshot.ai/v1", "note": ""},
    {"id": "anthropic", "label": "Anthropic (Claude)", "base_url": "https://api.anthropic.com/v1",
     "note": "Chế độ tương thích OpenAI. Nếu «Lấy danh sách model» lỗi: gõ tên model tay (vd anthropic/<tên model>)."},
]


def env_prefix(name: str) -> str:
    return f"CUSTOM_{name.upper()}"


def _norm_base(b: str) -> str:
    return str(b or "").strip().rstrip("/")


def specs() -> list[dict]:
    """Danh sách nhà cung cấp tùy chỉnh đã lưu (đã lọc mục hỏng — có log)."""
    raw = _cfg(CFG_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError as e:
        print(f"[custom-provider] {CFG_KEY} không phải JSON hợp lệ — bỏ qua: {e}", flush=True)
        return []
    out, seen = [], set()
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        n = str(it.get("name") or "").strip().lower()
        if not NAME_RE.match(n) or n in RESERVED or n in seen or not _norm_base(it.get("base_url")):
            print(f"[custom-provider] bỏ mục hỏng trong {CFG_KEY}: {it!r}", flush=True)
            continue
        seen.add(n)
        out.append({"name": n, "label": str(it.get("label") or n).strip()[:60] or n,
                    "base_url": _norm_base(it["base_url"]), "max_tokens": _int(it.get("max_tokens"), 8192),
                    "template": str(it.get("template") or "")})
    return out


def _int(v, default: int) -> int:
    try:
        x = int(float(v))
        return x if 256 <= x <= 1_000_000 else default
    except (TypeError, ValueError):
        return default


_CACHE: tuple[str, dict[str, Provider]] = ("", {})


def providers() -> dict[str, Provider]:
    """{tên: Provider} — đọc cấu hình lúc gọi (thêm / xoá trong Cài đặt có hiệu lực ngay, không restart)."""
    global _CACHE
    raw = _cfg(CFG_KEY)
    if raw == _CACHE[0]:
        return _CACHE[1]
    out = {}
    for sp in specs():
        pre = env_prefix(sp["name"])
        out[sp["name"]] = Provider(name=sp["name"], label=sp["label"], default_base=sp["base_url"],
                                   base_keys=(f"{pre}_BASE_URL",), key_keys=(f"{pre}_API_KEY",),
                                   max_tokens_cap=sp["max_tokens"], env=pre, custom=True)
    _CACHE = (raw, out)
    return out


def validate(spec: dict, *, existing: list[dict] | None = None, editing: str | None = None) -> dict:
    """Chuẩn hoá + kiểm 1 mục. Sai → ValueError (tiếng Việt, nói rõ sửa gì)."""
    n = str(spec.get("name") or "").strip().lower()
    if not NAME_RE.match(n):
        raise ValueError("tên ngắn: 2–24 ký tự, chữ thường không dấu / số / gạch dưới, bắt đầu bằng chữ (vd openrouter)")
    if n in RESERVED:
        raise ValueError(f"tên «{n}» đã dùng cho loại AI có sẵn — đặt tên khác")
    if n != (editing or "") and any(s["name"] == n for s in (existing or [])):
        raise ValueError(f"đã có nhà cung cấp «{n}»")
    b = _norm_base(spec.get("base_url"))
    if not re.match(r"^https?://[^\s/]+", b):
        raise ValueError("địa chỉ API phải bắt đầu bằng http:// hoặc https:// (vd https://openrouter.ai/api/v1)")
    if b.endswith("/chat/completions"):
        b = b[: -len("/chat/completions")]
    mt = spec.get("max_tokens")
    if mt not in (None, "") and _int(mt, 0) == 0:
        raise ValueError("trần output phải là số từ 256 đến 1000000")
    return {"name": n, "label": (str(spec.get("label") or "").strip() or n)[:60], "base_url": b,
            "max_tokens": _int(mt, 8192), "template": str(spec.get("template") or "")[:30]}


def dump(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False)
