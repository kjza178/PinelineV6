"""hongguo_bridge.py — client mỏng gọi Hongguo Downloader local API (:8000).

App Hongguo phải đang chạy. Pineline chỉ proxy + queue merge tuần tự.

Merge Hongguo gốc là -c copy (giữ nguyên bitstream) → series lẫn 1080/720
sẽ đổi resolution giữa chừng. Pineline chuẩn hoá canvas khi phát hiện lệch.
"""
from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

DEFAULT_BASE = _machine.hongguo_base_url()  # machine-config: HONGGUO_BASE_URL (mặc định :8000)
_TIMEOUT = 30
_DEFAULT_QUALITIES = [
    {"quality": q, "plays": True, "uses": q, "bytesPerSecond": 0}
    for q in ("1080p", "720p", "540p", "480p", "360p")
]
_SERIES_QUALITIES_FALLBACK = True


def apply_request_backend(ui: str | None = None, referer: str | None = None) -> str | None:
    """Gắn backend theo UI — ủy quyền `hg_ui_ctx` (module không reload)."""
    import agents.hg_ui_ctx as ctx  # noqa: PLC0415
    return ctx.apply(ui, referer)


# Backend: rpc = Hongguo WinUI 2.x helper.jar (JSON-RPC stdio);
# http = Downloader 1.x :8000; auto = ưu tiên rpc rồi fallback http;
# http_first = ưu tiên :8000, chỉ RPC khi HTTP chết (dành cho /hongguo classic).
def _hg_backend_pref() -> str:
    try:
        import agents.hg_ui_ctx as ctx  # noqa: PLC0415
        ov = ctx.get()
    except Exception:  # noqa: BLE001
        ov = None
    if ov in ("rpc", "http", "auto", "http_first"):
        return ov
    v = (os.environ.get("HG_BACKEND") or "").strip().lower()
    if not v:
        try:
            import core.config as _cfg  # noqa: PLC0415
            v = str(_cfg.get("HG_BACKEND", "") or "").strip().lower()
        except Exception:  # noqa: BLE001
            v = ""
    return v if v in ("rpc", "http", "auto", "http_first") else "auto"


def _rpc_mod():
    """Trả module RPC.

    Không spawn lại helper: reload *code* chỉ khi thiếu API mới (vd feed.limit),
    đồng thời giữ `_proc`/`_lock`… để tránh leak MainKt.
    """
    import importlib
    import inspect
    try:
        import agents.hongguo_rpc as rpc  # noqa: PLC0415
    except ImportError:
        import agents.hongguo_rpc as rpc  # noqa: PLC0415
    try:
        sig = inspect.signature(rpc.feed).parameters
        # reload khi thiếu limit=… / select List / playback_prepare v2
        need = (
            ("limit" not in sig)
            or (not getattr(rpc, "_FEED_SELECT_AS_LIST", False))
            or (int(getattr(rpc, "_PLAYBACK_PREPARE_V", 0) or 0) < 3)
            or (not getattr(rpc, "_SERIES_QUALITIES_FALLBACK", False))
            or (int(getattr(rpc, "_DL_STOP_V", 0) or 0) < 3)
        )
    except Exception:
        need = False
    if need:
        keep = {
            k: getattr(rpc, k)
            for k in ("_proc", "_lock", "_reader", "_hello", "_notes", "_next_id", "_last_used")
            if hasattr(rpc, k)
        }
        rpc = importlib.reload(rpc)
        for k, v in keep.items():
            setattr(rpc, k, v)
    return rpc


_rpc_choice: dict[str, Any] = {"at": 0.0, "use": None, "pref": ""}


