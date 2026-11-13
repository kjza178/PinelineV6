"""
Feeder + Run-watch cho Auto-pilot (chạy trong sidecar :8802, chỉ gọi API :8801).

Feeder — khi hàng chờ trống, không job nào chạy/chờ → thêm job mới theo "yêu cầu" (spec):
  lib_merged : file (merged) trong thư viện Hongguo chưa làm job
  auto_merge : ghép các folder tập lẻ đã tải đủ
  hongguo    : tải bộ mới từ bảng hot / từ khoá
  Tải + ghép chạy trước (prefetch) lúc job còn chạy để khi hết job là có phim thêm ngay.
  Yêu cầu bằng lời (/feed <câu>) → LLM agent dịch thành spec.

Run-watch — job đang chạy không có dấu hiệu sống (tiến độ, file ghi, CPU server) quá ngưỡng
  → báo Telegram + stop + xếp lại đầu hàng (tối đa RUNWATCH_MAX_HEAL lần / job·stage).
"""
from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

import agents.agent_autopilot as ap
import core.film_db as film_db

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
STATE_PATH = WORK / "logs" / "feeder_state.json"

SOURCES = ("lib_merged", "auto_merge", "hongguo")
DEFAULT_SPEC: dict[str, Any] = {
    "sources": ["lib_merged", "auto_merge", "hongguo"],
    "mode": "count",          # count: n phim · series: n bộ (mọi mùa có sẵn) · all: hết phim có sẵn
    "n": 3,
    "include": [],            # từ khoá tên phim phải có (1 trong các từ)
    "exclude": [],            # từ khoá loại bỏ
    "order": "smart",         # smart (điểm gợi ý) | oldest | newest | name — thứ tự chọn giữa các bộ
    "hg_category": "all",     # all | ai | human | comic
    "hg_board": "recommend",
    "hg_query": "",           # có từ khoá → tìm Hongguo theo từ khoá thay vì bảng hot
    "hg_min_score": 0.0,
    "hg_max_per_day": 3,      # tối đa số bộ tải mới / ngày
}

STUCK_MIN = float(os.getenv("RUNWATCH_STUCK_MIN") or "30")
STUCK_MIN_STAGE = {"asr": 45.0, "download": 45.0}
MAX_HEAL = int(os.getenv("RUNWATCH_MAX_HEAL") or "2")
CPU_ALIVE_FRAC = 0.05  # ≥5% một core trong khoảng quan sát = còn làm việc


def _now_s() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ─── state ───────────────────────────────────────────────────────────────

def load_state() -> dict:
    st: dict[str, Any] = {}
    if STATE_PATH.is_file():
        try:
            st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            st = {}
    st.setdefault("enabled", False)
    st["spec"] = normalize_spec(st.get("spec") or {})
    st.setdefault("request_text", "")
    st.setdefault("downloads", {})     # series_id → {title, at}
    st.setdefault("merges", {})        # name → at
    st.setdefault("fed", [])           # [{at, job_ids, titles}]
    st.setdefault("runwatch", {})
    st.setdefault("priority", [])      # bộ (core) ưu tiên, theo thứ tự
    st.setdefault("skip", [])          # bộ feeder không tự lấy
    return st


def save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    st["fed"] = st.get("fed", [])[-50:]
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_spec(d: dict) -> dict:
    s = dict(DEFAULT_SPEC)
    for k, v in (d or {}).items():
        if k in DEFAULT_SPEC and v is not None:
            s[k] = v
    s["sources"] = [x for x in (s.get("sources") or []) if x in SOURCES] or ["lib_merged"]
    s["mode"] = s["mode"] if s["mode"] in ("count", "series", "all") else "count"
    try:
        s["n"] = max(1, min(50, int(s["n"])))
    except (TypeError, ValueError):
        s["n"] = DEFAULT_SPEC["n"]
    for k in ("include", "exclude"):
        v = s.get(k) or []
        if isinstance(v, str):
            v = [x for x in re.split(r"[,;|]", v)]
        s[k] = [str(x).strip() for x in v if str(x).strip()]
    s["order"] = s["order"] if s["order"] in ("smart", "oldest", "newest", "name") else "smart"
    s["hg_category"] = s["hg_category"] if s["hg_category"] in ("all", "ai", "human", "comic") else "all"
    s["hg_board"] = str(s.get("hg_board") or "recommend")
    s["hg_query"] = str(s.get("hg_query") or "").strip()
    try:
        s["hg_min_score"] = max(0.0, min(10.0, float(s["hg_min_score"] or 0)))
    except (TypeError, ValueError):
        s["hg_min_score"] = 0.0
    try:
        s["hg_max_per_day"] = max(0, min(30, int(s["hg_max_per_day"])))
    except (TypeError, ValueError):
        s["hg_max_per_day"] = DEFAULT_SPEC["hg_max_per_day"]
    return s


def describe_spec(s: dict) -> str:
    src = {"lib_merged": "phim đã ghép", "auto_merge": "tự ghép tập lẻ", "hongguo": "tải mới Hongguo"}
    qty = {"count": f"{s['n']} phim/lần", "series": f"{s['n']} bộ (đủ các mùa có sẵn)/lần",
           "all": "tất cả phim có sẵn"}[s["mode"]]
    lines = [
        f"Nguồn: {', '.join(src[x] for x in s['sources'])}",
        f"Mỗi lần hết job thêm: {qty} · thứ tự bộ: " + {
            "smart": "gợi ý thông minh (nối bộ đang làm, mùa kế tiếp, ★ ưu tiên)", "oldest": "cũ nhất trước",
            "newest": "mới nhất trước", "name": "theo tên"}[s["order"]],
    ]
    if "lib_merged" not in s["sources"]:
        lines.append("Chỉ thêm phim do feeder tự tải/ghép (không lấy phim cũ trong thư viện)")
    if s["include"]:
        lines.append(f"Chỉ lấy tên có: {', '.join(s['include'])}")
    if s["exclude"]:
        lines.append(f"Bỏ tên có: {', '.join(s['exclude'])}")
    if "hongguo" in s["sources"]:
        how = f"từ khoá «{s['hg_query']}»" if s["hg_query"] else f"bảng hot {s['hg_category']}/{s['hg_board']}"
        sc = f" · điểm ≥ {s['hg_min_score']}" if s["hg_min_score"] else ""
        lines.append(f"Hongguo: {how}{sc} · tối đa {s['hg_max_per_day']} bộ/ngày")
    return "\n".join(lines)


# ─── HTTP helpers ────────────────────────────────────────────────────────

def _get(base: str, path: str, timeout: float = 30, **params) -> dict:
    r = requests.get(base.rstrip("/") + path, params=params or None, timeout=timeout)
    r.raise_for_status()
    return r.json() if r.content else {}


def _post(base: str, path: str, body: dict | None = None, timeout: float = 120) -> dict:
    return ap._post(base, path, body, timeout=timeout)


# ─── thư viện + job đã có ────────────────────────────────────────────────

def _hg():
    import agents.hongguo_bridge as hg  # noqa: PLC0415
    return hg


def _job_manifests() -> list[tuple[str, dict]]:
    out = []
    if not WORK.is_dir():
        return out
    for d in WORK.iterdir():
        mf = d / "manifest.json"
        if not mf.is_file():
            continue
        try:
            out.append((d.name, json.loads(mf.read_text(encoding="utf-8"))))
        except Exception:  # noqa: BLE001
            continue
    return out


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p or ""))


def imported_paths() -> set[str]:
    """Nguồn đã làm: job hiện có + database (kể cả job đã xoá thư mục)."""
    live = {_norm((m.get("request") or {}).get("orig_path") or "") for _, m in _job_manifests()}
    try:
        live |= film_db.source_keys()
    except Exception:  # noqa: BLE001
        pass
    return live - {""}


def lib_root(spec: dict | None = None) -> Path | None:
    try:
        hg = _hg()
        p = hg._output_dir(hg.DEFAULT_BASE)
        if p and Path(p).is_dir():
            return Path(p)
    except Exception:  # noqa: BLE001
        pass
    for _, m in _job_manifests():
        op = (m.get("request") or {}).get("orig_path")
        if op and Path(op).parent.parent.is_dir():
            return Path(op).parent.parent
    return _machine.library_dir() if _machine.library_dir().is_dir() else None


def _match(spec: dict, *texts: str) -> bool:
    t = " ".join(x for x in texts if x).lower()
    if spec["include"] and not any(k.lower() in t for k in spec["include"]):
        return False
    return not any(k.lower() in t for k in spec["exclude"])


def library(base: str) -> list[dict]:
    items = list((_get(base, "/api/hg/library", timeout=90) or {}).get("items") or [])
    try:
        hg = _hg()
        root = lib_root()
        if root and root.is_dir():
            seen_names = {str(it.get("name") or "") for it in items}
            for d in root.iterdir():
                if not d.is_dir() or d.name in seen_names or d.name.startswith("."):
                    continue
                info = hg.inspect_series_folder(d)
                if info.get("merged") and info.get("merged_file"):
                    title = d.name
                    s_id = ""
                    sj = d / ".series.json"
                    if sj.is_file():
                        try:
                            meta = json.loads(sj.read_text(encoding="utf-8"))
                            title = str(meta.get("title") or d.name)
                            s_id = str(meta.get("series_id") or "")
                        except Exception:
                            pass
                    items.append({
                        "name": d.name,
                        "title": title,
                        "series_id": s_id,
                        **info,
                    })
    except Exception:  # noqa: BLE001
        pass
    return items


