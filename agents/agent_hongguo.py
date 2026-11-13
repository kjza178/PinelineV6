"""agent_hongguo.py — Tích hợp bộ tool Hongguo Downloader & Auto-chain cho Pineline AI Agent.

Hỗ trợ 4 nhóm chức năng:
1. Cơ bản: hg_search, hg_download, hg_status, hg_library
2. Hậu kỳ: hg_merge, hg_clean
3. Khám phá: hg_rank, hg_preview
4. Tự động hóa A-Z: hg_auto_flow
"""

from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import agents.hongguo_bridge as hg
import engines.title_mt as title_mt

_LAST_RANK_CACHE: list[dict] = []

FLOW_MODES = ("name", "lib", "rank")
_LIB_WORDS = frozenset({"lib", "thư viện", "thu vien", "chưa làm", "chua lam", "kho"})
_RANK_RE = re.compile(r"^(top|bxh|rank|hot|trending|leaderboard|catalog)(\s*#?\d+)?$")

_BG_FLOWS: dict[str, float] = {}
_BG_LOCK = threading.Lock()


def detect_flow_mode(target: str) -> str:
    """Chỉ nhận cả chuỗi là từ khoá; tên phim chứa 'top'/'hot'/'kho' vẫn là 'name'."""
    t = re.sub(r"\s+", " ", (target or "").strip().lower())
    if t in _LIB_WORDS:
        return "lib"
    if _RANK_RE.match(t):
        return "rank"
    return "name"


def _find_hongguo_executable() -> tuple[Path | None, Path | None]:
    """Tìm đường dẫn pythonw.exe và launcher.pyc/app.pyc của Hongguo Downloader."""
    # 1. Kiểm tra biến môi trường hoặc thư mục AppData chuẩn
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Hongguo Downloader",
        Path(r"C:\Users\MyPC\AppData\Local\Programs\Hongguo Downloader"),
    ]
    for d in candidates:
        if d.is_dir():
            pyw = d / "python" / "pythonw.exe"
            launcher = d / "launcher.pyc"
            if not launcher.exists():
                launcher = d / "app.pyc"
            if pyw.is_file() and launcher.is_file():
                return pyw, launcher
    return None, None


