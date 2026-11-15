"""Route mỏng «Phim trên ổ» — logic ở services/disk_library.py (quét LIBRARY_DIR, tạo job, gộp tập)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


def _names(body: dict) -> list[str]:
    names = (body or {}).get("names")
    if not isinstance(names, list) or not names:
        raise HTTPException(400, "thiếu names")
    return [str(n) for n in names]


@router.get("/api/v6/disk-library")
def disk_library_scan():
    from services import disk_library
    return disk_library.scan()


@router.post("/api/v6/disk-library/add")
def disk_library_add(body: dict, request: Request):
    from services import disk_library
    base = str(request.base_url).rstrip("/")
    return disk_library.add_jobs(_names(body), base=base)


@router.post("/api/v6/disk-library/merge")
def disk_library_merge(body: dict):
    from services import disk_library
    return disk_library.merge(_names(body))
