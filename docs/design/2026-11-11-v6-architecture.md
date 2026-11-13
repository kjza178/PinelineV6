# Pineline V6 — kiến trúc (A–E) + desktop Tauri

- **Date:** 2026-11-11
- **Quyết định nền (DECISIONS 11-11):** dựng trên V5 package (đã có hotfix B1–B10) + phần riêng V4 · **thay cả V2/V4/V5** (1 codebase, nhiều instance) · render chạy **subprocess** mỗi job (bỏ `importlib.reload`) · **desktop Tauri**.
- **Cách làm:** dựng **khung đích trước**, rồi chuyển logic V5 vào theo pha. Không viết lại thuật toán ASR / dịch / TTS / render (đã qua >100 fix log).
- **Thư mục:** `Z:\PinelineV6` · cổng **8806** · `work` = `D:\PinelineV6_work` · `TEMP` = `D:\PinelineV6_tmp`.

---

## 1. Vì sao UI treo (bằng chứng) → điều V6 phải đổi

| Nguồn | Chi tiết |
|---|---|
| `static/ui/index3.html` | `setInterval(() => { paintQueueStatus(); refresh(); }, 2000)` → mỗi 2 s: `GET /api/jobs` + `GET /api/jobs/{selected}` + `GET /api/queue/jobs` |
| `server.list_jobs` | Mỗi lần gọi: duyệt **mọi** `work/<id>/`, `read_job()` (~140 dòng: đọc manifest có khoá, stat file, đọc tts/script) trên junction `work → D:` |
| `films_ui.html` | 4 `setInterval` + 71 `fetch` |
| `GET /api/jobs/{id}` | Có **side effect** (`_queue_split_parent_merge_if_ready`) — GET không được ghi |

Với ~100 job: ~50 manifest/giây chỉ để vẽ lại danh sách, tranh khoá với luồng pipeline đang ghi manifest → UI + server cùng khựng.

**Tauri trên Windows dùng WebView2** — cùng engine với `app_desktop.py`. Đổi vỏ không hết treo; phải đổi **luồng dữ liệu**:

1. **Job index trong bộ nhớ** (`state/index.py`): cập nhật khi `state.*` ghi manifest; `GET /api/jobs` trả từ index, O(1), không đọc đĩa.
2. **Một luồng sự kiện** `GET /api/events` (SSE): `job.updated`, `stage.progress`, `queue.changed`, `hold.set/cleared`, `log.line`. UI subscribe 1 lần, không polling. Tiến độ gộp (throttle 4 lần/giây/job).
3. **GET không có side effect.** Mọi việc tự động (merge part, heal) chạy ở runtime/brain.
4. **Tauri** làm vỏ: khởi chạy/giám sát backend Python (sidecar process), 1 cửa sổ, tray, thông báo native; UI là bundle tĩnh trong `desktop/ui` (không cần trình duyệt mở tab).

---

## 2. Cây thư mục đích

