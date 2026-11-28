"""API Bilibili: nhận dạng link, thông tin video / multi-P / hợp tập / series, playurl DASH, chọn luồng.

Mọi hàm nhận `session` (requests.Session hoặc giả lập trong test) + `headers` (UA, Referer, Cookie).
"""
from __future__ import annotations

import re
import time
import urllib.parse

from engines.bili import wbi

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
API = "https://api.bilibili.com"

QUALITY = {127: "8K", 126: "Dolby Vision", 125: "HDR", 120: "4K", 116: "1080P60", 112: "1080P+", 80: "1080P",
           74: "720P60", 64: "720P", 32: "480P", 16: "360P"}
CODEC_AVC, CODEC_HEVC, CODEC_AV1 = 7, 12, 13


class BiliError(RuntimeError):
    pass


def headers(cookie: str = "") -> dict:
    h = {"User-Agent": UA, "Referer": "https://www.bilibili.com", "Origin": "https://www.bilibili.com"}
    if cookie:
        h["Cookie"] = cookie
    return h


def _json(session, url: str, hdr: dict, params: dict | None = None, timeout: float = 12) -> dict:
    r = session.get(url, params=params, headers=hdr, timeout=timeout)
    try:
        d = r.json()
    except ValueError as e:
        raise BiliError(f"Bilibili trả không phải JSON (HTTP {r.status_code}) cho {url}") from e
    if d.get("code") != 0:
        raise BiliError(f"Bilibili lỗi {d.get('code')}: {d.get('message') or d.get('msg')} ({url.rsplit('/', 1)[-1]})")
    return d.get("data") or {}


# ── nhận dạng link ──
def is_bilibili(url: str) -> bool:
    u = (url or "").lower()
    return "bilibili.com" in u or "b23.tv" in u or bool(re.fullmatch(r"\s*bv[0-9a-z]{10}\s*", u))


def resolve_short(url: str, session) -> str:
    if "b23.tv" in url:
        try:
            return session.get(url, allow_redirects=True, timeout=8, headers={"User-Agent": UA}).url
        except Exception as e:  # noqa: BLE001
            raise BiliError(f"Không mở được link rút gọn {url}: {e}") from e
    return url


def parse_link(url: str) -> dict:
    """→ {type: video|collection|series|unknown, ...}. video có thể kèm p (tập trong multi-P)."""
    s = (url or "").strip()
    m = re.search(r"space\.bilibili\.com/(\d+)/channel/collectiondetail\?.*?sid=(\d+)", s) or \
        re.search(r"space\.bilibili\.com/(\d+)/lists\?.*?sid=(\d+)", s)
    if m:
        return {"type": "collection", "mid": m.group(1), "season_id": m.group(2)}
    m = re.search(r"space\.bilibili\.com/(\d+)/lists/(\d+)(?:\?.*?type=(season|series))?", s)
    if m:
        kind = "series" if m.group(3) == "series" else "collection"
        return {"type": kind, "mid": m.group(1), ("series_id" if kind == "series" else "season_id"): m.group(2)}
    m = re.search(r"space\.bilibili\.com/(\d+)/channel/seriesdetail\?.*?(?:sid|series_id)=(\d+)", s)
    if m:
        return {"type": "series", "mid": m.group(1), "series_id": m.group(2)}
    m = re.search(r"(BV[0-9A-Za-z]{10})", s)
    if m:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(s).query)
        try:
            p = int((q.get("p") or ["0"])[0])
        except ValueError:
            p = 0
        return {"type": "video", "bvid": m.group(1), "p": p if p > 0 else None}
    return {"type": "unknown", "raw": s}


def video_url(bvid: str, p: int | None = None) -> str:
    return f"https://www.bilibili.com/video/{bvid}" + (f"?p={p}" if p and p > 1 else "")


# ── WBI ──
_WBI: dict = {"at": 0.0, "key": ""}


