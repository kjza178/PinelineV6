# `scripts.f1_convert_saves`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `scripts/f1_convert_saves.py` · **Lớp:** `ops` · **96 dòng** (79 code) · **5 hàm**, **0 class** · sha1 `d40d6f6328`

> F1 (2026-11-11): chuyển mọi Manifest.save() sang state.job_state. Chạy 1 lần; idempotent.

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** —
- **Test:** ❌ chưa có test import trực tiếp

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `block` | 12 | 9 | `src, start_pat, end_pat, after` | Tìm khối từ dòng chứa start_pat tới hết dòng chứa end_pat (sau vị trí `after`). |
| `replace_block` | 23 | 4 | `src, start_pat, end_pat, new_line, after` |  |
| `server_semantic` | 29 | 30 | `src` |  |
| `add_import` | 64 | 11 | `src` |  |
| `convert` | 77 | 10 | `path` |  |

**Tín hiệu rủi ro (đếm tĩnh):** file_writes=1

### Lịch sử (từ docs/)

- **Fix logs (1):**
  - [2026-11-11 — F1 (B9): mọi ghi manifest qua state/](../../fixes/2026-11-11-f1-b9-single-write-path.md)
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
