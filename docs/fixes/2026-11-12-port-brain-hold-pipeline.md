# Port V2 → V6: Second Brain (tin Telegram theo mẫu + HOLD) + luồng dịch theo job + lệnh Telegram = chạy tay (agent P6, 2026-11-12)

- **Nguồn (chỉ đọc):** `Z:\PinelineV2` — agent G (`docs/fixes/2026-11-12-brain-telegram-hold.md`), H
  (`2026-11-12-brain-notify-v2.md` mục A–C), I2 (cùng file, mục D), F2 (`2026-11-12-translate-simple-default.md`).
  Diff gốc từ `.backup_edit_20261112_agent{G,H,I2,F2}\`.
- **Cách port:** 3-way merge (base = backup V2 trước sửa, V2 = bản sau sửa, đích = V6) cho `agent_brain`, `agent_heal`,
  `agent_watch`, `agent_hold` (I2), `routes_agent`, `agent_autopilot`, `agent_tele_cmd`; xung đột/import phẳng sửa tay theo
  luật V6 (package `agents.*`, `core.*`; ghi manifest qua `state/job_state`). Server: vá tay đúng chỗ V6.
- **Backup:** `Z:\PinelineV6\.backup_edit_20261112_agentP6\` (cùng đường dẫn tương đối, `_md5_before.txt`, `_md5_after.txt`).
- **Status:** trên đĩa V6. Có hiệu lực khi (re)start backend V6. Instance `v6` vẫn TẮT agent_brain/agent_watch/telegram →
  brain/watcher/tin Telegram chỉ chạy ở instance bật cờ (vd `prod` khi cutover). Gỡ HOLD khi chạy tay + đóng dấu luồng dịch
  chạy ở mọi instance.

## Đã port — đặt ở đâu

| Việc | V6 |
|---|---|
| Tin Telegram dựng bằng mẫu (1 khối/job, emoji + tên phim + P<n> + id; ≤ 3 dòng; khử lặp; văn LLM chỉ vào log) | MỚI `agents/agent_brain_notify.py`; `agents/agent_brain.py` (`_emit`, `_tg_flush_tick`, `detect_stage_events`, `tool_outcome`…) |
| Mặc định `AGENT_BRAIN_TELEGRAM=events`, `AGENT_BRAIN_TG_SEC=600` (sàn 600) | code `agents/agent_brain.py` + `config.json`/`pineline.config.json` (2 dòng, như V2 G) — cơ chế `core.config` của V6 |
| Gỡ HOLD cũ tất định (job xong mà còn cờ) + rule CLEAR | `agent_brain.clear_stale_holds` / rule path → `state.job_state.try_mutate` / `js.mutate` (bớt 1 `js.persist`) |
| Deep tick chỉ job failed/held/high/pending; bằng chứng tool trong prompt | `agents/agent_brain.py` |
| `heal_job` ghi `note` khi plan safe rỗng; `job_active` (queue.current — V6 `core.job_queue` — / engine sống / claim) → `skipped` | `agents/agent_heal.py`; hook `job_active` trong `http_routes/routes_agent._HEAL_HOOKS` |
| Hoãn heal 180 s sau start (`AGENT_HEAL_START_DELAY_SEC`) | `agents/agent_watch._loop`, brain bỏ heal/LLM tick trong thời gian hoãn |
| Gỡ HOLD khi người CHẠY TAY | MỚI `agents/agent_hold.py`; `server._manual_hold_override` gọi sau claim ở `run_stage`, `translate_fill`, `regen_tts`, `retranslate_job`, `mux_job` (`request: Request = None`) |
| Brain/auto không HOLD lại job đã override (trừ high QA mới) | `server._auto_gpu` (qa_brain), `routes_agent._agent_set_hold`, `set_hold` runtime brain (nay qua `js.try_mutate`, bỏ `write_text` thô) |
| Đuôi log engine khi lỗi | `server._run_engine` + `_save_engine_tail` (qua `js.try_mutate`) → `engine_fail_<script>.log` + `stages.<asr|tts|ocr|separate>.engine_tail…`; **V6 thêm** `_render_subprocess` (workers/render_worker) → `engine_fail_render_worker.log` + `stages.mux.engine_tail` |
| I2: lệnh Telegram/chat Agent của NGƯỜI = chạy tay | `agent_hold.user_context/manual_source/request_source`; `routes_agent`: `_agent_user_ctx` bọc `api_agent_chat`/`api_agent_confirm`, `_as_auto` bọc `_HEAL_HOOKS`/`_AV_SYNC_HOOKS`, `_agent_run_stage` không gỡ HOLD trước claim khi là lệnh người (+ `_agent_mark_qa_override` qua `js.try_mutate`); `agents/agent_autopilot.caller_headers`; `agents/agent_tele_cmd` gắn `X-Pineline-Caller: user`, `X-Pineline-Source: telegram` |
| `state.job_state.try_mutate(wd, fn) -> bool` (MỚI) | như `mutate` nhưng trả đã-ghi/không, không raise — cho logic agent «ghi nếu cần» |
| F2: luồng dịch THEO JOB | `engines/translate_simple/cfg.py`: `job_pipeline(wd, fresh=)`, `pipeline_default()` (= mặc định **instance**, `translate_pipeline()`), `_infer_job_pipeline`; đóng dấu `manifest.translate_pipeline{,_why,_at}` qua `js.try_mutate`. `server._translate_pipeline_simple(wd=None, *, fresh=False)`; mọi đường gọi dịch truyền `work_dir` |

**Đường gọi dịch (V6):** `_process_gpu`, `_auto_gpu` → luồng của job; `_run_stage("translate")` force = fresh;
`_translate_flow` (Dịch / Soát lại) theo job, «Dịch LẠI» (force, không review_only) = mặc định instance; `retranslate_job`
chốt luồng TRƯỚC khi đánh running; `translate_fill`: job simple → `pipeline.run` không force (trả `pipeline`), job cũ →
`run_translate` lô 25; `_run_requested_review` bước văn phong: simple `rereview=True` / cũ `run_review(force=True)`.
Job dở luồng cũ (script.json / stage translate đã chạy, chưa đóng dấu) → **legacy, làm nốt luồng cũ**.

**SSE:** không thêm loại event mới (V6 chưa có `hold.set/cleared`). Mọi ghi HOLD đi qua `Manifest.update` → hook
`state.index` → `job.updated` (có `hold_reason`) → UI cập nhật qua `/api/events`, không polling.

## Test (bản sao cloud `/home/claude/agentP6`, `AGENT_BRAIN=0`)

- Mới: `tests/test_agent_brain_events.py` (11), `tests/test_agent_brain_notify.py` (28 V2 + 2 V6: render worker tail,
  `_manual_hold_override` → index), `tests/test_agent_hold_telegram.py` (I2, 14), `test_translate_simple.py` +6 `test_p6_*`.
- Sửa kỳ vọng: `test_translate_simple.py` 2 ca (job có script.json phải đóng dấu simple / job mới) — như V2 F2;
  `test_f6_port_simple_demucs.test_server_branches_present` (đếm `_translate_pipeline_simple(work_dir|wd…)` ≥ 6);
  `test_f1_ratchet.PERSIST_BASELINE` 93 → **91** (đếm thật 92 → 91).
- `tests/test_translate_simple_next.py`: `_VOLATILE` đã có `applied_at`, glossary.json đã qua `_strip` (port F6) — không sửa.
- Toàn bộ `tests/` (cloud thiếu fastapi): trước 440 pass / 13 fail / 12 error → sau **494 / 13 / 12**, cùng danh sách fail.
  Với stub fastapi/yt_dlp/soundfile CHỈ ở cloud (ngoài cây): trước 501 / 10 / 6 → sau **562 / 10 / 6**, cùng danh sách.
- `scripts/verify_all.py --dry`: PASS; `tree_audit`: ERROR=0 (import=23, dups=1 — như trước).

## Lệnh user (Windows)

```
cd /d Z:\PinelineV6
set AGENT_BRAIN=0 && python -m pytest -q tests\test_agent_brain_events.py tests\test_agent_brain_notify.py tests\test_agent_hold_telegram.py tests\test_translate_simple.py tests\test_f6_port_simple_demucs.py tests\test_f1_ratchet.py
verify_all.bat
```

Recheck (khi chạy instance có brain/telegram): giống V2 H — `agent_brain.log` có `brain start … tg=events heal_delay=180s`,
`agent_watch.log` có `hoãn heal 180s sau khởi động`; bấm chạy tay TTS/mux (UI hoặc Telegram) trên job HOLD → manifest có
`agent.hold_override`; job đã dịch dở luồng cũ → `translate_pipeline=legacy, _why=inferred`.

## Giả định / còn lại

- Đặt module mới ở `agents/` (cùng gói brain/heal/watch). Không thêm khoá instance cho Telegram brain: dùng `config.json`
  như V2 (instance chỉ bật/tắt telegram).
- Không port `_ensure_brain_runtime` khác ngoài set_hold; `routes_agent._agent_set_hold` vẫn `js.persist` (nợ ratchet cũ).
- Không thêm event bus `hold.*` (chưa có ở V6) — `job.updated` đủ cho UI.
- Chưa chạy job thật / LLM / GPU trên V6.
