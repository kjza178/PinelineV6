# `server`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `server.py` · **Lớp:** `control` · **6626 dòng** (5743 code) · **186 hàm**, **28 class** · sha1 `be4015210e`

> server.py — Backend web Phase 1 (FastAPI). Bọc engine (download -> asr -> translate) thành app web: - Phục vụ trang dashboard (web/index.html)

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.agent_brain](./agents.agent_brain.md), [agents.agent_name_pass](./agents.agent_name_pass.md), [agents.agent_pronoun_pass](./agents.agent_pronoun_pass.md), [agents.agent_qa_gate](./agents.agent_qa_gate.md), [agents.agent_quality](./agents.agent_quality.md), [agents.agent_watch](./agents.agent_watch.md), [agents.auto_publisher](./agents.auto_publisher.md), [agents.hg_ui_ctx](./agents.hg_ui_ctx.md), [agents.hongguo_bridge](./agents.hongguo_bridge.md), [core.batch_config](./core.batch_config.md), [core.config](./core.config.md), [core.instance](./core.instance.md), [core.job_opts](./core.job_opts.md), [core.job_queue](./core.job_queue.md), [core.manifest](./core.manifest.md), [engines.asr.asr_hybrid_progress](./engines.asr.asr_hybrid_progress.md), [engines.asr.asr_spam](./engines.asr.asr_spam.md), [engines.asr.hardsub_asr](./engines.asr.hardsub_asr.md), [engines.assemble](./engines.assemble.md), [engines.clone_tts](./engines.clone_tts.md), [engines.download](./engines.download.md), [engines.llm](./engines.llm.md), [engines.ocr](./engines.ocr.md), [engines.playlist](./engines.playlist.md), [engines.render](./engines.render.md), [engines.render_fx](./engines.render_fx.md), [engines.render_job](./engines.render_job.md), [engines.separate](./engines.separate.md), [engines.series_glossary](./engines.series_glossary.md), [engines.split_parts](./engines.split_parts.md), [engines.subs](./engines.subs.md), [engines.translate](./engines.translate.md), [engines.translate_alts](./engines.translate_alts.md), [engines.vram](./engines.vram.md), [http_routes.routes_agent](./http_routes.routes_agent.md), [http_routes.routes_hg](./http_routes.routes_hg.md), [http_routes.routes_ui](./http_routes.routes_ui.md), [services.background](./services.background.md), [state.job_state](./state.job_state.md), [state.watcher](./state.watcher.md), [workers.render_worker](./workers.render_worker.md)
- **Được dùng bởi:** [app.main](./app.main.md), [http_routes.routes_agent](./http_routes.routes_agent.md), [http_routes.routes_hg](./http_routes.routes_hg.md), [scripts.check_batch3](./scripts.check_batch3.md), [scripts.check_batch4](./scripts.check_batch4.md), [scripts.check_batch5](./scripts.check_batch5.md)
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [agents.agent_core](./agents.agent_core.md), [app.main](./app.main.md), [core.instance](./core.instance.md), [http_routes.routes_agent](./http_routes.routes_agent.md), [http_routes.routes_films](./http_routes.routes_films.md), [http_routes.routes_hg](./http_routes.routes_hg.md), [http_routes.routes_ui](./http_routes.routes_ui.md), [pineline.server.__init__](./pineline.server.__init__.md), [pineline.server.routes.__init__](./pineline.server.routes.__init__.md), [pineline.server.routes.assets](./pineline.server.routes.assets.md), [pineline.server.routes.fs](./pineline.server.routes.fs.md), [pineline.server.routes.jobs](./pineline.server.routes.jobs.md), [pineline.server.routes.queue](./pineline.server.routes.queue.md), [pipeline.base](./pipeline.base.md), [restart_server](./restart_server.md), [runtime.context](./runtime.context.md), [scripts.check_batch1](./scripts.check_batch1.md), [scripts.check_batch2](./scripts.check_batch2.md), [scripts.check_batch3](./scripts.check_batch3.md), [scripts.check_batch4](./scripts.check_batch4.md), [scripts.f1_convert_saves](./scripts.f1_convert_saves.md), [scripts.fix_runall_decorator_v2v4v5](./scripts.fix_runall_decorator_v2v4v5.md), [scripts.sync_from_v2](./scripts.sync_from_v2.md), [scripts.tree_audit](./scripts.tree_audit.md), [state.job_state](./state.job_state.md), [tools.migrate_work](./tools.migrate_work.md)
- **Test:** `tests/test_app_integration.py`, `tests/v5/test_hardsub_blur_toggle.py`, `tests/v5/test_layout_paths.py`, `tests/v5/test_optimize_b5_b6.py`, `tests/v5/test_park_review_retry.py`, `tests/v5/test_queue_jobs_api.py`, `tests/v5/test_queue_run_one.py`, `tests/v5/test_server_split.py`, `tests/v5/test_source_link.py`, `tests/v5/test_translate_force_default.py`
- **Thư viện ngoài:** `context_studio`, `engines`, `fastapi`, `faster_whisper`, `huggingface_hub`, `psutil`, `pydantic`, `services`, `state`, `uvicorn`, `workers`

```mermaid
flowchart LR
  server[[server]]
  app_main[app.main] --> server
  http_routes_routes_agent[http_routes.routes_agent] --> server
  http_routes_routes_hg[http_routes.routes_hg] --> server
  scripts_check_batch3[scripts.check_batch3] --> server
  scripts_check_batch4[scripts.check_batch4] --> server
  scripts_check_batch5[scripts.check_batch5] --> server
  server --> agents_agent_brain[agents.agent_brain]
  server --> agents_agent_name_pass[agents.agent_name_pass]
  server --> agents_agent_pronoun_pass[agents.agent_pronoun_pass]
  server --> agents_agent_qa_gate[agents.agent_qa_gate]
  server --> agents_agent_quality[agents.agent_quality]
  server --> agents_agent_watch[agents.agent_watch]
  server --> agents_auto_publisher[agents.auto_publisher]
  server --> agents_hg_ui_ctx[agents.hg_ui_ctx]
  server --> agents_hongguo_bridge[agents.hongguo_bridge]
  server --> core_batch_config[core.batch_config]
  server --> core_config[core.config]
  server --> core_instance[core.instance]
  server --> core_job_opts[core.job_opts]
  server --> core_job_queue[core.job_queue]
  server --> core_manifest[core.manifest]
```

