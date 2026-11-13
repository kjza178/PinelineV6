# `scripts.check_batch2`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `scripts/check_batch2.py` · **Lớp:** `ops` · **327 dòng** (262 code) · **9 hàm**, **1 class** · sha1 `dced6717a7`

> Kiểm thử đợt 2 (bảo mật + config 1 nguồn + LLM lỗi dừng stage). KHÔNG chạm server/sidecar live: middleware & handler được tách bằng AST rồi exec vào app/handler giả.

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.hongguo_bridge](./agents.hongguo_bridge.md), [core.config](./core.config.md), [engines.llm](./engines.llm.md), [engines.translate](./engines.translate.md)
- **Được dùng bởi:** —
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `fastapi`, `redact`, `requests`

```mermaid
flowchart LR
  scripts_check_batch2[[scripts.check_batch2]]
  scripts_check_batch2 --> agents_hongguo_bridge[agents.hongguo_bridge]
  scripts_check_batch2 --> core_config[core.config]
  scripts_check_batch2 --> engines_llm[engines.llm]
  scripts_check_batch2 --> engines_translate[engines.translate]
```

### API routes (2)

| Method | Path | Handler | Dòng |
|---|---|---|---:|
| GET | `/api/x` | `_x` | 60 |
| GET | `/ui` | `_ui` | 65 |

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| **class `R`** | 190 | 3 method | |  |
| `check` | 20 | 8 | `name, cond` |  |
| `extract` | 30 | 11 | `path, names` |  |
| `_x` | 60 | 2 | `` |  |
| `_ui` | 65 | 2 | `` |  |
| `mk` | 106 | 10 | `headers, path` |  |
| `post_ok` | 203 | 3 | `url, headers, json, timeout` |  |
| `post_400` | 226 | 3 | `url, headers, json, timeout` |  |
| `boom` | 257 | 3 | `*a, **k` |  |
| `parse_err` | 270 | 4 | `rows_, *a, **k` |  |

**Cổng nhắc tới:** 11434, 8801, 8802

**File/artifact nhắc tới:** `config.json`

**Tín hiệu rủi ro (đếm tĩnh):** eval_exec=2 · global_stmt=1

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
