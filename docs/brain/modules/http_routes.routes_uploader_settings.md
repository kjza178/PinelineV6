# `http_routes.routes_uploader_settings`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `http_routes/routes_uploader_settings.py` · **Lớp:** `control` · **659 dòng** (530 code) · **29 hàm**, **0 class** · sha1 `5bc83071c5`

> routes_uploader_settings.py — API Router cho Quản lý Cài đặt (Settings) & YouTube Uploader. Tích hợp trực tiếp vào Pineline V4 Server (:8804): 1. Settings:

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.auto_publisher](./agents.auto_publisher.md), [agents.canva_client](./agents.canva_client.md), [agents.hongguo_bridge](./agents.hongguo_bridge.md), [agents.meta_studio](./agents.meta_studio.md), [agents.uploader_settings_api](./agents.uploader_settings_api.md), [core.config](./core.config.md), [core.film_db](./core.film_db.md), [engines.llm](./engines.llm.md), [tools.lib_check](./tools.lib_check.md)
- **Được dùng bởi:** —
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [restart_server](./restart_server.md)
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `fastapi`, `psutil`

```mermaid
flowchart LR
  http_routes_routes_uploader_settings[[http_routes.routes_uploader_settings]]
  http_routes_routes_uploader_settings --> agents_auto_publisher[agents.auto_publisher]
  http_routes_routes_uploader_settings --> agents_canva_client[agents.canva_client]
  http_routes_routes_uploader_settings --> agents_hongguo_bridge[agents.hongguo_bridge]
  http_routes_routes_uploader_settings --> agents_meta_studio[agents.meta_studio]
  http_routes_routes_uploader_settings --> agents_uploader_settings_api[agents.uploader_settings_api]
  http_routes_routes_uploader_settings --> core_config[core.config]
  http_routes_routes_uploader_settings --> core_film_db[core.film_db]
  http_routes_routes_uploader_settings --> engines_llm[engines.llm]
  http_routes_routes_uploader_settings --> tools_lib_check[tools.lib_check]
```

### API routes (36)

