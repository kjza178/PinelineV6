# `http_routes/` — router bodies

Đây là nơi sống các FastAPI router **UI / Hongguo / agent / films / uploader** (thân route thật, không phải attach registry).

| Module | Prefix / phạm vi |
|---|---|
| `routes_ui.py` | `/`, `/ui3`, `/hongguo`, … |
| `routes_hg.py` | `/api/hg/*`, `/meta/*` |
| `routes_agent.py` | `/api/agent/*` |
| `routes_films.py` | `/films/*` |
| `routes_uploader_settings.py` | uploader / Canva |

`server.py` nạp bằng `include_router`. Path map queue/jobs/assets/fs nằm ở `pineline.server.routes` (attach), không ở đây.

Import: `import http_routes.routes_ui as routes_ui` (không còn shim root).