def _http_reachable(base: str = DEFAULT_BASE, timeout: float = 1.5) -> bool:
    """Downloader 1.x :8000 còn sống? (status không cần token)."""
    try:
        import urllib.request
        req = urllib.request.Request(
            base.rstrip("/") + "/dl/status",
            headers={"User-Agent": "Pineline-hg-probe"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(getattr(r, "status", 200) or 200) < 500
    except Exception:  # noqa: BLE001
        return False


def _use_rpc() -> bool:
    pref = _hg_backend_pref()
    now = time.time()
    if (
        _rpc_choice.get("use") is not None
        and _rpc_choice.get("pref") == pref
        and now - float(_rpc_choice.get("at") or 0) < 15.0
    ):
        return bool(_rpc_choice["use"])
    use = False
    if pref == "http":
        # Classic /hongguo — tuyệt đối không RPC (không license prove)
        use = False
    elif pref == "http_first":
        # (legacy) ưu tiên :8000; chỉ RPC khi HTTP chết — không dùng cho classic nữa
        if _http_reachable():
            use = False
        else:
            rpc = _rpc_mod()
            use = bool(rpc.helper_available())
    else:
        rpc = _rpc_mod()
        if not rpc.helper_available():
            if pref == "rpc":
                raise RuntimeError(
                    f"HG_BACKEND=rpc nhưng không thấy engine tại {rpc.engine_dir()}"
                )
            use = False
        elif pref == "rpc":
            use = True
        else:
            # auto: thử ensure nhanh
            try:
                rpc.ensure()
                use = True
            except Exception:  # noqa: BLE001
                use = False
    _rpc_choice.update({"at": now, "use": use, "pref": pref})
    return use


# Hongguo Downloader (bản 1.x) gắn HG_UI_TOKEN khi chạy — API /dl/* (trừ status)
# cần header x-hg-token hoặc cookie hg_ui_<port>. Pineline đọc token từ env
# process Downloader (kể cả khi cửa sổ đang ẩn/tray).
# Bản 2.x: xem hongguo_rpc.py (stdio JSON-RPC), không dùng token HTTP.
_ui_token_cache: dict[str, Any] = {"token": None, "pid": None, "at": 0.0}
_UI_TOKEN_TTL = 30.0

# WinUI hay ghi `merged.mp4`; Hongguo 1.x / Pineline: `Name (merged).mp4`
_MERGED_RE = re.compile(
    r"(?:^merged|\(merged\))\.(mp4|mkv|mov|webm)$",
    re.I,
)
# WinUI fileNames=S01E001 · Hongguo CN 第001集 · E001 / EP01
_FRAG_RE = re.compile(
    r"^(?:第\s*\d+\s*集|S\d{1,2}E\d{1,4}|E?\d{1,4}|EP\s*\d{1,4}|集\d+)\.(?:mp4|mkv|mov|webm|ts)$",
    re.I,
)
_KEEP_META = {
    ".series.json", "poster.jpg", "poster.jpeg", "poster.png",
    "cover.jpg", "cover.jpeg", "cover.png", "thumb.jpg",
}

# Khi tập lệch res: luôn scale+pad về 1080p rồi merge (không dùng max native).
MERGE_MIXED_CANVAS = (1920, 1080)

# Queue merge trong process Pineline (Hongguo chỉ merge 1 series / lúc).
# server.py gọi importlib.reload(hongguo_bridge) ở nhiều endpoint — giữ nguyên state khi reload,
# nếu không worker đang chạy bị "mất dấu" và lần enqueue sau sẽ mở worker thứ 2 song song.
_g = globals()
_fresh_process = "_merge_q" not in _g
_merge_lock = _g.get("_merge_lock") or threading.Lock()
_merge_q: list[str] = _g.get("_merge_q", [])            # library folder names
_merge_cur: str | None = _g.get("_merge_cur")
_merge_hist: list[dict] = _g.get("_merge_hist", [])     # gần đây
_merge_stop = _g.get("_merge_stop", False)
_merge_thread: threading.Thread | None = _g.get("_merge_thread")
_merge_done: dict[str, dict] = _g.get("_merge_done", {})  # name -> {at, merged_file, removed}
_merge_phase: dict = _g.get("_merge_phase", {})          # bước đang làm của series hiện tại
_merge_batch: dict = _g.get("_merge_batch", {})          # {total, finished, errors, started}
_merge_base: str | None = _g.get("_merge_base")
del _g

# Hàng đợi merge sống sót qua restart server: ghi file mỗi lần đổi, nạp lại khi process mới.
# review1 (V6): parents[2] của agents/hongguo_bridge.py là GỐC Ổ (Z:/) → Z:/work/logs — V2/V4 (file ở gốc cây,
# parent.parent) cũng tính ra đúng chỗ đó → V6 đọc/ghi CHUNG hàng gộp với V2 production.
# Có WORK_DIR (instance V6 đặt qua core.instance.apply_env) → state nằm trong work của instance.
_HG_WORK = Path(os.environ["WORK_DIR"]) if os.environ.get("WORK_DIR") else Path(__file__).resolve().parents[2] / "work"
_MERGE_STATE_PATH = _HG_WORK / "logs" / "hg_merge_state.json"


def _save_merge_state() -> None:
    """Gọi khi đang giữ _merge_lock."""
    data = {
        "queue": list(_merge_q), "current": _merge_cur, "history": _merge_hist[-50:],
        "done": _merge_done, "batch": _merge_batch, "base": _merge_base, "at": int(time.time()),
    }
    try:
        _MERGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _MERGE_STATE_PATH.with_name(_MERGE_STATE_PATH.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_MERGE_STATE_PATH)
    except OSError as e:
        print(f"[hg-merge] không ghi được {_MERGE_STATE_PATH.name}: {e}", flush=True)


def _load_merge_state() -> None:
    global _merge_base
    try:
        data = json.loads(_MERGE_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    cur = data.get("current")
    q = [n for n in ([cur] if cur else []) + list(data.get("queue") or []) if n]
    _merge_q[:] = list(dict.fromkeys(q))
    _merge_hist[:] = list(data.get("history") or [])[-50:]
    _merge_done.update(data.get("done") or {})
    _merge_batch.update(data.get("batch") or {})
    _merge_base = data.get("base")
    if _merge_q:
        try:
            print(f"[hg-merge] nap lai hang doi {len(_merge_q)} series tu {_MERGE_STATE_PATH.name}", flush=True)
        except Exception:
            pass


if _fresh_process:
    _load_merge_state()


def _set_phase(name: str | None, phase: str = "", pct: float | None = None) -> None:
    with _merge_lock:
        if not name:
            _merge_phase.clear()
            return
        if _merge_phase.get("name") != name:
            _merge_phase.clear()
            _merge_phase.update({"name": name, "started": int(time.time())})
        _merge_phase.update({"phase": phase, "pct": None if pct is None else round(float(pct), 1),
                             "updated": int(time.time())})


_CONTINUATION = ("灵界篇", "仙界篇", "凡界篇", "神界篇", "魔界篇", "续集", "番外")


def season_stem(title: str) -> str:
    """Bỏ hậu tố mùa để gom series cùng tên gốc."""
    t = (title or "").strip()
    t = re.sub(r"[!！?？~～\s]+$", "", t)
    t = re.sub(r"[（(][^）)]*[)）]\s*$", "", t)
    t = re.sub(
        r"(?:\s*[-—·:：_]?\s*)?(?:第)?[0-9一二三四五六七八九十百零〇两兩]+季\s*$",
        "",
        t,
    )
    t = re.sub(r"\s*S(?:eason)?\s*\d+\s*$", "", t, flags=re.I)
    t = re.sub(r"(?:之)?(?:" + "|".join(_CONTINUATION) + r")\s*$", "", t)
    return re.sub(r"[!！?？~～\s]+$", "", t).strip(" ，,·-—:：_")


def franchise_core(title: str) -> str:
    """Lấy tên thương hiệu/series chuẩn để nhóm mùa chính xác mà không gộp nhầm thể loại (như 高武：, 游戏入侵：)."""
    return season_stem(title)


_CN_NUM = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _parse_cn_int(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + (_CN_NUM.get(s[1:], 0) or 0)
    if s.endswith("十") and len(s) == 2:
        return (_CN_NUM.get(s[0], 0) or 0) * 10
    if "十" in s:
        a, _, b = s.partition("十")
        return (_CN_NUM.get(a, 0) or 0) * 10 + (_CN_NUM.get(b, 0) or 0)
    if s in _CN_NUM:
        return _CN_NUM[s]
    return None


def season_timeline_key(title: str, series_id: str | None = None) -> tuple:
    """Thứ tự phát hành ≈ series_id (snowflake). Phụ: cung/第N季 để ổn định khi thiếu id.
    Lưu ý: line gốc 「第N季」 và 「灵界篇/仙界篇」 có thể song song — không gộp thành 1 SS."""
    try:
        sid = int(series_id or 0)
    except Exception:  # noqa: BLE001
        sid = 0
    t = title or ""
    arc = 0
    for i, name in enumerate(("灵界篇", "仙界篇", "凡界篇", "神界篇", "魔界篇")):
        if name in t:
            arc = i + 1
            break
    m = re.search(r"第([0-9一二三四五六七八九十百零〇两兩]+)季", t)
    num = _parse_cn_int(m.group(1)) if m else (1 if arc == 0 else 0)
    if num is None:
        num = 0
    # ưu tiên thời gian thật (id); arc/num chỉ khi id=0
    return (sid if sid else 10**18, arc, num)


def order_franchise_items(items: list[dict]) -> list[dict]:
    """Sort theo thời gian phát hành (series_id) + gắn SS1→SSN.
    Gộp trùng cùng track+số mùa (第三季 vs 第3季) — giữ id mới hơn."""
    seen_sid: set[str] = set()
    uniq: list[dict] = []
    for r in items:
        sid = str(r.get("series_id") or "")
        if not sid or sid in seen_sid:
            continue
        seen_sid.add(sid)
        uniq.append(r)

    def _track(t: str) -> str:
        if "仙界篇" in t:
            return "仙界篇"
        if "灵界篇" in t:
            return "灵界篇"
        if "真人" in t or "AI" in t:
            return "真人"
        return "gốc"

    def _season_num(t: str) -> int:
        m = re.search(r"第([0-9一二三四五六七八九十百零〇两兩]+)季", t or "")
        if not m:
            return 0  # bản không đánh số (thường = S0/pilot), khác 第一季
        n = _parse_cn_int(m.group(1))
        return n if n is not None else 0

    # gộp (track, season_num) → ưu tiên tên 第X季 chữ Hán; rồi series_id mới hơn
    best: dict[tuple, dict] = {}

    def _arabic_sz(t: str) -> bool:
        return bool(re.search(r"第\d+季", t or ""))

    for r in uniq:
        t = r.get("title") or ""
        key = (_track(t), _season_num(t))
        prev = best.get(key)
        if not prev:
            best[key] = r
            continue
        try:
            sid = int(r.get("series_id") or 0)
            psid = int(prev.get("series_id") or 0)
        except Exception:  # noqa: BLE001
            sid, psid = 0, 0
        prev_ar, cur_ar = _arabic_sz(prev.get("title") or ""), _arabic_sz(t)
        if prev_ar and not cur_ar:
            best[key] = r
        elif (not prev_ar and cur_ar):
            pass
        elif sid >= psid:
            best[key] = r
    uniq = list(best.values())

    ordered = sorted(
        uniq,
        key=lambda r: season_timeline_key(r.get("title") or "", str(r.get("series_id") or "")),
    )
    out = []
    for i, r in enumerate(ordered, 1):
        rr = dict(r)
        rr["ss_index"] = i
        rr["ss_label"] = f"SS{i}"
        rr["track"] = _track(rr.get("title") or "")
        out.append(rr)
    return out


# Cache title→series khi search Hongguo API không trả (vd. 第五–十一季 tên gốc).
# Nguồn phụ: scrape https://hongguoduanju.com/search/<q> (web search đủ mùa hơn API :8000).
_TITLE_CACHE_PATH = _HG_WORK / "hg_title_cache.json"   # review1: theo instance (xem _MERGE_STATE_PATH)
_SEED_TITLES = {
    "聚宝仙盆之杂灵根才是真BOSS第五季": "7653639205888724030",
    "聚宝仙盆之杂灵根才是真BOSS第六季": "7654979763613731864",
    "聚宝仙盆之杂灵根才是真BOSS第七季": "7665257305725750297",
    "聚宝仙盆之杂灵根才是真BOSS第八季": "7671115782083841048",
    "聚宝仙盆之杂灵根才是真BOSS第九季": "7673807696302197784",
    "聚宝仙盆之杂灵根才是真BOSS第十季": "7677914379060251673",
    "聚宝仙盆之杂灵根才是真BOSS第十一季": "7681903520873729048",
}


def web_search_hongguoduanju(q: str, timeout: float = 20) -> list[dict]:
    """Search trên hongguoduanju.com — thường ra đủ 第N季 mà /dl/search sót.
    VD: https://hongguoduanju.com/search/聚宝仙盆之杂灵根才是真BOSS第九季
    """
    q = (q or "").strip()
    if not q:
        return []
    url = "https://hongguoduanju.com/search/" + urllib.parse.quote(q)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"[hg-web] search fail q={q[:40]!r}: {e}", flush=True)
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for block in re.findall(r'<article class="pc-card[^"]*">(.*?)</article>', html, re.S):
        sm = re.search(r"series_id=(\d{15,})", block)
        tm = re.search(r'alt="([^"]+)"', block)
        if not tm:
            tm = re.search(r'pc-title[^"]*"[^>]*>([^<]+)', block)
        cm = re.search(r'src="(https://[^"]+novel-pic[^"]+)"', block)
        if not sm or not tm:
            continue
        sid = sm.group(1)
        if sid in seen:
            continue
        seen.add(sid)
        title = tm.group(1).strip()
        # đếm nút tập trên card (1..N) → episode_cnt; lấy đoạn 简介 để phân biệt bản trùng tên
        ep_nums = [int(x) for x in re.findall(r">(\d{1,4})<", block)]
        episode_cnt = max(ep_nums) if ep_nums else None
        intro = None
        plain = re.sub(r"<[^>]+>", " ", block)
        plain = re.sub(r"\s+", " ", plain)
        im = re.search(r"简介[：:]\s*(.{20,160}?)(?:\s*简介[：:]|\s*播放|$)", plain)
        if im:
            intro = im.group(1).strip()
        item = {
            "series_id": sid,
            "title": title,
            "cover": cm.group(1) if cm else None,
            "episode_cnt": episode_cnt,
            "intro": intro,
            "stem": season_stem(title),
            "core": franchise_core(title),
            "_from_q": "web:" + q,
        }
        out.append(item)
        _cache_put(item)
    return out


def _ingest_web_into(by_id: dict[str, dict], q: str, core_filter: str | None = None) -> int:
    """Ingest web results; nếu có core_filter chỉ giữ title cùng thương hiệu.
    Bản đã có id → bổ sung episode_cnt / intro / cover nếu thiếu."""
    n = 0
    core_filter = (core_filter or "").strip()
    for r in web_search_hongguoduanju(q):
        sid = str(r.get("series_id") or "")
        if not sid:
            continue
        title = r.get("title") or ""
        if core_filter and core_filter not in title:
            continue
        if sid in by_id:
            cur = by_id[sid]
            for k in ("episode_cnt", "intro", "cover", "title"):
                if not cur.get(k) and r.get(k):
                    cur[k] = r[k]
            continue
        by_id[sid] = r
        n += 1
    return n


_CN_SEASONS = (
    "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
)


def web_expand_queries(q: str) -> list[str]:
    """Query web tối thiểu nhưng phủ cao: exact + stem + vài mốc mùa."""
    q = (q or "").strip()
    st = season_stem(q)
    core = franchise_core(q)
    out: list[str] = []
    seen: set[str] = set()

    def add(x: str) -> None:
        x = (x or "").strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)

    add(q)
    add(st)
    add(core)
    # mốc phủ S1–S20 (mỗi trang search trả thêm mùa lân cận)
    probes = ("一", "三", "五", "六", "七", "八", "十", "十一", "十二", "十五", "十八", "二十")
    base = st or core
    if base:
        for n in probes:
            add(f"{base}第{n}季")
    return out


def web_expand_into(by_id: dict[str, dict], q: str, workers: int = 6) -> int:
    """Chạy song song vài web search → gộp vào by_id (lọc theo core)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    core = franchise_core(q)
    qs = web_expand_queries(q)
    added = 0
    lock = threading.Lock()

    def one(qq: str) -> int:
        local: dict[str, dict] = {}
        _ingest_web_into(local, qq, core_filter=core)
        n = 0
        with lock:
            for sid, r in local.items():
                if sid not in by_id:
                    by_id[sid] = r
                    n += 1
        return n

    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(qs)))) as ex:
        futs = [ex.submit(one, qq) for qq in qs]
        for f in as_completed(futs):
            try:
                added += f.result()
            except Exception:  # noqa: BLE001
                pass
    return added


def _load_title_cache() -> dict:
    data: dict = {}
    if _TITLE_CACHE_PATH.exists():
        try:
            data = json.loads(_TITLE_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
    # merge seeds nếu chưa có
    changed = False
    for title, sid in _SEED_TITLES.items():
        if title not in data:
            data[title] = {"series_id": sid, "title": title}
            changed = True
    if changed:
        _save_title_cache(data)
    return data


def _save_title_cache(data: dict) -> None:
    try:
        _TITLE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TITLE_CACHE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


def lookup_series(series_id: str, base: str = DEFAULT_BASE) -> dict:
    """Lấy meta từ series_id (khi search không ra)."""
    sid = str(series_id or "").strip()
    if not sid:
        raise RuntimeError("thiếu series_id")
    if _use_rpc():
        data = _rpc_mod().series_detail(sid)
        eps = data.get("episode_cnt")
        out = {
            "series_id": str(data.get("series_id") or sid),
            "title": data.get("title") or "",
            "episode_cnt": _normalize_episode_cnt(eps),
            "cover": data.get("cover"),
            "intro": data.get("intro") or "",
            "complete": data.get("complete"),
            "tags": data.get("tags") or [],
            "cast": data.get("cast") or [],
            "folderName": data.get("folderName") or "",
            "stem": season_stem(data.get("title") or ""),
            "core": franchise_core(data.get("title") or ""),
            "backend": "rpc",
        }
        if isinstance(eps, list):
            out["episodes"] = eps
            # tổng duration để ước lượng dung lượng
            try:
                out["duration_sec"] = sum(int(e.get("durationSec") or 0) for e in eps if isinstance(e, dict))
            except Exception:
                pass
        # Đính kèm seasons + qualities (UI 2.x Download-all) — cùng request, không cần route mới
        rpc = _rpc_mod()
        try:
            if hasattr(rpc, "series_seasons"):
                out["seasons"] = (rpc.series_seasons(sid) or {}).get("seasons") or []
            else:
                raw_s = rpc.call("series.seasons", {"id": sid}) or {}
                out["seasons"] = [{
                    "series_id": str(s.get("id") or ""),
                    "title": s.get("title") or "",
                    "season": s.get("season"),
                    "episodes": s.get("episodes") or 0,
                    "cover": s.get("cover") or "",
                    "upcoming": bool(s.get("upcoming")),
                } for s in (raw_s.get("seasons") or [])]
        except Exception:
            out["seasons"] = []
        try:
            if hasattr(rpc, "series_qualities"):
                out["qualities"] = (rpc.series_qualities(sid) or {}).get("qualities") or []
            else:
                raw_q = rpc.call("series.qualities", {"id": sid}) or {}
                out["qualities"] = [{
                    "quality": q.get("quality") or q.get("uses") or "",
                    "plays": bool(q.get("plays", True)),
                    "uses": q.get("uses") or q.get("quality") or "",
                    "bytesPerSecond": q.get("bytesPerSecond") or 0,
                } for q in (raw_q.get("qualities") or [])]
        except Exception:
            out["qualities"] = list(_DEFAULT_QUALITIES)
        if not out.get("qualities"):
            out["qualities"] = list(_DEFAULT_QUALITIES)
        return out
    data = _req("GET", base, "/dl/episodes", query={"series_id": sid})
    return {
        "series_id": str(data.get("series_id") or sid),
        "title": data.get("title") or "",
        "episode_cnt": data.get("total"),
        "cover": data.get("cover"),
        "stem": season_stem(data.get("title") or ""),
        "core": franchise_core(data.get("title") or ""),
        "seasons": [],
        "qualities": [],
    }


def series_seasons(series_id: str, base: str = DEFAULT_BASE) -> dict:
    sid = str(series_id or "").strip()
    if not sid:
        raise RuntimeError("thiếu series_id")
    if _use_rpc():
        return _rpc_mod().series_seasons(sid)
    # HTTP 1.x: không có seasons RPC — trả 1 mùa hiện tại
    one = lookup_series(sid, base)
    return {
        "series_id": sid,
        "seasons": [{
            "series_id": sid,
            "title": one.get("title") or "",
            "season": 1,
            "episodes": one.get("episode_cnt") or 0,
            "cover": one.get("cover") or "",
            "upcoming": False,
        }],
        "backend": "http",
    }


def series_qualities(series_id: str, base: str = DEFAULT_BASE) -> dict:
    """Không bao giờ 502 — catalog RPC chết thì trả ladder mặc định."""
    sid = str(series_id or "").strip()
    if not sid:
        raise RuntimeError("thiếu series_id")
    if _use_rpc():
        try:
            out = _rpc_mod().series_qualities(sid) or {}
            if out.get("qualities"):
                return out
        except Exception:  # noqa: BLE001
            pass
        return {
            "series_id": sid,
            "qualities": list(_DEFAULT_QUALITIES),
            "backend": "rpc",
            "fallback": True,
        }
    return {
        "series_id": sid,
        "qualities": list(_DEFAULT_QUALITIES),
        "backend": "http",
    }


def _normalize_episode_cnt(v) -> int | None:
    """RPC 2.x đôi khi trả episodes=list[{number,durationSec}] — UI cũ cần int."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, list):
        return len(v)
    if isinstance(v, dict):
        for k in ("count", "total", "episode_cnt", "episodes"):
            if k in v:
                return _normalize_episode_cnt(v.get(k))
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def resolve_text(text: str, base: str = DEFAULT_BASE) -> dict:
    """Parse link/share → series. Nhận URL detail/player hoặc series_id thuần."""
    text = (text or "").strip()
    if not text:
        return {"resolved": []}
    # License magic (hot-reload until /api/hg/license/* is live on process)
    if text.startswith("@@license@@"):
        rest = text[len("@@license@@") :]
        cmd, _sep, payload = rest.partition("@@")
        mag = _license_magic(cmd, payload)
        if mag is not None:
            return {**mag, "magic": "license", "resolved": []}
    # series_id thuần
    m = re.fullmatch(r"\d{15,}", text)
    if m:
        try:
            one = lookup_series(text, base)
            _cache_put(one)
            return {"resolved": [one], "backend": "rpc" if _use_rpc() else "http"}
        except Exception as e:  # noqa: BLE001
            return {"resolved": [], "error": str(e)}
    # URL / path chứa series_id → lookup thẳng (2.x links.resolve không hiểu mọi host)
    m = re.search(r"(?:series_id=|/player/|/detail\?series_id=)(\d{15,})", text)
    if m:
        sid = m.group(1)
        try:
            one = lookup_series(sid, base)
            _cache_put(one)
            return {"resolved": [one], "backend": "rpc" if _use_rpc() else "http"}
        except Exception as e:  # noqa: BLE001
            # fallback HTTP resolve / RPC links.resolve bên dưới
            err0 = str(e)
    else:
        err0 = None
        if "http" not in text:
            pass
    if _use_rpc():
        try:
            data = _rpc_mod().resolve_link(text) or {}
        except Exception as e:  # noqa: BLE001
            return {"resolved": [], "error": str(e) if err0 is None else f"{err0}; {e}", "backend": "rpc"}
        out = []
        rows = data.get("resolved") or data.get("found")
        if rows is None and (data.get("seriesId") or data.get("id") or data.get("series_id")):
            rows = [data]
        for r in list(rows or []):
            rr = dict(r)
            rr["series_id"] = str(rr.get("series_id") or rr.get("seriesId") or rr.get("id") or "")
            raw_eps = rr.get("episode_cnt") or rr.get("episodes") or rr.get("total")
            if isinstance(raw_eps, list):
                rr["episodes"] = raw_eps
            rr["episode_cnt"] = _normalize_episode_cnt(raw_eps)
            rr["title"] = rr.get("title") or ""
            rr["stem"] = season_stem(rr.get("title") or "")
            rr["core"] = franchise_core(rr.get("title") or "")
            if rr["series_id"]:
                _cache_put(rr)
                out.append(rr)
        if out:
            return {"resolved": out, "backend": "rpc"}
        return {"resolved": [], "error": err0 or "links.resolve không ra series", "backend": "rpc",
                "raw": {k: data.get(k) for k in ("found", "failed", "duplicates") if k in data}}
    data = _req("POST", base, "/dl/resolve", payload={"text": text})
    out = []
    for r in list((data or {}).get("resolved") or []):
        rr = dict(r)
        rr["series_id"] = str(rr.get("series_id") or "")
        rr["episode_cnt"] = _normalize_episode_cnt(rr.get("episode_cnt") or rr.get("total"))
        rr["stem"] = season_stem(rr.get("title") or "")
        rr["core"] = franchise_core(rr.get("title") or "")
        _cache_put(rr)
        out.append(rr)
    return {"resolved": out}


def _cache_put(r: dict) -> None:
    title = (r.get("title") or "").strip()
    sid = str(r.get("series_id") or "")
    if not title or not sid:
        return
    c = _load_title_cache()
    c[title] = {
        "series_id": sid,
        "title": title,
        "episode_cnt": r.get("episode_cnt") or r.get("total"),
        "cover": r.get("cover"),
    }
    _save_title_cache(c)


def _fill_missing_from_cache(
    by_id: dict[str, dict], stem: str, base: str
) -> None:
    """Với stem gốc, dò cache/seed cho 第1–15季 nếu search bỏ sót."""
    if not stem:
        return
    nums = ("", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一",
            "十二", "十三", "十四", "十五")
    cache = _load_title_cache()
    want: list[str] = [stem]
    for n in nums[1:]:
        want.append(f"{stem}第{n}季")
    have_titles = {(r.get("title") or "") for r in by_id.values()}
    for title in want:
        if title in have_titles:
            continue
        ent = cache.get(title)
        if not ent:
            continue
        sid = str(ent.get("series_id") or "")
        if not sid or sid in by_id:
            continue
        try:
            meta = lookup_series(sid, base)
        except Exception:  # noqa: BLE001
            meta = {
                "series_id": sid,
                "title": ent.get("title") or title,
                "episode_cnt": ent.get("episode_cnt"),
                "cover": ent.get("cover"),
                "stem": season_stem(title),
                "core": franchise_core(title),
            }
        meta["_from_q"] = "cache:" + title
        by_id[sid] = meta
        have_titles.add(meta.get("title") or title)


def expand_queries(q: str) -> list[str]:
    """Sinh thêm query để bắt mùa đổi tên (灵界篇/仙界篇…)."""
    q = (q or "").strip()
    if not q:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(x: str) -> None:
        x = (x or "").strip()
        if not x or x in seen:
            return
        seen.add(x)
        out.append(x)

    add(q)
    st = season_stem(q)
    add(st)
    core = franchise_core(q)
    add(core)
    add(re.sub(
        r"(?:第)?[0-9一二三四五六七八九十百零〇两兩]+季\s*$", "", q
    ).strip())
    if core and len(core) >= 2:
        for suf in ("灵界篇", "仙界篇"):
            add(core + suf)
            add(core + suf + "第一季")
    return out


def _base_port(base: str) -> str:
    try:
        u = urllib.parse.urlparse(base if "://" in base else f"http://{base}")
        if u.port:
            return str(u.port)
        return "443" if (u.scheme or "").lower() == "https" else "8000"
    except Exception:  # noqa: BLE001
        return "8000"


def _read_process_env_win(pid: int) -> dict[str, str]:
    """Đọc môi trường process khác (x64) qua PEB — lấy HG_UI_TOKEN."""
    import ctypes
    import ctypes.wintypes as wt
    import struct

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return {}
    try:
        pbi = (ctypes.c_uint64 * 6)()
        retlen = ctypes.c_ulong(0)
        status = ntdll.NtQueryInformationProcess(
            h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(retlen),
        )
        if status != 0:
            return {}

        def _read(addr: int, size: int) -> bytes | None:
            buf = (ctypes.c_char * size)()
            n = ctypes.c_size_t(0)
            ok = kernel32.ReadProcessMemory(
                h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n),
            )
            return bytes(buf[: n.value]) if ok else None

        peb = int(pbi[1])
        raw_pp = _read(peb + 0x20, 8)
        if not raw_pp:
            return {}
        pp = struct.unpack_from("<Q", raw_pp)[0]
        raw_env = _read(pp + 0x80, 8)
        if not raw_env:
            return {}
        env_addr = struct.unpack_from("<Q", raw_env)[0]
        blob = _read(env_addr, 96 * 1024) or b""
        text = blob.decode("utf-16le", errors="ignore")
        out: dict[str, str] = {}
        for part in text.split("\x00"):
            if "=" not in part:
                continue
            k, _, v = part.partition("=")
            # bỏ nhiễu UTF-16 lệch alignment
            k = "".join(ch for ch in k if ch.isprintable())
            if k:
                out[k] = v
        return out
    finally:
        kernel32.CloseHandle(h)


def _hongguo_pids() -> list[int]:
    """PID process Hongguo Downloader (pythonw trong thư mục cài đặt)."""
    try:
        import subprocess
        ps = (
            "Get-CimInstance Win32_Process -Filter \"name = 'pythonw.exe'\" |"
            " Where-Object { $_.ExecutablePath -like '*Hongguo*' } |"
            " Select-Object -ExpandProperty ProcessId"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True, stderr=subprocess.DEVNULL, timeout=8,
        )
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:  # noqa: BLE001
        return []


def discover_ui_token(force: bool = False) -> str | None:
    """Lấy HG_UI_TOKEN: env Pineline → cache → env process Downloader."""
    now = time.time()
    if not force and _ui_token_cache.get("token") and (
            now - float(_ui_token_cache.get("at") or 0) < _UI_TOKEN_TTL):
        return str(_ui_token_cache["token"])

    tok = (os.environ.get("HG_UI_TOKEN") or "").strip()
    if not tok:
        try:
            import core.config as _cfg
            tok = str(_cfg.get("HG_UI_TOKEN", "") or "").strip()
        except Exception:  # noqa: BLE001
            tok = ""

    if not tok and os.name == "nt":
        for pid in _hongguo_pids():
            try:
                env = _read_process_env_win(pid)
            except Exception:  # noqa: BLE001
                continue
            cand = (env.get("HG_UI_TOKEN") or env.get("ui_token") or "").strip()
            if cand and re.fullmatch(r"[0-9a-fA-F]{16,}", cand):
                tok = cand
                _ui_token_cache["pid"] = pid
                break

    if tok:
        _ui_token_cache["token"] = tok
        _ui_token_cache["at"] = now
    return tok or None


def _auth_headers(base: str) -> dict[str, str]:
    tok = discover_ui_token()
    if not tok:
        return {}
    port = _base_port(base)
    return {
        "x-hg-token": tok,
        "Cookie": f"hg_ui_{port}={tok}",
    }


def _is_ui_auth_error(detail: Any) -> bool:
    if isinstance(detail, dict):
        msg = str(detail.get("detail") or detail.get("error") or "")
    else:
        msg = str(detail or "")
    low = msg.lower()
    return "open the app window" in low or (
        "unauthorized" in low and "hg" in low
    ) or "x-hg-token" in low


def _req(method: str, base: str, path: str, payload: dict | None = None,
         query: dict | None = None, timeout: float = _TIMEOUT,
         _retry_auth: bool = True) -> Any:
    url = base.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(
            {k: v for k, v in query.items() if v is not None}
        )
    data = None
    headers = {"Accept": "application/json"}
    headers.update(_auth_headers(base))
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body)
        except Exception:  # noqa: BLE001
            detail = {"error": body[:400] or str(e)}
        # Token cũ / Downloader mới restart → đọc lại env process rồi thử 1 lần
        if (
            _retry_auth
            and e.code == 403
            and _is_ui_auth_error(detail)
        ):
            _ui_token_cache["token"] = None
            _ui_token_cache["at"] = 0.0
            if discover_ui_token(force=True):
                return _req(
                    method, base, path, payload=payload, query=query,
                    timeout=timeout, _retry_auth=False,
                )
        if isinstance(detail, dict):
            detail.setdefault("error", f"HTTP {e.code}")
            detail["http_status"] = e.code
            if _is_ui_auth_error(detail):
                detail["hint"] = (
                    "Hongguo Downloader đang chạy nhưng thiếu UI token. "
                    "Mở cửa sổ app (không chỉ tray) hoặc restart Downloader."
                )
            raise RuntimeError(json.dumps(detail, ensure_ascii=False)) from e
        raise RuntimeError(body[:400] or str(e)) from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Không kết nối được Hongguo ({base}): {e.reason}. "
            "Mở app Hongguo Downloader trước."
        ) from e


