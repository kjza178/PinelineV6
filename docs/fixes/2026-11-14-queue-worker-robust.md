# 2026-11-14 — Hàng chờ: R7.1 ghi file lỗi làm chết worker · R7.2 job giữ ghế bị chen

Rà khu vực «Hàng chờ» (`core/job_queue.py`) theo `docs/WORKING_MODEL.md` mục 4.
Mức kiểm: **C (cloud)** — tái hiện trước khi sửa, test giữ lại; CI chạy cả trên Windows. Cần restart :8806.

## R7.1 Ghi `_job_queue.json` lỗi một lần → worker chết im lặng
- **Tái hiện:** xếp 3 job; cho `os.replace` ném `PermissionError` đúng 1 lần (trên Windows hay gặp khi antivirus /
  tiến trình khác đang mở file; hoặc đĩa đầy) → thread worker văng: **0 job chạy**, `current` kẹt ở job đầu, 2 job
  còn lại đứng mãi tới khi có người xếp job mới. Không có dòng log nào ngoài traceback của thread.
- **Nguyên nhân:** `_save()` ném lỗi; nó được gọi trong khoá ở vòng worker và trong mọi API hàng chờ.
- **Sửa:**
  - `_save()` không bao giờ ném: thử lại 4 lần (chờ ngắn), hỏng thì ghi log (không spam), trả `False`; hàng chờ trong
    bộ nhớ vẫn chạy, lần ghi sau ghi bù. `queue_state()["persist_ok"]` = False khi đang không ghi được → UI desktop
    hiện cảnh báo trên thanh hàng chờ.
  - Vòng worker có lưới an toàn: lỗi không lường trước trong chính vòng lặp → log kèm traceback, chạy tiếp
    (5 lần liên tiếp thì dừng worker, giữ nguyên hàng chờ).
- **Sau sửa:** cùng kịch bản → chạy đủ A, B, C. Đĩa đầy kéo dài → vẫn chạy hết, báo «KHÔNG ghi được…», có chỗ lại thì tự ghi bù.

## R7.2 Job đang giữ ghế (park/HOLD) bị job khác chen lên trước
- **Tái hiện:** A park (giữ ghế); trong lúc đó đẩy C lên đầu hàng (`queue_move`, hoặc `queue_sort`, hoặc «Chạy tiếp»
  một job khác → `queue_front`); A sẵn sàng → thứ tự chạy `A, C, A, B` — trái luật Studio «không nhảy job».
- **Sửa:** khi job park sẵn sàng, đưa nó về đầu hàng trước khi vòng kế lấy job. Sau sửa: `A, A, C, B`.

## Ghi nhận, CHƯA sửa (cần chủ dự án quyết — ROADMAP R7.3–R7.5)
- **HOLD chặn cả hàng chờ vô thời hạn.** Đúng luật Studio hiện tại (1 ghế, không nhảy job), nhưng với mục tiêu chạy
  24/7 không người canh: một job HOLD lúc 2 giờ sáng = máy nghỉ tới khi có người. Cần quyết: giữ luật, hay cho phép
  «HOLD cần người quá N phút → nhường ghế cho job kế, job HOLD chờ bên lề».
- **Lỗi hệ thống đốt sạch hàng chờ.** Job `failed` → lấy ngay job kế. Nếu nguyên nhân là chung (GPU chết, đĩa đầy,
  cloud sập) thì cả hàng chờ bị đánh failed trong vài phút. Đề xuất: N job failed liên tiếp → tạm dừng hàng chờ + báo.
- **Lịch sử hàng chờ chỉ nằm trong bộ nhớ** (20 mục, mất khi restart) → khó truy «đêm qua chuyện gì xảy ra».

## Test
`tests/test_job_queue_robust.py` (5 test).
