# `core.film_db`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `core/film_db.py` · **Lớp:** `core` · **1091 dòng** (953 code) · **42 hàm**, **0 class** · sha1 `96fa91c2e4`

> Database phim đã làm (SQLite) — nguồn sự thật lâu dài, không mất khi xoá thư mục job. films   : 1 dòng / job — tên gốc + tên Việt, series/mùa, file nguồn, trạng thái, thời lượng, output, config lồng tiếng, link YouTube (

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.hongguo_bridge](./agents.hongguo_bridge.md), [engines.check_block](./engines.check_block.md)
- **Được dùng bởi:** [agents.agent_core](./agents.agent_core.md), [agents.agent_feeder](./agents.agent_feeder.md), [agents.agent_tele_cmd](./agents.agent_tele_cmd.md), [agents.auto_publisher](./agents.auto_publisher.md), [agents.films_merge_api](./agents.films_merge_api.md), [agents.hongguo_bridge](./agents.hongguo_bridge.md), [agents.meta_studio](./agents.meta_studio.md), [agents.test_channel](./agents.test_channel.md), [engines.check_block](./engines.check_block.md), [engines.series_glossary](./engines.series_glossary.md), [engines.split_parts](./engines.split_parts.md), [http_routes.routes_films](./http_routes.routes_films.md), [http_routes.routes_hg](./http_routes.routes_hg.md), [http_routes.routes_uploader_settings](./http_routes.routes_uploader_settings.md), [services.film_db_sync](./services.film_db_sync.md), [tools.lib_check](./tools.lib_check.md), [tools.test_channel](./tools.test_channel.md)
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [restart_server](./restart_server.md)
- **Test:** `tests/v5/test_auto_publisher_dedup.py`, `tests/v5/test_film_db_queue_meta.py`, `tests/v5/test_queue_http_state.py`

```mermaid
flowchart LR
  core_film_db[[core.film_db]]
  agents_agent_core[agents.agent_core] --> core_film_db
  agents_agent_feeder[agents.agent_feeder] --> core_film_db
  agents_agent_tele_cmd[agents.agent_tele_cmd] --> core_film_db
  agents_auto_publisher[agents.auto_publisher] --> core_film_db
  agents_films_merge_api[agents.films_merge_api] --> core_film_db
  agents_hongguo_bridge[agents.hongguo_bridge] --> core_film_db
  agents_meta_studio[agents.meta_studio] --> core_film_db
  agents_test_channel[agents.test_channel] --> core_film_db
  engines_check_block[engines.check_block] --> core_film_db
  engines_series_glossary[engines.series_glossary] --> core_film_db
  engines_split_parts[engines.split_parts] --> core_film_db
  http_routes_routes_films[http_routes.routes_films] --> core_film_db
  http_routes_routes_hg[http_routes.routes_hg] --> core_film_db
  http_routes_routes_uploader_settings[http_routes.routes_uploader_settings] --> core_film_db
  services_film_db_sync[services.film_db_sync] --> core_film_db
  core_film_db --> agents_hongguo_bridge[agents.hongguo_bridge]
  core_film_db --> engines_check_block[engines.check_block]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_strip_part_suffix` | 33 | 2 | `title` |  |
