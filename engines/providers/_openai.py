"""Lõi dùng chung cho nhà cung cấp LLM có API chuẩn OpenAI (`POST {base}/chat/completions`).

Mỗi nhà cung cấp (deepseek.py, alibaba.py…) chỉ khai báo một `Provider`: URL / tên khoá cấu hình / model mặc định /
chỉnh payload riêng. Module này lo phần chung: lấy key, gửi request, thử lại lỗi tạm, báo lỗi rõ ràng.

Lỗi trả ra đều là `engines.llm.LLMUnavailable` (cùng loại các đường khác) và giữ nguyên `requests.HTTPError` ở
`__cause__` → `engines.cloud_retry.classify_cloud_retry` đọc được mã HTTP: 429 = «limit», 5xx / timeout / mất kết nối =
«transient» (hàng chờ tự thử lại), 400/401/403/404 = lỗi cấu hình (không thử lại).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}
NO_RETRY_STATUS = {400, 401, 403, 404, 413, 422}


def _cfg(key: str, default: str = "") -> str:
    """Đọc cấu hình LÚC GỌI (env > pineline.config.json) — sửa key không cần restart."""
    try:
        from core.config import get
        v = get(key, default)
    except Exception:  # noqa: BLE001 — chạy ngoài server (script) vẫn đọc env
        import os
        v = os.getenv(key, default)
    return str(v or "").strip()


@dataclass(frozen=True)
class Provider:
    name: str                                   # tiền tố model: "<name>/<model>"
    label: str                                  # tên hiển thị
    default_base: str
    base_keys: tuple[str, ...]                  # khoá cấu hình URL (khoá đầu có giá trị thắng)
    key_keys: tuple[str, ...]                   # khoá cấu hình API key
    default_model: str
    models: tuple[str, ...] = ()                # gợi ý cho màn hình Cài đặt
    max_tokens_cap: int = 8192                  # trần max_tokens của nhà cung cấp
    adjust: Callable[[dict, str], None] | None = field(default=None, compare=False)   # chỉnh payload riêng

    def base(self) -> str:
        for k in self.base_keys:
            v = _cfg(k)
            if v:
                return v.rstrip("/")
        return self.default_base.rstrip("/")

    def key(self) -> str:
        for k in self.key_keys:
            v = _cfg(k)
            if v:
                return v
        return ""

    def configured(self) -> bool:
        return bool(self.key())


def _error(provider: Provider, msg: str, cause: BaseException | None = None):
    from engines.llm import LLMUnavailable
    err = LLMUnavailable(f"{provider.label}: {msg}")
    if cause is not None:
        err.__cause__ = cause
    return err


def _ensure_json_word(messages: list[dict]) -> list[dict]:
    """JSON mode của DeepSeek / Qwen bắt buộc prompt có chữ «json» — thiếu thì thêm một dòng vào system."""
    if any("json" in str(m.get("content") or "").lower() for m in messages):
        return messages
    out = [dict(m) for m in messages]
    note = "Trả lời bằng JSON."
    if out and out[0].get("role") == "system":
        out[0]["content"] = f"{out[0].get('content') or ''}\n{note}".strip()
    else:
        out.insert(0, {"role": "system", "content": note})
    return out


def chat_message(
    provider: Provider,
    model: str,
    messages: list[dict],
    *,
    temperature: float | None = 0.3,
    max_tokens: int = 8192,
    json_mode: bool = False,
    tools: list[dict] | None = None,
    tool_choice: Any = None,
    retries: int = 4,
    timeout: int = 300,
) -> dict:
    """Một lần gọi chat/completions → message dict của assistant ({content, tool_calls?, reasoning_content?})."""
    key = provider.key()
    if not key:
        raise _error(provider, f"chưa có API key — điền {provider.key_keys[0]} trong Cài đặt AI / pineline.config.json")
    model = (model or provider.default_model).strip()
    if json_mode:
        messages = _ensure_json_word(messages)
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max(1, min(int(max_tokens or provider.max_tokens_cap), provider.max_tokens_cap)),
        "stream": False,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
    if provider.adjust is not None:
        provider.adjust(payload, model)

    url = f"{provider.base()}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last: BaseException | None = None
    for attempt in range(max(1, retries)):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            print(f"[{provider.name}] lỗi mạng (thử {attempt + 1}/{retries}): {e}", flush=True)
            time.sleep(min(2 ** attempt, 8))
            continue
        if r.status_code in NO_RETRY_STATUS:
            hint = {401: "sai API key", 403: "key không có quyền / hết hạn mức", 404: "sai địa chỉ hoặc tên model",
                    400: "request bị từ chối (tên model / tham số)", 413: "nội dung quá dài"}.get(r.status_code, "")
            http = requests.HTTPError(f"{r.status_code} {r.text[:300]}", response=r)
            raise _error(provider, f"HTTP {r.status_code}{' — ' + hint if hint else ''}: {r.text[:300]}", http)
        if r.status_code in RETRY_STATUS or r.status_code >= 500:
            last = requests.HTTPError(f"{r.status_code} {r.text[:200]}", response=r)
            wait = min(2 ** attempt, 8)
            ra = r.headers.get("Retry-After")
            if ra and ra.replace(".", "", 1).isdigit():
                wait = min(float(ra), 30)
            print(f"[{provider.name}] HTTP {r.status_code} (thử {attempt + 1}/{retries}) — chờ {wait}s", flush=True)
            time.sleep(wait)
            continue
        try:
            r.raise_for_status()
            data = r.json()
        except (requests.HTTPError, ValueError) as e:
            raise _error(provider, f"phản hồi không đọc được (HTTP {r.status_code}): {r.text[:200]}", e) from e
        msg = ((data.get("choices") or [{}])[0].get("message")) or {}
        return msg
    raise _error(provider, f"gọi thất bại sau {retries} lần: {last}", last)


def chat_text(provider: Provider, model: str, messages: list[dict], **kw) -> str:
    """Như chat_message nhưng trả text. Model chỉ trả phần suy luận (reasoning_content) → dùng phần đó."""
    msg = chat_message(provider, model, messages, **kw)
    content = str(msg.get("content") or "").strip()
    return content or str(msg.get("reasoning_content") or "").strip()
