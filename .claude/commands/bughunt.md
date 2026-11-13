---
description: Săn bug có bằng chứng (bug-hunter) rồi xin phương án sửa (fix-advisor) cho các bug P0/P1
argument-hint: '[module | khu vực | "toàn bộ" | mô tả triệu chứng]'
---
Phạm vi: $ARGUMENTS (để trống = top 5 module trong docs/brain/generated/hotspots.md)

1. Chạy `python scripts/brain_build.py --check`; nếu có module đổi thì chạy `python scripts/brain_build.py` để thẻ module đúng code hiện tại.
2. Giao subagent **bug-hunter** săn bug trong phạm vi trên. Nếu phạm vi rộng (>3 module lớn), chia thành nhiều bug-hunter chạy song song, mỗi agent một nhóm module.
3. Với mỗi finding P0/P1 có độ tin cậy CONFIRMED hoặc LIKELY: giao **fix-advisor** (có thể song song, mỗi bug một agent) để có root cause + phương án + fix log nháp.
4. Trình bày cho user: bảng bug (mức, tin cậy, file:dòng, 1 câu) + phương án khuyến nghị cho từng bug + câu hỏi cần quyết. **Không sửa code** cho tới khi user duyệt.
5. Ghi 1 mục vào `docs/worklog/<hôm nay>.md` (yêu cầu → đã làm → link báo cáo `docs/reviews/...`).
