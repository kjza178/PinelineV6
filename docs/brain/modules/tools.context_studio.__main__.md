# `tools.context_studio.__main__`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `tools/context_studio/__main__.py` · **Lớp:** `misc` · **67 dòng** (54 code) · **1 hàm**, **0 class** · sha1 `3035da499e`

> CLI: python -m tools.context_studio analyze <srt|asr.json> -o work/<job> Chỉ PRE-SCAN — không dịch.

### Phụ thuộc

- **Gọi tới (import nội bộ):** [tools.context_studio.analyzer](./tools.context_studio.analyzer.md), [tools.context_studio.character_mapper](./tools.context_studio.character_mapper.md), [tools.context_studio.exporter](./tools.context_studio.exporter.md), [tools.context_studio.schema](./tools.context_studio.schema.md), [tools.context_studio.srt_io](./tools.context_studio.srt_io.md)
- **Được dùng bởi:** —
- **Test:** ❌ chưa có test import trực tiếp

```mermaid
flowchart LR
  tools_context_studio___main__[[tools.context_studio.__main__]]
  tools_context_studio___main__ --> tools_context_studio_analyzer[tools.context_studio.analyzer]
  tools_context_studio___main__ --> tools_context_studio_character_mapper[tools.context_studio.character_mapper]
  tools_context_studio___main__ --> tools_context_studio_exporter[tools.context_studio.exporter]
  tools_context_studio___main__ --> tools_context_studio_schema[tools.context_studio.schema]
  tools_context_studio___main__ --> tools_context_studio_srt_io[tools.context_studio.srt_io]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `main` | 20 | 44 | `argv` |  |

### Lịch sử (từ docs/)

- **Fix logs (1):**
  - [2026-11-11 — F2 (core/job_opts) + F3 tích hợp (1 tiến trình V5+V6, UI cũ hết polling)](../../fixes/2026-11-11-f2-f3-integration.md)
- **Reviews:** [2026-11-11-review-f2-f3](../../reviews/2026-11-11-review-f2-f3.md)
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