```
PinelineV6/
├─ app/                     FastAPI MỎNG — chỉ route + middleware, không giữ state
│  ├─ main.py               create_app(instance) · include_router · startup gọi runtime.start()
│  └─ routes/               jobs, queue, stages, agent, hongguo, films, uploader, events(SSE), ui
├─ runtime/                 STATE TOÀN CỤC của tiến trình (thay biến module trong server.py)
│  ├─ context.py            Runtime: stop_jobs, stage_busy, pools (_dl, _gpu), procs
│  ├─ queue_worker.py       vòng worker 1 ghế (từ core/job_queue) + park/retry (B2)
│  └─ engines.py            spawn_engine(): subprocess ASR/TTS/render, PYTHONPATH, kill theo job
├─ state/                   NGUỒN DUY NHẤT ghi trạng thái job  [B9]
│  ├─ job_state.py          set_stage / mark_running|done|failed / set_hold / clear_hold /
│  │                        set_queue_opts / set_progress — đều qua Manifest.update
│  └─ index.py              JobIndex trong bộ nhớ + phát event khi đổi
├─ events/
│  └─ bus.py                pub/sub trong tiến trình + SSE stream, throttle tiến độ
├─ pipeline/                Stage + Gate tường minh  [D]
│  ├─ base.py               Stage, Gate, GateResult(pass|park|hold|fail), Runner
│  ├─ stages/               download, asr, translate, review, tts, separate, ocr, render
│  ├─ gates/                ready_for_tts, review_complete (retry), quality (Brain), hold, qa_gate
│  └─ split.py              auto-split + merge part (bỏ khỏi GET)
├─ workers/                 chạy trong SUBPROCESS  [E]
│  └─ render_worker.py      assemble_fit + render.run_render cho 1 job → sửa render ăn ngay job sau
├─ services/                việc nền mà sidecar đang tự làm  [C]
│  ├─ feeder.py · film_db_sync.py · hongguo_flow.py · publisher.py
├─ sidecar/                 CHỈ Telegram + gọi API (không ghi DB/queue trực tiếp)  [C]
│  └─ telegram.py
├─ core/                    từ V5: config, manifest, fileio, film_db, job_queue (lõi), batch_config
│  ├─ instance.py           đọc instances/<tên>.json  →  cổng, work_root, temp, router bật
│  └─ job_opts.py           MỘT nguồn tham số job (pinned → batch → default)  [A]
├─ engines/ engines/asr/    từ V5 (thuật toán giữ nguyên)
├─ agents/                  từ V5 (brain, core, heal…) — gọi state/* thay vì save()
├─ instances/               v6.json (:8806) · prod.json (:8801 khi thay V2) · desktop.json
├─ desktop/
│  ├─ src-tauri/            Tauri 2: spawn backend, health check, tray, notification
│  └─ ui/                   index.html + js/ (store + EventSource) — thay index3/films_ui dần
├─ tests/  scripts/  docs/  (brain, CLAUDE.md, claude_setup như V2–V5)
```

---

## 3. Hợp đồng giữa các lớp

### 3.1 State (B9) — chỉ module này ghi `manifest.json`

```python
from state import job_state as js
js.mark_running(wd, "translate", cur_text="Pass1…")
js.set_progress(wd, "translate", review_done=120, review_total=2698)   # throttle → event
js.set_hold(wd, reason="qa_brain", detail="3 high QA", block_tts=True)
js.clear_hold(wd, reasons={"waiting_review"})
js.set_review_retry(wd, n_unrev=40, retry_sec=300, max_tries=3)       # B2 → park hoặc HOLD
js.set_queue_opts(wd, opts)
```

- Mỗi hàm = **một** `Manifest.update(fn)` (đọc lại đĩa → sửa đúng khoá của mình → ghi atomic trong khoá liên tiến trình). Không còn `m.data[...] = …; m.save()`.
- Sau ghi: `index.apply(job_id, data)` → `bus.publish("job.updated", …)`.
- Test: `tests/test_state_*.py` — 2 luồng ghi khoá khác nhau cùng lúc không mất cập nhật nào.

### 3.2 Pipeline (D)

```python
class Stage:      name; def done(ctx) -> bool; def run(ctx) -> None
class Gate:       name; def check(ctx) -> GateResult   # PASS | PARK(retry_at) | HOLD(reason) | FAIL(msg)
STUDIO_PIPELINE = [Download(), Asr(), Translate(), Review(), ReadyForTts(), ReviewComplete(),
                   Quality(), Hold(), QaGate(), Tts(), Separate(), Ocr(), Render()]
```

- `Runner.run(job)` đi lần lượt; Stage `done()` → bỏ qua (resume); Gate ≠ PASS → trả kết quả cho queue (park/hold/fail). Kiểm `.stop` giữa mọi bước.
- Không còn dò chuỗi tiếng Việt trong `cur_text` để quyết park (`server.py:4496` cũ).

### 3.3 Runtime

- `Runtime` là object duy nhất giữ `stop_jobs`, `stage_busy`, pool `dl/gpu`, `procs`. Route nhận qua dependency, **không `import server`**.
- `queue_worker` dùng `pipeline.Runner`; park/HOLD/retry theo `GateResult`.

### 3.4 Workers (E)

