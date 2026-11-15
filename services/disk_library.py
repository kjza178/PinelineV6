"""Phim chờ làm — thư viện phim đã tải (như tab Library của Hongguo 2), tạo job, gộp tập.

Nguồn: thư viện Hongguo 2 (RPC library.get: tên, series_id, poster, thư mục lưu) ghép với quét ổ thư mục đó. Máy chưa
có / chưa mở Hongguo 2 → chỉ quét ổ (LIBRARY_DIR). Ổ đĩa luôn quyết trạng thái (file gộp / tập lẻ / đã làm job).

2026-11-15: thay tab «Thư viện chưa làm» của trang Phim cũ. Tab cũ (agents/agent_feeder.library_view) lấy danh sách từ
Hongguo trước (/api/hg/library) rồi mới soi ổ → Hongguo không trả lời thì cả tab hỏng, và bỏ sót phim tải bằng công cụ khác.
Ở đây ổ đĩa là nguồn duy nhất.

Cấu trúc thư viện (Hongguo 2 / công cụ tải): LIBRARY_DIR/<bộ>/ chứa các tập lẻ (S01E001.mp4, 第001集.mp4, 001.mp4…)
và/hoặc file đã gộp «<bộ> (merged).mp4»; có thể có .series.json {title, series_id}. File video nằm thẳng trong
LIBRARY_DIR cũng tính là một phim.

Trạng thái mỗi phim:
  done        đã tạo job từ file này (job còn trong work root hoặc còn trong films.db)
  ready       có file để làm job (file đã gộp, hoặc bộ chỉ có 1 tập, hoặc file lẻ)
  need_merge  ≥2 tập lẻ, chưa gộp → «Gộp tập» trước
  merging     đang gộp
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".ts")

_merge_lock = threading.Lock()
_merge_q: list[str] = []
_merge_cur: str | None = None
_merge_err: dict[str, str] = {}


def _norm(p: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(p or "")))


def root() -> Path:
    from core import machine
    return machine.library_dir()


def _hg_library() -> tuple[dict, Path | None, str]:
    """({tên thư mục: mục Hongguo 2}, thư mục lưu của Hongguo 2, lỗi). Không có Hongguo 2 → ({}, None, lý do)."""
    try:
        import agents.hongguo_bridge as hg
        hg._use_rpc()
        data = hg._rpc_mod().library() or {}
    except Exception as e:  # noqa: BLE001
        return {}, None, str(e)
    by = {str(it.get("name") or ""): it for it in data.get("items") or [] if it.get("name")}
    folder = data.get("folder")
    if not folder:
        for it in by.values():
            if it.get("folder"):
                folder = str(Path(it["folder"]).parent)
                break
    return by, (Path(folder) if folder else None), ""


def _cached_vi(sid: str, zh: str) -> str:
    """Tên tiếng Việt đã dịch (cache engines/title_mt) — CHỈ đọc, không gọi dịch (GET không side effect)."""
    try:
        import engines.title_mt as tmt
        c = tmt._load_cache()
    except Exception as e:  # noqa: BLE001
        print(f"[disk_library] đọc cache tên dịch lỗi: {e}", flush=True)
        return ""
    hit = (c.get("by_id") or {}).get(sid) if sid else None
    hit = hit or (c.get("by_zh") or {}).get(zh)
    return str(hit.get("vi") or "") if hit and hit.get("zh") == zh else ""


def translate(names: list[str]) -> dict:
    """Dịch tên các phim chưa có tên Việt (engines/title_mt, có cache) — POST, gọi sau khi danh sách đã hiện."""
    want = {str(n) for n in names or []}
    items = [{"series_id": it.get("series_id") or "", "title": it["title"]}
             for it in scan()["items"] if it["name"] in want and not it.get("title_vi")]
    if not items:
        return {"ok": True, "translated": 0}
    import engines.title_mt as tmt
    r = tmt.translate_titles(items)
    return {"ok": True, "translated": r.get("translated", 0), "failed": r.get("failed", 0)}


POSTER_NAMES = ("poster.jpg", "poster.jpeg", "poster.png", "poster.webp", "cover.jpg", "cover.png", "thumb.jpg")


def poster(name: str) -> Path | None:
    import agents.hongguo_bridge as hg
    _, hg_root, _ = _hg_library_cached()
    for base in (hg_root, root()):
        folder = hg.safe_child(base, name) if base else None
        if folder and folder.is_dir():
            for n in POSTER_NAMES:
                p = folder / n
                if p.is_file() and p.stat().st_size > 32:
                    return p
    return None


_HG_CACHE: dict = {"at": 0.0, "val": ({}, None, "")}


def lib_dir() -> Path:
    """Thư mục thư viện đang dùng: thư mục lưu của Hongguo 2 nếu có, không thì LIBRARY_DIR (Cài đặt → Thư mục)."""
    _, hg_root, _ = _hg_library_cached()
    return hg_root if hg_root and hg_root.is_dir() else root()


def _hg_library_cached(max_age: float = 30.0) -> tuple[dict, Path | None, str]:
    if time.time() - _HG_CACHE["at"] > max_age:
        _HG_CACHE["val"] = _hg_library()
        _HG_CACHE["at"] = time.time()
    return _HG_CACHE["val"]


def _imported() -> set[str]:
    """Nguồn đã làm job: job trong work root + films.db (cả job đã xoá thư mục)."""
    out: set[str] = set()
    from core.instance import work_root_now
    wr = work_root_now(Path("work"))
    if wr.is_dir():
        for d in wr.iterdir():
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            try:
                op = ((json.loads(mf.read_text(encoding="utf-8")).get("request") or {}).get("orig_path") or "")
            except (OSError, ValueError) as e:
                print(f"[disk_library] đọc {mf} lỗi: {e}", flush=True)
                continue
            if op:
                out.add(_norm(op))
    try:
        import core.film_db as film_db
        out |= {k for k in film_db.source_keys() if k}
    except Exception as e:  # noqa: BLE001
        print(f"[disk_library] đọc films.db lỗi (bỏ qua phần đã làm cũ): {e}", flush=True)
    return out


def _series_meta(d: Path) -> tuple[str, str]:
    sj = d / ".series.json"
    if sj.is_file():
        try:
            m = json.loads(sj.read_text(encoding="utf-8"))
            return str(m.get("title") or d.name), str(m.get("series_id") or "")
        except (OSError, ValueError) as e:
            print(f"[disk_library] {sj} hỏng: {e}", flush=True)
    return d.name, ""


def _item(name: str, title: str, path: Path | None, state: str, **kw) -> dict:
    st = None
    if path is not None:
        try:
            st = path.stat()
        except OSError:
            st = None
    return {"name": name, "title": title, "path": str(path) if path else "", "state": state,
            "size": st.st_size if st else kw.pop("size", 0), "mtime": st.st_mtime if st else kw.pop("mtime", 0), **kw}


def scan(lib: Path | None = None, fresh: bool = False) -> dict:
    hg_items, hg_root, hg_err = ({}, None, "") if lib else _hg_library_cached(0 if fresh else 30)
    lib = Path(lib) if lib else (hg_root if hg_root and hg_root.is_dir() else root())
    source = "hongguo2" if hg_items else "disk"
    if not lib.is_dir():
        return {"ok": False, "root": str(lib), "items": [], "source": source, "hg_error": hg_err,
                "error": f"Không thấy thư mục thư viện {lib} — sửa ở Cài đặt → Thư mục"}
    import agents.hongguo_bridge as hg
    done = _imported()
    with _merge_lock:
        busy = set(_merge_q) | ({_merge_cur} if _merge_cur else set())
        errs = dict(_merge_err)
    items: list[dict] = []
    for p in sorted(lib.iterdir(), key=lambda x: x.name.lower()):
        if p.name.startswith("."):
            continue
        if p.is_file():
            if p.suffix.lower() in VIDEO_EXT and not p.name.endswith((".tmp.mp4", ".part")):
                items.append(_item(p.name, p.stem, p, "done" if _norm(p) in done else "ready", episodes=1))
            continue
        if not p.is_dir():
            continue
        title, sid = _series_meta(p)
        hgi = hg_items.get(p.name) or {}
        title, sid = str(hgi.get("title") or title), str(hgi.get("series_id") or sid)
        info = hg.inspect_series_folder(p)
        frags = hg._list_episode_frags(p)
        n = len(frags)
        if info.get("merged_file"):
            f = p / info["merged_file"]
            state = "done" if _norm(f) in done else "ready"
        elif n == 1:
            f = frags[0]
            state = "done" if _norm(f) in done else "ready"
        elif n >= 2:
            f = None
            state = "merging" if p.name in busy else "need_merge"
        else:
            vids = [x for x in p.iterdir() if x.is_file() and x.suffix.lower() in VIDEO_EXT
                    and not x.name.endswith(".tmp.mp4")]
            if len(vids) != 1:      # thư mục rỗng / nhiều file không rõ thứ tự → bỏ qua, không đoán
                continue
            f = vids[0]
            state = "done" if _norm(f) in done else "ready"
        size = sum(x.stat().st_size for x in frags) if f is None else 0
        items.append(_item(p.name, title, f, state, series_id=sid, episodes=max(n, 1), folder=str(p),
                           fragments=n if info.get("merged_file") else 0,
                           size=size, mtime=p.stat().st_mtime, merge_error=errs.get(p.name, ""),
                           in_hongguo=bool(hgi), has_poster=any((p / x).is_file() for x in POSTER_NAMES)))
    counts: dict[str, int] = {}
    for it in items:
        counts[it["state"]] = counts.get(it["state"], 0) + 1
        it["title_vi"] = _cached_vi(it.get("series_id") or "", it["title"])
    items.sort(key=lambda it: -float(it.get("mtime") or 0))          # mới tải lên đầu (như Library của Hongguo 2)
    return {"ok": True, "root": str(lib), "items": items, "counts": counts, "source": source, "hg_error": hg_err}


def _find(names: list[str]) -> list[dict]:
    want = {str(n) for n in names or []}
    return [it for it in scan()["items"] if it["name"] in want]


def add_jobs(names: list[str], base: str | None = None) -> dict:
    """Tạo job cho các phim «ready» rồi xếp hàng theo cấu hình batch (như tab cũ: agent_feeder.add_jobs — gom nhóm
    theo bộ, xếp mùa theo thứ tự)."""
    import agents.agent_feeder as feeder
    import agents.hongguo_bridge as hg
    chosen, skipped = [], []
    for it in _find(names):
        if it["state"] != "ready":
            skipped.append(f"{it['title']}: {'đã làm' if it['state'] == 'done' else 'chưa gộp tập'}")
            continue
        core = hg.franchise_core(it["title"])
        key = hg.season_timeline_key(it["title"], it.get("series_id"))
        season = int(key[2]) if key and len(key) > 2 and key[2] else 0
        chosen.append({"name": it["name"], "path": it["path"], "core": core, "season": season})
    if not chosen:
        return {"ok": False, "job_ids": [], "errors": skipped or ["không có phim nào khớp"]}
    if base is None:
        from core.instance import server_base
        base = server_base()
    r = feeder.add_jobs(base, chosen)
    r["errors"] = list(r.get("errors") or []) + skipped
    return {"ok": bool(r.get("job_ids")), **r}


def merge(names: list[str]) -> dict:
    """Xếp hàng gộp tập, chạy tuần tự nền: cùng độ phân giải → ffmpeg concat giữ nguyên chất lượng
    (hongguo_bridge.merge_series_copy); lệch → chuẩn hoá 1080p (merge_series_normalized). Tiến độ qua SSE
    `disk_library.merge`."""
    global _merge_cur
    lib = lib_dir()
    import agents.hongguo_bridge as hg
    queued = []
    with _merge_lock:
        for n in names or []:
            folder = hg.safe_child(lib, str(n))
            if not folder or not folder.is_dir() or n in _merge_q or n == _merge_cur:
                continue
            _merge_q.append(str(n))
            _merge_err.pop(str(n), None)
            queued.append(str(n))
        start = bool(_merge_q) and _merge_cur is None
        if start:
            _merge_cur = "…"          # giữ chỗ để không khởi động 2 luồng
    if start:
        threading.Thread(target=_merge_worker, name="disk-merge", daemon=True).start()
    return {"ok": bool(queued), "queued": queued}


def _publish(kind: str, data: dict) -> None:
    try:
        from events.bus import bus
        bus.publish(kind, data)
    except Exception as e:  # noqa: BLE001
        print(f"[disk_library] publish {kind} lỗi: {e}", flush=True)


def _merge_worker() -> None:
    global _merge_cur
    import agents.hongguo_bridge as hg
    while True:
        with _merge_lock:
            if not _merge_q:
                _merge_cur = None
                return
            name = _merge_q.pop(0)
            _merge_cur = name
        folder = hg.safe_child(lib_dir(), name)
        t0 = time.time()
        _publish("disk_library.merge", {"name": name, "state": "start"})
        try:
            last = [0.0]

            def on_pct(p: float) -> None:
                if p - last[0] >= 5:
                    last[0] = p
                    _publish("disk_library.merge", {"name": name, "state": "progress", "pct": round(p)})

            if not folder:
                r = {"ok": False, "error": "thư mục không hợp lệ"}
            elif hg.episode_resolutions(folder).get("mixed"):     # tập lệch độ phân giải → chuẩn hoá 1080p rồi gộp
                r = hg.merge_series_normalized(folder, on_pct=on_pct, target_wh=hg.MERGE_MIXED_CANVAS)
            else:                                                 # cùng độ phân giải → nối giữ nguyên chất lượng
                r = hg.merge_series_copy(folder, on_pct=on_pct)
            # KHÔNG xoá tập lẻ sau khi gộp (khác worker Hongguo cũ dọn vào thùng rác) — người dùng tự dọn
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "error": str(e)}
        if not r.get("ok"):
            print(f"[disk_library] gộp {name} lỗi: {r.get('error')}", flush=True)
            with _merge_lock:
                _merge_err[name] = str(r.get("error") or "lỗi không rõ")
        _publish("disk_library.merge", {"name": name, "state": "done" if r.get("ok") else "error",
                                        "error": r.get("error") or "", "sec": round(time.time() - t0)})


# ── Xoá phim khỏi thư viện / dọn tập lẻ ──

def _jobs_using(folder: Path) -> list[str]:
    """Job CHƯA xong mà nguồn nằm trong `folder` (job đọc thẳng file thư viện — server._place_source «direct»).
    Xoá thư mục lúc này làm hỏng job → chặn."""
    from core.instance import work_root_now
    import core.job_queue as jq
    wr = work_root_now(Path("work"))
    key = _norm(folder)
    out = []
    if not wr.is_dir():
        return out
    for d in wr.iterdir():
        mf = d / "manifest.json"
        if not mf.is_file():
            continue
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"[disk_library] đọc {mf} lỗi: {e}", flush=True)
            continue
        op = _norm((m.get("request") or {}).get("orig_path") or "")
        if op and (op == key or op.startswith(key + os.sep)):
            try:
                done = jq.is_wired_done(d)
            except Exception as e:  # noqa: BLE001
                print(f"[disk_library] is_wired_done({d.name}) lỗi: {e} → coi như chưa xong", flush=True)
                done = False
            if not done:
                out.append(d.name)
    return out


def _refresh_hongguo() -> None:
    _HG_CACHE["at"] = 0.0
    try:
        import agents.hongguo_bridge as hg
        hg._use_rpc()
        hg._rpc_mod().call("library.refresh", {}, timeout=15)
    except Exception as e:  # noqa: BLE001
        print(f"[disk_library] làm mới thư viện Hongguo 2 lỗi (bỏ qua): {e}", flush=True)


def delete(names: list[str]) -> dict:
    """Xoá HẲN thư mục phim (như «Xoá» của trang Phim cũ — agent_feeder delete_local). Chặn: tên ra ngoài thư viện,
    bộ đang gộp, bộ còn job chưa xong đang đọc file trong đó."""
    import shutil
    import stat

    import agents.hongguo_bridge as hg
    lib = lib_dir()
    deleted, errors, freed = [], [], 0
    with _merge_lock:
        busy = set(_merge_q) | ({_merge_cur} if _merge_cur else set())
    for n in names or []:
        n = str(n)
        p = hg.safe_child(lib, n)
        if not p or not p.exists():
            errors.append(f"{n}: không có trong thư viện")
            continue
        if n in busy:
            errors.append(f"{n}: đang gộp tập — chờ gộp xong")
            continue
        using = _jobs_using(p)
        if using:
            errors.append(f"{n}: job chưa xong đang dùng file này ({', '.join(using[:3])})")
            continue
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size

        def _fix(func, fpath, _exc):
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        try:
            shutil.rmtree(p, onerror=_fix) if p.is_dir() else p.unlink()
        except OSError as e:
            print(f"[disk_library] xoá {p} lỗi: {e}", flush=True)
        if p.exists():
            errors.append(f"{n}: không xoá được (file đang bị chương trình khác mở?)")
            continue
        deleted.append(n)
        freed += size
    if deleted:
        print(f"[disk_library] đã xoá {len(deleted)} phim, giải phóng {freed / 1e9:.1f} GB: {deleted}", flush=True)
        _refresh_hongguo()
    return {"ok": bool(deleted), "deleted": deleted, "errors": errors, "freed_bytes": freed}


def cleanup(names: list[str]) -> dict:
    """Dọn tập lẻ của bộ ĐÃ gộp (chỉ khi file gộp đủ nội dung) → thùng rác của thư viện (hongguo_bridge.cleanup_fragments)."""
    import agents.hongguo_bridge as hg
    lib = lib_dir()
    done, errors = {}, []
    for n in names or []:
        p = hg.safe_child(lib, str(n))
        if not p or not p.is_dir():
            errors.append(f"{n}: không có trong thư viện")
            continue
        r = hg.cleanup_fragments(p)
        if r.get("ok"):
            done[str(n)] = int(r.get("removed") or 0)
        else:
            errors.append(f"{n}: {r.get('error')}")
    return {"ok": bool(done), "cleaned": done, "errors": errors}
