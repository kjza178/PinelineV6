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
def disk_library_scan(fresh: int = 0):
    from services import disk_library
    return disk_library.scan(fresh=bool(fresh))


@router.get("/api/v6/disk-library/poster")
def disk_library_poster(name: str):
    from fastapi.responses import FileResponse

    from services import disk_library
    p = disk_library.poster(name)
    if not p:
        raise HTTPException(404, "không có poster")
    return FileResponse(p, headers={"Cache-Control": "public, max-age=3600"})


@router.post("/api/v6/disk-library/translate")
def disk_library_translate(body: dict):
    from services import disk_library
    return disk_library.translate(_names(body))


@router.post("/api/v6/disk-library/add")
def disk_library_add(body: dict, request: Request):
    from services import disk_library
    base = str(request.base_url).rstrip("/")
    return disk_library.add_jobs(_names(body), base=base)


@router.post("/api/v6/disk-library/merge")
def disk_library_merge(body: dict):
    from services import disk_library
    return disk_library.merge(_names(body))


@router.post("/api/v6/disk-library/delete")
def disk_library_delete(body: dict):
    """Xoá HẲN thư mục phim khỏi ổ (giao diện hỏi xác nhận trước)."""
    from services import disk_library
    return disk_library.delete(_names(body))


@router.post("/api/v6/disk-library/cleanup")
def disk_library_cleanup(body: dict):
    """Dọn tập lẻ của bộ đã gộp → thùng rác thư viện."""
    from services import disk_library
    return disk_library.cleanup(_names(body))
