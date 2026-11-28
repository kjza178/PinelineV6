"""Alibaba Cloud Model Studio (DashScope, chế độ tương thích OpenAI) — model Qwen: `alibaba/<tên model>`.
Tên đang có lấy từ `GET /models` (nút «Lấy danh sách model» trong Cài đặt AI) — không ghi cứng.

Cấu hình (env > pineline.config.json):
  DASHSCOPE_API_KEY   bắt buộc (nhận cả ALIBABA_API_KEY)
  ALIBABA_BASE_URL    mặc định máy chủ quốc tế https://dashscope-intl.aliyuncs.com/compatible-mode/v1
                      (tài khoản Trung Quốc đại lục: https://dashscope.aliyuncs.com/compatible-mode/v1)
  ALIBABA_ENABLE_THINKING  1 = bật chế độ suy nghĩ của Qwen3 (mặc định 0). Gọi không-stream thì phải TẮT,
                      nên Pineline gửi enable_thinking=false trừ khi bật khoá này.
"""
from __future__ import annotations

from ._openai import Provider, _cfg


def _adjust(payload: dict, model: str) -> None:
    payload["enable_thinking"] = _cfg("ALIBABA_ENABLE_THINKING", "0").lower() in ("1", "true", "yes", "on")


PROVIDER = Provider(
    name="alibaba",
    label="Alibaba (Qwen)",
    default_base="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    base_keys=("ALIBABA_BASE_URL",),
    key_keys=("DASHSCOPE_API_KEY", "ALIBABA_API_KEY"),
    max_tokens_cap=8192,
    adjust=_adjust,
)