| `is_junk_hg_name` | 37 | 15 | `name, job_id` | True nếu không nên dùng làm tên folder Lib / query /api/hg/poster. |
| `derive_hg_name` | 54 | 52 | `job_id, req, title_clean` | Tên folder Hongguo/Lib cho poster — không lấy job_id / downloads / part folder. |
| `_utc` | 209 | 2 | `` |  |
| `norm_path` | 213 | 2 | `p` |  |
| `connect` | 217 | 28 | `` |  |
| `log_event` | 247 | 6 | `job_id, kind, detail` |  |
| `_status_of` | 257 | 12 | `stages` |  |
| `_series_season` | 271 | 8 | `title, series_id` |  |
| `_num` | 281 | 5 | `v, cast` |  |
| `_is_upload_lock_active` | 288 | 20 | `job_id` | Kiểm tra nhanh xem .upload.lock của job còn thuộc về tiến trình đang sống không. |
| `row_from_manifest` | 310 | 39 | `job_id, m` |  |
| `sync_one` | 358 | 38 | `job_id` | Đồng bộ tức thì 1 job từ work/<job_id>/manifest.json vào films.db. |
| `sync` | 398 | 57 | `hg_library` | Quét manifest → upsert films; ghi event khi trạng thái đổi. Trả {new, changed, total}. |
| `needs_series_ids` | 457 | 4 | `` |  |
| `sync_uploads` | 463 | 82 | `` | Link YouTube từ Z:\yt_uploader\upload_history.jsonl và queue_state.json. |
| `source_keys` | 549 | 3 | `` |  |
| `series_ids` | 554 | 3 | `` |  |
| `update_block_status` | 559 | 18 | `job_id, status, reason` | Cập nhật trạng thái bản quyền (clean/blocked/claim/private/unknown) và lý do cho 1 phim. |
| `query` | 579 | 32 | `q, status, limit, order, work_exists, block_status` |  |
| `get` | 613 | 9 | `job_id` |  |
| `delete_film` | 624 | 19 | `job_id, delete_work` | Xoá 1 film khỏi database và tuỳ chọn xoá thư mục work trên ổ đĩa. |
| `delete_films` | 645 | 21 | `job_ids, delete_work` | Xoá nhiều film theo danh sách job_id. |
| `purge_missing` | 668 | 13 | `only_pending_failed` | Xoá toàn bộ hoặc các job chưa hoàn thành mà thư mục work đã bị xoá (work_exists = 0). |
| `stats` | 683 | 18 | `` |  |
| `to_csv` | 703 | 10 | `rows` |  |
| `_dur` | 715 | 5 | `sec` |  |
| `format_stats` | 722 | 12 | `` |  |
| `format_rows` | 736 | 12 | `rows` |  |
| `format_detail` | 750 | 18 | `job_id` |  |
| `upsert_library_block` | 772 | 64 | `name, core, title_vi, video_path, status, reason, test_yt_url, test…` | Lưu hoặc cập nhật trạng thái bản quyền phim trong Thư viện gốc. |
| `get_library_block` | 838 | 4 | `name` |  |
| `list_pending_rechecks` | 844 | 7 | `` | Lấy danh sách các bản ghi đang chờ recheck (status='testing'). |
| `list_library_blocks` | 853 | 5 | `` | Trả về dict name -> record của tất cả các bộ đã check trong Thư viện. |
| `delete_library_block` | 860 | 4 | `name` |  |
| `upsert_hg_watchlist` | 871 | 58 | `sid, title, name, core, season, eps, score, album, source, on_disk,…` | Ghi/ cập nhật 1 series Hongguo vào watchlist (chỉ meta, không video). |
| `upsert_hg_watchlist_many` | 931 | 24 | `items, source, on_disk, deleted` |  |
| `mark_hg_watchlist_deleted_by_names` | 957 | 15 | `names` | Đánh dấu xoá theo tên folder Lib (giữ sid/core). |
| `list_hg_watchlist` | 974 | 9 | `include_deleted` |  |
| `known_hg_sids` | 985 | 8 | `` | Mọi sid đã biết: films + watchlist. |
| `watchlist_cores` | 995 | 46 | `limit` | Franchise cores để quét mùa mới (watchlist + films.series). |
| `sync_library_blocks_from_yt_queue` | 1043 | 48 | `check_live` | Đồng bộ trạng thái bản quyền từ Z:\yt_uploader\queue_state.json cho các video D:\Lib. |

### Config / env keys (2)

`FILM_DB`, `YT_UPLOADER_DIR`

**File/artifact nhắc tới:** `.upload.lock`, `films.db`, `manifest.json`, `queue_state.json`, `upload_history.jsonl`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=19 · swallow_except=11

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
