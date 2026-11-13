# `scripts.brain_export`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `scripts/brain_export.py` · **Lớp:** `ops` · **67 dòng** (57 code) · **2 hàm**, **0 class** · sha1 `55af9d6b27`

> brain_export.py — Xuất dữ liệu Second Brain ra JSON gọn cho trang workflow (docs/brain/workflow.html). python scripts/brain_export.py                 # → docs/brain/generated/brain.json python scripts/brain_export.py --o

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** —
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [scripts.build_workflow_html](./scripts.build_workflow_html.md)
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `brain_build`

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `export` | 21 | 31 | `tree_name` |  |
| `main` | 54 | 10 | `` |  |

**File/artifact nhắc tới:** `brain.json`

**Tín hiệu rủi ro (đếm tĩnh):** file_writes=1

### Lịch sử (từ docs/)

- Fix logs: —
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
