# 2026-11-13 — Review V6: SSE treo server, mốc thời gian lẫn kiểu, test ghi data thật, .bat LF

Sửa trên cloud (Linux), nhánh `claude/elegant-shannon-oz6jos`. **Chưa chạy trên Windows** — cần `verify_all.bat` + restart :8806.

## 1. SSE `/api/events` làm đứng server
- **Triệu chứng:** mở 45 kết nối `/api/events` → `/api/health`, `/api/jobs` timeout.
- **Nguyên nhân:** generator đồng bộ (`bus.sse_lines`) → Starlette chạy trong threadpool (40 thread, dùng chung mọi
  route `def` của `server.py`); mỗi client giữ 1 thread suốt kết nối, client đã ngắt vẫn giữ tới hết keepalive 15 s.
- **Sửa:** `events/bus.py` thêm `AsyncSubscription`, `subscribe_async`, `sse_stream` (asyncio.Queue, publisher đẩy qua
  `loop.call_soon_threadsafe`); `app/main.py` route dùng generator async. `sse_lines` giữ cho script/test.
- **Recheck:** Pass — 100 kết nối SSE, `/api/health` 3 ms; ngắt hết → `sse_clients` về 0. Test `tests/test_events_bus.py` (3 test mới).

## 2. `started_at` / `finished_at` lẫn ISO và epoch
- **Triệu chứng:** `core.manifest` ghi ISO UTC, còn `state.job_state.mark_*`, heal `server.py`, `render_job` ghi
  `time.time()`. `film_db` (cột TEXT) cắt `[:10]` làm ngày, so `finished_at >= ?`, sắp «gần đây» → job được heal có
  ngày «1794362012»; `agent_feeder.process_ratio` bỏ các job đó; ETA ở `server.py` `float(started_at)` luôn lỗi với ISO.
- **Sửa:** `core/timeutil.py` (`now_iso`, `to_epoch`, `to_iso` — đọc mọi dạng cũ). Ghi: ISO UTC ở job_state / heal /
  render_job. Đọc: `film_db._row_from_manifest` qua `to_iso`, feeder + ETA qua `to_epoch`. `updated_at` stage GIỮ epoch.
  `job_state.mark_running`: lượt chạy mới đặt lại `started_at`, bỏ `finished_at` cũ (trước: `setdefault`).
- **Recheck:** Pass — `tests/test_timeutil.py`. Hàng cũ trong films.db tự đúng lại khi `film_db.sync()` đọc lại manifest.

## 3. pytest ghi vào `data/` thật; rác test đã bị commit
- **Nguyên nhân:** `agents/auto_publisher.py` tính `STATE_FILE`/`PUBLISH_QUEUE_FILE`/khoá lúc import → test đổi `DATA_DIR`
  không có tác dụng; `_revert_job_to_done` ghi thẳng `film_db.DB_PATH` thật.
- **Sửa:** `_data_path()` — theo `DATA_DIR` hiện tại; `tests/conftest.py` fixture autouse trỏ `film_db.DB_PATH` sang DB tạm;
  `is_pid_alive` chạy được ngoài Windows. Bỏ theo dõi git 2 file JSON runtime (đang chứa `job2`, `test_job_1`).
- **Recheck:** Pass — chạy cả bộ test, `git status data/` sạch; `test_scanner_skips_uploading_jobs` pass.
- **Lưu ý khi pull trên Windows:** git sẽ xoá 2 file JSON đó ở máy (auto-publish tự tạo lại; hàng chờ cũ chỉ có rác test).
  `data/films.db` vẫn còn trong git — chưa đụng (gỡ thì làm tại máy: `git rm --cached data/films.db`).

## 4. `.bat` xuống dòng LF
- **Sửa:** `.gitattributes`: `*.bat` / `*.cmd` `text eol=crlf`. **Recheck:** Pass — `test_bat_exists_crlf_ascii`.

## Còn lại (không sửa đợt này)
- `pineline.config.json` có API key thật vẫn nằm trong git + lịch sử: đổi key trên 9router, rồi tại máy
  `git rm --cached pineline.config.json` (KHÔNG gỡ từ xa — pull sẽ xoá file config ở máy).
- Test fail còn lại trên Linux: `test_kill_wrapper_after_child`, `check_batch4` (cần Windows/msvcrt), `check_batch3`
  mục «gỡ khoá bỏ quên» (chưa tìm nguyên nhân).
