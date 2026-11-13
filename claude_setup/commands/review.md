---
description: Review code (thay đổi gần đây hoặc một module) bằng code-reviewer, kèm bug-hunter cho phần rủi ro
argument-hint: '[module | "recent" | đường dẫn codebak]'
---
Phạm vi: $ARGUMENTS (để trống = "recent": thay đổi từ lần build brain cuối + CHANGELOG hôm nay)

1. Xác định diff: `python scripts/brain_build.py --check`, mục hôm nay của `docs/CHANGELOG.md`, thư mục `Z:\PinelineV5_codebak\<hôm nay>_*`.
2. Giao **code-reviewer** review phạm vi. Song song, nếu thay đổi đụng queue/manifest/thread/subprocess, giao thêm **bug-hunter** chỉ cho các hàm đã đổi.
3. Tổng hợp cho user: Kết luận (✅/⚠️/❌), danh sách blocking, cần restart gì, docs còn thiếu theo `.cursor/rules/docs-on-change.mdc`.
4. Nếu có blocking là bug → hỏi user có muốn chạy `/fix` cho từng mục không.