def mixin_key(session, hdr: dict) -> str:
    if time.time() - _WBI["at"] < 600 and _WBI["key"]:
        return _WBI["key"]
    r = session.get(f"{API}/x/web-interface/nav", headers=hdr, timeout=10)
    d = (r.json() or {}).get("data") or {}
    img = (d.get("wbi_img") or {})
    try:
        raw = img["img_url"].rsplit("/", 1)[1].split(".")[0] + img["sub_url"].rsplit("/", 1)[1].split(".")[0]
    except (KeyError, IndexError) as e:
        raise BiliError("Không lấy được khoá WBI từ Bilibili") from e
    _WBI.update(at=time.time(), key=wbi.derive_mixin_key(raw))
    return _WBI["key"]


def nav(session, hdr: dict) -> dict:
    """Trạng thái đăng nhập: {is_login, uname, vip} (không ném lỗi khi chưa đăng nhập)."""
    r = session.get(f"{API}/x/web-interface/nav", headers=hdr, timeout=8)
    d = (r.json() or {}).get("data") or {}
    vip = (d.get("vip_label") or {}).get("text") or ("VIP" if d.get("vipStatus") == 1 else "")
    return {"is_login": bool(d.get("isLogin")), "uname": d.get("uname") or "", "mid": d.get("mid") or 0, "vip": vip}


# ── thông tin ──
def video_info(bvid: str, session, hdr: dict) -> dict:
    d = _json(session, f"{API}/x/web-interface/view", hdr, {"bvid": bvid})
    pages = d.get("pages") or []
    return {"bvid": bvid, "title": d.get("title") or bvid, "pic": d.get("pic") or "",
            "owner": (d.get("owner") or {}).get("name") or "", "duration": d.get("duration") or 0,
            "desc": d.get("desc") or "", "pubdate": d.get("pubdate") or 0,
            "pages": [{"page": p.get("page") or i + 1, "cid": p.get("cid"), "part": p.get("part") or "",
                       "duration": p.get("duration") or 0} for i, p in enumerate(pages)]}


def _paged(fetch, total_key) -> tuple[list, dict]:
    items, page, meta = [], 1, {}
    while True:
        d = fetch(page)
        meta = d
        arc = d.get("archives") or []
        items.extend(arc)
        total = total_key(d)
        if not arc or (total and len(items) >= total) or len(arc) < 30 or page > 200:
            return items, meta
        page += 1


def collection(mid: str, season_id: str, session, hdr: dict) -> dict:
    key = mixin_key(session, hdr)

    def fetch(pg):
        return _json(session, f"{API}/x/polymer/web-space/seasons_archives_list", hdr,
                     wbi.sign({"mid": mid, "season_id": season_id, "page_num": pg, "page_size": 30}, key))
    items, meta = _paged(fetch, lambda d: (d.get("meta") or {}).get("total") or (d.get("page") or {}).get("total") or 0)
    return {"title": (meta.get("meta") or {}).get("name") or f"Hợp tập {season_id}",
            "items": [{"bvid": a.get("bvid"), "title": a.get("title") or a.get("bvid"), "duration": a.get("duration") or 0,
                       "pic": a.get("pic") or ""} for a in items if a.get("bvid")]}


def series(mid: str, series_id: str, session, hdr: dict) -> dict:
    def fetch(pg):
        return _json(session, f"{API}/x/series/archives", hdr, {"mid": mid, "series_id": series_id, "pn": pg, "ps": 30})
    items, _ = _paged(fetch, lambda d: (d.get("page") or {}).get("total") or 0)
    title = f"Series {series_id}"
    try:
        title = (_json(session, f"{API}/x/series/series", hdr, {"series_id": series_id}).get("meta") or {}).get("name") or title
    except BiliError as e:
        print(f"[bili] không lấy được tên series {series_id}: {e}", flush=True)
    return {"title": title, "items": [{"bvid": a.get("bvid"), "title": a.get("title") or a.get("bvid"),
                                       "duration": a.get("duration") or 0, "pic": a.get("pic") or ""}
                                      for a in items if a.get("bvid")]}


