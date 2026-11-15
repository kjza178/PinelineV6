# Nhà cung cấp AI qua API — DeepSeek, Alibaba (Qwen)

_2026-11-15. Quyết định chủ dự án: V6 bỏ 9router; AI dùng Aurora, AI local, hoặc nhà cung cấp API (DeepSeek, Alibaba)._

## Cách dùng

Đặt model theo tiền tố ở các khoá model (`SIMPLE_MODEL`, `PLAN_MODEL`, `NAME_MODEL`, `PRONOUN_MODEL`, `AGENT_MODEL`…):

| Tiền tố | Đi tới | Khoá cần có |
|---|---|---|
| `deepseek/<tên model>` | DeepSeek | `DEEPSEEK_API_KEY` (+ `DEEPSEEK_BASE_URL`, mặc định `https://api.deepseek.com`) |
| `alibaba/<tên model>` (hoặc `qwen/…`) | Alibaba Model Studio (DashScope, chế độ OpenAI) | `DASHSCOPE_API_KEY` (+ `ALIBABA_BASE_URL`, mặc định máy chủ quốc tế) |
| `chatgpt/…`, `aurora/…` | Aurora | như cũ |
| `local`, `llama/…` | llama-server :8083 | — |
| `local/<tên>` | Ollama | — |

## Tên model: không ghi cứng (sửa 2026-11-15)
Nhà cung cấp đổi và khai tử tên model — DeepSeek thông báo `deepseek-chat` / `deepseek-reasoner` ngừng hẳn sau
24/07/2026. Vì vậy code **không có tên model mặc định** cho DeepSeek/Alibaba:
- Tên lấy từ nhà cung cấp: `providers.list_models(p)` gọi `GET {base}/models` bằng key (chuẩn OpenAI).
  Desktop «Cài đặt AI» → dòng nhà cung cấp → nút **«Lấy danh sách model»** (`POST /api/v6/ai/models`; key vừa dán chưa
  lưu cũng hỏi được, không bị lưu). Danh sách nạp vào ô gợi ý của mọi chức năng; nhớ trong bộ nhớ server tới khi restart.
- Bộ cài sẵn «DeepSeek» / «Alibaba»: hỏi `/models` bằng key ĐÃ LƯU rồi `providers.pick_model()` chọn: bỏ model ảnh /
  âm thanh / embedding / coder / suy luận (reasoner, thinking); ưu tiên «flash» > «plus» > «chat» > «turbo» > «max» >
  «pro»; bản không ngày trước bản snapshot có ngày. Chưa có key / lỗi mạng → báo lỗi, không ghi gì.
- `deepseek/` thiếu tên → từ chối khi lưu và báo «chưa chọn tên model» khi gọi (không đoán tên).

## Cấu trúc

```
engines/providers/
  _openai.py    lõi chung: lấy key/URL lúc gọi, gửi chat/completions, thử lại 429/5xx/mất mạng, báo lỗi rõ;
                list_models (GET /models), pick_model (chọn bản «flash»)
  deepseek.py   khai báo Provider DeepSeek (+ bỏ temperature cho model suy luận: tên có reasoner/thinking)
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
- `GET /models` thật của DeepSeek và DashScope (chế độ tương thích OpenAI): định dạng `{"data":[{"id":…}]}` theo chuẩn
  OpenAI; kiểm bằng máy chủ giả. Thử 1 lần bằng nút «Lấy danh sách model» với key thật.
- Gọi API thật (cloud không có key): kiểm bằng máy chủ giả `tests/test_providers.py`.
- Tham số `enable_thinking` của DashScope: theo tài liệu chế độ tương thích OpenAI; cần thử 1 lần với key thật.
- Chất lượng dịch / xưng hô của DeepSeek, Qwen so với GPT: đo bằng `scripts/golden_ab.py` trước khi dùng cho hàng chờ.
