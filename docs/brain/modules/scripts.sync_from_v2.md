# `scripts.sync_from_v2`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `scripts/sync_from_v2.py` · **Lớp:** `ops` · **370 dòng** (310 code) · **12 hàm**, **0 class** · sha1 `0d11cb9982`

> sync_from_v2.py — Đồng bộ code từ cây nguồn (V2, bố cục phẳng) sang cây đích. Thay cho việc chép tay file root ("Sync V2/V4/V5") — cách đã làm V5 hỏng 11/2026. • Đích PHẲNG (V4): chép file runtime có trong allowlist, giữ

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** —
- **Test:** ❌ chưa có test import trực tiếp

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `md5` | 67 | 2 | `p` |  |
| `read` | 71 | 6 | `p` |  |
| `write` | 79 | 5 | `p, text, crlf, bom` |  |
| `is_package_tree` | 86 | 2 | `root` |  |
| `package_index` | 90 | 12 | `root` | stem → [đường dẫn tương đối trong package] (một tên có thể ở nhiều chỗ). |
| `home_for` | 104 | 5 | `stem` |  |
| `_line_offsets` | 113 | 6 | `text` |  |
| `rewrite_imports` | 121 | 55 | `text, dotted` | import phẳng → package theo `dotted` (stem → 'core.manifest'). Trả (text, số chỗ đổi). |
| `rewrite_file_paths` | 182 | 23 | `text, depth` | Module ở root được chuyển xuống `depth` cấp: thư mục của file == gốc dự án → giữ ý nghĩa đó. |
| `add_bootstrap` | 210 | 27 | `text, depth` | Cho phép `python engines/asr/x.py` import `core.*` khi chạy trực tiếp. |
| `build_plan` | 241 | 24 | `src, dst, only` |  |
| `main` | 267 | 100 | `` |  |

**File/artifact nhắc tới:** `sync_report.json`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=1 · file_writes=2 · swallow_except=1

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
