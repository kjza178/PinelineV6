# Baseline chất lượng dịch ZH→VI trên golden SILVER (M0/T0)

_2026-11-11 · đo bằng `scripts/eval_translation.py --golden data/golden --baseline` · số liệu thô: `docs/reviews/2026-11-11-translation-baseline.json`._
_Golden là **silver** (agent tự duyệt, chờ chủ dự án xác nhận) — xem `data/golden/README.md`. Không chạy LLM/GPU/HTTP; chỉ đọc `D:\PinelineV2_work` và `D:\Lib`._

## 1. Mẫu đo

| Job | Phim | Thể loại | Câu | Bản dịch | Lý do chọn |
|---|---|---|---|---|---|
| 32da3d8f225b | 标签造神 S3 | đô thị/đại học | 2972–3112 | NMT primary (`mt=1` 140/141) + REVIEW (`rv=1` 100%) | thoại 2 người dày, plan đã chốt anh–em (học trưởng/học muội), có cặp nam–nữ đồng mưu |
| 9dedde16e35b | 每秒万年修为 S4 | tu tiên/cổ phong | 560–699 | NMT primary (133/140) + REVIEW | plan ghi rõ «không có tuyến đô thị», nhiều tầng vai vế (lão phu, bản tọa, vãn bối, sư huynh) |
| d1e00b9e7393 | 仙界归来 S1 | mixed (đô thị hình sự + tu tiên) | 2496–2639 | **LLM dịch, không NMT** (`mt` 0/144) + REVIEW | đối chứng tự nhiên cho NMT; nhiều câu cắt đôi ASR; người nghe nữ |
| f0834acf0cc0 | 毒帝无双3D S6 | mixed (game/đô thị + thần thú) | 438–579 | NMT primary (137/142) + REVIEW | lóng mạng (我去, 笑个屁, 脚盆…), thần thú tự xưng 老子/本尊 |

Quét 36 thư mục work: chỉ 14 job có `script.json`, tất cả đã dịch + `rv` 100%, dài 2 305–5 136 câu (không job nào 150–700) → lấy **một cửa sổ liên tục ~140 câu dày thoại** mỗi job (xếp hạng cửa sổ theo mật độ 你/您/他/她). Cả 14 job đều `style_lock.mode = mixed`. Chỉ `05670cbf7c1c` có 1 câu `manual`; `D:\Lib` (83 phim) chỉ có video + `series.json` (dùng tag thể loại), **không có .srt cuối** để đối chiếu. `script.pre_review.json` của các job đều đã `rv=1` (snapshot trước re-review do Agent, không phải trước REVIEW lần đầu) → **không tách được** đóng góp NMT vs REVIEW từ dữ liệu có sẵn.

## 2. Chỉ số

