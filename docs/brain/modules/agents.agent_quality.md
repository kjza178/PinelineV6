# `agents.agent_quality`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `agents/agent_quality.py` · **Lớp:** `agent` · **356 dòng** (331 code) · **6 hàm**, **0 class** · sha1 `7903f24949`

> Deterministic VI quality scan + safe heals (Second Brain L2). Port từ D:\PinelineV2_tmp\_qa_watch_05670.py — không phụ thuộc Gemma.

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.agent_hang_fix](./agents.agent_hang_fix.md), [core.fileio](./core.fileio.md)
- **Được dùng bởi:** [agents.agent_brain](./agents.agent_brain.md), [agents.agent_hang_fix](./agents.agent_hang_fix.md), [agents.agent_qa_gate](./agents.agent_qa_gate.md), [server](./server.md)
- **Test:** `tests/v5/test_agent_quality.py`

```mermaid
flowchart LR
  agents_agent_quality[[agents.agent_quality]]
  agents_agent_brain[agents.agent_brain] --> agents_agent_quality
  agents_agent_hang_fix[agents.agent_hang_fix] --> agents_agent_quality
  agents_agent_qa_gate[agents.agent_qa_gate] --> agents_agent_quality
  server[server] --> agents_agent_quality
  agents_agent_quality --> agents_agent_hang_fix[agents.agent_hang_fix]
  agents_agent_quality --> core_fileio[core.fileio]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `scan_script` | 68 | 105 | `rows` | Trả flags {i,sev,kind,note,zh,vi,rv,mt}. |
| `apply_safe_heals` | 175 | 51 | `rows` | Heal deterministic an toàn tại chỗ. Trả số câu đổi. |
| `summarize_flags` | 228 | 13 | `flags` |  |
| `write_qa_brain` | 243 | 13 | `work_dir, flags, healed` | Ghi work/<job>/qa_brain.json cho gate + UI. |
| `load_qa_brain` | 258 | 9 | `work_dir` |  |
| `scan_and_heal_job` | 269 | 88 | `work_dir, write` | Scan script.json → heal → (high hang) LLM trace/fix → qa_brain.json. |

**File/artifact nhắc tới:** `qa_brain.json`, `script.json`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=5 · file_writes=3

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
