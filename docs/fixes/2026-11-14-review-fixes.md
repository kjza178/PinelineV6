# 2026-11-14 — Sửa theo review PR #1: films.db dùng chung giữa instance + 5 điểm nhỏ

Mức kiểm: **C (cloud)** — test tái hiện + toàn bộ pytest. Cần restart :8806 (lần đầu chạy tự thêm cột `work_root`
vào films.db).

## 1. films.db bị các instance ghi đè nhau (lỗi do bản «work root theo instance» cùng ngày gây ra)
- **Vấn đề:** `film_db.sync()` quét work root của instance đang chạy, nhưng `data/films.db` dùng chung. Sync của v6
  đánh `work_exists=0` + event «work_removed» cho MỌI job của prod (và ngược lại); `purge-missing` của một instance xoá
  lịch sử của instance kia. Desktop gọi sync mỗi lần mở Thư viện phim nên lỗi lộ ngay.
- **Sửa (`core/film_db.py`):** cột mới `films.work_root` (tự migrate) ghi work root của instance đã thấy job.
  `sync()` chỉ đánh dấu «mất thư mục» và `purge_missing()` chỉ xoá các dòng thuộc work root hiện tại (`_owns`).
  Dòng cũ chưa có `work_root` được coi là của `ROOT/work` (trước bản này mọi instance đều quét `ROOT/work`).
- **Không đổi:** DB vẫn một file (giữ nguyên lịch sử, thống kê chung).
- **Test:** `tests/test_film_db_instances.py` (3 test: hai instance sync xen kẽ không làm mất job của nhau; instance chủ
  vẫn phát hiện job bị xoá; dòng cũ thuộc instance mặc định).

## 6. Ghi nhật ký hàng chờ trong khoá
- `_hist_push` (gọi khi đang giữ `_lock`) giờ chỉ đẩy vào hàng đợi bộ nhớ; luồng `queue-history` ghi file + đọc tiêu đề
  job. `queue_history()` chờ luồng ghi xong trước khi đọc. Job bị nhường ghế (R7.3) dọn luôn `opts_by_job`.
- **Test:** `test_history_write_does_not_block_queue_lock` (ghi chậm 5 s → `queue_state()` vẫn < 0,5 s).

## 7. Mở Thư viện phim luôn đồng bộ toàn bộ
- Desktop chỉ đồng bộ khi lần trước quá 2 phút; thêm nút «Đồng bộ» để ép.

## 8. `agent_feeder.STATE_PATH` tính lúc import
- `_state_path()` tính lúc gọi (sidecar import trước khi áp instance).

## 9. Nuốt lỗi im lặng trong code mới (CLAUDE.md)
- `core/job_queue._history_append` (xoay file), `app/main._versions` (ffmpeg), `app/main._disk_free`: nay ghi log.

## 10. Hàm dung lượng ổ trùng lặp
- `/api/health` dùng `engines.split_parts.free_bytes` thay bản tự viết (bỏ `total_gb`).

## Kèm: test ghi vào `data/films.db` thật
- `tests/conftest.py` đặt `FILM_DB` tạm ngay khi nạp (trước `import server` lúc thu thập test);
  `tests/v5/test_review_batches.py` truyền `FILM_DB` tạm cho script chạy tiến trình riêng.

## Chưa sửa (chờ chủ dự án — review cùng đợt)
2. Plan hợp lệ nhưng không có nhân vật bị coi là hỏng (`SIMPLE_REQUIRE_PLAN`). 3. Lỗi tạm ở máy (CUDA OOM, file bị khoá)
bị coi là lỗi code. 4. «Chạy tiếp» khi hàng chờ đang tạm dừng không chạy và không báo. 5. Tab Bản dịch tự nạp lại làm
mất chữ đang gõ.
