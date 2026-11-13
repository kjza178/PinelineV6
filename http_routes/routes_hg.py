"""Cầu nối Hongguo Downloader (local :8000): /api/hg/*.

Tách từ server.py (đợt 7). Chỉ được nạp từ cuối server.py (sau khi mọi hàm/model lõi đã
định nghĩa): truy cập server qua `S.<tên>` để patch `server.<tên>` trong test vẫn có tác dụng."""
from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
import json
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.responses import Response
from pydantic import BaseModel

from fastapi import APIRouter, Depends, Request

import server as S


async def _bind_hg_ui_backend(request: Request) -> None:
    """Backend theo namespace (middleware gắn request.state.hg_ui) — fallback header.

    Phải async: Depends sync chạy threadpool → ContextVar không tới endpoint.
    /api/hg  = classic HTTP only · /api/hg2 = RPC (WinUI) — tách hoàn toàn.
    """
    import agents.hg_ui_ctx as ctx
    forced = getattr(request.state, "hg_ui", None)
    if forced in ("1", "classic", "http"):
        ctx.force("http")
        return
    if forced in ("2", "hg2", "rpc"):
        ctx.force("rpc")
        return
    ctx.apply(
        request.headers.get("x-hg-ui") or request.query_params.get("hg_ui"),
        request.headers.get("referer"),
    )


router = APIRouter(dependencies=[Depends(_bind_hg_ui_backend)])

__all__ = [
    "HgRankEpsIn",
    "HgTranslateTitlesIn",
    "_hg_base",
    "_hg_err",
    "hg_baike_info",
    "hg_baike_search",
    "hg_cancel",
    "hg_img",
    "hg_library",
    "hg_library_open",
    "hg_library_preview",
    "hg_merge",
    "hg_merge_queue",
    "hg_merge_queue_clear",
    "hg_ping",
    "hg_poster",
    "hg_preview",
    "hg_rank",
    "hg_rank_episodes",
    "hg_resolve",
    "hg_search",
    "hg_series",
    "hg_status",
    "hg_submit",
    "hg_translate_titles",
    "hg_web_search",
    "router",
]


def _hg_base(override: str | None = None) -> str:
    """Luôn DEFAULT_BASE: `base` từ client từng cho server gửi token Hongguo tới URL bất kỳ."""
    import agents.hongguo_bridge as hg
    return hg.DEFAULT_BASE


def _hg_err(e: Exception):
    msg = str(e)
    try:
        d = json.loads(msg)
        if isinstance(d, dict):
            return JSONResponse(d, status_code=int(d.get("http_status") or 502))
    except Exception:  # noqa: BLE001
        pass
    return JSONResponse({"error": msg}, status_code=502)


@router.get("/api/disk")
def api_disk(base: str | None = None):
    """Dung lượng ổ thư viện Hongguo (sidebar hongguo2)."""
    import shutil
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    folder = ""
    try:
        if hg._use_rpc():
            st = hg._rpc_mod().settings_get() or {}
            folder = str(st.get("saveFolder") or "")
    except Exception:  # noqa: BLE001
        folder = ""
    if not folder:
        folder = str(_machine.library_dir())
    root = Path(folder)
    # lấy ổ chứa folder (D:\ …)
    drive = root.anchor or str(root)[:3] or "D:\\"
    try:
        usage = shutil.disk_usage(drive)
        free_gb = usage.free / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        used_pct = round(100.0 * usage.used / max(1, usage.total), 1)
        return {
            "ok": True,
            "path": folder,
            "drive": drive,
            "free_gb": round(free_gb, 1),
            "used_gb": round(used_gb, 1),
            "total_gb": round(total_gb, 1),
            "used_pct": used_pct,
        }
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e), "path": folder}, status_code=500)


@router.get("/api/hg/ping")
def hg_ping(base: str | None = None):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    return hg.ping(_hg_base(base))


@router.get("/api/hg/settings")
def hg_settings(base: str | None = None):
    """Hongguo 2.x settings (saveFolder, quality…) — cùng helper WinUI."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.settings(_hg_base(base))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/settings")
def hg_settings_update(body: dict | None = None, base: str | None = None):
    """Patch settings (quality, saveFolder, merge…) — RPC settings.update."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    body = body or {}
    patch = body.get("patch") if isinstance(body.get("patch"), dict) else body
    if not isinstance(patch, dict) or not patch:
        return JSONResponse({"error": "thiếu patch"}, status_code=400)
    # Không nhận secret qua settings
    patch = {k: v for k, v in patch.items() if k not in ("key", "license", "token", "proof")}
    try:
        return hg.settings_update(patch, _hg_base(base))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/search")
