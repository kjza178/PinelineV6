"""translate_simple — luồng dịch ZH→VI gọn (2026-11-12, chủ dự án duyệt).

V6: MẶC ĐỊNH BẬT (cfg.translate_pipeline). Rollback: `"translate_pipeline": "legacy"` trong instances/<tên>.json
(hoặc env/config TRANSLATE_PIPELINE=legacy) → luồng cũ run_translate + run_review.

Luồng 6 bước (xem docs/design/2026-11-12-translate-simple.md):
  1) ASR có sẵn (asr.json)
  2) stage_draft : Plan (cloud) ∥ NMT nháp (GPU) — 2 thread song song
  3) stage_review: REVIEW cloud theo lô — MỘT lần gọi: sửa nghĩa + áp xưng hô theo cặp plan +
                   tên/thuật ngữ + thành ngữ + dịch câu trống; trả {i, vi, spk, to, conf}
  4) post + validate: hậu kỳ tất định (khoá thuật ngữ + chuẩn số, KHÔNG regex đại từ) → đánh cờ
  5) repair      : vá đúng câu bị cờ, ≤ SIMPLE_REPAIR_ROUNDS lượt
  6) gate        : done | park (server park waiting_review B2) | hold (cần người)

Một chính sách retry: mọi gọi cloud qua cloud_retry.call_with_cloud_retry. KHÔNG fallback local,
KHÔNG switch_preset (lý do: docs thiết kế mục «Retry»).
"""
