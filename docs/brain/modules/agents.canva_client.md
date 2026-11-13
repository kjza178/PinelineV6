# `agents.canva_client`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `agents/canva_client.py` · **Lớp:** `publish` · **394 dòng** (328 code) · **12 hàm**, **0 class** · sha1 `d9856e8e5b`

> canva_client.py — Tích hợp Canva Connect API chính thức cho Pineline. Quản lý OAuth2 (PKCE), Upload Assets, Tạo Design YouTube Thumbnail (16:9) và Autofill.

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** [agents.agent_tele_cmd](./agents.agent_tele_cmd.md), [http_routes.routes_uploader_settings](./http_routes.routes_uploader_settings.md)
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `requests`

```mermaid
flowchart LR
  agents_canva_client[[agents.canva_client]]
  agents_agent_tele_cmd[agents.agent_tele_cmd] --> agents_canva_client
  http_routes_routes_uploader_settings[http_routes.routes_uploader_settings] --> agents_canva_client
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `load_config` | 31 | 11 | `` |  |
| `save_config` | 44 | 8 | `client_id, client_secret, redirect_uri` |  |
| `load_token` | 54 | 13 | `` |  |
| `save_token` | 69 | 5 | `token_data` |  |
| `is_connected` | 76 | 3 | `` |  |
| `generate_auth_url` | 81 | 47 | `` | Tạo URL đăng nhập OAuth2 Canva kèm PKCE (code_verifier, code_challenge). |
| `exchange_code` | 130 | 35 | `code, state` | Đổi authorization code lấy access_token. |
| `refresh_access_token` | 167 | 22 | `refresh_token` | Tự động làm mới access_token bằng refresh_token. |
| `_get_auth_headers` | 191 | 7 | `` |  |
| `upload_asset` | 200 | 55 | `image_data_or_path, name` | Upload ảnh lên Canva Asset Library để sử dụng trong thiết kế. |
| `import_design_from_image` | 257 | 51 | `image_bytes, title` | Import ảnh trực tiếp vào Canva để tạo thiết kế có sẵn hình ảnh (không bị trang trắng). |
| `create_youtube_thumbnail` | 310 | 85 | `title, poster_source, thumb_image_source` | Tạo một thiết kế YouTube Thumbnail 16:9 mới trên Canva và trả về edit_url. |

### Config / env keys (3)

`CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, `CANVA_REDIRECT_URI`

**Cổng nhắc tới:** 8802

**File/artifact nhắc tới:** `canva_config.json`, `canva_pkce.json`, `canva_token.json`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=9 · file_writes=3 · sleep=2 · swallow_except=4

### Lịch sử (từ docs/)

- **Fix logs (1):**
  - [2026-11-11 — F4 (services + sidecar thuần) + F5 (render tiến trình con)](../../fixes/2026-11-11-f4-f5.md)
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
