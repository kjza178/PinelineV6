# 2026-11-11 — F1 (B9): mọi ghi manifest qua state/

## Vấn đề
V5 có 110 chỗ `Manifest(...).save()` rải trong server.py (48), engines, agents, routes. Ghi kiểu snapshot rồi gộp
3 chiều — không có một đường ghi chung, UI không biết khi nào manifest đổi (phải poll), không đếm/kiểm soát được.

## Đã làm
1. **Nhập code V5 vào V6** (agents, engines, core, http_routes, pineline, tools, scripts, rvc_bridge, vendor, static,
   config, claude_setup, server.py, UI html cũ; tests V5 → `tests/v5/`). Không chép data/ (hàng publish, token
   Canva là của instance V5). models/assets/voices: junction tới V5 qua `scripts/setup_f1_links.bat`.
2. **Cô lập instance** (an toàn với V2 production):
   - Bỏ đường cứng `D:\PinelineV2_work` (server `_clear_stop/_check_stopped`, agent_heal ×2, agent_tool_loop,
     uploader_settings_api) → `WORK_DIR` của instance. Trước đó V6 có thể xoá `.stop`/artifact job V2 trùng id.
   - `core.instance.apply_env`: `WORK_DIR`, `PINELINE_WORK_ROOT`, `AUTOPILOT_PORT/URL` (sidecar riêng 8816,
     không gọi nhầm sidecar :8802 của V2).
   - `instances/v6.json`: `agent_brain=false` tới F6 (không đổi preset llama :8083 dùng chung).
3. **Một đường ghi** — `core/manifest.py`: `save()` → `_commit()` nội bộ; `register_write_hook` chạy sau mọi lần ghi
   thành công. `state/job_state.py` đăng ký hook → JobIndex + event SSE cho MỌI lần ghi trong server.
   `state/watcher.py`: bắt lần ghi từ tiến trình con (asr/tts) theo mtime, chỉ stat job đang chạy.
4. **Chuyển 110 chỗ gọi** (`scripts/f1_convert_saves.py`, có kiểm AST: cả 110 đều là biến Manifest):
   - Ngữ nghĩa (server.py): park REVIEW → `js.set_review_retry`; HOLD Second Brain / QA gate → `js.set_hold`;
     queue_opts (hardsub_blur ×2, stamp opts) → `js.set_queue_opts`.
   - Còn lại 99 chỗ → `js.persist(m)` (cùng ngữ nghĩa cũ, nhưng đi qua state, có event, đếm được).
   - Import `js` đặt sau dòng sys.path/Manifest import (engine chạy như script).
5. **Gate**: `tests/test_f1_ratchet.py` — 0 `.save()` ngoài state/; `js.persist` ≤ 99 (chỉ giảm);
   tầng mới (app/pipeline/workers/…) cấm đụng Manifest trực tiếp. `js.mutate(wd, fn)` cho đọc-sửa-ghi nguyên tử.

## Backup
`.backup_edit_20261111_f1_pre/` (core, tests, app, instances, state trước F1) ·
`.backup_edit_20261111_f1_paths/` · `.backup_edit_20261111_f1_saves/` (25 file trước khi đổi save).

## Recheck
- Container: 44 test V6 Pass (gồm đồng thời persist ∥ set_stage, HOLD sống sót qua snapshot cũ, watcher).
- VM: ratchet 3/3 Pass trên cây thật; tree_audit ERROR=0 (WARN: context_studio, mặc định cổng env — có sẵn từ V5).
- Windows: chờ `scripts\setup_f1_links.bat` + `run_tests.bat` (gồm tests/v5).
- Chưa: chạy 1 job thật có HOLD/park/stop (cần route V5 gắn vào app V6 — F3).

## Recheck Windows lần 1 (07:27): 215 pass · 17 fail · 1 skip (10 phút)
- 8 fail do dời test V5 vào `tests/v5/` (ROOT = parents[1] → trỏ nhầm `tests/`) → sửa parents[2] (9 file; backup .backup_edit_20261111_f1_tests/).
- 9 fail CÓ TỪ TRƯỚC ở V2/V5 (hàm test gọi không tồn tại ở cả 3 cây; pronoun mixed đổi logic) → xfail có lý do trong tests/v5/conftest.py, xử lý F3–F5.
- 1 test kiểm bố cục riêng V5 (apps/run_v5.bat) → skip.
- KHÔNG có fail nào do F1 (đổi save → state). Test F1 + V6: pass.

## Recheck Windows lần 2 (08:39)
- `run_tests.bat` (V6, bỏ tests\v5): **44 passed in 4.07s** → Pass.
- `run_tests.bat all` lần chạy trước TREO ~50' sau test auto_publisher (~test 74) — chưa rõ test nào; bat mới có
  `-v` + `faulthandler_timeout=120` để dump chỗ treo vào scratch\pytest_all.log. Chưa xong.
