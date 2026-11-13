# `tools.test_channel`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `tools/test_channel.py` · **Lớp:** `publish` · **1435 dòng** (1210 code) · **38 hàm**, **0 class** · sha1 `9b871769c0`

> test_channel.py — Quản lý Kênh Phụ Test (Burner Channel) độc lập trong Pineline. Chức năng: - Đăng nhập Google OAuth Kênh Phụ / Gmail Test riêng (tách biệt hoàn toàn với kênh Sage Review).

### Phụ thuộc

- **Gọi tới (import nội bộ):** [core.film_db](./core.film_db.md), [engines.check_block](./engines.check_block.md), [tools.lib_check](./tools.lib_check.md)
- **Được dùng bởi:** [http_routes.routes_films](./http_routes.routes_films.md)
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [agents.test_channel](./agents.test_channel.md)
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `google`, `google_auth_oauthlib`, `googleapiclient`

```mermaid
flowchart LR
  tools_test_channel[[tools.test_channel]]
  http_routes_routes_films[http_routes.routes_films] --> tools_test_channel
  tools_test_channel --> core_film_db[core.film_db]
  tools_test_channel --> engines_check_block[engines.check_block]
  tools_test_channel --> tools_lib_check[tools.lib_check]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_safe_resolve_title_vi` | 37 | 15 | `name, title_vi` | Gọi lib_check.resolve_title_vi an toàn, tự động reload nếu module bị cache cũ. |
| `_utc_now` | 97 | 2 | `` |  |
| `_utc_now_plus_minutes` | 101 | 2 | `minutes` |  |
| `_today_utc` | 105 | 2 | `` |  |
| `_quota_day` | 109 | 8 | `` | Ngày reset hạn mức YouTube Data API (Pacific), không dùng UTC/VN. |
| `_classify_yt_block_error` | 119 | 10 | `err_str` | Chỉ nhận diện lỗi cứng. CẤM khớp chữ 'quota'/'429' mơ hồ (false positive). |
| `get_channel_limit_state` | 138 | 11 | `` | Kiểm tra xem kênh YouTube đã bị dính uploadLimitExceeded hôm nay chưa. |
| `mark_channel_limit_reached` | 151 | 9 | `channel_title` | Đánh dấu Kênh YouTube chạm giới hạn upload hôm nay. |
| `get_projects_health` | 162 | 8 | `` | Đọc dữ liệu kiểm tra tính khả dụng của từng Project. |
| `update_project_health` | 172 | 35 | `project_id, api_enabled, channel_title, channel_id, email, enable_u…` | Cập nhật trạng thái API của Project. |
| `verify_all_projects_health` | 209 | 75 | `force` | Kiểm tra thực tế trạng thái YouTube API và kênh kết nối cho từng Project. |
| `_load_exhausted_raw` | 286 | 8 | `` |  |
| `_save_exhausted_raw` | 296 | 3 | `data` |  |
| `_normalize_exhausted_entry` | 301 | 24 | `val` | Chuẩn hoá entry → {day, reason, at} hoặc None nếu hết hạn / rác. |
| `get_exhausted_projects` | 327 | 20 | `` | Map project_id → day (string) cho tương thích chỗ cũ; đã purge stale. |
| `get_exhausted_meta` | 349 | 9 | `` | Map project_id → {day, reason, at} sau purge. |
| `mark_project_exhausted` | 360 | 7 | `project_id, reason` |  |
| `reset_exhausted_projects` | 369 | 14 | `project_id` | Reset cờ hết quota / khóa upload cho project cụ thể hoặc toàn bộ. |
| `list_projects` | 385 | 42 | `` |  |
| `get_today_uploads_count` | 433 | 65 | `force` | Đếm số video đã upload trong ngày trên Kênh Test qua YouTube Data API (cache 120s, tốn chỉ 1-2 quota units). |
| `get_status` | 500 | 27 | `` | Kiểm tra trạng thái kết nối Kênh Phụ Test và danh sách Projects. |
| `disconnect` | 529 | 11 | `` | Ngắt kết nối Kênh Phụ Test. |
| `get_auth_url` | 542 | 50 | `redirect_uri, project_id` | Tạo URL đăng nhập Google OAuth cho Project được chọn hoặc project chưa kết nối tiếp theo. |
| `finish_oauth` | 594 | 92 | `code, redirect_uri, project_id` | Hoàn tất đăng nhập OAuth và lưu token cho Project tương ứng. |
| `get_available_service` | 688 | 92 | `project_id, force` | Lấy YouTube service từ pool (hoặc đúng 1 project nếu truyền project_id). |
| `get_read_service` | 782 | 19 | `` | Lấy YouTube service từ bất kỳ project nào đã kết nối để đọc metadata (chỉ tốn 1 quota unit, không phụ thuộc quota upload). |
| `get_youtube_service` | 803 | 4 | `` | Lấy YouTube API service khả dụng. |
| `upload_test_sample` | 811 | 101 | `name, sample_path, title_vi, project_id` | Upload clip mẫu 90s Unlisted. Có project_id → chỉ dùng account đó (không xoay vòng đốt cờ các acc khác). |
| `delete_test_video` | 914 | 20 | `video_id` | Xoá video test trên YouTube sau khi đã kiểm tra xong. |
| `wait_and_check_test_video` | 936 | 84 | `video_id, name, max_wait_sec` | Chờ YouTube xử lý xong video và kiểm tra bản quyền Content ID chính xác. |
| `recheck_single` | 1024 | 77 | `name` | Kiểm tra bản quyền Content ID của 1 video đã upload lên Kênh Phụ. |
| `check_and_process_pending_rechecks` | 1103 | 26 | `force` | Quét và kiểm tra lại các video đang chờ (mặc định kiểm tra những video đã qua 15p). |
| `start_recheck_daemon` | 1133 | 18 | `` | Khởi động daemon định kỳ kiểm tra các video đến hạn 5 phút. |
| `run_full_test_for_season` | 1155 | 107 | `name, auto_delete, wait_for_result, title_vi, project_id` | Thực hiện quy trình test 1 bộ phim: |
| `_batch_worker` | 1264 | 116 | `names, auto_delete, title_vi_map` |  |
| `stop_batch_test` | 1382 | 8 | `` | Yêu cầu dừng Test All sau item hiện tại (không kill giữa upload). |
| `start_batch_test` | 1392 | 27 | `names, limit, auto_delete, title_vi_map` | Bắt đầu chạy Test All hàng loạt ngầm. |
| `get_batch_progress` | 1421 | 11 | `` | Lấy tiến độ Test All hiện tại và thống kê số video đang chờ 15p. |

### Config / env keys (2)

`OAUTHLIB_RELAX_TOKEN_SCOPE`, `TEST_YT_SECRET`

**File/artifact nhắc tới:** `channel.json`, `channel_limit.json`, `client_secret.json`, `code_verifier.txt`, `current_oauth_project.txt`, `exhausted.json`, `projects.json`, `projects_health.json`, `token.json`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=34 · file_writes=13 · global_stmt=4 · sleep=6 · swallow_except=15 · threads=2

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
