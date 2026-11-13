# A/B cờ dịch translate-b3 trên golden silver

_2026-11-12 08:36 · `scripts/golden_ab.py` · golden `data\golden_heldout` · code V2 `Z:\PinelineV2`._
_Mode: run. Golden là SILVER — xem `data/golden/README.md`._

## Tổng (mọi job)

| Biến thể | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | job chấm được |
|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 34.7% | 47 | 0 | 100.0% | 77.5% | 0 | 74.6 | 0.153 | 50.9% | 0 | 2 |
| nmt_hint_regex_mask_off | 98.5% | 1 | 0 | 100.0% | 75.0% | 0 | 64.8 | 0.223 | 78.1% | 0 | 2 |
| simple | 98.2% | 1 | 0 | 100.0% | 75.0% | 0 | 61.9 | 0.235 | 76.7% | 0 | 2 |

## Theo job

### base

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2493d3c7cb29_p01 | 10.9% | 41 | 0 | 100.0% | 68.0% | 0 | 72.3 | 0.170 | 55.3% | 0 | ok |
| 2d3316593ea9 | 76.9% | 6 | 0 | 100.0% | 93.3% | 0 | 76.9 | 0.137 | 46.5% | 0 | ok |

### nmt_hint_regex_mask_off

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2493d3c7cb29_p01 | 97.7% | 1 | 0 | 100.0% | 76.0% | 0 | 65.3 | 0.215 | 80.8% | 0 | ok |
| 2d3316593ea9 | 100.0% | 0 | 0 | 100.0% | 73.3% | 0 | 64.4 | 0.231 | 75.3% | 0 | ok |

### simple

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2493d3c7cb29_p01 | 97.4% | 1 | 0 | 100.0% | 80.0% | 0 | 64.6 | 0.215 | 77.3% | 0 | ok |
| 2d3316593ea9 | 100.0% | 0 | 0 | 100.0% | 66.7% | 0 | 59.1 | 0.255 | 76.1% | 0 | ok |

Ghi chú: «Xưng hô đúng» = pronoun pair accuracy; «Câu cắt đôi còn» = câu gắn halfcue_dup chưa sửa. Định nghĩa đầy đủ: docstring `scripts/eval_translation.py`.
