# 2026-11-11 — F2 (core/job_opts) + F3 tích hợp (1 tiến trình V5+V6, UI cũ hết polling)

## Triệu chứng
1. Tuỳ chọn job (giọng, âm nền, zoom, brand, hardsub_blur…) quyết ở 5 chỗ: `batch_config.resolve_job_opts`,
   `server._req_audio`, đoạn stamp queue_opts trong `_queue_run_one`, `batch_config.render_kwargs`, dict `render_kw`
   dựng tay trong run_all / hàng chờ. Cùng 1 job bấm từ UI / hàng chờ / agent có thể ra opts khác nhau
   (run_all lấy mặc định cứng của `RunAllReq` cho trường không gửi; hàng chờ lấy config batch; agent lấy ghim ⊕ batch).
2. **Bug thật**: `@app.post("/api/jobs/{job_id}/run-all")` nằm TRÊN `def _req_audio` (chèn lúc fix B6) →
   POST /run-all gọi `_req_audio(work_dir, req)` (FastAPI coi là query) → 422; `run_all` không có route.
   Có ở **cả V2 (production), V4, V5** (server.py dòng 4229) — V6 đã sửa; V2/V4/V5 KHÔNG đụng (luật).
3. `python -m app --instance v6` chỉ có route mới; toàn bộ API V5 (server.py, http_routes) chưa gắn.
4. UI cũ (index3.html, index.html) poll `/api/jobs` 2 s (pollLoop) + `setInterval(...refresh...,2000)` khi hàng chờ chạy;
   banner «Mất kết nối» bật cả khi lỗi JS lúc vẽ.
5. GET có side effect: `GET /api/jobs/{id}` xếp ghép parent auto-split; `read_job` ghi `n_missing` (js.persist),
   ghi nhãn ASR (`_patch_asr_fields`), kích dịch tiêu đề bằng LLM (`_maybe_translate_title`).
6. `list_jobs` đọc lại script.json của MỌI job mỗi lần.
7. `WORK_ROOT = Path("work")` tương đối cwd; `_iter_job_engine_procs` khớp chuỗi `work/<job_id>` → có thể khớp
   nhầm engine của cây khác (V2) trùng job_id.

## Sửa
### F2 — `core/job_opts.py` (một nguồn)
- Ưu tiên: **tường minh (lần chạy này) > queue_opts ghim > config batch > DEFAULTS**; `None` không đè; `pin_opts` bỏ.
  Ngoại lệ: `voice` KHÔNG lấy từ ghim (giọng job ở stage tts, UI «chọn giọng» có thể mới hơn ghim).
- API: `resolve(explicit, pinned, batch, fill_voice=)`, `for_job(wd, explicit)`, `read_pinned`, `explicit_from_req`
  (pydantic `model_fields_set` — trường gửi thật), `normalize` (kiểu như stamp V5), `audio()` (duck_bgm/0.2/0.22),
  `pipeline_render_kw` (render_kw cho _auto_pipeline), `render_kwargs` (nguyên văn V5), `pin_subset`, `merge` (ngữ nghĩa cũ).
- `batch_config.resolve_job_opts/render_kwargs` → lớp mỏng gọi job_opts (code/test cũ không vỡ).
- Nối dây: `run_all`, `mux_job`, `_req_audio`, `_queue_run_one`, run_stage mux, `POST /api/queue/jobs` (+group, +opts),
  `routes_agent._agent_mux_run/_agent_resume`. `job_queue.queue_add(..., opts_for=)` để chụp opts RIÊNG từng job
  (ghim của job đó) lúc xếp hàng.
- run_all / Mux tay ghim bộ «đầu ra» (`pin_subset`: âm thanh + render) vào queue_opts (merge) → agent mux_run /
  resume / run_stage mux sau đó render y hệt lần người dùng bấm. Hàng chờ ghim toàn bộ opts như V5.
- Sửa decorator run-all (mục 2).

### F3 — tích hợp
- `app/main.py`: `create_app(inst)` (mặc định legacy=True) import `server` SAU khi `inst.apply_env()`, gắn lên
  `server.app`: `/api/health`, `/api/events` (SSE), `/api/v6/jobs`, `/api/v6/jobs/{id}` (thuần đọc), UI desktop ở `/v6/`.
  UI cũ giữ đường cũ (`/`, `/ui3`…). Gắn lần 2 → trả app cũ (không đăng ký startup 2 lần). Startup V6 chạy SAU startup
  server (không reset stage lần 2: server đã reset + sửa TTS), rồi index.load_all + watcher + bus flusher.
  `legacy=False` = app V6 đứng riêng như F0.
- `app/__main__.py`: `os.chdir(gốc cây)`; khoá 1 backend/cổng (`scratch/.backend_<port>.lock`) — uvicorn chạy startup
  TRƯỚC khi bind, bản thứ 2 sẽ kill engine «orphan» của bản đang chạy.
