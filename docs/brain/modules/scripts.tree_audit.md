# `scripts.tree_audit`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `scripts/tree_audit.py` · **Lớp:** `ops` · **226 dòng** (199 code) · **9 hàm**, **0 class** · sha1 `be425218b3`

> tree_audit.py — Kiểm tra sức khoẻ cây code Pineline (V2/V4/V5) sau mỗi lần sync/port. Chỉ đọc (AST + hash), không import, không chạy code. Bắt các lỗi kiểu: • import không resolve được trong cây này  → server/sidecar khô

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** —
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [scripts.sync_from_v2](./scripts.sync_from_v2.md), [scripts.verify_all](./scripts.verify_all.md)
- **Test:** ❌ chưa có test import trực tiếp

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `md5` | 51 | 5 | `p` |  |
| `py_files` | 58 | 15 | `root` |  |
| `module_exists` | 75 | 12 | `root, base_dir, dotted` | Có resolve được `dotted` từ sys.path = [thư mục script, ROOT] không. |
| `guarded` | 89 | 10 | `node, parents` | Import nằm trong try/except (ImportError/Exception) hay trong hàm → coi là tuỳ chọn. |
| `audit_imports` | 101 | 29 | `root` | (level, file, line, module, kind) — ERROR: import top-level không resolve; WARN: trong hàm/try. |
| `audit_ports` | 132 | 18 | `root` |  |
| `audit_duplicates` | 152 | 13 | `root` |  |
| `audit_peers` | 167 | 16 | `root, peers` |  |
| `main` | 185 | 38 | `` |  |

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=1 · file_writes=1 · swallow_except=1

### Lịch sử (từ docs/)

- **Fix logs (1):**
  - [2026-11-11 — F4 (services + sidecar thuần) + F5 (render tiến trình con)](../../fixes/2026-11-11-f4-f5.md)
- **Reviews:** [2026-11-11-review-f4-f5](../../reviews/2026-11-11-review-f4-f5.md), [2026-11-11-review-f2-f3](../../reviews/2026-11-11-review-f2-f3.md)
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
