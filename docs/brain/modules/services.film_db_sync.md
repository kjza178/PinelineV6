# `services.film_db_sync`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `services/film_db_sync.py` · **Lớp:** `publish` · **35 dòng** (29 code) · **2 hàm**, **0 class** · sha1 `ce63d3db02`

> services/film_db_sync.py — đồng bộ DB phim định kỳ (sidecar cũ: filmdb_loop) — V6 F4.

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.agent_feeder](./agents.agent_feeder.md), [core.film_db](./core.film_db.md), [core.instance](./core.instance.md)
- **Được dùng bởi:** [agents.agent_tele_cmd](./agents.agent_tele_cmd.md), [services.background](./services.background.md)
- **Test:** ❌ chưa có test import trực tiếp

```mermaid
flowchart LR
  services_film_db_sync[[services.film_db_sync]]
  agents_agent_tele_cmd[agents.agent_tele_cmd] --> services_film_db_sync
  services_background[services.background] --> services_film_db_sync
  services_film_db_sync --> agents_agent_feeder[agents.agent_feeder]
  services_film_db_sync --> core_film_db[core.film_db]
  services_film_db_sync --> core_instance[core.instance]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `sync_once` | 10 | 11 | `base` |  |
| `loop` | 23 | 13 | `stop, base, sec` |  |

### Config / env keys (1)

`FILMDB_SEC`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=2

### Lịch sử (từ docs/)

- **Fix logs (1):**
  - [2026-11-11 — F4 (services + sidecar thuần) + F5 (render tiến trình con)](../../fixes/2026-11-11-f4-f5.md)
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