def ensure_hongguo_running(timeout: float = 12.0) -> dict[str, Any]:
    """Kiểm tra Hongguo Downloader, nếu tắt sẽ tự khởi chạy."""
    p = hg.ping()
    if p.get("ok"):
        return {"ok": True, "running": True, "message": "Hongguo Downloader đang chạy sẵn sàng.", "base": p.get("base")}

    pyw, launcher = _find_hongguo_executable()
    if not pyw or not launcher:
        return {
            "ok": False,
            "running": False,
            "error": "Không tìm thấy thư mục cài đặt Hongguo Downloader (mặc định tại AppData/Local/Programs/Hongguo Downloader).",
        }

    try:
        # Khởi chạy ngầm không chặn
        subprocess.Popen(
            [str(pyw), str(launcher)],
            cwd=str(pyw.parent.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        return {"ok": False, "running": False, "error": f"Lỗi khi khởi chạy Hongguo: {e}"}

    # Chờ ping thành công
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(1.0)
        p = hg.ping()
        if p.get("ok"):
            return {
                "ok": True,
                "running": True,
                "message": "Đã tự động khởi chạy Hongguo Downloader thành công.",
                "base": p.get("base"),
            }

    return {
        "ok": False,
        "running": False,
        "error": f"Đã gửi lệnh bật nhưng sau {int(timeout)}s Hongguo Downloader chưa phản hồi :8000.",
    }


def tool_hg_search(query: str, expand: bool = False, limit: int = 10) -> str:
    """Tìm kiếm phim trên Hongguo theo tên hoặc từ khóa."""
    q = (query or "").strip()
    if not q:
        return json.dumps({"error": "thiếu query tìm kiếm"}, ensure_ascii=False)

    try:
        # Ưu tiên tìm nhanh qua API local :8000 (< 500ms).
        # Chỉ khi không ra kết quả và user yêu cầu expand mới thử mở rộng web.
        res = hg.search(q, expand=False)
        results = res.get("results") or []
        if not results and expand:
            res = hg.search(q, expand=True)
            results = res.get("results") or []
        # Tự động dịch tiêu đề sang Tiếng Việt cho kết quả search
        tr_data = title_mt.translate_titles(results[:limit], workers=10)
        tr_items = tr_data.get("items") or results[:limit]
        items = []
        for r in tr_items:
            items.append({
                "series_id": r.get("series_id"),
                "title": r.get("title"),
                "title_vi": r.get("title_vi") or "",
                "episodes": r.get("episode_cnt"),
                "score": r.get("score"),
                "cover": r.get("cover"),
            })
        return json.dumps({
            "ok": True,
            "query": q,
            "total_found": len(results),
            "results": items,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Lỗi tìm kiếm: {e}"}, ensure_ascii=False)


_LAST_PAGE_TITLE: str = "🔥 BXH Hongguo"

# Màn hình danh sách đang mở (giống app Hongguo Downloader): Leaderboard hoặc Catalog
_VIEW: dict[str, Any] = {
    "kind": "rank", "cat": "ai",
    "genre": "", "sort": "newest", "status": "", "q": "",
    "api_page": 1, "api_pages": 1, "count": 0,
}
LB_CATS = (("ai", "AI"), ("human", "Live-action"), ("comic", "Animated"))
CAT_SORTS = (("newest", "Mới nhất"), ("popular", "Phổ biến"), ("eps", "Nhiều tập"))
CAT_STATUS = (("", "Tất cả"), ("completed", "Hoàn thành"), ("ongoing", "Đang ra"))


def _norm_lb_cat(category: str) -> str:
    c = (category or "").lower()
    if any(w in c for w in ("anim", "anime", "hoạt hình", "hoathinh", "comic")):
        return "comic"
    if any(w in c for w in ("live", "human", "người đóng")):
        return "human"
    return "ai"


def load_leaderboard(category: str = "ai") -> list[dict]:
    """Leaderboard như app: /dl/rank board=new, 100 phim / thể loại (AI · Live-action · Animated)."""
    global _LAST_RANK_CACHE, _LAST_PAGE_TITLE
    cat = _norm_lb_cat(category)
    res = hg.rank(category=cat, board="new", size=100)
    items = []
    for i, r in enumerate(res.get("results") or [], 1):
        items.append({
            "rank": i,
            "series_id": str(r.get("series_id") or ""),
            "title": r.get("title") or "",
            "score": r.get("score"),
            "heat": r.get("heat"),
            "episodes": r.get("episode_cnt"),
            "cover": r.get("cover"),
        })
    _VIEW.update({"kind": "rank", "cat": cat, "api_page": 1, "api_pages": 1, "count": len(items)})
    _LAST_RANK_CACHE = items
    _LAST_PAGE_TITLE = f"🏆 Leaderboard · {dict(LB_CATS)[cat]}"
    return items


def load_catalog(genre: str | None = None, sort: str | None = None, status: str | None = None,
                 q: str | None = None, api_page: int = 1) -> list[dict]:
    """Catalog như app: explorer.hongguodownloader.com, 100 phim / trang, lọc thể loại · sắp xếp · trạng thái."""
    global _LAST_RANK_CACHE, _LAST_PAGE_TITLE
    for k, v in (("genre", genre), ("sort", sort), ("status", status), ("q", q)):
        if v is not None:
            _VIEW[k] = v
    if _VIEW["kind"] != "catalog":
        _VIEW["sort"] = _VIEW.get("sort") or "newest"
    api_page = max(1, int(api_page or 1))
    res = hg.catalog(genre=_VIEW["genre"], sort=_VIEW["sort"] or "newest", status=_VIEW["status"],
                     q=_VIEW["q"], page=api_page, size=100)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "catalog lỗi")
    base = (api_page - 1) * 100
    items = []
    for i, r in enumerate(res.get("items") or [], 1):
        items.append({
            "rank": base + i,
            "series_id": str(r.get("series_id") or ""),
            "title": r.get("title") or "",
            "score": r.get("score"),
            "episodes": r.get("episodes"),
            "genre": r.get("genre"),
            "cover": r.get("cover"),
        })
    _VIEW.update({"kind": "catalog", "api_page": api_page, "api_pages": int(res.get("pages") or 1),
                  "count": int(res.get("count") or len(items))})
    _LAST_RANK_CACHE = items
    label = " · ".join(x for x in (_VIEW["genre"] or "Tất cả", dict(CAT_SORTS).get(_VIEW["sort"], ""),
                                   dict(CAT_STATUS).get(_VIEW["status"], "")) if x)
    _LAST_PAGE_TITLE = f"🎬 Catalog · {label}" + (f" · «{_VIEW['q']}»" if _VIEW["q"] else "")
    return items


def _enrich_page(items: list[dict]) -> None:
    """Dịch tên + lấy số tập thật cho các phim của trang đang hiện (lười, có cache trong item)."""
    need_tr = [r for r in items if "title_vi" not in r]
    if need_tr:
        try:
            tr = title_mt.translate_titles([{"series_id": r["series_id"], "title": r["title"]} for r in need_tr],
                                           workers=10).get("items") or []
            m = {str(t.get("series_id")): t.get("title_vi") or "" for t in tr}
            for r in need_tr:
                r["title_vi"] = m.get(r["series_id"], "")
        except Exception:
            for r in need_tr:
                r["title_vi"] = ""
    need_eps = [r["series_id"] for r in items if not r.get("episodes") and r.get("series_id")]
    if need_eps:
        try:
            eps = hg.rank_episodes(need_eps).get("eps") or {}
            for r in items:
                if not r.get("episodes") and eps.get(r["series_id"]):
                    r["episodes"] = eps[r["series_id"]]
        except Exception:
            pass


def _item_dict(r: dict) -> dict:
    return {
        "rank": r["rank"], "series_id": r["series_id"], "title": r["title"],
        "title_vi": r.get("title_vi") or "", "score": r.get("score"), "heat": r.get("heat"),
        "episodes": r.get("episodes"), "genre": r.get("genre"),
        "preview_url": watch_url(r["series_id"], 1) if r.get("series_id") else None,
    }


def tool_hg_rank(category: str = "ai", board: str = "new", size: int = 15) -> str:
    """Leaderboard Hongguo (AI / Live-action / Animated) kèm tên Việt + link xem thử từng phim."""
    try:
        items = load_leaderboard(category)
        shown = items[:max(1, min(100, int(size or 15)))]
        _enrich_page(shown)
        return json.dumps({
            "ok": True, "view": "leaderboard", "category": _VIEW["cat"], "count": len(items),
            "rankings": [_item_dict(r) for r in shown],
            "hint": "Mỗi phim có preview_url (xem thử tập miễn phí). Số thứ tự rank dùng được cho hg_preview / hg_auto_flow.",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Lỗi lấy bảng xếp hạng: {e}"}, ensure_ascii=False)


def tool_hg_catalog(genre: str = "", sort: str = "newest", status: str = "", q: str = "",
                    page: int = 1, size: int = 15) -> str:
    """Catalog Hongguo theo thể loại / sắp xếp / trạng thái / từ khoá, kèm link xem thử từng phim."""
    try:
        items = load_catalog(genre=genre or "", sort=sort or "newest", status=status or "", q=q or "",
                             api_page=page)
        shown = items[:max(1, min(100, int(size or 15)))]
        _enrich_page(shown)
        return json.dumps({
            "ok": True, "view": "catalog", "genre": _VIEW["genre"], "sort": _VIEW["sort"],
            "status": _VIEW["status"], "page": _VIEW["api_page"], "pages": _VIEW["api_pages"],
            "total": _VIEW["count"], "items": [_item_dict(r) for r in shown],
            "hint": "Mỗi phim có preview_url (xem thử tập miễn phí). Số thứ tự rank dùng được cho hg_preview / hg_auto_flow.",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Lỗi lấy danh mục catalog: {e}"}, ensure_ascii=False)


def tool_hg_genres() -> str:
    """Lấy danh sách các thể loại phim có sẵn trong Hongguo Catalog."""
    try:
        g_list = hg.genres()
        return json.dumps({"ok": True, "genres": g_list}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Lỗi lấy danh sách thể loại: {e}"}, ensure_ascii=False)


def _mark(on: bool, label: str) -> str:
    return f"• {label}" if on else label


def render_view(page: int = 1, per_page: int = 10) -> tuple[str, list]:
    """Trang danh sách kiểu app: phim + nút 👁 xem thử cạnh ▶ làm, tab Leaderboard/Catalog, bộ lọc, phân trang."""
    if not _LAST_RANK_CACHE:
        return "Danh sách phim trống. Gõ /hg rank hoặc /hg catalog để tải danh sách.", []
    total_items = len(_LAST_RANK_CACHE)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), total_pages))
    page_items = _LAST_RANK_CACHE[(page - 1) * per_page: page * per_page]
    _enrich_page(page_items)
    is_cat = _VIEW["kind"] == "catalog"

    if is_cat:
        head = (f"{_LAST_PAGE_TITLE}\nTrang {_VIEW['api_page']}/{_VIEW['api_pages']} · phần {page}/{total_pages}"
                f" · {_VIEW['count']} phim")
    else:
        head = f"{_LAST_PAGE_TITLE}\nTrang {page}/{total_pages} · {total_items} phim"
    lines = [head, ""]
    for r in page_items:
        t_vi, t_zh = r.get("title_vi") or "", r.get("title") or ""
        disp = f"{t_vi} ({t_zh})" if t_vi and t_vi != t_zh else t_zh
        extra = []
        if r.get("score") and str(r.get("score")) not in ("None", ""):
            extra.append(f"⭐{r['score']}")
        if r.get("episodes"):
            extra.append(f"{r['episodes']} tập")
        heat = r.get("heat")
        if heat and str(heat) != "None":
            extra.append(f"🔥{heat // 1000}k" if isinstance(heat, (int, float)) and heat > 10000 else f"🔥{heat}")
        lines.append(f"{r['rank']}. {disp}" + (f" · {' · '.join(extra)}" if extra else ""))
    lines += ["", "👁 = xem thử tập miễn phí · ▶ = làm A-Z"]

    buttons: list = [[(_mark(not is_cat, "🏆 Leaderboard"), f"hgl:c:{_VIEW['cat']}"),
                      (_mark(is_cat, "🎬 Catalog"), "hgc:open")]]
    if is_cat:
        buttons.append([(f"🏷 Thể loại: {_VIEW['genre'] or 'Tất cả'}", "hgc:genres")])
        buttons.append([(_mark(_VIEW["sort"] == k, v), f"hgc:s:{k}") for k, v in CAT_SORTS])
        buttons.append([(_mark(_VIEW["status"] == k, v), f"hgc:st:{k}") for k, v in CAT_STATUS])
    else:
        buttons.append([(_mark(_VIEW["cat"] == k, v), f"hgl:c:{k}") for k, v in LB_CATS])
    for i in range(0, len(page_items), 2):
        row = []
        for r in page_items[i:i + 2]:
            row += [(f"👁 {r['rank']}", f"hg:pv:{r['series_id']}:1"), (f"▶ {r['rank']}", f"hg:auto:{r['series_id']}")]
        buttons.append(row)
    nav = []
    if page > 1:
        nav.append(("◀ Trước", f"hg:page:{page - 1}"))
    elif is_cat and _VIEW["api_page"] > 1:
        nav.append(("◀ Trang trước", f"hgc:ap:{_VIEW['api_page'] - 1}"))
    nav.append((f"📄 {page}/{total_pages}", "hg:noop"))
    if page < total_pages:
        nav.append(("Sau ▶", f"hg:page:{page + 1}"))
    elif is_cat and _VIEW["api_page"] < _VIEW["api_pages"]:
        nav.append(("Trang sau ▶", f"hgc:ap:{_VIEW['api_page'] + 1}"))
    buttons.append(nav)
    buttons.append([("🖼 Ảnh bìa trang này", f"hgv:covers:{page}")])
    return "\n".join(lines), buttons


render_rank_page = render_view


def render_genre_picker() -> tuple[str, list]:
    genres = hg.genres(25)
    rows = [[(_mark(not _VIEW["genre"], "Tất cả"), "hgc:g:")]]
    row: list = []
    for g in genres:
        row.append((_mark(_VIEW["genre"] == g, g), f"hgc:g:{g}"[:64]))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("↩ Quay lại", "hg:page:1")])
    return "🏷 Chọn thể loại Catalog:", rows


def view_callback(data: str) -> tuple[str, list]:
    """Callback nút tab / lọc / trang của Leaderboard & Catalog → (text, buttons) để sửa tin nhắn."""
    if data.startswith("hgl:c:"):
        load_leaderboard(data[len("hgl:c:"):])
    elif data == "hgc:open":
        load_catalog(api_page=1)
    elif data == "hgc:genres":
        return render_genre_picker()
    elif data.startswith("hgc:g:"):
        load_catalog(genre=data[len("hgc:g:"):], api_page=1)
    elif data.startswith("hgc:s:"):
        load_catalog(sort=data[len("hgc:s:"):] or "newest", api_page=1)
    elif data.startswith("hgc:st:"):
        load_catalog(status=data[len("hgc:st:"):], api_page=1)
    elif data.startswith("hgc:ap:"):
        n = data[len("hgc:ap:"):]
        load_catalog(api_page=int(n) if n.isdigit() else 1)
    return render_view(1)


def page_covers(page: int = 1, per_page: int = 10) -> list[tuple[str, str]]:
    """(cover_url, caption) cho các phim của trang đang hiện."""
    items = _LAST_RANK_CACHE[(max(1, page) - 1) * per_page: max(1, page) * per_page]
    out = []
    for r in items:
        if r.get("cover"):
            t_vi, t_zh = r.get("title_vi") or "", r.get("title") or ""
            out.append((r["cover"], f"{r['rank']}. {t_vi + ' · ' if t_vi and t_vi != t_zh else ''}{t_zh}"))
    return out


# V6 F4: cổng theo instance (core.instance) — thiếu env không rơi về 8801/8802 (V2 production).
from core.instance import server_base as _server_base, server_port as _server_port, sidecar_url as _sidecar_url  # noqa: E402
WATCH_BASE = _sidecar_url()
PINELINE_BASE = _server_base()

# Mã lỗi gốc của Hongguo /dl/preview
_PV_ERRORS = {
    "unavailable": "Phim này không có tập xem thử miễn phí trên hongguoduanju.",
    "locked": "Tập này không nằm trong phần xem thử miễn phí.",
}


def cache_pick(idx: int) -> dict | None:
    """Phim theo số thứ tự đang hiển thị (Catalog trang 2 bắt đầu từ 101)."""
    for r in _LAST_RANK_CACHE:
        if r.get("rank") == idx:
            return r
    return _LAST_RANK_CACHE[idx - 1] if 1 <= idx <= len(_LAST_RANK_CACHE) else None


def resolve_series_ref(ref: str) -> tuple[str | None, str | None, str | None]:
    """series_id | số thứ tự trong BXH/catalog vừa xem | tên phim → (series_id, title, lỗi)."""
    q = str(ref or "").strip()
    if not q:
        return None, None, "thiếu tên phim / series_id / số thứ tự"
    if q.isdigit() and len(q) <= 5:
        idx = int(q)
        if not _LAST_RANK_CACHE:
            return None, None, f"chưa có danh sách BXH/catalog nào để chọn #{idx}"
        sel = cache_pick(idx)
        if not sel:
            return None, None, f"#{idx} không có trong danh sách đang xem"
        return str(sel.get("series_id") or ""), sel.get("title"), None
    if q.isdigit() and len(q) >= 15:
        return q, None, None
    res = hg.search(q, expand=False)
    results = res.get("results") or []
    if not results:
        return None, None, f"không tìm thấy phim khớp '{q}'"
    exact = [r for r in results if q.lower() in (r.get("title") or "").lower()]
    top = exact[0] if exact else results[0]
    return str(top.get("series_id") or ""), top.get("title"), None


def watch_url(series_id: str, ep: int = 1) -> str:
    return f"{WATCH_BASE}/hg/watch?sid={series_id}&ep={max(1, int(ep or 1))}"


def preview_info(ref: str, ep: int = 1, fresh: bool = False) -> dict[str, Any]:
    """Tập xem thử miễn phí (Hongguo /dl/preview): link MP4 + số tập free/tổng."""
    sid, title, err = resolve_series_ref(ref)
    if err or not sid:
        return {"ok": False, "error": err or "không xác định được phim"}
    ep_n = max(1, int(ep or 1))
    res = hg.preview_series(sid, ep=ep_n, fresh=fresh)
    if not res.get("ok"):
        code = str(res.get("error") or "")
        out: dict[str, Any] = {
            "ok": False, "series_id": sid, "title": title, "ep": ep_n, "error_code": code,
            "error": _PV_ERRORS.get(code) or code or "Không lấy được preview",
        }
        if code == "locked" and ep_n > 1:
            first = hg.preview_series(sid, ep=1)
            if first.get("ok"):
                out["free"] = first.get("free")
                out["episode_cnt"] = first.get("episode_cnt")
                out["title"] = first.get("title") or title
                out["error"] = f"Tập {ep_n} không miễn phí — chỉ xem thử được tập 1–{first.get('free')}."
        return out
    return {
        "ok": True,
        "series_id": sid,
        "title": res.get("title") or title,
        "ep": res.get("ep") or ep_n,
        "free": res.get("free"),
        "episode_cnt": res.get("episode_cnt"),
        "duration_sec": res.get("duration"),
        "width": res.get("width"),
        "height": res.get("height"),
        "url": res.get("url"),
        "poster": res.get("poster"),
        "watch_url": watch_url(sid, ep_n),
    }


def download_preview(url: str, dest: Path, max_bytes: int = 49 * 1024 * 1024) -> Path | None:
    """Tải MP4 xem thử về máy. CDN trả 403 nếu request có Referer nên không gửi Referer.
    Trả None nếu file vượt max_bytes (giới hạn upload của bot Telegram)."""
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, stream=True, timeout=(15, 120)) as r:
        r.raise_for_status()
        size = int(r.headers.get("Content-Length") or 0)
        if size and size > max_bytes:
            return None
        got = 0
        tmp = dest.with_suffix(".part")
        with tmp.open("wb") as f:
            for chunk in r.iter_content(256 * 1024):
                got += len(chunk)
                if got > max_bytes:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    return None
                f.write(chunk)
        tmp.replace(dest)
    return dest


def render_watch_page(series_id: str, ep: int = 1) -> str:
    """Trang HTML xem thử: <video> không gửi Referer (CDN chặn Referer localhost)."""
    import html as _html

    info = preview_info(series_id, ep)
    sid = _html.escape(str(info.get("series_id") or series_id))
    title = _html.escape(str(info.get("title") or sid))
    free = int(info.get("free") or 0)
    total = info.get("episode_cnt") or "?"
    cur = int(info.get("ep") or ep or 1)
    tabs = "".join(
        f'<a class="ep{" on" if i == cur else ""}" href="/hg/watch?sid={sid}&ep={i}">Tập {i}</a>'
        for i in range(1, free + 1)
    )
    if info.get("ok"):
        nxt = f"/hg/watch?sid={sid}&ep={cur + 1}" if cur < free else ""
        body = (
            f'<video id="v" src="{_html.escape(info["url"])}" poster="{_html.escape(info.get("poster") or "")}"'
            ' controls autoplay playsinline></video>'
            + (f'<script>v.onended=()=>location.href="{nxt}"</script>' if nxt else "")
        )
    else:
        body = f'<p class="err">{_html.escape(str(info.get("error")))}</p>'
    return (
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        f"<title>Xem thử · {title}</title>"
        "<style>body{margin:0;background:#111;color:#eee;font:15px system-ui;text-align:center}"
        "h1{font-size:18px;margin:12px}video{max-width:100vw;max-height:80vh;background:#000}"
        ".ep{display:inline-block;margin:4px;padding:6px 12px;border-radius:6px;background:#333;color:#eee;"
        "text-decoration:none}.ep.on{background:#e53}.err{color:#f88;margin:40px}</style></head><body>"
        f"<h1>{title}</h1><div>Tập {cur}/{total} · miễn phí {free or '?'} tập</div>"
        f"<div>{tabs}</div>{body}</body></html>"
    )


def tool_hg_preview(series_id: str = "", ep: int = 1, folder_name: str = "") -> str:
    """Xem thử phim mà không cần tải: tập miễn phí online, hoặc phim đã tải trong D:\\Lib."""
    fn = (folder_name or "").strip()
    if fn:
        try:
            p = hg.resolve_preview_path(fn)
            if not p:
                return json.dumps({"error": f"'{fn}' chưa có file video trong thư viện"}, ensure_ascii=False)
            import urllib.parse
            return json.dumps({
                "ok": True,
                "source": "library",
                "folder_name": fn,
                "file": p.name,
                "watch_url": f"{PINELINE_BASE}/api/hg/library/preview?name={urllib.parse.quote(fn)}",
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": f"Lỗi preview thư viện: {e}"}, ensure_ascii=False)
    try:
        info = preview_info(series_id, ep=ep)
        if info.get("ok"):
            info["source"] = "online_free"
            info["note"] = (
                "Gửi user watch_url (trang xem, tự chuyển tập). Link url gốc hết hạn sau ~1 giờ "
                "và bị chặn nếu mở kèm Referer."
            )
        return json.dumps(info, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Lỗi preview: {e}"}, ensure_ascii=False)


def tool_hg_download(series_ids: list[str]) -> str:
    """Đưa danh sách series_id vào hàng đợi tải của Hongguo Downloader."""
    if not series_ids:
        return json.dumps({"error": "danh sách series_ids trống"}, ensure_ascii=False)

    chk = ensure_hongguo_running()
    if not chk.get("ok"):
        return json.dumps(chk, ensure_ascii=False)

    sids = [str(x).strip() for x in series_ids if str(x).strip()]
    try:
        res = hg.submit(sids)
        return json.dumps({
            "ok": True,
            "message": f"Đã gửi {len(sids)} bộ phim vào hàng đợi tải của Hongguo.",
            "series_ids": sids,
            "detail": res,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Lỗi submit tải: {e}"}, ensure_ascii=False)


def tool_hg_status() -> str:
    """Kiểm tra tiến độ tải của Hongguo Downloader."""
    try:
        p = hg.ping()
        if not p.get("ok"):
            return json.dumps({"ok": False, "running": False, "message": "Hongguo Downloader hiện đang tắt."}, ensure_ascii=False)

        st = hg.status()
        return json.dumps({
            "ok": True,
            "running": st.get("running"),
            "mode": st.get("mode"),
            "active_tasks": st.get("active", 0),
            "speed_rate": f"{round(st.get('rate', 0) / (1024 * 1024), 2)} MB/s",
            "episodes_done": st.get("eps_done", 0),
            "episodes_need": st.get("eps_need", 0),
            "queue_series": [
                {
                    "title": s.get("title"),
                    "series_id": s.get("series_id"),
                    "progress": s.get("progress"),
                    "status": s.get("status"),
                }
                for s in (st.get("series") or [])[:10]
            ],
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Lỗi kiểm tra status: {e}"}, ensure_ascii=False)


def tool_hg_library(query: str = "") -> str:
    """Liệt kê các bộ phim trong thư viện tải về (D:\\Lib)."""
    try:
        lib = hg.library()
        items = lib.get("items") or []
        q = (query or "").strip().lower()
        if q:
            items = [it for it in items if q in (it.get("name") or "").lower()]
        summary = []
        for it in items:
            summary.append({
                "folder_name": it.get("name"),
                "total_episodes": it.get("episodes"),
                "merged": it.get("merged"),
                "merged_file": it.get("merged_file"),
                "fragments": it.get("fragments", 0),
                "done": it.get("done"),
            })
        return json.dumps({
            "ok": True,
            "output_dir": lib.get("output_dir", str(_machine.library_dir())),
            "total_series": len(items),
            "series": summary,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Lỗi đọc thư viện: {e}"}, ensure_ascii=False)


def tool_hg_merge(folder_name: str) -> str:
    """Gộp các tập video mảnh (.mp4) trong thư viện thành 1 file (merged).mp4."""
    fn = (folder_name or "").strip()
    if not fn:
        return json.dumps({"error": "thiếu folder_name của bộ phim trong thư viện"}, ensure_ascii=False)

    try:
        # Hàng đợi merge sống trong server :8801 (UI thấy được); chỉ chạy cục bộ khi server không phản hồi.
        import requests
        port = os.environ.get("PORT") or str(_server_port())
        try:
            res = requests.post(f"http://127.0.0.1:{port}/api/hg/merge",
                                json={"names": [fn], "force": True}, timeout=30).json()
        except requests.RequestException:
            res = hg.merge_queue_add([fn], skip_done=False)
        if not res.get("ok", True) or res.get("error"):
            return json.dumps({"error": f"Lỗi merge: {res.get('error') or res}"}, ensure_ascii=False)
        return json.dumps({
            "ok": True,
            "message": f"Đã xếp bộ '{fn}' vào hàng đợi gộp video (1080p chuẩn hóa).",
            "detail": res,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Lỗi merge: {e}"}, ensure_ascii=False)


def tool_hg_clean(folder_name: str) -> str:
    """Dọn dẹp/xóa các file tập mảnh lẻ sau khi đã gộp xong file (merged).mp4."""
    fn = (folder_name or "").strip()
    if not fn:
        return json.dumps({"error": "thiếu folder_name"}, ensure_ascii=False)

    try:
        lib = hg.library()
        root = Path(lib.get("output_dir") or str(_machine.library_dir()))
        target_dir = root / fn
        if not target_dir.is_dir():
            it, ambiguous = _pick_lib_item(lib.get("items") or [], fn)
            if ambiguous:
                return json.dumps({"error": f"'{fn}' khớp nhiều thư mục — nói tên đầy đủ",
                                   "candidates": ambiguous[:10]}, ensure_ascii=False)
            if not it or not (root / (it.get("name") or "")).is_dir():
                return json.dumps({"error": f"Không tìm thấy thư mục '{fn}' trong {root}"}, ensure_ascii=False)
            target_dir = root / it["name"]

        res = hg.cleanup_fragments(target_dir)
        return json.dumps({
            "ok": res.get("ok", False),
            "folder": target_dir.name,
            "removed_count": res.get("removed", 0),
            "merged_file": res.get("merged_file"),
            "detail": res,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Lỗi cleanup: {e}"}, ensure_ascii=False)


def _pick_lib_item(items: list[dict], target: str) -> tuple[dict | None, list[str] | None]:
    """Chọn folder thư viện: tên trùng khớp → series_id → chuỗi con DUY NHẤT.
    Nhiều kết quả chuỗi con (bộ gốc vs 第三季) → trả danh sách để hỏi lại."""
    t = (target or "").strip().lower()
    if not t:
        return None, None
    for it in items:
        if (it.get("name") or "").strip().lower() == t or (it.get("title") or "").strip().lower() == t:
            return it, None
    if t.isdigit():
        for it in items:
            if str(it.get("series_id") or "") == t:
                return it, None
        if len(t) >= 15:
            return None, None
    subs = [it for it in items if t in (it.get("name") or "").lower()]
    if len(subs) == 1:
        return subs[0], None
    if len(subs) > 1:
        return None, [it.get("name") or "" for it in subs]
    return None, None


def _get_existing_pineline_titles(rt: Any = None) -> set[str]:
    """Lấy danh sách tiêu đề các phim đã tạo Job trong Pineline để tránh làm trùng."""
    existing = set()
    try:
        if rt and hasattr(rt, "list_jobs"):
            jobs = rt.list_jobs() or []
        else:
            server_port = os.environ.get("PORT") or str(_server_port())
            import requests
            r = requests.get(f"http://127.0.0.1:{server_port}/api/jobs", timeout=5)
            jobs = r.json() if r.status_code == 200 else []
        for j in jobs:
            t = (j.get("title") or "").strip().lower()
            tzh = (j.get("title_zh") or "").strip().lower()
            if t:
                existing.add(t)
            if tzh:
                existing.add(tzh)
    except Exception:
        pass
    return existing


def tool_hg_series(query_or_id: str) -> str:
    """Tra cứu trọn bộ các phần / season của một bộ phim (Full series / Đồng hệ liệt)."""
    q = (query_or_id or "").strip()
    if not q:
        return json.dumps({"error": "thiếu tên phim hoặc series_id"}, ensure_ascii=False)

    ensure_hongguo_running()

    # 1. Nếu là số thứ tự 1-20 từ BXH vừa xem
    if q.isdigit() and len(q) <= 5 and _LAST_RANK_CACHE:
        sel = cache_pick(int(q))
        if sel:
            q = sel.get("series_id") or sel.get("title")

    top_sid = None
    target_title = None

    # 2. Nếu là số ID dài (>= 15 ký tự)
    if q.isdigit() and len(q) >= 15:
        top_sid = q
    else:
        res = hg.search(q, expand=False)
        results = res.get("results") or []
        exact = [r for r in results if q.lower() in (r.get("title") or "").lower()]
        top = exact[0] if exact else (results[0] if results else None)
        if top:
            top_sid = str(top.get("series_id"))
            target_title = top.get("title")

    if not top_sid:
        return json.dumps({
            "ok": False,
            "message": f"Không tìm thấy phim nào khớp với '{q}' để tra cứu series.",
        }, ensure_ascii=False)

    # 3. Lấy thông tin từ Baike (mục 同系列 - related)
    seasons: list[dict] = []
    seen_sids: set[str] = set()

    try:
        b_info = hg.baike_info(top_sid)
        target_title = target_title or b_info.get("title")
        for rel in b_info.get("related") or []:
            sid = str(rel.get("series_id") or "")
            if sid and sid not in seen_sids:
                seen_sids.add(sid)
                seasons.append({
                    "series_id": sid,
                    "title": rel.get("title"),
                    "episode_cnt": rel.get("episode_cnt"),
                    "date": rel.get("date"),
                    "score": b_info.get("score") if sid == top_sid else None,
                })
    except Exception:
        pass

    # 4. Chỉ mở rộng thêm bằng hg.search nếu Baike chưa tìm thấy hoặc chỉ có 1 phần đơn lẻ
    if len(seasons) <= 1:
        try:
            stem = hg.season_stem(target_title or q)
            s_res = hg.search(stem, expand=True)
            for g in s_res.get("groups") or []:
                if g.get("franchise") or stem.lower() in (g.get("stem") or "").lower():
                    for it in g.get("items") or []:
                        it_title = it.get("title") or ""
                        # Chỉ nhận nếu cùng stem hoặc thương hiệu gốc
                        if stem.lower() in it_title.lower() or stem.lower() in hg.season_stem(it_title).lower():
                            sid = str(it.get("series_id") or "")
                            if sid and sid not in seen_sids:
                                seen_sids.add(sid)
                                seasons.append({
                                    "series_id": sid,
                                    "title": it_title,
                                    "episode_cnt": it.get("episodes"),
                                    "score": it.get("score"),
                                })
        except Exception:
            pass

    if not seasons and top_sid:
        seasons.append({
            "series_id": top_sid,
            "title": target_title or q,
        })

    # 5. Lấy số tập chính xác qua /dl/rank-episodes
    all_sids = [s["series_id"] for s in seasons]
    try:
        ep_map = hg.rank_episodes(all_sids).get("eps") or {}
        for s in seasons:
            s["episodes"] = ep_map.get(str(s["series_id"])) or s.get("episode_cnt")
    except Exception:
        pass

    # 6. Dịch tiêu đề sang Tiếng Việt
    try:
        tr_data = title_mt.translate_titles(seasons, workers=10)
        tr_map = {r["series_id"]: r.get("title_vi") for r in (tr_data.get("items") or [])}
        for s in seasons:
            s["title_vi"] = tr_map.get(s["series_id"]) or s.get("title")
    except Exception:
        pass

    # Sắp xếp lại theo mùa (S1, S2...)
    try:
        seasons = hg.order_franchise_items(seasons)
    except Exception:
        pass

    total_eps = sum((s.get("episodes") or 0) for s in seasons if isinstance(s.get("episodes"), int))

    return json.dumps({
        "ok": True,
        "query": q,
        "target_title": target_title or q,
        "total_seasons": len(seasons),
        "total_episodes": total_eps,
        "seasons": seasons,
    }, ensure_ascii=False, indent=2)

def tool_hg_auto_flow(
    query_or_id: str,
    voice: str | None = None,
    tts_engine: str | None = None,
    auto_run: bool = True,
    rt: Any = None,
    notify_cb: Any = None,
    mode: str | None = None,
) -> str:
    """Workflow tự động A-Z (mode: name | lib | rank; bỏ trống = đoán theo cả chuỗi):
    1. Chọn phim (từ tên/ID HOẶC bốc từ Bảng xếp hạng 'leaderboard'/'top 1' HOẶC từ thư viện 'lib')
    2. Tự tải nếu chưa có (chờ tải hoàn tất 100%)
    3. Gộp video chuẩn 1080p (chờ gộp xong file (merged).mp4)
    4. Tự động XÓA TẬP MẢNH LẺ để giải phóng ổ đĩa
    5. Thêm Job vào Pineline V2
    6. Xếp vào hàng chờ chạy tự động đến video xuất xưởng (ASR -> Dịch -> TTS -> Render).
    """
    target = (query_or_id or "").strip()
    mode = (mode or "").strip().lower() or detect_flow_mode(target)
    if mode not in FLOW_MODES:
        return json.dumps({"error": f"mode '{mode}' không hợp lệ (name | lib | rank)"}, ensure_ascii=False)
    if not target and mode == "name":
        return json.dumps({"error": "thiếu tên phim, ID, hoặc từ khóa ('top 1', 'leaderboard', 'lib')"}, ensure_ascii=False)

    # Tự động lấy cấu hình voice/tts từ batch config nếu người dùng không chỉ định riêng
    try:
        import core.batch_config as batch_config
        bcfg = batch_config.load() or {}
        voice = voice or bcfg.get("voice")
        if not voice:
            import engines.clone_tts as clone_tts
            avail = clone_tts.list_clone_voices()
            voice = avail[0] if avail else "v2"
        tts_engine = tts_engine or bcfg.get("tts_engine") or "vieneu"
    except Exception:
        voice = voice or "v2"
        tts_engine = tts_engine or "vieneu"

    # Nếu truyền số thứ tự 1, 2, 3... theo danh sách BXH vừa xem
    if mode == "name" and target.isdigit() and len(target) <= 5:
        sel = cache_pick(int(target)) if _LAST_RANK_CACHE else None
        if not sel:
            # Không đoán: khớp chuỗi con "3" sẽ trúng 第3季 / 2023 → gộp + dọn nhầm bộ
            return json.dumps({
                "ok": False, "stage": "select_rank",
                "message": f"Chưa có danh sách nào để chọn số {target} (hoặc số vượt danh sách). "
                           "Hãy xem BXH/thư viện trước, hoặc nói tên phim / series_id.",
            }, ensure_ascii=False)
        target = sel.get("series_id") or sel.get("title")

    # Đảm bảo Hongguo Downloader đang chạy
    ensure_hongguo_running()

    lib = hg.library()
    root = Path(lib.get("output_dir") or str(_machine.library_dir()))
    items = lib.get("items") or []
    existing_titles = _get_existing_pineline_titles(rt)

    found_folder = None

    # Nhánh A: Chọn từ Thư viện D:\Lib ('lib', 'thư viện', 'chưa làm')
    if mode == "lib":
        for it in items:
            fn = (it.get("name") or "").strip()
            if fn.lower() not in existing_titles:
                found_folder = it
                break
        if not found_folder and items:
            found_folder = items[0]
        if not found_folder:
            return json.dumps({
                "ok": False,
                "stage": "select_lib",
                "message": "Thư viện D:\\Lib hiện chưa có bộ phim nào tải về. Bạn có thể chọn từ BXH ('top 1') hoặc nhập tên phim.",
            }, ensure_ascii=False)

    # Nhánh B: Chọn từ Bảng xếp hạng / Leaderboard ('top', 'hot', 'bxh', 'leaderboard', 'catalog')
    elif mode == "rank":
        try:
            rank_res = hg.rank(category="all", board="hot", size=20)
            rankings = rank_res.get("results") or []
            selected_item = None
            for r in rankings:
                rtitle = (r.get("title") or "").strip()
                if rtitle.lower() not in existing_titles:
                    selected_item = r
                    break
            if not selected_item and rankings:
                selected_item = rankings[0]

            if selected_item:
                target = selected_item.get("title") or selected_item.get("series_id")
            else:
                return json.dumps({"ok": False, "message": "Không lấy được danh sách phim từ Bảng xếp hạng Hongguo."}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"Lỗi lấy BXH Hongguo: {e}"}, ensure_ascii=False)

    # Nhánh C: Tìm kiếm theo tên hoặc series_id cụ thể trong thư viện
    if not found_folder:
        found_folder, ambiguous = _pick_lib_item(items, target)
        if ambiguous:
            return json.dumps({
                "ok": False, "stage": "ambiguous",
                "message": f"'{target}' khớp nhiều bộ trong thư viện — nói tên đầy đủ hoặc series_id.",
                "candidates": ambiguous[:10],
            }, ensure_ascii=False)

    # 2. Xử lý tải nếu chưa có trong thư viện -> Tải & Chờ tải xong 100%
    if not found_folder:
        if target.isdigit() and len(target) >= 15:
            sid = target
            title = None
            try:
                b_info = hg.baike_info(sid)
                title = b_info.get("title")
            except Exception:
                pass
            if not title:
                title = sid
        else:
            search_res = hg.search(target, expand=False)
            results = search_res.get("results") or []
            if not results:
                return json.dumps({
                    "ok": False,
                    "stage": "search",
                    "message": f"Không tìm thấy phim nào khớp với '{target}' trên Hongguo Downloader.",
                }, ensure_ascii=False)

            best = results[0]
            sid = best.get("series_id")
            title = best.get("title")

        # Kiểm tra xem có đang trong queue download không, nếu chưa thì submit
        st = hg.status()
        active = [s for s in (st.get("series") or []) if str(s.get("sid")) == str(sid) or s.get("title") == title]
        if not active:
            hg.submit([sid])
            if notify_cb:
                notify_cb(f"📥 Đã gửi lệnh tải '{title}' (ID: {sid}). Đang chờ tải các tập về D:\\Lib...")

        # Vòng lặp chờ tải hoàn tất
        poll_start = time.time()
        last_pct = -1
        dl_finished = False
        while time.time() - poll_start < 1800:
            time.sleep(3)
            st = hg.status()
            active = [s for s in (st.get("series") or []) if str(s.get("sid")) == str(sid) or s.get("title") == title]
            if active:
                s = active[0]
                done = s.get("done", 0)
                total = s.get("total", 0)
                pct = round((done / total * 100)) if total else 0
                if pct >= 100 or s.get("status") == "done":
                    dl_finished = True
                    break
                if notify_cb and pct >= last_pct + 25:
                    last_pct = pct
                    rate_mb = round(st.get("rate", 0) / (1024 * 1024), 1)
                    notify_cb(f"📥 Tiến độ tải '{title}': {done}/{total} tập ({pct}%) · {rate_mb} MB/s")
            elif not st.get("running"):
                if title and (root / title).is_dir():
                    dl_finished = True   # còn kiểm đủ tập ở dưới trước khi gộp
                    break
        if not dl_finished:
            return json.dumps({
                "ok": False, "stage": "download_timeout",
                "message": f"Tải '{title}' chưa xong sau 30 phút — KHÔNG gộp/dọn. Hongguo vẫn tải tiếp; gọi lại sau.",
            }, ensure_ascii=False)

        # Quét lại thư viện sau khi tải xong
        lib = hg.library()
        items = lib.get("items") or []
        found_folder = next((it for it in items if sid and str(it.get("series_id") or "") == str(sid)), None)
        if not found_folder:
            found_folder, _ = _pick_lib_item(items, title or "")

        if not found_folder:
            return json.dumps({
                "ok": False,
                "stage": "download_failed",
                "message": f"Không tìm thấy thư mục phim '{title}' trong D:\\Lib sau khi tải.",
            }, ensure_ascii=False)

    folder_name = found_folder.get("name")
    folder_path = root / folder_name

    # Nếu đang tải dở dang -> Chờ tải hoàn tất 100%
    st = hg.status()
    if st.get("running"):
        active = [s for s in (st.get("series") or []) if (s.get("title") or "").lower() in folder_name.lower() or folder_name.lower() in (s.get("title") or "").lower()]
        if active and active[0].get("status") != "done":
            if notify_cb:
                notify_cb(f"⏳ Phim '{folder_name}' đang tải dở dang. Đang chờ tải hoàn tất 100% để gộp...")
            poll_start = time.time()
            waited_ok = False
            while time.time() - poll_start < 1800:
                time.sleep(3)
                st = hg.status()
                active = [s for s in (st.get("series") or []) if (s.get("title") or "").lower() in folder_name.lower() or folder_name.lower() in (s.get("title") or "").lower()]
                if not active or not st.get("running") or (active and active[0].get("status") == "done"):
                    waited_ok = True
                    break
            if not waited_ok:
                return json.dumps({
                    "ok": False, "stage": "download_timeout",
                    "message": f"'{folder_name}' vẫn đang tải sau 30 phút — KHÔNG gộp/dọn. Gọi lại sau.",
                }, ensure_ascii=False)

    # Đủ tập mới gộp (tải bị dừng giữa chừng vẫn có folder → không được gộp bản thiếu)
    fresh = next((it for it in (hg.library().get("items") or []) if it.get("name") == folder_name), found_folder)
    n_total, n_local = int(fresh.get("total") or 0), int(fresh.get("local") or fresh.get("fragments") or 0)
    if not fresh.get("merged") and n_total and n_local < n_total:
        return json.dumps({
            "ok": False, "stage": "download_incomplete",
            "message": f"'{folder_name}' mới có {n_local}/{n_total} tập — KHÔNG gộp. Tải đủ rồi gọi lại.",
        }, ensure_ascii=False)

    # 3. Gộp video chuẩn 1080p nếu chưa có (merged).mp4 -> Chờ gộp hoàn tất
    info = hg.inspect_series_folder(folder_path)
    merged_file = info.get("merged_file")
    server_port = os.environ.get("PORT") or str(_server_port())
    import requests

    if not merged_file or not (folder_path / merged_file).exists():
        if notify_cb:
            notify_cb(f"🔗 Đang gộp các tập lẻ của '{folder_name}' thành 1 file (merged).mp4 1080p...")
        try:
            requests.post(f"http://127.0.0.1:{server_port}/api/hg/merge", json={"names": [folder_name]}, timeout=10)
        except Exception:
            hg.merge_queue_add([folder_name], skip_done=False)

        # Chờ gộp hoàn tất
        merge_start = time.time()
        last_detail = ""
        while time.time() - merge_start < 1800:
            time.sleep(3)
            info = hg.inspect_series_folder(folder_path)
            merged_file = info.get("merged_file")
            if merged_file and (folder_path / merged_file).exists():
                break
            try:
                mq = requests.get(f"http://127.0.0.1:{server_port}/api/hg/merge-queue", timeout=5).json()
                for h in reversed(mq.get("history") or []):
                    if h.get("name") == folder_name:
                        detail = h.get("detail") or ""
                        if detail and detail != last_detail and ("%" in detail or "done" in detail):
                            last_detail = detail
                            if notify_cb:
                                notify_cb(f"🔗 Tiến độ gộp '{folder_name}': {detail}")
                        break
            except Exception:
                pass

    info = hg.inspect_series_folder(folder_path)
    merged_file = info.get("merged_file")
    if not merged_file or not (folder_path / merged_file).exists():
        return json.dumps({
            "ok": False,
            "stage": "merge_timeout",
            "message": f"Quá thời gian gộp file cho '{folder_name}'. Vui lòng kiểm tra tab gộp video.",
        }, ensure_ascii=False)

    merged_path = folder_path / merged_file

    # 4. TỰ ĐỘNG XÓA TẬP MẢNH LẺ sau khi đã có file merged hoàn chỉnh
    clean_res = hg.cleanup_fragments(folder_path)
    removed_count = clean_res.get("removed", 0)
    if notify_cb and removed_count > 0:
        notify_cb(f"🧹 Đã dọn {removed_count} tập lẻ của '{folder_name}' vào thùng rác (tự xoá sau {hg.TRASH_KEEP_DAYS} ngày).")
    elif notify_cb and not clean_res.get("ok"):
        notify_cb(f"⚠️ Giữ nguyên tập lẻ của '{folder_name}': {clean_res.get('error')}")

    # 5. Kiểm tra và Tạo Job mới trong Pineline V2
    job_id = None
    if folder_name.lower() in existing_titles:
        try:
            r = requests.get(f"http://127.0.0.1:{server_port}/api/jobs", timeout=5)
            for j in r.json():
                if (j.get("title") or "").strip().lower() == folder_name.lower():
                    job_id = j.get("job_id") or j.get("id")
                    break
        except Exception:
            pass
    else:
        try:
            resp = requests.post(
                f"http://127.0.0.1:{server_port}/api/jobs/local-path",
                json={"path": str(merged_path), "title": folder_name, "move": False},
                timeout=300,
            )
            data = resp.json()
            if data.get("ok"):
                job_id = data.get("job_id") or (data.get("job_ids") or [None])[0]
            else:
                return json.dumps({
                    "ok": False,
                    "stage": "create_job",
                    "error": data.get("error") or "Không tạo được job từ file merged.",
                }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"Lỗi kết nối tới Pineline server để tạo job: {e}"}, ensure_ascii=False)

    # 6. Nếu auto_run: xếp vào hàng chờ Pineline
    queue_msg = "Chưa xếp vào hàng chờ."
    if auto_run and job_id:
        try:
            import core.batch_config as batch_config
            bcfg = batch_config.load() or {}
        except Exception:
            bcfg = {}
        opts = dict(bcfg)
        if voice:
            opts["voice"] = voice
        if tts_engine:
            opts["tts_engine"] = tts_engine

        # Lưu cấu hình voice trực tiếp vào thư mục job để đảm bảo manifest & sidecar nhận đúng
        try:
            import core.config as config
            raw_w = config.get("WORK_ROOT", "work") or "work"
            w_root = Path(raw_w)
            if not w_root.is_absolute():
                w_root = Path(__file__).resolve().parents[1] / w_root
            j_tts = w_root / job_id / "tts_config.json"
            if j_tts.parent.exists():
                cur_d = {}
                if j_tts.exists():
                    try:
                        cur_d = json.loads(j_tts.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                if voice:
                    cur_d["voice"] = voice
                if tts_engine:
                    cur_d["engine"] = tts_engine
                j_tts.write_text(json.dumps(cur_d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        if rt and hasattr(rt, "queue_add"):
            rt.queue_add([job_id], opts)
            queue_msg = f"Đã tự động xếp Job {job_id} vào hàng chờ chạy tuần tự (ASR -> Dịch -> TTS -> Render)."
        else:
            try:
                payload = {"job_ids": [job_id], "skip_done": False, **opts}
                requests.post(f"http://127.0.0.1:{server_port}/api/queue/jobs", json=payload, timeout=10)
                queue_msg = f"Đã tự động xếp Job {job_id} vào hàng chờ chạy tuần tự (ASR -> Dịch -> TTS -> Render)."
            except Exception as e:
                queue_msg = f"Lỗi xếp hàng chờ: {e}"

    res_dict = {
        "ok": True,
        "stage": "completed",
        "job_id": job_id,
        "folder": folder_name,
        "merged_file": str(merged_path),
        "removed_fragments": removed_count,
        "message": f"Hoàn tất quy trình A-Z cho '{folder_name}'! Job ID: {job_id}. Đã xóa {removed_count} tập mảnh thừa để tiết kiệm ổ đĩa. {queue_msg}",
        "ui_link": f"http://127.0.0.1:{server_port}/ui3#{job_id}",
    }
    if notify_cb:
        notify_cb(f"🎬 {res_dict['message']}")
    return json.dumps(res_dict, ensure_ascii=False, indent=2)


# Định nghĩa Tool Schema tương thích OpenAI Tool Calling
HONGGUO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "hg_search",
            "description": "Tìm kiếm phim ngắn trên Hongguo theo tên hoặc từ khóa. Trả về danh sách kèm series_id, tiêu đề, số tập, điểm.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Tên phim hoặc từ khóa tìm kiếm"},
                    "expand": {"type": "boolean", "description": "Mở rộng tìm kiếm toàn bộ các mùa liên quan (mặc định false cho nhanh)", "default": False},
                    "limit": {"type": "integer", "description": "Số lượng kết quả trả về", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hg_rank",
            "description": (
                "Leaderboard Hongguo giống app (100 phim / tab). Mỗi phim kèm tên Việt, điểm, số tập, "
                "preview_url (xem thử tập miễn phí)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Tab: 'ai' (mặc định) | 'human' (Live-action / người đóng) | 'comic' (Animated / hoạt hình)",
                        "default": "ai",
                    },
                    "size": {"type": "integer", "description": "Số phim trả về (tối đa 100)", "default": 15},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hg_preview",
            "description": (
                "Xem thử phim không cần tải. Online: chỉ các tập MIỄN PHÍ (thường tập 1–3) — trả watch_url "
                "(trang xem), số tập free, tổng số tập; tập ngoài phần free trả lỗi. "
                "Phim đã tải trong D:\\Lib: truyền folder_name để lấy link xem file local."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "series_id": {
                        "type": "string",
                        "description": "series_id, HOẶC số thứ tự trong BXH/catalog vừa xem, HOẶC tên phim",
                    },
                    "ep": {"type": "integer", "description": "Tập muốn xem (mặc định 1)", "default": 1},
                    "folder_name": {
                        "type": "string",
                        "description": "Tên thư mục phim trong D:\\Lib (xem bản đã tải thay vì online)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hg_download",
            "description": "Gửi danh sách series_id vào hàng đợi tải của Hongguo Downloader (tự động bật app nếu chưa chạy). Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "series_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Danh sách các series_id cần tải",
                    },
                },
                "required": ["series_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hg_status",
            "description": "Xem tiến độ tải phim của Hongguo Downloader (tốc độ tải, số tập hoàn thành, danh sách đang tải).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hg_library",
            "description": "Liệt kê TOÀN BỘ phim trong thư viện đã tải về (D:\\Lib), kiểm tra đã gộp (merged) hay còn mảnh lẻ. "
                           "Truyền query để lọc theo tên folder (vd «第二季»).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Lọc theo chuỗi con trong tên folder (tuỳ chọn)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hg_merge",
            "description": "Gộp các tập video mảnh (.mp4) trong thư viện thành 1 file (merged).mp4 chuẩn hóa 1080p. Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string", "description": "Tên thư mục bộ phim trong D:\\Lib"},
                },
                "required": ["folder_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hg_clean",
            "description": "Xóa các tập mảnh sau khi đã có file (merged).mp4 để giải phóng dung lượng ổ đĩa. Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string", "description": "Tên thư mục bộ phim trong D:\\Lib"},
                },
                "required": ["folder_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hg_auto_flow",
            "description": (
                "Workflow tự động từ A-Z cho 1 bộ phim: Chọn phim từ tên/ID HOẶC chọn lọc từ Thư viện ('lib') "
                "/ Bảng xếp hạng ('leaderboard' / 'top 1') -> Tự tải nếu chưa có -> Tự gộp 1080p nếu chưa gộp "
                "-> Tự xóa sạch các tập mảnh lẻ để tiết kiệm ổ đĩa -> Tạo Job Pineline -> Xếp vào hàng chờ chạy thành phẩm. "
                "Chạy NỀN: trả về 'started' ngay, tiến độ báo qua Telegram — không gọi lại để hỏi tiến độ. "
                "Cần allow_mutate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_or_id": {
                        "type": "string",
                        "description": "Tên phim, ID cụ thể, HOẶC 'top 1' / 'leaderboard' (chọn từ BXH), HOẶC 'lib' (chọn từ thư viện D:\\Lib)",
                    },
                    "voice": {"type": "string", "description": "Giọng đọc TTS lồng tiếng (tuỳ chọn)"},
                    "tts_engine": {"type": "string", "description": "Engine TTS: vieneu hoặc omnivoice (tuỳ chọn)"},
                    "auto_run": {"type": "boolean", "description": "Tự động đưa vào hàng chờ chạy luôn (mặc định true)", "default": True},
                    "mode": {
                        "type": "string", "enum": list(FLOW_MODES),
                        "description": "name = query_or_id là tên/ID phim; lib = bốc bộ chưa làm trong D:\\Lib; "
                                       "rank = bốc từ BXH. Luôn truyền mode khi biết rõ ý user.",
                    },
                },
                "required": ["query_or_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hg_catalog",
            "description": (
                "Catalog Hongguo giống app (~28k phim, 100 phim / trang): lọc thể loại (Animated, AI, Urban, Underdog, "
                "Historical, 3D, System…), sắp xếp (newest, popular, eps), trạng thái, từ khoá. Mỗi phim kèm "
                "preview_url (xem thử tập miễn phí)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "genre": {"type": "string", "description": "Thể loại phim (vd: 'Animated', 'Urban', 'AI', 'System', 'Historical', '3D', v.v.)", "default": ""},
                    "sort": {"type": "string", "description": "Sắp xếp: 'newest' (mới nhất), 'popular' (phổ biến nhất), 'eps' (nhiều tập nhất)", "default": "newest"},
                    "status": {"type": "string", "description": "Trạng thái: '' (tất cả), 'completed' (hoàn thành), 'ongoing' (đang ra)", "default": ""},
                    "q": {"type": "string", "description": "Từ khoá tìm kiếm trong catalog", "default": ""},
                    "page": {"type": "integer", "description": "Trang (100 phim / trang)", "default": 1},
                    "size": {"type": "integer", "description": "Số lượng phim trả về (tối đa 100)", "default": 15},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hg_genres",
            "description": "Lấy danh sách các thể loại phim có sẵn trong Hongguo Catalog.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hg_series",
            "description": "Tra cứu toàn bộ các phần / mùa (full series / các season / cùng hệ liệt) của một bộ phim trên Hongguo kèm số tập chi tiết từng phần.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_or_id": {"type": "string", "description": "Tên phim hoặc series_id hoặc số thứ tự BXH"},
                },
                "required": ["query_or_id"],
            },
        },
    },
]

HONGGUO_MUTATE_TOOLS = frozenset({
    "hg_download",
    "hg_merge",
    "hg_clean",
    "hg_auto_flow",
})


def handle_hongguo_tool(rt: Any, name: str, args: dict[str, Any], allow_mutate: bool) -> str:
    """Điều phối thực thi các tool Hongguo cho AI Agent."""
    if name in HONGGUO_MUTATE_TOOLS and not allow_mutate:
        return json.dumps({
            "error": "mutate_blocked",
            "hint": f"Tool '{name}' cần quyền thao tác. Vui lòng bật 'Cho phép sửa script / chạy-dừng' trên panel Agent.",
        }, ensure_ascii=False)

    if name == "hg_series":
        return tool_hg_series(args.get("query_or_id", ""))
    if name == "hg_search":
        return tool_hg_search(args.get("query", ""), expand=bool(args.get("expand", False)), limit=int(args.get("limit") or 10))
    if name == "hg_rank":
        return tool_hg_rank(category=args.get("category", "ai"), size=int(args.get("size") or 15))
    if name == "hg_catalog":
        return tool_hg_catalog(
            genre=args.get("genre", ""),
            sort=args.get("sort", "newest"),
            status=args.get("status", ""),
            q=args.get("q", ""),
            page=int(args.get("page") or 1),
            size=int(args.get("size") or 15),
        )
    if name == "hg_genres":
        return tool_hg_genres()
    if name == "hg_preview":
        return tool_hg_preview(
            args.get("series_id", ""),
            ep=int(args.get("ep") or 1),
            folder_name=args.get("folder_name", ""),
        )
    if name == "hg_download":
        return tool_hg_download(args.get("series_ids") or [])
    if name == "hg_status":
        return tool_hg_status()
    if name == "hg_library":
        return tool_hg_library(args.get("query", ""))
    if name == "hg_merge":
        return tool_hg_merge(args.get("folder_name", ""))
    if name == "hg_clean":
        return tool_hg_clean(args.get("folder_name", ""))
    if name == "hg_auto_flow":
        return start_auto_flow_bg(rt, args, notify=getattr(rt, "notify", None))

    return json.dumps({"error": f"unknown hongguo tool: {name}"}, ensure_ascii=False)


def running_auto_flows() -> dict[str, float]:
    with _BG_LOCK:
        return dict(_BG_FLOWS)


def start_auto_flow_bg(rt: Any, args: dict[str, Any], notify: Any = None) -> str:
    """Chạy tool_hg_auto_flow trong thread nền; trùng target đang chạy thì không chạy thêm."""
    target = str(args.get("query_or_id") or "").strip()
    mode = str(args.get("mode") or "").strip().lower() or detect_flow_mode(target)
    if mode not in FLOW_MODES:
        return json.dumps({"error": f"mode '{mode}' không hợp lệ (name | lib | rank)"}, ensure_ascii=False)
    if not target and mode == "name":
        return json.dumps({"error": "thiếu tên phim / series_id"}, ensure_ascii=False)
    key = "name:" + re.sub(r"\s+", " ", target.lower()) if mode == "name" else mode
    with _BG_LOCK:
        since = _BG_FLOWS.get(key)
        if since is not None:
            return json.dumps({
                "ok": True, "started": False, "already_running": True, "target": target, "mode": mode,
                "running_min": round((time.time() - since) / 60, 1),
                "message": "Flow cho mục này đang chạy nền — đợi Telegram báo, không gọi lại.",
            }, ensure_ascii=False)
        _BG_FLOWS[key] = time.time()

    def _say(text: str) -> None:
        if notify:
            try:
                notify(text)
            except Exception as e:  # noqa: BLE001
                print(f"[hg_auto_flow] notify lỗi: {e}", flush=True)

    def _run() -> None:
        try:
            raw = tool_hg_auto_flow(
                target, voice=args.get("voice"), tts_engine=args.get("tts_engine"),
                auto_run=bool(args.get("auto_run", True)), rt=rt, notify_cb=_say, mode=mode,
            )
            res = json.loads(raw)
            if not res.get("ok"):
                _say(f"❌ Auto Flow '{target or mode}': {res.get('message') or res.get('error')}")
        except Exception as e:  # noqa: BLE001
            _say(f"❌ Lỗi chạy Auto Flow '{target or mode}': {e}")
        finally:
            with _BG_LOCK:
                _BG_FLOWS.pop(key, None)

    threading.Thread(target=_run, name=f"hg-auto-{key[:40]}", daemon=True).start()
    return json.dumps({
        "ok": True, "started": True, "background": True, "target": target, "mode": mode,
        "message": "Đã chạy nền (tải → gộp → dọn → tạo job → xếp hàng). Tiến độ báo qua Telegram.",
    }, ensure_ascii=False)
