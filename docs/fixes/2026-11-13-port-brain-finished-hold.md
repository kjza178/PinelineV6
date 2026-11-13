# 2026-11-13 — Port V2 → V6: brain bỏ job đã xong + phân loại tạm dừng (park ≠ HOLD cần người) — agent R6

Nguồn: V2 agent N — `Z:\PinelineV2\docs\fixes\2026-11-13-brain-finished-hold.md` (bảng vòng đời tạm dừng đầy đủ ở đó).
Base diff = `Z:\PinelineV2\.backup_edit_20261113_agentN\` → V2 hiện tại (md5 khớp `_md5_after.txt` của agent N).
Backup V6: `.backup_edit_20261113_agentR6\` (`_md5_before.txt` = md5 lúc đọc, `_md5_after.txt`).

## Triệu chứng (V2, cùng code ở V6)
- Job ĐÃ XONG (đã đăng / parent đã ghép Full / script.json, tts.json, asr.json đã dọn) bị `diagnose_job` bắt `asr_bad` +
  `tts_not_started` → heal `tts_run` → «chưa dịch xong» → Telegram «🩹 heal thất bại».
- Park tự động (simple/B2 `held_at=waiting_review`, parent `split_parts`) báo «Đã đặt HOLD … Cần bạn»; park → `review_failed`
  (cần người) lại không có tin; «Chạy tiếp» để lại `review_retries` > max → lần park kế nhảy thẳng `review_failed`.

## Sửa (merge 3 chiều trên nền V6 đã có brain G+H+I2 của P6 và gói K/L/M của Q6)
| File V6 | Thay đổi |
|---|---|
| `agents/agent_hold.py` | MỚI `job_finished` (một định nghĩa), `merged_full`, `hold_class`/`hold_info`/`AUTO_PARK`/`NEED_BY_HOLD`/`PARK_KEYS`/`review_retry_max`; `manual_override` gỡ thêm khoá park (trong `js.try_mutate` có sẵn). |
| `agents/agent_heal.py` | `diagnose_job`/`heal_job`/`_job_needs_attention` bỏ job xong; 3 detector gốc (`asr_bad`, `tts_not_started`, `tts_missing_clips`). |
| `agents/agent_brain.py` | `job_finished` uỷ `agents.agent_hold`; `sense_jobs`, `apply_actions(heal_job)` (hunk vá tay — V6 import `agents.agent_heal`), `_deep_focus`, nhắc lỗi bỏ job xong; `_hold_event` → `park_set`/`hold_set`; `clear_stale_holds` gỡ khoá park trong `js.try_mutate` sẵn có, part của parent đã ghép gỡ im lặng. |
| `agents/agent_brain_notify.py` | `park_set` «⏸ … tạm dừng soát dở N câu, tự thử lại lúc HH:MM (lần k/max)»; `hold_set` «⛔ … HOLD: … Cần bạn: …». |
| `agents/agent_brain_context.py` | snapshot LLM thêm `agent.hold_class`, `finished` + luật. |
| `http_routes/routes_agent.py` | `_agent_resume` gỡ thêm `review_retries/review_retry_at/simple_park/split_wait_parts` (dòng ghi `js.persist` sẵn có, không thêm lời ghi). |
| `tests/test_brain_finished_hold.py` | MỚI (23 ca, import `agents.*`). `tests/test_agent_brain_notify.py`: kỳ vọng hold_set ⏸ → ⛔. |

Import trần `from agent_hold/agent_brain_notify …` của V2 → `from agents.… import`. Không thêm `.save()`, không thêm
`js.persist` (ratchet giữ). Không đổi `server.py`/`state/job_state.py` (khoá park V6 dùng cùng tên: `apply_simple_gate`,
`set_review_retry`, `_split_wait_parts`).

## Recheck
- Cloud (bản sao, stub fastapi, `AGENT_BRAIN=0 python -m pytest -q --continue-on-collection-errors tests`):
  trước 669 passed / 10 failed / 3 errors → sau 692 passed / CÙNG 10 failed + 3 errors (môi trường: TestClient, sfx, desktop…).
  `test_f1_ratchet` pass. `scripts/verify_all.py --dry` PASS. ast.parse mọi file sửa: Pass.
- CHƯA kiểm trên Windows / chưa chạy brain thật (instance v6 tắt brain/telegram). Cần restart :8806 để có hiệu lực.
