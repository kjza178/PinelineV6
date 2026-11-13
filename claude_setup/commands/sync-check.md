---
description: Kiểm tra sức khoẻ cây code + kế hoạch sync an toàn giữa V2/V4/V5 (port-sync-guardian)
argument-hint: '[nguồn → đích, vd "V2 → V5" | để trống = cây hiện tại]'
---
Phạm vi: $ARGUMENTS

1. Chạy `python scripts/tree_audit.py --peers Z:\PinelineV5 Z:\PinelineV4 Z:\PinelineV5 --write` ở cây hiện tại và báo số ERROR.
2. Giao **port-sync-guardian** lập báo cáo sync (bảng ánh xạ file, file riêng từng cây, thứ tự, kiểm tra sau).
3. Trình bày cho user: lỗi ERROR hiện có, kế hoạch sync, câu hỏi cần quyết. **Không chép file / sửa code** khi user chưa duyệt.
