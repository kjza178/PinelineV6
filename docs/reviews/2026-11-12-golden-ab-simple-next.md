# A/B cờ dịch translate-b3 trên golden silver

_2026-11-12 08:03 · `scripts/golden_ab.py` · golden `data\golden` · code V2 `Z:\PinelineV2`._
_Mode: run. Golden là SILVER — xem `data/golden/README.md`._

## Tổng (mọi job)

| Biến thể | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | job chấm được |
|---|---|---|---|---|---|---|---|---|---|---|---|
| simple | 92.0% | 19 | 3 | 93.1% | 100.0% | 20 | 60.8 | 0.245 | 79.5% | 1 | 4 |
| simple_noleak | 92.0% | 15 | 3 | 92.3% | 69.7% | 3 | 63.8 | 0.217 | 75.9% | 0 | 3 |
| simple_rules | 92.0% | 14 | 3 | 100.0% | 97.0% | 4 | 62.7 | 0.223 | 74.2% | 0 | 3 |
| simple_rules_fill | 90.1% | 18 | 4 | 100.0% | 100.0% | 4 | 63.1 | 0.222 | 73.3% | 0 | 3 |

## Theo job

### simple

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | 97.3% | 2 | 0 | 100.0% | 100.0% | 0 | 59.2 | 0.247 | 80.8% | 0 | ok |
| 9dedde16e35b | 96.6% | 2 | 0 | 100.0% | 100.0% | 3 | 67.0 | 0.204 | 70.7% | 0 | ok |
| d1e00b9e7393 | 87.0% | 7 | 1 | 86.7% | 100.0% | 17 | 54.3 | 0.313 | 88.2% | 0 | ok |
| f0834acf0cc0 | 83.7% | 8 | 2 | 100.0% | 100.0% | 0 | 62.8 | 0.214 | 78.0% | 1 | ok |

### simple_noleak

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | 97.3% | 2 | 2 | 100.0% | 100.0% | 0 | 63.2 | 0.208 | 76.6% | 0 | ok |
| 9dedde16e35b | 96.6% | 2 | 0 | 100.0% | 81.8% | 3 | 65.2 | 0.223 | 75.0% | 0 | ok |
| d1e00b9e7393 | — | — | — | — | — | — | — | — | — | — | thiếu asr.json |
| f0834acf0cc0 | 79.2% | 11 | 1 | 83.3% | 42.9% | 0 | 63.0 | 0.220 | 76.1% | 0 | ok |

### simple_rules

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | 100.0% | 0 | 0 | 100.0% | 100.0% | 0 | 65.3 | 0.199 | 73.8% | 0 | ok |
| 9dedde16e35b | 96.4% | 2 | 0 | 100.0% | 100.0% | 4 | 64.7 | 0.216 | 70.7% | 0 | ok |
| d1e00b9e7393 | — | — | — | — | — | — | — | — | — | — | thiếu asr.json |
| f0834acf0cc0 | 73.3% | 12 | 3 | 100.0% | 92.9% | 0 | 58.1 | 0.253 | 78.2% | 0 | ok |

### simple_rules_fill

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | 98.7% | 1 | 0 | 100.0% | 100.0% | 0 | 60.6 | 0.239 | 79.4% | 0 | ok |
| 9dedde16e35b | 89.7% | 6 | 0 | 100.0% | 100.0% | 4 | 67.0 | 0.210 | 68.6% | 0 | ok |
| d1e00b9e7393 | — | — | — | — | — | — | — | — | — | — | thiếu asr.json |
| f0834acf0cc0 | 77.5% | 11 | 4 | 100.0% | 100.0% | 0 | 61.7 | 0.218 | 71.8% | 0 | ok |

Ghi chú: «Xưng hô đúng» = pronoun pair accuracy; «Câu cắt đôi còn» = câu gắn halfcue_dup chưa sửa. Định nghĩa đầy đủ: docstring `scripts/eval_translation.py`.
