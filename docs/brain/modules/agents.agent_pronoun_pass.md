# `agents.agent_pronoun_pass`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `agents/agent_pronoun_pass.py` · **Lớp:** `agent` · **257 dòng** (232 code) · **4 hàm**, **0 class** · sha1 `82b5b2e5cd`

> Rà XƯNG HÔ trong bản dịch theo glossary.pronouns + genre. 1. Quét deterministic (cùng apply_pronoun_postpass) rồi 2. Các câu còn nghi (Ta/Ngươi lệch genre, hoặc cặp who+to có mặt mà self/call lệch)

### Phụ thuộc

- **Gọi tới (import nội bộ):** [core.config](./core.config.md), [core.manifest](./core.manifest.md), [engines.llm](./engines.llm.md), [engines.pronoun_register_policy](./engines.pronoun_register_policy.md), [engines.subs](./engines.subs.md), [engines.translate](./engines.translate.md), [state.job_state](./state.job_state.md)
- **Được dùng bởi:** [engines.pronoun_consistency](./engines.pronoun_consistency.md), [server](./server.md)
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `state`

```mermaid
flowchart LR
  agents_agent_pronoun_pass[[agents.agent_pronoun_pass]]
  engines_pronoun_consistency[engines.pronoun_consistency] --> agents_agent_pronoun_pass
  server[server] --> agents_agent_pronoun_pass
  agents_agent_pronoun_pass --> core_config[core.config]
  agents_agent_pronoun_pass --> core_manifest[core.manifest]
  agents_agent_pronoun_pass --> engines_llm[engines.llm]
  agents_agent_pronoun_pass --> engines_pronoun_register_policy[engines.pronoun_register_policy]
  agents_agent_pronoun_pass --> engines_subs[engines.subs]
  agents_agent_pronoun_pass --> engines_translate[engines.translate]
  agents_agent_pronoun_pass --> state_job_state[state.job_state]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_fold` | 23 | 3 | `s` |  |
| `find_pronoun_issues` | 28 | 63 | `rows, plan` |  |
| `_fix_batch` | 93 | 36 | `chunk, plan_block, model, system` |  |
| `run_pronoun_pass` | 131 | 127 ⚠️ | `work_dir, model` |  |

**Hàm dài ≥120 dòng:** `run_pronoun_pass` (127, L131)

**File/artifact nhắc tới:** `glossary.json`, `script.json`, `sub.vi.srt`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=8 · file_writes=3 · swallow_except=3

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
