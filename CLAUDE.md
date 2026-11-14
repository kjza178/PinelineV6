# Pineline V6 — một codebase, nhiều instance

Mục tiêu: thay V2/V4/V5 (**chưa cutover** — V2 vẫn là production). Thiết kế đích: docs/design/2026-11-11-v6-architecture.md.

## Đọc trước (theo thứ tự)
1. `docs/ROADMAP.md` — kế hoạch, việc đang làm, trạng thái từng mục.
2. `docs/WORKING_MODEL.md` — cách làm một việc: tái hiện → sửa → kiểm → commit → CI → tài liệu; ba mức «đã kiểm» (cloud / Windows / job thật).
3. `docs/V6_HANDOFF.md`, `docs/STATE.md` — bàn giao và trạng thái máy thật.
(Trên máy chủ dự án còn `Z:\PinelineV2\docs\HANDOFF.md` — nằm NGOÀI repo; không có thì bỏ qua.)

## Hiện trạng — đừng nhầm đích với thực tế
- Khung V6 đã có và dùng thật: `core/instance`, `state/` (ghi manifest một cửa), `events/` (SSE), `workers/render_worker`.
- **Lõi vẫn là `server.py` (≈6.900 dòng) + `engines/` + `agents/` của V5.** `app/routes/` và `sidecar/` còn trống;
  `runtime.Runtime` được tạo nhưng server chưa dùng (vẫn `_PROCS`/`_STOP_JOBS`/`_STAGE_BUSY`); `pipeline/` mới có dịch simple.
- **V6 chưa chạy job thật nào.** Mọi pha «xong» bên dưới = xong ở cloud (unit test), chờ kiểm trên Windows.
- Các «Luật» dưới đây là luật cho code MỚI / code đang sửa; code cũ chưa đạt hết (xem ghi chú từng dòng).

## Luật
- Ghi trạng thái job CHỈ qua `state/job_state.py`: hàm ngữ nghĩa (mark_*, set_hold, set_queue_opts, set_review_retry…) hoặc `js.mutate(wd, fn)`. `js.persist(m)` chỉ cho code V5 chưa tách — số lượng chỉ được GIẢM (tests/test_f1_ratchet.py). Cấm `.save()`.
- Route mới đặt trong `app/` và phải mỏng: không logic pipeline, không side effect ở GET. (Hiện trạng: route vẫn ở
  `server.py` + `http_routes/`; còn `GET /api/voice-sample`, `GET /api/cookies` ghi đĩa — ROADMAP R5.3.)
- UI không polling — mọi cập nhật qua `events.bus` → SSE `/api/events`. (Đạt: `desktop/ui`. Chưa: `films_ui.html`,
  `hongguo*.html`, vài hẹn giờ trong `index3.html`.) UI desktop: để yên = 0 request.
- Cổng/đường dẫn lấy từ `core.instance` (instances/*.json), không hardcode. (Còn: `FIRERED_ROOT` trong config trỏ cây V2 — G0.5.)
- Render/mux chạy `workers/render_worker.py` (tiến trình con) khi instance bật `render_subprocess` (v6/test bật, **prod đang tắt**).
- Cổng/thư mục phụ thuộc MÁY (llama, presets, Aurora, Hongguo, model MT, thư viện, yt_uploader) lấy từ `core/machine.py` (key config), không hardcode. Kiểm máy: `scripts/check_env.py`.
- Test: `python -m pytest -q tests` (AGENT_BRAIN=0) — phải 0 fail trên cả Linux và Windows; CI (`.github/workflows/ci.yml`)
  chạy mỗi PR. Không skip/xfail test để xanh. Test không ghi ra ngoài thư mục tạm.
- Không kill :8000 / :8083. Sao lưu = git (commit nhỏ, có mã mục ROADMAP); không chép thư mục backup vào repo.
- Không nuốt lỗi im lặng trong code mới: `except` phải ghi log đủ tìm nguyên nhân.

## Chạy
- Lần đầu: `scripts\setup_f1_links.bat` (junction work/models/assets/voices, copy cookies)
- Backend: `run_v6.bat` → http://127.0.0.1:8806/ (UI cũ) · /v6/ (UI mới)
- Sidecar (tuỳ chọn): `run_sidecar_v6.bat` → :8816 (Telegram theo cờ instance; v6 tắt)
- Desktop: `desktop\dev.bat` (dev) / `desktop\build.bat` (installer)

## Pha
F0 khung (xong) → F1 B9 chuyển mọi save() (xong) → F2 core/job_opts (xong) → F3 D events + UI (tích hợp V5+V6 1 cổng, UI cũ SSE: xong ở cloud, chờ Windows; còn Runtime/Stage-Gate) → F4 C services + sidecar thuần (xong ở cloud, chờ Windows) → F5 E render worker tiến trình con (xong ở cloud, chờ Windows; prod `render_subprocess=false` tới khi so job thật) → F6 lượt kiểm cuối (B11 phần an toàn, `verify_all.bat`, brain, `docs/V6_HANDOFF.md`: xong ở cloud, chờ Windows) → **cutover: user quyết** (chạy `verify_all.bat` + checklist HANDOFF trước).
- Render: sửa logic ở `engines/render_job.py` (một nguồn cho worker + đường in-process cũ). Không thêm `importlib.reload`.
- Cổng/URL trong agents/tools: `core.instance.server_base()/sidecar_url()` — không mặc định 8801/8802 (tests/test_f4_services.py).
- Việc nền (autopilot/feeder/film_db) ở `services/`, chạy trong server theo cờ `feeder`; sidecar chỉ Telegram + HTTP mỏng.
  (Hiện trạng: `services/` là lớp bọc gọi lại `agents/*`; sidecar thật vẫn là `agents/agent_tele_cmd.py` — ROADMAP S5.)
- Script ops (restart_server, migrate_work) chỉ nhắm tiến trình của CÂY + instance (`mw.server_procs`/`instance_procs`); không `procs("…")` trần. HTML cũ có :8801/:8802 → phục vụ qua `core.instance.localize_html`.
- Kill tiến trình con có cháu (render_worker, engine): `workers.render_worker.kill_tree`, không chỉ `terminate()`.
- Opts job CHỈ quyết qua `core/job_opts.py` (resolve/for_job/normalize/render_kwargs). Không dựng dict opts tay.
- Dịch vụ nền trong server.py phải hỏi `_instance_flag(...)` (cờ instance), không chỉ env AGENT_*.
- Kiểm toàn bộ trên Windows: `verify_all.bat` (log `scratch\verify_all_*.log`). Spawn tiến trình con ghi manifest → bọc `state.watcher.watching(job_id)`.
