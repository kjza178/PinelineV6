"""FastAPI Router cho Thư viện Phim & Database (đồng bộ trực tiếp trong Pineline V4).

Thay vì phải chạy sidecar :8802 riêng mới dùng được films_ui.html,
router này gắn trực tiếp vào server.py (port 8804).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parent.parent  # project root (file lives in http_routes/)
sys.path.insert(0, str(ROOT))

import core.film_db as film_db
import agents.agent_feeder as agent_feeder

router = APIRouter()


def _host_fallback() -> str:
    """V6 F4: thiếu header Host → host:cổng của instance (không cứng 8804)."""
    from core.instance import server_port
    return f"127.0.0.1:{server_port()}"


def _get_base_url(req: Request) -> str:
    return str(req.base_url).rstrip("/")


@router.get("/films/ui", response_class=HTMLResponse)
@router.get("/library/ui", response_class=HTMLResponse)
def films_ui():
    for p in (ROOT / "static" / "ui" / "films_ui.html", ROOT / "films_ui.html"):
        if p.exists():
            from core.instance import localize_html  # review2: :8801/:8802 cứng → instance
            return HTMLResponse(localize_html(p.read_text(encoding="utf-8"), server=""))
    return HTMLResponse("<h2>Không tìm thấy films_ui.html</h2>", status_code=404)


@router.get("/films")
def list_films(
    q: str = Query("", description="Tìm theo tên/mã"),
    status: str = Query("", description="Lọc theo trạng thái"),
    limit: int = Query(5000, description="Giới hạn số lượng"),
    order: str = Query("recent", description="Thứ tự sắp xếp"),
    work_exists: Optional[str] = Query(None),
    block_status: str = Query(""),
):
    try:
        film_db.sync_uploads()
    except Exception:
        pass

    rows = film_db.query(
        q=q,
        status=status,
        limit=limit,
        order=order,
        work_exists=work_exists,
        block_status=block_status,
    )
    return {
        "total": len(rows),
        "stats": film_db.stats(),
        "films": rows,
    }


@router.get("/films.csv")
def export_films_csv(
    q: str = Query(""),
    status: str = Query(""),
    limit: int = Query(5000),
    order: str = Query("recent"),
    work_exists: Optional[str] = Query(None),
    block_status: str = Query(""),
):
    rows = film_db.query(
        q=q,
        status=status,
        limit=limit,
        order=order,
        work_exists=work_exists,
        block_status=block_status,
    )
    csv_str = "\ufeff" + film_db.to_csv(rows)
    return Response(
        content=csv_str,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=films.csv"},
    )


@router.get("/films/{job_id}")
def get_film(job_id: str):
    d = film_db.get(job_id)
    if not d:
        return JSONResponse({"error": "not found"}, status_code=404)
    return d


@router.post("/films/sync")
def sync_films():
    try:
        r = film_db.sync()
        return {"ok": True, "result": r, "stats": film_db.stats()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/films/delete")
def delete_films(payload: Dict[str, Any] = Body(...), req: Request = None):
    job_ids = payload.get("job_ids")
    if not job_ids and payload.get("job_id"):
        job_ids = [payload.get("job_id")]
    delete_work = bool(payload.get("delete_work", False))
    if job_ids and req:
        base = _get_base_url(req)
        try:
            agent_feeder._post(base, "/api/queue/jobs/remove", {"job_ids": job_ids}, timeout=10)
        except Exception:
            pass
    r = film_db.delete_films(job_ids or [], delete_work=delete_work)
    return r


@router.post("/films/purge-missing")
def purge_missing(payload: Dict[str, Any] = Body(...)):
    only_pf = bool(payload.get("only_pending_failed", False))
    r = film_db.purge_missing(only_pending_failed=only_pf)
    return r


@router.post("/films/check-block")
def check_block_films(payload: Dict[str, Any] = Body(...)):
    try:
        import engines.check_block as check_block
        job_ids = payload.get("job_ids")
        if not job_ids and payload.get("job_id"):
            job_ids = [payload.get("job_id")]
        res = check_block.check_jobs(job_ids or [])
        return {"ok": True, "results": res}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/films/check-block-all")
def check_block_all():
    try:
        import engines.check_block as check_block
        res = check_block.check_all()
        return {"ok": True, "results": res}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ==========================================
# THƯ VIỆN HONGGUO / LIBRARY VIEWER
# ==========================================

@router.get("/library")
def get_library(req: Request):
    base = _get_base_url(req)
    try:
        return agent_feeder.library_view(base)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/library/accounts")
def library_accounts():
    try:
        import tools.lib_check as lib_check
        return {"ok": True, "accounts": lib_check.list_yt_accounts()}
    except Exception as e:
        return {"ok": False, "error": str(e), "accounts": []}


@router.post("/library/action")
def library_action(payload: Dict[str, Any] = Body(...), req: Request = None):
    """Ủy thác feeder (skip/priority/add/merge/…) — không gọi film_db.mark_library_* (API không tồn tại)."""
    action = str(payload.get("action") or "").strip()
    name = str(payload.get("name") or "").strip()
    names = list(payload.get("names") or ([] if not name else [name]))
    cores = list(payload.get("cores") or ([] if not name else [name]))
    if req:
        base = _get_base_url(req)
    else:
        from core.instance import server_base as _server_base  # V6 F4: không cứng 8805
        base = _server_base()

    if action == "enqueue":
        # alias UI cũ → add job từ thư viện
        action = "add"
    if not action:
        return JSONResponse({"ok": False, "error": "Missing action"}, status_code=400)
    try:
        return agent_feeder.library_action(base, action, names=names, cores=cores)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/library/set-block-status")
def library_set_block_status(payload: Dict[str, Any] = Body(...)):
    name = str(payload.get("name") or "").strip()
    status = str(payload.get("status") or "unknown").strip()
    reason = str(payload.get("reason") or "").strip()
    rec = film_db.upsert_library_block(name=name, status=status, reason=reason)
    return {"ok": True, "record": rec}


@router.post("/library/clear-samples")
def library_clear_samples():
    try:
        import tools.lib_check as lib_check
        return lib_check.clear_all_samples()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/library/cut-sample")
def library_cut_sample(payload: Dict[str, Any] = Body(...)):
    try:
        import tools.lib_check as lib_check
        name = str(payload.get("name") or "").strip()
        dur = int(payload.get("duration") or 60)
        return lib_check.cut_sample(name, dur)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/library/enqueue-test")
def library_enqueue_test(payload: Dict[str, Any] = Body(...)):
    try:
        import tools.lib_check as lib_check
        name = str(payload.get("name") or "").strip()
        privacy = str(payload.get("privacy") or "unlisted")
        account = str(payload.get("account") or "default")
        return lib_check.enqueue_test_upload(name, privacy=privacy, account=account)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/library/test-url")
def library_test_url(payload: Dict[str, Any] = Body(...)):
    try:
        import tools.lib_check as lib_check
        url = str(payload.get("url") or "").strip()
        return lib_check.test_url_copyright(url)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/library/scan-queue")
def library_scan_queue():
    try:
        import tools.lib_check as lib_check
        return lib_check.scan_queue_status()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ─── Kênh Phụ Test (Burner Channel) API ──────────────────────────────────────
import tools.test_channel as test_channel


@router.get("/test-channel/status")
def tc_status():
    return test_channel.get_status()


@router.get("/test-channel/reset-quota")
def tc_reset_quota(project_id: Optional[str] = Query(None)):
    return test_channel.reset_exhausted_projects(project_id)


@router.get("/test-channel/recheck-health")
def tc_recheck_health():
    test_channel.verify_all_projects_health(force=True)
    return test_channel.get_status()


@router.get("/test-channel/auth-url")
def tc_auth_url(req: Request, redirect_uri: Optional[str] = Query(None), project_id: Optional[str] = Query(None)):
    host = req.headers.get("host") or _host_fallback()
    r_uri = redirect_uri or f"http://{host}/test-channel/callback"
    try:
        auth_res = test_channel.get_auth_url(r_uri, project_id=project_id)
        return {**auth_res, "redirect_uri": r_uri}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/test-channel/callback")
def tc_callback(req: Request, code: str = Query(""), state: str = Query("")):
    host = req.headers.get("host") or _host_fallback()
    redirect_uri = f"http://{host}/test-channel/callback"
    if not code:
        return HTMLResponse("<h3>⚠️ Không tìm thấy authorization code từ Google.</h3>", status_code=400)
    try:
        res = test_channel.finish_oauth(code, redirect_uri, project_id=state)
        ch_name = res.get("channel_title") or "Kênh Test"
        html = f"""
        <html><head><meta charset="utf-8"></head><body style="background:#0f172a;color:#f8fafc;font-family:sans-serif;padding:40px;text-align:center">
        <h2 style="color:#22c55e">🎉 Kết nối Kênh Phụ thành công!</h2>
        <p>Đã cấp quyền cho kênh: <b>{ch_name}</b></p>
        <p style="color:#94a3b8">Cửa sổ này sẽ tự đóng sau 3 giây...</p>
        <script>setTimeout(function(){{ window.close(); }}, 3000);</script>
        </body></html>
        """
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<h3>Lỗi xác thực OAuth: {e}</h3>", status_code=500)


@router.post("/test-channel/finish-auth")
def tc_finish_auth(req: Request, payload: Dict[str, Any] = Body(...)):
    code = str(payload.get("code") or "").strip()
    host = req.headers.get("host") or _host_fallback()
    redirect_uri = str(payload.get("redirect_uri") or f"http://{host}/test-channel/callback")
    try:
        res = test_channel.finish_oauth(code, redirect_uri)
        return res
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/test-channel/disconnect")
def tc_disconnect():
    ok = test_channel.disconnect()
    return {"ok": ok}


@router.post("/test-channel/test-one")
def tc_test_one(payload: Dict[str, Any] = Body(...)):
    name = str(payload.get("name") or "").strip()
    title_vi = str(payload.get("title_vi") or "").strip()
    auto_delete = bool(payload.get("auto_delete", False))
    project_id = str(payload.get("project_id") or "").strip() or None
    try:
        res = test_channel.run_full_test_for_season(
            name, auto_delete=auto_delete, title_vi=title_vi, project_id=project_id
        )
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/test-channel/batch-start")
def tc_batch_start(payload: Dict[str, Any] = Body(...)):
    names = payload.get("names")
    title_vi_map = payload.get("title_vi_map")
    limit = int(payload.get("limit") or 500)
    auto_delete = bool(payload.get("auto_delete", False))
    res = test_channel.start_batch_test(names=names, limit=limit, auto_delete=auto_delete, title_vi_map=title_vi_map)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@router.get("/test-channel/batch-progress")
def tc_batch_progress():
    return test_channel.get_batch_progress()


@router.post("/test-channel/recheck-one")
def tc_recheck_one(payload: Dict[str, Any] = Body(...)):
    name = str(payload.get("name") or "").strip()
    res = test_channel.recheck_single(name)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@router.post("/test-channel/recheck-pending")
def tc_recheck_pending(payload: Dict[str, Any] = Body(...)):
    force = bool(payload.get("force", False))
    res = test_channel.check_and_process_pending_rechecks(force=force)
    return {"ok": True, "count": len(res), "results": res}

