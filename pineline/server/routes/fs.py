"""FS picker: /api/fs/* — LIVE registry (body trong server.py)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["fs"])

_ROUTES: list[tuple[list[str], str, str]] = [
    (["GET"], "/api/fs/list-dirs", "api_fs_list_dirs"),
    (["POST"], "/api/fs/pick-dir", "api_fs_pick_dir"),
    (["POST"], "/api/fs/pick-dirs", "api_fs_pick_dirs"),
]


def attach(server_mod) -> None:
    for methods, path, name in _ROUTES:
        fn = getattr(server_mod, name, None)
        if fn is None:
            raise AttributeError(f"server thiếu handler fs '{name}' cho {methods} {path}")
        router.add_api_route(path, fn, methods=methods)


__all__ = ["router", "attach", "_ROUTES"]
