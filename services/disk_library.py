"""Phim trên ổ — quét THẲNG thư mục thư viện (LIBRARY_DIR, vd D:\\Lib), tạo job, gộp tập.

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


def scan(lib: Path | None = None) -> dict:
    lib = Path(lib) if lib else root()
    if not lib.is_dir():
        return {"ok": False, "root": str(lib), "items": [],
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
                           size=size, mtime=p.stat().st_mtime, merge_error=errs.get(p.name, "")))
    counts: dict[str, int] = {}
    for it in items:
        counts[it["state"]] = counts.get(it["state"], 0) + 1
    return {"ok": True, "root": str(lib), "items": items, "counts": counts}


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
    lib = root()
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
        folder = hg.safe_child(root(), name)
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
