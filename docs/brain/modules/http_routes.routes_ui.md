# `http_routes.routes_ui`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `http_routes/routes_ui.py` · **Lớp:** `control` · **116 dòng** (88 code) · **11 hàm**, **0 class** · sha1 `ca7273d307`

> Trang UI: /, /ui3, /ui4, /hongguo, /hongguo2, /films/ui, /studio, /v5-map. /hongguo2 = skin WinUI 2.x (Browse/Ranking/New/Library). /films|/film|/films/ui = films_ui.html · /studio = index3 (Lồng tiếng Studio).

### Phụ thuộc

- **Gọi tới (import nội bộ):** [core.instance](./core.instance.md)
- **Được dùng bởi:** [server](./server.md)
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [restart_server](./restart_server.md)
- **Test:** `tests/test_review2_f4f5.py`, `tests/v5/test_layout_paths.py`
- **Thư viện ngoài:** `fastapi`

```mermaid
flowchart LR
  http_routes_routes_ui[[http_routes.routes_ui]]
  server[server] --> http_routes_routes_ui
  http_routes_routes_ui --> core_instance[core.instance]
```

### API routes (14)

| Method | Path | Handler | Dòng |
|---|---|---|---:|
| GET | `/` | `index` | 43 |
| GET | `/film` | `ui_films` | 101 |
| GET | `/films` | `ui_films` | 101 |
| GET | `/films/ui` | `ui_films` | 101 |
| GET | `/hongguo` | `ui_hongguo` | 87 |
| GET | `/hongguo2` | `ui_hongguo2` | 93 |
| GET | `/index3.html` | `ui3` | 73 |
| GET | `/index4.html` | `ui4` | 80 |
| GET | `/studio` | `ui_studio` | 108 |
| GET | `/studio/ui` | `ui_studio` | 108 |
| GET | `/ui-hg` | `ui_hongguo` | 87 |
| GET | `/ui3` | `ui3` | 73 |
| GET | `/ui4` | `ui4` | 80 |
| GET | `/v5-map` | `ui_v5_map` | 114 |

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `find_index` | 32 | 8 | `` | Tìm index.html ở web/ hoặc ngay cạnh server.py (phòng khi tải file phẳng). |
| `index` | 43 | 9 | `` |  |
| `_localize` | 54 | 7 | `text` | review2: URL cứng V2 (:8801 server / :8802 sidecar) trong HTML cũ → instance này (core.instance). |
| `_html_file` | 63 | 6 | `name` |  |
| `ui3` | 73 | 3 | `` | UI C — Shell vs Data (khung tĩnh, chống giật innerHTML). |
| `ui4` | 80 | 3 | `` | UI D — Full ops: preview + script grid + queue bảng %. |
| `ui_hongguo` | 87 | 3 | `` | UI riêng — điều khiển Hongguo Downloader (search/tải/merge). |
| `ui_hongguo2` | 93 | 3 | `` | Hongguo Downloader 2.x skin (Browse / Ranking / New / Library). |
| `ui_films` | 101 | 3 | `` | Films UI (thư viện phim / mùa mới) — cùng host :8801. |
| `ui_studio` | 108 | 3 | `` | Lồng tiếng Studio — UI3 ops shell. |
| `ui_v5_map` | 114 | 3 | `` | Interactive module/call graph (static HTML; curated nodes). |

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=1

### Lịch sử (từ docs/)

- Fix logs: —
- **Reviews:** [2026-11-11-review-f4-f5](../../reviews/2026-11-11-review-f4-f5.md)
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
