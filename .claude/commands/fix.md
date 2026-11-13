---
description: Quy trình sửa bug trọn vẹn — chẩn đoán (fix-advisor) → user duyệt → backup → sửa → test → review → docs → sync brain
argument-hint: <triệu chứng hoặc mã bug B#/R# từ báo cáo>
---
Bug: $ARGUMENTS

Pha 1 — Chẩn đoán (không sửa gì):
1. Giao **brain-librarian** tra: bug tương tự trong `docs/fixes/`, quyết định liên quan trong `docs/DECISIONS.md`, module dính líu.
2. Giao **fix-advisor** với kết quả trên → root cause + phương án + test + fix log nháp.
3. Trình bày phương án khuyến nghị cho user và **dừng lại chờ duyệt** (trừ khi user đã nói rõ "cứ sửa").

Pha 2 — Áp dụng (sau khi user đồng ý):
4. Backup đúng rule dự án: `python D:\PinelineV2_tmp\snapshot.py` + chép file gốc sắp sửa vào `Z:\PinelineV5_codebak\<YYYY-MM-DD>_<slug>\` (giữ đường dẫn tương đối).
5. Áp patch nhỏ nhất. `python -m py_compile <file>`; chạy pytest liên quan + test mới.
6. Giao **code-reviewer** review đúng phần vừa sửa (diff với codebak). Sửa các mục blocking.
7. Docs: `docs/fixes/YYYY-MM-DD-<slug>.md` (template fix-log) + dòng trong `docs/fixes/README.md`, `docs/CHANGELOG.md` (ghi cần restart gì), `docs/worklog/<hôm nay>.md`, `docs/STATE.md` mục *Chờ restart* nếu có.
8. Giao **brain-keeper** sync các module đã đổi (cập nhật "Bẫy đã biết").
9. Báo user: đã sửa gì, test kết quả, cần restart gì (restart khi queue đang chạy → hỏi yes/no), link fix log.