def hg_search(q: str, base: str | None = None, expand: bool = True):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    if not (q or "").strip():
        return JSONResponse({"error": "thiếu q"}, status_code=400)
    try:
        return hg.search(q.strip(), _hg_base(base), expand=bool(expand))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/resolve")
def hg_resolve(body: dict | None = None, base: str | None = None):
    """Dán link Hongguo / series_id → meta (khi search không ra, vd. 第十一季)."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    body = body or {}
    text = (body.get("text") or body.get("url") or body.get("q") or "").strip()
    b = _hg_base(body.get("base") or base)
    if not text:
        return JSONResponse({"error": "thiếu text/url/series_id"}, status_code=400)
    try:
        return hg.resolve_text(text, b)
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/baike-search")
def hg_baike_search(q: str, base: str | None = None):
    """Nhận diện trên duanjubaike → danh sách version → match Hongguo từng cái.
    Loại tên lạc họ / id lệch."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    if not (q or "").strip():
        return JSONResponse({"error": "thiếu q"}, status_code=400)
    try:
        return hg.search_via_baike(q.strip(), _hg_base(base))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/baike-info")
def hg_baike_info(series_id: str | None = None, title: str | None = None,
                  translate: bool = True):
    """Chi tiết sâu từ duanjubaike (intro/tags/score/同系列).

    Tra theo series_id Hongguo/baike; nếu thiếu hoặc miss thì search theo title.
    """
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    sid = (series_id or "").strip() or None
    tit = (title or "").strip() or None
    if not sid and not tit:
        return JSONResponse({"error": "thiếu series_id hoặc title"}, status_code=400)
    try:
        info = hg.baike_deep(series_id=sid, title=tit)
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)
    if translate:
        try:
            import engines.title_mt as _tmt
            _tmt = importlib.reload(_tmt)
            pack = []
            if info.get("title"):
                pack.append({"series_id": info.get("series_id") or "", "title": info["title"]})
            if info.get("intro"):
                pack.append({"series_id": (info.get("series_id") or "") + ":intro",
                             "title": info["intro"][:180]})
            for r in (info.get("related") or [])[:12]:
                if r.get("title"):
                    pack.append({"series_id": r.get("series_id") or "", "title": r["title"]})
            tr = _tmt.translate_titles(pack)
            by = {str(x.get("series_id") or ""): x.get("title_vi") for x in tr.get("items") or []}
            if by.get(str(info.get("series_id") or "")):
                info["title_vi"] = by[str(info.get("series_id") or "")]
            intro_key = str(info.get("series_id") or "") + ":intro"
            if by.get(intro_key):
                info["intro_vi"] = by[intro_key]
            for r in (info.get("related") or []):
                vi = by.get(str(r.get("series_id") or ""))
                if vi:
                    r["title_vi"] = vi
        except Exception as e:  # noqa: BLE001
            info["translate_error"] = str(e)[:200]
    return info


@router.get("/api/hg/web-search")
def hg_web_search(q: str):
    """Scrape hongguoduanju.com/search — dùng khi API Downloader sót mùa."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    if not (q or "").strip():
        return JSONResponse({"error": "thiếu q"}, status_code=400)
    try:
        by_id: dict = {}
        n = hg.web_expand_into(by_id, q.strip(), workers=6)
        items = hg.order_franchise_items(list(by_id.values()))
        return {
            "results": items,
            "n": len(items),
            "added": n,
            "core": hg.franchise_core(q),
            "queries": hg.web_expand_queries(q),
        }
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/series/{series_id}")
def hg_series(series_id: str, base: str | None = None):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.lookup_series(series_id, _hg_base(base))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/series/{series_id}/seasons")
def hg_series_seasons(series_id: str, base: str | None = None):
    """Franchise seasons (S1…Sn) — RPC series.seasons."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.series_seasons(series_id, _hg_base(base))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/series/{series_id}/qualities")