| Chỉ số | 32da3d8f225b | 9dedde16e35b | d1e00b9e7393 | f0834acf0cc0 | TỔNG |
|---|---|---|---|---|---|
| Số câu | 141 | 140 | 144 | 142 | 567 |
| Pronoun pair acc. | 24.0% (18/75) | 38.3% (23/60) | 90.9% (50/55) | 45.3% (24/53) | 47.3% (115/243) |
| Câu không đại từ (omitted) | 0 | 0 | 1 | 1 | 2 |
| Consistency (đổi cặp vượt gold) | 4 (sys 32 / gold 28) | 0 (sys 16 / gold 16) | 0 (sys 6 / gold 10) | 0 (sys 12 / gold 12) | 4 (sys 66 / gold 66) |
| Gender ref3 acc. | 80.0% (4/5) | 100.0% (5/5) | 100.0% (16/16) | 88.9% (8/9) | 94.3% (33/35) |
| Idiom/lóng acc. | 87.5% (7/8) | 72.7% (8/11) | 100.0% (5/5) | 64.3% (9/14) | 76.3% (29/38) |
| Câu cắt đôi còn lại (tag) | 0/0 | 3/4 | 17/19 | 0/0 | 20/23 |
| Cặp kề lặp ý (tự dò; gold) | 5 (5) | 1 (1) | 6 (0) | 2 (2) | 14 (8) |
| chrF vs gold | 67.0 | 78.3 | 78.0 | 76.8 | 75.0 |
| Edit rate (1−sim) | 0.208 | 0.135 | 0.166 | 0.150 | 0.165 |
| % câu khác gold | 61.0% | 45.7% | 36.1% | 39.4% | 45.5% |
| lỗi `pronoun` (còn/gắn) | 57/58 | 38/38 | 6/6 | 30/30 | 131/132 |
| lỗi `gender_ref3` (còn/gắn) | 1/1 | 0/0 | 0/0 | 1/2 | 2/3 |
| lỗi `idiom` (còn/gắn) | 0/0 | 3/7 | 0/0 | 5/7 | 8/14 |
| lỗi `meaning` (còn/gắn) | 30/31 | 12/13 | 21/21 | 11/13 | 74/78 |
| lỗi `style` (còn/gắn) | 15/15 | 5/6 | 1/1 | 2/2 | 23/24 |
| lỗi `halfcue_dup` (còn/gắn) | 0/0 | 3/4 | 17/19 | 0/0 | 20/23 |
| lỗi `name` (còn/gắn) | 2/2 | 2/2 | 6/8 | 3/4 | 13/16 |
| lỗi `term` (còn/gắn) | 0/0 | 2/2 | 2/2 | 4/4 | 8/8 |

Ghi chú đọc bảng: «Pronoun pair acc.» chỉ tính câu có đại từ ngôi 1/2 trong VI; `--self-check` (chấm chính `vi_gold`) cho 100% ở pronoun/ref3/idiom → bộ trích khớp nhãn. chrF/edit rate so với gold «chỉ sửa khi sai», nên ≈ tỉ lệ chữ phải sửa. «Cặp kề lặp ý (tự dò)» có dương tính giả (gold cũng 8), dùng như chỉ báo phụ.

**Tóm tắt:** 262/567 câu (46%) có ít nhất một lỗi; xưng hô đúng **47%** (NMT: 24–45%; job LLM: 91%); thành ngữ/lóng 76%; giới ngôi 3 94%; 20/23 câu cắt đôi chưa được gom.

## 3. Top lỗi theo loại (ví dụ ≤ 3 dòng)

**1) Xưng hô — 132 câu (50% số câu lỗi).** Phân rã 128 câu sai theo bộ trích:
- 59 câu: cặp thân mật/đồng đội đã chốt anh–em trong plan → ra **tôi–cậu**.
  `#3017 我猜你新学会做某种好吃的甜品。` · hệ thống: «Tôi đoán cậu mới học được cách làm món tráng miệng…» · gold: «Anh đoán em mới học làm được món tráng miệng ngon nào đó.»
- 55 câu: cổ phong/thô lỗ (ta–ngươi, lão phu, bản tôn, lão tử–mày, đệ–sư huynh) → bị **là phẳng thành tôi–cậu**.
  `#641 莫启林，是你，` · «Mặc Kỳ Lân, là cậu?» · gold «Mặc Kỳ Lân, là ngươi?» — `#558 就是你藤爷爷` · «Đằng gia gia của cậu» · gold «ông nội Đằng của mày đây»
- 7 câu Trần Phong→Đằng Xà (lão Đằng/ông) → «cậu»; 4 câu **sai giới người nghe**: kẻ bắt cóc nói với con tin nữ «thả **anh** đi» (`#2515`).

**2) Sai nghĩa — 78 câu**, phần lớn là calque câu-đơn đã qua REVIEW (`rv=1`):
`#3060 学长你这储物空间到底多大？` · «…không gian trữ vật của ngài rốt cuộc bao nhiêu tuổi?» — `#3020 还是不对再猜。` · «Vẫn là không nên đoán tiếp.» (đảo nghĩa) — `#2518 一个亿` · «Một tỷ» (sai số).
17/78 có gốc ASR (chữ ZH sai: 龙区/道商 → «căn cứ vào thương nhân đạo lý»).

