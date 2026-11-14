# 2026-11-14 — Desktop: R2.1 đóng app giết cả cây tiến trình · R2.2 backend chết sớm báo ngay

Mức kiểm: **C (cloud)** — `cargo check --target x86_64-pc-windows-msvc` qua. **Hành vi phải kiểm trên Windows (W).**
Cần build lại app (`desktop\dev.bat` / `desktop\build.bat`).

## R2.1
- **Vấn đề:** thoát app gọi `Child::kill()` = TerminateProcess chỉ tiến trình Python. Render worker, ffmpeg, engine
  ASR/TTS (tiến trình cháu) sống tiếp: giữ GPU, vẫn ghi file.
- **Sửa (`desktop/src-tauri/src/main.rs` `kill_tree`):** `taskkill /PID <pid> /T /F` (không bật cửa sổ console) trước,
  rồi `kill()` + `wait()`.
- **Kiểm trên Windows:** đang render → đóng app → Task Manager không còn `ffmpeg.exe` / `python.exe` của job.

## R2.2
- **Vấn đề:** Python không có trong PATH, hoặc backend văng lúc import → app vẫn chờ đủ 180 s rồi mới báo, không nói vì sao.
- **Sửa:** vòng chờ gọi `try_wait()` trên backend — đã thoát thì báo ngay; không spawn được cũng báo ngay. Màn chờ
  (`desktop/splash/index.html`) hiện lý do + đường dẫn log đầy đủ.
- **Kiểm trên Windows:** đặt `PINELINE_PYTHON=khong_co.exe` → mở app → thấy lỗi trong vài giây.

## Chưa làm (ROADMAP R2.3)
Hỏi xác nhận khi đóng app lúc còn job đang chạy; thu nhỏ xuống khay.
