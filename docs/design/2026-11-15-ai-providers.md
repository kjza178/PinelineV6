# Nhà cung cấp AI qua API — DeepSeek, Alibaba (Qwen)

_2026-11-15. Quyết định chủ dự án: V6 bỏ 9router; AI dùng Aurora, AI local, hoặc nhà cung cấp API (DeepSeek, Alibaba)._

## Cách dùng

Đặt model theo tiền tố ở các khoá model (`SIMPLE_MODEL`, `PLAN_MODEL`, `NAME_MODEL`, `PRONOUN_MODEL`, `AGENT_MODEL`…):

| Tiền tố | Đi tới | Khoá cần có |
|---|---|---|
| `deepseek/deepseek-chat` | DeepSeek | `DEEPSEEK_API_KEY` (+ `DEEPSEEK_BASE_URL`, mặc định `https://api.deepseek.com`) |
| `alibaba/qwen-plus` (hoặc `qwen/…`) | Alibaba Model Studio (DashScope, chế độ OpenAI) | `DASHSCOPE_API_KEY` (+ `ALIBABA_BASE_URL`, mặc định máy chủ quốc tế) |
| `chatgpt/…`, `aurora/…` | Aurora | như cũ |
| `local`, `llama/…` | llama-server :8083 | — |
| `local/<tên>` | Ollama | — |

## Cấu trúc

```
engines/providers/
  _openai.py    lõi chung: lấy key/URL lúc gọi, gửi chat/completions, thử lại 429/5xx/mất mạng, báo lỗi rõ
  deepseek.py   khai báo Provider DeepSeek (+ bỏ temperature cho deepseek-reasoner)
  alibaba.py    khai báo Provider Alibaba (+ enable_thinking=false, ALIBABA_ENABLE_THINKING=1 để bật)
  __init__.py   resolve("deepseek/…") → (Provider, model); đăng ký nhà cung cấp
```
`engines.llm.chat()` và Agent (`agents.agent_core._chat_turn_model`, có gọi công cụ) hỏi `providers.resolve()` trước mọi
đường khác. Thêm nhà cung cấp = một file `PROVIDER = Provider(...)` + một dòng trong `_REGISTRY`.

Lỗi: luôn `LLMUnavailable`, giữ `requests.HTTPError` ở `__cause__` → `cloud_retry` phân loại đúng: 429 = limit,
5xx / mất mạng = transient (hàng chờ tự thử lại), 401/403/404/400 = lỗi cấu hình (báo ngay, không thử lại).

## Gỡ 9router
- Mặc định `LLM_BASE_URL` = llama local `http://127.0.0.1:8083/v1` (trước: `192.168.31.247:20128`), `LLM_MODEL` / `AGENT_MODEL`
  mặc định `local` (trước `ollama/gemma4:31b-cloud`), `CLOUD_LLM_BASE_URL` mặc định trống.
- Model `ollama/…-cloud` chỉ chạy khi cấu hình rõ `CLOUD_LLM_BASE_URL` + `CLOUD_LLM_API_KEY`; không thì báo đổi sang
  deepseek/alibaba/chatgpt/local (không còn đoán địa chỉ LAN).
- **File `pineline.config.json` trên máy KHÔNG bị sửa** — chủ dự án tự đổi các khoá đang trỏ 9router.

## Chức năng KHÔNG chuyển được sang DeepSeek/Alibaba (cần Aurora)
- Lập plan `PLAN_MODE=file` (upload file phụ đề) → dùng `PLAN_MODE=sample`.
- Phân tích xưng hô cả phim (`PRONOUN_ANALYZE`, upload file).
- Meta Studio (tiêu đề / mô tả YouTube — cứng `gpt-5-6`) và ảnh thumbnail (`gpt-image-2`).

## Chưa kiểm
- Gọi API thật (cloud không có key): kiểm bằng máy chủ giả `tests/test_providers.py`.
- Tham số `enable_thinking` của DashScope: theo tài liệu chế độ tương thích OpenAI; cần thử 1 lần với key thật.
- Chất lượng dịch / xưng hô của DeepSeek, Qwen so với GPT: đo bằng `scripts/golden_ab.py` trước khi dùng cho hàng chờ.
