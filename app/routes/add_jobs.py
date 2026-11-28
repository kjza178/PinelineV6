"""Route mỏng «Thêm job» (xem trước link → chọn tập → tạo) + cookie Bilibili — logic ở services/add_jobs, engines/bili."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/api/v6/source/inspect")
def source_inspect(body: dict):
    """POST (không phải GET): gọi mạng ra Bilibili / yt-dlp — không phải đọc thuần."""
    from services import add_jobs
    try:
        return add_jobs.inspect(str((body or {}).get("url") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        print(f"[add_jobs] xem link lỗi: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(502, f"Không đọc được link: {e}") from e


@router.post("/api/v6/jobs/from-link")
def jobs_from_link(body: dict):
    from services import add_jobs
    b = body or {}
    try:
        return add_jobs.create(str(b.get("url") or ""), b.get("parts") or [], str(b.get("group_title") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/api/v6/bili/cookie")
def bili_cookie_get(check: int = 1):
    from engines.bili import cookies
    return cookies.status(check=bool(check))


@router.post("/api/v6/bili/cookie")
def bili_cookie_set(body: dict):
    """{cookie: "<JSON Cookie-Editor | SESSDATA=…; …>"} hoặc {clear: true}. Không trả giá trị cookie."""
    from engines.bili import cookies
    b = body or {}
    if b.get("clear"):
        return {"ok": True, "cleared": cookies.clear(), **cookies.status(check=False)}
    try:
        cookies.save(str(b.get("cookie") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, **cookies.status(check=True)}


@router.post("/api/v6/fs/pick-files")
def pick_files(body: dict | None = None):
    """Mở hộp chọn file của Windows trên máy chạy server (= máy chạy app). Trả {paths}. Huỷ → paths rỗng."""
    from services import add_jobs
    try:
        return {"paths": add_jobs.pick_files(str((body or {}).get("initial") or ""))}
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e