### API routes (68)

| Method | Path | Handler | Dòng |
|---|---|---|---:|
| POST | `/api/agent/brain/tick` | `api_agent_brain_tick` | 6578 |
| GET | `/api/brand-logos` | `list_brand_logos` | 5444 |
| POST | `/api/clone-voice` | `clone_voice` | 5571 |
| GET | `/api/cookies` | `cookies_status` | 5092 |
| POST | `/api/cookies` | `import_cookies` | 5118 |
| GET | `/api/frames` | `list_frames` | 5434 |
| GET | `/api/fs/list-dirs` | `api_fs_list_dirs` | 3059 |
| POST | `/api/fs/pick-dir` | `api_fs_pick_dir` | 3113 |
| POST | `/api/fs/pick-dirs` | `api_fs_pick_dirs` | 3187 |
| GET | `/api/jobs` | `list_jobs` | 5164 |
| POST | `/api/jobs` | `create_job` | 2500 |
| POST | `/api/jobs/group` | `jobs_group` | 2591 |
| POST | `/api/jobs/local` | `create_local_job_upload` | 3253 |
| POST | `/api/jobs/local-batch` | `create_local_job_batch` | 2976 |
| POST | `/api/jobs/local-path` | `create_local_job_path` | 2905 |
| DELETE | `/api/jobs/{job_id}` | `delete_job` | 6228 |
| GET | `/api/jobs/{job_id}` | `job_detail` | 5178 |
| POST | `/api/jobs/{job_id}/apply-alt` | `apply_alt_pick` | 4967 |
| POST | `/api/jobs/{job_id}/asr-engine` | `set_asr_engine` | 4427 |
| POST | `/api/jobs/{job_id}/asr-select` | `select_asr_source` | 4743 |
| GET | `/api/jobs/{job_id}/asr-sources` | `list_asr_sources` | 4725 |
| POST | `/api/jobs/{job_id}/assemble` | `assemble_job` | 6081 |
| DELETE | `/api/jobs/{job_id}/audio` | `delete_audio` | 5919 |
| POST | `/api/jobs/{job_id}/audio` | `upload_audio` | 5885 |
| GET | `/api/jobs/{job_id}/context-studio` | `get_context_studio` | 4831 |
| POST | `/api/jobs/{job_id}/context-studio` | `run_context_studio_api` | 4887 |
| GET | `/api/jobs/{job_id}/file/{path:path}` | `job_file` | 6175 |
| GET | `/api/jobs/{job_id}/hardsub-asr` | `get_hardsub_asr` | 5056 |
| POST | `/api/jobs/{job_id}/hardsub-asr` | `run_hardsub_asr_api` | 5026 |
| POST | `/api/jobs/{job_id}/mark-done` | `mark_job_done` | 3888 |
| POST | `/api/jobs/{job_id}/merge-parts` | `api_merge_job_parts` | 6136 |
| POST | `/api/jobs/{job_id}/mux` | `mux_job` | 6052 |
| POST | `/api/jobs/{job_id}/reveal` | `reveal_job` | 6147 |
| POST | `/api/jobs/{job_id}/run-all` | `run_all` | 4162 |
| POST | `/api/jobs/{job_id}/run/{stage}` | `run_stage` | 3804 |
| POST | `/api/jobs/{job_id}/script-clean-spam` | `clean_script_spam` | 4099 |
| POST | `/api/jobs/{job_id}/script-line` | `edit_script_line` | 3993 |
| POST | `/api/jobs/{job_id}/source` | `upload_source` | 5807 |
| POST | `/api/jobs/{job_id}/source-path` | `import_source_path` | 5845 |
| POST | `/api/jobs/{job_id}/split-parts` | `api_split_job_parts` | 6125 |
| GET | `/api/jobs/{job_id}/split-plan` | `get_split_plan` | 6103 |
| GET | `/api/jobs/{job_id}/srt/{lang}` | `job_srt` | 5772 |
| POST | `/api/jobs/{job_id}/start` | `start_job` | 3291 |
| POST | `/api/jobs/{job_id}/stop` | `stop_job` | 3867 |
| POST | `/api/jobs/{job_id}/translate` | `retranslate_job` | 5980 |
| GET | `/api/jobs/{job_id}/translate-alts` | `get_translate_alts` | 4922 |
| POST | `/api/jobs/{job_id}/translate-alts` | `run_translate_alts_api` | 4941 |
| POST | `/api/jobs/{job_id}/translate-fill` | `translate_fill` | 3946 |
| POST | `/api/jobs/{job_id}/tts` | `regen_tts` | 5661 |
| POST | `/api/jobs/{job_id}/tts-clip` | `regen_clip` | 5755 |
| POST | `/api/jobs/{job_id}/tts-voice` | `set_tts_voice` | 4138 |
| GET | `/api/jobs/{job_id}/tts-wav-ok` | `tts_wav_ok` | 5645 |
| GET|HEAD | `/api/jobs/{job_id}/video` | `job_video` | 5342 |
| GET | `/api/llm/models` | `get_available_llm_models` | 6592 |
| GET | `/api/models` | `get_available_llm_models` | 6592 |
| POST | `/api/queue/group` | `jobs_group` | 2591 |
| GET | `/api/queue/jobs` | `api_queue_jobs_state` | 2867 |
| POST | `/api/queue/jobs` | `api_queue_jobs_add` | 2707 |
| POST | `/api/queue/jobs/clear` | `api_queue_jobs_clear` | 2900 |
| POST | `/api/queue/jobs/group` | `api_queue_jobs_add_group` | 2726 |
| POST | `/api/queue/jobs/move` | `api_queue_jobs_move` | 2838 |
| POST | `/api/queue/jobs/opts` | `api_queue_jobs_opts` | 2745 |
| POST | `/api/queue/jobs/remove` | `api_queue_jobs_remove` | 2766 |
| POST | `/api/queue/jobs/reorder` | `api_queue_jobs_reorder` | 2854 |
| POST | `/api/queue/jobs/sort` | `api_queue_jobs_sort` | 2829 |
| POST | `/api/queue/jobs/stop` | `api_queue_jobs_stop` | 2895 |
| GET | `/api/voice-sample` | `voice_sample` | 5604 |
| GET | `/api/voices` | `list_voices` | 5426 |

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| **class `_Stopped`** | 766 | 0 method | | Người dùng bấm Dừng — không phải lỗi. |
| **class `NewJob`** | 1227 | 0 method | |  |
| **class `LocalJobIn`** | 1233 | 0 method | | Tạo job từ video local — không cần URL. |
| **class `LocalBatchIn`** | 1244 | 0 method | | Import nhiều path (file và/hoặc folder), 1 dòng = 1 path. |
| **class `HgSubmitIn`** | 1254 | 0 method | |  |
| **class `HgMergeIn`** | 1261 | 0 method | |  |
| **class `JobGroupReq`** | 1267 | 0 method | | Gom / sửa nhóm series trên hàng chờ. |
| **class `HgBaseIn`** | 1277 | 0 method | |  |
| **class `TTSReq`** | 1281 | 0 method | |  |
| **class `ClipReq`** | 1288 | 0 method | |  |
| **class `MuxReq`** | 1294 | 0 method | |  |
| **class `TranslateReq`** | 1319 | 0 method | |  |
| **class `ScriptLineEdit`** | 1329 | 0 method | | Sửa tay 1 câu trong script.json (zh và/hoặc vi). |
| **class `TTSVoiceReq`** | 1336 | 0 method | |  |
| **class `RunAllReq`** | 1342 | 0 method | |  |
| **class `JobQueueReq`** | 1370 | 0 method | |  |
| **class `JobQueueGroupReq`** | 1376 | 0 method | | Enqueue cả series theo group_id (sort group_index). |
| **class `JobQueueRemoveReq`** | 1382 | 0 method | |  |
| **class `AgentChatReq`** | 1386 | 0 method | | Pineline V2 — chat với AI Agent nhúng. |
| **class `AgentConfirmReq`** | 1396 | 0 method | |  |
| **class `AsrEngineReq`** | 1401 | 0 method | |  |
| **class `AsrSelectReq`** | 1405 | 0 method | | Chọn file ASR sidecar → promote thành asr.json (vd asr.fun_nano.json). |
| **class `ContextStudioReq`** | 1410 | 0 method | | PRE-SCAN Context Studio trước dịch. |
| **class `TranslateAltsReq`** | 1416 | 0 method | | Quét câu → phương án dịch tối ưu (ngữ cảnh + văn phong). |
| **class `ApplyAltReq`** | 1423 | 0 method | |  |
| **class `MarkDoneReq`** | 1428 | 0 method | |  |
| **class `HardsubAsrReq`** | 5018 | 0 method | |  |
| **class `SourcePathIn`** | 5801 | 0 method | |  |
| `_instance_flag` | 81 | 12 | `name, default` | V6 F3: công tắc dịch vụ nền theo instance (core.instance.apply_env đặt PINELINE_FLAG_<NAME>). |
| `_mux_output_ok` | 111 | 6 | `work_dir, min_bytes` |  |
| `_part_pipeline_complete` | 119 | 19 | `work_dir` | Part đã mux + output đủ lớn và không bị đánh stale → không chạy lại pipeline. |
| `_ensure_parent_mux_merged_done` | 140 | 25 | `work_dir, n_parts` | Heal UI parent: đã có Full/merged mà mux còn running@80%. |
| `_parent_full_output_ok` | 167 | 9 | `work_dir, sub_ids` |  |
| `_all_split_parts_complete` | 178 | 14 | `parent_id` | True nếu parent có sub_job_ids và mọi part đã mux/output xong. |
| `_queue_split_parent_merge_if_ready` | 194 | 89 | `parent_id, reason` | Parts đã xong mà parent chưa Full (worker auto-split chết/restart) → xếp ghép nền. |
| `_notify_parent_after_part_done` | 285 | 9 | `part_job_id` | Part mux xong → nếu mọi part xong thì xếp merge về job gốc. |
| `_split_parent_id` | 296 | 5 | `job_id` | d693…_p03 → d693… ; job thường → None. |
| `_job_any_busy` | 303 | 7 | `job_id` | Trả tên stage đang claim, hoặc None. |
| `_job_lineage_busy` | 312 | 15 | `job_id` | Busy của job hoặc parent auto-split (part chạy dưới claim của parent). |
| `_claim_stage` | 329 | 7 | `job_id, stage` | Giữ chỗ trước khi submit thread — chống bấm 2-3 lần tạo nhiều task. |
| `_release_stage` | 338 | 5 | `job_id, stage` |  |
| `_busy_response` | 345 | 9 | `job_id, work_dir` | 409 nếu job đang có claim hoặc stage 'running' trong manifest, ngược lại None. |
| `_submit_claimed` | 356 | 17 | `executor, job_id, stage, fn, *args, **kwargs` | Claim (job, stage) rồi submit; nhả claim khi xong. Trả 409 nếu đã có claim. |
| `_manifest_running` | 375 | 10 | `work_dir` | Stage đang 'running' trong manifest (nếu có). |
| `_job_proc_alive` | 387 | 6 | `job_id` | True nếu còn subprocess engine/ffmpeg đăng ký cho job. |
| `_path_key` | 395 | 10 | `p` | Khoá so đường dẫn: work là junction → D:, psutil.cwd() trả đường Z: chưa resolve. |
| `_json_rows` | 407 | 6 | `p` |  |
| `_stage_complete_on_disk` | 415 | 26 | `work_dir, stage` | Artifact ĐỦ (không phải bản dở sau Dừng/crash) — điều kiện để tự nâng stage lên done. |
| `_stage_has_artifact` | 443 | 22 | `work_dir, stage` | Artifact đĩa đủ để coi stage đã xong (dùng heal stale running). |
| `_heal_stale_running` | 467 | 106 | `work_dir` | Sửa stage status=running mồ côi (kill/crash/skip không mark_done). |
| `_next_incomplete_stage` | 575 | 8 | `work_dir` | Bước WIRED đầu tiên chưa done (để mark_running đúng — không ép lại ASR). |
| `_engine_script_path` | 588 | 7 | `script` | Đường dẫn script engine: root (V2/V4 phẳng) hoặc package (V5: engines/asr/…). |
| `_run_engine` | 597 | 88 | `script, work_dir, *args` | Chạy engine NẶNG (ASR/TTS) ở TIẾN TRÌNH RIÊNG. |
| `_iter_job_engine_procs` | 696 | 41 | `job_id, script_name` | Quét toàn hệ thống process Python đang chạy engine cho job_id (kể cả orphan). |
| `_kill_job_engine_procs` | 739 | 13 | `job_id, script_name, exclude_pid` | Kill process engine trùng job (system-wide). Trả số process đã kill. |
| `_noisy_log` | 758 | 6 | `line` | Lọc log rác TTS (ref_text dài, tqdm...). Giữ gen_text + [ok]/[tts]/lỗi. |
| `_kill_job_children` | 770 | 73 | `job_id` | Kill MỌI tiến trình liên quan job (asr/clone_tts/demucs/ffmpeg/aria2c), |
| `_stop_job` | 845 | 90 | `job_id` | Dừng mọi việc đang chạy của 1 job + đưa stage 'running' về 'pending'. |
| `_clear_stop` | 937 | 12 | `job_id` | Xoá cờ Stop in-memory + file .stop (cả WORK_ROOT lẫn WORK_DIR của instance). |
| `_check_stopped` | 951 | 11 | `job_id` | True nếu user bấm Dừng (in-memory hoặc file .stop). |
| `_stage_artifact_payload` | 970 | 58 | `work_dir, stage` | Nếu đĩa đã có kết quả bước → payload để mark_done. None = chưa có artifact. |
| `_heal_stages_from_disk` | 1030 | 20 | `m` | Misclick/Dừng giữa chừng: file đã ĐỦ mà status còn pending/failed → đánh done. |
| `_force_mark_stage` | 1052 | 15 | `m, stage, require_artifact` | Đánh dấu 1 bước done ngay (không chạy lại). |
| `_url_host` | 1083 | 6 | `v` |  |
| async `_local_only_guard` | 1092 | 14 | `request, call_next` | Chỉ phục vụ máy này: Host lạ = DNS rebinding; Origin/Referer lạ trên /api = trang web |
| async `_quiet_conn_reset` | 1109 | 14 | `` | Trên Windows, khi client (UI poll) đóng kết nối đột ngột giữa request, event |
| `_reset_stale_running` | 1126 | 38 | `` | Khi server khởi động, KHÔNG có thread nào đang chạy. Vậy mọi stage còn ghi |
| `_start_queue_worker` | 1167 | 9 | `` | Worker hàng đợi chỉ chạy SAU khi reset stage treo + kill engine orphan; chạy lúc import |
| `_resume_hg_merge` | 1179 | 13 | `` | Hàng đợi gộp Hongguo nạp lại từ work/logs/hg_merge_state.json → chạy tiếp nếu Hongguo đang mở |
| `_startup_fix_tts` | 1194 | 31 | `work_dir, st` | Khôi phục voice/progress TTS sau crash — không mất cấu hình. |
| `_process_gpu` | 1434 | 25 | `job_id, target` | Phần NẶNG (GPU/LLM): ASR -> Dịch. Chạy ở hàng đợi _gpu (tuần tự). |
| `run_pipeline` | 1461 | 16 | `job_id, url, target, cookies_from_browser` | TẢI (hàng đợi _dl, song song). Tải xong -> đẩy ASR+Dịch sang hàng đợi _gpu |
| `_check_and_run_auto_split_parts` | 1479 | 145 ⚠️ | `job_id, target, audio_mode, voice_vol, orig_vol, use_ocr, fit, reso…` | Tự động phát hiện video dài (>= 3.0h): |
| `_auto_pipeline` | 1626 | 49 | `job_id, audio_mode, voice_vol, orig_vol, use_ocr, fit, resolution, …` | 1-CLICK (chạy ở hàng _dl): tải (nếu cần) -> đẩy phần GPU sang hàng _gpu. |
| `_run_requested_review` | 1677 | 59 | `wd, target` | Rà soát do Agent/user yêu cầu (manifest.agent.review_request): rà tên nhân vật theo bảng |
| `_save_series_glossary` | 1738 | 9 | `wd` |  |
| `_auto_gpu` | 1754 | 275 ⚠️ | `job_id, target, audio_mode, voice_vol, orig_vol, use_ocr, fit, reso…` | Phần GPU của 1-click (hàng _gpu, tuần tự): ASR -> Dịch -> TTS -> [Tách nền] |
| `_has_cjk` | 2037 | 2 | `s` |  |
| `_extract_vi_title` | 2041 | 28 | `raw` | Bóc tách tên tiếng Việt từ response LLM có thể dài dòng / giải thích. |
| `_translate_title` | 2071 | 45 | `job_id` | Dịch tiêu đề gốc (Trung) sang tiếng Việt bằng gemma4 local, lưu title_vi. |
| `_maybe_translate_title` | 2118 | 16 | `job_id, title` | Kích hoạt dịch tiêu đề (lazy) nếu còn tiếng Trung. Bỏ qua khi ASR/TTS đang |
| `_script_missing` | 2141 | 16 | `work_dir` | (số câu trống vi, tổng số dòng) của script.json; None nếu không có. Đọc lại chỉ khi file đổi. |
| `_asr_labels_from_source` | 2159 | 16 | `src_name` | Suy engine + model từ tên file sidecar (nguồn sự thật). |
| `_sync_asr_labels` | 2177 | 36 | `work_dir, asr, persist` | Ép engine/model khớp asr_source (+ n_segments từ asr.json nếu có). |
| `_apply_hybrid_asr_overlay` | 2215 | 23 | `work_dir, result` | Hybrid FA: UI đọc stages.asr — overlay % từ asr.*.progress.json nếu sidecar đang chạy. |
| `_sync_translate_missing` | 2240 | 16 | `work_dir, rows` | V6 F3: đồng bộ stages.translate.n_missing sau khi SỬA script (trước làm trong GET read_job). |
| `read_job` | 2258 | 114 | `work_dir` |  |
| `_create` | 2384 | 14 | `url, target, cookies_from_browser, group` | Tạo 1 job ở trạng thái CHỜ (chưa chạy). Người dùng bấm ▶ Bắt đầu mới chạy. |
| `_job_id_local` | 2400 | 2 | `key` |  |
| `_create_local_shell` | 2404 | 20 | `target, job_key, title, orig_path, group` | Tạo job local (không URL). job_key ổn định theo path để resume cùng file. |
| `_list_local_videos` | 2426 | 24 | `root, recursive, limit` | Liệt kê file video trong folder (sắp tên tự nhiên). |
| `_import_local_video_file` | 2452 | 45 | `src, target, move, title, group` | Copy/move 1 file video → job local. Trả dict ok/job_id hoặc error. |
| `create_job` | 2500 | 24 | `job` | Thêm link -> tạo job CHỜ. 1 link ra NHIỀU video (multi-P/playlist) -> gom |
| `_patch_job_group` | 2526 | 28 | `job_id, group_id, group_title, group_index, group_total, part_title` | Ghi/xoá group_* trong manifest.request. Trả False nếu job không tồn tại. |
| `_renumber_group` | 2556 | 31 | `group_id, title` | Sắp lại group_index 1..N theo index cũ (hoặc created). Trả job_ids còn trong nhóm. |
| `jobs_group` | 2591 | 113 | `body` | Gom job thành series / thêm vào nhóm / đổi tên / tách. |
| `api_queue_jobs_add` | 2707 | 16 | `req` |  |
| `api_queue_jobs_add_group` | 2726 | 16 | `req` |  |
| `api_queue_jobs_opts` | 2745 | 18 | `body` | {"opts_by_job": {job_id: opts}} — đổi opts job đang chờ tại chỗ (nguyên tử, giữ thứ tự). |
| `api_queue_jobs_remove` | 2766 | 5 | `req` |  |
| `_queue_current_detail` | 2773 | 53 | `job_id` | Stage/progress/ETA thô cho dashboard queue. |
| `api_queue_jobs_sort` | 2829 | 6 | `body` |  |
| `api_queue_jobs_move` | 2838 | 13 | `body` |  |
| `api_queue_jobs_reorder` | 2854 | 10 | `body` |  |
| `api_queue_jobs_state` | 2867 | 25 | `` |  |
| `api_queue_jobs_stop` | 2895 | 2 | `` |  |
| `api_queue_jobs_clear` | 2900 | 2 | `` |  |
| `create_local_job_path` | 2905 | 68 | `body` | Tạo job từ file HOẶC folder video local. |
| `create_local_job_batch` | 2976 | 80 | `body` | Import nhiều path (file + folder). Mỗi folder → 1 group riêng. |
| `api_fs_list_dirs` | 3059 | 51 | `path, count_videos, only_with_video` | Liệt kê folder con trực tiếp (1 cấp) để tick chọn import. |
| `api_fs_pick_dir` | 3113 | 71 | `title, initial` | Mở hộp thoại Windows chọn folder (native). Chạy process riêng để không kẹt uvicorn. |
| `api_fs_pick_dirs` | 3187 | 63 | `initial` | Hộp thoại chọn NHIỀU folder cùng lúc (Ctrl/Shift). Trả paths[]. |
| async `create_local_job_upload` | 3253 | 35 | `file, target` | Tạo job từ upload video (không cần link). File lớn nên dùng /local-path. |
| `start_job` | 3291 | 73 | `job_id` | Chạy tự động các bước còn lại (download -> asr -> dịch) cho 1 job. |
| `_run_stage` | 3366 | 104 | `job_id, stage, target, force, voice, speed, engine` | Chạy ĐÚNG 1 bước (người dùng tự chọn). download -> hàng _dl; còn lại -> _gpu. |
| `_save_tts_voice` | 3472 | 3 | `work_dir, voice` | Lưu giọng vào manifest + sidecar — KHÔNG được wipe stages khác. |
| `_save_tts_speed` | 3477 | 2 | `work_dir, speed` |  |
| `_save_tts_engine` | 3481 | 2 | `work_dir, engine` |  |
| `_patch_tts_fields` | 3485 | 41 | `work_dir, **fields` | Ghi field TTS an toàn: đọc đĩa trực tiếp, merge, atomic write. Không dùng |
| `_job_cfg_opts` | 3528 | 9 | `work_dir` | Gộp opts + queue_opts (queue_opts thắng) — dùng khi resolve voice/speed/engine. |
| `_resolve_voice` | 3539 | 24 | `work_dir, voice` | Giọng: arg → stages.tts → tts_config → queue_opts/opts → list[0] (cuối cùng). |
| `_resolve_queue_voice` | 3565 | 22 | `work_dir, voice` | Queue chỉ dùng giọng được chọn rõ ràng hoặc đã lưu cho chính job. |
| `_resolve_speed` | 3589 | 23 | `work_dir, speed` |  |
| `_asr_script` | 3630 | 2 | `engine` |  |
| `_patch_asr_fields` | 3634 | 31 | `work_dir, **fields` | Ghi field ASR an toàn (không wipe stages khác). |
| `_save_asr_engine` | 3667 | 27 | `work_dir, engine` | Lưu engine. Nếu đã có sidecar tương ứng → promote luôn (tránh lệch nhãn/nội dung). |
| `_resolve_asr_engine` | 3696 | 10 | `work_dir, engine` |  |
| `_resolve_tts_engine` | 3708 | 23 | `work_dir, engine` |  |
| `_do_tts` | 3733 | 21 | `work_dir, voice, speed, engine, force` | Chạy TTS clone tiếng Việt (vieneu/omnivoice). Điều phối worker venv riêng |
| `_do_render` | 3756 | 22 | `work_dir, force, audio_mode, voice_vol, orig_vol, add_sub, fit, res…` | Ghép giọng (assemble nếu chưa) rồi render video localize (che+sub+trộn nền). |
| `_render_subprocess` | 3780 | 21 | `work_dir, kw` | V6 F5: render ở tiến trình con (workers.render_worker.run_job). Proc đăng ký vào _PROCS → nút |
| `run_stage` | 3804 | 60 | `job_id, stage, force, voice, speed, engine` | Chạy riêng 1 bước của job (chủ động chọn bước nào làm). Kiểm tra điều kiện |
| `stop_job` | 3867 | 18 | `job_id` | DỪNG mọi việc đang chạy của job (kill subprocess asr/tts/demucs/ffmpeg/aria2c |
| `mark_job_done` | 3888 | 55 | `job_id, body` | Đánh dấu bước (hoặc cả job) = done NGAY — không chạy lại / không chờ. |
| `translate_fill` | 3946 | 44 | `job_id` | VÁ câu trống ở bước Dịch: chạy lại dịch với LÔ NHỎ (25) — run_translate tự |
| `edit_script_line` | 3993 | 103 | `job_id, req` | Sửa tay zh và/hoặc vi: ghi script.json + regen SRT. Không đụng TTS clip |
| `clean_script_spam` | 4099 | 36 | `job_id` | Rút chuỗi 哈/呃 spam trong script.json (ASR lỗi) + prefill vi trivial nếu trống. |
| `set_tts_voice` | 4138 | 12 | `job_id, req` | Lưu giọng TTS đã chọn vào manifest (để nút Chạy / tự động dùng đúng giọng). |
| `_req_audio` | 4152 | 5 | `work_dir, req` | fix 2026-11-11 B6: audio của run-all / mux = trường client GỬI → queue_opts đã pin của job |
| `run_all` | 4162 | 66 | `job_id, req` | 1-CLICK: chạy tự động toàn bộ pipeline từ bước còn thiếu tới hết. |
| `_queue_run_one` | 4230 | 194 ⚠️ | `job_id, opts` | Run one queued job synchronously through mux and return status + detail. |
| `set_asr_engine` | 4427 | 9 | `job_id, req` | Lưu engine ASR (hybrid_fa / fun_asr_nano / …) cho job. |
| `_list_asr_sources` | 4456 | 51 | `work_dir` | Liệt kê asr.json + asr.*.json có dữ liệu (bỏ progress/meta/chars/bak). |
| `_promote_asr_source` | 4509 | 119 | `work_dir, source` | Copy sidecar → asr.json + sub.zh.srt; backup asr.json cũ. |
| `_invalidate_translate_for_asr` | 4630 | 43 | `work_dir, asr_source, n_asr` | Backup script + đánh dấu translate lệch khi đổi asr_source. |
| `_translate_asr_sync_info` | 4675 | 47 | `work_dir` | UI: script có khớp asr.json không? |
| `list_asr_sources` | 4725 | 15 | `job_id` | Danh sách file ASR có sẵn để chọn (Nano / FireRed / …). |
| `select_asr_source` | 4743 | 13 | `job_id, req` | Promote file ASR (vd asr.fun_nano.json) → asr.json + sub.zh.srt. |
| `_context_studio_input` | 4758 | 7 | `work_dir` | Ưu tiên asr.json → sub.zh.srt. |
| `_do_context_studio` | 4767 | 61 | `work_dir, use_llm` | Chạy PRE-SCAN → ghi pack vào work_dir. |
| `get_context_studio` | 4831 | 53 | `job_id` | Xem pack Context Studio (summary + style_lock + characters). |
| `run_context_studio_api` | 4887 | 32 | `job_id, req` | Chạy Context Studio PRE-SCAN → glossary/style_lock/qa_flags trong work/<job>. |
| `get_translate_alts` | 4922 | 16 | `job_id` | Pack analyze checklist + danh sách câu đa nghĩa (vi_alts.json). |
| `run_translate_alts_api` | 4941 | 23 | `job_id, req` | Quét câu đa nghĩa theo chunk + pack analyze → vi_alts.json. |
| `apply_alt_pick` | 4967 | 49 | `job_id, req` | Chốt một phương án alts[pick] → vi (+ regen SRT). |
| `run_hardsub_asr_api` | 5026 | 27 | `job_id, req` | OCR hardsub → rà/sửa chữ ZH trên ASR (giữ timing). Không thay bước dịch. |
| `get_hardsub_asr` | 5056 | 15 | `job_id` |  |
| `_cookie_info` | 5077 | 12 | `path` | Đếm cookie + kiểm định dạng Netscape của 1 file cookie. |
| `cookies_status` | 5092 | 3 | `` |  |
| `_header_to_netscape` | 5101 | 14 | `header, plat` | Chuyển chuỗi header 'name=value; name2=value2' (copy từ DevTools) -> Netscape. |
| async `import_cookies` | 5118 | 43 | `file, text, platform` | Nhập cookies bằng FILE (.txt Netscape từ extension) HOẶC dán TEXT vào popup. |
| `list_jobs` | 5164 | 11 | `` |  |
| `job_detail` | 5178 | 8 | `job_id` |  |
| `_audio_dur` | 5192 | 11 | `path` | Đo độ dài audio (giây) bằng ffprobe. Lỗi -> 0. |
| `_probe_video` | 5205 | 22 | `path` | Rút width/height/duration/vcodec bằng ffprobe. Lỗi -> {}. |
| `_clear_job_sources` | 5229 | 10 | `work_dir` | Xoá source.* / mảnh .part cũ trước khi import. |
| `_link_or_copy` | 5241 | 9 | `src, dest` | Hard link nếu được (cùng ổ — không tốn thêm dung lượng); khác ổ / FS không hỗ trợ mới copy. |
| `_direct_source_roots` | 5252 | 3 | `` |  |
| `_is_direct_source` | 5257 | 13 | `src` | File trong thư viện (SOURCE_DIRECT_ROOTS) → job đọc thẳng video_path, không đặt file vào job. |
| `_place_source` | 5272 | 11 | `src, work_dir, move` | Trả (đường dẫn video job dùng, cách đặt: direct/move/link/copy). |
| `_finalize_imported_source` | 5285 | 42 | `work_dir, dest, orig_name, source_kind, source_mode` | Ghi meta + mark download done. `dest` có thể nằm ngoài job (source_mode=direct: file thư viện). |
| `_find_video` | 5329 | 10 | `work_dir` | Tìm file video đã tải: ưu tiên video_path trong manifest, fallback glob. |
| `job_video` | 5342 | 12 | `job_id` | Stream video đã tải để preview. FileResponse tự xử lý Range -> tua được. |
| `_write_srt` | 5364 | 54 | `work_dir, lang` | Sinh .srt từ json nguồn, ghi ra sub.<lang>.srt. Trả (path, nội dung) hoặc |
| `_clone_voice_list` | 5420 | 3 | `` | Giọng clone dùng chung cho cả 2 engine (registry voices/f5_voices.json). |
| `list_voices` | 5426 | 5 | `` | Giọng clone (ref audio + lời) dùng chung — cả vieneu lẫn omnivoice đều |
| `list_frames` | 5434 | 7 | `` | Danh sách PNG khung trong assets/overlays/frames. |
| `list_brand_logos` | 5444 | 7 | `` | Logo kênh (assets/overlays/logos hoặc brand). |
| `_ref_asr_enabled` | 5460 | 20 | `` | ASR mẫu: mặc định tắt nếu chưa có model (tránh treo tải Whisper 2GB lúc Thêm giọng). |
| `_probe_audio_dur` | 5482 | 10 | `path` |  |
| `_transcribe_ref` | 5494 | 12 | `path` | Lấy transcript của clip mẫu (F5 cần ref_text). Model medium int8 nhẹ VRAM. |
| `_prep_ref` | 5508 | 60 | `raw, max_dur` | Chuẩn hoá clip mẫu: cắt ≤max_dur, xuất wav 24k mono. Trả (wav, ref_text, dur). |
| `clone_voice` | 5571 | 25 | `name, file` | Clone 1 giọng từ file mẫu (3-8s). Đăng ký vào registry chung (VieNeu/OmniVoice). |
| `voice_sample` | 5604 | 38 | `engine, voice, speed` | Đọc câu mẫu bằng engine+giọng (+ tốc độ) để nghe thử. Cache trên đĩa. |
| `tts_wav_ok` | 5645 | 13 | `job_id` | Danh sách index seg_*.wav còn trên đĩa (size OK) — UI khỏi báo thiếu oan khi chưa có tts.json. |
| `regen_tts` | 5661 | 91 | `job_id, req` | Lồng tiếng: mặc định RESUME (chỉ đọc câu thiếu). force=true -> đọc lại toàn bộ. |
| `regen_clip` | 5755 | 14 | `job_id, req` | Đọc lại DUY NHẤT 1 câu (index i) khi clip đó lỗi. Nhanh, không đụng câu khác. |
| `job_srt` | 5772 | 13 | `job_id, lang` | Sinh .srt từ asr.json (zh) / script.json (vi), ghi ra file trong job rồi |
| `_reveal_path` | 5787 | 12 | `target` | Mở trình quản lý file, bôi sẵn đúng file (nếu target là file) hoặc mở thư mục. |
| async `upload_source` | 5807 | 35 | `job_id, file` | Import source video qua upload (stream, không nhét cả file vào RAM). |
| `import_source_path` | 5845 | 37 | `job_id, body` | Import source từ đường dẫn local (cùng máy server) — phù hợp file multi-GB. |
| async `upload_audio` | 5885 | 31 | `job_id, file` | Nhập 1 file audio lồng tiếng LÀM SẴN ở ngoài (từ SRT) để dùng cho mux. |
| `delete_audio` | 5919 | 14 | `job_id` | Bỏ audio ngoài đã nhập -> quay lại dùng TTS nội bộ. |
| `_translate_batch_size` | 5935 | 10 | `` | Cỡ lô dịch — đọc TRANSLATE_BATCH (mặc định 20). Không hardcode 100. |
| `_translate_flow` | 5947 | 30 | `work_dir, target, model, review_model, review_only, mode, force` | Luồng dịch: (tùy chọn) DỊCH NHÁP rồi (tùy chọn) SOÁT lại lượt 2. |
| `retranslate_job` | 5980 | 69 | `job_id, req` | Dịch job: 1 lượt, HOẶC 2 lượt (nháp + soát), HOẶC chỉ soát bản có sẵn. |
| `mux_job` | 6052 | 26 | `job_id, req` | Render video localize -> output.mp4 (che watermark/hardsub + sub Việt + OCR |
| `assemble_job` | 6081 | 19 | `job_id, fit, max_speed` | Ghép clip TTS thành 1 track. |
| `get_split_plan` | 6103 | 19 | `job_id, target_hours` | Xem trước kế hoạch chia part thông minh cho video dài (trước khi chia). |
| `api_split_job_parts` | 6125 | 8 | `job_id, target_hours` | Chia video dài thành các sub-jobs (Part 1, Part 2...) để chạy pipeline độc lập chống lệch time. |
| `api_merge_job_parts` | 6136 | 8 | `job_id` | Ghép các sub-jobs đã render hoàn chỉnh thành bản Full output_full.mp4 (lossless concat 30s). |
| `reveal_job` | 6147 | 25 | `job_id, file` | Mở thư mục job trong Explorer, chọn sẵn 1 file. Không truyền file -> chọn |
| `job_file` | 6175 | 50 | `job_id, path` | Phục vụ 1 file BÊN TRONG work/<job_id>/ (asr.json, script.json, mp3...). |
| `delete_job` | 6228 | 16 | `job_id` | Xoá 1 job = xoá cả thư mục work/<job_id>/ (video, audio, manifest...). |
| `_queue_park_ready` | 6262 | 37 | `job_id` | True khi job đang park (HOLD/soát) có thể chạy tiếp TTS — không nhảy job khác. |
| async `_hg1_hg2_split` | 6425 | 29 | `request, call_next` | Tách hoàn toàn Hongguo1 / Hongguo2. |
| `_title_write_hook` | 6456 | 13 | `work_dir, data` | V6 F3: dịch tiêu đề lazy khi manifest của job đổi (trước: gọi trong GET read_job mỗi lần poll). |
| `_start_title_translate` | 6472 | 23 | `` | Đăng ký hook + quét 1 lần lúc khởi động (job cũ còn tiêu đề Trung). Không chạy trong pytest. |
| `_startup_split_merge_heal` | 6498 | 23 | `` | V6 F3: parent auto-split có đủ part xong mà chưa ghép Full (worker chết/restart) → xếp ghép. |
| `_start_background_services` | 6524 | 14 | `` | V6 F4: autopilot/feeder/runwatch/film_db/config-live (+ auto-publish nếu cờ) chạy TRONG server |
| `_start_agent_watch` | 6541 | 13 | `` |  |
| `_start_agent_brain` | 6557 | 18 | `` | Second Brain — daemon full-silent (ops + quality + infra). |
| `api_agent_brain_tick` | 6578 | 10 | `` | Chạy 1 tick Second Brain thủ công (ops/debug). |
| `get_available_llm_models` | 6592 | 7 | `` | Trả về danh sách toàn bộ các model LLM khả dụng (llama-server :8083, Ollama, 9router). |

**Hàm dài ≥120 dòng:** `_auto_gpu` (275, L1754), `_queue_run_one` (194, L4230), `_check_and_run_auto_split_parts` (145, L1479)

### Config / env keys (20)

`AGENT_QA_GATE`, `DL_WORKERS`, `HF_HUB_DISABLE_SYMLINKS`, `HF_HUB_DISABLE_SYMLINKS_WARNING`, `HG_BACKEND`, `HG_HELPER_SINGLETON`, `HOST`, `JOB_WORKERS`, `PINELINE_PORT`, `PINELINE_ROUTERS`, `PORT`, `PYTHONIOENCODING`, `QUEUE_REVIEW_RETRY_MAX`, `QUEUE_REVIEW_RETRY_SEC`, `REF_ASR`, `REF_ASR_DEVICE`, `REF_ASR_MODEL`, `TRANSLATE_BATCH`, `VIENEU_BOOT_SEC`, `WORK_DIR`

**Cổng nhắc tới:** 8000

**File/artifact nhắc tới:** `.bak.json`, `_cookies.txt`, `_f5.wav`, `asr.aligned.json`, `asr.aligned_fa.json`, `asr.aligned_fix.json`, `asr.aligned_n1.json`, `asr.aligned_repaired.json`, `asr.consensus.json`, `asr.firered.json`, `asr.fun_nano.json`, `asr.fun_nano_retry.json`, `asr.hardsub.json`, `asr.json`, `asr.nano_fa.json`, `asr.paraformer_zh.json`, `asr.sensevoice.json`, `bgm.wav`, `context_pack.json`, `dub_fit.wav`, `glossary.json`, `hardsub_cues.json`, `manifest.json`, `meta.json`, `no_vocals.wav`, `ocr.json`, `output.mp4`, `output.partial.mp4`, `output_full.mp4`, `qa_flags.json`, `script.display.json`, `script.json`, `script.pre_agent_review.json`, `source.mp4`, `sub.vi.srt`, `sub.zh.srt`, `tts.json`, `tts_config.json`, `vi_alts.json`, `vocals.wav`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=205 · file_writes=23 · global_stmt=2 · pools=3 · subprocess=10 · swallow_except=66 · threads=2

### Lịch sử (từ docs/)

- **Fix logs (3):**
  - [2026-11-11 — F4 (services + sidecar thuần) + F5 (render tiến trình con)](../../fixes/2026-11-11-f4-f5.md)
  - [2026-11-11 — F2 (core/job_opts) + F3 tích hợp (1 tiến trình V5+V6, UI cũ hết polling)](../../fixes/2026-11-11-f2-f3-integration.md)
  - [2026-11-11 — F1 (B9): mọi ghi manifest qua state/](../../fixes/2026-11-11-f1-b9-single-write-path.md)
- **Reviews:** [2026-11-11-review-f4-f5](../../reviews/2026-11-11-review-f4-f5.md), [2026-11-11-review-f2-f3](../../reviews/2026-11-11-review-f2-f3.md)
- Nhắc trong CHANGELOG: **1** lần · DECISIONS: **0** lần

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
