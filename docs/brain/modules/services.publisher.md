# `services.publisher`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `services/publisher.py` · **Lớp:** `misc` · **26 dòng** (20 code) · **2 hàm**, **0 class** · sha1 `2586f0a6b0`

> services/publisher.py — worker tự đăng YouTube (agents.auto_publisher) — V6 F4. Trước: worker chạy trong sidecar, còn server chỉ `enqueue_publish`. Nay server tự chạy worker khi instance bật CẢ `feeder` (server sở hữu vi

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.auto_publisher](./agents.auto_publisher.md)
- **Được dùng bởi:** [services.background](./services.background.md)
- **Test:** `tests/test_f4_services.py`

```mermaid
flowchart LR
  services_publisher[[services.publisher]]
  services_background[services.background] --> services_publisher
  services_publisher --> agents_auto_publisher[agents.auto_publisher]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_flag` | 11 | 2 | `name` |  |
| `start_worker` | 15 | 12 | `` | Trả True nếu đã khởi động. Cờ auto_publish tắt (hoặc không có env instance) → không làm gì. |

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=1

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
