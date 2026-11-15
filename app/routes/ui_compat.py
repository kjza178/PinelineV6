"""Route mỏng cho các nút trang Phim / Studio mà trước đây chỉ sidecar (:8802) phục vụ — logic ở services/ui_tools.py.

2026-11-15: V6 phục vụ các trang này từ server và tắt sidecar → trước đây bấm là 404.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/meta/baike")
@router.get("/api/meta/baike")
def meta_baike(q: str = "", title: str = ""):
    from services import ui_tools
    try:
        return ui_tools.baike(q or title)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        print(f"[ui_compat] baike lỗi: {e}", flush=True)
        raise HTTPException(502, f"Tra Baike lỗi: {e}") from e


@router.post("/meta/ai-image")
@router.post("/api/meta/ai-image")
def meta_ai_image(body: dict):
    from services import ui_tools
    try:
        return ui_tools.ai_image(body if isinstance(body, dict) else {})
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        print(f"[ui_compat] ai-image lỗi: {e}", flush=True)
        raise HTTPException(502, str(e)) from e


@router.get("/config")
def batch_config_get():
    from services import ui_tools
    return ui_tools.batch_config_get()


@router.post("/config")
async def batch_config_set(request: Request):
    from services import ui_tools
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    try:
        out = ui_tools.batch_config_set(body if isinstance(body, dict) else {})
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if out.get("changed"):
        from events.bus import bus
        bus.publish("config.changed", {"changed": sorted(out["changed"])})
    return out


@router.post("/test-channel/batch-stop")
def test_channel_batch_stop():
    from services import ui_tools
    return ui_tools.test_channel_batch_stop()
