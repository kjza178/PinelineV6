"""Trang UI: /, /ui3, /ui4, /hongguo, /hongguo2, /films/ui, /studio, /v5-map.

/hongguo2 = skin WinUI 2.x (Browse/Ranking/New/Library).
/films|/film|/films/ui = films_ui.html · /studio = index3 (Lồng tiếng Studio).

Tách từ server.py (đợt 7). Chỉ được nạp từ cuối server.py (sau khi mọi hàm/model lõi đã
định nghĩa): truy cập server qua `S.<tên>` để patch `server.<tên>` trong test vẫn có tác dụng."""
from __future__ import annotations

from pathlib import Path
from fastapi.responses import HTMLResponse

from fastapi import APIRouter, Request

router = APIRouter()

__all__ = [
    "_html_file",
    "find_index",
    "index",
    "ui3",
    "ui4",
    "ui_hongguo",
    "ui_hongguo2",
    "ui_films",
    "ui_studio",
    "ui_v5_map",
    "router",
]


def find_index() -> Path | None:
    """Tìm index.html ở web/ hoặc ngay cạnh server.py (phòng khi tải file phẳng)."""
    here = Path(__file__).resolve().parents[1]
    for p in (here / "web" / "index.html", here / "index.html",
              Path("web/index.html"), Path("index.html")):
        if p.exists():
            return p
    return None


@router.get("/", response_class=HTMLResponse)
def index():
    p = find_index()
    if not p:
        return HTMLResponse(
            "<h2>Không tìm thấy index.html</h2>"
            "<p>Đặt file vào <code>web/index.html</code> cạnh server.py.</p>",
            status_code=500,
        )
    return _localize(p.read_text(encoding="utf-8"))


def _localize(text: str) -> str:
    """review2: URL cứng V2 (:8801 server / :8802 sidecar) trong HTML cũ → instance này (core.instance)."""
    try:
        from core.instance import localize_html
        return localize_html(text, server="")
    except Exception:  # noqa: BLE001
        return text


def _html_file(name: str) -> HTMLResponse:
    here = Path(__file__).resolve().parents[1]
    for p in (here / name, here / "static" / "ui" / name, Path(name)):
        if p.exists():
            return HTMLResponse(_localize(p.read_text(encoding="utf-8")))
    return HTMLResponse(f"<h2>Không tìm thấy {name}</h2>", status_code=404)


@router.get("/ui3", response_class=HTMLResponse)
@router.get("/index3.html", response_class=HTMLResponse)
def ui3():
    """UI C — Shell vs Data (khung tĩnh, chống giật innerHTML)."""
    return _html_file("index3.html")


@router.get("/ui4", response_class=HTMLResponse)
@router.get("/index4.html", response_class=HTMLResponse)
def ui4():
    """UI D — Full ops: preview + script grid + queue bảng %."""
    return _html_file("index4.html")


@router.get("/hongguo", response_class=HTMLResponse)
@router.get("/ui-hg", response_class=HTMLResponse)
def ui_hongguo():
    """UI riêng — điều khiển Hongguo Downloader (search/tải/merge)."""
    return _html_file("hongguo.html")


@router.get("/hongguo2", response_class=HTMLResponse)
def ui_hongguo2():
    """Hongguo Downloader 2.x skin (Browse / Ranking / New / Library)."""
    return _html_file("hongguo2.html")


@router.get("/films/ui", response_class=HTMLResponse)
@router.get("/film", response_class=HTMLResponse)
@router.get("/films", response_class=HTMLResponse)
def ui_films(request: Request):
    """Films UI (thư viện phim / mùa mới) — cùng host :8801.

    fix 2026-11-14: `GET /films` cũng là API JSON danh sách phim (routes_films.list_films) mà CHÍNH films_ui.html
    gọi (`fetch('/films?limit=…')`). Router này nạp trước nên trước đây mọi lời gọi đó nhận HTML → trang Phim hỏng
    («reading 'series'»). Nay: trình duyệt mở trang (không query, Accept HTML) → HTML; còn lại → API JSON.
    """
    if request.url.path == "/films" and (request.query_params or "text/html" not in request.headers.get("accept", "")):
        import os
        on = os.getenv("PINELINE_ROUTERS")      # None = bật hết (V5); có giá trị = danh sách router instance bật
        try:
            if on is not None and "films" not in {x.strip() for x in on.split(",")}:
                raise RuntimeError("instance không bật router «films»")
            from http_routes import routes_films
        except Exception as e:  # noqa: BLE001 — router films tắt / lỗi import
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": f"thư viện phim chưa bật trên instance này ({e})"}, status_code=404)
        qp = request.query_params
        try:
            limit = int(qp.get("limit") or 5000)
        except ValueError:
            limit = 5000
        from fastapi.responses import JSONResponse
        # route khai báo response_class=HTMLResponse → phải tự bọc JSONResponse (dict trả thẳng sẽ hỏng 500)
        return JSONResponse(routes_films.list_films(q=qp.get("q", ""), status=qp.get("status", ""), limit=limit,
                                                    order=qp.get("order", "recent"), work_exists=qp.get("work_exists"),
                                                    block_status=qp.get("block_status", "")))
    return _html_file("films_ui.html")


@router.get("/studio", response_class=HTMLResponse)
@router.get("/studio/ui", response_class=HTMLResponse)
def ui_studio():
    """Lồng tiếng Studio — UI3 ops shell."""
    return _html_file("index3.html")


@router.get("/v5-map", response_class=HTMLResponse)
def ui_v5_map():
    """Interactive module/call graph (static HTML; curated nodes)."""
    return _html_file("v5-map.html")