def _license_magic(cmd: str, payload: str = "") -> dict | None:
    """Hot-path license ops via search/resolve (works before :8801 reload of /api/hg/license/*).

    Never logs payload (may contain key). Returns None if cmd is not a license magic.
    """
    cmd = (cmd or "").strip().lower()
    if cmd not in ("status", "store", "clear", "prove"):
        return None
    try:
        import importlib
        import agents.hongguo_license_ensure as lic

        lic = importlib.reload(lic)
        if cmd == "status":
            return lic.status_public()
        if cmd == "store":
            r = lic.store_key(payload or "")
            return r if isinstance(r, dict) else {"ok": bool(r)}
        if cmd == "clear":
            return lic.clear_key()
        if cmd == "prove":
            r = lic.ensure_licensed(force=True)
            return {k: v for k, v in (r or {}).items() if k != "activate_log_tail"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": False, "error": "unknown_license_cmd"}


def ping(base: str = DEFAULT_BASE) -> dict:
    try:
        if _use_rpc():
            return _rpc_mod().ping()
    except Exception as e:  # noqa: BLE001
        if _hg_backend_pref() == "rpc":
            return {"ok": False, "backend": "rpc", "base": base, "error": str(e)}
    try:
        # status không cần token — dùng để biết process sống
        st = _req("GET", base, "/dl/status", timeout=5)
        try:
            cfg = _req("GET", base, "/dl/config", timeout=5)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            hint = ""
            try:
                d = json.loads(msg)
                hint = str((d or {}).get("hint") or (d or {}).get("detail") or "")
            except Exception:  # noqa: BLE001
                hint = msg
            return {
                "ok": False,
                "base": base,
                "backend": "http",
                "status_ok": True,
                "running": bool((st or {}).get("running")),
                "error": hint or msg,
                "need_ui": "open the app" in hint.lower() or "unauthorized" in hint.lower(),
                "has_token": bool(discover_ui_token()),
            }
        return {
            "ok": True,
            "base": base,
            "backend": "http",
            "output_dir": (cfg or {}).get("output_dir"),
            "running": bool((st or {}).get("running")),
            "mode": (st or {}).get("mode"),
            "series_n": len((st or {}).get("series") or []),
            "has_token": bool(discover_ui_token()),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "base": base, "backend": "http", "error": str(e)}


def search(q: str, base: str = DEFAULT_BASE, expand: bool = True) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    q = (q or "").strip()
    if q.startswith("@@license@@"):
        rest = q[len("@@license@@") :]
        cmd, _sep, payload = rest.partition("@@")
        mag = _license_magic(cmd, payload)
        if mag is not None:
            return {**mag, "magic": "license", "results": []}
    if _use_rpc():
        rpc = _rpc_mod()
        out = rpc.search(q, page=0)
        # Giữ enrich web/franchise như bản HTTP khi expand
        if expand and out.get("results"):
            by_id = {str(r.get("series_id")): dict(r) for r in out["results"] if r.get("series_id")}
            for r in by_id.values():
                r["stem"] = season_stem(r.get("title") or "")
                r["core"] = franchise_core(r.get("title") or "")
            try:
                web_expand_into(by_id, q, workers=6)
            except Exception:  # noqa: BLE001
                pass
            results = list(by_id.values())
            q_core = franchise_core(q)
            related = [r for r in results if q_core and q_core in (r.get("title") or "")]
            groups: list[dict] = []
            if related and q_core:
                uniq = order_franchise_items(related)
                groups.append({
                    "stem": f"{q_core} · cùng thương hiệu (SS1→SS{len(uniq)})",
                    "items": uniq,
                    "n": len(uniq),
                    "franchise": True,
                })
            out = {
                **out,
                "results": results,
                "groups": groups,
                "expand": True,
                "core": q_core,
                "queries": list(out.get("queries") or [q]),
            }
        return out

    queries = expand_queries(q) if expand else [q]
    st0 = season_stem(q)
    if "之" in st0:
        tail = st0.split("之", 1)[1].strip()
        if tail and tail not in queries:
            queries.append(tail)

    by_id: dict[str, dict] = {}
    used_q: list[str] = []
    lock = threading.Lock()

    def _ingest_one(qq: str) -> None:
        try:
            data = _req("GET", base, "/dl/search", query={"q": qq})
        except Exception:  # noqa: BLE001
            return
        with lock:
            if qq not in used_q:
                used_q.append(qq)
            for r in list((data or {}).get("results") or []):
                sid = str(r.get("series_id") or "")
                if not sid or sid in by_id:
                    continue
                r = dict(r)
                r["stem"] = season_stem(r.get("title") or "")
                r["core"] = franchise_core(r.get("title") or "")
                r.setdefault("_from_q", qq)
                by_id[sid] = r

    def _run_many(qs: list[str], workers: int = 6) -> None:
        qs = [x for x in qs if x]
        if not qs:
            return
        with ThreadPoolExecutor(max_workers=min(workers, len(qs))) as ex:
            futs = [ex.submit(_ingest_one, qq) for qq in qs]
            for f in as_completed(futs):
                try:
                    f.result()
                except Exception:  # noqa: BLE001
                    pass

    _run_many(queries)

    if expand:
        brands: set[str] = set()
        q_core0 = franchise_core(q)
        st_full = season_stem(q)
        if q_core0:
            brands.add(q_core0 + "灵界篇")
            brands.add(q_core0 + "仙界篇")
        if st_full:
            brands.add(st_full)
        with lock:
            snapshot = list(by_id.values())
        for r in snapshot:
            t = r.get("title") or ""
            for s in ("灵界篇", "仙界篇"):
                if s in t:
                    brands.add((r.get("core") or q_core0 or "") + s)
            if q_core0 and q_core0 in t:
                brands.add(season_stem(t))
        # API chỉ dò vài mốc (chậm nếu full 1–20); phủ đủ nhờ web bên dưới
        api_probes = ("一", "二", "三", "四", "五", "七", "八", "十", "十二", "十五")
        with lock:
            done = set(used_q)
        extra = [
            f"{b}第{n}季"
            for b in brands if b
            for n in api_probes
            if f"{b}第{n}季" not in done
        ]
        _run_many(extra, workers=8)

        # Web hongguoduanju.com — nguồn chính cho mùa cao / sót API (~5–15s)
        web_expand_into(by_id, q, workers=6)

        _fill_missing_from_cache(by_id, season_stem(q), base)
        _fill_missing_from_cache(by_id, q_core0 or "", base)
        for r in list(by_id.values()):
            t = r.get("title") or ""
            if q_core0 and q_core0 in t:
                _fill_missing_from_cache(by_id, season_stem(t), base)

    results = list(by_id.values())
    q_core = franchise_core(q)

    def _mainline(r: dict) -> bool:
        t = r.get("title") or ""
        if not q_core or q_core not in t:
            return False
        # bản AI/真人 tách riêng — chỉ hiện khi user gõ đúng
        if ("真人" in t or "AI真人" in t) and ("真人" not in q and "AI" not in q.upper()):
            return False
        if ("杂灵根" in q) or ("BOSS" in q.upper()) or ("boss" in q.lower()):
            return ("杂灵根" in t) or ("灵界篇" in t) or ("仙界篇" in t)
        return True

    related = [r for r in results if _mainline(r)]
    groups: dict[str, list] = {}
    for r in results:
        groups.setdefault(r["stem"] or r.get("title") or "?", []).append(r)

    out_groups: list[dict] = []
    if expand and related and q_core:
        uniq = order_franchise_items(related)
        out_groups.append({
            "stem": f"{q_core} · cùng thương hiệu (SS1→SS{len(uniq)})",
            "items": uniq,
            "n": len(uniq),
            "franchise": True,
        })
    out_groups.extend(
        {"stem": k, "items": v, "n": len(v)} for k, v in groups.items()
    )
    return {
        "results": results,
        "groups": out_groups,
        "queries": used_q,
        "expand": expand,
        "core": q_core,
    }


def status(base: str = DEFAULT_BASE) -> dict:
    if _use_rpc():
        return _rpc_mod().status()
    return _req("GET", base, "/dl/status")


def settings(base: str = DEFAULT_BASE) -> dict:
    """WinUI settings.get — saveFolder/quality/parallel…"""
    if _use_rpc():
        raw = _rpc_mod().settings_get() or {}
        return {"ok": True, "settings": raw, "backend": "rpc"}
    try:
        raw = _req("GET", base, "/dl/settings") or {}
        return {"ok": True, "settings": raw, "backend": "http"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "settings": {}}


def settings_update(patch: dict | None = None, base: str = DEFAULT_BASE) -> dict:
    """WinUI settings.update — ghi patch vào helper (cùng store với app)."""
    patch = dict(patch or {})
    if _use_rpc():
        raw = _rpc_mod().settings_update(patch) or {}
        return {"ok": True, "settings": raw, "backend": "rpc"}
    try:
        raw = _req("POST", base, "/dl/settings", json=patch) or {}
        return {"ok": True, "settings": raw, "backend": "http"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "settings": {}}


def cancel(base: str = DEFAULT_BASE) -> dict:
    """Stop all active downloads."""
    if _use_rpc():
        return _rpc_mod().cancel()
    return _req("POST", base, "/dl/cancel")


def cancel_download(series_id: str, base: str = DEFAULT_BASE) -> dict:
    """Stop 1 download (RPC: cancelAll + requeue others)."""
    sid = str(series_id or "").strip()
    if not sid:
        raise RuntimeError("series_id trống")
    if _use_rpc():
        return _rpc_mod().cancel_download(sid)
    # HTTP 1.x: thử cancel theo series rồi fallback cancel all
    try:
        return _req("POST", base, "/dl/cancel", payload={"series_id": sid})
    except Exception:  # noqa: BLE001
        return _req("POST", base, "/dl/cancel")


def clear_finished(base: str = DEFAULT_BASE) -> dict:
    if _use_rpc():
        return _rpc_mod().clear_finished()
    return _req("POST", base, "/dl/clear-finished")


def remove_download(series_id: str, base: str = DEFAULT_BASE) -> dict:
    sid = str(series_id or "").strip()
    if not sid:
        raise RuntimeError("series_id trống")
    if _use_rpc():
        return _rpc_mod().remove_download(sid)
    return _req("POST", base, "/dl/remove", payload={"series_id": sid})


def retry_download(series_id: str, base: str = DEFAULT_BASE) -> dict:
    sid = str(series_id or "").strip()
    if not sid:
        raise RuntimeError("series_id trống")
    if _use_rpc():
        return _rpc_mod().retry_download(sid)
    return _req("POST", base, "/dl/retry", payload={"series_id": sid})


def open_folder(path: str | None = None, name: str | None = None) -> dict:
    """Mở thư mục series trong Explorer (WinUI Open folder)."""
    target = (path or "").strip()
    if not target and name:
        # fallback: library open by name (HTTP 1.x) hoặc tìm trong snapshot
        try:
            if _use_rpc():
                for j in (_rpc_mod().status().get("jobs") or []):
                    if (j.get("title") or "") == name or (j.get("series_id") or "") == name:
                        target = str(j.get("folder") or "")
                        break
        except Exception:  # noqa: BLE001
            pass
    if not target:
        raise RuntimeError("thiếu folder")
    p = Path(target)
    if not p.exists():
        raise RuntimeError(f"folder không tồn tại: {target}")
    folder = str(p if p.is_dir() else p.parent)
    try:
        os.startfile(folder)  # type: ignore[attr-defined]
    except Exception:
        subprocess.Popen(["explorer", folder], shell=False)
    return {"ok": True, "folder": folder}


def submit(
    series_ids: list[str],
    base: str = DEFAULT_BASE,
    scores: dict | None = None,
    quality: str | None = None,
) -> dict:
    ids = [str(x).strip() for x in series_ids if str(x).strip()]
    if not ids:
        raise RuntimeError("series_ids trống")
    if _use_rpc():
        return _rpc_mod().submit(ids, scores=scores, quality=quality)
    payload: dict = {"series_ids": ids}
    if scores:
        payload["scores"] = scores
    if quality:
        payload["quality"] = quality
    return _req("POST", base, "/dl/submit", payload=payload)


def library_update(base: str = DEFAULT_BASE, names: list[str] | None = None) -> dict:
    if _use_rpc():
        rpc = _rpc_mod()
        # 2.x: refresh thư viện; tải hết mùa mới = downloadNewSeasons (cần license)
        if names:
            return rpc.library_refresh()
        try:
            return rpc.library_download_new_seasons()
        except Exception as e:  # noqa: BLE001
            # fallback refresh nếu chưa có proof
            try:
                refreshed = rpc.library_refresh()
                refreshed["warning"] = str(e)
                return refreshed
            except Exception:  # noqa: BLE001
                raise e
    payload = {}
    if names:
        payload["names"] = [str(x) for x in names if str(x).strip()]
    return _req("POST", base, "/dl/library/update", payload=payload)


def library_update_info(base: str = DEFAULT_BASE) -> dict:
    """WinUI nút Update info — quét meta/poster, không tải mùa mới."""
    if _use_rpc():
        return _rpc_mod().library_update_info()
    return _req("POST", base, "/dl/library/update-info")


def seasons_decline(series_ids: list[str], base: str = DEFAULT_BASE) -> dict:
    ids = [str(x).strip() for x in series_ids if str(x).strip()]
    if not ids:
        raise RuntimeError("series_ids trống")
    return _req("POST", base, "/dl/library/seasons/decline", payload={"series_ids": ids})


def _normalize_browse_items(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for it in items or []:
        sid = str(it.get("series_id") or it.get("id") or "")
        if not sid:
            continue
        title = it.get("title") or ""
        eps = it.get("episode_cnt")
        if eps is None:
            eps = it.get("episodes")
        out.append({
            "series_id": sid,
            "title": title,
            "cover": it.get("cover"),
            "score": it.get("score"),
            "heat": it.get("heat"),
            "tags": it.get("tags") or ([it["genre"]] if it.get("genre") else []),
            "remarks": it.get("remarks") or it.get("genre") or "",
            "episode_cnt": _normalize_episode_cnt(eps),
            "complete": it.get("complete"),
            "status": it.get("status"),
            "stem": season_stem(title),
            "core": franchise_core(title),
        })
    return out


_GENRE_ALIASES: dict[str, list[str]] = {
    "urban": ["urban", "都市", "都市日常", "都市爱情"],
    "ai": ["ai", "人工智能", "系统"],
    "system": ["system", "系统"],
    "animated": ["animated", "animation", "anime", "动画", "动漫", "comic"],
    "3d": ["3d", "三维"],
    "romance": ["romance", "love", "爱情", "恋爱", "甜宠"],
    "love": ["love", "romance", "爱情", "恋爱"],
    "fantasy": ["fantasy", "奇幻", "玄幻", "仙侠"],
    "historical": ["historical", "history", "古装", "历史", "古代"],
    "modern": ["modern", "现代"],
    "rebirth": ["rebirth", "重生"],
    "time travel": ["time travel", "穿越"],
    "plot": ["plot", "剧情", "打脸"],
    "underdog": ["underdog", "逆袭"],
    "male lead": ["male lead", "霸总", "男频", "男主"],
    "wealthy family": ["wealthy", "豪门", "总裁"],
    "period drama": ["period", "古装", "年代"],
    "rural": ["rural", "乡村", "种田"],
    "otherworld": ["otherworld", "异界", "异能"],
    "wild premise": ["wild", "脑洞"],
    "ceo": ["ceo", "总裁", "霸总"],
}


def _filter_by_genre_tag(results: list[dict], genre: str) -> list[dict]:
    g = (genre or "").strip().lower()
    if not g or g in ("all", "any"):
        return results
    keys = list(dict.fromkeys([g] + _GENRE_ALIASES.get(g, [])))
    hit: list[dict] = []
    for r in results:
        blob = " ".join([
            str(r.get("remarks") or ""),
            " ".join(str(t) for t in (r.get("tags") or [])),
            str(r.get("title") or ""),
        ]).lower()
        if any(k.lower() in blob for k in keys if k):
            hit.append(r)
    return hit


# WinUI 2.x Browse facets — EN labels (FilterText) + empty-chip defaults
_FEED_LABELS_PATH = Path(__file__).resolve().parents[1] / "hongguo_feed_labels.json"
_FEED_LABELS_CACHE: dict[str, Any] | None = None
_FEED_EMPTY_EN = {
    "genre": "All genres",
    "category_dim_theme": "Any theme",
    "category_dim_role": "Any setting",
    "category_dim_epoch": "Any background",
    "sort": "Recommended",
    "gender": "Everyone",
    "online_time": "Any time",
}
_FEED_ROW_ORDER = (
    "genre",
    "category_dim_theme",
    "category_dim_role",
    "category_dim_epoch",
    "sort",
    "gender",
    "online_time",
)


def _feed_labels() -> dict[str, Any]:
    global _FEED_LABELS_CACHE
    if _FEED_LABELS_CACHE is not None:
        return _FEED_LABELS_CACHE
    data: dict[str, Any] = {"pairs": {}, "empty": dict(_FEED_EMPTY_EN)}
    try:
        raw = json.loads(_FEED_LABELS_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data["pairs"] = dict(raw.get("pairs") or {})
            empty = dict(raw.get("empty") or {})
            if empty:
                data["empty"] = {**_FEED_EMPTY_EN, **empty}
    except Exception:  # noqa: BLE001
        pass
    # hard fallbacks missing from auto-extract
    data["pairs"].setdefault("AI剧", "AI drama")
    _FEED_LABELS_CACHE = data
    return data


def _feed_item_en(zh_name: str) -> str:
    zh = (zh_name or "").strip()
    if not zh:
        return ""
    return str((_feed_labels().get("pairs") or {}).get(zh) or zh)


def _localize_feed_rows(rows: list[dict] | None) -> list[dict]:
    """Gắn label EN (WinUI FilterText) cho selector panel."""
    empty = (_feed_labels().get("empty") or _FEED_EMPTY_EN)
    out: list[dict] = []
    for row in rows or []:
        typ = str(row.get("type") or "")
        items_out = []
        for it in row.get("items") or []:
            zh = str(it.get("name") or "")
            en = _feed_item_en(zh)
            items_out.append({
                "id": str(it.get("id") or ""),
                "name": zh,
                "label": en,
            })
        out.append({
            "type": typ,
            "name": str(row.get("name") or ""),
            "empty_label": str(empty.get(typ) or row.get("name") or typ),
            "items": items_out,
        })
    # giữ thứ tự WinUI
    rank = {t: i for i, t in enumerate(_FEED_ROW_ORDER)}
    out.sort(key=lambda r: rank.get(r["type"], 99))
    return out


def feed_browse(
    *,
    select: dict[str, str] | None = None,
    offset: int = 0,
    size: int = 48,
    panel: bool = True,
    session_id: str = "",
) -> dict:
    """Hongguo 2.x Browse = feed.page + selector panel (genre/theme/setting/…)."""
    size_n = max(1, min(200, int(size or 48)))
    off = max(0, int(offset or 0))
    sel = {str(k): str(v) for k, v in (select or {}).items() if str(v or "").strip()}
    results: list[dict] = []
    rows: list[dict] = []
    session = (session_id or "").strip()
    source = "feed"
    has_more = False
    next_off = None
    if _use_rpc():
        try:
            # lần đầu luôn xin panel để có facets; lần sau giữ session
            want_panel = bool(panel) or not sel
            fed = _rpc_mod().feed(
                offset=off,
                select=sel or None,
                panel=want_panel,
                session_id=session,
                limit=size_n,
            ) or {}
            results = list(fed.get("results") or [])
            rows = list(fed.get("rows") or [])
            session = str(fed.get("sessionId") or session or "")
            has_more = bool(fed.get("hasMore"))
            next_off = fed.get("nextOffset")
        except Exception:
            results = []
            has_more = False
            next_off = None
    if not results and not sel:
        source = "rank_fallback"
        if _use_rpc():
            try:
                results = list((_rpc_mod().rank(
                    category="all", board="recommend", offset=off, limit=size_n,
                ) or {}).get("results") or [])
                has_more = len(results) >= size_n
                next_off = off + len(results)
            except Exception:
                results = []
    facets = _localize_feed_rows(rows) if rows else []
    # nếu RPC không trả rows (panel=false) — giữ facets rỗng; UI dùng cache
    sliced = results[:size_n]
    if next_off is None:
        next_off = off + len(sliced)
    return {
        "results": sliced,
        "n": len(sliced),
        "board": "browse",
        "source": source,
        "facets": facets,
        "sessionId": session,
        "hasMore": bool(has_more) if results else False,
        "nextOffset": int(next_off),
        "select": sel,
        "backend": "rpc" if _use_rpc() else "http",
    }


def _filter_by_status(results: list[dict], status: str) -> list[dict]:
    st = (status or "").strip().lower()
    if not st or st in ("all", "any", ""):
        return results
    want_done = st in ("completed", "complete", "done", "完结")
    want_on = st in ("ongoing", "updating", "连载")
    out: list[dict] = []
    for r in results:
        complete = r.get("complete")
        status_s = str(r.get("status") or r.get("remarks") or "").lower()
        is_done = complete is True or any(k in status_s for k in ("complete", "完结", "full"))
        is_on = complete is False or any(k in status_s for k in ("ongoing", "连载", "updating"))
        if want_done and is_done:
            out.append(r)
        elif want_on and (is_on or not is_done):
            out.append(r)
    return out


# WinUI Ranking chart tabs (short label + subtitle)
_RANK_CHART_EN: dict[str, tuple[str, str]] = {
    "ranklist_hot_sc": ("Recommended", "Ranked by views, interaction and your interests"),
    "ranklist_hot_play_sc": ("Trending", "Most played right now"),
    "ranklist_prestige": ("Top rated", "Highest viewer ratings"),
    "comic_series_rank": ("Comic", "Top comic dramas"),
    "ranklist_new_rank_sc": ("New", "Best new releases"),
    "ranklist_must_watch": ("Must-watch", "Must-watch picks"),
    "ranklist_followed": ("Most saved", "Saved by the most viewers"),
    "ranklist_hot_search_sc": ("Hot search", "Most searched this week"),
}
_RANK_CHART_ALIAS: dict[str, str] = {
    "recommend": "ranklist_hot_sc",
    "recommended": "ranklist_hot_sc",
    "hot": "ranklist_hot_play_sc",
    "trending": "ranklist_hot_play_sc",
    "score": "ranklist_prestige",
    "prestige": "ranklist_prestige",
    "comic": "comic_series_rank",
    "new": "ranklist_new_rank_sc",
    "must": "ranklist_must_watch",
    "must_watch": "ranklist_must_watch",
    "collect": "ranklist_followed",
    "followed": "ranklist_followed",
    "search": "ranklist_hot_search_sc",
    "hot_search": "ranklist_hot_search_sc",
}
_RANK_BOARD_ALIAS: dict[str, str] = {
    "all": "",
    "female": "gender_female",
    "male": "gender_male",
    "plot": "cate_316",
    "urban": "cate_261",
    "comeback": "cate_739",
}
_RANK_BOARD_EN_EXTRA: dict[str, str] = {
    "总榜": "All",
    "推荐": "Recommended",
    "热播": "Trending",
    "新剧": "New",
    "女频": "Female",
    "男频": "Male",
    "都市日常": "Urban life",
    "打脸虐渣": "Face-slap",
    "逆袭": "Comeback",
    "大女主": "Strong heroine",
    "马甲": "Hidden identity",
    "女性成长": "Female growth",
    "重生": "Rebirth",
    "乡村": "Rural",
    "穿越": "Time travel",
    "系统": "System",
    "剧情": "Plot",
}


def _rank_board_label(zh: str) -> str:
    z = (zh or "").strip()
    if not z:
        return "All"
    if z in _RANK_BOARD_EN_EXTRA:
        return _RANK_BOARD_EN_EXTRA[z]
    return _feed_item_en(z) or z


def _localize_rank_charts(charts: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for c in charts or []:
        cid = str(c.get("id") or "")
        lab, desc_en = _RANK_CHART_EN.get(cid, (str(c.get("name") or cid), str(c.get("desc") or "")))
        boards = []
        for b in c.get("boards") or []:
            bid = str(b.get("id") or "")
            bname = str(b.get("name") or "")
            boards.append({
                "id": bid,
                "name": bname,
                "label": _rank_board_label(bname),
            })
        out.append({
            "id": cid,
            "name": str(c.get("name") or ""),
            "label": lab,
            "desc": str(c.get("desc") or ""),
            "desc_en": desc_en,
            "boards": boards,
        })
    return out


def _resolve_rank_chart(chart: str | None, board_legacy: str | None) -> str:
    raw = (chart or "").strip()
    if not raw:
        raw = (board_legacy or "").strip()
    if not raw:
        return "ranklist_hot_sc"
    low = raw.lower()
    if low in _RANK_CHART_ALIAS:
        return _RANK_CHART_ALIAS[low]
    if raw in _RANK_CHART_EN or raw.startswith("ranklist_") or raw == "comic_series_rank":
        return raw
    return _RANK_CHART_ALIAS.get(low, "ranklist_hot_sc")


def _resolve_rank_board(board: str | None, category: str | None) -> str:
    """RPC board id ('' = 总榜). category giữ alias cũ all/female/male/…"""
    b = (board if board is not None else "").strip()
    if b.lower() in _RANK_BOARD_ALIAS:
        return _RANK_BOARD_ALIAS[b.lower()]
    if b.lower() in ("all",):
        return ""
    # legacy: category dùng như board filter
    cat = (category or "").strip().lower()
    if not b and cat and cat in _RANK_BOARD_ALIAS:
        return _RANK_BOARD_ALIAS[cat]
    if b in ("recommend", "hot", "score", "comic", "new", "must", "collect", "search"):
        return ""
    return b


def rank_page(
    *,
    chart: str = "",
    board: str = "",
    category: str = "all",
    offset: int = 0,
    size: int = 48,
) -> dict:
    """Hongguo 2.x Ranking = rank.page (charts + boards)."""
    # WinUI/user: ranking tối đa 100
    size_n = max(1, min(100, int(size or 100)))
    off = max(0, int(offset or 0))
    chart_id = _resolve_rank_chart(chart, board)
    board_id = _resolve_rank_board(board, category)
    # nếu board chính là chart alias thì không dùng làm filter
    if (board or "").strip().lower() in _RANK_CHART_ALIAS:
        board_id = _resolve_rank_board("", category)
    if not _use_rpc():
        return {"results": [], "n": 0, "charts": [], "chart": chart_id, "board": board_id,
                "source": "unavailable", "backend": "http"}
    raw = _rpc_mod().rank(chart=chart_id, board=board_id or None, offset=off, limit=size_n) or {}
    charts = _localize_rank_charts(raw.get("charts") or [])
    active = str(raw.get("chart") or chart_id)
    active_board = str(raw.get("board") if raw.get("board") is not None else board_id)
    cur = next((c for c in charts if c["id"] == active), charts[0] if charts else None)
    results = list(raw.get("results") or [])[:size_n]
    return {
        "results": results,
        "n": len(results),
        "charts": charts,
        "chart": active,
        "board": active_board,
        "chart_label": (cur or {}).get("label") or active,
        "desc": (cur or {}).get("desc_en") or (cur or {}).get("desc") or "",
        "hasMore": bool(raw.get("hasMore")),
        "source": "rank",
        "backend": "rpc",
    }


def rank(category: str = "all", board: str = "recommend",
         offset: int = 0, size: int = 100, base: str = DEFAULT_BASE,
         chart: str = "") -> dict:
    """Bảng hot giống app Hongguo Downloader.

    board: browse/feed | catalog* | (legacy alias) recommend/hot/…
    chart: ranklist_* (WinUI 2.x) — ưu tiên hơn board alias.
    category / board: filter board trong chart (all/female/… hoặc gender_female).
    """
    _CAT_MAP = {
        "animated": "comic",
        "comic": "comic",
        "anime": "comic",
        "animation": "comic",
        "hoạt hình": "comic",
        "hoathinh": "comic",
        "live-action": "human",
        "human": "human",
        "live": "human",
        "người đóng": "human",
        "ai": "ai",
        "all": "all",
    }
    raw_cat = (category or "all").strip() or "all"
    cat = raw_cat.lower()
    cat = _CAT_MAP.get(cat, cat)
    bd = (board or "recommend").strip() or "recommend"
    size_n = max(1, min(200, int(size or 100)))
    off = max(0, int(offset or 0))

    # Browse 2.x = feed.page (+ selector panel). category giữ tương thích cũ.
    if bd in ("browse", "feed"):
        select: dict[str, str] = {}
        low = raw_cat.lower()
        if low in ("ai", "ai_series"):
            select["genre"] = "ai_series"
        elif low in ("human", "live", "live-action", "short_play", "live_action"):
            select["genre"] = "short_play"
        elif low in ("comic", "animated", "comic_series"):
            select["genre"] = "comic_series"
        out = feed_browse(select=select or None, offset=off, size=size_n, panel=True)
        out["category"] = raw_cat
        return out

    # Catalog Explorer (genre/sort) — fallback rank nếu explorer lỗi
    if bd == "catalog" or bd.startswith("catalog_"):
        sort = "newest"
        if bd.startswith("catalog_"):
            sort = (bd.split("_", 1)[1] or "newest").strip() or "newest"
        if sort not in ("newest", "popular", "eps"):
            sort = "newest"
        genre = "" if raw_cat.lower() in ("all", "") else raw_cat
        # đừng ép Urban→all: giữ tên genre Explorer
        cat_out = catalog(genre=genre, sort=sort, page=max(1, off // size_n + 1), size=size_n)
        items = _normalize_browse_items(cat_out.get("items") or [])
        if items:
            return {
                "results": items,
                "n": len(items),
                "board": bd,
                "category": genre or "all",
                "source": "catalog",
                "page": cat_out.get("page"),
                "pages": cat_out.get("pages"),
                "genres": genres(40),
                "backend": "explorer",
            }
        # Explorer down → rank recommend + filter tag
        fb = []
        if _use_rpc():
            try:
                fb = list(rank_page(chart="ranklist_hot_sc", offset=0, size=size_n).get("results") or [])
            except Exception:
                fb = []
        if genre:
            fb = _filter_by_genre_tag(fb, genre)
        return {
            "results": fb[:size_n],
            "n": len(fb[:size_n]),
            "board": bd,
            "category": genre or "all",
            "source": "rank_fallback",
            "warning": cat_out.get("error") or "catalog unavailable",
            "genres": genres(40),
            "backend": "rpc" if _use_rpc() else "http",
        }

    # Ranking 2.x — rank.page charts/boards
    if _use_rpc():
        # `board` query cũ = chart alias (recommend/hot/…); board filter nằm ở category
        # hoặc `board` = gender_female / cate_* khi đã có `chart=`
        chart_arg = (chart or "").strip()
        board_arg = bd
        if not chart_arg and bd.lower() in _RANK_CHART_ALIAS:
            chart_arg = bd
            board_arg = ""
        elif not chart_arg and (bd.startswith("ranklist_") or bd == "comic_series_rank"):
            chart_arg = bd
            board_arg = ""
        return rank_page(
            chart=chart_arg,
            board=board_arg,
            category=raw_cat,
            offset=off,
            size=size_n,
        )
    data = _req(
        "GET", base, "/dl/rank",
        query={
            "category": cat,
            "board": bd,
            "offset": max(0, int(offset or 0)),
            "size": max(1, min(200, int(size or 100))),
        },
    )
    results: list[dict] = []
    for r in list((data or {}).get("results") or []):
        rr = dict(r)
        sid = str(rr.get("series_id") or "")
        if not sid:
            continue
        rr["series_id"] = sid
        rr["stem"] = season_stem(rr.get("title") or "")
        rr["core"] = franchise_core(rr.get("title") or "")
        results.append(rr)
    return {
        "results": results,
        "n": len(results),
        "has_more": bool((data or {}).get("has_more")),
        "next_offset": (data or {}).get("next_offset"),
        "category": cat,
        "board": bd,
    }


def genres(limit: int = 25) -> list[str]:
    """Lấy danh sách các thể loại phim từ Hongguo Catalog Explorer."""
    try:
        url = f"https://explorer.hongguodownloader.com/genres?limit={max(1, limit)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [g.get("genre") for g in (data.get("genres") or []) if g.get("genre")]
    except Exception:
        return [
            "Animated", "AI", "Urban", "Underdog", "Plot", "Historical",
            "Modern", "3D", "Rural", "Time Travel", "System", "Love",
            "Wealthy Family", "Wild Premise", "Otherworld", "Male Lead",
            "Romance", "Rebirth", "CEO", "Period Drama", "Fantasy", "Payback"
        ]


def catalog(
    genre: str = "",
    sort: str = "newest",
    status: str = "",
    q: str = "",
    page: int = 1,
    size: int = 100,
) -> dict:
    """Khám phá kho phim Hongguo Catalog (Explorer).

    genre: Animated | AI | Urban | Underdog | Plot | Historical | Modern | 3D | Rural | Time Travel | System | Love | Wealthy Family | Otherworld | ...
    sort: newest | popular | eps
    status: "" (all) | completed | ongoing
    q: từ khoá tìm kiếm
    """
    params = {
        "page": max(1, int(page or 1)),
        "size": max(1, min(100, int(size or 100))),
        "sort": sort or "newest",
    }
    if genre:
        g_low = genre.strip().lower()
        if any(w in g_low for w in ("anim", "anime", "hoạt hình", "hoathinh", "comic")):
            genre = "Animated"
        elif any(w in g_low for w in ("live", "human", "người đóng")):
            genre = "Live-action"
        elif g_low in ("ai",):
            genre = "AI"
        params["genre"] = genre
    if status:
        params["status"] = status
    if q:
        params["q"] = q.strip()

    qs = urllib.parse.urlencode(params)
    url = f"https://explorer.hongguodownloader.com/explorer?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = []
            for it in (data.get("items") or []):
                sid = str(it.get("series_id") or "")
                if not sid:
                    continue
                items.append({
                    "series_id": sid,
                    "title": it.get("title") or "",
                    "cover": it.get("cover"),
                    "score": it.get("score"),
                    "episodes": it.get("episodes"),
                    "genre": it.get("genre") or genre,
                })
            return {
                "ok": True,
                "count": data.get("count", len(items)),
                "page": data.get("page", page),
                "pages": data.get("pages", 1),
                "items": items,
            }
    except Exception as e:
        return {"ok": False, "error": str(e), "items": []}


def rank_episodes(series_ids: list[str], base: str = DEFAULT_BASE) -> dict:
    """Bổ sung số tập cho list rank (POST /dl/rank-episodes)."""
    ids = [str(x).strip() for x in (series_ids or []) if str(x).strip()]
    if not ids:
        return {"eps": {}}
    data = _req("POST", base, "/dl/rank-episodes", payload={"series_ids": ids})
    return data if isinstance(data, dict) else {"eps": {}}


def preview_series(series_id: str, ep: int = 1, fresh: bool = False,
                   base: str = DEFAULT_BASE, quality: str | None = None) -> dict:
    """Xem thử 1 tập.

    Ưu tiên HTTP Hongguo :8000 ``/dl/preview`` (CDN free). Nếu :8000 tắt /
    backend RPC → ``playback.prepare`` (tải MP4 vào hongguo-player, stream qua
    ``/api/hg/playback/stream?session=``).
    """
    sid = str(series_id or "").strip()
    if not sid:
        return {"ok": False, "error": "thiếu series_id"}
    ep_n = max(1, int(ep or 1))
    # 1) HTTP Downloader 1.x nếu còn sống
    if not _use_rpc():
        query: dict[str, Any] = {"series_id": sid, "ep": str(ep_n)}
        if fresh:
            query["fresh"] = "1"
        try:
            data = _req("GET", base, "/dl/preview", query=query)
            if isinstance(data, dict) and (data.get("ok") or data.get("url")):
                return data
        except Exception as e:  # noqa: BLE001
            http_err = str(e)
        else:
            http_err = "bad_response"
    else:
        http_err = "rpc_backend"
        # vẫn thử :8000 nhanh nếu user mở song song
        try:
            query = {"series_id": sid, "ep": str(ep_n)}
            if fresh:
                query["fresh"] = "1"
            data = _req("GET", base, "/dl/preview", query=query, timeout=3)
            if isinstance(data, dict) and data.get("url"):
                return data
        except Exception:  # noqa: BLE001
            pass

    # 2) WinUI helper playback.prepare → file local
    rpc = None
    try:
        rpc = _rpc_mod()
    except Exception:  # noqa: BLE001
        rpc = None
    if rpc is not None and rpc.helper_available():
        try:
            # Preview luôn 480p (nhanh); cache hit ≈ tức thì
            fed = rpc.playback_prepare(
                sid,
                episode=ep_n,
                quality=(quality or "480p"),
                timeout=120.0,
                force=bool(fresh),
            )
            sess = int(fed.get("session") or 0)
            return {
                "ok": True,
                "url": f"/api/hg/playback/stream?session={sess}",
                "local": True,
                "backend": "rpc",
                "session": sess,
                "quality": fed.get("quality"),
                "path": fed.get("path"),
                "cached": bool(fed.get("cached")),
                "free": 3,
                "episode": ep_n,
                "episode_cnt": None,
                "series_id": sid,
            }
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "error": str(e),
                "http_error": http_err,
                "hint": "Cần license prove (Settings) hoặc mở Hongguo :8000 cho /dl/preview",
            }
    return {
        "ok": False,
        "error": http_err or "preview_unavailable",
        "hint": "Mở Hongguo Downloader :8000 hoặc bật HG_BACKEND=rpc + license",
    }


def library(base: str = DEFAULT_BASE) -> dict:
    if _use_rpc():
        data = _rpc_mod().library()
        try:
            root = Path((_rpc_mod().settings_get() or {}).get("saveFolder") or "") or None
        except Exception:  # noqa: BLE001
            root = _machine.library_dir() if _machine.library_dir().is_dir() else None
    else:
        data = _req("GET", base, "/dl/library")
        root = _output_dir(base)
    items = list((data or {}).get("items") or [])
    for it in items:
        name = it.get("name") or it.get("title") or ""
        info = inspect_series_folder(root / name) if root and name else {}
        it.update(info)
        with _merge_lock:
            if name in _merge_done:
                mf = _merge_done[name].get("merged_file")
                has_merged = bool(it.get("merged_file") or (root and mf and (root / name / mf).is_file()))
                if has_merged and not it.get("fragments"):
                    it["done"] = True
                    it["merged"] = True
                    if mf:
                        it.setdefault("merged_file", mf)
                    it["merge_done_at"] = _merge_done[name].get("at")
                else:
                    it["done"] = False
                    if not (root and it.get("merged_file") and (root / name / it["merged_file"]).is_file()):
                        it["merged"] = False
                        it["merged_file"] = None
    data["items"] = items
    data["done_n"] = sum(1 for x in items if x.get("done") or x.get("merged"))
    data.setdefault("backend", "rpc" if _use_rpc() else "http")
    return data


def _output_dir(base: str) -> Path | None:
    if _use_rpc():
        try:
            p = (_rpc_mod().settings_get() or {}).get("saveFolder")
            return Path(p) if p else None
        except Exception:  # noqa: BLE001
            return None
    try:
        cfg = _req("GET", base, "/dl/config", timeout=5)
        p = (cfg or {}).get("output_dir")
        return Path(p) if p else None
    except Exception:  # noqa: BLE001
        return None


def inspect_series_folder(folder: Path) -> dict:
    """Quét folder series: có file (merged)? còn bao nhiêu mảnh tập?"""
    if not folder.is_dir():
        return {"merged": False, "done": False, "fragments": 0, "merged_file": None}
    merged_file = None
    try:
        entries = list(folder.iterdir())
    except OSError:
        return {"merged": False, "done": False, "fragments": 0, "merged_file": None}
    for f in entries:
        if not f.is_file():
            continue
        n = f.name
        if n.endswith((".tmp", ".tmp.mp4", ".bak", ".part")):
            continue
        if _MERGED_RE.search(n):
            merged_file = n
    # Dedupe SxxExxx / 第N集 — cùng _list_episode_frags dùng khi merge
    frag_paths = _list_episode_frags(folder)
    frags = [p.name for p in frag_paths]
    merged = bool(merged_file)
    # done = đã merge và đã dọn mảnh (hoặc không còn mảnh)
    done = merged and len(frags) == 0
    # Preview: ưu tiên merged; không thì tập đầu (sắp theo số tập)
    preview_file = merged_file
    if not preview_file and frags:
        preview_file = frags[0]
    return {
        "merged": merged,
        "merged_file": merged_file,
        "fragments": len(frags),
        "done": done,
        "preview_file": preview_file,
        "can_preview": bool(preview_file),
    }


def safe_child(root: Path | None, name: str) -> Path | None:
    """root/name chỉ khi name là 1 cấp thư mục ngay dưới root.
    Chặn `..`, `/`, `\\`, và `C:x` (Path("D:/Lib") / "C:x" nhảy sang ổ C)."""
    name = (name or "").strip()
    if not root or not name or name in (".", "..") or any(c in name for c in "/\\:") or ".." in name:
        return None
    p = Path(root) / name
    try:
        if os.path.normcase(os.path.abspath(p.parent)) != os.path.normcase(os.path.abspath(root)):
            return None
    except (OSError, ValueError):
        return None
    return p


def resolve_preview_path(name: str, base: str = DEFAULT_BASE) -> Path | None:
    """Đường dẫn file xem thử trong thư viện (merged hoặc tập đầu)."""
    folder = safe_child(_output_dir(base), name)
    if not folder:
        return None
    info = inspect_series_folder(folder)
    pf = info.get("preview_file")
    if not pf:
        return None
    p = folder / pf
    return p if p.is_file() else None


def resolve_poster_path(name: str, base: str = DEFAULT_BASE) -> Path | None:
    """poster.jpg/png trong folder series (local)."""
    folder = safe_child(_output_dir(base), name)
    if not folder or not folder.is_dir():
        return None
    for cand in (
        "poster.jpg", "poster.jpeg", "poster.png",
        "cover.jpg", "cover.jpeg", "cover.png", "thumb.jpg",
    ):
        p = folder / cand
        if p.is_file() and p.stat().st_size > 32:
            return p
    return None


def fetch_poster(name: str, base: str = DEFAULT_BASE) -> tuple[bytes, str]:
    """Bytes poster: ưu tiên /dl/poster (auth), fallback file local."""
    name = (name or "").strip()
    if not name:
        raise RuntimeError("thiếu name")
    try:
        from core.film_db import is_junk_hg_name
        if is_junk_hg_name(name):
            raise FileNotFoundError("không có poster")
    except ImportError:
        pass
    # 1) API Downloader (có token)
    try:
        url = (
            base.rstrip("/")
            + "/dl/poster?name="
            + urllib.parse.quote(name)
        )
        headers = {"Accept": "image/*,*/*"}
        headers.update(_auth_headers(base))
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
            ctype = r.headers.get("Content-Type") or "image/jpeg"
        if data and len(data) > 32 and not ctype.startswith("application/json"):
            return data, ctype
    except Exception:  # noqa: BLE001
        pass
    # 2) File trong D:\Lib\<name>\
    p = resolve_poster_path(name, base)
    if p:
        suf = p.suffix.lower()
        media = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp",
        }.get(suf, "image/jpeg")
        return p.read_bytes(), media
    raise FileNotFoundError("không có poster")


_IMG_CACHE: dict[str, tuple[float, bytes, str]] = {}
_IMG_CACHE_TTL = 3600.0  # 1 giờ


def fetch_img_proxy(url: str, base: str = DEFAULT_BASE) -> tuple[bytes, str]:
    """Proxy ảnh cover: RPC ``covers.get`` → file local; HTTP :8000/img; fallback fetch trực tiếp."""
    url = (url or "").strip()
    if not url:
        raise RuntimeError("thiếu url")

    now = time.time()
    cached = _IMG_CACHE.get(url)
    if cached and (now - cached[0] < _IMG_CACHE_TTL):
        return cached[1], cached[2]

    def _store(data: bytes, ctype: str) -> tuple[bytes, str]:
        if len(_IMG_CACHE) > 500:
            _IMG_CACHE.clear()
        _IMG_CACHE[url] = (now, data, ctype)
        return data, ctype

    is_hg_cdn = any(h in url.lower() for h in ("byteimg.com", "novel-pic", "fqnovel", "pstatp.com", "heic"))
    # 2.x RPC: helper tải/ký cover → path Roaming\HongguoDownloader\covers\…
    if is_hg_cdn and _use_rpc():
        try:
            rpc = _rpc_mod()
            out = rpc.call("covers.get", {"url": url}) or {}
            path = Path(str(out.get("path") or ""))
            if path.is_file():
                data = path.read_bytes()
                suf = path.suffix.lower()
                ctype = {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".webp": "image/webp", ".gif": "image/gif", ".heic": "image/heic",
                }.get(suf, "image/jpeg")
                if data:
                    return _store(data, ctype)
        except Exception:
            pass

    if is_hg_cdn and str(base).startswith("http"):
        try:
            target = base.rstrip("/") + "/img?url=" + urllib.parse.quote(url, safe="")
            headers = {"Accept": "image/*,*/*"}
            headers.update(_auth_headers(base))
            req = urllib.request.Request(target, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type") or "image/jpeg"
                if data:
                    return _store(data, ctype)
        except Exception:
            pass

    # Ảnh ngoài (Baidu, Duanjubaike...) hoặc fallback: fetch trực tiếp
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": "https://www.hongguo.com/",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        data = r.read()
        ctype = r.headers.get("Content-Type") or "image/jpeg"

    if not data:
        raise RuntimeError("ảnh trống")
    return _store(data, ctype)


TRASH_KEEP_DAYS = 7
MERGE_MIN_COVER = 0.98   # merged phải dài ≥ 98% tổng thời lượng tập mới được dọn tập


def _trash_root(folder: Path) -> Path:
    """Thùng rác NGOÀI thư viện (D:\\Lib → D:\\Lib_trash): Hongguo/Pineline quét thư viện
    sẽ không thấy tập đã dọn là tập mới / series mới. Cùng ổ → move = rename tức thì."""
    lib = folder.parent
    if lib.parent == lib:
        return lib / ".hg_trash"
    return lib.parent / f"{lib.name}_trash"


def purge_trash(folder: Path, days: float = TRASH_KEEP_DAYS) -> int:
    """Xoá hẳn các lô trong thùng rác cũ hơn `days` ngày. Trả số lô đã xoá."""
    troot = _trash_root(folder)
    if not troot.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for series in troot.iterdir():
        if not series.is_dir():
            continue
        for batch in series.iterdir():
            try:
                if batch.is_dir() and batch.stat().st_mtime < cutoff:
                    shutil.rmtree(batch, ignore_errors=True)
                    n += 1
            except OSError:
                continue
        try:
            next(series.iterdir())
        except StopIteration:
            series.rmdir()
        except OSError:
            pass
    return n


def merged_covers_fragments(folder: Path, merged_file: str | None = None) -> dict:
    """Kiểm file gộp có chứa đủ các tập lẻ đang nằm trong folder không.

    ok      — merged mới hơn mọi tập và dài ≥ MERGE_MIN_COVER × tổng thời lượng tập.
    stale   — có tập mới hơn merged (tải sau khi gộp) → phải gộp lại, không dọn.
    remerge — stale nhưng tập lẻ vẫn chứa đủ nội dung merged cũ → gộp lại từ tập là an toàn.
    """
    if not merged_file:
        merged_file = inspect_series_folder(folder).get("merged_file")
    if not merged_file:
        return {"ok": False, "reason": "chưa có file merged"}
    mp = folder / merged_file
    frags = _list_episode_frags(folder)
    if not frags:
        return {"ok": True, "reason": "không còn tập lẻ", "n": 0}
    try:
        m_mtime = mp.stat().st_mtime
        newest = max(f.stat().st_mtime for f in frags)
    except OSError as e:
        return {"ok": False, "reason": f"stat: {e}"}
    m_dur = _probe_dur(mp)
    f_dur = sum(_probe_dur(f) for f in frags)
    out = {"n": len(frags), "merged_dur": round(m_dur, 1), "frags_dur": round(f_dur, 1)}
    if newest > m_mtime + 60:
        n_new = sum(1 for f in frags if f.stat().st_mtime > m_mtime + 60)
        return {**out, "ok": False, "stale": True, "n_new": n_new,
                "remerge": m_dur > 0 and f_dur >= m_dur * MERGE_MIN_COVER,
                "reason": f"{n_new} tập mới hơn file gộp"}
    if m_dur <= 0 or f_dur <= 0:
        return {**out, "ok": False, "reason": "không đo được thời lượng (ffprobe)"}
    if m_dur < f_dur * MERGE_MIN_COVER:
        return {**out, "ok": False,
                "reason": f"file gộp {m_dur:.0f}s < {MERGE_MIN_COVER:.0%} tổng tập {f_dur:.0f}s"}
    return {**out, "ok": True}


def _move_to_trash(folder: Path, files: list[Path]) -> tuple[list[str], list[str], Path]:
    dest = _trash_root(folder) / folder.name / time.strftime("%Y%m%d_%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    errors: list[str] = []
    for f in files:
        try:
            shutil.move(str(f), str(dest / f.name))
            moved.append(f.name)
        except OSError as e:
            errors.append(f"{f.name}: {e}")
    return moved, errors, dest


def cleanup_fragments(folder: Path) -> dict:
    """Dọn tập lẻ sau khi đã có (merged) ĐỦ nội dung: chỉ file đúng mẫu tên tập,
    chuyển vào thùng rác (giữ TRASH_KEEP_DAYS ngày) thay vì xoá hẳn."""
    if not folder.is_dir():
        return {"ok": False, "error": "folder missing", "removed": 0}
    info = inspect_series_folder(folder)
    if not info.get("merged_file"):
        return {"ok": False, "error": "chưa có file merged", "removed": 0}
    try:
        purge_trash(folder)
    except Exception:  # noqa: BLE001
        pass
    chk = merged_covers_fragments(folder, info["merged_file"])
    if not chk.get("ok"):
        return {"ok": False, "error": f"không dọn: {chk.get('reason')}", "removed": 0,
                "merged_file": info["merged_file"], "check": chk}
    frags = _list_episode_frags(folder)
    if not frags:
        return {"ok": True, "removed": 0, "files": [], "merged_file": info["merged_file"],
                "errors": []}
    moved, errors, dest = _move_to_trash(folder, frags)
    return {
        "ok": not errors,
        "removed": len(moved),
        "files": moved[:30],
        "merged_file": info["merged_file"],
        "trash": str(dest),
        "errors": errors[:5],
        "check": chk,
    }


def _ep_num_from_name(name: str) -> int:
    """Số tập từ S01E013 / 第013集 / E13 / EP13 — không lấy nhầm season S01."""
    stem = Path(name).stem
    for pat in (
        r"第\s*(\d+)\s*集",
        r"S\d{1,2}E(\d{1,4})",
        r"EP\s*(\d{1,4})",
        r"^E(\d{1,4})$",
        r"^(\d{1,4})$",
    ):
        m = re.search(pat, stem, re.I)
        if m:
            return int(m.group(1))
    m = re.search(r"(\d{1,4})", stem)
    return int(m.group(1)) if m else 10**9


def _list_episode_frags(folder: Path) -> list[Path]:
    """Liệt kê tập mảnh; dedupe cùng số tập (ưu tiên SxxExxx + file đọc được)."""
    cands: list[Path] = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return cands
    for f in entries:
        if f.is_file() and _FRAG_RE.match(f.name):
            cands.append(f)
    groups: dict[int, list[Path]] = {}
    for f in cands:
        groups.setdefault(_ep_num_from_name(f.name), []).append(f)
    out: list[Path] = []
    for num in sorted(groups):
        files = groups[num]
        if len(files) == 1:
            out.append(files[0])
            continue
        # Trùng tên kiểu S01E013 + 第013集 (một bên hay corrupt) → chọn bản đọc được
        files.sort(
            key=lambda p: (
                0 if re.match(r"S\d{1,2}E", p.name, re.I) else 1,
                -int(p.stat().st_size) if p.exists() else 0,
            )
        )
        picked: Path | None = None
        for p in files:
            if _probe_wh(p):
                picked = p
                break
        out.append(picked or files[0])
    return out


def _probe_wh(path: Path) -> tuple[int, int] | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        line = (r.stdout or "").strip().splitlines()
        if not line:
            return None
        parts = line[0].split(",")
        if len(parts) < 2:
            return None
        w, h = int(parts[0]), int(parts[1])
        if w > 0 and h > 0:
            return w, h
    except Exception:  # noqa: BLE001
        return None
    return None


def _probe_dur(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        return float((r.stdout or "").strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _frame_wh_at(path: Path, t: float) -> tuple[int, int] | None:
    """Decode 1 frame tại t — bắt size thật (kể cả mid-stream đổi res)."""
    try:
        # -ss trước -i: nhanh; denser sampling bù keyframe jump
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
             "-frames:v", "1", "-vf", "showinfo", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        err = r.stderr or ""
        m = re.search(r"\bw:(\d+)\s+h:(\d+)\b", err)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if w > 16 and h > 16:
                return w, h
        all_sz = re.findall(r"(\d{3,5})x(\d{3,5})", err)
        if len(all_sz) >= 2:
            w, h = int(all_sz[-1][0]), int(all_sz[-1][1])
            if w > 16 and h > 16:
                return w, h
    except Exception:  # noqa: BLE001
        return None
    return None


def _pick_h264_encoder() -> list[str]:
    """Args -c:v … cho normalize (ưu tiên NVENC)."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15,
        )
        enc = r.stdout or ""
        if "h264_nvenc" in enc:
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                    "-b:v", "4M", "-maxrate", "4M", "-bufsize", "8M"]
    except Exception:  # noqa: BLE001
        pass
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def _scale_vf(w: int, h: int) -> str:
    w, h = w - (w % 2), h - (h % 2)
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )


def episode_resolutions(folder: Path) -> dict[str, Any]:
    """Quét độ phân giải từng tập mảnh. mixed=True nếu lệch nhau."""
    eps = _list_episode_frags(folder)
    sizes: dict[str, tuple[int, int]] = {}
    for ep in eps:
        wh = _probe_wh(ep)
        if wh:
            sizes[ep.name] = wh
    uniq = set(sizes.values())
    target = max(uniq, key=lambda wh: wh[0] * wh[1]) if uniq else None
    return {
        "episodes": len(eps),
        "probed": len(sizes),
        "sizes": {k: f"{v[0]}x{v[1]}" for k, v in sizes.items()},
        "unique": [f"{w}x{h}" for w, h in sorted(uniq)],
        "mixed": len(uniq) > 1,
        "target": f"{target[0]}x{target[1]}" if target else None,
        "target_wh": target,
        "files": eps,
    }


def merged_has_mixed_res(path: Path, samples: int = 0) -> dict[str, Any]:
    """Kiểm tra file (merged) có đổi res giữa chừng không."""
    dur = _probe_dur(path)
    if dur <= 0:
        return {"mixed": False, "error": "no duration"}
    # ~ mỗi 3 phút (mặc định), hoặc samples điểm đều
    if samples and samples > 0:
        n = max(8, int(samples))
        times = [dur * i / (n - 1) for i in range(n)]
    else:
        times = []
        t = 0.0
        while t < dur - 0.5:
            times.append(t)
            t += 180.0
        times.append(max(0.0, dur - 0.5))
    found: list[tuple[float, int, int]] = []
    for t in times:
        wh = _frame_wh_at(path, min(t, max(0.0, dur - 0.5)))
        if wh:
            found.append((t, wh[0], wh[1]))
    uniq = {(w, h) for _, w, h in found}
    target = max(uniq, key=lambda wh: wh[0] * wh[1]) if uniq else None
    return {
        "mixed": len(uniq) > 1,
        "samples": [{"t": round(t, 1), "wh": f"{w}x{h}"} for t, w, h in found],
        "unique": [f"{w}x{h}" for w, h in sorted(uniq)],
        "target_wh": target,
        "duration": dur,
    }


def normalize_merged_file(
    path: Path,
    target_wh: tuple[int, int] | None = None,
    on_pct: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Re-encode 1 file (merged) về canvas cố định (scale+pad)."""
    path = Path(path)
    if not path.is_file():
        return {"ok": False, "error": "missing file"}
    if not target_wh:
        info = merged_has_mixed_res(path)
        if not info.get("mixed"):
            return {"ok": True, "skipped": True, "reason": "uniform"}
        target_wh = MERGE_MIXED_CANVAS
    if not target_wh:
        target_wh = MERGE_MIXED_CANVAS
    tw, th = int(target_wh[0]), int(target_wh[1])
    tw, th = tw - (tw % 2), th - (th % 2)
    dur = _probe_dur(path)
    tmp = path.with_suffix(".norm.partial.mp4")
    final_tmp = path.with_suffix(".norm.tmp.mp4")
    vargs = _pick_h264_encoder()
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
        "-i", str(path),
        "-vf", _scale_vf(tw, th),
        *vargs, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(tmp),
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if on_pct and dur > 0 and line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                    on_pct(min(99.0, us / 1e6 / dur * 100))
                except ValueError:
                    pass
        rc = proc.wait()
        if rc != 0 or not tmp.exists() or tmp.stat().st_size < 1000:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            return {"ok": False, "error": f"ffmpeg rc={rc}"}
        tmp.replace(final_tmp)
        bak = path.with_suffix(path.suffix + ".pre_norm.bak")
        if bak.exists():
            bak.unlink(missing_ok=True)
        path.replace(bak)
        final_tmp.replace(path)
        try:
            bak.unlink(missing_ok=True)
        except OSError:
            pass
        if on_pct:
            on_pct(100.0)
        return {"ok": True, "path": str(path), "target": f"{tw}x{th}",
                "encoder": vargs[1] if len(vargs) > 1 else "?"}
    except Exception as e:  # noqa: BLE001
        for p in (tmp, final_tmp):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        return {"ok": False, "error": str(e)}


def merge_series_normalized(
    folder: Path,
    on_pct: Callable[[float], None] | None = None,
    target_wh: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Merge các tập mảnh về 1 canvas (scale+pad) rồi concat.

    Dùng khi episode_resolutions().mixed — thay cho Hongguo -c copy.
    Mặc định canvas 1920×1080 (policy scale_1080).
    """
    folder = Path(folder)
    info = episode_resolutions(folder)
    eps: list[Path] = list(info.get("files") or [])
    if len(eps) < 2:
        return {"ok": False, "error": "need ≥2 episodes", **{k: info[k] for k in
                ("mixed", "unique", "target") if k in info}}
    if not info.get("mixed"):
        return {"ok": False, "skipped": True, "reason": "uniform — dùng Hongguo copy",
                "unique": info.get("unique")}
    target = target_wh or info.get("target_wh") or MERGE_MIXED_CANVAS
    if info.get("target_wh") and info["target_wh"][1] > info["target_wh"][0]:
        target = info["target_wh"]
    tw, th = int(target[0]), int(target[1])
    tw, th = tw - (tw % 2), th - (th % 2)
    out_name = f"{folder.name} (merged).mp4"
    out_path = folder / out_name
    vargs = _pick_h264_encoder()
    vf = _scale_vf(tw, th)
    tmp_root = Path(tempfile.mkdtemp(prefix="hg_norm_merge_"))
    try:
        parts: list[Path] = []
        skipped_bad: list[str] = []
        n = len(eps)
        for i, ep in enumerate(eps):
            # Bỏ tập hỏng (Invalid data / thiếu bitstream) — NVENC -22 hay dính case này
            if not _probe_wh(ep):
                skipped_bad.append(ep.name)
                if on_pct:
                    on_pct((i + 1) / n * 90.0)
                continue
            part = tmp_root / f"{i:04d}.mp4"
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(ep),
                "-vf", vf, *vargs, "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(part),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0 or not part.exists():
                # NVENC đôi khi -22 trên input lệch → thử libx264 1 lần
                vsoft = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
                cmd2 = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(ep),
                    "-vf", vf, *vsoft, "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart",
                    str(part),
                ]
                r2 = subprocess.run(cmd2, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
                if r2.returncode != 0 or not part.exists():
                    skipped_bad.append(ep.name)
                    if on_pct:
                        on_pct((i + 1) / n * 90.0)
                    continue
            parts.append(part)
            if on_pct:
                on_pct((i + 1) / n * 90.0)
        if len(parts) < 2:
            return {
                "ok": False,
                "error": (
                    f"need ≥2 readable episodes (got {len(parts)}; "
                    f"skipped corrupt/unreadable: {skipped_bad[:8]})"
                ),
                "skipped": skipped_bad,
            }

        list_txt = tmp_root / "list.txt"
        with list_txt.open("w", encoding="utf-8") as f:
            for p in parts:
                # concat demuxer: escape single quotes and use forward slashes for Windows
                esc = p.as_posix().replace("'", "'\\''")
                f.write(f"file '{esc}'\n")
        partial = folder / (out_name + ".tmp.mp4")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_txt),
            "-c", "copy", "-f", "mp4", "-movflags", "+faststart",
            str(partial),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 or not partial.exists():
            err = (r.stderr or r.stdout or "")[-300:]
            return {"ok": False, "error": f"concat: {err or r.returncode}"}
        if out_path.exists():
            out_path.unlink()
        partial.replace(out_path)
        if on_pct:
            on_pct(100.0)
        return {
            "ok": True,
            "merged_file": out_name,
            "path": str(out_path),
            "target": f"{tw}x{th}",
            "policy": "scale_1080",
            "episodes": n,
            "unique_in": info.get("unique"),
            "encoder": vargs[1] if len(vargs) > 1 else "?",
        }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def mark_done(name: str, merged_file: str | None = None, removed: int = 0) -> None:
    with _merge_lock:
        _merge_done[name] = {
            "at": int(time.time()),
            "merged_file": merged_file,
            "removed": removed,
        }
        _save_merge_state()


def merge_one(name: str, base: str = DEFAULT_BASE) -> dict:
    name = (name or "").strip()
    if not name:
        raise RuntimeError("thiếu name")
    return _req("POST", base, "/dl/library/merge", payload={"name": name})


def merge_status(base: str = DEFAULT_BASE) -> dict:
    return _req("GET", base, "/dl/library/merge-status")


def open_library(name: str | None = None, base: str = DEFAULT_BASE) -> dict:
    payload = {"name": name} if name else {}
    return _req("POST", base, "/dl/library/open", payload=payload)


def _film_db():
    """film_db dual-tree: V5 `core.film_db` hoặc V2/V4 `film_db`."""
    try:
        from core import film_db as m  # type: ignore
        return m
    except Exception:  # noqa: BLE001
        import core.film_db as m  # type: ignore
        return m


def remember_library_items(items: list[dict] | None, *, source: str = "library",
                           on_disk: int = 1, deleted: bool = False) -> int:
    """Ghi meta series vào hg_watchlist (không giữ video)."""
    try:
        return int(_film_db().upsert_hg_watchlist_many(
            list(items or []), source=source, on_disk=on_disk, deleted=deleted
        ))
    except Exception:  # noqa: BLE001
        return 0


def season_num(title: str, season_field=None) -> int:
    """Số mùa từ field hoặc chữ 「第N季」 trong title; 0 = không rõ."""
    if season_field is not None and str(season_field).strip() != "":
        try:
            n = int(season_field)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    m = re.search(r"第([0-9一二三四五六七八九十百零〇两兩]+)季", title or "")
    if not m:
        return 0
    n = _parse_cn_int(m.group(1))
    return n if n is not None else 0


def discover_new_seasons(base: str = DEFAULT_BASE, max_cores: int = 16,
                         expand: bool = False) -> dict:
    """Chỉ tìm mùa/series mới theo watchlist+films+lib hiện có — KHÔNG gọi /dl/library/update.

    Trả board dạng seasons (held=mới hơn max đã biết / older=≤max / related) + groups.
    Không tự tải.
    """
    fdb = _film_db()
    lib = library(base)
    items = list((lib or {}).get("items") or [])
    # Seed / refresh watchlist từ Lib đang còn trên đĩa
    remember_library_items(items, source="library", on_disk=1, deleted=False)
    # Bổ sung sid đã làm từ films.db
    try:
        for sid in sorted(fdb.series_ids()):
            fdb.upsert_hg_watchlist(sid=sid, source="films", on_disk=0)
    except Exception:  # noqa: BLE001
        pass

    known = set(fdb.known_hg_sids())
    for it in items:
        sid = str(it.get("series_id") or it.get("sid") or "").strip()
        if sid:
            known.add(sid)

    # max season đã biết theo franchise core (watchlist + lib)
    max_known: dict[str, int] = {}

    def _bump(core: str, title: str, season_field=None) -> None:
        c = (core or franchise_core(title) or "").strip()
        if not c:
            return
        sn = season_num(title, season_field)
        if sn > max_known.get(c, 0):
            max_known[c] = sn

    try:
        for w in fdb.list_hg_watchlist():
            _bump(str(w.get("core") or ""), str(w.get("title") or w.get("name") or ""),
                  w.get("season"))
    except Exception:  # noqa: BLE001
        pass
    for it in items:
        _bump(str(it.get("core") or ""), str(it.get("title") or it.get("name") or ""),
              it.get("season"))

    cores = list(fdb.watchlist_cores(limit=max(1, int(max_cores))))
    # Nếu watchlist trống: lấy core từ Lib live
    if not cores:
        seen: set[str] = set()
        for it in items:
            title = str(it.get("title") or it.get("name") or "")
            c = franchise_core(title)
            if c and c not in seen:
                seen.add(c)
                cores.append(c)
            if len(cores) >= max_cores:
                break
    # Chuẩn hoá stem (watchlist/films.series đôi khi còn 「第N季」)
    norm: list[str] = []
    seen_stem: set[str] = set()
    for c in cores:
        stem = (franchise_core(c) or str(c or "").strip() or "").strip()
        if stem and stem not in seen_stem:
            seen_stem.add(stem)
            norm.append(stem)
    cores = norm

    held: list[dict] = []
    older: list[dict] = []
    related: list[dict] = []
    by_sid: dict[str, dict] = {}

    def _board_item(r: dict, base_guess: str = "") -> dict | None:
        sid = str(r.get("series_id") or r.get("sid") or "").strip()
        if not re.fullmatch(r"\d{15,20}", sid):
            return None
        title = str(r.get("title") or r.get("name") or sid)
        sn = season_num(title, r.get("season"))
        base_v = str(
            r.get("core") or r.get("stem") or franchise_core(title) or base_guess or ""
        ).strip()
        base_v = (franchise_core(base_v) or base_v).strip()
        return {
            "sid": sid,
            "title": title,
            "eps": int(r.get("episodes") or r.get("eps") or r.get("total") or 0),
            "season": sn,
            "score": str(r.get("score") or ""),
            "base": base_v,
            "album": str(r.get("album") or ""),
            "short": True,
        }

    def _same_franchise(it_base: str, it_title: str, core: str) -> bool:
        stem = (franchise_core(core) or core or "").strip()
        b = (it_base or franchise_core(it_title) or "").strip()
        if not stem and not core:
            return False
        if b and (b == core or b == stem):
            return True
        if stem and (stem in (it_title or "") or (b and (stem in b or b in stem))):
            return True
        if core and core in (it_title or ""):
            return True
        return False

    scanned = 0
    for core in cores[: max(1, int(max_cores))]:
        scanned += 1
        try:
            res = search(core, base=base, expand=bool(expand))
        except Exception:  # noqa: BLE001
            continue
        rows = list(res.get("results") or [])
        for g in res.get("groups") or []:
            if isinstance(g, dict) and isinstance(g.get("items"), list):
                rows.extend(g["items"])
        for row in rows:
            it = _board_item(row, core)
            if not it or it["sid"] in by_sid:
                continue
            by_sid[it["sid"]] = it
            if it["sid"] in known:
                continue
            if not _same_franchise(it["base"], it["title"], core):
                related.append(it)
                continue
            mk = (
                max_known.get(it["base"] or "", 0)
                or max_known.get(core, 0)
                or max_known.get(franchise_core(core) or "", 0)
            )
            it["max_known"] = mk
            # Có số mùa và ≤ max đã biết → older; còn lại (mới hơn / chưa rõ số) → held
            if it["season"] > 0 and mk > 0 and it["season"] <= mk:
                older.append(it)
            else:
                held.append(it)

    # Gom theo franchise cho Films UI
    group_map: dict[str, dict] = {}
    for bucket, key in ((held, "newer"), (older, "older"), (related, "related")):
        for it in bucket:
            core = str(it.get("base") or franchise_core(it.get("title") or "") or "?").strip() or "?"
            g = group_map.get(core)
            if not g:
                g = {
                    "core": core,
                    "max_known": int(max_known.get(core, 0) or 0),
                    "newer": [],
                    "older": [],
                    "related": [],
                }
                group_map[core] = g
            g[key].append(it)
            if it.get("max_known") and int(it["max_known"]) > g["max_known"]:
                g["max_known"] = int(it["max_known"])
    groups = sorted(
        group_map.values(),
        key=lambda g: (-(len(g["newer"]) + len(g["related"])), g["core"]),
    )

    seasons = {
        "state": "done",
        "auto": [],
        "locked": [],
        "failed": [],
        "stopped": [],
        "declined": [],
        "older": older,
        "removed": [],
        "soon": [],
        "held": held,
        "related": related,
        "big": {"albums": []},
        "albums": 0,
        "partial": False,
        "deferred": 0,
    }
    return {
        "ok": True,
        "seasons": seasons,
        "groups": groups,
        "max_known": max_known,
        "scanned_cores": scanned,
        "cores": cores[: max(1, int(max_cores))],
        "known_n": len(known),
        "found_n": len(held) + len(related),
        "newer_n": len(held),
        "older_n": len(older),
        "held_n": len(held),
        "related_n": len(related),
        "watchlist_n": len(fdb.list_hg_watchlist()),
        "download": False,
        "hint": "chỉ thông tin — bấm + Thêm mới tải · held=mới hơn max đã biết",
    }


def delete_library_series(names: list[str], base: str = DEFAULT_BASE) -> dict:
    r"""Xoá vĩnh viễn thư mục series tại ổ đĩa D:\Lib để giải phóng dung lượng.

    Trước khi xoá: ghi meta (sid/core/title…) vào films.db.`hg_watchlist` để vẫn quét được mùa mới.
    """
    root = _output_dir(base)
    if not root or not root.is_dir():
        return {"ok": False, "error": f"Không tìm thấy thư mục thư viện ({root})"}
    deleted: list[str] = []
    errors: list[str] = []
    freed_bytes = 0
    remembered = 0
    import stat
    import shutil
    import subprocess

    # Snapshot meta trước khi xoá đĩa
    by_name: dict[str, dict] = {}
    try:
        for it in (library(base).get("items") or []):
            nm = str(it.get("name") or "").strip()
            if nm:
                by_name[nm] = it
    except Exception:  # noqa: BLE001
        by_name = {}

    def _robust_remove(folder: Path):
        def _fix_perm(func, fpath, exc_info):
            try:
                os.chmod(fpath, stat.S_IWRITE)
                func(fpath)
            except Exception:
                pass
        try:
            shutil.rmtree(folder, onerror=_fix_perm)
        except Exception:
            try:
                shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                pass
        if folder.exists():
            try:
                subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(folder)], capture_output=True, timeout=10)
            except Exception:
                pass

    with _merge_lock:
        for nm in names:
            nm = (nm or "").strip()
            folder = safe_child(root, nm)
            if not folder:
                continue
            meta = by_name.get(nm) or {"name": nm, "title": nm}
            if folder.is_dir():
                try:
                    for f in folder.rglob("*"):
                        if f.is_file():
                            try:
                                freed_bytes += f.stat().st_size
                            except OSError:
                                pass
                except Exception:
                    pass
                try:
                    _robust_remove(folder)
                    if folder.exists():
                        errors.append(f"{nm}: Thư mục đang bị tiến trình khác khóa")
                    else:
                        deleted.append(nm)
                        _merge_done.pop(nm, None)
                        if nm in _merge_q:
                            _merge_q.remove(nm)
                        remembered += remember_library_items(
                            [meta], source="delete", on_disk=0, deleted=True
                        )
                except Exception as e:
                    errors.append(f"{nm}: {e}")
            elif not folder.exists():
                deleted.append(nm)
                _merge_done.pop(nm, None)
                if nm in _merge_q:
                    _merge_q.remove(nm)
                remembered += remember_library_items(
                    [meta], source="delete", on_disk=0, deleted=True
                )
        if deleted:
            _save_merge_state()
            try:
                _film_db().mark_hg_watchlist_deleted_by_names(deleted)
            except Exception:  # noqa: BLE001
                pass

    return {
        "ok": len(errors) == 0 or len(deleted) > 0,
        "deleted": deleted,
        "errors": errors,
        "count": len(deleted),
        "freed_mb": round(freed_bytes / (1024 * 1024), 1),
        "watchlist_saved": remembered,
    }


# ---------- merge queue (Pineline-side) ----------

def merge_resume(base: str | None = None) -> bool:
    """Hàng đợi còn (nạp lại sau restart) mà không có worker → chạy tiếp, chỉ khi Hongguo đang mở
    (Hongguo tắt thì mọi series sẽ báo lỗi và rơi khỏi hàng đợi)."""
    b = base or _merge_base or DEFAULT_BASE
    with _merge_lock:
        if not _merge_q or _merge_stop or (_merge_thread and _merge_thread.is_alive()):
            return False
    if _output_dir(b) is None:
        return False
    with _merge_lock:
        if not _merge_q or (_merge_thread and _merge_thread.is_alive()):
            return False
        _start_worker(b)
    print(f"[hg-merge] chạy tiếp hàng đợi {len(_merge_q)} series", flush=True)
    return True


def merge_queue_state(base: str = DEFAULT_BASE) -> dict:
    try:
        ms = merge_status(base)
    except Exception as e:  # noqa: BLE001
        ms = {"error": str(e)}
    # review1: GET này (UI/feeder poll) từng tự chạy tiếp hàng gộp → lách cờ instance hg_merge_resume.
    if (os.getenv("PINELINE_FLAG_HG_MERGE_RESUME") or "1").strip().lower() in ("1", "true", "yes", "on"):
        try:
            merge_resume(base)
        except Exception:  # noqa: BLE001
            pass
    with _merge_lock:
        return {
            "queue": list(_merge_q),
            "current": _merge_cur,
            "phase": dict(_merge_phase),
            "batch": dict(_merge_batch),
            "history": list(_merge_hist[-20:]),
            "done": dict(_merge_done),
            "worker": bool(_merge_thread and _merge_thread.is_alive()),
            "hongguo": ms,
        }


def merge_queue_add(names: list[str], base: str = DEFAULT_BASE,
                    skip_done: bool = True) -> dict:
    """Chỉ xếp hàng rồi trả về ngay — kiểm tra/chuẩn hoá/dọn tập đều do worker làm (UI thấy tiến độ)."""
    global _merge_base
    cleaned = []
    skipped = []
    seen = set()
    root = _output_dir(base)
    for n in names:
        n = (n or "").strip()
        if not n or n in seen:
            continue
        seen.add(n)
        if root and not safe_child(root, n):
            skipped.append(n)
            continue
        if skip_done and root and inspect_series_folder(root / n).get("done"):
            skipped.append(n)
            continue
        cleaned.append(n)
    if not cleaned and not skipped:
        raise RuntimeError("danh sách merge trống")
    with _merge_lock:
        idle = not _merge_q and not _merge_cur
        if idle or not _merge_batch:
            _merge_batch.clear()
            _merge_batch.update({"total": 0, "finished": 0, "errors": 0, "started": int(time.time())})
        added = [n for n in cleaned if n not in _merge_q and n != _merge_cur]
        _merge_q.extend(added)
        _merge_batch["total"] = int(_merge_batch.get("total") or 0) + len(added)
        _merge_base = base
        _save_merge_state()
        if cleaned:
            _start_worker(base)
        return {
            "ok": True,
            "queued": cleaned,
            "skipped_done": skipped,
            "queue": list(_merge_q),
            "current": _merge_cur,
        }


def merge_queue_clear() -> dict:
    global _merge_stop
    with _merge_lock:
        _merge_q.clear()
        _merge_stop = True
        _save_merge_state()
    return {"ok": True}


def _start_worker(base: str) -> None:
    global _merge_thread, _merge_stop
    if _merge_thread and _merge_thread.is_alive():
        return
    _merge_stop = False
    _merge_thread = threading.Thread(
        target=_merge_worker, args=(base,), daemon=True, name="hg-merge-queue",
    )
    _merge_thread.start()


def _finish(name: str) -> None:
    """Kết thúc 1 series (xong hoặc lỗi): bỏ current, xoá phase, đếm vào batch, lưu state."""
    global _merge_cur
    with _merge_lock:
        if _merge_cur == name:
            _merge_cur = None
        _merge_phase.clear()
        if _merge_batch:
            last = next((h for h in reversed(_merge_hist) if h.get("name") == name), {})
            _merge_batch["finished"] = int(_merge_batch.get("finished") or 0) + 1
            if last.get("status") != "done":
                _merge_batch["errors"] = int(_merge_batch.get("errors") or 0) + 1
        _save_merge_state()


def _merge_worker(base: str) -> None:
    global _merge_cur, _merge_stop
    root = _output_dir(base)
    while True:
        with _merge_lock:
            if _merge_stop:
                _merge_cur = None
                _merge_phase.clear()
                _save_merge_state()
                return
            if not _merge_q:
                _merge_cur = None
                _merge_phase.clear()
                _save_merge_state()
                return
            name = _merge_q.pop(0)
            _merge_cur = name
            _save_merge_state()
        _set_phase(name, "kiểm tra")

        folder = safe_child(root, name)
        if root and folder is None:
            _hist(name, "error", "tên folder không hợp lệ")
            _finish(name)
            continue
        # đã merge sẵn -> chuẩn hoá nếu lẫn res, rồi dọn mảnh
        if folder and folder.is_dir():
            info = inspect_series_folder(folder)
            if info.get("merged"):
                mf = info.get("merged_file")
                if mf:
                    mp = folder / mf
                    try:
                        _set_phase(name, "kiểm tra độ phân giải file đã gộp")
                        chk = merged_has_mixed_res(mp)
                        if chk.get("mixed"):
                            _hist(name, "normalize",
                                  f"merged lẫn {chk.get('unique')} → 1920x1080")
                            _set_phase(name, "chuẩn hoá file đã gộp → 1080p", 0)
                            nr = normalize_merged_file(
                                mp, MERGE_MIXED_CANVAS,
                                on_pct=lambda p: _set_phase(name, "chuẩn hoá file đã gộp → 1080p", p),
                            )
                            if not nr.get("ok") and not nr.get("skipped"):
                                _hist(name, "error",
                                      f"normalize merged: {nr.get('error')}")
                                _finish(name)
                                continue
                            _hist(name, "normalize",
                                  f"ok {nr.get('target')} · {nr.get('encoder')}")
                    except Exception as e:  # noqa: BLE001
                        _hist(name, "error", f"normalize check: {e}")
                        _finish(name)
                        continue
                _set_phase(name, "dọn tập lẻ → thùng rác")
                r = cleanup_fragments(folder)
                chk = r.get("check") or {}
                if r.get("ok"):
                    mark_done(name, info.get("merged_file"), r.get("removed") or 0)
                    _hist(name, "done", f"already merged · dọn {r.get('removed') or 0} tập → thùng rác")
                    _finish(name)
                    continue
                if not (chk.get("stale") and chk.get("remerge")):
                    _hist(name, "error", r.get("error") or "không dọn được")
                    _finish(name)
                    continue
                # Có tập tải sau khi gộp, tập lẻ vẫn đủ nội dung cũ → cất merged cũ, gộp lại
                _move_to_trash(folder, [folder / info["merged_file"]])
                _hist(name, "remerge", f"{chk.get('reason')} → gộp lại")

        # Tập mảnh lệch res → merge chuẩn hoá (scale+pad), bỏ Hongguo -c copy
        if folder and folder.is_dir():
            _set_phase(name, "kiểm tra độ phân giải từng tập")
            try:
                eres = episode_resolutions(folder)
            except Exception as e:  # noqa: BLE001
                eres = {"mixed": False, "error": str(e)}
            if eres.get("mixed"):
                _hist(name, "normalize",
                      f"eps lẫn {eres.get('unique')} → 1920x1080")
                last_pct = [-1.0]

                def _np(p: float) -> None:
                    _set_phase(name, "gộp + chuẩn hoá 1080p (tập lệch độ phân giải)", p)
                    if p - last_pct[0] >= 5 or p >= 99:
                        last_pct[0] = p
                        _hist(name, "normalize", f"{p:.0f}% → 1920x1080")

                _np(0)
                nr = merge_series_normalized(
                    folder, on_pct=_np, target_wh=MERGE_MIXED_CANVAS,
                )
                if nr.get("ok"):
                    _set_phase(name, "dọn tập lẻ → thùng rác")
                    cr = cleanup_fragments(folder)
                    removed = int(cr.get("removed") or 0)
                    mf = nr.get("merged_file")
                    if cr.get("ok"):
                        mark_done(name, mf, removed)
                        _hist(name, "done",
                              f"{mf} · norm {nr.get('target')} · dọn {removed} tập → thùng rác")
                    else:
                        _hist(name, "error", f"{mf} · {cr.get('error')}")
                    _finish(name)
                    continue
                _hist(name, "error",
                      f"normalize merge: {nr.get('error') or nr}")
                _finish(name)
                continue

        # đợi nếu Hongguo đang merge cái khác
        wait_err = None
        while True:
            if _merge_stop:
                with _merge_lock:
                    _merge_q.insert(0, name)
                    _merge_cur = None
                    _merge_phase.clear()
                    _save_merge_state()
                return
            try:
                ms = merge_status(base)
            except Exception as e:  # noqa: BLE001
                wait_err = str(e)
                break
            if not ms.get("running"):
                break
            if ms.get("name") == name and (
                ms.get("done") or (ms.get("pct") or 0) >= 100
            ):
                break
            _set_phase(name, f"chờ Hongguo gộp xong «{ms.get('name') or '?'}»")
            time.sleep(2.5)
        if wait_err:
            _hist(name, "error", wait_err)
            _finish(name)
            continue

        # start merge (Hongguo lossless copy — chỉ khi mọi tập cùng res)
        _set_phase(name, "Hongguo gộp", 0)
        try:
            r = merge_one(name, base)
            if not r.get("ok") and r.get("busy"):
                with _merge_lock:
                    _merge_q.append(name)
                    if _merge_cur == name:
                        _merge_cur = None
                    _save_merge_state()
                time.sleep(3)
                continue
            if not r.get("ok"):
                _hist(name, "error", r.get("error") or str(r))
                _finish(name)
                continue
        except Exception as e:  # noqa: BLE001
            _hist(name, "error", str(e))
            _finish(name)
            continue

        # poll tới xong
        ok = False
        err = ""
        merged_file = None
        seen_running = False
        t_start = time.time()
        for _ in range(3600):
            if _merge_stop:
                break
            try:
                ms = merge_status(base)
            except Exception as e:  # noqa: BLE001
                err = str(e)
                break
            same = ms.get("name") == name
            if same and ms.get("file"):
                merged_file = ms.get("file")
            if ms.get("running") and same:
                seen_running = True
                _set_phase(name, "Hongguo gộp", ms.get("pct") or 0)
            # done/pct của lần gộp TRƯỚC (series khác) vẫn còn trong status → chỉ tin khi đúng tên
            if same and (ms.get("done") or (not ms.get("running") and (ms.get("pct") or 0) >= 99)):
                ok = not bool(ms.get("error"))
                err = ms.get("error") or ""
                break
            if seen_running and not ms.get("running"):
                ok = not (same and ms.get("error"))
                err = (ms.get("error") or "") if same else ""
                break
            if not seen_running and time.time() - t_start > 120:
                err = "Hongguo không bắt đầu gộp sau 120s"
                break
            time.sleep(2.5)

        removed = 0
        if ok and folder and folder.is_dir():
            # đợi file merged xuất hiện trên đĩa
            for _ in range(30):
                info = inspect_series_folder(folder)
                if info.get("merged"):
                    merged_file = info.get("merged_file") or merged_file
                    break
                time.sleep(1)
            # hậu kiểm: Hongguo copy vẫn có thể lẫn nếu probe lệch
            if merged_file:
                mp = folder / merged_file
                if mp.is_file():
                    try:
                        _set_phase(name, "kiểm tra độ phân giải sau gộp")
                        chk = merged_has_mixed_res(mp)
                        if chk.get("mixed"):
                            _hist(name, "normalize",
                                  f"post-merge lẫn {chk.get('unique')} → 1920x1080")
                            _set_phase(name, "chuẩn hoá sau gộp → 1080p", 0)
                            nr = normalize_merged_file(
                                mp, MERGE_MIXED_CANVAS,
                                on_pct=lambda p: _set_phase(name, "chuẩn hoá sau gộp → 1080p", p),
                            )
                            if nr.get("ok") and not nr.get("skipped"):
                                _hist(name, "normalize",
                                      f"post ok {nr.get('target')}")
                            elif not nr.get("ok") and not nr.get("skipped"):
                                ok = False
                                _hist(name, "error", f"post-norm: {nr.get('error')}")
                    except Exception as e:  # noqa: BLE001
                        ok = False
                        _hist(name, "error", f"post-norm: {e}")
            _set_phase(name, "dọn tập lẻ → thùng rác")
            cr = cleanup_fragments(folder) if ok else {"ok": False, "error": "post-norm lỗi — giữ tập"}
            removed = int(cr.get("removed") or 0)
            if cr.get("merged_file"):
                merged_file = cr["merged_file"]
            if cr.get("ok"):
                mark_done(name, merged_file, removed)
                detail = f"dọn {removed} tập → thùng rác"
                if merged_file:
                    detail = f"{merged_file} · {detail}"
                _hist(name, "done", detail)
            else:
                _hist(name, "error", cr.get("error") or "không dọn được")
        else:
            _hist(name, "done" if ok else "error",
                  err or ("" if ok else "timeout/stopped"))

        _finish(name)


def _hist(name: str, status: str, detail: str = "") -> None:
    with _merge_lock:
        _merge_hist.append({
            "name": name, "status": status, "detail": detail, "at": int(time.time()),
        })
        if len(_merge_hist) > 50:
            del _merge_hist[:-50]
        if status == "done":
            _merge_done.setdefault(name, {"at": int(time.time())})
        _save_merge_state()


# ===== duanjubaike.net — nhận diện version rồi map Hongguo =====

_BAIKE = "https://www.duanjubaike.net"
_BAIKE_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,vi;q=0.8,en;q=0.7",
}


def _baike_get(url: str, timeout: float = 25) -> str:
    req = urllib.request.Request(url, headers=_BAIKE_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _baike_clean_title(t: str) -> str:
    t = (t or "").strip()
    t = re.sub(r"^漫剧《\s*", "", t)
    t = re.sub(r"\s*》\s*$", "", t)
    return t.strip()


def _titles_same_franchise(root: str, other: str) -> bool:
    """True nếu other cùng họ với root (bỏ ranking nhiễu trong 同系列)."""
    a, b = (root or "").strip(), (other or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    ca, cb = franchise_core(a), franchise_core(b)
    if ca and cb and ca == cb:
        return True
    sa, sb = season_stem(a), season_stem(b)
    if sa and sb and (sa == sb or sa in b or sb in a):
        return True
    # chung đoạn Hán đầu (tối thiểu 6 ký tự) — bắt bản đuôi ! / 2 / ？
    han_a = "".join(re.findall(r"[\u4e00-\u9fff]", sa or a))
    han_b = "".join(re.findall(r"[\u4e00-\u9fff]", sb or b))
    if len(han_a) >= 6 and len(han_b) >= 6 and (
        han_a[:8] == han_b[:8] or han_a in han_b or han_b in han_a
    ):
        return True
    return False


def baike_search(q: str, timeout: float = 25) -> list[dict]:
    """Search https://www.duanjubaike.net/so.html?keyword=…"""
    q = (q or "").strip()
    if not q:
        return []
    url = _BAIKE + "/so.html?keyword=" + urllib.parse.quote(q)
    try:
        html = _baike_get(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        print(f"[baike] search fail q={q[:40]!r}: {e}", flush=True)
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'href="(/manju/info-(\d{15,})\.html)"[^>]*title="([^"]*)"',
        html,
    ):
        sid = m.group(2)
        if sid in seen:
            continue
        seen.add(sid)
        title = _baike_clean_title(m.group(3))
        # cover gần link
        pos = m.start()
        sub = html[max(0, pos - 200) : pos + 1200]
        cm = re.search(r'src="(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', sub, re.I)
        if not cm:
            cm = re.search(
                r'src="(https?://[^"]*duanjubaike[^"]+\.jpg[^"]*)"', sub, re.I
            )
        out.append({
            "series_id": sid,
            "title": title,
            "cover": cm.group(1) if cm else None,
            "baike_url": _BAIKE + m.group(1),
            "source": "baike_search",
        })
    # fallback: info-id + alt
    if not out:
        for m in re.finditer(
            r'info-(\d{15,})\.html[\s\S]{0,500}?alt="([^"]+)"', html
        ):
            sid = m.group(1)
            if sid in seen:
                continue
            seen.add(sid)
            out.append({
                "series_id": sid,
                "title": _baike_clean_title(m.group(2)),
                "cover": None,
                "baike_url": f"{_BAIKE}/manju/info-{sid}.html",
                "source": "baike_search",
            })
    return out


def baike_info(series_id: str, timeout: float = 25) -> dict:
    """Trang info-{id}: meta + 同系列 (đã lọc nhiễu ranking).

    Thêm: tags, score, heat, plays, favorites, duration, status, kind.
    """
    sid = str(series_id or "").strip()
    if not sid:
        raise RuntimeError("thiếu series_id")
    url = f"{_BAIKE}/manju/info-{sid}.html"
    html = _baike_get(url, timeout=timeout)
    h1 = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
    title = _baike_clean_title(h1.group(1) if h1 else "")
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))

    em = re.search(r"全\s*(\d+)\s*集", plain)
    episode_cnt = int(em.group(1)) if em else None
    dm = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*上线", plain)
    date = None
    if dm:
        date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"

    # Meta description thường đủ score / duration / heat / plays / fav
    score = heat = plays = favorites = duration = None
    kind = None
    meta = re.search(
        r'content="([^"]*评分[^"]*)"', html
    ) or re.search(r'name="description"\s+content="([^"]+)"', html, re.I)
    meta_txt = meta.group(1) if meta else ""
    sm = re.search(r"评分[为为]?\s*([0-9.]+)\s*分", meta_txt or plain)
    if sm:
        try:
            score = float(sm.group(1))
        except ValueError:
            score = None
    dum = re.search(r"(?:时长为|总时长)\s*([^，,。\s]+(?:小时|分钟|时|分)[^，,。\s]*)",
                    meta_txt or plain)
    if dum:
        duration = dum.group(1).strip()
    else:
        dum2 = re.search(r"总时长</p>\s*<p[^>]*>\s*([^<]+)", html)
        if dum2:
            duration = dum2.group(1).strip()
    hm = re.search(r"([0-9.]+)\s*万热度", meta_txt or plain)
    if hm:
        heat = hm.group(1) + "万"
    pm = re.search(r"([0-9.]+)\s*万播放", meta_txt or plain)
    if pm:
        plays = pm.group(1) + "万"
    else:
        pm2 = re.search(
            r"总播放</p>[\s\S]{0,200}?<p[^>]*>\s*([0-9.]+)\s*</p>\s*<p[^>]*>\s*万",
            html,
        )
        if pm2:
            plays = pm2.group(1) + "万"
    fm = re.search(r"([0-9.]+)\s*万(?:人)?收藏", meta_txt or plain)
    if fm:
        favorites = fm.group(1) + "万"
    if "AI漫剧" in (meta_txt or "") or "AI漫剧" in plain[:800]:
        kind = "AI漫剧"
    elif "漫剧" in (title or ""):
        kind = "漫剧"

    status = None
    if "已完结" in plain[:5000]:
        status = "已完结"
    elif "连载中" in plain[:5000]:
        status = "连载中"

    # Tags: badge sau 分类标签
    tags: list[str] = []
    ti = html.find("分类标签")
    if ti >= 0:
        chunk = html[ti : ti + 2500]
        for bm in re.finditer(
            r'badge[^>]*>\s*([^<]{1,20})\s*</(?:div|span|a)>', chunk, re.I
        ):
            t = bm.group(1).strip()
            if t and t not in tags and t not in ("分类标签", "分类标签："):
                tags.append(t)
            if len(tags) >= 12:
                break

    intro = None
    im = re.search(
        r"剧情介绍[：:]\s*</h3>\s*<div[^>]*>\s*([^<]{20,800})\s*</div>",
        html,
        re.I,
    )
    if im:
        intro = im.group(1).strip()
    if not intro:
        im2 = re.search(
            r"(?:剧情介绍|简介)[：:\s]*([\u4e00-\u9fffA-Za-z0-9，。、；！？“”‘’…\s]{30,400})",
            plain,
        )
        if im2:
            intro = im2.group(1).strip()
    if intro:
        # cắt nhiễu ranking / footer
        for cut in ("漫剧排行榜", "同系列漫剧", "Copyright", "短剧百科"):
            p = intro.find(cut)
            if p > 40:
                intro = intro[:p].strip()
                break

    cover = None
    cm = re.search(
        rf'src="(https?://[^"]*{re.escape(sid)}[^"]*\.jpg[^"]*)"', html, re.I
    )
    if not cm:
        cm = re.search(r'src="(https?://[^"]+\.jpg[^"]*)"', html, re.I)
    if cm:
        cover = cm.group(1)

    related: list[dict] = []
    a = html.find("同系列漫剧")
    if a < 0:
        a = html.find("同系列")
    b = html.find("漫剧排行榜", a) if a >= 0 else -1
    if a < 0:
        chunk = ""
    elif b > a:
        chunk = html[a:b]
    else:
        chunk = html[a : a + 9000]

    seen: set[str] = set()
    root = title or ""
    for m in re.finditer(
        r'href="/manju/info-(\d{15,})\.html"[^>]*title="([^"]+)"',
        chunk,
    ):
        rid, rt = m.group(1), _baike_clean_title(m.group(2))
        if rid in seen:
            continue
        if root and not _titles_same_franchise(root, rt):
            continue
        seen.add(rid)
        sub = chunk[m.start() : m.start() + 1600]
        e2 = re.search(r"全\s*(\d+)\s*集", sub, re.S)
        d2 = re.search(r"(\d{4}-\d{2}-\d{2})", sub)
        related.append({
            "series_id": rid,
            "title": rt,
            "episode_cnt": int(e2.group(1)) if e2 else None,
            "date": d2.group(1) if d2 else None,
            "baike_url": f"{_BAIKE}/manju/info-{rid}.html",
            "source": "baike_related",
        })

    # luôn gồm chính nó
    if sid not in seen:
        related.insert(0, {
            "series_id": sid,
            "title": title,
            "episode_cnt": episode_cnt,
            "date": date,
            "cover": cover,
            "baike_url": url,
            "source": "baike_self",
        })
    else:
        for r in related:
            if r["series_id"] == sid:
                r.setdefault("episode_cnt", episode_cnt)
                r.setdefault("date", date)
                r["cover"] = cover
                break

    return {
        "series_id": sid,
        "title": title,
        "episode_cnt": episode_cnt,
        "date": date,
        "intro": intro,
        "cover": cover,
        "baike_url": url,
        "tags": tags,
        "score": score,
        "heat": heat,
        "plays": plays,
        "favorites": favorites,
        "duration": duration,
        "status": status,
        "kind": kind,
        "related": related,
        "n_related": len(related),
    }


def baike_deep(series_id: str | None = None, title: str | None = None,
               timeout: float = 25) -> dict:
    """Lấy info sâu từ baike: ưu tiên series_id, không có thì search theo title."""
    sid = str(series_id or "").strip()
    tit = str(title or "").strip()
    picked_from = None
    if sid and re.fullmatch(r"\d{15,}", sid):
        try:
            info = baike_info(sid, timeout=timeout)
            if info.get("title") or info.get("intro") or info.get("episode_cnt"):
                info["lookup"] = "id"
                return info
        except Exception as e:  # noqa: BLE001
            picked_from = f"id_fail:{e}"
    if not tit:
        raise RuntimeError("thiếu series_id/title để tra baike")
    hits = baike_search(tit, timeout=timeout)
    if not hits:
        raise RuntimeError(f"baike không thấy: {tit[:60]}")
    qn = re.sub(r"\s+", "", tit)
    def _score(h: dict) -> tuple:
        t = re.sub(r"\s+", "", h.get("title") or "")
        exact = 1 if t == qn else 0
        # ưu tiên đúng id Hongguo nếu trùng
        same_id = 1 if sid and str(h.get("series_id")) == sid else 0
        contain = 1 if qn in t or t in qn else 0
        return (same_id, exact, contain, -abs(len(t) - len(qn)))
    hits_sorted = sorted(hits, key=_score, reverse=True)
    pick = hits_sorted[0]
    info = baike_info(str(pick["series_id"]), timeout=timeout)
    info["lookup"] = "title"
    info["query"] = tit
    info["hongguo_series_id"] = sid or None
    info["baike_hits"] = len(hits_sorted)
    if picked_from:
        info["id_note"] = picked_from
    return info


def baike_versions(q: str, timeout: float = 25) -> dict:
    """Q → search baike → chọn hit khớp nhất → danh sách version (đã lọc)."""
    hits = baike_search(q, timeout=timeout)
    if not hits:
        return {"query": q, "hits": [], "versions": [], "picked": None}
    qn = re.sub(r"\s+", "", q)
    def score(h: dict) -> tuple:
        t = re.sub(r"\s+", "", h.get("title") or "")
        exact = 1 if t == qn else 0
        contain = 1 if qn in t or t in qn else 0
        return (exact, contain, -abs(len(t) - len(qn)))
    hits_sorted = sorted(hits, key=score, reverse=True)
    picked = hits_sorted[0]
    try:
        info = baike_info(picked["series_id"], timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {
            "query": q, "hits": hits_sorted, "picked": picked,
            "versions": [picked], "error": str(e),
        }
    vers = info.get("related") or []
    # nếu related rỗng / chỉ self — vẫn 1 version
    if not vers:
        vers = [{
            "series_id": info["series_id"],
            "title": info.get("title") or picked.get("title"),
            "episode_cnt": info.get("episode_cnt"),
            "date": info.get("date"),
            "cover": info.get("cover") or picked.get("cover"),
            "baike_url": info.get("baike_url"),
            "source": "baike_self",
        }]
    # gắn cover từ picked nếu thiếu
    for v in vers:
        if not v.get("cover") and picked.get("cover"):
            if v.get("series_id") == picked.get("series_id"):
                v["cover"] = picked["cover"]
    return {
        "query": q,
        "hits": hits_sorted,
        "picked": {
            **picked,
            "title": info.get("title") or picked.get("title"),
            "episode_cnt": info.get("episode_cnt"),
            "intro": info.get("intro"),
            "cover": info.get("cover") or picked.get("cover"),
            "date": info.get("date"),
        },
        "versions": vers,
        "n_versions": len(vers),
    }


def _norm_title_key(t: str) -> str:
    t = (t or "").strip().lower()
    t = t.replace("：", ":").replace("！", "!").replace("？", "?")
    t = re.sub(r"\s+", "", t)
    return t


def _hongguo_match_version(
    ver: dict, base: str = DEFAULT_BASE
) -> tuple[dict | None, str]:
    """Map 1 version baike → Hongguo. Ưu tiên series_id, rồi search đúng tên."""
    sid = str(ver.get("series_id") or "").strip()
    title = (ver.get("title") or "").strip()
    # 1) lookup id trực tiếp (baike dùng cùng series_id Hongguo)
    if sid:
        try:
            meta = lookup_series(sid, base)
            ht = (meta.get("title") or "").strip()
            if ht and title and not _titles_same_franchise(title, ht):
                # id trên baike trỏ nhầm / tái sử dụng — thử search theo tên
                id_err = f"id {sid} lệch tên Hongguo: {ht}"
            else:
                out = {
                    **ver,
                    "series_id": str(meta.get("series_id") or sid),
                    "title": ht or title,
                    "episode_cnt": meta.get("episode_cnt") or ver.get("episode_cnt"),
                    "cover": meta.get("cover") or ver.get("cover"),
                    "stem": season_stem(ht or title),
                    "core": franchise_core(ht or title),
                    "hg_match": "id",
                    "baike_title": title,
                }
                _cache_put(out)
                return out, ""
        except Exception as e:  # noqa: BLE001
            id_err = str(e)[:80]
    else:
        id_err = "no id"

    # 2) search đúng title trên Hongguo API + web, lọc tên
    if not title:
        return None, "thiếu title + " + id_err
    want = _norm_title_key(title)
    candidates: list[dict] = []
    try:
        data = _req("GET", base, "/dl/search", query={"q": title})
        rows = []
        if isinstance(data, dict):
            rows = list(data.get("results") or [])
        elif isinstance(data, list):
            rows = data
        for r in rows:
            if isinstance(r, dict):
                candidates.append(r)
    except Exception:  # noqa: BLE001
        pass
    try:
        for r in web_search_hongguoduanju(title):
            candidates.append(r)
    except Exception:  # noqa: BLE001
        pass

    best = None
    best_score = -1
    for r in candidates:
        rt = (r.get("title") or "").strip()
        if not rt:
            continue
        if not _titles_same_franchise(title, rt):
            continue
        rk = _norm_title_key(rt)
        score = 0
        if rk == want:
            score = 100
        elif want in rk or rk in want:
            score = 80
        elif season_stem(title) == season_stem(rt):
            score = 60
        else:
            score = 40
        if str(r.get("series_id") or "") == sid:
            score += 20
        if score > best_score:
            best_score = score
            best = r
    if not best:
        return None, f"Hongguo không thấy «{title}» ({id_err})"
    out = {
        **ver,
        "series_id": str(best.get("series_id") or ""),
        "title": best.get("title") or title,
        "episode_cnt": best.get("episode_cnt") or best.get("total") or ver.get("episode_cnt"),
        "cover": best.get("cover") or ver.get("cover"),
        "stem": season_stem(best.get("title") or title),
        "core": franchise_core(best.get("title") or title),
        "hg_match": "title",
        "baike_title": title,
    }
    _cache_put(out)
    return out, ""


def search_via_baike(q: str, base: str = DEFAULT_BASE) -> dict:
    """Baike nhận diện → versions → match từng cái trên Hongguo.

    Magic (hot-reload khi chưa có /api/hg/baike-info):
      q = ``@@deep@@{series_id}@@{title}`` → trả baike_deep(...).
    """
    q = (q or "").strip()
    if q.startswith("@@deep@@"):
        rest = q[len("@@deep@@"):]
        sid, _sep, tit = rest.partition("@@")
        info = baike_deep(
            series_id=(sid.strip() or None),
            title=(tit.strip() or None),
        )
        info["mode"] = "baike_info"
        return info
    if not q:
        return {"error": "thiếu q", "versions": [], "matched": [], "rejected": []}
    bk = baike_versions(q)
    versions = bk.get("versions") or []
    matched: list[dict] = []
    rejected: list[dict] = []
    for ver in versions:
        hit, reason = _hongguo_match_version(ver, base)
        if hit:
            matched.append(hit)
        else:
            rejected.append({**ver, "reject_reason": reason})
    # Giữ mọi series_id (không gộp 第N季) — baike liệt kê version song song
    matched_sorted = sorted(
        matched,
        key=lambda r: season_timeline_key(
            r.get("title") or "", str(r.get("series_id") or "")
        ),
    )
    labeled = []
    for i, r in enumerate(matched_sorted, 1):
        rr = dict(r)
        rr["ss_index"] = i
        rr["ss_label"] = f"V{i}"
        rr["track"] = "baike"
        labeled.append(rr)
    matched = labeled
    return {
        "query": q,
        "mode": "baike",
        "baike": {
            "picked": bk.get("picked"),
            "hits": bk.get("hits") or [],
            "n_versions": bk.get("n_versions") or len(versions),
            "error": bk.get("error"),
        },
        "versions": versions,
        "matched": matched,
        "rejected": rejected,
        "n_matched": len(matched),
        "n_rejected": len(rejected),
        "groups": [{
            "stem": (bk.get("picked") or {}).get("title") or q,
            "items": matched,
            "n": len(matched),
            "franchise": True,
            "source": "baike",
        }] if matched else [],
        "results": matched,
    }
