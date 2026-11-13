"""services/film_db_sync.py — đồng bộ DB phim định kỳ (sidecar cũ: filmdb_loop) — V6 F4."""
from __future__ import annotations

import os
import threading

FILMDB_SEC = max(30, int(os.getenv("FILMDB_SEC") or "120"))


def sync_once(base: str | None = None) -> dict:
    import core.film_db as film_db
    lib = None
    if film_db.needs_series_ids():
        try:
            import agents.agent_feeder as feeder
            from core.instance import server_base
            lib = feeder.library((base or server_base()).rstrip("/"))
        except Exception:  # noqa: BLE001
            lib = None
    return film_db.sync(lib)


def loop(stop: threading.Event, *, base: str | None = None, sec: float | None = None) -> None:
    import core.film_db as film_db
    every = sec or FILMDB_SEC
    print(f"[film-db] {film_db.DB_PATH} · đồng bộ mỗi {every}s", flush=True)
    while not stop.is_set():
        try:
            r = sync_once(base)
            if r["new"] or r["changed"] or r["uploads"]:
                print(f"[film-db] +{len(r['new'])} mới · {len(r['changed'])} đổi trạng thái · "
                      f"{r['uploads']} upload · tổng {r['total']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[film-db] lỗi: {e}", flush=True)
        stop.wait(every)
