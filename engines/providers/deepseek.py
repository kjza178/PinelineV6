"""DeepSeek — API chuẩn OpenAI. Model dùng trong Pineline: `deepseek/deepseek-chat` (dịch, soát, agent).

Cấu hình (env > pineline.config.json):
  DEEPSEEK_API_KEY    bắt buộc
  DEEPSEEK_BASE_URL   mặc định https://api.deepseek.com
Ghi chú: `deepseek-reasoner` (chế độ suy luận) bỏ qua temperature — không gửi để tránh bị từ chối; chậm và đắt hơn,
không nên dùng cho dịch hàng loạt.
"""
from __future__ import annotations

from ._openai import Provider


def _adjust(payload: dict, model: str) -> None:
    if "reasoner" in model:
        payload.pop("temperature", None)


PROVIDER = Provider(
    name="deepseek",
    label="DeepSeek",
    default_base="https://api.deepseek.com",
    base_keys=("DEEPSEEK_BASE_URL",),
    key_keys=("DEEPSEEK_API_KEY",),
    default_model="deepseek-chat",
    models=("deepseek-chat", "deepseek-reasoner"),
    max_tokens_cap=8192,
    adjust=_adjust,
)
