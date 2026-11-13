"""Queue API: /api/queue/* (+ alias /api/jobs/group) — LIVE registry.

Thân handler giữ trong root `server.py` (cùng pattern jobs.attach).
Module này chỉ đăng ký path → hàm trên server module.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["queue"])

# (methods, path, attr trên server module)
_ROUTES: list[tuple[list[str], str, str]] = [
    (["POST"], "/api/queue/group", "jobs_group"),
    (["POST"], "/api/jobs/group", "jobs_group"),  # alias path cũ
    (["POST"], "/api/queue/jobs", "api_queue_jobs_add"),
    (["POST"], "/api/queue/jobs/group", "api_queue_jobs_add_group"),
    (["POST"], "/api/queue/jobs/opts", "api_queue_jobs_opts"),
    (["POST"], "/api/queue/jobs/remove", "api_queue_jobs_remove"),
    (["POST"], "/api/queue/jobs/sort", "api_queue_jobs_sort"),
    (["POST"], "/api/queue/jobs/move", "api_queue_jobs_move"),
    (["POST"], "/api/queue/jobs/reorder", "api_queue_jobs_reorder"),
    (["GET"], "/api/queue/jobs", "api_queue_jobs_state"),
    (["POST"], "/api/queue/jobs/stop", "api_queue_jobs_stop"),
    (["POST"], "/api/queue/jobs/clear", "api_queue_jobs_clear"),
]


def attach(server_mod) -> None:
    """Gắn handler từ server module lên router; gọi trước include_router."""
    for methods, path, name in _ROUTES:
        fn = getattr(server_mod, name, None)
        if fn is None:
            raise AttributeError(f"server thiếu handler queue '{name}' cho {methods} {path}")
        router.add_api_route(path, fn, methods=methods)


__all__ = ["router", "attach", "_ROUTES"]
