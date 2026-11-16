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


@router.post("/api/v6/open-external")
def open_external(body: dict):
    """App desktop (WebView2) không mở được cửa sổ mới → nhờ server mở trình duyệt ngoài (chỉ Google / YouTube / localhost)."""
    from services import ui_tools
    try:
        return ui_tools.open_external((body or {}).get("url") or "")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── Tài khoản kênh test YouTube trên máy mới (services/test_channel_setup) ──
@router.get("/api/v6/test-channel/candidates")
def tc_candidates():
    from services import test_channel_setup as tcs
    return {"items": tcs.candidates()}


@router.post("/api/v6/test-channel/projects")
def tc_add_project(body: dict):
    """{name, client_json} (nội dung file OAuth client) hoặc {path} (file client_secret*.json trong yt_uploader)."""
    from services import test_channel_setup as tcs
    b = body or {}
    try:
        rec = tcs.add_from_candidate(str(b["path"]), str(b.get("name") or "")) if b.get("path") else \
            tcs.add_project(str(b.get("name") or ""), b.get("client_json") or "")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "project": rec}


@router.post("/api/v6/test-channel/projects/remove")
def tc_remove_project(body: dict):
    from services import test_channel_setup as tcs
    return {"ok": tcs.remove_project(str((body or {}).get("id") or ""))}
