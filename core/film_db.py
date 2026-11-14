"""
Database phim đã làm (SQLite) — nguồn sự thật lâu dài, không mất khi xoá thư mục job.

  films   : 1 dòng / job — tên gốc + tên Việt, series/mùa, file nguồn, trạng thái, thời lượng,
            output, config lồng tiếng, link YouTube (đồng bộ từ yt_uploader)
  events  : lịch sử (tạo job, đổi trạng thái, feeder thêm, heal, upload)

sync() quét work/*/manifest.json → upsert; job đã xoá thư mục vẫn giữ (work_exists=0).
"""
from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
import csv
import io
import json
import os
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.timeutil import to_iso as _to_iso

# Thư mục/parent không phải tên series Hongguo — không dùng làm hg_name/poster.
_HG_NAME_JUNK = frozenset({
    "downloads", "download", "work", "tmp", "temp", "_split_parts",
    "parts", "output", "videos", "video",
})
_RE_JOB_FOLDER = re.compile(r"^[0-9a-f]{8,16}(_p\d{2})?$", re.I)
_RE_PART_SUFFIX = re.compile(r"\s*-\s*Phần\s+\d+\s*/\s*\d+\s*$", re.I)


def _strip_part_suffix(title: str) -> str:
    return _RE_PART_SUFFIX.sub("", title or "").strip()


def is_junk_hg_name(name: str, job_id: str = "") -> bool:
    """True nếu không nên dùng làm tên folder Lib / query /api/hg/poster."""
    n = (name or "").strip()
    if not n:
        return True
    if job_id and n == job_id:
        return True
    if n.lower() in _HG_NAME_JUNK:
        return True
    if _RE_JOB_FOLDER.fullmatch(n):
        return True
    # "5", "4" từ bilibili downloads — không phải series HG
    if n.isdigit() and len(n) <= 3:
        return True
    return False


def derive_hg_name(job_id: str, req: dict | None, title_clean: str = "") -> str:
    """Tên folder Hongguo/Lib cho poster — không lấy job_id / downloads / part folder."""
    req = req or {}
    job_id = (job_id or "").strip()
    explicit = str(req.get("hg_name") or "").strip()
    if explicit and not is_junk_hg_name(explicit, job_id):
        return explicit

    parent_id = str(req.get("parent_job_id") or "").strip()
    if parent_id:
        try:
            prow = get(parent_id)
            ph = str((prow or {}).get("hg_name") or "").strip()
            if ph and not is_junk_hg_name(ph, parent_id):
                return ph
        except Exception:
            pass
        try:
            pmf = WORK / parent_id / "manifest.json"
            if pmf.is_file():
                pm = json.loads(pmf.read_text(encoding="utf-8"))
                preq = pm.get("request") or {}
                ph = str(preq.get("hg_name") or "").strip()
                if not ph and preq.get("orig_path"):
                    ph = Path(preq["orig_path"]).parent.name
                if ph and not is_junk_hg_name(ph, parent_id):
                    return ph
                pt = _strip_part_suffix(
                    str(preq.get("title") or (pm.get("meta") or {}).get("title") or "")
                )
                if pt and not is_junk_hg_name(pt, parent_id):
                    return pt
        except Exception:
            pass

    orig = str(req.get("orig_path") or "").strip()
    if orig:
        try:
            parent_folder = Path(orig).parent.name
            if parent_folder and not is_junk_hg_name(parent_folder, job_id):
                return parent_folder
        except Exception:
            pass

    for cand in (
        _strip_part_suffix(title_clean),
        _strip_part_suffix(str(req.get("group_title") or "")),
        _strip_part_suffix(str(req.get("title") or "")),
    ):
        if cand and not is_junk_hg_name(cand, job_id):
            return cand
    return ""

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
DB_PATH = Path(os.getenv("FILM_DB") or ROOT / "data" / "films.db")
YT_DIR = _machine.yt_uploader_dir()
YT_HISTORY = YT_DIR / "upload_history.jsonl"
QUEUE_STATE = YT_DIR / "queue_state.json"
CORE_STAGES = ("download", "asr", "translate", "tts", "mux")

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS films (
    job_id        TEXT PRIMARY KEY,
    title         TEXT,
    title_vi      TEXT,
    series        TEXT,
    season        INTEGER,
    group_id      TEXT,
    group_title   TEXT,
    hg_name       TEXT,
    hg_series_id  TEXT,
    source_path   TEXT,
    source_key    TEXT,
    status        TEXT,
    stage         TEXT,
    duration_sec  REAL,
    width         INTEGER,
    height        INTEGER,
    output_path   TEXT,
    output_bytes  INTEGER,
    voice         TEXT,
    tts_engine    TEXT,
    audio_mode    TEXT,
    opts_json     TEXT,
    created_at    TEXT,
    finished_at   TEXT,
    yt_url        TEXT,
    yt_channel    TEXT,
    uploaded_at   TEXT,
    work_exists   INTEGER DEFAULT 1,
    note          TEXT,
    block_status  TEXT DEFAULT 'unknown',
    block_reason  TEXT,
    checked_at    TEXT,
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS films_source ON films(source_key);
CREATE INDEX IF NOT EXISTS films_series ON films(series);
CREATE INDEX IF NOT EXISTS films_status ON films(status);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT,
    job_id  TEXT,
    kind    TEXT,
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id);

