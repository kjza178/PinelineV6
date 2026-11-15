"""Nhà cung cấp LLM qua API — mỗi nhà cung cấp một file, phần còn lại chỉ gọi theo tên model.

    model = "deepseek/deepseek-chat"  →  engines.providers.deepseek
    model = "alibaba/qwen-plus"       →  engines.providers.alibaba  (nhận cả tiền tố "qwen/")

`engines.llm.chat()` và Agent (`agents.agent_core`) hỏi `resolve(model)` trước mọi đường khác. Thêm nhà cung cấp mới =
thêm một file khai báo `PROVIDER = Provider(...)` rồi đăng ký vào `_REGISTRY` dưới đây.

Đường khác giữ nguyên ở engines.llm: `chatgpt/…` / `aurora/…` (Aurora), `local/…` (Ollama), `llama/…` / "local" (:8083).
"""
from __future__ import annotations

from . import alibaba, deepseek
from ._openai import Provider, chat_message, chat_text

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
    p = _REGISTRY.get(prefix.strip().lower())
    if p is None:
        return None
    return p, (rest.strip() or p.default_model)


def all_providers() -> list[Provider]:
    seen, out = set(), []
    for p in _REGISTRY.values():
        if p.name not in seen:
            seen.add(p.name)
            out.append(p)
    return out


__all__ = ["Provider", "resolve", "all_providers", "chat_message", "chat_text"]
