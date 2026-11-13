# `scripts.brain_build`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `scripts/brain_build.py` · **Lớp:** `ops` · **857 dòng** (726 code) · **23 hàm**, **2 class** · sha1 `0494d084d7`

> brain_build.py — Sinh/làm mới phần TỰ ĐỘNG của Second Brain (docs/brain/). Đọc mã nguồn bằng AST (không import, không chạy code) → viết: docs/brain/modules/<module>.md     thẻ module: AUTO (sinh lại) + MANUAL (giữ nguyên

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** —
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [scripts.brain_export](./scripts.brain_export.md)
- **Test:** ❌ chưa có test import trực tiếp

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| **class `Fn`** | 110 | 1 method | |  |
| **class `Mod`** | 124 | 0 method | |  |
| `layer_of` | 156 | 6 | `name` |  |
| `_sub_dirs` | 164 | 19 | `` | Thư mục con chứa code: SUB_PACKAGES + mọi thư mục cấp 1 có .py (bố cục package V5). |
| `discover` | 185 | 12 | `` |  |
| `mod_name` | 199 | 4 | `p` |  |
| `_const_str` | 205 | 4 | `node` |  |
| `_first_line` | 211 | 5 | `doc, n` |  |
| `_args` | 218 | 10 | `fn` |  |
| `_call_name` | 230 | 7 | `func` |  |
| `analyze` | 239 | 132 ⚠️ | `p, known` |  |
| `_add_import` | 373 | 8 | `m, dotted, known` |  |
| `link_docs` | 385 | 39 | `mods` |  |
| `card_path` | 428 | 2 | `name` |  |
| `link` | 432 | 4 | `name, from_dir` |  |
| `mermaid_id` | 438 | 2 | `name` |  |
| `render_auto` | 442 | 111 | `m, mods` |  |
| `write_card` | 555 | 17 | `m, mods, stamp` |  |
| `manual_filled` | 574 | 7 | `name` |  |
| `hotspot_score` | 583 | 10 | `m` |  |
| `write_generated` | 595 | 145 ⚠️ | `mods, stamp` |  |
| `load_all` | 744 | 27 | `` |  |
| `cmd_check` | 773 | 21 | `mods` |  |
| `cmd_module` | 796 | 15 | `mods, name` |  |
| `main` | 813 | 41 | `` |  |

**Hàm dài ≥120 dòng:** `write_generated` (145, L595), `analyze` (132, L239)

**File/artifact nhắc tới:** `_meta.json`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=4 · file_writes=10 · swallow_except=2

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
