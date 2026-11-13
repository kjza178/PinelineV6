# `agents.agent_core`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `agents/agent_core.py` · **Lớp:** `agent` · **2080 dòng** (1971 code) · **30 hàm**, **2 class** · sha1 `f7df4e82b0`

> agent_core.py — AI Agent nhúng trong Pineline V2. Không gọi HTTP self. Tools gọi runtime đã bind từ server.py (list jobs, queue, manifest, draft YT).

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.agent_av_sync](./agents.agent_av_sync.md), [agents.agent_heal](./agents.agent_heal.md), [agents.agent_hongguo](./agents.agent_hongguo.md), [agents.agent_qa_gate](./agents.agent_qa_gate.md), [agents.agent_stage_io](./agents.agent_stage_io.md), [agents.agent_tool_loop](./agents.agent_tool_loop.md), [agents.agent_watch](./agents.agent_watch.md), [core.batch_config](./core.batch_config.md), [core.config](./core.config.md), [core.film_db](./core.film_db.md), [services.feeder](./services.feeder.md)
- **Được dùng bởi:** [agents.agent_brain](./agents.agent_brain.md), [http_routes.routes_agent](./http_routes.routes_agent.md), [scripts.check_batch4](./scripts.check_batch4.md), [scripts.check_batch5](./scripts.check_batch5.md)
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [restart_server](./restart_server.md)
- **Test:** `tests/v5/test_agent_core_smoke.py`, `tests/v5/test_agent_parser.py`, `tests/v5/test_layout_paths.py`
- **Thư viện ngoài:** `requests`

```mermaid
flowchart LR
  agents_agent_core[[agents.agent_core]]
  agents_agent_brain[agents.agent_brain] --> agents_agent_core
  http_routes_routes_agent[http_routes.routes_agent] --> agents_agent_core
  scripts_check_batch4[scripts.check_batch4] --> agents_agent_core
  scripts_check_batch5[scripts.check_batch5] --> agents_agent_core
  agents_agent_core --> agents_agent_av_sync[agents.agent_av_sync]
  agents_agent_core --> agents_agent_heal[agents.agent_heal]
  agents_agent_core --> agents_agent_hongguo[agents.agent_hongguo]
  agents_agent_core --> agents_agent_qa_gate[agents.agent_qa_gate]
  agents_agent_core --> agents_agent_stage_io[agents.agent_stage_io]
  agents_agent_core --> agents_agent_tool_loop[agents.agent_tool_loop]
  agents_agent_core --> agents_agent_watch[agents.agent_watch]
  agents_agent_core --> core_batch_config[core.batch_config]
  agents_agent_core --> core_config[core.config]
  agents_agent_core --> core_film_db[core.film_db]
  agents_agent_core --> services_feeder[services.feeder]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| **class `Runtime`** | 1070 | 0 method | |  |
| **class `AgentResult`** | 1973 | 0 method | |  |
| `_films_tool` | 902 | 12 | `args` |  |
| `_service_cmd` | 916 | 16 | `kind, rest` | V6 F4: gọi THẲNG services/feeder (trong tiến trình server) — bỏ vòng server → POST sidecar /cmd |
| `_truthy` | 981 | 2 | `v` |  |
| `tool_tier` | 985 | 29 | `name, args` | read / mutate / confirm. confirm = phá huỷ/tốn lâu, cần user bấm ✅ khi gọi từ Telegram. |
| `describe_action` | 1016 | 6 | `name, args` |  |
| `_prune_pending` | 1024 | 3 | `now` |  |
| `park_action` | 1029 | 13 | `name, args` | Giữ tool call lại chờ user xác nhận; trùng (name, args) chưa hết hạn thì dùng lại id cũ. |
| `pending_actions` | 1044 | 4 | `` |  |
| `confirm_action` | 1050 | 14 | `cid, approve` |  |
| `bind` | 1097 | 3 | `runtime` |  |
| `_require_rt` | 1102 | 4 | `` |  |
| `summarize_job` | 1108 | 28 | `j, work_root` |  |
| `_load_script` | 1138 | 8 | `work_root, job_id` |  |
| `_line_brief` | 1148 | 9 | `r` |  |
| `_read_hold` | 1159 | 15 | `work_root, job_id` |  |
| `script_peek` | 1176 | 20 | `work_root, job_id, sample` |  |
| `script_search` | 1198 | 25 | `work_root, job_id, query, missing_only, limit` |  |
| `script_get_lines` | 1225 | 14 | `work_root, job_id, i_from, i_to` |  |
| `classify_jobs` | 1241 | 26 | `jobs, work_root` |  |
| `default_yt_draft` | 1269 | 38 | `work_root, job_id, max_lines` |  |
| `_dispatch` | 1309 | 424 ⚠️ | `name, args, allow_mutate` |  |
| `_llm_cfg` | 1735 | 5 | `` | Cấu hình LLM cho Agent chat (có thể tách khỏi job translate) — config.agent_llm(). |
| `_chat_turn` | 1742 | 22 | `messages, use_tools, tool_choice` | Một turn OpenAI chat/completions. Trả message dict (content + tool_calls). |
| `_chat_turn_model` | 1766 | 35 | `messages, use_tools, model, tool_choice` |  |
| `_chat_turn_text_protocol` | 1803 | 67 | `messages, model` | Fallback khi model không tool-call: ép JSON {tool,args} hoặc {final}. |
| `_parse_pycall_tool_calls` | 1878 | 44 | `content` | Model hay in «Action: `hg_auto_flow(query_or_id="…", auto_run=True)`» thay vì gọi tool. |
| `_parse_text_tool_calls` | 1924 | 36 | `content` | Model qua proxy đôi khi in tool call dạng text (```json [{"name":..,"arguments":..}]```). |
| `clip_tool_result` | 1965 | 5 | `result, limit` |  |
| `run_agent` | 1980 | 96 | `message, history, allow_mutate, max_steps, require_confirm, force_f…` | Chạy agent. history = [{role, content}, ...] (user/assistant only). |
| `status_snapshot` | 2078 | 3 | `` |  |

**Hàm dài ≥120 dòng:** `_dispatch` (424, L1309)

### Config / env keys (3)

`AGENT_TOOL_RESULT_MAX`, `AGENT_WATCH_MAX_JOBS`, `LLM_ENABLE_THINKING`

**File/artifact nhắc tới:** `manifest.json`, `output.mp4`, `script.json`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=5 · global_stmt=1 · swallow_except=1

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
