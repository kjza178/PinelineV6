---
description: Đồng bộ Second Brain với code hiện tại (build tự động + cập nhật phần viết tay)
argument-hint: '[enrich N | module1,module2 | để trống]'
---
Tham số: $ARGUMENTS

Giao **brain-keeper**:
- Để trống → quy trình "sync": `--check`, build, cập nhật MANUAL cho module đổi hành vi theo CHANGELOG/worklog/fix log hôm nay.
- `enrich N` → viết phần MANUAL cho N thẻ chưa có, ưu tiên theo `docs/brain/generated/hotspots.md`.
- Danh sách module → build `--only` và viết lại MANUAL các module đó.

Sau đó báo user: file đã cập nhật, thẻ còn thiếu, mâu thuẫn code ↔ docs phát hiện được.
