# STATE — Pineline V6

- **Pha:** F0 xong · F1 xong · **F2 xong** (`core/job_opts.py`, gate test cloud) · **F3 tích hợp xong ở cloud, chờ Windows**
  · **F4 (services/sidecar) + F5 (render subprocess) xong ở cloud, chờ Windows**
  (`tests/test_app_integration.py`, chạy `run_v6.bat`, build Tauri). Còn F3: Runtime/Stage-Gate thay `_auto_gpu`, route không `import server`.
- **Chạy:** `run_v6.bat` / desktop `desktop\dev.bat` (:8806). Cùng cổng: UI cũ `/` + `/ui3` (index3, SSE không poll),
  UI mới `/v6/`; API V5 nguyên đường cũ; API V6: `/api/health`, `/api/events`, `/api/v6/jobs[/{id}]`.
- **F4 + F5 xong ở cloud, chờ Windows** (docs/fixes/2026-11-11-f4-f5.md): render qua `workers/render_worker.py` (cờ
  `render_subprocess`: v6/test bật, **prod tắt** tới khi so 1 job thật); việc nền trong server theo cờ `feeder` (v6/test tắt);
  sidecar V6 = `run_sidecar_v6.bat` (:8816, Telegram tắt, không vòng nền); không còn vòng /cmd.
- **Nợ đếm được:** `js.persist` = 91 (ratchet, P6 11-12). `importlib.reload` còn ngoài đường render: server.py 4 (Demucs ×2,
  clone_tts, translate), routes_hg 43 (→ services/hongguo_flow), agent_tele_cmd 30.
- **Bug ở V2 production chưa sửa (không đụng V2):** route POST /run-all gắn nhầm vào `_req_audio` (server.py:4229 ở V2/V4/V5).
- **An toàn:** cờ instance v6 tắt agent_brain/agent_watch/telegram/auto_publish/hg_merge_resume/title_translate (LLM)/queue_autostart (chặn cứng trong server.py; hàng gộp Hongguo theo WORK_DIR — review1); WORK_DIR riêng D:\PinelineV6_work; sidecar 8816 (`run_sidecar_v6.bat`). Không đụng V2 :8801/:8802, :8000, :8083.
- **Review F2/F3:** docs/reviews/2026-11-11-review-f2-f3.md (4 lỗi cao/trung bình đã sửa; còn L1–L6 ghi nhận).
- **Review F4/F5 (review2):** docs/reviews/2026-11-11-review-f4-f5.md — 2 nghiêm trọng + 3 cao đã sửa (UI cũ không còn gọi
  :8802/:8801; restart_server/migrate_work chỉ nhắm cây+instance, từ chối cổng V2; Dừng render kill cả cây). Còn L4–L6.
  `restart_server.py --instance v6 [--sidecar]` — instance prod bị từ chối trừ `--allow-foreign-port`.
- **F6 (lượt kiểm cuối, 11-11)**: audit chéo pha + B11 phần an toàn + `verify_all.bat` + brain V6 + bàn giao
  **`docs/V6_HANDOFF.md`** (trạng thái gate từng pha, checklist Windows, việc user quyết). Sửa: tiến độ tiến trình con
  (render worker/engine) lên UI ngay (watcher theo dõi job khi spawn — trước trễ ≤ 30 s). Cloud 192 passed / 2 skipped.
  **Chưa cutover**; việc tiếp theo: user chạy `verify_all.bat` rồi checklist HANDOFF mục 3. docs/fixes/2026-11-11-f6-final.md
- **Máy khác cấu hình (11-11, machine-config):** key máy trong `core/machine.py` (`MACHINE_KEYS`) + mẫu
  `config/pineline.config.example.json`; doctor `python scripts\check_env.py [--offline]`; hướng dẫn `docs/SETUP_MAY_MOI.md`.
  Đã port tương đương sang V5 (`core/machine.py`) và V4 (`machine.py`, chưa commit git — user tự commit).
- **Port simple + Demucs (11-12, F6):** luồng dịch simple là MẶC ĐỊNH (Stage/Gate thật đầu tiên trong `pipeline/`,
  `_auto_gpu` gọi qua Runner); Demucs trước ASR mặc định bật, lỗi → audio gốc. Rollback theo instance:
  `"translate_pipeline": "legacy"`, `"asr_use_demucs": false`. Cloud 354 passed / 9 skipped. Chưa chạy job thật trên V6.
  docs/fixes/2026-11-12-port-simple-demucs.md
- **Port brain/HOLD + luồng dịch theo job (11-12, P6):** tin Telegram theo mẫu, gỡ HOLD khi chạy tay (UI + Telegram/chat Agent),
  hoãn heal 180 s, đuôi log engine/render, `translate_pipeline` đóng dấu theo job (job dở luồng cũ làm nốt). Cloud 494 passed
  (trước 440), không fail mới. Brain/telegram vẫn tắt ở instance v6. docs/fixes/2026-11-12-port-brain-hold-pipeline.md
- **Port preflight/slice/junk (11-13, Q6):** nguồn video kiểm 1 lần (sau tải / lười ở stage đầu), lỗi audio → `source_norm.*`;
  extract_pcm bền; FireRed slice tại khoảng lặng; câu rác + nhận nháp ≤ tolerance; parent chờ part park. Cloud 577 → 667 pass,
  cùng fail. Chưa chạy job thật trên V6; cần restart :8806. docs/fixes/2026-11-13-port-preflight-slice-junk.md
- **Port brain job xong / park ≠ HOLD (11-13, R6):** heal/watch/brain/LLM bỏ job đã xong (đã đăng, đã ghép Full…);
  park tự thử lại báo ⏸ (không «Cần bạn»), HOLD cần người ⛔; «Chạy tiếp» reset bộ đếm park. Cloud 669 → 692 pass, cùng fail.
  Cần restart :8806. docs/fixes/2026-11-13-port-brain-finished-hold.md