- `render_worker.py <work_dir> --opts opts.json` chạy trong subprocess (như ASR). Mỗi job nạp code render mới nhất → bỏ `importlib.reload` (43 chỗ ở routes_hg cũng bỏ: Hongguo gọi qua `services/hongguo_flow`).

### 3.5 Instance (thay V2/V4/V5)

```json
// instances/v6.json
{"name":"v6","port":8806,"work_root":"D:/PinelineV6_work","temp":"D:/PinelineV6_tmp",
 "routers":["films","uploader"],"desktop":true,"agent_brain":true,"telegram":false}
```

- `python -m app --instance v6` · Tauri đọc cùng file để biết cổng.
- Chuyển production: `instances/prod.json` (:8801, work cũ) — **không copy code giữa cây nữa**.

### 3.6 Sidecar (C)

- Telegram chỉ gọi API. Feeder, film_db, Hongguo A–Z, auto_publisher chạy trong `services/` của server (một tiến trình sở hữu queue + DB), lịch qua runtime scheduler. Bỏ `POST :8802/cmd` từ agent_core.

---

## 4. Ánh xạ V5 → V6 (tóm tắt)

| V5 | V6 |
|---|---|
| `server.py` route/handler | `app/routes/*.py` |
| `server._auto_gpu`, `_queue_run_one`, `_check_and_run_auto_split_parts` | `pipeline/stages|gates`, `pipeline/split.py`, `runtime/queue_worker.py` |
| `server._STOP_JOBS/_STAGE_BUSY/_gpu/_dl/_PROCS`, `_run_engine` | `runtime/context.py`, `runtime/engines.py` |
| 48 `Manifest.save()` trong server + 4 routes_agent | `state/job_state.py` |
| `batch_config.resolve_job_opts` + `_req_audio` | `core/job_opts.py` |
| `server._do_render` + reload | `workers/render_worker.py` |
| `agents/agent_tele_cmd.py` (feeder, film_db loop, publisher) | `sidecar/telegram.py` + `services/*` |
| `static/ui/index3.html` polling | `desktop/ui` + `/api/events` |
| `src-tauri` (V5, devUrl :8805) | `desktop/src-tauri` (spawn backend, instance-aware) |

---

## 5. Pha & tiêu chí xong

| Pha | Việc | Xong khi |
|---|---|---|
| **F0** | Tạo cây; khung `state/ events/ pipeline/ runtime/ workers/ instances/`; copy V5; `core/instance.py`; Tauri shell spawn backend | pytest khung + V5 cũ pass; `tree_audit` 0; :8806 start; 1 job ngắn ra `output.mp4` |
| **F1 B9** | Mọi ghi manifest → `state.job_state` (theo nhóm: stage, HOLD, queue_opts, progress) | grep `\.save()` ngoài `state/` = 0; test đồng thời; job thật có HOLD/park/stop |
| **F2 A** | `core/job_opts.py` | 1 job bấm từ UI/queue/agent → cùng opts (test) |
| **F3 D** | Runtime + Stage/Gate; `server.py` → `app/` | route không `import server`; `_auto_gpu` xoá; job thật khớp output V2 |
| **F3b UI** | JobIndex + `/api/events` + UI desktop subscribe | 0 polling định kỳ; CPU server idle < 2 % với 100 job |
| **F4 C** | services + sidecar thuần | không còn `/cmd` vòng; feeder/film_db chạy trong server |
| **F5 E** | render subprocess | 0 `importlib.reload`; sửa render có hiệu lực job kế |
| **F6** | B11 + docs + brain + chuyển production | chạy song song V2 ≥ 3 job thật khớp → `instances/prod.json` |

Mỗi pha: `tree_audit` 0 · pytest · `/review` · 1 job thật so output với V2 (thời lượng, lệch sub/tiếng, số câu HOLD).

## 6. Rủi ro

- **GPU dùng chung với V2 production** → V6 test với `agent_brain=false` cho tới F6, không đổi preset llama :8083; job test chạy khi V2 rảnh.
- **Đổi luồng ghi manifest** (F1) dễ lộ race cũ → test đồng thời + so log job thật.
- **UI mới** (F3b): làm song song, giữ `index3.html` chạy được tới khi UI mới đủ chức năng.
