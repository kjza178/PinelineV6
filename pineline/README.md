# Package `pineline/` — attach registries

Chỉ còn **`pineline.server.routes`** làm bản đồ path LIVE. Unity 2026-11-04 xoá TARGET shells rỗng (`agents`, `pipeline`, `sidecar`, `desktop`, `ops`, `server/services`).

```
pineline/
  server/
    routes/        # queue / jobs / assets / fs — attach() registries
```

| Module | Vai trò |
|---|---|
| `pineline.server.routes.queue` | LIVE attach — `/api/queue/*` |
| `pineline.server.routes.jobs` | LIVE attach — `/api/jobs/*` |
| `pineline.server.routes.assets` | LIVE attach — cookies / voices / frames / models |
| `pineline.server.routes.fs` | LIVE attach — `/api/fs/*` |

Thân handler queue/jobs/assets/fs vẫn trong root **`server.py`**. Router bodies UI/hg/agent/films sống ở **`http_routes/`** (không trong package này). Code live khác: **`agents/`**, **`engines/`**, **`tools/`**.

Cổng V5: **8805**. Chi tiết: `docs/LAYOUT.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`.
