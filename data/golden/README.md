# Golden set dịch ZH→VI (M0) — trạng thái: **SILVER** (chờ chủ dự án xác nhận)

_Dựng 2026-11-11 bởi agent (Claude) đóng vai người duyệt. Chưa có người thật duyệt → mọi nhãn là **silver**:
dùng để đo xu hướng/so trước–sau, KHÔNG coi là chân lý tuyệt đối. Thiết kế: `docs/design/2026-11-11-translation-quality.md` (M0)._

## Thành phần

| Job (V2 work) | Phim | Thể loại mẫu | Câu mẫu (liên tục) | Cảnh |
|---|---|---|---|---|
| `32da3d8f225b` | 标签造神 mùa 3 | đô thị / đại học (modern) | 2972–3112 (141) | Hà Hải Khôn↔Mị Nương; Cố Thanh↔Ma Kính; Cố Thanh↔Hạ Thư Nhiên (học trưởng–học muội) |
| `9dedde16e35b` | 每秒万年修为 mùa 4 | tu tiên / cổ phong | 560–699 (140) | Long Ngạo Thiên đấu Âm Dương Huyền Triệt; Ngao Hoàng (lão phu) ↔ Mặc Kỳ Lân (bản tọa) |
| `d1e00b9e7393` | 仙界归来 mùa 1 | mixed (đô thị hình sự + tu tiên) | 2496–2639 (144) | bắt cóc – giải cứu – cảnh sát; job **không có NMT** (đối chứng) |
| `f0834acf0cc0` | 毒帝无双3D mùa 6 | mixed (game/đô thị + thần thú) | 438–579 (142) | đồng đội (lóng mạng), Tử Du (nữ), Đằng Xà (lão tử) ↔ Bát Kỳ Đại Xà (bản tôn) |

Tổng **567 câu**. Mỗi job một thư mục:

- `meta.json` — tiêu đề, thể loại, khoảng câu, danh sách cảnh, `status: silver`.
- `characters.json` — nhân vật trong mẫu (id, zh, vi, giới, vai, quan hệ). Người nói không suy ra được → không tạo nhân vật (dùng `?`).
- `pairs.json` — cặp xưng hô spk→to: `self`, `call` (danh sách dạng chấp nhận), `range` (nếu quan hệ đổi), `evidence` (câu ZH làm căn cứ), `confidence`.
- `lines.gold.jsonl` — mỗi dòng: `i, scene, zh, vi_system, spk, to, pron_expected{self,call,ref3}, vi_gold, error_tags, note` (+ `idiom{zh,accept,forbid}` nếu câu có thành ngữ/lóng).
  - `vi_system` = bản V2 hiện có lúc dựng (đã qua REVIEW, `rv=1`).
  - `vi_gold` = **chỉ sửa khi sai** (xưng hô / nghĩa / thành ngữ / văn phong / câu cắt đôi / tên / thuật ngữ); câu không sai giữ nguyên `vi_system`. `vi_gold = ""` = câu cắt đôi lặp ý nên gộp/bỏ.
  - `pron_expected.self/call`: danh sách dạng chấp nhận; `null` = không kỳ vọng. `ref3`: `m|f|n` = giới người được nhắc bằng 他/她/它.
  - `error_tags` ∈ `pronoun, gender_ref3, idiom, meaning, style, halfcue_dup, name, term` — lỗi của `vi_system`.
- `_src/<job>.txt` + `_src/meta.py` — bản chú thích gốc (dễ sửa tay), sinh lại JSON bằng `scripts/golden_build_silver.py`.

## Dùng

```bash
python scripts/eval_translation.py --golden data/golden --baseline                 # đo bản hiện tại (vi_system)
python scripts/eval_translation.py --golden data/golden --work-dir D:/PinelineV2_work  # đo script.json mới của các job
python scripts/eval_translation.py --golden data/golden --job 32da3d8f225b --script <path>/script.json
python scripts/eval_translation.py --golden data/golden --self-check              # kiểm bộ trích (phải ≈100%)
#  --out-json r.json --out-md r.md
```
Câu được ghép theo `i` (kiểm `zh`); lệch chỉ số thì tìm theo `zh` trong ±40 câu. Chỉ số xem docstring `scripts/eval_translation.py`.
Baseline: `docs/reviews/2026-11-11-translation-baseline.md`.

## Chủ dự án xác nhận / sửa

1. Sửa trực tiếp `pairs.json` và `lines.gold.jsonl` (hoặc `_src/<job>.txt` rồi chạy
   `python scripts/golden_build_silver.py --src data/golden/_src --scripts D:/PinelineV2_work --out data/golden` —
   lệnh này GHI ĐÈ JSON từ `_src`, nên chọn MỘT nơi để sửa; khuyến nghị sửa `_src` vì gọn).
2. Ưu tiên xem: các `pairs` có `confidence: medium` (Hà–Mị, Âm Dương–Long Ngạo Thiên, Tần Vũ Phi «em/tôi» trong cảnh được cứu,
   Trần Phong→Đằng Xà «anh/ông»), các câu `spk: "?"` (48 câu), và các câu có `note` chứa «ASR» (chữ ZH sai — gold dịch theo nghĩa đoán).
3. Duyệt xong: đổi `status` trong `meta.json`/`pairs.json` thành `gold`, ghi người duyệt + ngày.
4. Chạy `--self-check` sau khi sửa: nếu < 100% là bộ trích đại từ chưa hiểu dạng mới → thêm vào `FIRST/SECOND/KIN/NOT_PRON` trong `scripts/eval_translation.py` (có test `tests/test_eval_translation.py`).

## Giới hạn đã biết
- Mỗi «tập» là **một cửa sổ ~140 câu liên tục** trong job cả mùa (2 300–5 100 câu/job): work V2 không có job 150–700 câu. Không có job nào có sửa tay `manual` đáng kể (chỉ 1 câu ở `05670cbf7c1c`); `D:\Lib` chỉ chứa video + `series.json` (không có .srt cuối) → không đối chiếu được sub thành phẩm.
- Chỉ số residual cho `meaning/style/name/term` xấp xỉ bằng chrF < 0.8 so với gold → bản dịch lại đúng nhưng khác chữ vẫn bị đếm; cần người xem.
- `gender_ref3` chỉ có 35 câu kỳ vọng (mẫu ít 他/她 mơ hồ).
- Không chứa media/âm thanh; nội dung thoại là của chủ dự án — chỉ để trong cây V6.
