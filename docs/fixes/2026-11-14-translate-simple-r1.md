# 2026-11-14 — Luồng dịch simple: R1.1 soát lại báo xong giả · R1.2 plan hỏng bị lưu · R1.3 lỗi code bị park · R1.4 giờ

Mức kiểm: **C (cloud)** — tái hiện bằng LLM giả trước khi sửa, test giữ lại sau khi sửa. Chưa chạy job thật (W/J).
Cần restart :8806.

## R1.1 «Soát lại» khi cloud chết vẫn báo xong
- **Tái hiện:** dịch xong 6 câu → cho mọi lô lỗi 429 → `pipeline.run(rereview=True)` trả `done`, `n_ok 6/6`,
  manifest «đủ bản dịch», chỉ gọi model 1 lần (không thử lại).
- **Nguyên nhân:** `stage_review.run` tính câu còn lại bằng cờ `rv`; khi soát lại, `rv=1` còn từ lượt trước.
- **Sửa:** theo dõi tập câu được soát TRONG lượt gọi (`done_pos`). Lô lỗi được thử lại; `n_ok`/`n_left` đúng →
  pipeline biết cloud chết (bỏ bước vá). Kết quả + manifest ghi `rereview_left`, `cur_text` có
  «SOÁT LẠI CHƯA XONG: N câu giữ bản cũ». Bản dịch cũ giữ nguyên.
- **Sau sửa:** `n_ok 0/6`, `n_left 6`, 2 lần gọi, cảnh báo hiện trên UI.

## R1.2 Lập plan hỏng → cả phim mất bảng nhân vật
- **Tái hiện:** `build_plan` lỗi 429 → luồng vẫn `done`, `glossary.json` ghi ra với 0 nhân vật / 0 cặp xưng hô;
  chạy lại khi cloud đã ổn → `build_plan` được gọi 0 lần («dùng lại glossary.json có sẵn»).
- **Sửa (`stage_draft.build_plan`, `pipeline.run`, `cfg.require_plan`):**
  - plan lỗi hoặc không có nhân vật/thuật ngữ/cặp nào = **plan hỏng**: đánh dấu `_meta.plan_failed`, **không ghi**
    `glossary.json`, không «dạy» bảng bộ; `glossary.json` mang dấu này bị bỏ qua và lập lại.
  - `SIMPLE_REQUIRE_PLAN=1` (mặc định): plan hỏng → **không soát**, cổng `park` «chưa lập được bảng nhân vật» →
    hàng chờ tự thử lại (B2). Lý do không soát: câu soát lúc này mang `rv=1` và sẽ không được soát lại bằng plan đúng.
  - `SIMPLE_REQUIRE_PLAN=0`: dịch với plan tạm như trước (cho nội dung thật sự không có nhân vật), vẫn không lưu plan hỏng.
- **Lưu ý:** job cũ đã dính `glossary.json` trống từ trước (không có dấu) không tự phát hiện được — xoá file đó rồi «Dịch LẠI».

## R1.3 Lỗi code bị xử lý như lỗi cloud
- **Tái hiện:** engine ném `KeyError` → Stage đổi thành park, hẹn thử lại 3 × 5 phút rồi mới HOLD; lý do cắt còn 120 ký tự.
- **Sửa (`pipeline/stages/simple_translate.py`, `pipeline/gates/simple_translate_gate.py`):** phân loại bằng
  `engines.cloud_retry.classify_cloud_retry` (+ `TimeoutError`/`ConnectionError`): lỗi tạm → park như cũ; lỗi khác →
  ghi traceback đủ vào `<job>/translate_error.log`, engine trả `status="error"`, Gate đánh `stages.translate = failed`
  kèm lý do và trả FAIL — không đếm lượt thử lại, không giữ ghế.

## R1.4 Giờ của stage dịch
- `pipeline._now()` dùng `core.timeutil.now_iso()` (ISO UTC) thay `time.strftime` giờ máy không múi giờ.

## Test
`tests/test_translate_simple_fixes.py` (8 test), `tests/test_f6_port_simple_demucs.py` (đổi 1 test theo hành vi mới + 1 test mới).
