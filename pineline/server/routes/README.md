# `pineline.server.routes` — attach registries

| Module | Prefix | Trạng thái |
|---|---|---|
| `queue.py` | `/api/queue/*` (+ alias `/api/jobs/group`) | **LIVE registry** — `attach()`; body trong `server.py` |
| `jobs.py` | `/api/jobs/*` | **LIVE registry** — `attach()`; GET+HEAD tách `operation_id` |
| `assets.py` | cookies / voices / frames / brand / clone / models | **LIVE registry** |
| `fs.py` | `/api/fs/*` | **LIVE registry** |

Router bodies (UI / Hongguo / agent / films / uploader) **không** nằm đây — xem **`http_routes/`** (`routes_ui`, `routes_hg`, `routes_agent`, `routes_films`, `routes_uploader_settings`); `server.py` `include_router`.

## Quy ước SSOT (2026-11-04)

1. **Một thân handler** (queue/jobs/assets/fs) — nằm trong `server.py` (xen helper). Không giữ bản copy trong package.
2. **Một bản đồ path** — `pineline.server.routes.{queue,jobs,assets,fs}._ROUTES` + `attach(server_mod)` rồi `app.include_router`.
3. **Cấm** `@app.get/post` trùng path đã `attach`.
4. `http_routes` dùng `import server as S` / lazy proxy; registry lấy hàm bằng `getattr(server_mod, name)`.

Không merge bodies từ `http_routes` vào package này trong đợt unity. `server.py` không còn `@app` API path — chỉ `on_event` + include_router.