def candidates(base: str, st: dict, items: list[dict] | None = None, *, manual: bool = False) -> list[dict]:
    """Phim (merged) chưa làm job, đã lọc + sắp: gom theo bộ, trong bộ theo thứ tự mùa.
    Không có nguồn lib_merged → chỉ phim feeder tự tải/ghép. manual=True: bỏ lọc (thêm tay từ tab thư viện)."""
    hg = _hg()
    spec = st["spec"]
    root = lib_root(spec)
    if root is None:
        return []
    done = imported_paths()
    own_only = "lib_merged" not in spec["sources"] and not manual
    skip = set(st.get("skip") or [])
    items = library(base) if items is None else items

    # Bỏ qua các mùa đã bị chặn bản quyền YouTube (Content ID Block)
    try:
        import core.film_db as film_db
        lib_blocks = film_db.list_library_blocks()
    except Exception:
        lib_blocks = {}

    out = []
    for it in items:
        # Không bao giờ chọn mùa đã bị Block bản quyền trên YouTube
        blk = lib_blocks.get(it["name"])
        if blk and blk.get("status") in ("blocked", "partially_blocked"):
            continue
        mf = it.get("merged_file")
        if not it.get("merged") or not mf:
            continue
        if own_only and str(it.get("series_id") or "") not in st["downloads"] and it["name"] not in st["merges"]:
            continue
        p = root / it["name"] / mf
        if not p.is_file() or _norm(str(p)) in done:
            continue
        title = it.get("title") or it["name"]
        core = hg.franchise_core(title)
        if not manual and (core in skip or not _match(spec, it["name"], title)):
            continue
        out.append({
            "name": it["name"], "title": title, "path": str(p), "series_id": str(it.get("series_id") or ""),
            "core": core, "key": hg.season_timeline_key(title, it.get("series_id")),
            "mtime": p.stat().st_mtime,
        })
    groups: dict[str, list[dict]] = {}
    for c in out:
        groups.setdefault(c["core"], []).append(c)
    for g in groups.values():
        g.sort(key=lambda c: c["key"])
    order = list(groups.values())
    if spec["order"] == "name":
        order.sort(key=lambda g: g[0]["core"])
    elif spec["order"] == "smart":
        info = _db_series_info()
        order.sort(key=lambda g: -group_score(g[0]["core"], g, st, info)[0])
    else:
        order.sort(key=lambda g: min(c["mtime"] for c in g), reverse=spec["order"] == "newest")
    prio = list(st.get("priority") or [])
    order.sort(key=lambda g: prio.index(g[0]["core"]) if g[0]["core"] in prio else len(prio))
    return [c for g in order for c in g]


# ─── chấm điểm gợi ý + tab thư viện ──────────────────────────────────────

def _db_series_info() -> dict[str, dict]:
    """series → {done, uploaded, max_season} từ database phim."""
    out: dict[str, dict] = {}
    try:
        for r in film_db.query(limit=5000):
            d = out.setdefault(r["series"] or "", {"done": 0, "uploaded": 0, "max_season": 0, "jobs": 0})
            d["jobs"] += 1
            if r["status"] == "done":
                d["done"] += 1
                d["max_season"] = max(d["max_season"], int(r.get("season") or 0))
            if r.get("yt_url"):
                d["uploaded"] += 1
    except Exception:  # noqa: BLE001
        pass
    return out


def group_score(core: str, seasons: list[dict], st: dict, info: dict) -> tuple[float, list[str]]:
    """Điểm gợi ý 1 bộ: nối mạch bộ đang làm > mùa kế tiếp > đủ mùa sẵn > mới tải."""
    score, why = 0.0, []
    d = info.get(core) or {}
    if d.get("done"):
        score += 40
        why.append(f"đang làm dở bộ này ({d['done']} phần xong)")
        if d.get("uploaded"):
            score += 20
            why.append(f"{d['uploaded']} phần đã lên kênh — khán giả chờ phần tiếp")
        nums = [int(_season_no(s)) for s in seasons if _season_no(s)]
        if nums and min(nums) == d.get("max_season", 0) + 1:
            score += 20
            why.append(f"đúng mùa kế tiếp (S{min(nums)})")
    ready = [s for s in seasons if s.get("state", "ready") == "ready"]
    if ready and len(ready) == len(seasons):
        score += 10
        why.append("mọi mùa đã ghép sẵn")
    elif len(ready) < len(seasons):
        score -= 5
        why.append(f"{len(seasons) - len(ready)} mùa còn chờ ghép")
    if len(seasons) >= 3:
        score += 5
        why.append(f"bộ dài {len(seasons)} mùa — đủ nội dung cho kênh")
    mt = max((s.get("mtime") or 0) for s in seasons)
    if mt:
        days = (time.time() - mt) / 86400
        if days < 10:
            score += round(10 - days, 1)
            why.append(f"tải {days:.0f} ngày trước")
    prio = list(st.get("priority") or [])
    if core in prio:
        score += 1000 - prio.index(core)
        why.insert(0, "★ bạn đã đánh dấu ưu tiên")
    return score, why


def _season_no(s: dict) -> int:
    k = s.get("key")
    return int(k[2]) if k and len(k) > 2 and k[2] else 0


def _probe_dur(path: str, st: dict) -> float | None:
    cache = st.setdefault("probe", {})
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    hit = cache.get(path)
    if hit and hit.get("mtime") == mt:
        return hit.get("dur")
    import subprocess  # noqa: PLC0415
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        dur = float(out) if out else None
    except Exception:  # noqa: BLE001
        dur = None
    cache[path] = {"mtime": mt, "dur": dur}
    return dur


