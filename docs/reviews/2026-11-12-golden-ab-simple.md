# A/B cờ dịch translate-b3 trên golden silver

_2026-11-12 03:18 · `scripts/golden_ab.py` · golden `Z:\PinelineV6\data\golden` · code V2 `Z:\PinelineV2`._
_Mode: run. Golden là SILVER — xem `data/golden/README.md`._

## Tổng (mọi job)

| Biến thể | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | job chấm được |
|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 35.1% | 157 | 5 | 88.2% | 65.8% | 22 | 64.5 | 0.227 | 64.7% | 0 | 4 |
| regex_mask_off | 47.7% | 126 | 14 | 91.2% | 68.4% | 22 | 65.5 | 0.216 | 65.1% | 0 | 4 |
| nmt_hint_regex_mask_off | 89.0% | 26 | 5 | 93.3% | 79.0% | 20 | 63.0 | 0.228 | 77.2% | 0 | 4 |
| simple | 92.0% | 19 | 3 | 93.1% | 100.0% | 20 | 60.8 | 0.245 | 79.5% | 1 | 4 |

## Theo job

### base

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | 24.0% | 57 | 4 | 80.0% | 75.0% | 0 | 61.8 | 0.240 | 69.5% | 0 | ok |
| 9dedde16e35b | 38.3% | 37 | 1 | 100.0% | 72.7% | 4 | 74.3 | 0.157 | 52.1% | 0 | ok |
| d1e00b9e7393 | 37.0% | 34 | 0 | 86.7% | 40.0% | 18 | 48.9 | 0.342 | 92.4% | 0 | ok |
| f0834acf0cc0 | 45.3% | 29 | 0 | 88.9% | 64.3% | 0 | 73.3 | 0.167 | 44.4% | 0 | ok |

### regex_mask_off

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | 52.0% | 36 | 12 | 80.0% | 87.5% | 0 | 65.7 | 0.210 | 67.4% | 0 | ok |
| 9dedde16e35b | 88.3% | 7 | 0 | 100.0% | 72.7% | 3 | 76.9 | 0.143 | 50.7% | 0 | ok |
| d1e00b9e7393 | 5.5% | 52 | 0 | 86.7% | 40.0% | 19 | 48.1 | 0.342 | 92.4% | 0 | ok |
| f0834acf0cc0 | 39.2% | 31 | 2 | 100.0% | 64.3% | 0 | 71.8 | 0.168 | 49.3% | 0 | ok |

### nmt_hint_regex_mask_off

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | 92.0% | 6 | 0 | 100.0% | 87.5% | 0 | 64.3 | 0.212 | 79.4% | 0 | ok |
| 9dedde16e35b | 96.7% | 2 | 0 | 100.0% | 81.8% | 3 | 70.3 | 0.183 | 70.7% | 0 | ok |
| d1e00b9e7393 | 88.5% | 6 | 0 | 86.7% | 60.0% | 17 | 57.3 | 0.281 | 82.6% | 0 | ok |
| f0834acf0cc0 | 75.5% | 12 | 5 | 100.0% | 78.6% | 0 | 60.4 | 0.236 | 76.1% | 0 | ok |

### simple

| Job | Xưng hô đúng | Câu sai xưng hô | Đổi cặp vượt gold | Giới ngôi 3 | Thành ngữ/lóng | Câu cắt đôi còn | chrF | Edit rate | % khác gold | Không ghép | chạy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | 97.3% | 2 | 0 | 100.0% | 100.0% | 0 | 59.2 | 0.247 | 80.8% | 0 | ok |
| 9dedde16e35b | 96.6% | 2 | 0 | 100.0% | 100.0% | 3 | 67.0 | 0.204 | 70.7% | 0 | ok |
| d1e00b9e7393 | 87.0% | 7 | 1 | 86.7% | 100.0% | 17 | 54.3 | 0.313 | 88.2% | 0 | ok |
| f0834acf0cc0 | 83.7% | 8 | 2 | 100.0% | 100.0% | 0 | 62.8 | 0.214 | 78.0% | 1 | ok |

Ghi chú: «Xưng hô đúng» = pronoun pair accuracy; «Câu cắt đôi còn» = câu gắn halfcue_dup chưa sửa. Định nghĩa đầy đủ: docstring `scripts/eval_translation.py`.
