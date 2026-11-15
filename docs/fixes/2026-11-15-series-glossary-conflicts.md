# 2026-11-15 — Bảng nhân vật của bộ bị tập mới âm thầm ghi đè

**Mức kiểm:** C (unit test `tests/test_series_conflicts.py`). Chờ W/J.

## Triệu chứng (đọc code)
`engines/series_glossary.save_from` gộp bảng của tập vào kho bộ bằng `_merge_list(base=kho, top=tập)` → **tập mới luôn
thắng**. Nó được gọi ngay sau khi lập plan (`translate_simple/stage_draft.build_plan`, `translate.py` luồng cũ) với
`glossary.json` là plan THÔ vừa lập (ghi trước `apply`). Hệ quả: plan của tập 5 dịch 林凡 = «Lâm Phiền» (AI đoán khác)
→ kho bộ đổi theo, không báo → tập 6, 7… kế thừa tên sai. Cùng lỗi cho giới tính, cặp xưng hô, thuật ngữ khoá.
Ngoài ra: ghi kho không atomic; lỗi đồng bộ films.db bị nuốt (`except: pass`).

## Sửa
- Trường rời rạc được bảo vệ: nhân vật `vi`, `gender`; cặp xưng hô `self`, `call`; thuật ngữ khoá `vi`.
  Khác bản kho → giữ kho, ghi `data/series_glossary/<bộ>.conflicts.json` (gộp trùng theo mục/trường/bản đề xuất,
  đếm số lần, tập nào). Ô kho trống → điền. Bí danh → hợp. Mục mới → thêm. Mô tả tự do (vai trò, thể loại) không tạo xung đột.
- Tập có `context_pack.json` `skip_plan` (bảng do người duyệt qua Context Studio) → ghi đè, xung đột ghi trạng thái `auto`.
- `resolve_conflict(core, id, keep|use_new|skip)`; `use_new` ghi kho và đóng các đề xuất khác cho cùng mục/trường.
- API: `GET /api/v6/series/conflicts`, `POST /api/v6/series/conflicts/resolve`; sự kiện SSE `series.conflict`.
- Desktop: nút «Xung đột tên (N)» chỉ hiện khi có; hộp thoại chọn Giữ / Dùng bản mới / Bỏ qua.
- Ghi kho bằng `atomic_write_json`; lỗi films.db / đọc file có log.

## Chưa làm
- «Dùng bản mới» chỉ áp cho các tập SAU; tập đã dịch không tự dịch lại.
