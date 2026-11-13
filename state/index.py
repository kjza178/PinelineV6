"""state/index.py — Danh sách job trong bộ nhớ (thay việc đọc lại mọi manifest mỗi 2 s).

- `load_all(work_root)` 1 lần lúc start; sau đó chỉ `apply(job_id, data)` mỗi khi state.job_state ghi.
- `GET /api/jobs` trả `index.list()` — không đụng đĩa.
- Mỗi thay đổi phát `job.updated` lên bus; ghi mà tóm tắt không đổi → `job.touched` (gộp theo job).
- Cách ly instance (fix 2026-11-12 agentJ): `load_all(work_root)` GẮN index vào work root đó và nạp LẠI từ đầu
  (không cộng dồn); hook ghi manifest đi qua `apply_dir(work_dir, …)` → job ngoài work root (thư mục tạm của
  test/script, cây V2…) KHÔNG vào danh sách/SSE. Chưa gắn (chưa load_all) = nhận mọi thư mục như cũ;
  `unbind()` lúc app shutdown.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from events.bus import bus as _default_bus

STAGES = ("download", "asr", "translate", "tts", "mux")
_RANK = {"running": 0, "held": 1, "pending": 2, "failed": 3, "done": 4}


def summarize(job_id: str, data: dict) -> dict[str, Any]:
    """Tóm tắt nhẹ cho danh sách (không stat file). Chi tiết lấy qua GET /api/jobs/{id}."""
    stages = data.get("stages") or {}
    agent = data.get("agent") or {}
    st = {n: (stages.get(n) or {}).get("status") or "pending" for n in STAGES}
    if any(v == "running" for v in st.values()):
        status = "running"
    elif agent.get("held_at") or agent.get("hold_after_translate") or agent.get("qa_blocked"):
        status = "held"
    elif any(v == "failed" for v in st.values()):
        status = "failed"
    elif all(v == "done" for v in st.values()):
        status = "done"
    else:
        status = "pending"
    running = next((n for n, v in st.items() if v == "running"), None)
    cur = (stages.get(running) or {}).get("cur_text") if running else None
    req = data.get("request") or {}
    return {
        "job_id": job_id,
        "title": data.get("title_vi") or data.get("title") or req.get("title") or job_id,
        "status": status,
        "stages": st,
        "running_stage": running,
        "cur_text": (cur or "")[:160],
        "held_at": agent.get("held_at"),
        "hold_reason": (agent.get("hold_reason") or "")[:160],
        "group_id": data.get("group_id"),
        "updated_at": data.get("updated_at"),
    }


class JobIndex:
    def __init__(self, publish: Callable[[str, dict], Any] | None = None) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._publish = publish or _default_bus.publish
        self._root: str | None = None          # work root đã gắn (đã chuẩn hoá) — None = nhận mọi thư mục
        self._owns_cache: dict[str, bool] = {}

    # ── cách ly instance ──
    @staticmethod
    def _norm(p: Path | str) -> str:
        return os.path.normcase(os.path.abspath(str(p)))

    @property
    def root(self) -> Path | None:
        return Path(self._root) if self._root else None

    def bind(self, work_root: Path | str) -> None:
        """Gắn index vào work root của instance và XOÁ danh sách cũ (job thư mục khác không lẫn vào)."""
        with self._lock:
            self._root = self._norm(work_root)
            self._owns_cache = {}
            self._jobs = {}

    def unbind(self) -> None:
        """Gỡ gắn (app shutdown): trở lại nhận mọi thư mục; danh sách cũ bỏ."""
        with self._lock:
            self._root = None
            self._owns_cache = {}
            self._jobs = {}

    def owns(self, work_dir: Path | str) -> bool:
        """Job dir `work_dir` thuộc work root đã gắn? Chưa gắn → True. So nhanh theo abspath; khác thì so
        realpath (junction/symlink kiểu ROOT/work → work root thật) — kết quả cache theo thư mục cha."""
        root = self._root
        if root is None:
            return True
        parent = self._norm(Path(work_dir).parent)
        if parent == root:
            return True
        hit = self._owns_cache.get(parent)
        if hit is None:
            try:
                hit = self._norm(os.path.realpath(parent)) == self._norm(os.path.realpath(root))
            except OSError:
                hit = False
            if len(self._owns_cache) > 256:
                self._owns_cache.clear()
            self._owns_cache[parent] = hit
        return hit

    def apply_dir(self, work_dir: Path | str, data: dict) -> dict | None:
        """Như apply nhưng theo đường dẫn job: ngoài work root đã gắn → bỏ qua (None)."""
        if not self.owns(work_dir):
            return None
        return self.apply(Path(work_dir).name, data)

    def load_all(self, work_root: Path) -> int:
        """Gắn `work_root` + nạp lại TOÀN BỘ từ đĩa (thay danh sách cũ, không cộng dồn)."""
        self.bind(work_root)
        n = 0
        if not Path(work_root).is_dir():
            return 0
        for d in Path(work_root).iterdir():
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            with self._lock:
                self._jobs[d.name] = summarize(d.name, data)
            n += 1
        return n

    def apply(self, job_id: str, data: dict) -> dict:
        row = summarize(job_id, data)
        with self._lock:
            changed = self._jobs.get(job_id) != row
            self._jobs[job_id] = row
        if changed:
            self._publish("job.updated", row)
        else:
            # V6 F3: manifest có ghi nhưng tóm tắt không đổi (tiến độ %, n_done…) → báo nhẹ để UI cũ
            # (index3) tự refresh thay vì poll 2 s. Bus gộp theo job (THROTTLED) → ≤ 4 event/s/job.
            self._publish("job.touched", {"job_id": job_id, "status": row["status"]})
        return row

    def remove(self, job_id: str) -> None:
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
        if existed:
            self._publish("job.removed", {"job_id": job_id})

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._jobs.get(job_id)
            return dict(row) if row else None

    def list(self) -> list[dict]:
        with self._lock:
            rows = [dict(r) for r in self._jobs.values()]
        rows.sort(key=lambda r: (_RANK.get(r["status"], 9), r["job_id"]))
        return rows


index = JobIndex()
