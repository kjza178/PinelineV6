# `runtime.context`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `runtime/context.py` · **Lớp:** `misc` · **79 dòng** (64 code) · **0 hàm**, **1 class** · sha1 `28befb90b7`

> runtime/context.py — State toàn cục của 1 tiến trình server, gom về một object. Thay các biến module trong server.py cũ (`_STOP_JOBS`, `_STAGE_BUSY`, `_PROCS`, pool `_dl`/`_gpu`). Route nhận Runtime qua dependency → khôn

### Phụ thuộc

- **Gọi tới (import nội bộ):** [core.instance](./core.instance.md)
- **Được dùng bởi:** [app.main](./app.main.md)
- **Test:** `tests/test_runtime.py`

```mermaid
flowchart LR
  runtime_context[[runtime.context]]
  app_main[app.main] --> runtime_context
  runtime_context --> core_instance[core.instance]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| **class `Runtime`** | 17 | 8 method | |  |

**Tín hiệu rủi ro (đếm tĩnh):** file_writes=1 · pools=2

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
