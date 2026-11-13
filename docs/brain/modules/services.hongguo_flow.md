# `services.hongguo_flow`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `services/hongguo_flow.py` · **Lớp:** `source` · **13 dòng** (10 code) · **1 hàm**, **0 class** · sha1 `e1efa898c1`

> services/hongguo_flow.py — điểm vào Hongguo cho server (V6 F4, khung). Mục tiêu (thiết kế 3.4): `http_routes/routes_hg.py` gọi `services.hongguo_flow.bridge()` thay cho ~43 chỗ `importlib.reload(agents.hongguo_bridge)`. 

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.hongguo_bridge](./agents.hongguo_bridge.md)
- **Được dùng bởi:** —
- **Test:** ❌ chưa có test import trực tiếp

```mermaid
flowchart LR
  services_hongguo_flow[[services.hongguo_flow]]
  services_hongguo_flow --> agents_hongguo_bridge[agents.hongguo_bridge]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `bridge` | 10 | 4 | `` | Module agents.hongguo_bridge (import thường, không reload). |

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
