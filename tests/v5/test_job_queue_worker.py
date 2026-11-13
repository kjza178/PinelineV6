import json
import threading
import time
from pathlib import Path

import core.job_queue as jq


def _mf(wd: Path) -> None:
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "manifest.json").write_text(
        json.dumps({"stages": {"download": {"status": "done"}}}), encoding="utf-8"
    )


def test_worker_runs_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    calls = []

    def fake(job_id, opts):
        calls.append(job_id)
        time.sleep(0.05)
        return "done", "ok"

    jq.set_runner(fake)
    _mf(tmp_path / "j1")
    _mf(tmp_path / "j2")
    jq.queue_add(["j1", "j2"], opts={"voice": "v5"})
    for _ in range(100):
        if not jq.queue_state()["worker"] and not jq.queue_state()["queue"]:
            break
        time.sleep(0.05)
    assert calls == ["j1", "j2"]


def test_worker_uses_per_job_opts(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    seen = []

    def fake(job_id, opts):
        seen.append((job_id, opts.get("voice")))
        return "done", "ok"

    jq.set_runner(fake)
    _mf(tmp_path / "j1")
    _mf(tmp_path / "j2")
    jq.queue_add(["j1"], opts={"voice": "v1"})
    jq.queue_add(["j2"], opts={"voice": "v2"})
    for _ in range(100):
        if not jq.queue_state()["worker"] and not jq.queue_state()["queue"]:
            break
        time.sleep(0.05)
    assert seen == [("j1", "v1"), ("j2", "v2")]


def test_stop_clears_remaining(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    stopped = []

    def fake(job_id, opts):
        time.sleep(0.3)
        return "done", "ok"

    def hook(jid):
        stopped.append(jid)

    jq.set_runner(fake)
    jq.set_stop_hook(hook)
    _mf(tmp_path / "j1")
    _mf(tmp_path / "j2")
    jq.queue_add(["j1", "j2"], opts={})
    time.sleep(0.05)
    r = jq.queue_stop()
    time.sleep(0.4)
    st = jq.queue_state()
    assert st["queue"] == []
    assert r["stopped_job"] == "j1"
    assert stopped == ["j1"]


def test_worker_clears_stale_stop_before_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    events = []

    def clear_hook(job_id):
        events.append(("clear", job_id))

    def fake(job_id, opts):
        events.append(("run", job_id))
        return "done", "ok"

    jq.set_clear_stop_hook(clear_hook)
    jq.set_runner(fake)
    _mf(tmp_path / "j1")
    try:
        jq.queue_add(["j1"], opts={})
        for _ in range(100):
            if not jq.queue_state()["worker"]:
                break
            time.sleep(0.01)
        assert events == [("clear", "j1"), ("run", "j1")]
    finally:
        jq.set_clear_stop_hook(None)


def test_worker_parks_held_job_before_next(tmp_path, monkeypatch):
    """Studio invariant: held → giữ ghế, không chạy job kế đến khi park_ready."""
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(jq, "_PARK_POLL_SEC", 0.05)
    jq._reset_for_tests()
    calls = []
    release = threading.Event()

    def fake(job_id, opts):
        calls.append(job_id)
        if job_id == "j1" and calls.count("j1") == 1:
            return "held", "waiting review"
        return "done", "ok"

    def ready(job_id):
        return release.is_set() and job_id == "j1"

    jq.set_runner(fake)
    jq.set_park_ready_hook(ready)
    _mf(tmp_path / "j1")
    _mf(tmp_path / "j2")
    jq.queue_add(["j1", "j2"], opts={"voice": "v5"}, sort_first=False)
    time.sleep(0.25)
    assert calls == ["j1"]
    assert jq.queue_state()["current"] == "j1"
    assert "j2" in jq.queue_state()["queue"]
    release.set()
    for _ in range(200):
        if not jq.queue_state()["worker"] and not jq.queue_state()["queue"]:
            break
        time.sleep(0.05)
    assert calls == ["j1", "j1", "j2"]


def test_stop_then_immediate_enqueue_runs_new_job(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    old_started = threading.Event()
    release_old = threading.Event()
    new_finished = threading.Event()
    calls = []

    def fake(job_id, opts):
        calls.append(job_id)
        if job_id == "old":
            old_started.set()
            assert release_old.wait(timeout=2)
        else:
            new_finished.set()
        return "done", "ok"

    jq.set_runner(fake)
    _mf(tmp_path / "old")
    _mf(tmp_path / "new")
    jq.queue_add(["old"], opts={"voice": "v1"})
    assert old_started.wait(timeout=2)

    jq.queue_stop()
    result = jq.queue_add(["new"], opts={"voice": "v2"})
    release_old.set()

    assert result["queued"] == ["new"]
    assert new_finished.wait(timeout=2)
    for _ in range(100):
        if not jq.queue_state()["worker"]:
            break
        time.sleep(0.01)
    assert calls == ["old", "new"]
    state = jq.queue_state()
    assert state["queue"] == []
    assert state["history"][-1]["detail"] == "ok"