def hg_series_qualities(series_id: str, base: str | None = None):
    """Quality ladder + bitrate — RPC series.qualities."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.series_qualities(series_id, _hg_base(base))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/status")
def hg_status(base: str | None = None):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.status(_hg_base(base))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/cancel")
def hg_cancel(body: S.HgBaseIn | None = None):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.cancel(_hg_base((body.base if body else None)))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/downloads/clear-finished")
def hg_dl_clear_finished(body: S.HgBaseIn | None = None):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.clear_finished(_hg_base((body.base if body else None)))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/downloads/cancel")
def hg_dl_cancel_one(body: dict | None = None):
    """Stop 1 download đang chạy/chờ."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    body = body or {}
    sid = str(body.get("series_id") or body.get("seriesId") or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "thiếu series_id"}, status_code=400)
    try:
        return hg.cancel_download(sid, _hg_base(body.get("base")))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/downloads/remove")
def hg_dl_remove(body: dict | None = None):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    body = body or {}
    sid = str(body.get("series_id") or body.get("seriesId") or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "thiếu series_id"}, status_code=400)
    try:
        return hg.remove_download(sid, _hg_base(body.get("base")))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/downloads/retry")
def hg_dl_retry(body: dict | None = None):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    body = body or {}
    sid = str(body.get("series_id") or body.get("seriesId") or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "thiếu series_id"}, status_code=400)
    try:
        return hg.retry_download(sid, _hg_base(body.get("base")))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/downloads/open-folder")
def hg_dl_open_folder(body: dict | None = None):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    body = body or {}
    try:
        return hg.open_folder(path=body.get("folder") or body.get("path"), name=body.get("name"))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/submit")
def hg_submit(body: S.HgSubmitIn):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.submit(
            body.series_ids,
            _hg_base(body.base),
            scores=body.scores,
            quality=getattr(body, "quality", None),
        )
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/rank")
def hg_rank(
    category: str = "all",
    board: str = "recommend",
    chart: str = "",
    offset: int = 0,
    size: int = 100,
    base: str | None = None,
):
    """Bảng hot / Browse feed / Catalog.

    chart=ranklist_* (WinUI Ranking tabs)
    board=browse|feed → feed.page; catalog* → Explorer; hoặc board filter trong chart
    category = board alias cũ (all/female/male/…) hoặc genre Explorer
    """
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.rank(
            category=category,
            board=board,
            chart=chart,
            offset=offset,
            size=size,
            base=_hg_base(base),
        )
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/genres")
def hg_genres(limit: int = 40):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return {"genres": hg.genres(limit)}
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/catalog")
def hg_catalog(
    genre: str = "",
    sort: str = "newest",
    status: str = "",
    q: str = "",
    page: int = 1,
    size: int = 48,
):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.catalog(genre=genre, sort=sort, status=status, q=q, page=page, size=size)
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/feed")
def hg_feed(
    offset: int = 0,
    size: int = 48,
    panel: int = 1,
    session_id: str = "",
    genre: str = "",
    category_dim_theme: str = "",
    category_dim_role: str = "",
    category_dim_epoch: str = "",
    sort: str = "",
    gender: str = "",
    online_time: str = "",
    base: str | None = None,
):
    """Hongguo 2.x Browse = feed.page + selector panel (WinUI facets)."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    select = {
        "genre": genre,
        "category_dim_theme": category_dim_theme,
        "category_dim_role": category_dim_role,
        "category_dim_epoch": category_dim_epoch,
        "sort": sort,
        "gender": gender,
        "online_time": online_time,
    }
    select = {k: v for k, v in select.items() if (v or "").strip()}
    try:
        return hg.feed_browse(
            select=select or None,
            offset=offset,
            size=size,
            panel=bool(panel),
            session_id=session_id or "",
        )
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


class HgRankEpsIn(BaseModel):
    series_ids: list[str] = []
    base: str | None = None


@router.post("/api/hg/rank-episodes")
def hg_rank_episodes(body: HgRankEpsIn):
    """Lazy fill episode_cnt cho series trên bảng hot."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.rank_episodes(body.series_ids, _hg_base(body.base))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


class HgTranslateTitlesIn(BaseModel):
    items: list[dict] = []   # [{series_id?, title}]


@router.post("/api/hg/translate-titles")
def hg_translate_titles(body: HgTranslateTitlesIn):
    """Dịch thô title Trung→Việt (Google gtx + cache) — chỉ để duyệt Hongguo."""
    import importlib
    import engines.title_mt as _tmt
    _tmt = importlib.reload(_tmt)
    try:
        return _tmt.translate_titles(list(body.items or []))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/library")
