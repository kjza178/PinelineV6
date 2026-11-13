# `scripts.fix_runall_decorator_v2v4v5`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `scripts/fix_runall_decorator_v2v4v5.py` · **Lớp:** `ops` · **30 dòng** (29 code) · **0 hàm**, **0 class** · sha1 `811104340d`

> Sửa decorator /run-all bị gắn nhầm vào _req_audio (lỗi do fix B6 ngày 11-11) ở V2/V4/V5. Chỉ sửa file server.py (có backup); server đang chạy KHÔNG bị ảnh hưởng tới lần restart sau. Chạy:  python Z:\PinelineV6\scripts\fi

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** —
- **Test:** ❌ chưa có test import trực tiếp

**Tín hiệu rủi ro (đếm tĩnh):** file_writes=1

### Lịch sử (từ docs/)

- Fix logs: —
- **Reviews:** [2026-11-11-review-f2-f3](../../reviews/2026-11-11-review-f2-f3.md)
- Nhắc trong CHANGELOG: **0** lần · DECISIONS: **0** lần

<!-- AUTO:END -->

<!-- MANUAL:START -->
## Vai trò (viết tay)

_Chưa viết. 2–4 câu: module này chịu trách nhiệm gì trong pipeline, ai gọi nó, nó KHÔNG làm gì._

## Luồng chính

_Các hàm vào (entry) → bước chính → output/side effect. Ghi tên hàm thật._

## Bất biến & hợp đồng

_Điều luôn phải đúng (vd: chỉ ghi manifest qua `Manifest.update`; 1 job / ghế). Vi phạm = bug._

## Bẫy đã biết

_Lỗi từng gặp + link `docs/fixes/...`. Những chỗ dễ sửa sai._

## Ghi chú vận hành

_Config key quan trọng, cần restart gì khi sửa (:8801 / :8802 / hot-reload), cách test._
<!-- MANUAL:END -->