**3) Văn phong — 24 câu** (Hán hoá/cứng: «Trực tiếp có thể ra mắt», «hắn» cho anh ruột/người thương, «nàng» cho mẹ).

**4) Câu cắt đôi lặp ý — 23 câu (19 ở d1e0)**: ASR sinh mảnh lặp, dịch lặp nguyên:
`#2498 陈先生进入以后。` «Sau khi anh Trần vào.» / `#2499 环境陈先生进入以后，` «Sau khi anh Trần vào,» — `#2559 是我你身上的人命。` «là những mạng người trên người anh.»

**5) Tên/thuật ngữ — 16 + 8 câu**: biến thể ASR không gom về Bible: «Chu Việt» (周越=周月), «Cô **Tống**» (秦小姐), «lão Đào» (老陶=老藤), «Thẩm Cẩm» (沈锦≈神级), «Gustin Bieen».

**6) Thành ngữ/lóng — 9/38 sai**: `#600 手下留人` «Thuộc hạ lưu người» · `#648 苟延残喘` «cẩu kéo dài hơi tàn» · `#467 脚盆` «trong chậu chân» · `#462 我去` «Tôi đi.» · `#577 大爷的` «Lão gia có chút bản lĩnh».

## 4. Nguyên nhân gắn với thành phần (theo review kiến trúc, mã K*)

| Lỗi | Bằng chứng trong mẫu | Thành phần |
|---|---|---|
| tôi–cậu thay anh–em đã chốt | plan (`glossary.pronouns`) của cả 3 job NMT **đã có đúng cặp** (Cố Thanh↔Hạ Thư Nhiên anh/em; Ngao Hoàng lão phu–tiểu tử; Đằng Xà lão tử–Trần tiểu tử) nhưng bản cuối bỏ qua; job LLM không NMT (d1e0) đúng 91% | **K13 NMT primary** (không nhận plan) + **K19 mask đóng băng** (REVIEW không được sửa đại từ) + K20 filter |
| cổ phong → tôi/cậu | V2 `translate.py` (đọc, không sửa): `apply_style_polish` nhánh `_is_mixed_genre` thay `\bngươi\b→cậu`, `\bta\b→tôi` (dòng ~1867–1876) và `_detox_modern_pronouns` (~1997); mọi job đều `mode=mixed`, kể cả phim thuần cổ phong 9ded → 38/38 lỗi xưng hô của 9ded | **K23 style polish (nhánh xưng hô mixed)** + K24 detox; style_lock một mức cho cả phim |
| sai giới người nghe (anh↔cô) | 4 câu; người nghe không xuất hiện tên trong câu | thiếu người nói/nghe (**M2/M3**) |
| calque/sai nghĩa sau REVIEW | 61 câu sai nghĩa không do ASR vẫn `rv=1` | K13 NMT câu-đơn + K18 «mặc định giữ nguyên» + **K20 filter cosmetic** (cần trace để xác minh fix bị bỏ) |
| câu cắt đôi | d1e0 dùng ASR `fun_nano` → mảnh ZH lặp; K25 half-cue heal không gom | **M1/K02–K03** + cờ gom câu cắt đôi |
| tên/thuật ngữ | biến thể ASR có sẵn trong `pronoun_lock.conflicts` (d1e0 ghi rõ 周越/周夜, 陈轩, 秦正海…) nhưng không áp vào VI | K06/K08 Bible không dùng làm từ điển sửa ASR (**M1 hotword**, M4) |
| thành ngữ/lóng | lóng mạng không có trong seed/luật; NMT dịch chữ | **M6** lexicon (+ K23 luật lẻ) |

## 5. Ước tính phần lỗi mỗi mô-đun/cờ có thể sửa (trên 262 câu lỗi; ước lượng, cần đo bằng chạy lại)