| Method | Path | Handler | Dòng |
|---|---|---|---:|
| GET | `/api/auto-publish/config` | `auto_publish_get_config` | 473 |
| POST | `/api/auto-publish/config` | `auto_publish_save_config` | 480 |
| POST | `/api/auto-publish/scan` | `auto_publish_scan` | 516 |
| GET | `/api/auto-publish/state` | `auto_publish_status` | 528 |
| GET | `/api/auto-publish/status` | `auto_publish_status` | 528 |
| POST | `/api/auto-publish/trigger` | `auto_publish_trigger` | 488 |
| GET | `/api/canva/auth-url` | `canva_auth_url` | 552 |
| GET | `/api/canva/callback` | `canva_callback` | 563 |
| POST | `/api/canva/config` | `canva_save_config` | 591 |
| POST | `/api/canva/create` | `canva_create` | 603 |
| GET | `/api/canva/status` | `canva_status` | 539 |
| GET | `/api/settings` | `get_all_settings` | 47 |
| POST | `/api/settings` | `update_settings` | 108 |
| POST | `/api/settings/test-llm` | `test_llm_conn` | 191 |
| POST | `/api/settings/test-telegram` | `test_telegram_msg` | 155 |
| POST | `/api/uploader/clear-done` | `clear_done_uploads` | 399 |
| POST | `/api/uploader/clear-skipped` | `uploader_clear_skipped` | 450 |
| POST | `/api/uploader/enqueue` | `enqueue_upload` | 330 |
| POST | `/api/uploader/launch` | `launch_yt_uploader_process` | 409 |
| POST | `/api/uploader/move` | `uploader_move_item` | 456 |
| POST | `/api/uploader/remove` | `remove_upload_item` | 377 |
| POST | `/api/uploader/settings` | `uploader_save_settings` | 462 |
| POST | `/api/uploader/skip` | `uploader_skip_item` | 438 |
| POST | `/api/uploader/skip-current` | `uploader_skip_current` | 444 |
| GET | `/api/uploader/status` | `get_uploader_status` | 294 |
| GET | `/auto-publish/config` | `auto_publish_get_config` | 473 |
| POST | `/auto-publish/config` | `auto_publish_save_config` | 480 |
| POST | `/auto-publish/scan` | `auto_publish_scan` | 516 |
| GET | `/auto-publish/state` | `auto_publish_status` | 528 |
| GET | `/auto-publish/status` | `auto_publish_status` | 528 |
| POST | `/auto-publish/trigger` | `auto_publish_trigger` | 488 |
| GET | `/canva/auth-url` | `canva_auth_url` | 552 |
| GET | `/canva/callback` | `canva_callback` | 563 |
| POST | `/canva/config` | `canva_save_config` | 591 |
| POST | `/canva/create` | `canva_create` | 603 |
| GET | `/canva/status` | `canva_status` | 539 |

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `get_all_settings` | 47 | 58 | `` | Lấy toàn bộ cấu hình hiện tại của Pineline V4. |
| `update_settings` | 108 | 44 | `payload` | Lưu cài đặt mới vào pineline.config.json và đồng bộ biến môi trường. |
| `test_telegram_msg` | 155 | 33 | `payload` | Thử gửi 1 tin nhắn test đến Telegram để kiểm tra bot token và chat ID. |
| `test_llm_conn` | 191 | 38 | `payload` | Thử gửi 1 prompt nhỏ đến LLM để đo độ trễ và kiểm tra model. |
| `_is_yt_uploader_running` | 235 | 16 | `` | Kiểm tra xem tiến trình yt_uploader.py có đang chạy không. |
| `_read_upload_history` | 253 | 17 | `limit` | Đọc lịch sử upload từ Z:\yt_uploader\upload_history.jsonl. |
| `_read_upload_queue` | 272 | 9 | `` | Đọc hàng đợi upload từ Z:\yt_uploader\queue_state.json. |
| `_write_upload_queue` | 283 | 8 | `items` | Ghi an toàn vào Z:\yt_uploader\queue_state.json. |
| `get_uploader_status` | 294 | 33 | `` | Lấy trạng thái tổng thể của YouTube Uploader. |
| `enqueue_upload` | 330 | 44 | `payload` | Thêm một hoặc nhiều video vào hàng chờ upload của yt_uploader. |
| `remove_upload_item` | 377 | 19 | `payload` | Xóa một item khỏi hàng chờ upload. |
| `clear_done_uploads` | 399 | 7 | `` | Xóa tất cả các video đã hoàn thành (xong/lỗi) khỏi hàng chờ. |
| `launch_yt_uploader_process` | 409 | 19 | `` | Khởi động tiến trình yt_uploader.py chạy nền nếu đang tắt. |
| `_usa_json` | 430 | 5 | `fn, *args, **kwargs` | Bridge sang uploader_settings_api (sidecar-compatible). |
| `uploader_skip_item` | 438 | 3 | `payload` |  |
| `uploader_skip_current` | 444 | 3 | `` |  |
| `uploader_clear_skipped` | 450 | 3 | `` |  |
| `uploader_move_item` | 456 | 3 | `payload` |  |
| `uploader_save_settings` | 462 | 3 | `payload` |  |
| `auto_publish_get_config` | 473 | 3 | `` |  |
| `auto_publish_save_config` | 480 | 4 | `body` |  |
| `auto_publish_trigger` | 488 | 24 | `body` |  |
| `auto_publish_scan` | 516 | 6 | `` |  |
| `auto_publish_status` | 528 | 3 | `` |  |
| `canva_status` | 539 | 9 | `` |  |
| `canva_auth_url` | 552 | 7 | `` |  |
| `canva_callback` | 563 | 24 | `code, state` |  |
| `canva_save_config` | 591 | 8 | `body` |  |
| `canva_create` | 603 | 56 | `body` |  |

### Config / env keys (6)

`AGENT_TELEGRAM`, `TELEGRAM_ALLOWED_USERS`, `TELEGRAM_BOT_TOKEN`, `TEMP`, `WORK_DIR`, `YT_UPLOADER_DIR`

**Cổng nhắc tới:** 8083

**File/artifact nhắc tới:** `queue_state.json`, `settings.json`, `upload_history.jsonl`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=18 · file_writes=1 · sleep=1 · subprocess=1 · swallow_except=8

### Lịch sử (từ docs/)

- Fix logs: —
- Nhắc trong CHANGELOG: **0** lần · DECISIONS: **0** lần

<!-- AUTO:END -->

<!-- MANUAL:START -->
## Vai trò (viết tay)

_Chưa viết. 2–4 câu: module này chịu trách nhiệm gì trong pipeline, ai gọi nó, nó KHÔNG làm gì._

## Luồng chính

_Các hàm vào (entry) → bước chính → output/side effect. Ghi tên hàm thật._

## Bất biến & hợp đồng

_Điều luôn phải đúng (vd: chỉ ghi manifest qua `Manifest.update`; 1 job / ghế). Vi phạm = bug._

## Bẫy đã biết

_Lỗi từng gặp + link `docs/fixes/...`. Những chỗ dễ sửa sai._

## Ghi chú vận hành

_Config key quan trọng, cần restart gì khi sửa (:8801 / :8802 / hot-reload), cách test._
<!-- MANUAL:END -->
