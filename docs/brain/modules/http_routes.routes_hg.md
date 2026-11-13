# `http_routes.routes_hg`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `http_routes/routes_hg.py` · **Lớp:** `control` · **1018 dòng** (880 code) · **56 hàm**, **3 class** · sha1 `50fd06757d`

> Cầu nối Hongguo Downloader (local :8000): /api/hg/*. Tách từ server.py (đợt 7). Chỉ được nạp từ cuối server.py (sau khi mọi hàm/model lõi đã định nghĩa): truy cập server qua `S.<tên>` để patch `server.<tên>` trong test v

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.hg_ui_ctx](./agents.hg_ui_ctx.md), [agents.hongguo_bridge](./agents.hongguo_bridge.md), [agents.hongguo_license_ensure](./agents.hongguo_license_ensure.md), [agents.meta_studio](./agents.meta_studio.md), [core.film_db](./core.film_db.md), [engines.title_mt](./engines.title_mt.md), [server](./server.md)
- **Được dùng bởi:** [server](./server.md)
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [restart_server](./restart_server.md), [services.hongguo_flow](./services.hongguo_flow.md)
- **Test:** `tests/v5/test_server_split.py`
- **Thư viện ngoài:** `fastapi`, `pydantic`

```mermaid
flowchart LR
  http_routes_routes_hg[[http_routes.routes_hg]]
  server[server] --> http_routes_routes_hg
  http_routes_routes_hg --> agents_hg_ui_ctx[agents.hg_ui_ctx]
  http_routes_routes_hg --> agents_hongguo_bridge[agents.hongguo_bridge]
  http_routes_routes_hg --> agents_hongguo_license_ensure[agents.hongguo_license_ensure]
  http_routes_routes_hg --> agents_meta_studio[agents.meta_studio]
  http_routes_routes_hg --> core_film_db[core.film_db]
  http_routes_routes_hg --> engines_title_mt[engines.title_mt]
  http_routes_routes_hg --> server[server]
```

### API routes (58)

| Method | Path | Handler | Dòng |
|---|---|---|---:|
| GET | `/api/disk` | `api_disk` | 90 |
| GET | `/api/hg/baike-info` | `hg_baike_info` | 211 |
| GET | `/api/hg/baike-search` | `hg_baike_search` | 196 |
| POST | `/api/hg/cancel` | `hg_cancel` | 327 |
| GET | `/api/hg/catalog` | `hg_catalog` | 465 |
| POST | `/api/hg/downloads/cancel` | `hg_dl_cancel_one` | 349 |
| POST | `/api/hg/downloads/clear-finished` | `hg_dl_clear_finished` | 338 |
| POST | `/api/hg/downloads/open-folder` | `hg_dl_open_folder` | 395 |
| POST | `/api/hg/downloads/remove` | `hg_dl_remove` | 365 |
| POST | `/api/hg/downloads/retry` | `hg_dl_retry` | 380 |
| GET | `/api/hg/feed` | `hg_feed` | 483 |
| GET | `/api/hg/genres` | `hg_genres` | 454 |
| GET | `/api/hg/img` | `hg_img` | 680 |
| GET | `/api/hg/library` | `hg_library` | 557 |
| POST | `/api/hg/library/delete` | `hg_library_delete` | 769 |
| POST | `/api/hg/library/open` | `hg_library_open` | 759 |
| GET|HEAD | `/api/hg/library/preview` | `hg_library_preview` | 655 |
| POST | `/api/hg/library/seasons/decline` | `hg_seasons_decline` | 807 |
| POST | `/api/hg/library/seasons/discover` | `hg_seasons_discover` | 820 |
| POST | `/api/hg/library/update` | `hg_library_update` | 794 |
| POST | `/api/hg/library/update-info` | `hg_library_update_info` | 782 |
| GET | `/api/hg/library/watchlist` | `hg_library_watchlist` | 840 |
| DELETE | `/api/hg/license/key` | `hg_license_key_clear` | 990 |
| POST | `/api/hg/license/key` | `hg_license_key` | 974 |
| POST | `/api/hg/license/prove` | `hg_license_prove` | 1001 |
| GET | `/api/hg/license/status` | `hg_license_status` | 962 |
| POST | `/api/hg/merge` | `hg_merge` | 735 |
| GET | `/api/hg/merge-queue` | `hg_merge_queue` | 747 |
| POST | `/api/hg/merge-queue/clear` | `hg_merge_queue_clear` | 753 |
| GET | `/api/hg/ping` | `hg_ping` | 128 |
| GET|HEAD | `/api/hg/playback/stream` | `hg_playback_stream` | 639 |
| GET | `/api/hg/poster` | `hg_poster` | 701 |
| GET | `/api/hg/preview` | `hg_preview` | 568 |
| GET | `/api/hg/preview/job` | `hg_preview_job` | 627 |
| POST | `/api/hg/preview/start` | `hg_preview_start` | 606 |
| GET | `/api/hg/rank` | `hg_rank` | 423 |
| POST | `/api/hg/rank-episodes` | `hg_rank_episodes` | 529 |
| POST | `/api/hg/resolve` | `hg_resolve` | 179 |
| GET | `/api/hg/search` | `hg_search` | 166 |
| GET | `/api/hg/series/{series_id}` | `hg_series` | 281 |
| GET | `/api/hg/series/{series_id}/qualities` | `hg_series_qualities` | 304 |
| GET | `/api/hg/series/{series_id}/seasons` | `hg_series_seasons` | 292 |
| GET | `/api/hg/settings` | `hg_settings` | 136 |
| POST | `/api/hg/settings` | `hg_settings_update` | 148 |
| GET | `/api/hg/status` | `hg_status` | 316 |
| POST | `/api/hg/submit` | `hg_submit` | 407 |
| POST | `/api/hg/translate-titles` | `hg_translate_titles` | 545 |
| GET | `/api/hg/web-search` | `hg_web_search` | 258 |
| POST | `/api/meta/generate` | `meta_generate` | 858 |
| POST | `/api/meta/generate-title` | `meta_generate_title` | 877 |
| GET | `/api/meta/series-info` | `meta_series_info` | 946 |
| GET | `/api/meta/thumbnail` | `meta_thumbnail_get` | 937 |
| POST | `/api/meta/thumbnail` | `meta_thumbnail` | 907 |
| POST | `/meta/generate` | `meta_generate` | 858 |
| POST | `/meta/generate-title` | `meta_generate_title` | 877 |
| GET | `/meta/series-info` | `meta_series_info` | 946 |
| GET | `/meta/thumbnail` | `meta_thumbnail_get` | 937 |
| POST | `/meta/thumbnail` | `meta_thumbnail` | 907 |

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| **class `HgRankEpsIn`** | 523 | 0 method | |  |
| **class `HgTranslateTitlesIn`** | 540 | 0 method | |  |
| **class `HgPreviewStartIn`** | 598 | 0 method | |  |
| async `_bind_hg_ui_backend` | 19 | 18 | `request` | Backend theo namespace (middleware gắn request.state.hg_ui) — fallback header. |
| `_hg_base` | 72 | 4 | `override` | Luôn DEFAULT_BASE: `base` từ client từng cho server gửi token Hongguo tới URL bất kỳ. |
| `_hg_err` | 78 | 9 | `e` |  |
| `api_disk` | 90 | 35 | `base` | Dung lượng ổ thư viện Hongguo (sidebar hongguo2). |
| `hg_ping` | 128 | 5 | `base` |  |
| `hg_settings` | 136 | 9 | `base` | Hongguo 2.x settings (saveFolder, quality…) — cùng helper WinUI. |
| `hg_settings_update` | 148 | 15 | `body, base` | Patch settings (quality, saveFolder, merge…) — RPC settings.update. |
| `hg_search` | 166 | 10 | `q, base, expand` |  |
| `hg_resolve` | 179 | 14 | `body, base` | Dán link Hongguo / series_id → meta (khi search không ra, vd. 第十一季). |
| `hg_baike_search` | 196 | 12 | `q, base` | Nhận diện trên duanjubaike → danh sách version → match Hongguo từng cái. |
| `hg_baike_info` | 211 | 44 | `series_id, title, translate` | Chi tiết sâu từ duanjubaike (intro/tags/score/同系列). |
| `hg_web_search` | 258 | 20 | `q` | Scrape hongguoduanju.com/search — dùng khi API Downloader sót mùa. |
| `hg_series` | 281 | 8 | `series_id, base` |  |
| `hg_series_seasons` | 292 | 9 | `series_id, base` | Franchise seasons (S1…Sn) — RPC series.seasons. |
| `hg_series_qualities` | 304 | 9 | `series_id, base` | Quality ladder + bitrate — RPC series.qualities. |
| `hg_status` | 316 | 8 | `base` |  |
| `hg_cancel` | 327 | 8 | `body` |  |
| `hg_dl_clear_finished` | 338 | 8 | `body` |  |
| `hg_dl_cancel_one` | 349 | 13 | `body` | Stop 1 download đang chạy/chờ. |
| `hg_dl_remove` | 365 | 12 | `body` |  |
| `hg_dl_retry` | 380 | 12 | `body` |  |
| `hg_dl_open_folder` | 395 | 9 | `body` |  |
| `hg_submit` | 407 | 13 | `body` |  |
| `hg_rank` | 423 | 28 | `category, board, chart, offset, size, base` | Bảng hot / Browse feed / Catalog. |
| `hg_genres` | 454 | 8 | `limit` |  |
| `hg_catalog` | 465 | 15 | `genre, sort, status, q, page, size` |  |
| `hg_feed` | 483 | 38 | `offset, size, panel, session_id, genre, category_dim_theme, categor…` | Hongguo 2.x Browse = feed.page + selector panel (WinUI facets). |
| `hg_rank_episodes` | 529 | 9 | `body` | Lazy fill episode_cnt cho series trên bảng hot. |
| `hg_translate_titles` | 545 | 9 | `body` | Dịch thô title Trung→Việt (Google gtx + cache) — chỉ để duyệt Hongguo. |
| `hg_library` | 557 | 8 | `base` |  |
| `hg_preview` | 568 | 28 | `series_id, ep, fresh, quality, base` | Xem thử 1 tập — :8000 /dl/preview hoặc RPC playback.prepare → stream local. |
| `hg_preview_start` | 606 | 18 | `body` | Bắt đầu prepare nền (progress poll qua /api/hg/preview/job). |
| `hg_preview_job` | 627 | 9 | `ticket` | Trạng thái job preview async (progress 0..1). |
| `hg_playback_stream` | 639 | 13 | `session` | Stream MP4 đã prepare bởi helper (thư mục hongguo-player). |
| `hg_library_preview` | 655 | 22 | `name, base` | Stream video thư viện Hongguo (merged hoặc tập đầu) — tua được (Range). |
| `hg_img` | 680 | 18 | `url, u, base` | Proxy ảnh cover qua Hongguo /img (HEIC → JPEG) — kèm UI token. |
| `hg_poster` | 701 | 31 | `name, base` | Poster thư viện: /dl/poster (auth) hoặc file local poster.jpg. |
| `hg_merge` | 735 | 9 | `body` | Enqueue merge tuần tự (Hongguo chỉ chạy 1 merge / lúc). |
| `hg_merge_queue` | 747 | 3 | `base` |  |
| `hg_merge_queue_clear` | 753 | 3 | `` |  |
| `hg_library_open` | 759 | 7 | `body` |  |
| `hg_library_delete` | 769 | 10 | `body` |  |
| `hg_library_update_info` | 782 | 9 | `base` | WinUI «Update info» — library.updateInfo. |
| `hg_library_update` | 794 | 10 | `body` |  |
| `hg_seasons_decline` | 807 | 10 | `body` |  |
| `hg_seasons_discover` | 820 | 17 | `body` | Chỉ tìm mùa mới theo watchlist+films+lib — không tải / không gọi Update all. |
| `hg_library_watchlist` | 840 | 14 | `include_deleted` | Danh sách meta watchlist (sau xoá Lib vẫn còn) — không tải. |
| `meta_generate` | 858 | 15 | `body` |  |
| `meta_generate_title` | 877 | 26 | `body` |  |
| `meta_thumbnail` | 907 | 26 | `body` |  |
| `meta_thumbnail_get` | 937 | 5 | `file` |  |
| `meta_series_info` | 946 | 13 | `job_id` |  |
| `hg_license_status` | 962 | 9 | `` | Safe license status for Hongguo 2.x UI — never returns key/proof. |
| `hg_license_key` | 974 | 13 | `body` |  |
| `hg_license_key_clear` | 990 | 8 | `` |  |
| `hg_license_prove` | 1001 | 15 | `body` |  |

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=53 · swallow_except=3

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