| Mô-đun / cờ thử nghiệm | Nhắm vào | Ước tính | Cơ sở |
|---|---|---|---|
| **Cờ tắt regex xưng hô mixed** (K23/K24: không ép ta/ngươi→tôi/cậu khi plan/pairs có dạng cổ) | 55 câu cổ phong | 0–55 (≤21%) | chỉ có ích nếu bản nháp sinh ta/ngươi; NMT có thể không sinh → phải đo cùng nhánh B |
| **Q8 nhánh B/C** (LLM dịch có plan, NMT làm hint/tắt) | 59 anh–em + 7 + phần cổ phong + calque | ~100–115 xưng hô (≈80–90%) + ~20–30 nghĩa → **≈45–55% câu lỗi** | job LLM d1e0: 91% vs NMT 24–45% (khác phim → nhiễu, cần A/B trên cùng cửa sổ) |
| **Q9 mask «ràng buộc»** (REVIEW được sửa đại từ theo pairs) | như trên, trên nền NMT | ~60–90% lỗi xưng hô có cặp trong plan | 121/128 câu sai thuộc cặp đã có trong plan/hiển nhiên từ ZH (师兄, 老夫, 本尊) |
| **M2/M3** người nói/nghe + giới | cặp không có trong plan, sai giới người nghe, cảnh đổi người | ~10–20 câu (4–8%) | 4 câu sai giới + cặp người lạ (Hà–Mị, Long–Âm Dương); 48/567 câu ngay người duyệt cũng không chắc người nói |
| **M1** nguồn ZH (hotword từ Bible/alias, hardsub) | lỗi gốc ASR | ~33 câu (13%) | ghi chú «ASR» trên 33 câu lỗi |
| **Cờ gom câu cắt đôi** | halfcue_dup | ~20 câu (8%) | 20/23 chưa gom, 17 ở job fun_nano |
| **M6** lexicon thành ngữ/lóng | idiom + lóng | ~10–14 câu (4–5%) | 9 idiom sai + vài câu lóng gắn `meaning` |
| **M5** kiểm tất định + RepairPass | phát hiện mọi lệch xưng hô có pairs | phát hiện ~95% của 128; sửa phụ thuộc RepairPass | bộ trích của `eval_translation.py` dùng lại được làm detector |

## 6. Nhận định — làm gì trước

1. **Thí nghiệm Q8 + cờ regex trước tiên (rẻ, đòn bẩy lớn nhất):** chạy lại 4 cửa sổ golden với `MT_ZH_VI_ROLE=hint` (đã có sẵn) và với nhánh polish mixed **tắt phần xưng hô**; đo bằng `eval_translation.py --work-dir`. Xưng hô là ½ số câu lỗi và plan **đã chứa đúng cặp** — vấn đề là đường dữ liệu bỏ qua plan (NMT + mask + regex), không phải thiếu thông tin.
2. **Q9 mask ràng buộc / M5 detector** dùng `pairs` làm ràng buộc khi nhánh B chưa đủ.
3. **M1 hotword + gom câu cắt đôi** (13% + 8%), đặc biệt cho job ASR `fun_nano`.
4. **M6** sau (4–5%); **M2 diarization** để sau cùng — trên mẫu này chỉ ~4–8% lỗi cần biết người nói ngoài những gì plan + ZH đã cho (khác giả định «M2 là đòn bẩy lớn nhất» trong thiết kế; cần xác nhận trên mẫu lớn hơn).

## 7. Giới hạn / rủi ro đo
- Golden **silver**; 4 cửa sổ × ~140 câu, 4 phim, đều plan `mixed`; không có job đô thị/cổ trang thuần plan.
- So NMT vs LLM là **giữa các phim khác nhau** (d1e0 dịch ngày 11-08, cấu hình có thể khác) — chỉ là gợi ý, cần A/B cùng cửa sổ.
- Không có snapshot trước REVIEW lần đầu → không đo được REVIEW đổi bao nhiêu % câu (cần trace, Q2 review kiến trúc).
- Residual `meaning/style/name/term` xấp xỉ bằng chrF.
