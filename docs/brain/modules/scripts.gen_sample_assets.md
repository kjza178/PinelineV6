# `scripts.gen_sample_assets`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `scripts/gen_sample_assets.py` · **Lớp:** `ops` · **108 dòng** (88 code) · **4 hàm**, **0 class** · sha1 `98f843b4dc`

> Tạo sample assets cho Pineline (grain / LUT / BGM). Chạy:  python scripts/gen_sample_assets.py - grain.png  : film grain loop (overlay)

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** —
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `PIL`

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_write_grain` | 28 | 14 | `path, size` |  |
| `_write_cube` | 44 | 28 | `path, n` | LUT soft contrast + hơi ấm (không phá da/anime). |
| `_write_bgm` | 74 | 23 | `path, dur` |  |
| `main` | 99 | 6 | `` |  |

**Tín hiệu rủi ro (đếm tĩnh):** file_writes=1 · subprocess=1

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
