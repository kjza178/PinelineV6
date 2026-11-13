"""hongguo_rpc.py — JSON-RPC stdio client cho Hongguo WinUI 2.x helper.jar.

Không dùng HTTP :8000 (bản 1.x). Spawn engine local:

  …/HongguoWinUI/current/engine/runtime/bin/java.exe
  -cp lib\\* com.hongguo.helper.MainKt

Mỗi dòng stdin/stdout = 1 JSON-RPC 2.0 object.
"""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_ENGINE_DIR = Path(
    os.environ.get("HG_ENGINE_DIR")
    or (Path(os.environ.get("LOCALAPPDATA", "")) / "HongguoWinUI" / "current" / "engine")
)

_PROTOCOL = 2
# Bridge hot-reload marker: feed.select phải là Map→List[str]
_FEED_SELECT_AS_LIST = True
_PLAYBACK_PREPARE_V = 3

# 0 = không idle-kill (giống WinUI giữ helper sống). Mặc định 1h.
_IDLE_SEC = float(os.environ.get("HG_RPC_IDLE_SEC") or "3600")
_CALL_TIMEOUT = float(os.environ.get("HG_RPC_TIMEOUT") or "60")
# 1 = chỉ 1 MainKt Pineline trên máy (không đụng helper của HongguoWinUI.exe).
_SINGLETON = (os.environ.get("HG_HELPER_SINGLETON") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

_lock = threading.RLock()
_proc: subprocess.Popen | None = None
_reader: threading.Thread | None = None
_pending: dict[int, dict[str, Any]] = {}
_notes: dict[str, Any] = {}  # method -> last params
_next_id = 1
_last_used = 0.0
_hello: dict[str, Any] | None = None
_stderr_tail: list[str] = []
_pid_file = Path(
    os.environ.get("HG_HELPER_PIDFILE")
    or (Path(os.environ.get("LOCALAPPDATA", "")) / "Pineline" / "hg_helper.pid")
)


class HgRpcError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data

    def as_dict(self) -> dict:
        d: dict[str, Any] = {"error": str(self)}
        if self.code is not None:
            d["rpc_code"] = self.code
        if self.data is not None:
            d["data"] = self.data
        return d


def engine_dir() -> Path:
    return Path(DEFAULT_ENGINE_DIR)


def helper_available(edir: Path | None = None) -> bool:
    d = Path(edir or engine_dir())
    java = d / "runtime" / "bin" / "java.exe"
    jar = d / "lib" / "helper.jar"
    return java.is_file() and jar.is_file()


def _java_cmd(d: Path) -> list[str]:
    java = d / "runtime" / "bin" / "java.exe"
    return [
        str(java),
        "-Xlog:disable",
        "-Xlog:all=warning:stderr",
        "-XX:+DisableAttachMechanism",
        "-Dhongguo.signer.dir=signer",
        "-Dhongguo.ffmpeg=ffmpeg\\ffmpeg.exe",
        "-Djna.boot.library.path=lib\\native",
        "-Djna.nosys=true",
        "-Djna.nounpack=true",
        "-Dfile.encoding=UTF-8",
        "-cp",
        "lib\\*",
        "com.hongguo.helper.MainKt",
    ]


def _on_line(line: str) -> None:
    line = (line or "").strip()
    if not line:
        return
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return
    if not isinstance(msg, dict):
        return
    mid = msg.get("id")
    if mid is not None and "method" not in msg:
        # response
        with _lock:
            slot = _pending.get(int(mid))
            if slot is not None:
                slot["msg"] = msg
                slot["ev"].set()
        return
    method = msg.get("method")
    if method:
        with _lock:
            _notes[str(method)] = msg.get("params") or {}


def _reader_loop(proc: subprocess.Popen) -> None:
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            _on_line(raw)
    except Exception:  # noqa: BLE001
        pass
    finally:
        with _lock:
            for slot in _pending.values():
                if not slot["ev"].is_set():
                    slot["msg"] = {
                        "jsonrpc": "2.0",
                        "id": slot.get("id"),
                        "error": {"code": -32000, "message": "helper exited"},
                    }
                    slot["ev"].set()


def _stderr_loop(proc: subprocess.Popen) -> None:
    assert proc.stderr is not None
    try:
        for raw in proc.stderr:
            s = (raw or "").rstrip()
            if not s:
                continue
            with _lock:
                _stderr_tail.append(s)
                del _stderr_tail[:-30]
    except Exception:  # noqa: BLE001
        pass


def _write_pidfile(pid: int | None) -> None:
    try:
        _pid_file.parent.mkdir(parents=True, exist_ok=True)
        if pid and pid > 0:
            _pid_file.write_text(f"{pid}\n{os.getpid()}\n", encoding="utf-8")
        elif _pid_file.exists():
            _pid_file.unlink()
    except Exception:  # noqa: BLE001
        pass


def _is_helper_cmdline(cmdline: list[str] | str | None) -> bool:
    if not cmdline:
        return False
    s = " ".join(cmdline) if isinstance(cmdline, (list, tuple)) else str(cmdline)
    low = s.lower()
    return "mainkt" in low and "hongguowinui" in low.replace("/", "\\")


def reap_orphan_helpers(*, keep_pid: int | None = None, only_children: bool = True) -> int:
    """Kill MainKt Hongguo helper bị mất handle (leak). Không đụng helper của WinUI.exe.

    only_children=True: chỉ con của process hiện tại (an toàn cho server/sidecar).
    only_children=False: mọi MainKt không parent HongguoWinUI (dọn máy — dùng CLI).
    """
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return 0
    me = os.getpid()
    killed = 0
    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
        try:
            info = p.info
            pid = int(info.get("pid") or 0)
            if not pid or (keep_pid and pid == keep_pid):
                continue
            if not _is_helper_cmdline(info.get("cmdline")):
                continue
            ppid = int(info.get("ppid") or 0)
            try:
                parent_name = psutil.Process(ppid).name() if ppid else ""
            except Exception:  # noqa: BLE001
                parent_name = ""
            if parent_name.lower() == "hongguowinui.exe":
                continue
            if only_children and ppid != me:
                continue
            p.terminate()
            try:
                p.wait(timeout=2)
            except Exception:  # noqa: BLE001
                p.kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # type: ignore[name-defined]
            continue
        except Exception:  # noqa: BLE001
            continue
    return killed


def _kill_unlocked() -> None:
    global _proc, _reader, _hello
    proc = _proc
    _proc = None
    _reader = None
    _hello = None
    keep = None
    if proc is not None:
        keep = proc.pid
        try:
            if proc.stdin and proc.poll() is None:
                proc.stdin.write(
                    json.dumps({"jsonrpc": "2.0", "id": 0, "method": "app.shutdown", "params": {}})
                    + "\n"
                )
                proc.stdin.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    # dọn con MainKt còn sót (mất handle sau reload)
    reap_orphan_helpers(keep_pid=None, only_children=True)
    _write_pidfile(None)


def stop() -> None:
    with _lock:
        _kill_unlocked()


def _has_active_downloads_unlocked() -> bool:
    """Peek nhanh — tránh idle-kill khi đang tải (giống WinUI giữ helper)."""
    note = _notes.get("downloads.changed") or {}
    if note.get("jobs"):
        for j in note.get("jobs") or []:
            st = str((j or {}).get("state") or (j or {}).get("status") or "").lower()
            if st in ("downloading", "queued", "running", "pending", "waiting", "active", "paused"):
                return True
    return False


def _ensure_unlocked(edir: Path | None = None) -> subprocess.Popen:
    """Đảm bảo 1 helper sống. Có thể gọi ngoài lock; tự khóa đoạn mutate."""
    global _proc, _reader, _last_used, _hello, _notes
    d = Path(edir or engine_dir())
    if not helper_available(d):
        raise HgRpcError(f"không thấy Hongguo 2.x engine tại {d}")
    with _lock:
        if _proc is not None and _proc.poll() is None:
            _last_used = time.time()
            keep = _proc.pid
            _write_pidfile(keep)
        else:
            keep = None
    if keep is not None:
        # Helper đang sống — vẫn dọn orphan (tele/powershell) để giống WinUI 1 engine.
        if _SINGLETON:
            reap_orphan_helpers(keep_pid=keep, only_children=False)
        return _proc  # type: ignore[return-value]
    with _lock:
        if _proc is not None and _proc.poll() is None:
            _last_used = time.time()
            _write_pidfile(_proc.pid)
            return _proc
        _kill_unlocked()
        _notes.pop("engine.ready", None)
        proc = subprocess.Popen(
            _java_cmd(d),
            cwd=str(d),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        _proc = proc
        _reader = threading.Thread(target=_reader_loop, args=(proc,), name="hg-rpc-out", daemon=True)
        _reader.start()
        threading.Thread(target=_stderr_loop, args=(proc,), name="hg-rpc-err", daemon=True).start()
    # chờ engine.ready — KHÔNG giữ lock (reader cần lock để ghi _notes)
    deadline = time.time() + 8.0
    while time.time() < deadline:
        with _lock:
            ready = "engine.ready" in _notes
        if ready:
            break
        if proc.poll() is not None:
            with _lock:
                if _proc is proc:
                    _proc = None
            raise HgRpcError(
                "helper thoát ngay khi start"
                + (f": {_stderr_tail[-1]}" if _stderr_tail else "")
            )
        time.sleep(0.05)
    # Singleton: dọn mọi MainKt Pineline sót (powershell/tele/reload) — giữ WinUI.
    reap_orphan_helpers(keep_pid=proc.pid, only_children=not _SINGLETON)
    with _lock:
        _write_pidfile(proc.pid)
        _last_used = time.time()
    return proc


def ensure(edir: Path | None = None) -> dict[str, Any]:
    """Đảm bảo helper sống; trả app.hello (cache)."""
    global _hello
    # Spawn/wait ngoài hold-lock dài — tránh deadlock với stdout reader
    _ensure_unlocked(edir)
    if _hello:
        return dict(_hello)
    hello = call("app.hello", {})
    with _lock:
        _hello = dict(hello or {})
    return dict(_hello)


def call(method: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
    """Gọi method JSON-RPC; trả `result` hoặc raise HgRpcError."""
    global _next_id, _last_used
    timeout = float(_CALL_TIMEOUT if timeout is None else timeout)
    params = dict(params or {})
    # ensure helper trước (có thể chờ engine.ready; không giữ lock)
    _ensure_unlocked()
    with _lock:
        proc = _proc
        if proc is None or proc.poll() is not None:
            raise HgRpcError("helper không sẵn sàng")
        _next_id += 1
        req_id = _next_id
        ev = threading.Event()
        _pending[req_id] = {"ev": ev, "msg": None, "id": req_id}
        line = json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
            ensure_ascii=False,
        )
        try:
            assert proc.stdin is not None
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except Exception as e:  # noqa: BLE001
            _pending.pop(req_id, None)
            _kill_unlocked()
            raise HgRpcError(f"không ghi được stdin helper: {e}") from e
        _last_used = time.time()

    if not ev.wait(timeout):
        with _lock:
            _pending.pop(req_id, None)
        raise HgRpcError(f"timeout {timeout:.0f}s — {method}")

    with _lock:
        slot = _pending.pop(req_id, {}) or {}
        msg = slot.get("msg") or {}
        _last_used = time.time()

    if "error" in msg:
        err = msg.get("error") or {}
        raise HgRpcError(
            str(err.get("message") or err),
            code=err.get("code"),
            data=err.get("data"),
        )
    return msg.get("result")


def note(method: str) -> Any:
    with _lock:
        return _notes.get(method)


def license_status() -> dict:
    try:
        return dict(call("license.status", {}) or {})
    except HgRpcError:
        return {"state": "unknown"}


def ping() -> dict:
    """Shape gần hongguo_bridge.ping()."""
    try:
        hello = ensure()
        st = license_status()
        settings = call("settings.get", {}) or {}
        return {
            "ok": True,
            "backend": "rpc",
            "base": f"stdio:{engine_dir()}",
            "output_dir": settings.get("saveFolder"),
            "running": False,
            "mode": "rpc",
            "engineVersion": hello.get("engineVersion"),
            "protocol": hello.get("protocol") or _PROTOCOL,
            "capabilities": hello.get("capabilities") or [],
            "license": st.get("state"),
            "has_token": st.get("state") not in (None, "none", "unknown"),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "backend": "rpc", "error": str(e)}


def _card_to_result(c: dict) -> dict:
    sid = str(c.get("id") or c.get("seriesId") or c.get("series_id") or "")
    return {
        "series_id": sid,
        "title": c.get("title") or "",
        "cover": c.get("cover") or "",
        "episode_cnt": c.get("episodes") or c.get("episode_cnt"),
        "score": c.get("score"),
        "heat": c.get("heat"),
        "tags": c.get("tags") or [],
        "remarks": c.get("remarks"),
        "intro": c.get("intro"),
    }


def search(q: str, *, page: int = 0) -> dict:
    q = (q or "").strip()
    raw = call("search.page", {"query": q, "page": int(page)}) or {}
    cards = list(raw.get("cards") or [])
    results = [_card_to_result(c) for c in cards if str(c.get("id") or "")]
    return {
        "results": results,
        "groups": [],
        "queries": [q],
        "expand": False,
        "core": "",
        "hasMore": bool(raw.get("hasMore")),
        "nextOffset": raw.get("nextOffset"),
        "searchId": raw.get("searchId"),
        "backend": "rpc",
        "raw_keys": list(raw.keys()),
    }


def rank(*, chart: str | None = None, board: str | None = None,
         category: str | None = None, offset: int = 0, limit: int = 100) -> dict:
    """rank.page + rank.more đến tối đa ``limit`` (mặc định 100).

    Helper chỉ trả ~5–10 card/lần; ``offset`` trên rank.page không paginate —
    phải gọi ``rank.more`` tuần tự.
    """
    want = max(1, min(100, int(limit or 100)))
    params: dict[str, Any] = {}
    if chart:
        params["chart"] = chart
    if board:
        params["board"] = board
    if category and category not in ("all", ""):
        params["category"] = category
    # trang đầu: xin nhiều; helper vẫn cắt ~8–10
    params["limit"] = want
    if offset:
        params["offset"] = int(offset)
    raw = call("rank.page", params) or {}
    cards = list(raw.get("cards") or [])
    seen: set[str] = set()
    ordered: list[dict] = []
    for c in cards:
        cid = str(c.get("id") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        ordered.append(c)
    has_more = bool(raw.get("hasMore"))
    chart_id = str(raw.get("chart") or chart or "")
    more_params: dict[str, Any] = {}
    if chart_id:
        more_params["chart"] = chart_id
    if board:
        more_params["board"] = board
    # gom thêm qua rank.more
    guard = 0
    while has_more and len(ordered) < want and guard < 40:
        guard += 1
        try:
            more = call("rank.more", more_params, timeout=30) or {}
        except HgRpcError:
            break
        batch = list(more.get("cards") or [])
        if not batch:
            has_more = bool(more.get("hasMore"))
            break
        for c in batch:
            cid = str(c.get("id") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            ordered.append(c)
            if len(ordered) >= want:
                break
        has_more = bool(more.get("hasMore"))
    ordered = ordered[:want]
    results = [_card_to_result(c) for c in ordered]
    return {
        "results": results,
        "charts": raw.get("charts"),
        "chart": raw.get("chart"),
        "board": raw.get("board"),
        "hasMore": has_more and len(results) >= want,
        "n": len(results),
        "backend": "rpc",
    }


def feed(
    *,
    offset: int = 0,
    select: dict[str, str] | None = None,
    panel: bool = False,
    session_id: str = "",
    limit: int = 48,
) -> dict:
    """Browse landpage — feed.page (+ trang tiếp theo session).

    Helper cố định ~20 card/lần (limit/size bị bỏ qua). Muốn 48/100 phải
    gọi lại feed.page với sessionId + nextOffset cho đủ ``limit``.

    select: map FilterRow.type → FilterItem.id (vd genre=short_play).
    panel=True: kèm selector rows (7 facet giống WinUI 2.x) — chỉ trang đầu.
    """
    want = max(1, min(200, int(limit or 48)))
    off0 = max(0, int(offset or 0))
    # Helper JsonKt.select = Map<String, List<String>> (landpage select_items).
    # String đơn bị bỏ qua → mọi genre/theme trả cùng feed.
    clean: dict[str, list[str]] = {}
    for k, v in (select or {}).items():
        key = str(k)
        if isinstance(v, (list, tuple)):
            vals = [str(x).strip() for x in v if str(x or "").strip()]
        else:
            s = str(v or "").strip()
            vals = [s] if s else []
        if vals:
            clean[key] = vals
    params: dict[str, Any] = {"offset": off0}
    if panel:
        params["panel"] = True
    if clean:
        params["select"] = clean
    sid = (session_id or "").strip()
    if sid:
        params["sessionId"] = sid
    raw = call("feed.page", params) or {}
    seen: set[str] = set()
    ordered: list[dict] = []
    rows = list(raw.get("rows") or [])
    session = str(raw.get("sessionId") or sid or "")
    has_more = bool(raw.get("hasMore"))
    next_off = raw.get("nextOffset")
    if next_off is None:
        next_off = off0 + len(list(raw.get("cards") or []))

    def _absorb(cards: list) -> None:
        for c in cards:
            cid = str(c.get("id") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            ordered.append(c)

    _absorb(list(raw.get("cards") or []))
    guard = 0
    while has_more and len(ordered) < want and guard < 25:
        guard += 1
        more_params: dict[str, Any] = {
            "offset": int(next_off or 0),
            "sessionId": session,
        }
        if clean:
            more_params["select"] = clean
        try:
            more = call("feed.page", more_params, timeout=45) or {}
        except HgRpcError:
            break
        batch = list(more.get("cards") or [])
        if not batch:
            has_more = bool(more.get("hasMore"))
            break
        before = len(ordered)
        _absorb(batch)
        if more.get("sessionId"):
            session = str(more.get("sessionId") or session)
        nxt = more.get("nextOffset")
        next_off = int(nxt) if nxt is not None else int(next_off or 0) + len(batch)
        has_more = bool(more.get("hasMore"))
        if len(ordered) == before:
            break

    ordered = ordered[:want]
    return {
        "results": [_card_to_result(c) for c in ordered],
        "hasMore": has_more and len(ordered) >= want,
        "nextOffset": int(next_off or (off0 + len(ordered))),
        "rows": rows,
        "sessionId": session,
        "backend": "rpc",
        "n": len(ordered),
    }


def library() -> dict:
    raw = call("library.get", {}) or {}
    entries = list(raw.get("entries") or [])
    # Lần đầu helper thường trả entries=[] — đợi/refresh lấy snapshot
    if not entries:
        try:
            call("library.refresh", {}, timeout=30)
        except HgRpcError:
            pass
        deadline = time.time() + 8.0
        while time.time() < deadline:
            ch = note("library.changed") or {}
            if ch.get("entries") is not None and not ch.get("scanning"):
                entries = list(ch.get("entries") or [])
                raw = {**raw, **{k: ch.get(k) for k in ("folder", "newSeasons") if k in ch}}
                break
            time.sleep(0.2)
        if not entries:
            raw = call("library.get", {}) or {}
            entries = list(raw.get("entries") or [])
    items = []
    for e in entries:
        sid = str(e.get("seriesId") or e.get("series_id") or "")
        title = e.get("title") or ""
        folder = e.get("folder") or ""
        name = Path(folder).name if folder else title
        eps = int(e.get("episodes") or 0)
        items.append({
            "name": name,
            "series_id": sid,
            "title": title,
            "local": eps,
            "total": eps,
            "new": 0,
            "fresh": 0,
            "score": "",
            "updated": e.get("updatedAt"),
            "size": e.get("bytes") or 0,
            "poster": bool(e.get("cover")),
            "cover": e.get("cover") or "",
            "folder": folder,
        })
    note_ch = note("library.changed") or {}
    new_seasons = note_ch.get("newSeasons")
    if new_seasons is None:
        new_seasons = raw.get("newSeasons") or []
    return {
        "items": items,
        "folder": raw.get("folder") or note_ch.get("folder"),
        "newSeasons": list(new_seasons or []),
        "backend": "rpc",
    }


def library_update_info() -> dict:
    """WinUI «Update info» — library.updateInfo."""
    return call("library.updateInfo", {}) or {"ok": True}


def library_refresh() -> dict:
    call("library.refresh", {})
    # events may follow
    time.sleep(0.3)
    return library()


_ACTIVE_STATES = frozenset({
    "downloading", "queued", "running", "pending", "waiting", "active", "paused",
    "preparing", "stalled",
})
_DONE_STATES = frozenset({"done", "completed", "finished", "complete"})
_FAIL_STATES = frozenset({"failed", "error", "fail"})
_DL_STOP_V = 3  # hot-reload marker — cancel one + retry license+reprove


def _job_state(j: dict) -> str:
    return str(j.get("state") or j.get("status") or "").strip().lower()


def _folder_basename(folder: str) -> str:
    """Tên thư mục tải = tên hiển thị đúng (snapshot hay để title rỗng)."""
    try:
        name = Path(folder or "").name.strip()
    except Exception:  # noqa: BLE001
        return ""
    if not name or name in (".", ".."):
        return ""
    return name


_title_cache: dict[str, str] = {}
_cover_cache: dict[str, str] = {}


def _enrich_title_cover(sid: str, *, need_title: bool, need_cover: bool) -> tuple[str, str]:
    if not sid or (not need_title and not need_cover):
        return "", ""
    if not need_title and sid in _cover_cache:
        return "", _cover_cache.get(sid) or ""
    if not need_cover and sid in _title_cache:
        return _title_cache.get(sid) or "", ""
    try:
        raw = call("series.detail", {"id": sid}, timeout=12) or {}
    except Exception:  # noqa: BLE001
        return "", ""
    title = str(raw.get("folderName") or raw.get("title") or "").strip()
    cover = str(raw.get("cover") or "").strip()
    if title:
        _title_cache[sid] = title
    if cover:
        _cover_cache[sid] = cover
    return title, cover


def _normalize_dl_job(j: dict) -> dict:
    out = dict(j or {})
    st = _job_state(out)
    out["state"] = st
    out["status"] = st
    out["series_id"] = str(out.get("seriesId") or out.get("series_id") or "")
    folder = str(out.get("folder") or "")
    title = str(out.get("title") or "").strip()
    # Ưu tiên tên folder trên đĩa (khớp Open folder); snapshot thường title=""
    title = _folder_basename(folder) or title
    cover = str(out.get("cover") or "").strip()
    sid = out["series_id"]
    if sid and (not title or not cover):
        et, ec = _enrich_title_cover(sid, need_title=not bool(title), need_cover=not bool(cover))
        title = title or et
        cover = cover or ec
    out["title"] = title
    out["cover"] = cover
    out["quality"] = out.get("quality") or ""
    out["folder"] = folder
    out["episodes"] = int(out.get("episodes") or 0)
    out["done"] = int(out.get("done") or 0)
    out["failed"] = int(out.get("failed") or 0)
    try:
        out["progress"] = float(out.get("progress") or 0)
    except (TypeError, ValueError):
        out["progress"] = 0.0
    out["bytes"] = int(out.get("bytes") or 0)
    out["bytesPerSecond"] = int(out.get("bytesPerSecond") or out.get("bytes_per_second") or 0)
    if st in _DONE_STATES:
        out["bucket"] = "done"
    elif st in _FAIL_STATES or out["failed"] > 0 and out["done"] == 0 and st not in _ACTIVE_STATES:
        out["bucket"] = "failed"
    elif st in _ACTIVE_STATES or (0 < out["progress"] < 1 and st not in _DONE_STATES):
        out["bucket"] = "active"
    else:
        out["bucket"] = st or "other"
    return out


def status() -> dict:
    snap = call("downloads.snapshot", {}) or {}
    jobs = [_normalize_dl_job(j) for j in list(snap.get("jobs") or [])]
    active = [j for j in jobs if j.get("bucket") == "active"]
    done = [j for j in jobs if j.get("bucket") == "done"]
    failed = [j for j in jobs if j.get("bucket") == "failed"]
    bps = sum(int(j.get("bytesPerSecond") or 0) for j in jobs)
    return {
        "running": bool(active),
        "mode": "rpc",
        "series": jobs,
        "jobs": jobs,
        "active": len(active),
        "done": len(done),
        "failed": len(failed),
        "all": len(jobs),
        "bytes_per_second": bps,
        "speed_mbs": round(bps / (1024 * 1024), 1),
        "backend": "rpc",
        "source": "downloads.snapshot",
    }


def cancel() -> dict:
    """Stop all active downloads (RPC chỉ có downloads.cancelAll)."""
    return call("downloads.cancelAll", {}) or {"ok": True, "stopped": "all"}


def clear_finished() -> dict:
    return call("downloads.clearFinished", {}) or {"ok": True}


def _active_jobs_from_snap(snap: dict | None = None) -> list[dict]:
    snap = snap if snap is not None else (call("downloads.snapshot", {}) or {})
    out = []
    for j in list(snap.get("jobs") or []):
        nj = _normalize_dl_job(j)
        if nj.get("bucket") == "active":
            out.append(nj)
    return out


def cancel_download(series_id: str) -> dict:
    """Stop 1 series.

    Helper 0.3.x không có downloads.cancel(seriesId) — chỉ cancelAll.
    Cách: cancelAll rồi re-queue các job active khác (giữ quality).
    """
    sid = str(series_id or "").strip()
    if not sid:
        raise HgRpcError("series_id trống")
    snap = call("downloads.snapshot", {}) or {}
    active = _active_jobs_from_snap(snap)
    target = next((j for j in active if str(j.get("series_id") or "") == sid), None)
    if not target:
        # Không còn active — coi như đã stop
        return {
            "ok": True,
            "stopped": sid,
            "restored": 0,
            "note": "not_active",
            "backend": "rpc",
        }
    others = [j for j in active if str(j.get("series_id") or "") != sid]
    call("downloads.cancelAll", {})
    restored = 0
    if others:
        requests = []
        for j in others:
            req: dict[str, Any] = {"seriesId": str(j.get("series_id") or "")}
            q = str(j.get("quality") or "").strip()
            if q:
                req["quality"] = q
            if req["seriesId"]:
                requests.append(req)
        if requests:
            try:
                _ensure_licensed_quiet()
                call("downloads.add", {"requests": requests})
                restored = len(requests)
            except Exception as e:  # noqa: BLE001
                return {
                    "ok": True,
                    "stopped": sid,
                    "restored": 0,
                    "restore_error": str(e),
                    "backend": "rpc",
                }
    return {
        "ok": True,
        "stopped": sid,
        "restored": restored,
        "backend": "rpc",
    }


def remove_download(series_id: str) -> dict:
    """Xóa khỏi danh sách. Đang tải → stop (cancel) thay vì báo lỗi under way."""
    sid = str(series_id or "").strip()
    if not sid:
        raise HgRpcError("series_id trống")
    try:
        return call("downloads.remove", {"seriesId": sid}) or {"ok": True}
    except HgRpcError as e:
        msg = str(e).lower()
        if "under way" in msg or "cancel it instead" in msg or "cancel" in msg:
            return cancel_download(sid)
        raise


def retry_download(series_id: str) -> dict:
    """Thử lại job failed — cần license giống downloads.add."""
    sid = str(series_id or "").strip()
    if not sid:
        raise HgRpcError("series_id trống")
    _ensure_licensed_quiet()
    try:
        return call("downloads.retry", {"seriesId": sid}) or {"ok": True, "retried": sid}
    except HgRpcError as e:
        msg = str(e).lower()
        # Helper báo đang chờ licence / mất proof sau spawn lại → prove force rồi thử 1 lần
        if "licence" in msg or "license" in msg:
            try:
                import agents.hongguo_license_ensure as _lic  # noqa: PLC0415
                _lic.ensure_licensed(force=True)
                return call("downloads.retry", {"seriesId": sid}) or {"ok": True, "retried": sid}
            except HgRpcError as e2:
                raise HgRpcError(
                    f"Hongguo cần license để retry. Chi tiết: {e2}",
                    code=getattr(e2, "code", None) or -32010,
                    data={"reason": "need_license"},
                ) from e2
        raise


def _ensure_licensed_quiet() -> None:
    # Classic /hongguo = Downloader :8000 — tuyệt đối không prove license
    try:
        import agents.hg_ui_ctx as _ctx  # noqa: PLC0415
        if _ctx.is_classic() or _ctx.get() == "http":
            raise HgRpcError(
                "Hongguo classic dùng Downloader :8000 — không license RPC. "
                "Mở app Hongguo Downloader (1.x) hoặc dùng /hongguo2.",
                code=-32011,
                data={"reason": "classic_no_license"},
            )
    except HgRpcError:
        raise
    except Exception:  # noqa: BLE001
        pass
    try:
        import agents.hongguo_license_ensure as _lic  # noqa: PLC0415

        _lic.ensure_or_raise()
    except HgRpcError:
        raise
    except Exception as e:  # noqa: BLE001
        # Không nhét activate log dài vào message (UI alert sẽ nổ)
        msg = str(e)
        if len(msg) > 180:
            msg = msg[:180] + "…"
        raise HgRpcError(
            f"Hongguo license ensure lỗi: {msg}",
            code=-32010,
            data={"reason": "need_license"},
        ) from e


def submit(
    series_ids: list[str],
    scores: dict | None = None,
    quality: str | None = None,
) -> dict:
    ids = [str(x).strip() for x in series_ids if str(x).strip()]
    if not ids:
        raise HgRpcError("series_ids trống")
    _ensure_licensed_quiet()
    q = (quality or "").strip() or None
    if not q:
        try:
            q = str((settings_get() or {}).get("quality") or "").strip() or None
        except Exception:  # noqa: BLE001
            q = None
    requests = []
    for sid in ids:
        req: dict[str, Any] = {"seriesId": sid}
        if scores and sid in scores:
            req["score"] = scores[sid]
        if q:
            req["quality"] = q
        requests.append(req)
    try:
        return call("downloads.add", {"requests": requests}) or {}
    except HgRpcError as e:
        # license chưa inject vào helper spawn riêng
        if e.code == -32010 or "licence" in str(e).lower() or "license" in str(e).lower():
            raise HgRpcError(
                "Hongguo 2.x RPC cần license proof để tải "
                f"(license.status có thể đang 'none'). Chi tiết: {e}",
                code=e.code,
                data={"reason": "need_license", **(e.data or {} if isinstance(e.data, dict) else {})},
            ) from e
        raise


def library_download_new_seasons() -> dict:
    _ensure_licensed_quiet()
    try:
        return call("library.downloadNewSeasons", {}) or {}
    except HgRpcError as e:
        if e.code == -32010 or "key" in str(e).lower() or "proof" in str(e).lower():
            raise HgRpcError(
                "downloadNewSeasons cần Hongguo key/license proof trên helper RPC. "
                f"{e}",
                code=e.code,
                data={"reason": "need_license"},
            ) from e
        raise


def resolve_link(text: str) -> dict:
    return call("links.resolve", {"text": text}) or {}


_PLAY_ROOTS = (
    Path(r"D:\Temp\hongguo-player"),
    Path(r"D:\PinelineV2_tmp\hongguo-player"),
    Path(r"D:\PinelineV4_tmp\hongguo-player"),
    Path(r"D:\PinelineV5_tmp\hongguo-player"),
    Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / "hongguo-player",
    Path(os.environ.get("LOCALAPPDATA") or ".") / "Temp" / "hongguo-player",
)
_play_sessions: dict[int, dict[str, Any]] = {}
# sid|ep|quality -> path (tái dùng file đã prepare — Play lần 2 gần như tức thì)
_play_cache: dict[str, str] = {}
_play_jobs: dict[str, dict[str, Any]] = {}
_PLAY_CACHE_FILE = Path(
    os.environ.get("TEMP") or os.environ.get("TMP") or r"D:\PinelineV2_tmp"
) / "hongguo_play_cache.json"
# Preview mặc định nhẹ — 1080p làm chờ hàng phút
_PREVIEW_QUALITY_DEFAULT = "480p"
_PREVIEW_QUALITY_ALLOW = frozenset({"360p", "480p", "540p", "720p"})


def _play_cache_key(series_id: str, episode: int, quality: str) -> str:
    return f"{series_id}|{int(episode)}|{(quality or '').strip().lower()}"


def _play_cache_load() -> None:
    global _play_cache
    if _play_cache:
        return
    try:
        if _PLAY_CACHE_FILE.is_file():
            raw = json.loads(_PLAY_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _play_cache = {str(k): str(v) for k, v in raw.items() if v}
    except Exception:  # noqa: BLE001
        _play_cache = {}


def _play_cache_save() -> None:
    try:
        _PLAY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PLAY_CACHE_FILE.write_text(
            json.dumps(_play_cache, ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


def _preview_quality(quality: str | None) -> str:
    q = (quality or "").strip().lower()
    if q in _PREVIEW_QUALITY_ALLOW:
        return q
    # 1080p / trống → 480p (nhanh)
    return _PREVIEW_QUALITY_DEFAULT


def _play_path_allowed(p: Path) -> bool:
    try:
        rp = p.resolve()
    except OSError:
        return False
    for root in _PLAY_ROOTS:
        try:
            rr = root.resolve()
        except OSError:
            continue
        if rp == rr or rr in rp.parents:
            return True
    return False


def playback_stop(session: int | None = None) -> dict:
    """Dừng session playback (best-effort)."""
    params: dict[str, Any] = {}
    if session is not None:
        params["session"] = int(session)
    else:
        # lấy session mới nhất đã biết
        cur = note("playback.state") or {}
        if cur.get("session") is not None:
            params["session"] = int(cur["session"])
    if not params:
        return {"ok": True, "skipped": True}
    try:
        call("playback.stop", params, timeout=15)
    except HgRpcError:
        try:
            call("playback.ended", params, timeout=15)
        except HgRpcError:
            pass
    return {"ok": True, **params}


def playback_prepare(
    series_id: str,
    *,
    episode: int = 1,
    quality: str | None = None,
    timeout: float = 120.0,
    force: bool = False,
) -> dict:
    """Chuẩn bị tập xem thử qua helper (tải về hongguo-player/*.mp4).

    Cần license prove trên cùng helper. Trả ``path`` local khi ``state=ready``.
    Dùng cache đĩa nếu đã prepare cùng sid/ep/quality.
    """
    sid = str(series_id or "").strip()
    if not sid:
        raise HgRpcError("thiếu seriesId")
    ep = max(1, int(episode or 1))
    q = _preview_quality(quality)
    _play_cache_load()
    ck = _play_cache_key(sid, ep, q)
    if not force:
        cached = _play_cache.get(ck) or ""
        if cached:
            p = Path(cached)
            if p.is_file() and _play_path_allowed(p) and p.stat().st_size > 100_000:
                # session ảo âm để stream qua cache (không đụng helper session)
                sess = -abs(hash(ck)) % 1_000_000_000 or -1
                info = {
                    "ok": True,
                    "session": int(sess),
                    "path": str(p),
                    "quality": q,
                    "state": "ready",
                    "series_id": sid,
                    "episode": ep,
                    "backend": "rpc",
                    "cached": True,
                }
                _play_sessions[int(info["session"])] = info
                return info

    _ensure_licensed_quiet()
    # dừng session cũ nếu còn (tránh kẹt preparing)
    try:
        playback_stop()
    except Exception:  # noqa: BLE001
        pass
    params: dict[str, Any] = {"seriesId": sid, "episode": ep, "quality": q}
    with _lock:
        _notes.pop("playback.state", None)
    prep = call("playback.prepare", params, timeout=45) or {}
    session = int(prep.get("session") or 0)
    deadline = time.time() + max(20.0, float(timeout or 120))
    last: dict[str, Any] = {}
    while time.time() < deadline:
        st = dict(note("playback.state") or {})
        if st:
            last = st
            state = str(st.get("state") or "").lower()
            path = str(st.get("path") or "").strip()
            prog = st.get("progress")
            if state == "ready" and path:
                p = Path(path)
                if p.is_file() and _play_path_allowed(p):
                    info = {
                        "ok": True,
                        "session": int(st.get("session") or session or 0),
                        "path": str(p),
                        "quality": st.get("quality") or q,
                        "state": state,
                        "series_id": sid,
                        "episode": ep,
                        "backend": "rpc",
                        "cached": False,
                        "progress": 1.0,
                    }
                    _play_sessions[int(info["session"])] = info
                    _play_cache[ck] = str(p)
                    _play_cache_save()
                    return info
            if state == "failed":
                raise HgRpcError(
                    str(st.get("message") or "playback failed"),
                    data={"playback": st},
                )
            if prog is not None:
                last = {**st, "progress": prog}
        time.sleep(0.25)
    raise HgRpcError(
        "Tải preview quá lâu — bấm Retry (đã cache sẽ nhanh hơn)",
        data={"playback": last, "session": session},
    )


def playback_prepare_start(
    series_id: str,
    *,
    episode: int = 1,
    quality: str | None = None,
    force: bool = False,
) -> dict:
    """Chạy prepare nền — UI poll ``playback_job`` để hiện %.

    Cache hit → trả ready ngay (không spawn thread).
    """
    import uuid

    sid = str(series_id or "").strip()
    ep = max(1, int(episode or 1))
    q = _preview_quality(quality)
    _play_cache_load()
    if not force:
        ck = _play_cache_key(sid, ep, q)
        cached = _play_cache.get(ck) or ""
        if cached:
            p = Path(cached)
            if p.is_file() and _play_path_allowed(p) and p.stat().st_size > 100_000:
                sess = -abs(hash(ck)) % 1_000_000_000 or -1
                info = {
                    "ok": True,
                    "session": int(sess),
                    "path": str(p),
                    "quality": q,
                    "cached": True,
                    "series_id": sid,
                    "episode": ep,
                    "backend": "rpc",
                }
                _play_sessions[int(sess)] = info
                ticket = f"cache-{sess}"
                _play_jobs[ticket] = {
                    "status": "ready",
                    "progress": 1.0,
                    "ok": True,
                    "session": sess,
                    "url": f"/api/hg/playback/stream?session={sess}",
                    "quality": q,
                    "cached": True,
                    "local": True,
                    "backend": "rpc",
                    "free": 3,
                    "series_id": sid,
                    "episode": ep,
                }
                return {"ok": True, "ticket": ticket, **_play_jobs[ticket]}

    ticket = uuid.uuid4().hex[:12]
    _play_jobs[ticket] = {
        "status": "preparing",
        "progress": 0.0,
        "ok": False,
        "series_id": sid,
        "episode": ep,
        "quality": q,
    }

    def _run() -> None:
        try:
            # theo dõi progress từ notes trong lúc prepare
            def _watch() -> None:
                while True:
                    job = _play_jobs.get(ticket) or {}
                    if job.get("status") in ("ready", "failed"):
                        return
                    st = dict(note("playback.state") or {})
                    if st.get("progress") is not None:
                        try:
                            _play_jobs[ticket]["progress"] = float(st.get("progress") or 0)
                        except (TypeError, ValueError):
                            pass
                    time.sleep(0.3)

            threading.Thread(target=_watch, name=f"hg-pv-watch-{ticket}", daemon=True).start()
            info = playback_prepare(sid, episode=ep, quality=q, timeout=120, force=force)
            _play_jobs[ticket] = {
                "status": "ready",
                "progress": 1.0,
                "ok": True,
                "session": info["session"],
                "url": f"/api/hg/playback/stream?session={info['session']}",
                "quality": info.get("quality"),
                "cached": bool(info.get("cached")),
                "series_id": sid,
                "episode": ep,
                "local": True,
                "backend": "rpc",
                "free": 3,
            }
        except Exception as e:  # noqa: BLE001
            _play_jobs[ticket] = {
                "status": "failed",
                "progress": 0.0,
                "ok": False,
                "error": str(e),
                "series_id": sid,
                "episode": ep,
                "quality": q,
            }

    threading.Thread(target=_run, name=f"hg-pv-{ticket}", daemon=True).start()
    return {"ok": True, "ticket": ticket, "status": "preparing", "progress": 0.0, "quality": q}


def playback_job(ticket: str) -> dict:
    t = str(ticket or "").strip()
    job = _play_jobs.get(t)
    if not job:
        return {"ok": False, "status": "missing", "error": "unknown ticket"}
    out = dict(job)
    # cập nhật progress từ note nếu còn preparing
    if out.get("status") == "preparing":
        st = dict(note("playback.state") or {})
        if st.get("progress") is not None:
            try:
                out["progress"] = float(st.get("progress") or 0)
                _play_jobs[t]["progress"] = out["progress"]
            except (TypeError, ValueError):
                pass
    return out


def playback_session_path(session: int) -> Path | None:
    info = _play_sessions.get(int(session or 0))
    if not info:
        # fallback: note hiện tại
        st = dict(note("playback.state") or {})
        if int(st.get("session") or 0) == int(session) and st.get("path"):
            info = {"path": st["path"]}
    if not info:
        return None
    p = Path(str(info.get("path") or ""))
    if p.is_file() and _play_path_allowed(p):
        return p
    return None



def series_detail(series_id: str) -> dict:
    sid = str(series_id or "").strip()
    if not sid:
        raise HgRpcError("thiếu series id")
    raw = call("series.detail", {"id": sid}) or {}
    eps = raw.get("episodes")
    if isinstance(eps, list):
        episode_cnt: int | list | None = eps  # bridge normalize → int + giữ list
    elif isinstance(eps, (int, float)):
        episode_cnt = int(eps)
    else:
        episode_cnt = eps
    return {
        "series_id": str(raw.get("id") or sid),
        "title": raw.get("title"),
        "cover": raw.get("cover"),
        "intro": raw.get("intro"),
        "complete": raw.get("complete"),
        "tags": raw.get("tags"),
        "cast": raw.get("cast"),
        "episode_cnt": episode_cnt,
        "folderName": raw.get("folderName"),
        "backend": "rpc",
        "raw": raw,
    }


def series_seasons(series_id: str) -> dict:
    sid = str(series_id or "").strip()
    if not sid:
        raise HgRpcError("thiếu series id")
    raw = call("series.seasons", {"id": sid}) or {}
    seasons = []
    for s in (raw.get("seasons") or []):
        seasons.append({
            "series_id": str(s.get("id") or ""),
            "title": s.get("title") or "",
            "season": s.get("season"),
            "episodes": s.get("episodes") or 0,
            "cover": s.get("cover") or "",
            "upcoming": bool(s.get("upcoming")),
        })
    return {"series_id": sid, "seasons": seasons, "backend": "rpc"}


_DEFAULT_QUALITIES = [
    {"quality": q, "plays": True, "uses": q, "bytesPerSecond": 0}
    for q in ("1080p", "720p", "540p", "480p", "360p")
]
_SERIES_QUALITIES_FALLBACK = True


def series_qualities(series_id: str) -> dict:
    """Quality ladder mặc định.

    Không gọi RPC `series.qualities` — helper 0.3.x hay crash/timeout
    («catalog service didn't answer») → 502 + chết process. Play/Download
    chỉ cần ladder chuẩn; prepare chọn quality theo request.
    """
    sid = str(series_id or "").strip()
    if not sid:
        raise HgRpcError("thiếu series id")
    return {
        "series_id": sid,
        "qualities": list(_DEFAULT_QUALITIES),
        "backend": "rpc",
        "fallback": True,
    }



def settings_get() -> dict:
    return call("settings.get", {}) or {}


def settings_update(patch: dict | None = None) -> dict:
    """WinUI settings.update — params = {patch: {...}}."""
    patch = dict(patch or {})
    if not patch:
        return settings_get()
    return call("settings.update", {"patch": patch}) or settings_get()


def _idle_watch() -> None:
    global _last_used
    while True:
        time.sleep(30)
        if _IDLE_SEC <= 0:
            continue
        with _lock:
            if _proc is None or _proc.poll() is not None:
                continue
            if time.time() - _last_used <= _IDLE_SEC:
                continue
            if _has_active_downloads_unlocked():
                _last_used = time.time()
                continue
            _kill_unlocked()


def cleanup_machine_orphans() -> dict:
    """CLI/ops: kill mọi MainKt Pineline (không đụng WinUI)."""
    with _lock:
        keep = _proc.pid if (_proc is not None and _proc.poll() is None) else None
    n = reap_orphan_helpers(keep_pid=keep, only_children=False)
    return {"killed": n, "kept": keep}


threading.Thread(target=_idle_watch, name="hg-rpc-idle", daemon=True).start()
atexit.register(stop)