def expand(url: str, session, hdr: dict) -> dict:
    """Link → {kind, title, parts:[{url, title, duration}]} — 1 video thường = 1 phần; multi-P = mỗi P một phần."""
    url = resolve_short(url, session)
    info = parse_link(url)
    if info["type"] == "collection":
        c = collection(info["mid"], info["season_id"], session, hdr)
        return {"kind": "collection", "title": c["title"],
                "parts": [{"url": video_url(x["bvid"]), "title": x["title"], "duration": x["duration"]} for x in c["items"]]}
    if info["type"] == "series":
        c = series(info["mid"], info["series_id"], session, hdr)
        return {"kind": "series", "title": c["title"],
                "parts": [{"url": video_url(x["bvid"]), "title": x["title"], "duration": x["duration"]} for x in c["items"]]}
    if info["type"] == "video":
        v = video_info(info["bvid"], session, hdr)
        pages = v["pages"]
        if info.get("p"):                                   # link chỉ đúng 1 tập
            pages = [p for p in pages if p["page"] == info["p"]] or pages[:1]
        if len(pages) <= 1:
            pg = pages[0] if pages else {"page": 1, "part": "", "duration": v["duration"]}
            return {"kind": "video", "title": v["title"], "pic": v["pic"],
                    "parts": [{"url": video_url(v["bvid"], pg["page"] if info.get("p") else None),
                               "title": v["title"] if len(v["pages"]) <= 1 else f"{v['title']} - {pg['part']}",
                               "duration": pg["duration"] or v["duration"]}]}
        return {"kind": "multi_p", "title": v["title"], "pic": v["pic"],
                "parts": [{"url": video_url(v["bvid"], p["page"]), "title": p["part"] or f"P{p['page']}",
                           "duration": p["duration"]} for p in pages]}
    raise BiliError(f"Không nhận ra link Bilibili: {url}")


def playurl(bvid: str, cid: int, session, hdr: dict, qn: int = 127) -> dict:
    key = mixin_key(session, hdr)
    d = _json(session, f"{API}/x/player/wbi/playurl", hdr,
              wbi.sign({"bvid": bvid, "cid": cid, "qn": qn, "fnval": 4048, "fnver": 0, "fourk": 1}, key))
    if not d.get("dash"):
        raise BiliError("Bilibili không trả luồng DASH (video bị giới hạn vùng / cần đăng nhập?)")
    return d


def pick_streams(dash: dict, max_height: int | None = None, prefer_hevc: bool = False) -> tuple[dict, dict]:
    """Chọn (video, audio). Video: cao nhất ≤ max_height; cùng độ phân giải ưu tiên AVC (hoặc HEVC nếu prefer_hevc),
    tránh AV1 (khó xử lý / render chậm) trừ khi chỉ còn AV1. Audio: bitrate cao nhất (luồng thường; FLAC nếu chỉ có nó)."""
    vids = [v for v in dash.get("video") or [] if v.get("baseUrl") or v.get("base_url")]
    if not vids:
        raise BiliError("Không có luồng hình")
    if max_height:
        ok = [v for v in vids if (v.get("height") or 0) <= max_height]
        vids = ok or sorted(vids, key=lambda v: v.get("height") or 0)[:1]
    order = {CODEC_HEVC: 0, CODEC_AVC: 1} if prefer_hevc else {CODEC_AVC: 0, CODEC_HEVC: 1}
    vids.sort(key=lambda v: (-(v.get("height") or 0), order.get(v.get("codecid"), 5), -(v.get("bandwidth") or 0)))
    video = vids[0]
    auds = list(dash.get("audio") or [])
    if not auds and (dash.get("flac") or {}).get("audio"):
        auds = [dash["flac"]["audio"]]
    if not auds:
        raise BiliError("Không có luồng tiếng")
    audio = max(auds, key=lambda a: a.get("bandwidth") or 0)
    return video, audio


def stream_urls(s: dict) -> list[str]:
    return [u for u in [s.get("baseUrl") or s.get("base_url")] + list(s.get("backupUrl") or s.get("backup_url") or []) if u]
