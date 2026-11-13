# `pineline.server.routes.queue`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `pineline/server/routes/queue.py` · **Lớp:** `misc` · **38 dòng** (29 code) · **1 hàm**, **0 class** · sha1 `d45810b0a6`

> Queue API: /api/queue/* (+ alias /api/jobs/group) — LIVE registry. Thân handler giữ trong root `server.py` (cùng pattern jobs.attach). Module này chỉ đăng ký path → hàm trên server module.

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** —
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [pineline.server.routes.jobs](./pineline.server.routes.jobs.md)
- **Test:** `tests/v5/test_queue_http_state.py`
- **Thư viện ngoài:** `fastapi`

```mermaid
flowchart LR
  pineline_server_routes_queue[[pineline.server.routes.queue]]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `attach` | 29 | 7 | `server_mod` | Gắn handler từ server module lên router; gọi trước include_router. |

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
