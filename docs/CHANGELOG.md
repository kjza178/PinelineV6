# CHANGELOG — V6

## 2026-11-13
- Port V2 → V6 (agent R6, từ V2 agent N): brain/heal/watch bỏ job ĐÃ XONG (`agents.agent_hold.job_finished` — đã đăng,
  parent đã ghép Full, part của parent đó, mux+output); 3 detector heal gốc; tin tạm dừng đúng loại (park tự thử lại ⏸ ≠
  HOLD cần người ⛔); «Chạy tiếp»/chạy tay/job xong gỡ khoá park. docs/fixes/2026-11-13-port-brain-finished-hold.md
- Port V2 → V6 (agent Q6): preflight + chuẩn hoá video nguồn (`engines/source_preflight.py`, mọi `_find_video` +
  `resolve_asr_source` + render worker qua `stage_source_video`; import gắn `preflight_pending`; GET chỉ đọc),
  `media_ts.extract_pcm_from_video` bền (K/K2 + chỗ gọi của chủ dự án), FireRed chia slice tại khoảng lặng + đệm chồng lấn,
  câu ASR rác `engines/text_junk.py` (FireRed lọc; simple tự soát «…»), simple: lượt review câu thiếu + nhận nháp
  ≤ `SIMPLE_UNREVIEWED_TOLERANCE`, parent auto-split giữ ghế khi part park/HOLD (`held_at=split_parts`), cur_text parent
  «đã ghép từ N parts». Ghi qua `state.job_state`; cờ instance tuỳ chọn `video_preflight`/`video_normalize`; tools
  `preflight_scan`, `asr_boundary_scan` (work root instance). docs/fixes/2026-11-13-port-preflight-slice-junk.md

## 2026-11-12
- Fix sau port (agent J): index job cách ly theo work root instance — `state/index.py` `load_all` GẮN + nạp lại
  (không cộng dồn), hook ghi manifest → `apply_dir` bỏ job ngoài work root, app shutdown `unbind`. Trước đó test
  brain/hold P6 (chạy trước test_app_integration) ghi thư mục tạm → /api/v6/jobs + snapshot SSE lẫn
  '2937ffd7989f','f0834acf0cc0','jB' (không phải job thật, không ghi đĩa thật). Ratchet bỏ qua `_scratch*`/`scratch*`/
  `_to_delete*`; `_scratch_agentB` + `scratch/agentP6/new` → `_to_delete/scratch_20261112/`. Test restart_server giải mã
  UTF-8 (Windows cp1252 → 0x8f). Test mới `tests/test_index_instance_isolation.py`.
- Port V2 → V6 (agent P6): Second Brain tin Telegram theo mẫu (`agents/agent_brain_notify.py`, mặc định `events`/600 s),
  gỡ HOLD cũ + gỡ HOLD khi chạy tay (`agents/agent_hold.py`, 5 route server, lệnh Telegram/chat Agent của người = tay — I2),
  hoãn heal 180 s + không heal job đang chạy, đuôi log engine/render worker khi lỗi, luồng dịch THEO JOB
  (`cfg.job_pipeline`, đóng dấu `manifest.translate_pipeline`; Dịch LẠI = mặc định instance), `js.try_mutate`.
  docs/fixes/2026-11-12-port-brain-hold-pipeline.md
- Port V2 → V6 (F6): luồng dịch simple (`engines/translate_simple/` + Stage `pipeline/stages/simple_translate.py` +
  Gate `pipeline/gates/simple_translate_gate.py`, ghi agent qua `state.job_state.apply_simple_gate`), Demucs trước ASR
  (`engines/separate.py` chia khúc khoảng lặng/đúng mẫu, `engines/asr/asr.py` fallback), `engines/cloud_retry.py`,
  `data/lexicon/`, `tools/{collect_manual_edits,simple_stats}.py`. Mặc định simple + Demucs=1; cờ instance
  `translate_pipeline` (legacy = rollback), `asr_use_demucs`. docs/fixes/2026-11-12-port-simple-demucs.md

## 2026-11-11
- F0: khung state/events/pipeline/runtime/workers, Tauri shell, UI SSE.
- F0 fix: backend không lên (import fastapi), encoding Windows, SSE đứt (generator đồng bộ).
- F1: nhập code V5; cô lập instance (WORK_DIR, sidecar, bỏ D:\PinelineV2_work cứng); một đường ghi manifest
  (hook + watcher); 110 `.save()` → state (11 ngữ nghĩa, 99 persist); ratchet test.
- F2: `core/job_opts.py` — một nguồn opts job (tường minh > ghim > batch > mặc định); UI/hàng chờ/agent cùng opts
  (test gate). Sửa bug route run-all gắn nhầm vào `_req_audio` (có cả ở V2/V4/V5 — chưa sửa ở đó).