def hg_library(base: str | None = None):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.library(_hg_base(base))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/preview")
def hg_preview(
    series_id: str,
    ep: int = 1,
    fresh: int = 0,
    quality: str = "",
    base: str | None = None,
):
    """Xem thử 1 tập — :8000 /dl/preview hoặc RPC playback.prepare → stream local."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    sid = (series_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "thiếu series_id"}, status_code=400)
    try:
        # Preview luôn ưu tiên 480p — 1080p quá chậm
        q = (quality or "").strip() or "480p"
        if q.lower() in ("1080p", "best", "source"):
            q = "480p"
        return hg.preview_series(
            sid,
            ep=ep,
            fresh=bool(fresh),
            base=_hg_base(base),
            quality=q,
        )
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


class HgPreviewStartIn(BaseModel):
    series_id: str = ""
    ep: int = 1
    quality: str = "480p"
    fresh: bool = False


@router.post("/api/hg/preview/start")
def hg_preview_start(body: HgPreviewStartIn):
    """Bắt đầu prepare nền (progress poll qua /api/hg/preview/job)."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    sid = (body.series_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "thiếu series_id"}, status_code=400)
    q = (body.quality or "480p").strip()
    if q.lower() in ("1080p", "best", "source"):
        q = "480p"
    try:
        rpc = hg._rpc_mod()
        return rpc.playback_prepare_start(
            sid, episode=int(body.ep or 1), quality=q, force=bool(body.fresh),
        )
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/preview/job")
def hg_preview_job(ticket: str = ""):
    """Trạng thái job preview async (progress 0..1)."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg._rpc_mod().playback_job(ticket)
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.api_route("/api/hg/playback/stream", methods=["GET", "HEAD"])
def hg_playback_stream(session: int = 0):
    """Stream MP4 đã prepare bởi helper (thư mục hongguo-player)."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        rpc = hg._rpc_mod()
        p = rpc.playback_session_path(int(session or 0))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)
    if not p:
        return JSONResponse({"error": "playback session not ready"}, status_code=404)
    return FileResponse(str(p), media_type="video/mp4", filename=p.name)


@router.api_route("/api/hg/library/preview", methods=["GET", "HEAD"])
def hg_library_preview(name: str, base: str | None = None):
    """Stream video thư viện Hongguo (merged hoặc tập đầu) — tua được (Range)."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    name = (name or "").strip()
    if not name:
        return JSONResponse({"error": "thiếu name"}, status_code=400)
    try:
        p = hg.resolve_preview_path(name, _hg_base(base))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)
    if not p:
        return JSONResponse({"error": "chưa có file xem thử (cần tải / merge)"}, status_code=404)
    suffix = p.suffix.lower()
    media = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".mov": "video/quicktime",
    }.get(suffix, "application/octet-stream")
    return FileResponse(str(p), media_type=media, filename=p.name)


@router.get("/api/hg/img")
def hg_img(url: str | None = None, u: str | None = None, base: str | None = None):
    """Proxy ảnh cover qua Hongguo /img (HEIC → JPEG) — kèm UI token.

    Chấp nhận cả ``?url=`` (hongguo.html) và ``?u=`` (hongguo2).
    """
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    target = (url or u or "").strip()
    if not target:
        return JSONResponse({"error": "thiếu url"}, status_code=400)
    try:
        data, ctype = hg.fetch_img_proxy(target, _hg_base(base))
        return Response(content=data, media_type=ctype, headers={
            "Cache-Control": "public, max-age=86400",
        })
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=502)


@router.get("/api/hg/poster")
def hg_poster(name: str, base: str | None = None):
    """Poster thư viện: /dl/poster (auth) hoặc file local poster.jpg.

    Thiếu poster → 404 (không 502). Tên junk (job_id / downloads) cũng 404,
    tránh spam Bad Gateway khi Films UI dùng hg_name sai.
    """
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    name = (name or "").strip()
    if not name:
        return JSONResponse({"error": "thiếu name"}, status_code=400)
    try:
        from core.film_db import is_junk_hg_name
        if is_junk_hg_name(name):
            return JSONResponse({"error": "không có poster", "name": name}, status_code=404)
    except Exception:
        pass
    try:
        data, ctype = hg.fetch_poster(name, _hg_base(base))
        return Response(content=data, media_type=ctype, headers={
            "Cache-Control": "public, max-age=3600",
        })
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e) or "không có poster"}, status_code=404)
    except Exception as e:  # noqa: BLE001
        msg = str(e) or "poster error"
        # «không có poster» = thiếu file/API — 404; lỗi mạng/auth thật → 502
        if "không có poster" in msg.lower() or "not found" in msg.lower():
            return JSONResponse({"error": msg}, status_code=404)
        return JSONResponse({"error": msg}, status_code=502)


@router.post("/api/hg/merge")
def hg_merge(body: S.HgMergeIn):
    """Enqueue merge tuần tự (Hongguo chỉ chạy 1 merge / lúc)."""
    import agents.hongguo_bridge as hg
    try:
        return hg.merge_queue_add(
            body.names, _hg_base(body.base), skip_done=not body.force,
        )
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/merge-queue")
def hg_merge_queue(base: str | None = None):
    import agents.hongguo_bridge as hg
    return hg.merge_queue_state(_hg_base(base))


@router.post("/api/hg/merge-queue/clear")
def hg_merge_queue_clear():
    import agents.hongguo_bridge as hg
    return hg.merge_queue_clear()


@router.post("/api/hg/library/open")
def hg_library_open(body: dict | None = None):
    import agents.hongguo_bridge as hg
    body = body or {}
    try:
        return hg.open_library(body.get("name"), _hg_base(body.get("base")))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/library/delete")
def hg_library_delete(body: dict | None = None):
    import agents.hongguo_bridge as hg
    body = body or {}
    names = list(body.get("names") or [])
    if not names:
        return JSONResponse({"ok": False, "error": "Chưa chọn series để xoá"}, status_code=400)
    try:
        return hg.delete_library_series(names, _hg_base(body.get("base")))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/library/update-info")
def hg_library_update_info(base: str | None = None):
    """WinUI «Update info» — library.updateInfo."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    try:
        return hg.library_update_info(_hg_base(base))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/library/update")