def process_ratio() -> float:
    """Thời gian xử lý / thời lượng phim — trung vị từ lịch sử các job xong liên tiếp."""
    try:
        rows = [r for r in film_db.query(status="done", limit=500) if r.get("finished_at") and r.get("duration_sec")]
    except Exception:  # noqa: BLE001
        rows = []
    # finished_at trong DB cũ lẫn ISO / epoch dạng chữ → quy về epoch rồi mới sắp và trừ (core/timeutil).
    from core.timeutil import to_epoch  # noqa: PLC0415
    timed = [(t, r) for t, r in ((to_epoch(r["finished_at"]), r) for r in rows) if t is not None]
    timed.sort(key=lambda x: x[0])
    ratios = []
    for (ta, _a), (tb, b) in zip(timed, timed[1:]):
        gap = tb - ta
        if 600 < gap < 12 * 3600:
            ratios.append(gap / float(b["duration_sec"]))
    if not ratios:
        return 2.0
    ratios.sort()
    return round(ratios[len(ratios) // 2], 2)


def _translate(base: str, titles: list[str], st: dict) -> dict[str, str]:
    cache = st.setdefault("tr", {})
    miss = [t for t in dict.fromkeys(titles) if t and t not in cache]
    if miss:
        try:
            r = _post(base, "/api/hg/translate-titles", {"items": [{"title": t} for t in miss]}, timeout=120)
            for it in r.get("items") or []:
                if it.get("title_vi"):
                    cache[it["title"]] = it["title_vi"]
        except Exception:  # noqa: BLE001
            pass
    return {t: cache.get(t, "") for t in titles}


def library_view(base: str) -> dict:
    """Tab «Thư viện chưa làm»: bộ có mùa ghép sẵn chưa làm job hoặc còn tập lẻ chờ ghép, kèm điểm gợi ý."""
    hg = _hg()
    st = load_state()
    root = lib_root(st["spec"])
    items = library(base)
    done = imported_paths()
    hg_st = _hg_status(base)
    downloading = _downloading_sids(hg_st)
    groups: dict[str, dict] = {}
    for it in items:
        title = it.get("title") or it["name"]
        core = hg.franchise_core(title)
        s = {"name": it["name"], "title": title, "series_id": str(it.get("series_id") or ""),
             "key": hg.season_timeline_key(title, it.get("series_id"))}
        if it.get("merged") and it.get("merged_file") and root:
            p = root / it["name"] / it["merged_file"]
            if _norm(str(p)) in done:
                s["state"] = "done"
            else:
                s.update(state="ready", path=str(p), mtime=p.stat().st_mtime if p.is_file() else 0,
                         size=p.stat().st_size if p.is_file() else 0)
        elif it.get("fragments"):
            total, fr = int(it.get("total") or 0), int(it.get("fragments") or 0)
            state = "downloading" if s["series_id"] in downloading else (
                "need_merge" if not total or fr >= total else "partial")
            s.update(state=state, fragments=fr, total=total)
            try:
                s["mtime"] = (root / it["name"]).stat().st_mtime if root else 0
            except OSError:
                s["mtime"] = 0
        else:
            continue
        g = groups.setdefault(core, {"core": core, "seasons": []})
        g["seasons"].append(s)
    info = _db_series_info()
    ratio = process_ratio()
    out = []
    for core, g in groups.items():
        todo = [s for s in g["seasons"] if s["state"] != "done"]
        if not todo:
            continue
        g["seasons"].sort(key=lambda s: s["key"])
        for s in g["seasons"]:
            s["season"] = _season_no(s)
            if s["state"] == "ready":
                s["dur"] = _probe_dur(s["path"], st)
        score, why = group_score(core, todo, st, info)
        dur = sum(s.get("dur") or 0 for s in todo if s["state"] == "ready")
        g.update(score=round(score, 1), reasons=why, n_todo=len(todo),
                 n_ready=sum(1 for s in todo if s["state"] == "ready"),
                 n_done=sum(1 for s in g["seasons"] if s["state"] == "done"),
                 dur=dur, est_sec=dur * ratio, priority=core in (st.get("priority") or []),
                 skipped=core in (st.get("skip") or []), poster=todo[0]["name"])
        for s in g["seasons"]:
            s.pop("key", None)
        out.append(g)
    tr = _translate(base, [g["core"] for g in out] + [s["title"] for g in out for s in g["seasons"]], st)
    # Gắn trạng thái bản quyền từ films.db (library_blocks)
    try:
        import core.film_db as film_db
        lib_blocks = film_db.list_library_blocks()
    except Exception:
        lib_blocks = {}

    for g in out:
        for s in g["seasons"]:
            s["title_vi"] = tr.get(s["title"], "")
            blk = lib_blocks.get(s["name"])
            if blk:
                s["block_status"] = blk.get("status") or "unknown"
                s["block_reason"] = blk.get("reason") or ""
                s["test_yt_url"] = blk.get("test_yt_url") or ""
                s["test_sample_path"] = blk.get("test_sample_path") or ""
                s["recheck_at"] = blk.get("recheck_at") or ""
            else:
                s["block_status"] = "unknown"
                s["block_reason"] = ""
                s["test_yt_url"] = ""
                s["test_sample_path"] = ""
                s["recheck_at"] = ""
        # Tổng hợp trạng thái block cho cả bộ
        statuses = [s["block_status"] for s in g["seasons"]]
        if "blocked" in statuses:
            g["block_status"] = "blocked"
        elif "partially_blocked" in statuses:
            g["block_status"] = "partially_blocked"
        elif "claim" in statuses:
            g["block_status"] = "claim"
        elif any(st == "clean" for st in statuses):
            g["block_status"] = "clean"
        elif any(st == "testing" for st in statuses):
            g["block_status"] = "testing"
        else:
            g["block_status"] = "unknown"

        # tên bộ = tiêu đề mùa đầu (đủ ngữ cảnh hơn tên gốc rút gọn, vd 聚宝仙盆 → «dồi dào»)
        first = next((s["title_vi"] for s in g["seasons"]
                      if s["title_vi"] and not re.search(r"mùa thứ|mùa \d|phần \d", s["title_vi"], re.I)), "")
        g["core_short_vi"] = tr.get(g["core"], "")
        g["core_vi"] = first or g["core_short_vi"]
    out.sort(key=lambda g: (g["skipped"], -g["score"]))
    save_state(st)
    ready = [g for g in out if not g["skipped"]]
    return {
        "groups": out, "ratio": ratio,
        "summary": {
            "series": len(ready), "ready": sum(g["n_ready"] for g in ready),
            "need_merge": sum(1 for g in ready for s in g["seasons"] if s["state"] == "need_merge"),
            "downloading": sum(1 for g in ready for s in g["seasons"] if s["state"] in ("downloading", "partial")),
            "hours": round(sum(g["dur"] for g in ready) / 3600, 1),
            "est_days": round(sum(g["est_sec"] for g in ready) / 86400, 1),
            "skipped": len(out) - len(ready),
        },
        "feeder": {"enabled": st["enabled"], "order": st["spec"]["order"], "priority": st.get("priority") or []},
    }


def library_action(base: str, action: str, names: list[str] | None = None, cores: list[str] | None = None) -> dict:
    st = load_state()
    names, cores = list(names or []), list(cores or [])
    if action in ("priority", "unpriority", "skip", "unskip"):
        key = "priority" if "priority" in action else "skip"
        cur = list(st.get(key) or [])
        if action.startswith("un"):
            cur = [c for c in cur if c not in cores]
        else:
            cur += [c for c in cores if c not in cur]
        st[key] = cur
        if key == "priority":
            st["skip"] = [c for c in st.get("skip") or [] if c not in cores]
        save_state(st)
        return {"ok": True, key: cur}
    if action == "merge":
        r = _post(base, "/api/hg/merge", {"names": names}, timeout=60)
        for n in r.get("queued") or []:
            st["merges"][n] = _now_s()
        save_state(st)
        return {"ok": not r.get("error"), **r}
    if action == "add":
        want = set(names)
        cands = candidates(base, st, manual=True)
        chosen = [c for c in cands if c["name"] in want or c["core"] in cores]
        if not chosen:
            return {"ok": False, "error": "không có phim ghép sẵn nào khớp (hoặc phim đã bị chặn bản quyền)"}
        r = add_jobs(base, chosen)
        return {"ok": bool(r["job_ids"]), **r}
    if action in ("delete_local", "delete_blocked_local"):
        import shutil
        hg = _hg()
        root = lib_root(st.get("spec"))
        if not root or not root.is_dir():
            return {"ok": False, "error": f"Không tìm thấy thư mục thư viện ({root})"}

        try:
            import core.film_db as film_db
            lib_blocks = film_db.list_library_blocks()
        except Exception:
            lib_blocks = {}

        to_delete = set(names)
        if cores:
            for it in library(base):
                title = it.get("title") or it["name"]
                core = hg.franchise_core(title)
                if core in cores:
                    blk = lib_blocks.get(it["name"])
                    if action == "delete_blocked_local":
                        if blk and blk.get("status") in ("blocked", "partially_blocked"):
                            to_delete.add(it["name"])
                    else:
                        to_delete.add(it["name"])

        deleted = []
        errors = []
        freed_bytes = 0

        def _robust_remove(folder: Path):
            import stat
            import subprocess
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

        for nm in to_delete:
            p = root / nm
            if p.is_dir():
                try:
                    for f in p.rglob("*"):
                        if f.is_file():
                            try:
                                freed_bytes += f.stat().st_size
                            except OSError:
                                pass
                except Exception:
                    pass
                try:
                    _robust_remove(p)
                    if p.exists():
                        errors.append(f"{nm}: Không thể xoá thư mục (đang bị ứng dụng khác khóa)")
                    else:
                        deleted.append(nm)
                except Exception as e:
                    errors.append(f"{nm}: {e}")
            else:
                deleted.append(nm)

            # Xoá file clip mẫu 90s tạm nếu có
            try:
                sample_file = ROOT / "data" / "samples" / f"{nm}_sample90s.mp4"
                if sample_file.is_file():
                    sample_file.unlink(missing_ok=True)
            except Exception:
                pass

            # Xoá bản ghi khỏi database library_blocks
            try:
                film_db.delete_library_block(nm)
            except Exception:
                pass

        freed_mb = round(freed_bytes / (1024 * 1024), 1)
        freed_gb = round(freed_mb / 1024, 2)
        return {
            "ok": True,
            "deleted": deleted,
            "count": len(deleted),
            "freed_mb": freed_mb,
            "freed_gb": freed_gb,
            "errors": errors,
        }
    if action in ("combine_seasons", "combine_and_add"):
        return combine_library_seasons(
            base,
            names=names,
            cores=cores,
            move_to_trash=True,
            add_to_queue=(action == "combine_and_add"),
        )
    if action in ("combine_rendered_jobs", "combine_rendered_jobs_and_pub"):
        custom_title = cores[0] if cores else None
        return combine_rendered_jobs(
            base,
            job_ids=names,
            custom_title_vi=custom_title,
            auto_publish=(action == "combine_rendered_jobs_and_pub"),
        )
    return {"ok": False, "error": f"action lạ: {action}"}


# ─── Gộp nhiều mùa (D:\Lib) hoặc gộp nhiều Job đã render xong ─────────────

def _probe_stream_meta(path: Path) -> dict:
    """Lấy width, height, vcodec, duration của 1 file video."""
    import subprocess
    meta = {"width": 0, "height": 0, "vcodec": "", "duration": 0.0}
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,codec_name:format=duration",
                "-of", "json",
                str(path),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        data = json.loads(r.stdout or "{}")
        streams = data.get("streams") or []
        if streams:
            st0 = streams[0]
            meta["width"] = int(st0.get("width") or 0)
            meta["height"] = int(st0.get("height") or 0)
            meta["vcodec"] = str(st0.get("codec_name") or "").lower()
        fmt = data.get("format") or {}
        if fmt.get("duration"):
            meta["duration"] = float(fmt["duration"])
    except Exception:  # noqa: BLE001
        pass
    return meta


def _concat_videos_smart(inputs: list[Path], out_mp4: Path) -> dict:
    """Nối danh sách file MP4 thành 1 file out_mp4 duy nhất.
    - Nếu tất cả cùng độ phân giải (width, height) và codec h264:
      Remux qua MPEG-TS tạm (-c:v copy -bsf:v h264_mp4toannexb -c:a aac -b:a 192k -ar 48000 -ac 2)
      rồi concat TS -> MP4 (-c copy -bsf:a aac_adtstoasc -movflags +faststart).
      Cách này giữ nguyên 100% chất lượng hình ảnh (tốc độ ổ cứng, vài giây/phần)
      và triệt tiêu hoàn toàn lỗi lệch time_base (stts压缩) giữa các file MP4.
    - Nếu các phần lệch độ phân giải (vd 720p lẫn 1080p, hoặc 1882x1080 lẫn 1920x1080):
      Chỉ encode lại phần lệch khung; phần đã đúng W×H + h264 giữ -c:v copy (chỉ chỉnh audio),
      rồi concat -c copy. Tránh encode lại cả loạt khi chỉ 1 file lệch vài pixel.
    """
    import shutil
    import subprocess
    import tempfile

    valid = [Path(p) for p in inputs if p and Path(p).is_file()]
    if len(valid) < 2:
        return {"ok": False, "error": "Cần ít nhất 2 file video hợp lệ để gộp"}

    metas = [_probe_stream_meta(p) for p in valid]
    wh_set = {(m["width"], m["height"]) for m in metas if m["width"] > 0 and m["height"] > 0}
    all_h264 = all(m["vcodec"] == "h264" for m in metas)
    uniform = (len(wh_set) == 1) and all_h264

    if wh_set:
        tw, th = max(wh_set, key=lambda wh: wh[0] * wh[1])
    else:
        tw, th = 1080, 1920
    tw, th = tw - (tw % 2), th - (th % 2)

    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tempfile.mkdtemp(prefix="pineline_concat_"))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        if uniform:
            ts_parts: list[Path] = []
            for idx, src in enumerate(valid):
                ts_p = tmp_root / f"part_{idx:04d}.ts"
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(src),
                    "-c:v", "copy", "-bsf:v", "h264_mp4toannexb",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-f", "mpegts", str(ts_p),
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags)
                if r.returncode != 0 or not ts_p.is_file() or ts_p.stat().st_size < 1024:
                    uniform = False
                    break
                ts_parts.append(ts_p)

            if uniform and len(ts_parts) == len(valid):
                list_txt = tmp_root / "list_ts.txt"
                with list_txt.open("w", encoding="utf-8") as f:
                    for p in ts_parts:
                        esc = p.as_posix().replace("'", "'\\''")
                        f.write(f"file '{esc}'\n")
                partial_mp4 = out_mp4.with_suffix(".partial.mp4")
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(list_txt),
                    "-c", "copy", "-bsf:a", "aac_adtstoasc",
                    "-movflags", "+faststart",
                    str(partial_mp4),
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags)
                if r.returncode == 0 and partial_mp4.is_file() and partial_mp4.stat().st_size > 1024:
                    if out_mp4.exists():
                        out_mp4.unlink(missing_ok=True)
                    partial_mp4.replace(out_mp4)
                    out_meta = _probe_stream_meta(out_mp4)
                    return {
                        "ok": True,
                        "path": str(out_mp4),
                        "mode": "lossless_ts_copy",
                        "width": out_meta["width"] or tw,
                        "height": out_meta["height"] or th,
                        "duration_sec": round(out_meta["duration"] or sum(m["duration"] for m in metas), 2),
                        "size_mb": round(out_mp4.stat().st_size / (1024 * 1024), 1),
                        "part_durations": [m["duration"] for m in metas],
                    }

        # Fallback: chỉ encode phần lệch W×H; phần đã khớp giữ -c:v copy (audio AAC chuẩn hoá).
        hg = _hg()
        vargs = hg._pick_h264_encoder()
        vf = hg._scale_vf(tw, th)
        norm_parts: list[Path] = []
        reencoded = 0
        copied = 0
        for idx, src in enumerate(valid):
            part_p = tmp_root / f"norm_{idx:04d}.mp4"
            m = metas[idx] if idx < len(metas) else {}
            same_wh = int(m.get("width") or 0) == tw and int(m.get("height") or 0) == th
            can_copy = same_wh and (m.get("vcodec") == "h264")
            if can_copy:
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(src),
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-movflags", "+faststart",
                    str(part_p),
                ]
                mode_i = "copy"
            else:
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(src),
                    "-vf", vf, *vargs, "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-movflags", "+faststart",
                    str(part_p),
                ]
                mode_i = "reencode"
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags)
            if r.returncode != 0 or not part_p.is_file() or part_p.stat().st_size < 1024:
                # Copy fail (timebase/bitstream) → encode phần đó
                if mode_i == "copy":
                    cmd = [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(src),
                        "-vf", vf, *vargs, "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                        "-movflags", "+faststart",
                        str(part_p),
                    ]
                    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags)
                    mode_i = "reencode_fallback"
            if r.returncode != 0 or not part_p.is_file() or part_p.stat().st_size < 1024:
                err = (r.stderr or r.stdout or "")[-300:]
                return {"ok": False, "error": f"Lỗi chuẩn hoá {src.name}: {err or r.returncode}"}
            if mode_i.startswith("reencode"):
                reencoded += 1
            else:
                copied += 1
            norm_parts.append(part_p)

        list_txt = tmp_root / "list_mp4.txt"
        with list_txt.open("w", encoding="utf-8") as f:
            for p in norm_parts:
                esc = p.as_posix().replace("'", "'\\''")
                f.write(f"file '{esc}'\n")
        partial_mp4 = out_mp4.with_suffix(".partial.mp4")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_txt),
            "-c", "copy", "-movflags", "+faststart",
            str(partial_mp4),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags)
        if r.returncode != 0 or not partial_mp4.is_file() or partial_mp4.stat().st_size < 1024:
            err = (r.stderr or r.stdout or "")[-300:]
            return {"ok": False, "error": f"Lỗi nối file: {err or r.returncode}"}
        if out_mp4.exists():
            out_mp4.unlink(missing_ok=True)
        partial_mp4.replace(out_mp4)
        out_meta = _probe_stream_meta(out_mp4)
        return {
            "ok": True,
            "path": str(out_mp4),
            "mode": "normalized_canvas_partial" if reencoded and copied else ("normalized_canvas" if reencoded else "copy_remux"),
            "parts_reencoded": reencoded,
            "parts_copied": copied,
            "width": out_meta["width"] or tw,
            "height": out_meta["height"] or th,
            "duration_sec": round(out_meta["duration"] or sum(m["duration"] for m in metas), 2),
            "size_mb": round(out_mp4.stat().st_size / (1024 * 1024), 1),
            "part_durations": [m["duration"] for m in metas],
        }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _sanitize_folder_name(name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*]+', " ", (name or "").strip())
    return re.sub(r"\s+", " ", s).strip(" .")


