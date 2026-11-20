"""Port V5 fix 2026-11-20 (queue-stop-gpu-wait): Dừng job mà future còn xếp sau job khác trên _gpu (JOB_WORKERS=1)
→ huỷ future, nhả ghế hàng chờ. Trước: `done_event.wait()` vô hạn → `current` treo tới khi job kia nhả GPU."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import Future

import pytest

import server


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(server.job_queue, "WORK_ROOT", tmp_path)
    server._STOP_JOBS.clear()
    server._STAGE_BUSY.clear()
    yield
    server._STOP_JOBS.clear()
    server._STAGE_BUSY.clear()


def test_stop_cancels_pending_gpu_future(tmp_path, monkeypatch):
    wd = tmp_path / "job-1"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"request": {"url": "https://x.invalid/v", "target": "vi"},
                                                  "stages": {"tts": {"status": "pending", "voice": "v"}}}), encoding="utf-8")
    video = wd / "source.mp4"
    video.write_bytes(b"v")
    released = []
    monkeypatch.setattr(server.job_queue, "is_wired_done", lambda _wd: False)
    monkeypatch.setattr(server, "list_clone_voices", lambda: ["v"])
    monkeypatch.setattr(server, "_job_any_busy", lambda _j: None)
    monkeypatch.setattr(server, "_manifest_running", lambda _wd: None)
    monkeypatch.setattr(server, "_claim_stage", lambda *_a: True)
    monkeypatch.setattr(server, "_next_incomplete_stage", lambda _wd: "asr")
    monkeypatch.setattr(server, "_find_video", lambda _wd: video)
    monkeypatch.setattr(server, "_release_stage", lambda j, s: released.append((j, s)))
    monkeypatch.setattr(server, "_heal_stale_running", lambda _wd: [])
    pending = Future()                                   # job khác đang giữ GPU → future của ta chưa chạy

    class Busy:
        def submit(self, fn):
            return pending
    monkeypatch.setattr(server, "_gpu", Busy())
    threading.Timer(0.3, lambda: server._STOP_JOBS.add("job-1")).start()
    t0 = time.time()
    assert server._queue_run_one("job-1", {}) == ("stopped", "đã dừng")
    assert time.time() - t0 < 5 and pending.cancelled() and ("job-1", "__all__") in released