CREATE TABLE IF NOT EXISTS library_blocks (
    name             TEXT PRIMARY KEY,
    core             TEXT,
    title_vi         TEXT,
    video_path       TEXT,
    status           TEXT DEFAULT 'unknown',
    reason           TEXT,
    test_yt_url      TEXT,
    test_yt_id       TEXT,
    test_sample_path TEXT,
    tested_at        TEXT,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS lib_blocks_core ON library_blocks(core);
CREATE INDEX IF NOT EXISTS lib_blocks_status ON library_blocks(status);

CREATE TABLE IF NOT EXISTS series_glossary (
    core             TEXT PRIMARY KEY,
    glossary_json    TEXT,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS series_glossary_core ON series_glossary(core);

CREATE TABLE IF NOT EXISTS hg_watchlist (
    sid          TEXT PRIMARY KEY,
    core         TEXT,
    title        TEXT,
    name         TEXT,
    season       INTEGER,
    eps          INTEGER,
    score        TEXT,
    album        TEXT,
    source       TEXT,
    on_disk      INTEGER DEFAULT 1,
    deleted_at   TEXT,
    seen_at      TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS hg_watch_core ON hg_watchlist(core);
CREATE INDEX IF NOT EXISTS hg_watch_name ON hg_watchlist(name);
CREATE INDEX IF NOT EXISTS hg_watch_disk ON hg_watchlist(on_disk);
"""


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_path(p: str | None) -> str:
    return os.path.normcase(os.path.normpath(p)) if p else ""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    # Tự động migrate các cột mới nếu bảng đã tạo từ trước
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(films)").fetchall()}
        if cols:
            if "block_status" not in cols:
                con.execute("ALTER TABLE films ADD COLUMN block_status TEXT DEFAULT 'unknown'")
            if "block_reason" not in cols:
                con.execute("ALTER TABLE films ADD COLUMN block_reason TEXT")
            if "checked_at" not in cols:
                con.execute("ALTER TABLE films ADD COLUMN checked_at TEXT")
    except Exception:
        pass
    try:
        lb_cols = {r["name"] for r in con.execute("PRAGMA table_info(library_blocks)").fetchall()}
        if lb_cols and "recheck_at" not in lb_cols:
            con.execute("ALTER TABLE library_blocks ADD COLUMN recheck_at TEXT")
    except Exception:
        pass
    con.executescript(SCHEMA)
    try:
        con.execute("CREATE INDEX IF NOT EXISTS films_block ON films(block_status)")
    except Exception:
        pass
    return con


def log_event(job_id: str, kind: str, detail: Any = "") -> None:
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    with _lock, connect() as con:
        con.execute("INSERT INTO events(ts, job_id, kind, detail) VALUES (?,?,?,?)",
                    (_utc(), job_id, kind, detail[:2000]))


# ─── đọc manifest ────────────────────────────────────────────────────────

def _status_of(stages: dict) -> tuple[str, str]:
    running = next((n for n, s in stages.items() if isinstance(s, dict) and s.get("status") == "running"), "")
    if running:
        return "running", running
    failed = next((n for n, s in stages.items()
                   if isinstance(s, dict) and s.get("status") in ("failed", "error")), "")
    if failed:
        return "failed", failed
    if (stages.get("mux") or {}).get("status") == "done":
        return "done", "mux"
    done = [n for n in CORE_STAGES if (stages.get(n) or {}).get("status") == "done"]
    return "pending", done[-1] if done else ""


def _series_season(title: str, series_id: str | None) -> tuple[str, int | None]:
    try:
        import agents.hongguo_bridge as hg  # noqa: PLC0415
        core = hg.franchise_core(title)
        key = hg.season_timeline_key(title, series_id)
        return core, int(key[2]) if key[2] else None
    except Exception:  # noqa: BLE001
        return title, None


def _num(v, cast=float):
    try:
        return cast(float(v))
    except (TypeError, ValueError):
        return None


def _is_upload_lock_active(job_id: str) -> bool:
    """Kiểm tra nhanh xem .upload.lock của job còn thuộc về tiến trình đang sống không."""
    for lock_path in (WORK / job_id / ".upload.lock", ROOT / "data" / f".upload_{job_id}.lock"):
        if not lock_path.is_file():
            continue
        try:
            content = lock_path.read_text(encoding="utf-8").strip().split()
            owner_pid = int(content[0]) if content else -1
            mtime = lock_path.stat().st_mtime
            if owner_pid > 0:
                import ctypes
                import time
                h = ctypes.windll.kernel32.OpenProcess(0x1000, False, owner_pid)
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
                    if time.time() - mtime < 14400.0:
                        return True
        except Exception:
            pass
    return False


def row_from_manifest(job_id: str, m: dict) -> dict:
    req = m.get("request") or {}
    stages = m.get("stages") or {}
    dl = stages.get("download") or {}
    mux = stages.get("mux") or {}
    opts = m.get("queue_opts") or {}
    title = req.get("title") or dl.get("title") or job_id
    clean = title.replace(" (merged)", "").strip()
    src = req.get("orig_path") or req.get("url") or dl.get("source_url") or ""
    hg_name = derive_hg_name(job_id, req, clean)
    status, stage = _status_of(stages)
    if status == "done":
        if m.get("yt_url") or m.get("upload_status") == "uploaded":
            status = "uploaded"
        elif m.get("upload_status") == "uploading" and _is_upload_lock_active(job_id):
            status = "uploading"
    out = mux.get("output_path") or ""
    out_bytes = None
    if out and status in ("done", "uploaded", "uploading"):
        try:
            out_bytes = Path(out).stat().st_size
        except OSError:
            out_bytes = None
    series, season = _series_season(clean, None)
    return {
        "job_id": job_id, "title": clean, "title_vi": m.get("title_vi") or "",
        "series": series, "season": season,
        "group_id": req.get("group_id"), "group_title": req.get("group_title"),
        "hg_name": hg_name, "source_path": src, "source_key": norm_path(src) if req.get("orig_path") else src,
        "status": status, "stage": stage,
        "duration_sec": _num(dl.get("duration_sec")), "width": _num(mux.get("width") or dl.get("width"), int),
        "height": _num(mux.get("height") or dl.get("height"), int),
        "output_path": out, "output_bytes": out_bytes,
        "voice": opts.get("voice") or (stages.get("tts") or {}).get("voice"),
        "tts_engine": opts.get("tts_engine") or (stages.get("tts") or {}).get("engine"),
        "audio_mode": opts.get("audio_mode"),
        "opts_json": json.dumps(opts, ensure_ascii=False) if opts else None,
        "created_at": m.get("created_at"), "finished_at": _to_iso(mux.get("finished_at")) if status in ("done", "uploaded", "uploading") else None,
    }


# ─── đồng bộ ─────────────────────────────────────────────────────────────

_SYNC_COLS = ("title", "title_vi", "series", "season", "group_id", "group_title", "hg_name", "source_path",
              "source_key", "status", "stage", "duration_sec", "width", "height", "output_path", "output_bytes",
              "voice", "tts_engine", "audio_mode", "opts_json", "created_at", "finished_at")


def sync_one(job_id: str) -> dict | None:
    """Đồng bộ tức thì 1 job từ work/<job_id>/manifest.json vào films.db."""
    job_id = str(job_id or "").strip()
    if not job_id:
        return None
    mf = WORK / job_id / "manifest.json"
    if not mf.is_file():
        return get(job_id)
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
        r = row_from_manifest(job_id, m)
    except Exception:
        return get(job_id)
    now = _utc()
    with _lock, connect() as con:
        prev_row = con.execute("SELECT * FROM films WHERE job_id=?", (job_id,)).fetchone()
        prev = dict(prev_row) if prev_row else None
        if prev is None:
            cols = ["job_id", *_SYNC_COLS, "work_exists", "updated_at"]
            con.execute(f"INSERT INTO films({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [r["job_id"], *[r[c] for c in _SYNC_COLS], 1, now])
            con.execute("INSERT INTO events(ts, job_id, kind, detail) VALUES (?,?,?,?)",
                        (now, r["job_id"], "created", r["status"]))
        else:
            vals = {c: r[c] for c in _SYNC_COLS}
            if r["output_bytes"] is None and prev.get("output_bytes"):
                vals["output_bytes"] = prev["output_bytes"]
            if prev.get("status") == "uploaded" or prev.get("yt_url"):
                if r.get("status") in ("done", "uploading"):
                    vals["status"] = "uploaded"
            elif prev.get("status") == "uploading" and r.get("status") == "done" and _is_upload_lock_active(job_id):
                vals["status"] = "uploading"
            diff = {c: v for c, v in vals.items() if prev.get(c) != v} or {}
            if diff or not prev.get("work_exists"):
                sets = ", ".join(f"{c}=?" for c in diff) + (", " if diff else "") + "work_exists=1, updated_at=?"
                con.execute(f"UPDATE films SET {sets} WHERE job_id=?", [*diff.values(), now, r["job_id"]])
        updated = con.execute("SELECT * FROM films WHERE job_id=?", (job_id,)).fetchone()
        return dict(updated) if updated else None


def sync(hg_library: list[dict] | None = None) -> dict:
    """Quét manifest → upsert films; ghi event khi trạng thái đổi. Trả {new, changed, total}."""
    rows = []
    if WORK.is_dir():
        for d in WORK.iterdir():
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            try:
                rows.append(row_from_manifest(d.name, json.loads(mf.read_text(encoding="utf-8"))))
            except Exception:  # noqa: BLE001
                continue
    live = {r["job_id"] for r in rows}
    new, changed = [], []
    now = _utc()
    with _lock, connect() as con:
        old = {r["job_id"]: dict(r) for r in con.execute("SELECT * FROM films")}
        for r in rows:
            prev = old.get(r["job_id"])
            if prev is None:
                cols = ["job_id", *_SYNC_COLS, "work_exists", "updated_at"]
                con.execute(f"INSERT INTO films({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [r["job_id"], *[r[c] for c in _SYNC_COLS], 1, now])
                con.execute("INSERT INTO events(ts, job_id, kind, detail) VALUES (?,?,?,?)",
                            (now, r["job_id"], "created", r["status"]))
                new.append(r["job_id"])
                continue
            vals = {c: r[c] for c in _SYNC_COLS}
            if r["output_bytes"] is None and prev.get("output_bytes"):
                vals["output_bytes"] = prev["output_bytes"]
            if (prev.get("status") == "uploaded" or prev.get("yt_url")) and r.get("status") in ("done", "uploading"):
                vals["status"] = "uploaded"
            elif prev.get("status") == "uploading" and r.get("status") == "done" and _is_upload_lock_active(r["job_id"]):
                vals["status"] = prev["status"]
            diff = {c: v for c, v in vals.items() if prev.get(c) != v} or {}
            if diff or not prev.get("work_exists"):
                sets = ", ".join(f"{c}=?" for c in diff) + (", " if diff else "") + "work_exists=1, updated_at=?"
                con.execute(f"UPDATE films SET {sets} WHERE job_id=?", [*diff.values(), now, r["job_id"]])
            if "status" in diff:
                con.execute("INSERT INTO events(ts, job_id, kind, detail) VALUES (?,?,?,?)",
                            (now, r["job_id"], "status", f"{prev.get('status')} → {r['status']} ({r['stage']})"))
                changed.append(r["job_id"])
        for jid, prev in old.items():
            if jid not in live and prev.get("work_exists"):
                con.execute("UPDATE films SET work_exists=0, updated_at=? WHERE job_id=?", (now, jid))
                con.execute("INSERT INTO events(ts, job_id, kind, detail) VALUES (?,?,?,?)",
                            (now, jid, "work_removed", "thư mục job đã xoá — giữ lịch sử"))
        if hg_library:
            by_name = {it.get("name"): str(it.get("series_id") or "") for it in hg_library}
            for r in con.execute("SELECT job_id, hg_name FROM films WHERE (hg_series_id IS NULL OR hg_series_id='') "
                                 "AND hg_name <> ''").fetchall():
                sid = by_name.get(r["hg_name"])
                if sid:
                    con.execute("UPDATE films SET hg_series_id=? WHERE job_id=?", (sid, r["job_id"]))
        total = con.execute("SELECT COUNT(*) FROM films").fetchone()[0]
    up = sync_uploads()
    return {"new": new, "changed": changed, "total": total, "uploads": up}


def needs_series_ids() -> bool:
    with _lock, connect() as con:
        return bool(con.execute("SELECT 1 FROM films WHERE (hg_series_id IS NULL OR hg_series_id='') "
                                "AND hg_name <> '' LIMIT 1").fetchone())


def sync_uploads() -> int:
    """Link YouTube từ Z:\\yt_uploader\\upload_history.jsonl và queue_state.json."""
    entries: list[dict[str, Any]] = []

    # 1. Quét từ queue_state.json (trạng thái hàng chờ hiện tại của yt_uploader)
    if QUEUE_STATE.is_file():
        try:
            q_data = json.loads(QUEUE_STATE.read_text(encoding="utf-8", errors="replace"))
            if isinstance(q_data, list):
                for item in q_data:
                    url = item.get("url")
                    st = str(item.get("status") or "").lower()
                    if url and ("xong" in st or "done" in st):
                        entries.append({
                            "url": url,
                            "file": item.get("path") or item.get("file") or "",
                            "title": item.get("title") or "",
                            "channel": item.get("channel") or item.get("account") or "",
                            "time": item.get("time") or "",
                        })
        except Exception:  # noqa: BLE001
            pass

    # 2. Quét từ upload_history.jsonl (lịch sử upload lâu dài)
    if YT_HISTORY.is_file():
        try:
            for line in YT_HISTORY.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    e = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                url = e.get("url")
                st = str(e.get("status") or "").lower()
                # Chấp nhận mọi trạng thái đã upload ("xong", "xong (đang xử lý)", "xong (công khai)", "done")
                if url and ("xong" in st or "done" in st):
                    entries.append({
                        "url": url,
                        "file": e.get("file") or e.get("path") or "",
                        "title": e.get("title") or "",
                        "channel": e.get("channel") or e.get("name") or "",
                        "time": e.get("time") or "",
                    })
        except Exception:  # noqa: BLE001
            pass

    if not entries:
        return 0

    n = 0
    with _lock, connect() as con:
        rows = con.execute("SELECT job_id, title, title_vi, yt_url FROM films").fetchall()
        ids = {r[0] for r in rows}
        title_to_jid: dict[str, str] = {}
        for r in rows:
            jid = r[0]
            if r[1]:
                title_to_jid[str(r[1]).strip().lower()] = jid
            if r[2]:
                title_to_jid[str(r[2]).strip().lower()] = jid

        for e in entries:
            p = Path(e["file"])
            # Match theo job_id trong đường dẫn (bất kỳ thư mục con nào khớp job_id)
            jid = next((part for part in p.parts if part in ids), None)
            
            # Match fallback theo tiêu đề phim / tên tiếng Việt nếu không có job_id trong path
            if not jid and e.get("title"):
                clean_t = str(e["title"]).strip().lower()
                jid = title_to_jid.get(clean_t)

            if not jid:
                continue

            cur = con.execute("SELECT yt_url FROM films WHERE job_id=?", (jid,)).fetchone()
            if cur and cur[0] == e["url"]:
                continue
            con.execute("UPDATE films SET yt_url=?, yt_channel=?, uploaded_at=? WHERE job_id=?",
                        (e["url"], e.get("channel") or "", e.get("time"), jid))
            con.execute("INSERT INTO events(ts, job_id, kind, detail) VALUES (?,?,?,?)",
                        (_utc(), jid, "uploaded", f"{e['url']} · {e.get('channel') or ''}"))
            n += 1
    return n


# ─── tra cứu ─────────────────────────────────────────────────────────────

def source_keys() -> set[str]:
    with _lock, connect() as con:
        return {r[0] for r in con.execute("SELECT source_key FROM films WHERE source_key <> ''")}


def series_ids() -> set[str]:
    with _lock, connect() as con:
        return {r[0] for r in con.execute("SELECT hg_series_id FROM films WHERE hg_series_id <> ''")}


def update_block_status(job_id: str, status: str, reason: str = "") -> dict:
    """Cập nhật trạng thái bản quyền (clean/blocked/claim/private/unknown) và lý do cho 1 phim."""
    jid = str(job_id or "").strip()
    if not jid:
        return {"ok": False, "error": "job_id không hợp lệ"}
    now = _utc()
    status = str(status or "unknown").strip().lower()
    reason = str(reason or "").strip()
    with _lock, connect() as con:
        con.execute(
            "UPDATE films SET block_status=?, block_reason=?, checked_at=?, updated_at=? WHERE job_id=?",
            (status, reason, now, now, jid),
        )
        con.execute(
            "INSERT INTO events(ts, job_id, kind, detail) VALUES (?,?,?,?)",
            (now, jid, "block_check", f"{status}: {reason}"[:2000]),
        )
    return {"ok": True, "job_id": jid, "block_status": status, "block_reason": reason, "checked_at": now}


def query(q: str = "", status: str = "", limit: int = 50, order: str = "recent",
          work_exists: str | int | None = None, block_status: str = "") -> list[dict]:
    sql = "SELECT * FROM films WHERE 1=1"
    args: list[Any] = []
    if q:
        like = f"%{q}%"
        sql += " AND (title LIKE ? OR title_vi LIKE ? OR series LIKE ? OR group_title LIKE ? OR job_id = ?)"
        args += [like, like, like, like, q]
    if status:
        if status == "uploaded":
            sql += " AND yt_url <> ''"
        elif status == "not_uploaded":
            sql += " AND status='done' AND (yt_url IS NULL OR yt_url='')"
        else:
            sql += " AND status=?"
            args.append(status)
    if block_status:
        if block_status == "has_block":
            sql += " AND block_status IN ('blocked', 'claim')"
        else:
            sql += " AND block_status=?"
            args.append(block_status)
    if work_exists is not None and str(work_exists).strip() != "":
        val = 1 if str(work_exists).lower() in ("1", "true", "yes") else 0
        sql += " AND work_exists = ?"
        args.append(val)
    sql += {"recent": " ORDER BY COALESCE(finished_at, created_at) DESC",
            "series": " ORDER BY series, season, title"}.get(order, " ORDER BY created_at DESC")
    sql += " LIMIT ?"
    args.append(max(1, min(int(limit or 50), 5000)))
    with _lock, connect() as con:
        return [dict(r) for r in con.execute(sql, args)]


def get(job_id: str) -> dict | None:
    with _lock, connect() as con:
        r = con.execute("SELECT * FROM films WHERE job_id=?", (job_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["events"] = [dict(e) for e in con.execute(
            "SELECT ts, kind, detail FROM events WHERE job_id=? ORDER BY id", (job_id,))]
        return d


def delete_film(job_id: str, delete_work: bool = True) -> dict:
    """Xoá 1 film khỏi database và tuỳ chọn xoá thư mục work trên ổ đĩa."""
    jid = str(job_id or "").strip()
    if not jid:
        return {"ok": False, "error": "job_id không hợp lệ"}
    work_deleted = False
    if delete_work:
        try:
            root = WORK.resolve()
            p = (WORK / jid).resolve()
            if (p.parent == root or p.parent == WORK) and p.is_dir():
                shutil.rmtree(p)
                work_deleted = True
        except Exception as e:
            pass
    with _lock, connect() as con:
        con.execute("DELETE FROM events WHERE job_id=?", (jid,))
        n = con.execute("DELETE FROM films WHERE job_id=?", (jid,)).rowcount
    return {"ok": True, "job_id": jid, "deleted": bool(n), "work_deleted": work_deleted}


def delete_films(job_ids: list[str], delete_work: bool = True) -> dict:
    """Xoá nhiều film theo danh sách job_id."""
    ids = [str(x).strip() for x in job_ids if str(x).strip()]
    if not ids:
        return {"ok": False, "deleted_count": 0, "deleted_ids": [], "error": "Danh sách rỗng"}
    work_deleted_count = 0
    if delete_work:
        root = WORK.resolve()
        for jid in ids:
            try:
                p = (WORK / jid).resolve()
                if (p.parent == root or p.parent == WORK) and p.is_dir():
                    shutil.rmtree(p)
                    work_deleted_count += 1
            except Exception:
                pass
    with _lock, connect() as con:
        placeholders = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM events WHERE job_id IN ({placeholders})", ids)
        n = con.execute(f"DELETE FROM films WHERE job_id IN ({placeholders})", ids).rowcount
    return {"ok": True, "deleted_count": n, "work_deleted_count": work_deleted_count, "deleted_ids": ids}


def purge_missing(only_pending_failed: bool = False) -> dict:
    """Xoá toàn bộ hoặc các job chưa hoàn thành mà thư mục work đã bị xoá (work_exists = 0)."""
    with _lock, connect() as con:
        sql = "SELECT job_id FROM films WHERE work_exists = 0"
        if only_pending_failed:
            sql += " AND status IN ('pending', 'failed')"
        target_ids = [r[0] for r in con.execute(sql).fetchall()]
        if not target_ids:
            return {"ok": True, "count": 0, "ids": []}
        placeholders = ",".join("?" * len(target_ids))
        con.execute(f"DELETE FROM events WHERE job_id IN ({placeholders})", target_ids)
        n = con.execute(f"DELETE FROM films WHERE job_id IN ({placeholders})", target_ids).rowcount
    return {"ok": True, "count": n, "ids": target_ids}


def stats() -> dict:
    with _lock, connect() as con:
        c = {r[0]: r[1] for r in con.execute("SELECT status, COUNT(*) FROM films GROUP BY status")}
        tot = con.execute("SELECT COUNT(*), COALESCE(SUM(CASE WHEN status='done' THEN duration_sec END),0), "
                          "SUM(CASE WHEN yt_url <> '' THEN 1 ELSE 0 END), COUNT(DISTINCT series) FROM films").fetchone()
        we_rows = con.execute("SELECT work_exists, COUNT(*) FROM films GROUP BY work_exists").fetchall()
        we_map = {r[0]: r[1] for r in we_rows}
        series = [dict(r) for r in con.execute(
            "SELECT series, COUNT(*) n, SUM(status='done') done, SUM(yt_url <> '') up FROM films "
            "GROUP BY series ORDER BY MAX(created_at) DESC LIMIT 15")]
        week = con.execute("SELECT COUNT(*) FROM films WHERE status='done' AND finished_at >= ?",
                           (datetime.fromtimestamp(datetime.now().timestamp() - 7 * 86400, timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%S"),)).fetchone()[0]
        by_block = {r[0]: r[1] for r in con.execute("SELECT COALESCE(block_status, 'unknown'), COUNT(*) FROM films WHERE yt_url <> '' GROUP BY block_status")}
    return {"by_status": c, "total": tot[0], "done_hours": round((tot[1] or 0) / 3600, 1),
            "uploaded": tot[2] or 0, "n_series": tot[3], "done_7d": week, "series": series,
            "work_exists": we_map.get(1, 0), "work_missing": we_map.get(0, 0),
            "by_block": by_block}


def to_csv(rows: list[dict]) -> str:
    cols = ["job_id", "title", "title_vi", "series", "season", "status", "stage", "duration_sec",
            "created_at", "finished_at", "output_path", "yt_url", "yt_channel", "uploaded_at", "source_path",
            "voice", "tts_engine", "work_exists", "block_status", "block_reason", "checked_at"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def _dur(sec) -> str:
    if not sec:
        return "?"
    m = int(sec) // 60
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m}m"


def format_stats() -> str:
    s = stats()
    b = s["by_status"]
    lines = [
        f"Database phim: {s['total']} job · {s['n_series']} bộ",
        f"xong {b.get('done', 0)} ({s['done_hours']} giờ phim) · đang chạy {b.get('running', 0)} · "
        f"chờ {b.get('pending', 0)} · lỗi {b.get('failed', 0)} · đã upload {s['uploaded']}",
        f"7 ngày qua xong {s['done_7d']} phim",
        "Theo bộ (mới nhất):",
    ]
    lines += [f"  {x['series']}: {x['done'] or 0}/{x['n']} xong · {x['up'] or 0} upload" for x in s["series"]]
    return "\n".join(lines)


def format_rows(rows: list[dict]) -> str:
    if not rows:
        return "Không có phim nào khớp."
    icon = {"done": "✓", "running": "▶", "failed": "✗", "pending": "·"}
    out = []
    for r in rows:
        name = r["title_vi"] or r["title"]
        extra = " · ▲YT" if r.get("yt_url") else ""
        gone = " · (đã xoá thư mục)" if not r.get("work_exists") else ""
        day = (r.get("finished_at") or r.get("created_at") or "")[:10]
        out.append(f"{icon.get(r['status'], '?')} {name} · {_dur(r.get('duration_sec'))} · {day}{extra}{gone} [{r['job_id']}]")
    return "\n".join(out)


def format_detail(job_id: str) -> str:
    d = get(job_id)
    if not d:
        return f"Không thấy job {job_id} trong database."
    lines = [
        f"{d['title_vi'] or d['title']}",
        f"Gốc: {d['title']} · bộ {d['series']}" + (f" mùa {d['season']}" if d.get("season") else ""),
        f"Trạng thái: {d['status']} ({d['stage']}) · dài {_dur(d.get('duration_sec'))}"
        + (f" · {d['width']}x{d['height']}" if d.get("width") else ""),
        f"Tạo {d['created_at'][:16] if d.get('created_at') else '?'} · xong {(d.get('finished_at') or '-')[:16]}",
        f"Giọng {d.get('voice') or '?'} · {d.get('tts_engine') or '?'} · âm {d.get('audio_mode') or '?'}",
        f"Nguồn: {d['source_path']}",
        f"Output: {d.get('output_path') or '-'}",
        f"YouTube: {d.get('yt_url') or 'chưa upload'}" + (f" ({d['yt_channel']})" if d.get("yt_channel") else ""),
        "Lịch sử:",
    ]
    lines += [f"  {e['ts'][:16]} {e['kind']}: {e['detail']}" for e in d["events"][-12:]]
    return "\n".join(lines)


# ─── Quản lý bản quyền Thư viện phim gốc (library_blocks) ───────────────

def upsert_library_block(
    name: str,
    core: str = "",
    title_vi: str = "",
    video_path: str = "",
    status: str = "unknown",
    reason: str = "",
    test_yt_url: str = "",
    test_yt_id: str = "",
    test_sample_path: str = "",
    recheck_at: str | None = None,
) -> dict[str, Any]:
    """Lưu hoặc cập nhật trạng thái bản quyền phim trong Thư viện gốc."""
    now = _utc()
    with _lock, connect() as con:
        cur = con.execute("SELECT * FROM library_blocks WHERE name=?", (name,)).fetchone()
        if cur:
            if recheck_at is not None:
                con.execute(
                    """
                    UPDATE library_blocks
                    SET core=COALESCE(NULLIF(?,''), core),
                        title_vi=COALESCE(NULLIF(?,''), title_vi),
                        video_path=COALESCE(NULLIF(?,''), video_path),
                        status=?,
                        reason=?,
                        test_yt_url=COALESCE(NULLIF(?,''), test_yt_url),
                        test_yt_id=COALESCE(NULLIF(?,''), test_yt_id),
                        test_sample_path=COALESCE(NULLIF(?,''), test_sample_path),
                        recheck_at=?,
                        tested_at=?,
                        updated_at=?
                    WHERE name=?
                    """,
                    (core, title_vi, video_path, status, reason, test_yt_url, test_yt_id, test_sample_path, recheck_at, now, now, name),
                )
            else:
                con.execute(
                    """
                    UPDATE library_blocks
                    SET core=COALESCE(NULLIF(?,''), core),
                        title_vi=COALESCE(NULLIF(?,''), title_vi),
                        video_path=COALESCE(NULLIF(?,''), video_path),
                        status=?,
                        reason=?,
                        test_yt_url=COALESCE(NULLIF(?,''), test_yt_url),
                        test_yt_id=COALESCE(NULLIF(?,''), test_yt_id),
                        test_sample_path=COALESCE(NULLIF(?,''), test_sample_path),
                        tested_at=?,
                        updated_at=?
                    WHERE name=?
                    """,
                    (core, title_vi, video_path, status, reason, test_yt_url, test_yt_id, test_sample_path, now, now, name),
                )
        else:
            con.execute(
                """
                INSERT INTO library_blocks (name, core, title_vi, video_path, status, reason, test_yt_url, test_yt_id, test_sample_path, recheck_at, tested_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, core, title_vi, video_path, status, reason, test_yt_url, test_yt_id, test_sample_path, recheck_at or "", now, now),
            )
        row = con.execute("SELECT * FROM library_blocks WHERE name=?", (name,)).fetchone()
        return dict(row) if row else {}


def get_library_block(name: str) -> dict[str, Any] | None:
    with _lock, connect() as con:
        r = con.execute("SELECT * FROM library_blocks WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None


def list_pending_rechecks() -> list[dict[str, Any]]:
    """Lấy danh sách các bản ghi đang chờ recheck (status='testing')."""
    with _lock, connect() as con:
        rows = con.execute(
            "SELECT * FROM library_blocks WHERE status='testing' AND (test_yt_id IS NOT NULL AND test_yt_id != '') ORDER BY recheck_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def list_library_blocks() -> dict[str, dict[str, Any]]:
    """Trả về dict name -> record của tất cả các bộ đã check trong Thư viện."""
    with _lock, connect() as con:
        rows = con.execute("SELECT * FROM library_blocks").fetchall()
        return {r["name"]: dict(r) for r in rows}


def delete_library_block(name: str) -> bool:
    with _lock, connect() as con:
        res = con.execute("DELETE FROM library_blocks WHERE name=?", (name,))
        return res.rowcount > 0


# ─── Hongguo watchlist (meta sau khi xoá Lib — quét mùa mới không cần đĩa) ───

_SID_RE = __import__("re").compile(r"^\d{15,20}$")


def upsert_hg_watchlist(
    *,
    sid: str,
    title: str = "",
    name: str = "",
    core: str = "",
    season: int | None = None,
    eps: int | None = None,
    score: str = "",
    album: str = "",
    source: str = "library",
    on_disk: int | None = None,
    deleted: bool = False,
) -> dict[str, Any]:
    """Ghi/ cập nhật 1 series Hongguo vào watchlist (chỉ meta, không video)."""
    sid = str(sid or "").strip()
    if not _SID_RE.match(sid):
        return {"ok": False, "error": "sid không hợp lệ"}
    title = str(title or name or sid).strip()
    name = str(name or "").strip()
    if not core:
        try:
            import agents.hongguo_bridge as hg  # noqa: PLC0415
            core = hg.franchise_core(title) or ""
        except Exception:  # noqa: BLE001
            core = ""
    now = _utc()
    with _lock, connect() as con:
        prev = con.execute("SELECT * FROM hg_watchlist WHERE sid=?", (sid,)).fetchone()
        if deleted:
            on_disk_v = 0
            deleted_at = now
        else:
            on_disk_v = 1 if on_disk is None else int(on_disk)
            deleted_at = (prev["deleted_at"] if prev else None) if on_disk_v else now
            if on_disk_v:
                deleted_at = None
        if prev:
            con.execute(
                """UPDATE hg_watchlist SET core=?, title=?, name=COALESCE(NULLIF(?,''), name),
                   season=COALESCE(?, season), eps=COALESCE(?, eps), score=COALESCE(NULLIF(?,''), score),
                   album=COALESCE(NULLIF(?,''), album), source=?, on_disk=?, deleted_at=?,
                   seen_at=?, updated_at=? WHERE sid=?""",
                (
                    core or prev["core"], title, name,
                    season, eps, score, album, source, on_disk_v, deleted_at,
                    now, now, sid,
                ),
            )
        else:
            con.execute(
                """INSERT INTO hg_watchlist
                   (sid, core, title, name, season, eps, score, album, source, on_disk, deleted_at, seen_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, core, title, name, season, eps, score, album, source, on_disk_v, deleted_at, now, now),
            )
        row = con.execute("SELECT * FROM hg_watchlist WHERE sid=?", (sid,)).fetchone()
    return {"ok": True, **dict(row)}


def upsert_hg_watchlist_many(items: list[dict[str, Any]], *, source: str = "library",
                             on_disk: int = 1, deleted: bool = False) -> int:
    n = 0
    for it in items or []:
        sid = str(it.get("sid") or it.get("series_id") or "").strip()
        if not _SID_RE.match(sid):
            continue
        upsert_hg_watchlist(
            sid=sid,
            title=str(it.get("title") or it.get("name") or ""),
            name=str(it.get("name") or ""),
            core=str(it.get("core") or ""),
            season=it.get("season") if it.get("season") is not None else None,
            eps=it.get("eps") if it.get("eps") is not None else (
                it.get("total") if it.get("total") is not None else None
            ),
            score=str(it.get("score") or ""),
            album=str(it.get("album") or ""),
            source=source,
            on_disk=on_disk,
            deleted=deleted,
        )
        n += 1
    return n


def mark_hg_watchlist_deleted_by_names(names: list[str]) -> int:
    """Đánh dấu xoá theo tên folder Lib (giữ sid/core)."""
    names = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not names:
        return 0
    now = _utc()
    with _lock, connect() as con:
        n = 0
        for nm in names:
            cur = con.execute(
                "UPDATE hg_watchlist SET on_disk=0, deleted_at=?, updated_at=? WHERE name=? OR title=?",
                (now, now, nm, nm),
            )
            n += cur.rowcount
        return n


def list_hg_watchlist(include_deleted: bool = True) -> list[dict[str, Any]]:
    with _lock, connect() as con:
        if include_deleted:
            rows = con.execute("SELECT * FROM hg_watchlist ORDER BY updated_at DESC").fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM hg_watchlist WHERE on_disk=1 ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def known_hg_sids() -> set[str]:
    """Mọi sid đã biết: films + watchlist."""
    with _lock, connect() as con:
        a = {r[0] for r in con.execute(
            "SELECT hg_series_id FROM films WHERE hg_series_id IS NOT NULL AND hg_series_id<>''"
        )}
        b = {r[0] for r in con.execute("SELECT sid FROM hg_watchlist")}
    return {s for s in (a | b) if _SID_RE.match(str(s))}


def watchlist_cores(limit: int = 40) -> list[str]:
    """Franchise cores để quét mùa mới (watchlist + films.series).

    Chuẩn hoá qua franchise_core để bỏ 「第N季」 còn sót trong core/series.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _stem(raw: str) -> str:
        c = str(raw or "").strip()
        if not c:
            return ""
        try:
            import agents.hongguo_bridge as hg  # noqa: PLC0415
            return (hg.franchise_core(c) or c).strip()
        except Exception:  # noqa: BLE001
            try:
                import agents.hongguo_bridge as hg  # noqa: PLC0415
                return (hg.franchise_core(c) or c).strip()
            except Exception:  # noqa: BLE001
                return c

    def _add(raw: str) -> None:
        c = _stem(raw)
        if c and c not in seen:
            seen.add(c)
            out.append(c)

    with _lock, connect() as con:
        rows = con.execute(
            """SELECT core, MAX(updated_at) u FROM hg_watchlist
               WHERE core IS NOT NULL AND core<>'' GROUP BY core ORDER BY u DESC LIMIT ?""",
            (max(1, int(limit)),),
        ).fetchall()
        for r in rows:
            _add(r[0])
        if len(out) < limit:
            for r in con.execute(
                """SELECT series, MAX(updated_at) u FROM films
                   WHERE series IS NOT NULL AND series<>'' GROUP BY series ORDER BY u DESC LIMIT ?""",
                (max(1, int(limit)),),
            ):
                _add(r[0])
                if len(out) >= limit:
                    break
    return out


def sync_library_blocks_from_yt_queue(check_live: bool = False) -> list[dict[str, Any]]:
    """Đồng bộ trạng thái bản quyền từ Z:\\yt_uploader\\queue_state.json cho các video D:\\Lib."""
    q_path = YT_DIR / "queue_state.json"
    if not q_path.is_file():
        return []
    try:
        data = json.loads(q_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    import engines.check_block as check_block
    import importlib
    check_block = importlib.reload(check_block)

    updated = []
    for item in data:
        p = item.get("path") or ""
        u = item.get("url") or ""
        if not p or not _machine.in_library(p) or not u:
            continue
        # Tên mùa là tên thư mục cha trực tiếp của file merged/video trong D:\Lib
        path_obj = Path(p)
        name = path_obj.parent.name
        vid = check_block.extract_video_id(u)
        
        status = "unknown"
        reason = ""
        if check_live:
            res = check_block.check_youtube_block(u)
            status = res.get("status") or "unknown"
            reason = res.get("reason") or ""
        else:
            # Nếu chưa check live, chỉ kiểm tra nếu đã có trong db hoặc để unknown
            existing = get_library_block(name)
            if existing and existing.get("status") and existing["status"] != "unknown":
                status = existing["status"]
                reason = existing.get("reason") or ""

        rec = upsert_library_block(
            name=name,
            video_path=p,
            status=status,
            reason=reason,
            test_yt_url=u,
            test_yt_id=vid,
        )
        updated.append(rec)
    return updated