def combine_library_seasons(
    base: str,
    names: list[str] | None = None,
    cores: list[str] | None = None,
    move_to_trash: bool = True,
    add_to_queue: bool = False,
) -> dict:
    """Gộp nhiều mùa (SS1 + SS2 + SS3...) trong thư viện D:\\Lib thành 1 thư mục phim duy nhất
    trước khi Import vào Pineline, tránh tốn công render và đăng lẻ tẻ các mùa ngắn.
    """
    import shutil
    st = load_state()
    hg = _hg()
    root = lib_root(st.get("spec"))
    if not root or not root.is_dir():
        return {"ok": False, "error": f"Không tìm thấy thư mục thư viện ({root})"}

    names_set = set(names or [])
    cores_set = set(cores or [])
    if not names_set and not cores_set:
        return {"ok": False, "error": "Chưa chọn mùa hoặc bộ phim nào để gộp"}

    all_cands = candidates(base, st, manual=True)
    matched = [c for c in all_cands if c["name"] in names_set or c["core"] in cores_set]
    if len(matched) < 2:
        return {"ok": False, "error": f"Chỉ tìm thấy {len(matched)} mùa sẵn sàng (đã ghép tập lẻ). Cần ít nhất 2 mùa ready để gộp!"}

    by_core: dict[str, list[dict]] = {}
    for c in matched:
        by_core.setdefault(c["core"], []).append(c)

    groups_to_merge: list[tuple[str, list[dict]]] = []
    for core, items in by_core.items():
        if len(items) >= 2:
            items.sort(key=lambda x: x["key"])
            groups_to_merge.append((core, items))

    if not groups_to_merge and names and len(matched) >= 2:
        order_map = {nm: idx for idx, nm in enumerate(names)}
        matched.sort(key=lambda x: (order_map.get(x["name"], 999), x["key"]))
        groups_to_merge.append((matched[0]["core"], matched))

    if not groups_to_merge:
        return {"ok": False, "error": "Mỗi bộ được chọn cần có ít nhất 2 mùa sẵn sàng (ready) để gộp"}

    combined_results: list[dict] = []
    combined_cands: list[dict] = []
    errors: list[str] = []
    trash_base = hg._trash_root(root / "dummy") / "_combined_seasons" / time.strftime("%Y%m%d_%H%M%S")

    for core, items in groups_to_merge:
        season_nums = []
        for idx, it in enumerate(items, 1):
            s_no = _season_no(it) or idx
            season_nums.append(s_no)
        if len(season_nums) >= 2 and season_nums == list(range(min(season_nums), max(season_nums) + 1)):
            tag = f"Gộp S{min(season_nums)}-S{max(season_nums)}"
        else:
            tag = f"Gộp {len(items)} mùa"

        folder_name = _sanitize_folder_name(f"{core} ({tag})")
        target_dir = root / folder_name
        target_dir.mkdir(parents=True, exist_ok=True)
        out_mp4 = target_dir / f"{folder_name} (merged).mp4"

        input_paths = [Path(it["path"]) for it in items]
        res = _concat_videos_smart(input_paths, out_mp4)
        if not res.get("ok"):
            errors.append(f"{core}: {res.get('error')}")
            continue

        # Copy poster / cover / .series.json từ mùa đầu tiên sang thư mục gộp
        first_folder = root / items[0]["name"]
        if first_folder.is_dir():
            for meta_name in ("poster.jpg", "poster.jpeg", "poster.png", "cover.jpg", "cover.jpeg", "cover.png", "thumb.jpg"):
                src_meta = first_folder / meta_name
                if src_meta.is_file() and not (target_dir / meta_name).exists():
                    try:
                        shutil.copy2(src_meta, target_dir / meta_name)
                    except OSError:
                        pass
            sj_src = first_folder / ".series.json"
            sj_dst = target_dir / ".series.json"
            sj_data = {}
            if sj_src.is_file():
                try:
                    sj_data = json.loads(sj_src.read_text(encoding="utf-8"))
                except Exception:
                    sj_data = {}
            sj_data["title"] = folder_name
            sj_data["combined_from"] = [it["name"] for it in items]
            sj_data["combined_at"] = _now_s()
            try:
                sj_dst.write_text(json.dumps(sj_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass

        # Đánh dấu vào hg_merge_state để Hongguo Bridge nhận diện ngay
        try:
            hg.mark_done(folder_name, merged_file=out_mp4.name, removed=0)
        except Exception:
            pass

        # Di chuyển các thư mục mùa lẻ vào thùng rác D:\Lib_trash để không bị làm trùng
        moved_seasons: list[str] = []
        if move_to_trash:
            trash_dir = trash_base / _sanitize_folder_name(core)
            trash_dir.mkdir(parents=True, exist_ok=True)
            for it in items:
                src_folder = root / it["name"]
                if src_folder.is_dir() and src_folder.resolve() != target_dir.resolve():
                    try:
                        shutil.move(str(src_folder), str(trash_dir / src_folder.name))
                        moved_seasons.append(it["name"])
                    except OSError as e:
                        errors.append(f"Không chuyển được {it['name']} vào thùng rác: {e}")

        st["merges"][folder_name] = _now_s()
        combined_results.append({
            "core": core,
            "folder_name": folder_name,
            "path": str(out_mp4),
            "duration_sec": res.get("duration_sec"),
            "size_mb": res.get("size_mb"),
            "mode": res.get("mode"),
            "seasons_merged": [it["name"] for it in items],
            "moved_to_trash": moved_seasons,
        })
        combined_cands.append({
            "name": folder_name,
            "title": folder_name,
            "path": str(out_mp4),
            "series_id": str(items[0].get("series_id") or ""),
            "core": core,
            "season": 1,
            "key": items[0]["key"],
            "mtime": time.time(),
        })

    save_state(st)
    queue_res = {}
    if add_to_queue and combined_cands:
        queue_res = add_jobs(base, combined_cands)

    return {
        "ok": bool(combined_results),
        "combined": combined_results,
        "count": len(combined_results),
        "queued_jobs": queue_res.get("job_ids") or [],
        "errors": errors,
    }


def _fmt_srt_ts(sec: float) -> str:
    sec = max(0.0, float(sec or 0.0))
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


_COMBINE_CN_NUM = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _combine_parse_cn_int(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + (_COMBINE_CN_NUM.get(s[1:], 0) or 0)
    if s.endswith("十") and len(s) == 2:
        return (_COMBINE_CN_NUM.get(s[0], 0) or 0) * 10
    if "十" in s:
        a, _, b = s.partition("十")
        return (_COMBINE_CN_NUM.get(a, 0) or 0) * 10 + (_COMBINE_CN_NUM.get(b, 0) or 0)
    if s in _COMBINE_CN_NUM:
        return _COMBINE_CN_NUM[s]
    return None


def _combine_part_num(*texts: str) -> int:
    """Bóc số phần từ title_vi / title_zh / name / job_id.
    Missing → 0 (xếp trước Phần 1 — thường là bản gốc chưa gắn số)."""
    blob = " ".join(str(t or "") for t in texts if t)
    if not blob.strip():
        return 0
    m = re.match(
        r"^(?:phần|mùa|season|tập|p)\s*(\d+)\s*[|:\-–—]",
        blob.strip(),
        re.I,
    )
    if m:
        return int(m.group(1))
    m = re.search(
        r"(?:^|[\s_\-\.\(:|])(?:p|phần|mùa|season|s|tập|t)\s*(\d+)",
        blob,
        re.I,
    )
    if m:
        return int(m.group(1))
    m = re.search(r"\bpart\s*(\d+)\b", blob, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"第([0-9一二三四五六七八九十百零〇两兩]+)[季集部话話]", blob)
    if m:
        n = _combine_parse_cn_int(m.group(1))
        if n is not None:
            return n
    m = re.search(r"(?:^|[_\-\s.])p(\d+)\b", blob, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"_p(\d+)$", blob, re.I)
    if m:
        return int(m.group(1))
    return 0


def _part_sort_key(jid: str, manifest: dict) -> tuple:
    """(part, created_at, job_id). Thiếu số phần → 0."""
    m = manifest or {}
    req = m.get("request") or {}
    title_vi = str(m.get("title_vi") or "").strip()
    title_zh = str(m.get("title_zh") or "").strip()
    name = str(req.get("title") or req.get("name") or "").strip()
    part = _combine_part_num(title_vi, title_zh, name, str(jid))
    if not part:
        try:
            part = int(req.get("season") or m.get("season") or 0)
        except (TypeError, ValueError):
            part = 0
    created = str(m.get("created_at") or "")
    return (part, created, str(jid))


def combine_rendered_jobs(
    base: str,
    job_ids: list[str],
    custom_title_vi: str | None = None,
    auto_publish: bool = False,
    preserve_order: bool = False,
    allow_oversized: bool = False,
) -> dict:
    """Nối các Job ĐÃ RENDER xong (work/<jid>/output.mp4) thành 1 Job video dài hoàn chỉnh
    để tạo Thumbnail/Meta và đăng YouTube (Trọn Bộ / Gộp Nhiều Phần).
    Mặc định sắp theo Phần 1→N (không tin thứ tự tick UI). preserve_order=True giữ thứ tự client (↑↓).
    """
    import hashlib
    import shutil

    clean_ids = [str(x).strip() for x in (job_ids or []) if str(x).strip()]
    if len(clean_ids) < 2:
        return {"ok": False, "error": "Cần chọn ít nhất 2 job đã xong để gộp"}

    job_records: list[dict] = []
    for jid in clean_ids:
        wdir = WORK / jid
        mf_path = wdir / "manifest.json"
        if not mf_path.is_file():
            return {"ok": False, "error": f"Job {jid} không còn manifest.json trên ổ đĩa"}
        try:
            m = json.loads(mf_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": False, "error": f"Lỗi đọc manifest của {jid}: {e}"}
        mux_out = ((m.get("stages") or {}).get("mux") or {}).get("output_path") or ""
        out_p = Path(mux_out) if mux_out else (wdir / "output.mp4")
        if not out_p.is_file():
            out_p = wdir / "output.mp4"
        if not out_p.is_file():
            return {"ok": False, "error": f"Job {jid} chưa có file video hoàn chỉnh (output.mp4)"}
        job_records.append({"job_id": jid, "work_dir": wdir, "manifest": m, "output_mp4": out_p})

    if not preserve_order:
        job_records.sort(key=lambda r: _part_sort_key(r["job_id"], r["manifest"]))
        clean_ids = [r["job_id"] for r in job_records]

    merged_jid = hashlib.sha1(f"merged_jobs:{','.join(clean_ids)}:{int(time.time())}".encode("utf-8")).hexdigest()[:12]
    merged_dir = WORK / merged_jid
    merged_dir.mkdir(parents=True, exist_ok=True)
    merged_mp4 = merged_dir / "output.mp4"

    concat_res = _concat_videos_smart([r["output_mp4"] for r in job_records], merged_mp4)
    if not concat_res.get("ok"):
        shutil.rmtree(merged_dir, ignore_errors=True)
        return {"ok": False, "error": concat_res.get("error") or "Lỗi nối video"}

    part_durs = concat_res.get("part_durations") or []
    # Gộp script.json và sub.vi.srt (cộng dồn offset thời gian của từng phần)
    merged_segments: list[dict] = []
    srt_Blocks: list[str] = []
    cum_offset = 0.0
    seg_idx = 1
    first_script_meta: dict = {}

    for i, rec in enumerate(job_records):
        sp = rec["work_dir"] / "script.json"
        if sp.is_file():
            try:
                sdata = json.loads(sp.read_text(encoding="utf-8"))
                if not first_script_meta:
                    first_script_meta = {k: v for k, v in sdata.items() if k != "segments"}
                for seg in sdata.get("segments") or []:
                    new_seg = dict(seg)
                    st_t = float(seg.get("start") or 0.0) + cum_offset
                    ed_t = float(seg.get("end") or st_t) + cum_offset
                    new_seg["id"] = seg_idx
                    new_seg["start"] = round(st_t, 3)
                    new_seg["end"] = round(ed_t, 3)
                    merged_segments.append(new_seg)
                    txt_vi = str(seg.get("text_vi") or seg.get("vi") or seg.get("text") or "").strip()
                    if txt_vi and ed_t > st_t:
                        srt_Blocks.append(f"{seg_idx}\n{_fmt_srt_ts(st_t)} --> {_fmt_srt_ts(ed_t)}\n{txt_vi}\n")
                    seg_idx += 1
            except Exception:
                pass
        dur_i = part_durs[i] if i < len(part_durs) and part_durs[i] > 0 else float(
            (((rec["manifest"].get("stages") or {}).get("download") or {}).get("duration_sec")) or 0.0
        )
        cum_offset += dur_i

    if merged_segments:
        script_out = {**first_script_meta, "segments": merged_segments}
        (merged_dir / "script.json").write_text(json.dumps(script_out, ensure_ascii=False, indent=2), encoding="utf-8")
    if srt_Blocks:
        (merged_dir / "sub.vi.srt").write_text("\n".join(srt_Blocks), encoding="utf-8")

    # Tạo manifest.json cho job gộp
    first_m = job_records[0]["manifest"]
    first_req = dict(first_m.get("request") or {})
    first_dl = dict(((first_m.get("stages") or {}).get("download")) or {})
    first_row = film_db.row_from_manifest(job_records[0]["job_id"], first_m)
    base_series = first_row.get("series") or first_req.get("title") or "Series"

    # Đặt tên gốc và tên tiếng Việt cho bản gộp
    combined_orig_title = f"{base_series} (Gộp {len(job_records)} phần)"
    if custom_title_vi and custom_title_vi.strip():
        final_title_vi = custom_title_vi.strip()
    else:
        vi_titles = []
        for r in job_records:
            t_vi = str(r["manifest"].get("title_vi") or "").strip()
            if t_vi and t_vi not in vi_titles:
                vi_titles.append(t_vi)
        if vi_titles:
            base_vi = re.sub(r"\s*[-—·|]?\s*(?:mùa|phần|tập|ss|season)\s*\d+.*$", "", vi_titles[0], flags=re.I).strip()
            final_title_vi = f"{base_vi or vi_titles[0]} - Trọn Bộ ({len(job_records)} Phần)"
        else:
            final_title_vi = f"{base_series} - Trọn Bộ ({len(job_records)} Phần)"

    now_iso = datetime.utcnow().isoformat() + "Z"
    total_dur = float(concat_res.get("duration_sec") or cum_offset)
    w = int(concat_res.get("width") or first_dl.get("width") or 1080)
    h = int(concat_res.get("height") or first_dl.get("height") or 1920)

    merged_manifest = {
        "job_id": merged_jid,
        "created_at": now_iso,
        "updated_at": now_iso,
        "title_vi": final_title_vi,
        "merged_from_jobs": clean_ids,
        "request": {
            **first_req,
            "title": combined_orig_title,
        },
        "queue_opts": first_m.get("queue_opts") or {},
        "stages": {
            "download": {
                "status": "done",
                "progress": 1.0,
                "title": combined_orig_title,
                "duration_sec": total_dur,
                "width": w,
                "height": h,
                "video_path": str(merged_mp4),
                "finished_at": now_iso,
            },
            "asr": {"status": "done", "progress": 1.0, "segments": len(merged_segments), "finished_at": now_iso},
            "translate": {"status": "done", "progress": 1.0, "segments": len(merged_segments), "finished_at": now_iso},
            "tts": {"status": "done", "progress": 1.0, "finished_at": now_iso},
            "mux": {
                "status": "done",
                "progress": 1.0,
                "output_path": str(merged_mp4),
                "width": w,
                "height": h,
                "finished_at": now_iso,
            },
        },
    }
    (merged_dir / "manifest.json").write_text(json.dumps(merged_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        film_db.sync_one(merged_jid)
        film_db.log_event(merged_jid, "combined_rendered_jobs", {"from": clean_ids, "duration_sec": total_dur})
    except Exception:  # noqa: BLE001
        pass

    pub_res = None
    if auto_publish:
        try:
            import agents.auto_publisher as auto_publisher
            # File gộp >11.5h: từ chối trừ khi UI đã confirm (allow_oversized).
            if (not allow_oversized) and float(total_dur or 0) > getattr(
                auto_publisher, "YT_MAX_DURATION_SEC", 41400
            ):
                pub_res = {
                    "ok": False,
                    "error": f"Từ chối auto-publish: tổng ~{float(total_dur)/3600:.1f}h >11.5h (YouTube thường từ chối)",
                    "refused_oversized": True,
                    "need_confirm": True,
                }
            else:
                busy_now, active_owner = auto_publisher.is_any_job_uploading()
                if busy_now and active_owner != merged_jid:
                    pub_res = auto_publisher.enqueue_publish(
                        merged_jid, force=True, allow_oversized=allow_oversized
                    )
                else:
                    pub_res = auto_publisher.publish_job(
                        merged_jid, force=True, allow_oversized=allow_oversized
                    )
        except Exception as e:  # noqa: BLE001
            pub_res = {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "job_id": merged_jid,
        "title": combined_orig_title,
        "title_vi": final_title_vi,
        "duration_sec": total_dur,
        "size_mb": concat_res.get("size_mb"),
        "mode": concat_res.get("mode"),
        "merged_from": clean_ids,
        "publish": pub_res,
    }


def pick(spec: dict, cands: list[dict]) -> list[dict]:
    if not cands:
        return []
    if spec["mode"] == "all":
        return cands
    if spec["mode"] == "series":
        cores = list(dict.fromkeys(c["core"] for c in cands))[: spec["n"]]
        return [c for c in cands if c["core"] in cores]
    return cands[: spec["n"]]


def _have(spec: dict, cands: list[dict]) -> int:
    return len({c["core"] for c in cands}) if spec["mode"] == "series" else len(cands)


# ─── thêm job ────────────────────────────────────────────────────────────

def _existing_group(core: str) -> str | None:
    for _, m in _job_manifests():
        req = m.get("request") or {}
        if req.get("group_id") and (req.get("group_title") or "") == core:
            return req["group_id"]
    return None


def add_jobs(base: str, chosen: list[dict]) -> dict:
    """Tạo job local cho từng file, gom nhóm theo bộ, rồi xếp hàng theo config batch."""
    ids: list[str] = []
    titles: list[str] = []
    errors: list[str] = []
    by_core: dict[str, list[str]] = {}
    # Sắp xếp candidate theo Bộ và Phần (Season 1 -> Season 2 -> Season 3..)
    sorted_chosen = list(chosen or [])
    sorted_chosen.sort(key=lambda c: (str(c.get("core") or "").lower(), int(c.get("season") or 1), str(c.get("name") or "")))
    for c in sorted_chosen:
        r = _post(base, "/api/jobs/local-path",
                  {"path": c["path"], "title": c["name"], "recursive": False, "group_folder": False},
                  timeout=1800)
        jid = r.get("job_id")
        if not jid:
            errors.append(f"{c['name']}: {r.get('error') or r}")
            continue
        ids.append(jid)
        titles.append(c["name"])
        by_core.setdefault(c["core"], []).append(jid)
    for core, jids in by_core.items():
        gid = _existing_group(core)
        if gid:
            _post(base, "/api/queue/group", {"action": "add", "group_id": gid, "job_ids": jids}, timeout=60)
        elif len(jids) > 1:
            _post(base, "/api/queue/group", {"action": "create", "title": core, "job_ids": jids}, timeout=60)
    queued: dict = {}
    if ids:
        opts = ap.queue_opts(base)
        queued = _post(base, "/api/queue/jobs", {"job_ids": ids, **opts}, timeout=60)
        try:
            film_db.sync()
            for jid in ids:
                film_db.log_event(jid, "feeder_add", load_state().get("request_text") or "mặc định")
        except Exception:  # noqa: BLE001
            pass
    return {"job_ids": ids, "titles": titles, "errors": errors, "queued": queued.get("queued")}


# ─── prefetch: ghép + tải ────────────────────────────────────────────────

def _merge_busy(base: str) -> bool:
    try:
        mq = _get(base, "/api/hg/merge-queue", timeout=20)
    except Exception:  # noqa: BLE001
        return True
    return bool(mq.get("current") or mq.get("queue") or (mq.get("hongguo") or {}).get("running"))


def _hg_status(base: str) -> dict:
    try:
        return _get(base, "/api/hg/status", timeout=20)
    except Exception:  # noqa: BLE001
        return {"running": True}


def _downloading_sids(hg_st: dict) -> set[str]:
    """series_id đang tải dở. /dl/status trả khoá «sid» (không phải series_id) và giữ cả bộ đã xong."""
    out = set()
    for x in hg_st.get("series") or []:
        if not isinstance(x, dict):
            out.add(str(x))
        elif x.get("status") != "done":
            out.add(str(x.get("sid") or x.get("series_id") or ""))
    out.discard("")
    return out


def mergeable(spec: dict, items: list[dict], hg_st: dict) -> list[dict]:
    downloading = _downloading_sids(hg_st)
    out = []
    for it in items:
        if it.get("merged") or not it.get("fragments"):
            continue
        total = int(it.get("total") or 0)
        if total and int(it.get("fragments") or 0) < total:
            continue  # tải chưa đủ tập
        if hg_st.get("running") and str(it.get("series_id") or "") in downloading:
            continue
        if not _match(spec, it.get("name") or "", it.get("title") or ""):
            continue
        out.append(it)
    return out


def hongguo_pick(base: str, spec: dict, items: list[dict], k: int) -> list[dict]:
    have = {str(it.get("series_id") or "") for it in items}
    try:
        have |= film_db.series_ids()
    except Exception:  # noqa: BLE001
        pass
    if spec["hg_query"]:
        d = _get(base, "/api/hg/search", timeout=90, q=spec["hg_query"])
        rows = d.get("results") or d.get("items") or []
    else:
        d = _get(base, "/api/hg/rank", timeout=90, category=spec["hg_category"],
                 board=spec["hg_board"], size=100)
        rows = d.get("results") or []
    out = []
    for r in rows:
        sid = str(r.get("series_id") or "")
        if not sid or sid in have:
            continue
        try:
            score = float(r.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        if spec["hg_min_score"] and score < spec["hg_min_score"]:
            continue
        if not _match(spec, r.get("title") or ""):
            continue
        out.append({"series_id": sid, "title": r.get("title") or sid, "score": score})
        if len(out) >= k:
            break
    return out


def prefetch(base: str, st: dict, items: list[dict], n_cands: int) -> list[str]:
    spec = st["spec"]
    notes: list[str] = []
    hg_st = _hg_status(base)
    want = 0 if spec["mode"] == "all" else spec["n"]
    merge_list = mergeable(spec, items, hg_st) if "auto_merge" in spec["sources"] else []
    if merge_list and not _merge_busy(base):
        names = [it["name"] for it in merge_list]
        r = _post(base, "/api/hg/merge", {"names": names}, timeout=60)
        if r.get("queued"):
            for n in r["queued"]:
                st["merges"][n] = _now_s()
            notes.append(f"ghép {len(r['queued'])} bộ: " + ", ".join(r["queued"][:5]))
    if "hongguo" not in spec["sources"] or hg_st.get("running") or want == 0:
        return notes
    pending_frag = [it for it in items if not it.get("merged") and it.get("fragments")]
    if spec["mode"] == "series":
        hg = _hg()
        n_frag = len({hg.franchise_core(it.get("title") or it["name"]) for it in pending_frag})
    else:
        n_frag = len(pending_frag)
    short = want - n_cands - n_frag
    today = sum(1 for v in st["downloads"].values() if str(v.get("at", "")).startswith(_today()))
    k = min(short, spec["hg_max_per_day"] - today)
    if k <= 0:
        return notes
    picks = hongguo_pick(base, spec, items, k)
    if picks:
        r = _post(base, "/api/hg/submit", {"series_ids": [p["series_id"] for p in picks]}, timeout=60)
        if not r.get("error"):
            for p in picks:
                st["downloads"][p["series_id"]] = {"title": p["title"], "at": _now_s()}
            notes.append(f"tải {len(picks)} bộ Hongguo: " + ", ".join(p["title"] for p in picks))
        else:
            notes.append(f"tải Hongguo lỗi: {r.get('error')}")
    return notes


# ─── vòng feeder ─────────────────────────────────────────────────────────

def pipeline_idle(brief: dict) -> bool:
    c = brief.get("counts") or {}
    return (brief.get("ok") and not brief.get("current") and not brief.get("busy")
            and int(brief.get("queue_len") or 0) == 0
            and int(c.get("running") or 0) == 0 and int(c.get("pending") or 0) == 0)


def feed_tick(base: str, *, force: bool = False) -> dict:
    """1 vòng: prefetch (ghép/tải) + nếu pipeline rảnh thì thêm job. force=True: thêm ngay kể cả đang chạy."""
    st = load_state()
    if not st["enabled"] and not force:
        return {"skipped": "feeder_off"}
    spec = st["spec"]
    out: dict[str, Any] = {"notes": []}
    items = library(base)
    cands = candidates(base, st, items)
    brief = ap.status_brief(base)
    if force or pipeline_idle(brief):
        chosen = pick(spec, cands)
        if chosen:
            r = add_jobs(base, chosen)
            out["added"] = r
            if r["job_ids"]:
                st["fed"].append({"at": _now_s(), "job_ids": r["job_ids"], "titles": r["titles"]})
                out["notes"].append(f"thêm {len(r['job_ids'])} job: " + ", ".join(r["titles"][:8]))
                st.pop("empty_notified", None)
            if r["errors"]:
                out["notes"].append("lỗi tạo job: " + "; ".join(r["errors"][:3]))
            cands = [c for c in cands if c not in chosen]
        elif not st.get("empty_notified"):
            out["notes"].append("hết phim để thêm — đang chờ tải/ghép" if "hongguo" in spec["sources"]
                                or "auto_merge" in spec["sources"] else "hết phim để thêm")
            st["empty_notified"] = _now_s()
    try:
        out["notes"] += prefetch(base, st, items, _have(spec, cands))
    except Exception as e:  # noqa: BLE001
        out["notes"].append(f"prefetch lỗi: {e}")
    st["last_tick"] = _now_s()
    save_state(st)
    return out


def preview(base: str) -> str:
    st = load_state()
    spec = st["spec"]
    items = library(base)
    cands = candidates(base, st, items)
    chosen = pick(spec, cands)
    hg_st = _hg_status(base)
    mer = mergeable(spec, items, hg_st) if "auto_merge" in spec["sources"] else []
    lines = [f"Có sẵn {len(cands)} phim đã ghép chưa làm · {len(mer)} bộ đủ tập chờ ghép"
             + (" · Hongguo đang tải" if hg_st.get("running") else "")]
    if chosen:
        lines.append(f"Lần thêm tới ({len(chosen)}):")
        lines += [f"  - {c['name']}" for c in chosen[:15]] + (["  - …"] if len(chosen) > 15 else [])
    else:
        lines.append("Lần thêm tới: chưa có phim phù hợp")
    return "\n".join(lines)


# ─── yêu cầu bằng lời → spec ─────────────────────────────────────────────

_PARSE_SYS = (
    "Bạn chuyển yêu cầu tiếng Việt của user về việc TỰ THÊM JOB lồng tiếng phim thành JSON spec.\n"
    "Chỉ trả JSON thuần, chỉ các khoá cần đổi so với spec hiện tại:\n"
    '  "sources": mảng con của ["lib_merged","auto_merge","hongguo"] '
    "(lib_merged=lấy cả phim đã ghép sẵn trong thư viện; auto_merge=tự ghép tập lẻ đã tải; "
    "hongguo=tải bộ mới từ Hongguo). Không có lib_merged = chỉ làm phim do chính feeder tải/ghép. "
    "User không nói gì về nguồn thì giữ nguyên.\n"
    '  "mode": "count" (n phim/mùa/job mỗi lần) | "series" (n BỘ mỗi lần, 1 bộ = mọi mùa của cùng tên) '
    '| "all" (tất cả). Từ "bộ" → series; "phim", "mùa", "job", "video" → count.\n'
    '  "n": số phim (count) hoặc số bộ (series) mỗi lần\n'
    '  "include": mảng từ khoá tên phim (tiếng Trung nếu user đưa) phải có · "exclude": mảng từ khoá loại\n'
    '  "order": "smart" (điểm gợi ý: nối mạch bộ đang làm, mùa kế tiếp) | "oldest" | "newest" | "name"\n'
    '  "hg_query": từ khoá tìm trên Hongguo ("" = dùng bảng hot) · "hg_min_score": điểm tối thiểu 0-10\n'
    '  "hg_category": "all"|"ai"|"human"|"comic" (ai=phim AI, human=người đóng, comic=hoạt hình)\n'
    '  "hg_max_per_day": số bộ tải mới tối đa mỗi ngày\n'
    'Thêm "summary": 1 câu tiếng Việt tóm tắt bạn hiểu yêu cầu thế nào.'
)


def _llm_cfg() -> tuple[str, str, list[str]]:
    """Cùng nguồn với agent chat (config.agent_llm): sidecar không còn cần key trong .bat."""
    import core.config as _cfg
    c = _cfg.agent_llm()
    return c["base"], c["key"], [c["model"]] + ([c["fallback"]] if c["fallback"] else [])


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def parse_request_llm(text: str, cur: dict) -> dict:
    base, key, models = _llm_cfg()
    msgs = [
        {"role": "system", "content": _PARSE_SYS},
        {"role": "user", "content": f"Spec hiện tại: {json.dumps(cur, ensure_ascii=False)}\nYêu cầu: {text}"},
    ]
    last: Exception | None = None
    for m in models:
        try:
            r = requests.post(f"{base}/chat/completions", timeout=90,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              json={"model": m, "messages": msgs, "temperature": 0, "max_tokens": 600,
                                    "stream": False})
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            content = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            d = _extract_json(content)
            d["_model"] = m
            return d
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"LLM không dịch được yêu cầu: {last}")


def parse_request_rules(text: str) -> dict:
    t = text.lower()
    d: dict[str, Any] = {}
    m = re.search(r"(\d+|một|hai|ba|bốn|năm)\s*(phim|job|video|mùa|bộ|series)?", t)
    words = {"một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5}
    if re.search(r"tất cả|toàn bộ|hết phim|\ball\b", t):
        d["mode"] = "all"
    elif m:
        n = words.get(m.group(1)) or int(m.group(1))
        d["mode"], d["n"] = ("series" if m.group(2) in ("bộ", "series") else "count"), n
    elif re.search(r"cả bộ|trọn bộ|series", t):
        d["mode"], d["n"] = "series", 1
    srcs = []
    if re.search(r"đã ghép|merged|thư viện|có sẵn", t):
        srcs.append("lib_merged")
    if re.search(r"ghép|merge", t):
        srcs.append("auto_merge")
    if re.search(r"hongguo|tải|hot|mới", t):
        srcs.append("hongguo")
    if srcs:
        d["sources"] = list(dict.fromkeys(["lib_merged", *srcs]))
    sc = re.search(r"(?:điểm|score)\s*(?:>=|≥|>|trên|từ)?\s*(\d+(?:[.,]\d)?)", t)
    if sc:
        d["hg_min_score"] = float(sc.group(1).replace(",", "."))
    q = re.findall(r"[\"«“]([^\"»”]+)[\"»”]", text)
    if q:
        d["include"] = q
    d["summary"] = "(hiểu theo luật đơn giản — LLM không trả lời)"
    return d


def set_request(text: str) -> dict:
    st = load_state()
    try:
        d = parse_request_llm(text, st["spec"])
    except Exception as e:  # noqa: BLE001
        d = parse_request_rules(text)
        d["_llm_error"] = str(e)[:200]
    summary = str(d.pop("summary", "") or "")
    meta = {k: d.pop(k) for k in list(d) if k.startswith("_")}
    st["spec"] = normalize_spec({**st["spec"], **d})
    st["request_text"] = text
    st["enabled"] = True
    st.pop("empty_notified", None)
    save_state(st)
    return {"spec": st["spec"], "summary": summary, **meta}


# ─── run-watch ───────────────────────────────────────────────────────────

def _sidecar_films_ui() -> str:
    from core.instance import sidecar_url  # V6 F4
    return sidecar_url() + "/films/ui"


def _server_tree_cpu(lib: Path | None) -> float | None:
    """Tổng CPU-seconds của server (cổng instance) + tiến trình con (bỏ ffmpeg ghép thư viện Hongguo)."""
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return None
    srv = None
    from core.instance import server_cmd_matches, server_port as _server_port  # V6 F4: server của instance
    _srv_port = _server_port()
    for p in psutil.process_iter(["cmdline"]):
        # review2: so token (`-m app --instance v6` không có số cổng trong cmdline; "8806" chuỗi con dễ khớp nhầm)
        if server_cmd_matches(p.info.get("cmdline") or [], port=_srv_port):
            srv = p
            break
    if srv is None:
        return None
    lib_s = str(lib).lower() if lib else None
    total = 0.0
    for p in [srv, *srv.children(recursive=True)]:
        try:
            if lib_s and p is not srv and lib_s in " ".join(p.cmdline()).lower():
                continue
            t = p.cpu_times()
            total += t.user + t.system
        except Exception:  # noqa: BLE001
            continue
    return total


def _job_activity(job_id: str) -> float:
    wd = WORK / job_id
    newest = 0.0
    try:
        with os.scandir(wd) as it:
            for e in it:
                try:
                    newest = max(newest, e.stat().st_mtime)
                except OSError:
                    pass
    except OSError:
        pass
    return newest


def _running_stage(job_id: str) -> tuple[str | None, dict, dict]:
    try:
        m = json.loads((WORK / job_id / "manifest.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None, {}, {}
    for name, s in (m.get("stages") or {}).items():
        if isinstance(s, dict) and s.get("status") == "running":
            return name, s, m
    return None, {}, m


def _running_jobs(base: str) -> tuple[list[str], dict]:
    q = _get(base, "/api/queue/jobs", timeout=20)
    ids = [q["current"]] if q.get("current") else []
    try:
        snap = (_get(base, "/api/agent/status", timeout=30).get("snapshot") or {})
        for r in snap.get("running") or []:
            jid = r.get("job_id") if isinstance(r, dict) else None
            if jid and jid not in ids:
                ids.append(jid)
    except Exception:  # noqa: BLE001
        pass
    return ids, q


def _fmt_dur(sec: float) -> str:
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    return f"{h}h{r // 60:02d}m" if h else f"{r // 60}m{r % 60:02d}s"


def _requeue_front(base: str, job_id: str, m: dict) -> None:
    q = _get(base, "/api/queue/jobs", timeout=20)
    pending = [str(x) for x in (q.get("queue") or []) if str(x) != job_id]
    snaps = q.get("opts_by_job") or {}
    fallback = q.get("opts") or {}
    if pending:
        _post(base, "/api/queue/jobs/remove", {"job_ids": pending}, timeout=30)
    opts = dict(m.get("queue_opts") or ap.queue_opts(base))
    opts.pop("pin_opts", None)
    _post(base, "/api/queue/jobs", {"job_ids": [job_id], "skip_done": True, **opts}, timeout=60)
    for jid in pending:
        o = dict(snaps.get(jid) or fallback)
        o.pop("pin_opts", None)
        _post(base, "/api/queue/jobs", {"job_ids": [jid], "skip_done": True, **o}, timeout=60)


def runwatch_tick(base: str, *, heal: bool, notify) -> dict:
    """Kiểm job đang chạy. notify(text) để báo. heal=True: tự stop + xếp lại khi treo."""
    st = load_state()
    rw = st["runwatch"]
    seen = rw.setdefault("jobs", {})
    attempts = rw.setdefault("attempts", {})
    now = time.time()
    cpu = _server_tree_cpu(lib_root())
    ids, q = _running_jobs(base)
    report: list[dict] = []
    # job vừa xong (theo lịch sử queue)
    last_hist = rw.get("last_hist_ts", 0)
    for h in q.get("history") or []:
        if float(h.get("ts") or 0) > last_hist:
            _, _, m = _running_stage(h["job_id"])
            title = (m.get("request") or {}).get("title") or h["job_id"]
            if last_hist:
                icon = "✓" if h.get("status") == "done" else "✗"
                notify(f"{icon} Job {title} → {h.get('status')} {h.get('detail') or ''}".strip())
            rw["last_hist_ts"] = max(rw.get("last_hist_ts", 0), float(h.get("ts") or 0))
    rw.setdefault("last_hist_ts", rw.get("last_hist_ts", 0))
    for jid in list(seen):
        if jid not in ids:
            seen.pop(jid)
    for jid in ids:
        stage, s, m = _running_stage(jid)
        title = (m.get("request") or {}).get("title") or jid
        sig = [stage, s.get("progress"), s.get("cur_text"), s.get("done"), s.get("n_done")]
        act = _job_activity(jid)
        prev = seen.get(jid)
        alive = True
        if prev and prev.get("stage") == stage:
            dt = now - prev["t"]
            cpu_d = (cpu - prev["cpu"]) if (cpu is not None and prev.get("cpu") is not None) else None
            progressed = sig != prev["sig"] or act > prev["act"] + 1
            busy_cpu = cpu_d is not None and dt > 0 and cpu_d / dt >= CPU_ALIVE_FRAC
            alive = progressed or busy_cpu
            last_alive = now if alive else prev["last_alive"]
            since = prev["since"]
        else:
            last_alive, since, cpu_d, dt = now, now, None, 0
        seen[jid] = {"stage": stage, "sig": sig, "act": act, "cpu": cpu, "t": now,
                     "last_alive": last_alive, "since": since, "title": title}
        idle_min = (now - last_alive) / 60
        limit = STUCK_MIN_STAGE.get(stage or "", STUCK_MIN)
        info = {"job_id": jid, "title": title, "stage": stage, "progress": s.get("progress"),
                "cur_text": s.get("cur_text"), "stage_for": _fmt_dur(now - since),
                "idle_min": round(idle_min, 1), "limit_min": limit,
                "cpu_pct": round(100 * cpu_d / dt, 1) if cpu_d is not None and dt else None}
        report.append(info)
        if stage and idle_min >= limit:
            key = f"{jid}:{stage}"
            n = int(attempts.get(key, 0))
            base_msg = (f"⚠ Job treo: {title} · stage {stage} · {idle_min:.0f} phút không tiến độ, "
                        f"không ghi file, CPU server ~0")
            if heal and n < MAX_HEAL:
                attempts[key] = n + 1
                _post(base, f"/api/jobs/{jid}/stop", {}, timeout=60)
                time.sleep(3)
                _requeue_front(base, jid, m)
                notify(f"{base_msg}\n→ đã dừng và xếp lại đầu hàng (lần {n + 1}/{MAX_HEAL})")
                try:
                    film_db.log_event(jid, "heal_stuck", f"{stage} treo {idle_min:.0f} phút · lần {n + 1}")
                except Exception:  # noqa: BLE001
                    pass
                seen.pop(jid, None)
            elif not rw.get("alerted", {}).get(key):
                rw.setdefault("alerted", {})[key] = _now_s()
                extra = ("đã tự chạy lại đủ số lần — cần xem tay. Gửi /restartserver nếu muốn restart "
                         "server (hàng chờ được giữ)") if heal else "Auto-pilot OFF nên không tự xử lý"
                notify(f"{base_msg}\n→ {extra}")
    rw["last_tick"] = _now_s()
    rw["report"] = report
    save_state(st)
    return {"running": report, "queue_len": len(q.get("queue") or []), "current": q.get("current")}


def running_text(base: str) -> str:
    try:
        q = _get(base, "/api/queue/jobs", timeout=20)
    except Exception as e:  # noqa: BLE001
        return f"Không đọc được hàng chờ: {e}"
    rw = load_state()["runwatch"]
    seen = rw.get("jobs") or {}
    ids, _ = _running_jobs(base)
    if not ids:
        lines = ["Không có job nào đang chạy."]
    else:
        lines = []
        cd = q.get("current_detail") or {}
        for jid in ids:
            stage, s, m = _running_stage(jid)
            title = (m.get("request") or {}).get("title") or jid
            prog = s.get("progress")
            p = f"{float(prog):.0f}%" if isinstance(prog, (int, float)) else "…"
            eta = cd.get("eta_sec") if cd.get("job_id") == jid else None
            w = seen.get(jid) or {}
            idle = (time.time() - w["last_alive"]) / 60 if w.get("last_alive") else 0
            lines.append(
                f"▶ {title}\n   stage {stage or '?'} · {p}"
                + (f" · còn ~{_fmt_dur(eta)}" if eta else "")
                + (f" · {s.get('cur_text')}" if s.get("cur_text") else "")
                + (f"\n   stage chạy {_fmt_dur(time.time() - w['since'])}" if w.get("since") else "")
                + (f" · không hoạt động {idle:.0f} phút" if idle >= 5 else " · đang hoạt động")
            )
    labels = q.get("queue_labels") or []
    lines.append(f"Hàng chờ: {len(q.get('queue') or [])} job"
                 + (": " + ", ".join(x['label'] for x in labels[:5]) if labels else ""))
    return "\n".join(lines)


# ─── /lib: thư viện chưa làm qua Telegram / Agent ─────────────────────────

LIB_PAGE = 8
_LIB_LAST: list[str] = []          # thứ tự bộ ở lần /lib gần nhất → số thứ tự ổn định giữa các lệnh
_LIB_ACTIONS = {
    "add": "add", "them": "add", "thêm": "add", "làm": "add", "lam": "add",
    "star": "priority", "uutien": "priority", "ưu": "priority", "ưutiên": "priority", "priority": "priority",
    "unstar": "unpriority", "bouutien": "unpriority", "unpriority": "unpriority",
    "skip": "skip", "boqua": "skip", "bỏ": "skip", "bỏqua": "skip",
    "unskip": "unskip", "laylai": "unskip", "lấylại": "unskip",
    "merge": "merge", "ghep": "merge", "ghép": "merge",
}


def lib_verb(word: str) -> str | None:
    """Động từ /lib làm thay đổi trạng thái (add/priority/…) hoặc None."""
    w = str(word or "").strip()
    return _LIB_ACTIONS.get(_fold(w).replace(" ", "")) or _LIB_ACTIONS.get(w.lower())


def _fold(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", str(s or "").lower().replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def lib_key(core: str) -> str:
    import hashlib
    return hashlib.sha1(core.encode("utf-8")).hexdigest()[:10]


def _lib_find(groups: list[dict], token: str) -> list[dict]:
    """token = số thứ tự (theo /lib gần nhất) · key nút bấm · tên gốc / tên Việt (không dấu cũng được)."""
    token = token.strip()
    if not token:
        return []
    hit = [g for g in groups if lib_key(g["core"]) == token]
    if hit:
        return hit
    by_core = {g["core"]: g for g in groups}
    if token.isdigit():
        order = _LIB_LAST or [g["core"] for g in groups]
        i = int(token) - 1
        return [by_core[order[i]]] if 0 <= i < len(order) and order[i] in by_core else []
    f = _fold(token)
    exact = [g for g in groups if token == g["core"] or f in (_fold(g.get("core_vi")), _fold(g.get("core_short_vi")))]
    return exact or [g for g in groups if token in g["core"] or f in _fold(g.get("core_vi"))
                     or f in _fold(g.get("core_short_vi")) or any(token in s["name"] for s in g["seasons"])]


def _lib_line(i: int, g: dict) -> str:
    tag = "★ " if g["priority"] else ("🚫 " if g["skipped"] else "")
    extra = f" · {g['n_todo'] - g['n_ready']} chờ ghép" if g["n_todo"] > g["n_ready"] else ""
    return (f"{i}. {tag}{g['core_vi'] or g['core']} ({g['core']})\n"
            f"    {g['n_ready']} mùa sẵn{extra} · {_fmt_dur(g['dur'])} phim · xử lý ~{_fmt_dur(g['est_sec'])}"
            f" · {g['score']:g}đ")


def _lib_buttons(g: dict, i: int | None = None) -> list[list[tuple[str, str]]]:
    k, n = lib_key(g["core"]), (f"{i} " if i else "")
    row = []
    if g["n_ready"]:
        row.append((f"✅ {n}Thêm", f"lib:add:{k}"))
    if g["n_todo"] > g["n_ready"] and any(s["state"] == "need_merge" for s in g["seasons"]):
        row.append((f"🔗 {n}Ghép", f"lib:merge:{k}"))
    row.append((f"{'☆ Bỏ ưu' if g['priority'] else '★ ' + n + 'Ưu tiên'}",
                f"lib:{'unpriority' if g['priority'] else 'priority'}:{k}"))
    row.append((f"{'↩ Lấy lại' if g['skipped'] else '🚫 ' + n + 'Bỏ'}",
                f"lib:{'unskip' if g['skipped'] else 'skip'}:{k}"))
    return [row]


def _lib_detail(g: dict) -> str:
    icon = {"ready": "✓ sẵn", "done": "✔ đã làm", "need_merge": "🔗 chờ ghép",
            "downloading": "⬇ đang tải", "partial": "… thiếu tập"}
    lines = [f"{'★ ' if g['priority'] else ''}{g['core_vi'] or g['core']} ({g['core']}) · {g['score']:g} điểm"]
    if g.get("reasons"):
        lines.append("Vì: " + "; ".join(g["reasons"]))
    for s in g["seasons"]:
        lines.append(f"  S{s.get('season') or '?'} · {icon.get(s['state'], s['state'])}"
                     + (f" · {_fmt_dur(s['dur'])}" if s.get("dur") else "")
                     + (f" · {s['fragments']}/{s['total']} tập" if s["state"] in ("need_merge", "partial", "downloading")
                        and s.get("total") else "")
                     + (f" — {s['title_vi']}" if s.get("title_vi") else ""))
    lines.append(f"Tổng {_fmt_dur(g['dur'])} phim · xử lý ~{_fmt_dur(g['est_sec'])}")
    return "\n".join(lines)


def lib_command(base: str, arg: str) -> tuple[str, list]:
    """/lib [all | <số|tên> | add|star|unstar|skip|unskip|merge <số|tên> ...] → (text, nút inline)."""
    global _LIB_LAST
    v = library_view(base)
    groups = v["groups"]
    parts = arg.split(maxsplit=1)
    verb = _LIB_ACTIONS.get(_fold(parts[0]).replace(" ", "")) if parts else None
    verb = verb or (_LIB_ACTIONS.get(parts[0].lower()) if parts else None)

    if verb:
        rest = parts[1] if len(parts) > 1 else ""
        tokens = [t for t in re.split(r"[,\s]+", rest) if t] if re.fullmatch(r"[\d,\s]+", rest or "x") else [rest]
        found: list[dict] = []
        for t in tokens:
            hit = _lib_find(groups, t)
            if len(hit) > 1 and not t.isdigit():
                return ("«" + t + "» khớp nhiều bộ, ghi rõ hơn hoặc dùng số:\n"
                        + "\n".join(_lib_line(i + 1, g) for i, g in enumerate(hit[:8])), [])
            found += [g for g in hit if g not in found]
        if not found:
            return (f"Không tìm thấy bộ «{rest}». Gõ /lib để xem danh sách có số thứ tự.", [])
        cores = [g["core"] for g in found]
        names = [s["name"] for g in found for s in g["seasons"] if s["state"] == "need_merge"]
        if verb == "merge" and not names:
            return ("Các bộ này không có mùa nào chờ ghép.", [])
        r = library_action(base, verb, names=names, cores=cores)
        label = ", ".join(g["core_vi"] or g["core"] for g in found)
        if not r.get("ok"):
            return (f"Không làm được ({verb}) với {label}: {r.get('error') or r}", [])
        msg = {
            "add": lambda: f"Đã thêm {len(r.get('job_ids') or [])} job vào hàng chờ:\n" + "\n".join(r.get("titles") or []),
            "priority": lambda: f"★ Ưu tiên: {label} — feeder sẽ lấy bộ này trước mọi bộ khác",
            "unpriority": lambda: f"Bỏ ưu tiên: {label}",
            "skip": lambda: f"🚫 Bỏ qua: {label} — feeder không tự lấy nữa (/lib unskip để lấy lại)",
            "unskip": lambda: f"Cho feeder lấy lại: {label}",
            "merge": lambda: f"🔗 Đã xếp ghép {len(r.get('queued') or names)} mùa của {label}",
        }[verb]()
        if verb == "add" and not ap.load_state().get("enabled"):
            msg += "\n(Hàng chờ tự chạy; Auto-pilot OFF chỉ ảnh hưởng tự xử lý lỗi/treo)"
        return (msg, [])

    if arg and arg.lower() not in ("all", "tatca", "tất cả", "hết", "het"):
        hit = _lib_find(groups, arg)
        if not hit:
            return (f"Không tìm thấy bộ «{arg}». Gõ /lib để xem danh sách.", [])
        if len(hit) > 1:
            _LIB_LAST = [g["core"] for g in hit]
            return (f"{len(hit)} bộ khớp «{arg}»:\n" + "\n".join(_lib_line(i + 1, g) for i, g in enumerate(hit)),
                    [b for i, g in enumerate(hit[:LIB_PAGE]) for b in _lib_buttons(g, i + 1)])
        return (_lib_detail(hit[0]), _lib_buttons(hit[0]))

    show_all = bool(arg)
    visible = [g for g in groups if not g["skipped"]] + ([g for g in groups if g["skipped"]] if show_all else [])
    _LIB_LAST = [g["core"] for g in visible]
    page = visible if show_all else visible[:LIB_PAGE]
    s = v["summary"]
    head = (f"Thư viện chưa làm: {s['series']} bộ · {s['ready']} mùa sẵn ({s['hours']} giờ phim, "
            f"xử lý ~{s['est_days']} ngày) · {s['need_merge']} mùa chờ ghép"
            + (f" · {s['skipped']} bộ đã bỏ qua" if s.get("skipped") else ""))
    if show_all:
        body = "\n".join(f"{i + 1}. {'★ ' if g['priority'] else '🚫 ' if g['skipped'] else ''}"
                         f"{g['core_vi'] or g['core']} · {g['n_ready']} sẵn · {_fmt_dur(g['dur'])}"
                         for i, g in enumerate(page))
        return (head + "\n" + body + "\n\n/lib <số> xem chi tiết · /lib add <số> thêm", [])
    body = "\n".join(_lib_line(i + 1, g) for i, g in enumerate(page))
    foot = (f"\n\nBấm nút, hoặc gõ: /lib add 1 3 · /lib star 2 · /lib skip 4 · /lib merge 5 · /lib <số|tên>"
            f"\n/lib all = cả {len(visible)} bộ · web: {_sidecar_films_ui()}")
    return (head + "\n\n" + body + foot, [b for i, g in enumerate(page) for b in _lib_buttons(g, i + 1)])


def lib_callback(base: str, data: str) -> str:
    """Nút inline «lib:<action>:<key>»."""
    try:
        _, action, key = data.split(":", 2)
    except ValueError:
        return "Nút không hợp lệ"
    verb = {"add": "add", "merge": "merge", "priority": "star", "unpriority": "unstar",
            "skip": "skip", "unskip": "unskip"}.get(action)
    return lib_command(base, f"{verb} {key}")[0] if verb else "Nút không hợp lệ"
