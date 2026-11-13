# Golden HELD-OUT dịch ZH→VI — trạng thái: **SILVER** (chờ chủ dự án xác nhận)

_Dựng 2026-11-12 bởi agent A (Claude). Cùng định dạng `data/golden` (xem `data/golden/README.md`), thêm `"heldout": true` trong `meta.json`/`pairs.json`._

## ⚠️ LUẬT: chỉ để ĐO — KHÔNG dùng để seed

**KHÔNG** được dùng bất kỳ nội dung nào ở đây (câu ZH, vi_gold, dạng chấp nhận thành ngữ, cặp xưng hô) để
seed/sửa `PinelineV2/data/lexicon/idioms_zh_vi.json`, prompt, `_meaning_rules/_style_rules`, glossary, test kỳ vọng
hay bất kỳ quy tắc dịch nào. Lý do: golden cũ (`data/golden`) đã bị «học tủ» — lexicon được seed một phần từ lỗi golden
(phủ 37/38 thành ngữ) nên «Thành ngữ 100%» của simple là ảo. Bộ này để đo thật. Nếu buộc phải sửa lexicon theo một
lỗi thấy ở đây → coi mục đó đã «cháy», ghi vào bảng dưới và loại khỏi số đo thành ngữ.

## Thành phần (2 cửa sổ liên tục, khác 4 phim của `data/golden`)

| Job (V2 work) | Phim | Thể loại cửa sổ | Câu | Nội dung |
|---|---|---|---|---|
| `2493d3c7cb29_p01` | 游戏入侵…屠戮神明三 (phần 1/4) | cổ phong/huyền huyễn thuần | 1815–1955 (141) | Tuyết Đế ↔ Nguyệt Luân Khách đàm phán → tuyên chiến (ta–ngươi) |
| `2d3316593ea9` | 无敌财神 mùa 6 | đô thị hiện đại | 0–141 (142) | thẩm tra gian tế; Lục Phóng ↔ lão Chu (bạn thân, tôi–cậu/tao–mày); lóng game |

Giả định đã chốt (user vắng): V2 work **không có** job cổ trang thuần hay đô thị thuần có `asr.json` (golden_ab cần
`asr.json`); `4041cc3bb069_p*` thiếu `asr.json`, `365baf19df54` cùng phim golden `32da3d8f225b`. Nên chọn **cửa sổ**
thuần thể loại trong job mixed. `2493d3c7cb29_p01` chưa qua REVIEW (`vi_system` = bản dịch thô, rv=0).

Tổng 283 câu · 72 câu chấm xưng hô · 7 câu ref3 · **40 câu thành ngữ/lóng (37 cụm khác nhau)**.
`_src/` + `_src/meta.py` là bản chú thích; sinh lại JSON:
`python scripts/golden_build_silver.py --src data/golden_heldout/_src --scripts D:/PinelineV2_work --out data/golden_heldout`.

## Thành ngữ/lóng held-out mà lexicon hiện tại KHÔNG chứa (đo thật)

Kiểm 2026-11-12: `idioms_zh_vi.json` (52 mục) phủ **0/40** câu; glossary plan của 2 job (type chengyu/idiom/style)
phủ 0; `translate.py`/`translate_simple/` không có chuỗi nào trong số này.

- `2493d3c7cb29_p01`: 袖手旁观, 大祸临头, 大驾光临, 有失远迎, 罢兵言和, 欺人太甚, 炮灰, 拱火, 昏天黑地 (ASR 昏天血地),
  趁虚而入, 抄老巢, 倾巢而出, 弹指间, 吃不吃得下, 一派胡言, 谈不拢便动手, 将进酒…杯莫停, 永远的神, 货真价实,
  引狼入室, 一句准话, 蚕食, 不可一世, 溯源归宗, 彻头彻尾
- `2d3316593ea9`: 欲加之罪何患无辞, 赶尽杀绝, 干得漂亮 (×2), 不划算, 性价比 (×3), 有舍才有得, 败家, 开挂, 手速,
  抢单之王, 反向指标, 开刷

| Mục đã «cháy» (đã lỡ dùng để seed) | Ngày | Ghi chú |
|---|---|---|
| (chưa có) | | |

## Dùng

```bat
python scripts\eval_translation.py --golden data\golden_heldout --self-check     :: phải 100%
python scripts\eval_translation.py --golden data\golden_heldout --baseline       :: vi_system
scripts\golden_ab_simple.bat dry heldout      :: in kế hoạch (2 job × base,nmt_hint_regex_mask_off,simple)
scripts\golden_ab_simple.bat heldout          :: CHẠY THẬT — tốn quota GPU/GPT, chỉ khi V2 rảnh
scripts\golden_ab_simple.bat score heldout    :: chấm lại → docs\reviews\2026-11-12-golden-ab-heldout.md
scripts\ab_inspect.bat heldout                :: soi mức câu simple vs base
```

Baseline `vi_system` (2026-11-12): xưng hô 34.7% (25/72; cổ phong 10.9% vì bản thô dùng tôi–cậu), thành ngữ 70.0%
(28/40), ref3 100% (7/7), chrF 79.3. Self-check 100% mọi chỉ số.

## Giới hạn
- Silver, 1 người duyệt (agent). Cặp `confidence: medium`: Nguyệt Luân Khách→Tuyết Đế, người bị giữ→trưởng quan, Lục Phóng→nhóm.
- Cổ phong: chọn ta–ngươi theo `glossary.tone` («cường giả dùng ta – ngươi») dù `style_lock.pronoun_policy` của job
  ghi «CẤM mặc định … bổn tọa» — chủ dự án xem lại nếu muốn tôi–cậu.
- Mỗi cửa sổ có 1 câu ASR lặp đôi (`1838 明白明白`, `137 开刷开刷`) mà script V2 đã gỡ: luồng simple chưa gỡ lặp
  → có thể ra 1 câu «không ghép»/job (nhiều khả năng chính là câu «không ghép» của `f0834acf0cc0`: i=542 ASR `杀吧杀吧` vs gold `杀吧`).
- Không chứa media; thoại thuộc chủ dự án — chỉ để trong cây V6.
