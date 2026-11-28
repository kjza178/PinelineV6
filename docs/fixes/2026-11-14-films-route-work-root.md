# 2026-11-14 — Trang Phim hỏng (route /films trùng) · module cứng ROOT/work

Mức kiểm: **C (cloud)** — tái hiện bằng backend thật + Chromium, có test. Cần restart :8806.

## 1. `GET /films` trả HTML cho cả lời gọi API → trang Phim cũ hỏng
- **Triệu chứng:** mở `/films` (trang thư viện phim) → console «Cannot read properties of undefined (reading 'series')»,
  danh sách trống.
- **Nguyên nhân:** `/films` khai báo ở `http_routes/routes_ui.py` (trả `films_ui.html`) và `http_routes/routes_films.py`
  (trả JSON danh sách). Router UI nạp trước nên thắng; chính `films_ui.html` gọi `fetch('/films?limit=…')` → nhận HTML.
- **Sửa:** `routes_ui.ui_films`: trình duyệt mở trang (không query, Accept HTML) → HTML; còn lại → JSON của
  `routes_films.list_films` (instance tắt router `films` → 404 JSON nêu lý do).

## 2. film_db / auto_publisher / feeder / meta_studio / batch_config cứng `ROOT/"work"`
- **Vấn đề:** chỉ đúng khi junction `ROOT/work` trỏ đúng work root của instance. Instance `prod` (work `Z:/PinelineV2/work`)
  sẽ đồng bộ thư viện phim / đăng YouTube / đọc cấu hình batch từ NHẦM cây; instance `test` ghi đè cấu hình batch thật
  (đã xảy ra khi chạy thử UI ở cloud).
- **Sửa:** `core.instance.work_root_now()` đọc `PINELINE_WORK_ROOT` / `WORK_DIR` (do `Instance.apply_env` đặt) LÚC GỌI;
  các module dùng nó (`film_db._work`, `auto_publisher._work_dir`, `agent_feeder._work`, `meta_studio._work_root`,
  `batch_config._config_path`). Giá trị bị test đặt riêng vẫn được tôn trọng.
- **Còn:** `agent_feeder.STATE_PATH`, `agent_autopilot.STATE_PATH` vẫn tính lúc import.

## Test
`tests/test_app_integration.py`: `test_films_route_serves_page_to_browser_and_json_to_fetch`,
`test_modules_follow_the_instance_work_root`, `test_health_reports_running_jobs_for_desktop_close_prompt`.
