"""DeepSeek — API chuẩn OpenAI. Dùng: `deepseek/<tên model>` (dịch, soát, agent).

KHÔNG ghi cứng tên model: DeepSeek đổi/khai tử tên (`deepseek-chat`, `deepseek-reasoner` ngừng sau 24/07/2026).
Tên đang có lấy từ `GET /models` bằng key của bạn — nút «Lấy danh sách model» trong Cài đặt AI
(engines/providers/_openai.list_models); bộ cài sẵn «DeepSeek» chọn bản «flash» trong danh sách đó.

Cấu hình (env > pineline.config.json):
  DEEPSEEK_API_KEY    bắt buộc
  DEEPSEEK_BASE_URL   mặc định https://api.deepseek.com
Ghi chú: model suy luận (tên có «reasoner» / «thinking») bỏ qua temperature — không gửi để tránh bị từ chối; chậm và
đắt hơn, không nên dùng cho dịch hàng loạt.
"""
from __future__ import annotations

from ._openai import Provider


def _adjust(payload: dict, model: str) -> None:
    ml = model.lower()
    if "reasoner" in ml or "thinking" in ml:
        payload.pop("temperature", None)


PROVIDER = Provider(
    name="deepseek",
    label="DeepSeek",
    default_base="https://api.deepseek.com",
    base_keys=("DEEPSEEK_BASE_URL",),
    key_keys=("DEEPSEEK_API_KEY",),
    max_tokens_cap=8192,
    adjust=_adjust,
)
