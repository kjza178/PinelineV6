"""runtime/context.py — State toàn cục của 1 tiến trình server, gom về một object.

Thay các biến module trong server.py cũ (`_STOP_JOBS`, `_STAGE_BUSY`, `_PROCS`, pool `_dl`/`_gpu`).
Route nhận Runtime qua dependency → không cần `import server` (bỏ vòng import routes_* ↔ server).
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from core.instance import Instance


@dataclass
class Runtime:
    instance: Instance
    dl_workers: int = 2
    gpu_workers: int = 1
    stop_jobs: set[str] = field(default_factory=set)
    stage_busy: dict[str, set[str]] = field(default_factory=dict)
    procs: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    dl: ThreadPoolExecutor | None = field(default=None, repr=False)
    gpu: ThreadPoolExecutor | None = field(default=None, repr=False)

    def start_pools(self) -> None:
        if self.dl is None:
            self.dl = ThreadPoolExecutor(self.dl_workers, thread_name_prefix="pl-dl")
        if self.gpu is None:
            self.gpu = ThreadPoolExecutor(self.gpu_workers, thread_name_prefix="pl-gpu")

    def shutdown(self) -> None:
        for p in (self.dl, self.gpu):
            if p is not None:
                p.shutdown(wait=False, cancel_futures=True)
        self.dl = self.gpu = None

    # ---- dừng ----
    def request_stop(self, job_id: str) -> None:
        with self._lock:
            self.stop_jobs.add(job_id)
        (self.instance.work_root / job_id).mkdir(parents=True, exist_ok=True)
        (self.instance.work_root / job_id / ".stop").write_text("1", encoding="utf-8")

    def clear_stop(self, job_id: str) -> None:
        with self._lock:
            self.stop_jobs.discard(job_id)
        try:
            (self.instance.work_root / job_id / ".stop").unlink()
        except FileNotFoundError:
            pass

    def is_stopped(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self.stop_jobs

    # ---- claim stage (1 job không chạy 2 stage nặng cùng lúc) ----
    def claim(self, job_id: str, stage: str) -> bool:
        with self._lock:
            busy = self.stage_busy.setdefault(job_id, set())
            if busy and (stage == "__all__" or "__all__" in busy or stage in busy):
                return False
            busy.add(stage)
            return True

    def release(self, job_id: str, stage: str) -> None:
        with self._lock:
            busy = self.stage_busy.get(job_id)
            if busy:
                busy.discard(stage)
                if not busy:
                    self.stage_busy.pop(job_id, None)

    def busy_stage(self, job_id: str) -> str | None:
        with self._lock:
            b = self.stage_busy.get(job_id)
            return next(iter(b)) if b else None
