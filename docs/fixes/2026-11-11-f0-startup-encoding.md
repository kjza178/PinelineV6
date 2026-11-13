# 2026-11-11 — F0: backend không lên + 1 test lỗi encoding

## Triệu chứng
- Tauri kẹt "Đang khởi động backend…"; scratch/desktop_backend.log: `PydanticUndefinedAnnotation: name 'Request' is not defined`.
- pytest Windows: 30/31, `test_index_load_all` → UnicodeEncodeError cp1252; cuối log `PermissionError D:\Temp\pytest-of-MyPC\pytest-current`.

## Nguyên nhân
- app/main.py có `from __future__ import annotations` nhưng import FastAPI/Request bên trong `create_app` → FastAPI không resolve được annotation dạng chuỗi.
- Test ghi chuỗi tiếng Việt bằng `write_text()` không encoding → Windows mặc định cp1252.
- pytest tạo symlink `pytest-current` trong D:\Temp → WinError 5 (chỉ ở bước dọn, không ảnh hưởng kết quả).

## Sửa
- Import fastapi ở đầu module app/main.py.
- Test: `encoding="utf-8"`.
- run_v6.bat / run_tests.bat: `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`; run_tests dùng `--basetemp=scratch\pytest_tmp -p no:cacheprovider`.
- desktop main.rs: spawn `python -X utf8 -m app …` (log tiếng Việt không vỡ trên console cp1252).
- Backup: .backup_edit_20261111_f0fix/ (main.rs không backup — chỉ thêm "-X","utf8").

## Recheck
- Container (ép PYTHONIOENCODING=cp1252): 31/31 Pass.
- Windows: chờ run_tests.bat + desktop\dev.bat.

## Lần 2 — UI "mất kết nối — tự nối lại"
- Nguyên nhân (suy từ code, chưa có log): /api/events gọi `request.is_disconnected()` trong async generator, song song với listener disconnect của StreamingResponse → tranh `receive()` → stream đứt.
- Sửa: generator đồng bộ dùng `bus.sse_lines` (Starlette chạy trong threadpool, tự lo ngắt). Backend ghi log ra `scratch/backend_<instance>.log`.
- Backup: .backup_edit_20261111_sse/. Recheck: container 31/31; Windows chờ chạy lại.