- F3 tích hợp: `python -m app` phục vụ cả API V5 (server.py) lẫn V6 (/api/health, /api/events, /api/v6/jobs, UI /v6/)
  trên 1 cổng; cờ instance chặn brain/watch/telegram/auto-publish/hg-resume; UI cũ hết polling 2 s (SSE + live_sync.js);
  GET /api/jobs/{id} không ghi; cache script.json trong list_jobs; js.persist 99→98.
- Review F2/F3 (review1): cờ instance mới `title_translate`, `queue_autostart` (v6/test tắt); hàng gộp Hongguo theo
  WORK_DIR (trước dùng chung `<ổ>/work/logs` với V2) và GET merge-queue tôn trọng `hg_merge_resume`; khoá backend theo work
  root; live_sync.js: CLOSED → poll, gộp tiến độ 2 s, dự phòng 5 s khi job chạy, tab ẩn. docs/reviews/2026-11-11-review-f2-f3.md
- F5 (render tiến trình con): `engines/render_job.do_render` = một nguồn logic render (thân `_do_render` cũ);
  `workers/render_worker.py` chạy nó ở tiến trình con (không importlib.reload; sửa render có hiệu lực job kế; Dừng kill
  worker; lỗi → mark_failed). Cờ instance `render_subprocess` (v6/test bật, prod tắt tới khi so job thật). js.persist 98→93.
- F4 (services + sidecar thuần): `services/` (feeder, autopilot, film_db_sync, publisher, hongguo_flow, background);
  agent tools running/lib/feed gọi thẳng services (bỏ vòng server→sidecar /cmd); việc nền chạy trong server theo cờ `feeder`
  (v6/test tắt, prod bật); sidecar `--instance` (cổng instance, Telegram theo cờ, không vòng nền) + `run_sidecar_v6.bat`;
  bỏ cổng cứng 8800/8801/8802/8804/8805 khi thiếu env (core.instance.server_base/sidecar_url). tree_audit cổng 8→0.
  docs/fixes/2026-11-11-f4-f5.md
- Review F4/F5 (review2): UI cũ (index/index3/hongguo/films_ui) gọi cứng sidecar/server V2 :8802/:8801 → đổi lúc
  phục vụ sang instance (`core.instance.localize_html`); restart_server/migrate_work chỉ nhắm tiến trình của CÂY + instance
  (so token cổng/instance + cwd), bỏ env PORT lạc, từ chối cổng ngoài V6 (8801/8802/8000/8083) trừ `--allow-foreign-port`,
  bỏ run_v5/run_v2/run_tele_cmd.bat, Telegram theo cờ; Dừng render kill CẢ CÂY worker (ffmpeg/demucs cháu, hết kẹt pipe);
  sidecar chạy trần mặc định instance v6; work_dir tuyệt đối cho worker; run-watch nhận server `-m app`.
  docs/reviews/2026-11-11-review-f4-f5.md
- F6 lượt kiểm cuối: watcher theo dõi job ngay khi spawn render worker / engine (`state.watcher.watch_job/watching`) —
  tiến độ con lên SSE không chờ quét 30 s; uploader settings mặc định WORK_DIR/TEMP theo instance (bỏ cứng V5);
  B11: `engines/mux.py` (code chết) → _to_delete, bỏ import `run_asr` thừa, test hàm thuần agent_feeder/agent_tele_cmd/
  clone_tts; `verify_all.bat` + `scripts/verify_all.py` (pytest V6 + v5, tree_audit, smoke backend :8899, bảng PASS/FAIL);
  brain V6 (`docs/brain/`); bàn giao `docs/V6_HANDOFF.md`. docs/fixes/2026-11-11-f6-final.md
- machine-config: `core/machine.py` — cổng/thư mục phụ thuộc máy thành key config (mặc định = máy gốc, không đổi hành vi):
  LLAMA_BASE_URL (trống → LLM_BASE_URL nếu localhost → :8083), AUTO_SWITCH_PRESETS, MODEL_PRESETS_DIR, MODEL_START_SCRIPT,
  MODEL_START_CMD, CHATGPT/AURORA_BASE_URL, AURORA_FALLBACK_URLS, CLOUD_LLM_BASE_URL, HONGGUO_BASE_URL, MT_MODEL_DIR,
  LIBRARY_DIR, YT_UPLOADER_DIR, LIB_SAMPLE_DIR. preset_switcher bỏ qua (không lỗi) khi thiếu presets/AUTO_SWITCH_PRESETS=0;
  Aurora hỏng → lỗi rõ + gợi ý đổi model. Doctor `scripts/check_env.py`, `docs/SETUP_MAY_MOI.md`, 14 test. Port sang V5 + V4.
  docs/fixes/2026-11-11-machine-config.md
