"""Nhà cung cấp LLM qua API — mỗi nhà cung cấp một file, phần còn lại chỉ gọi theo tên model.

    model = "deepseek/<tên>"  →  engines.providers.deepseek
    model = "alibaba/<tên>"   →  engines.providers.alibaba  (nhận cả tiền tố "qwen/")

Tên model KHÔNG ghi cứng — lấy từ nhà cung cấp: `list_models(provider)` (GET /models), `pick_model(ids)` chọn bản
«flash» cho dịch/soát.

`engines.llm.chat()` và Agent (`agents.agent_core`) hỏi `resolve(model)` trước mọi đường khác. Thêm nhà cung cấp mới =
thêm một file khai báo `PROVIDER = Provider(...)` rồi đăng ký vào `_REGISTRY` dưới đây.

Nhà cung cấp TÙY CHỈNH (người dùng tự khai trong Cài đặt → AI, `providers/custom.py`): mọi API chuẩn OpenAI —
`<tên>/<model>`; đọc cấu hình lúc gọi nên thêm / xoá không cần restart.

Đường khác giữ nguyên ở engines.llm: `chatgpt/…` / `aurora/…` (Aurora), `local/…` (Ollama), `llama/…` / "local" (:8083).
"""
from __future__ import annotations

from . import alibaba, custom, deepseek
from ._openai import Provider, cached_models, chat_message, chat_text, list_models, pick_model

_REGISTRY: dict[str, Provider] = {
    "deepseek": deepseek.PROVIDER,
    "alibaba": alibaba.PROVIDER,
    "qwen": alibaba.PROVIDER,          # bí danh
}


def resolve(model: str | None) -> tuple[Provider, str] | None:
    """(nhà cung cấp, tên model bỏ tiền tố) nếu `model` thuộc một nhà cung cấp API; không thì None."""
    m = (model or "").strip()
    if "/" not in m:
        return None
    prefix, _, rest = m.partition("/")
    key = prefix.strip().lower()
    p = _REGISTRY.get(key) or custom.providers().get(key)
    if p is None:
        return None
    return p, rest.strip()


def all_providers() -> list[Provider]:
    seen, out = set(), []
    for p in [*_REGISTRY.values(), *custom.providers().values()]:
        if p.name not in seen:
            seen.add(p.name)
            out.append(p)
    return out


__all__ = ["Provider", "resolve", "all_providers", "chat_message", "chat_text", "list_models", "cached_models",
           "pick_model"]