def hg_library_update(body: dict | None = None):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    body = body or {}
    names = body.get("names")
    try:
        return hg.library_update(_hg_base(body.get("base")), names=names)
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/library/seasons/decline")
def hg_seasons_decline(body: dict | None = None):
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    body = body or {}
    series_ids = list(body.get("series_ids") or [])
    try:
        return hg.seasons_decline(series_ids, _hg_base(body.get("base")))
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/library/seasons/discover")
def hg_seasons_discover(body: dict | None = None):
    """Chỉ tìm mùa mới theo watchlist+films+lib — không tải / không gọi Update all."""
    import importlib
    import agents.hongguo_bridge as hg
    hg = importlib.reload(hg)
    body = body or {}
    try:
        max_cores = int(body.get("max_cores") or 16)
    except (TypeError, ValueError):
        max_cores = 16
    expand = bool(body.get("expand") or False)
    try:
        return hg.discover_new_seasons(
            _hg_base(body.get("base")), max_cores=max_cores, expand=expand
        )
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.get("/api/hg/library/watchlist")
def hg_library_watchlist(include_deleted: bool = True):
    """Danh sách meta watchlist (sau xoá Lib vẫn còn) — không tải."""
    try:
        import core.film_db as fdb
        rows = fdb.list_hg_watchlist(include_deleted=bool(include_deleted))
        return {
            "ok": True,
            "n": len(rows),
            "items": rows,
            "on_disk": sum(1 for r in rows if int(r.get("on_disk") or 0) == 1),
            "deleted": sum(1 for r in rows if int(r.get("on_disk") or 0) == 0),
        }
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/meta/generate")
@router.post("/meta/generate")
def meta_generate(body: dict):
    import agents.meta_studio as meta_studio
    try:
        r = meta_studio.generate_metadata(
            title_vi=str(body.get("title_vi") or ""),
            title_zh=str(body.get("title_zh") or ""),
            info_text=str(body.get("info_text") or ""),
            prompt_guide=str(body.get("prompt_guide") or ""),
            tone=str(body.get("tone") or "giật gân, kịch tính"),
        )
        if not r.get("ok"):
            return JSONResponse(r, status_code=500)
        return r
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/api/meta/generate-title")
@router.post("/meta/generate-title")
def meta_generate_title(body: dict):
    import agents.meta_studio as meta_studio
    try:
        job_id = str(body.get("job_id") or "").strip()
        series_name = str(body.get("series_name") or body.get("title_vi") or body.get("title") or "").strip()
        title_zh = str(body.get("title_zh") or body.get("title") or "").strip()
        season = int(body.get("season") or 1)
        info_text = str(body.get("info_text") or "").strip()
        summary = str(body.get("summary") or "").strip()
        tone = str(body.get("tone") or "").strip()
        count = int(body.get("count") or 5)
        r = meta_studio.generate_youtube_titles(
            series_name=series_name,
            season=season,
            episode_info=info_text,
            summary=summary,
            job_id=job_id,
            tone=tone,
            count=count,
            title_zh=title_zh,
        )
        if not r.get("ok"):
            return JSONResponse(r, status_code=500)
        return r
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/api/meta/thumbnail")
@router.post("/meta/thumbnail")
def meta_thumbnail(body: dict):
    import agents.meta_studio as meta_studio
    try:
        poster_src = body.get("poster_url") or body.get("poster_path")
        if not poster_src and body.get("poster_name"):
            try:
                import agents.hongguo_bridge as hg
                data, _ = hg.fetch_poster(str(body["poster_name"]).strip(), _hg_base(body.get("base")))
                if data:
                    poster_src = data
            except Exception:
                pass
        if not poster_src:
            return JSONResponse({"ok": False, "error": "Thiếu nguồn ảnh poster hoặc không tìm thấy poster"}, status_code=400)
        out = meta_studio.create_thumbnail(
            poster_source=poster_src,
            hook_text_1=str(body.get("hook_text_1") or ""),
            hook_text_2=str(body.get("hook_text_2") or ""),
            badge_text=str(body.get("badge_text") or "TRỌN BỘ THUYẾT MINH"),
            sub_text=str(body.get("sub_text") or ""),
            style=str(body.get("style") or "fire_gold"),
            out_filename=str(body.get("out_filename") or ""),
        )
        return {"ok": True, "path": str(out), "filename": out.name, "url": f"/api/meta/thumbnail?file={out.name}"}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/api/meta/thumbnail")
