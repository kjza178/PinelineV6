# A/B cờ dịch translate-b3 trên golden silver

_2026-11-12 03:20 · `scripts/golden_ab.py` · golden `Z:\PinelineV6\data\golden` · code V2 `Z:\PinelineV2`._
_Mode: score-only. Golden là SILVER — xem `data/golden/README.md`._

## Tổng (mọi job)

| Biến thể | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | job chấm được |
|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 24.0% | 57 | 4 | 80.0% | 75.0% | 0 | 61.8 | 0.240 | 69.5% | 0 | 1 |
| regex_mask_off | — | — | — | — | — | — | — | — | — | — | 0 |
| simple | — | — | — | — | — | — | — | — | — | — | 0 |

## Theo job

### base

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | 24.0% | 57 | 4 | 80.0% | 75.0% | 0 | 61.8 | 0.240 | 69.5% | 0 | ok |

### regex_mask_off

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | — | — | — | — | — | — | — | — | — | — | chưa chạy |

### simple

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | — | — | — | — | — | — | — | — | — | — | chưa chạy |

Ghi chú: «Xưng hô đúng» = pronoun pair accuracy; «Câu cắt đôi còn» = câu gắn halfcue_dup chưa sửa. Định nghĩa đầy đủ: docstring `scripts/eval_translation.py`.
