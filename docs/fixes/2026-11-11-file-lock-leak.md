# 2026-11-11 — core/fileio.file_lock: khoá rò → mất loại trừ / TimeoutError

## Triệu chứng (run_tests.bat all, check_batch3/4)
- 3 tiến trình × 40 lần tăng trong khoá ≠ 120; 10 luồng `update_state` → `TimeoutError: không lấy được khoá`.
- `[manifest] không lấy được khoá manifest.json … — ghi không khoá` (manifest rơi về ghi KHÔNG khoá).

## Nguyên nhân
Khoá kiểu "file .lock tồn tại = đang khoá" (O_EXCL) + bên chờ mở file đọc PID mỗi 50 ms. Trên Windows,
người giữ khoá `unlink()` thất bại khi bên khác đang mở file (lỗi bị `suppress`) → file .lock ở lại →
mọi người chờ tới stale 60 s / timeout. Manifest dùng timeout 5 s rồi ghi KHÔNG khoá → mất loại trừ.
**fileio.py giống hệt ở V2/V5/V6 (md5 924b9ffc…) → bug có sẵn ở V2 production.**

## Sửa (chỉ V6)
Khoá byte của hệ điều hành: `msvcrt.locking` (Windows) / `flock` (POSIX) trên `<path>.lock`.
Tiến trình chết → OS tự nhả (không cần đoán PID/stale). Windows: xoá file sau khi nhả (chỉ thành công khi
không ai mở → an toàn). POSIX: không xoá (tránh 2 inode). Chữ ký giữ nguyên.
Test: tests/test_file_lock.py (3 tiến trình × 40 = 120; 10 luồng không ai chồng nhau; file sót không chặn; timeout).
Backup: .backup_edit_20261111_filelock/.

## V2
CHƯA sửa. Không được thay giữa chừng: bản cũ (server đang chạy) + bản mới (subprocess mới) KHÔNG loại trừ
nhau. Chỉ áp khi restart toàn bộ V2 lúc hàng chờ rảnh, có đồng ý.

## Recheck
- Container: 48/48 Pass (×3).
- Windows: chờ run_tests.bat + run_tests.bat all (check_batch3/4 phải Pass).

## Mốc V5 (scripts/baseline_v5_tests.bat, 08:51): 18 failed / 175 passed — chạy trên CHÍNH cây V5
Cả 18 đều có trong danh sách fail của V6 hoặc đã xếp loại → **không có fail nào do V6 gây ra**:
film_db ×2, layout ×4, pronoun ×3, queue_http_state, restart_guard, check_batch2/3/4/5, translate_force ×3.
check_batch3/4 = bug khoá file ở trên (có sẵn ở V5/V2). Riêng V6 có thêm `test_skip_if_already_uploaded`
(flaky: luồng drain nền của test trước giành job) → fixture chặn drain nền trong tests/v5/conftest.py.