@router.get("/meta/thumbnail")
def meta_thumbnail_get(file: str):
    p = (Path(__file__).resolve().parents[1] / "data" / "thumbnails" / file).resolve()
    if p.is_file() and p.parent == (Path(__file__).resolve().parents[1] / "data" / "thumbnails").resolve():
        return Response(content=p.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})
    return JSONResponse({"error": "not found"}, status_code=404)


@router.get("/api/meta/series-info")
@router.get("/meta/series-info")
def meta_series_info(job_id: str = ""):
    import agents.meta_studio as meta_studio
    series_key, series_name = meta_studio.get_series_key(job_id)
    shared_meta = meta_studio.get_series_meta(series_key, series_name=series_name)
    shared_thumb = meta_studio.get_series_thumbnail(series_key, series_name=series_name)
    return {
        "ok": True,
        "has_shared": bool(shared_meta and shared_thumb and shared_thumb.is_file()),
        "series_key": series_key,
        "series_name": series_name,
        "shared_meta": shared_meta,
        "thumb_url": f"/meta/thumbnail?file={shared_thumb.name}" if (shared_thumb and shared_thumb.is_file()) else None,
    }


@router.get("/api/hg/license/status")
def hg_license_status():
    """Safe license status for Hongguo 2.x UI — never returns key/proof."""
    import importlib
    import agents.hongguo_license_ensure as lic
    lic = importlib.reload(lic)
    try:
        return lic.status_public()
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/license/key")
def hg_license_key(body: dict | None = None):
    import importlib
    import agents.hongguo_license_ensure as lic
    lic = importlib.reload(lic)
    body = body or {}
    key = (body.get("key") or "").strip()
    try:
        r = lic.store_key(key)
        if not r.get("ok"):
            return JSONResponse(r, status_code=400)
        return r
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.delete("/api/hg/license/key")
def hg_license_key_clear():
    import importlib
    import agents.hongguo_license_ensure as lic
    lic = importlib.reload(lic)
    try:
        return lic.clear_key()
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)


@router.post("/api/hg/license/prove")
def hg_license_prove(body: dict | None = None):
    import importlib
    import agents.hongguo_license_ensure as lic
    lic = importlib.reload(lic)
    body = body or {}
    force = bool(body.get("force") or False)
    try:
        r = lic.ensure_licensed(force=force)
        # strip activate log (may contain sensitive fragments)
        r = {k: v for k, v in r.items() if k != "activate_log_tail"}
        if not r.get("ok"):
            return JSONResponse(r, status_code=502)
        return r
    except Exception as e:  # noqa: BLE001
        return _hg_err(e)



