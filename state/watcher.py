"""state/watcher.py — Bắt các lần ghi manifest từ TIẾN TRÌNH CON (asr/tts/render worker).

Hook trong core.manifest chỉ chạy ở tiến trình nào ghi. Engine chạy subprocess ghi tiến độ
thẳng vào manifest.json → server không biết. Watcher stat (không đọc) manifest của job đang
chạy mỗi `interval` giây; mtime đổi mới đọc + index.apply → event UI.

Rẻ: chỉ stat job running/được watch (vài job), quét đủ thư mục mỗi `full_every` giây để thấy job mới.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path

from state.index import JobIndex, index as _default_index

# F6: watcher đang chạy trong tiến trình (thường 1 — app startup). Cho phép nơi spawn tiến trình con
# (render_worker.run_job, server._run_engine) báo "theo dõi job này NGAY" thay vì chờ quét đủ (≤ full_every s):
# trước đây job chưa "running" trong index (vd chạy riêng bước mux) → tiến độ render từ worker chỉ lên UI
# sau tới 30 s.
_ACTIVE: list["ManifestWatcher"] = []
_ACTIVE_LOCK = threading.Lock()


class ManifestWatcher:
    def __init__(self, work_root: Path, *, index: JobIndex | None = None,
                 interval: float = 1.0, full_every: float = 30.0) -> None:
        self.work_root = Path(work_root)
        self.index = index or _default_index
        self.interval = interval
        self.full_every = full_every
        self._mtimes: dict[str, float] = {}
        self._extra: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def watch(self, job_id: str) -> None:
        with self._lock:
            self._extra.add(job_id)

    def unwatch(self, job_id: str) -> None:
        with self._lock:
            self._extra.discard(job_id)

    def _targets(self) -> set[str]:
        ids = {r["job_id"] for r in self.index.list() if r["status"] == "running"}
        with self._lock:
            return ids | self._extra

    def _check(self, job_id: str) -> bool:
        mf = self.work_root / job_id / "manifest.json"
        try:
            mt = mf.stat().st_mtime
        except OSError:
            return False
        if self._mtimes.get(job_id) == mt:
            return False
        self._mtimes[job_id] = mt
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._mtimes.pop(job_id, None)   # đang ghi dở → lần sau đọc lại
            return False
        self.index.apply(job_id, data)
        return True

    def scan_once(self, *, full: bool = False) -> int:
        ids = self._targets()
        if full and self.work_root.is_dir():
            ids |= {d.name for d in self.work_root.iterdir() if (d / "manifest.json").is_file()}
            known = {r["job_id"] for r in self.index.list()}
            for gone in known - ids:
                if not (self.work_root / gone / "manifest.json").exists():
                    self.index.remove(gone)
        return sum(self._check(j) for j in ids)

    def start(self) -> None:
        if self._thread:
            return
        # Mốc mtime ban đầu: index đã load_all, không phát lại event.
        if self.work_root.is_dir():
            for d in self.work_root.iterdir():
                try:
                    self._mtimes[d.name] = (d / "manifest.json").stat().st_mtime
                except OSError:
                    pass

        def loop():
            waited = 0.0
            while not self._stop.wait(self.interval):
                waited += self.interval
                full = waited >= self.full_every
                if full:
                    waited = 0.0
                try:
                    self.scan_once(full=full)
                except Exception as e:  # noqa: BLE001
                    print(f"[watcher] lỗi: {e}")
        self._thread = threading.Thread(target=loop, name="manifest-watch", daemon=True)
        self._thread.start()
        with _ACTIVE_LOCK:
            if self not in _ACTIVE:
                _ACTIVE.append(self)

    def stop(self) -> None:
        self._stop.set()
        with _ACTIVE_LOCK:
            if self in _ACTIVE:
                _ACTIVE.remove(self)


def active_watchers() -> list[ManifestWatcher]:
    with _ACTIVE_LOCK:
        return list(_ACTIVE)


def watch_job(job_id: str) -> int:
    """Theo dõi job ở mọi watcher đang chạy (không có watcher → không làm gì). Trả số watcher."""
    ws = active_watchers()
    for w in ws:
        w.watch(job_id)
    return len(ws)


def unwatch_job(job_id: str, *, final_check: bool = True) -> None:
    """Bỏ theo dõi; trước đó đọc lần cuối (ghi cuối của tiến trình con — done/failed — lên index ngay)."""
    for w in active_watchers():
        if final_check:
            try:
                w._check(job_id)
            except Exception:  # noqa: BLE001
                pass
        w.unwatch(job_id)


@contextmanager
def watching(job_id: str):
    """`with watching(jid): spawn tiến trình con…` — tiến độ con ghi manifest lên UI trong ~interval s."""
    watch_job(job_id)
    try:
        yield
    finally:
        unwatch_job(job_id)