- `server.py`:
  - `WORK_ROOT = Path(os.getenv("WORK_DIR") or "work")` (V6 tuyệt đối; chạy server.py kiểu cũ vẫn "work").
  - `_instance_flag(name)`: đọc `PINELINE_FLAG_<NAME>` (apply_env đặt). Chặn cứng: Agent watcher, Second Brain
    (config.json có `AGENT_BRAIN=1` kiểu file-thắng-env nên env không đủ), auto-publish sau job xong, tự chạy tiếp
    hàng gộp Hongguo (:8000). Router tuỳ chọn theo `instance.routers` (`PINELINE_ROUTERS`). Telegram: apply_env đặt
    `AGENT_TELEGRAM=0` khi `telegram=false` (mọi gửi đi qua `agent_watch.telegram_send`, đọc env này).
  - GET thuần đọc: bỏ ghép parent khỏi `job_detail` → `_startup_split_merge_heal` (quét lúc khởi động) + đường cũ
    `_notify_parent_after_part_done` khi part xong; `read_job` chỉ tính n_missing (không persist) — đồng bộ khi SỬA
    script (`_sync_translate_missing`, js.mutate, trong `edit_script_line`); nhãn ASR tính không ghi
    (`_sync_asr_labels(persist=False)`); dịch tiêu đề chuyển sang write hook manifest + quét 1 lần lúc khởi động.
  - `_script_missing()`: cache (mtime_ns, size) của script.json → list_jobs không đọc lại file không đổi.
  - `_LOCAL_HOSTS` thêm `tauri.localhost` (origin WebView2).
- `core/instance.py`: `flags()`; apply_env ghi đè `PINELINE_FLAG_*`, `PINELINE_ROUTERS`, AGENT_* = 0 khi cờ tắt.
  `instances/*.json`: thêm `auto_publish`, `hg_merge_resume` (v6/test: false; prod: true).
- `state/index.py`: ghi mà tóm tắt không đổi → `job.touched` (bus gộp theo job, `THROTTLED`) để UI cũ biết tiến độ đổi.
- UI cũ: `static/live_sync.js` (thuần, test bằng Node) — EventSource `/api/events`; snapshot/job.updated/job.removed/
  stage.progress/job.touched → `refresh()` gộp 300 ms, không chạy chồng; dự phòng 30 s; SSE lỗi ≥3 lần → poll 5 s, thử
  SSE lại sau 60 s. index3.html + index.html: bỏ `setInterval(...,2000)` hàng chờ và pollLoop 2 s/5 s; refresh tách
  try/catch: lỗi mạng/HTTP → `setConn(false)`, lỗi vẽ → `console.error`.
- Tauri `main.rs`: mở `/v6/`, chờ backend 180 s (nạp server.py lâu hơn F0).

## Quyết định (ghi lại vì có đổi hành vi nhỏ)
- run_all / Mux tay: trường client KHÔNG gửi giờ lấy ghim → batch (trước: mặc định cứng của RunAllReq/MuxReq, vd
  orig_vol 0.6, audio voice_bgm). UI index3 gửi đủ trường nên UI không đổi; chỉ client gửi thiếu (agent/script) bị ảnh hưởng —
  đúng ý B6.
- run_all: `translate_mode` không gửi → batch ("batch") thay vì None (env TRANSLATE_MODE — trong config.json cũng "batch").
  `asr_engine` vẫn chỉ khi client chọn (không đè engine đã lưu của job).
- speed_factor: giữ nguyên V5 — Mux tay/hàng chờ/1-click = 1.0; chỉ run_stage mux áp `render_speed` (khác biệt có từ V5,
  để F4 quyết).
- Xếp hàng từ UI: ghim của TỪNG job thắng batch (trước: batch lúc xếp đè ghim).
- Dịch tiêu đề / ghép parent sau restart giờ chạy lúc khởi động (trước: lúc UI GET) — cùng việc, đổi thời điểm.

## Backup
`.backup_edit_20261111_f2f3/` — mọi file sửa (cp --parents) trước khi ghi.

## Recheck
- **Cloud (đã chạy)**: `python3 -m pytest -q -p no:cacheprovider --ignore=tests/v5 tests` → xem số trong báo cáo/worklog;
  tests/v5 (không fastapi) giống hệt baseline trước sửa (cùng tập fail/err do thiếu deps). Thêm: chạy tests/v5 với
  stub fastapi/yt_dlp/soundfile (KHÔNG commit stub) → cùng tập pass/fail như cây gốc; test_optimize_b5_b6 (B6),
  test_queue_run_one, test_hardsub_blur_toggle, test_park_review_retry Pass.
  `node --check` khối <script> index3/index, live_sync.js, desktop/ui/app.js: Pass. Logic live_sync chạy thật bằng Node (test).
- **Chỉ kiểm được trên Windows**: `tests/test_app_integration.py` (TestClient: /api/health, /api/v6/jobs, /api/events
  snapshot, /api/jobs legacy, GET không ghi, cache script.json, /v6/ UI); khởi động `run_v6.bat` thật (import server.py
  đủ deps); Tauri build (`desktop\build.bat`) với main.rs mới; index3 trong trình duyệt (SSE, banner); 1 job thật.

## Review độc lập (review1, 11-11) — docs/reviews/2026-11-11-review-f2-f3.md
- Sửa: dịch tiêu đề gọi LLM không có cờ → cờ `title_translate` (v6/test tắt); hàng gộp Hongguo dùng chung `<ổ>/work/logs`
  với V2 + GET /api/hg/merge-queue lách cờ `hg_merge_resume` → theo WORK_DIR + chặn theo cờ; hàng chờ tự chạy lúc khởi động
  → cờ `queue_autostart` (v6/test tắt); khoá 1 backend thêm theo work root; live_sync.js: CLOSED → poll ngay, tiến độ gộp 2 s,
  dự phòng 5 s khi có job chạy, tab ẩn không gọi API.
- Backup `.backup_edit_20261111_review1/`. Recheck cloud: 105 passed, 1 skipped (`--ignore=tests/v5`); tree_audit ERROR=0.
