"""Hàng chờ bền cho chạy 24/7 (ROADMAP R7.1, R7.2 — docs/fixes/2026-11-14-queue-worker-robust.md)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import core.job_queue as jq


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(jq, "_PARK_POLL_SEC", 0.5)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_save_failed", 0)
    for j in ("A", "B", "C"):
        (tmp_path / j).mkdir()
        (tmp_path / j / "manifest.json").write_text(json.dumps({"stages": {}, "request": {"title": j}}),
                                                    encoding="utf-8")
    yield tmp_path
    jq._reset_for_tests()


def _wait(cond, sec: float = 6.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < sec:
        if cond():
            return True
        time.sleep(0.03)
    return False


def _idle() -> bool:
    st = jq.queue_state()
    return not st["worker"] and not st["queue"] and st["current"] is None


# ───────────── R7.1: ghi _job_queue.json hỏng không được giết worker ─────────────

def test_one_failed_save_does_not_kill_the_worker(root, monkeypatch):
    """Trước fix: PermissionError ở os.replace văng trong thread worker → không job nào chạy, current kẹt."""
    ran: list[str] = []
    jq.set_runner(lambda jid, o: (ran.append(jid), ("done", ""))[1])
    real, n = os.replace, {"c": 0}

    def flaky(a, b):
        n["c"] += 1
        if n["c"] == 2:
            raise PermissionError(13, "file đang bị tiến trình khác mở")
        return real(a, b)
    monkeypatch.setattr(os, "replace", flaky)
    jq.queue_add(["A", "B", "C"], opts={}, sort_first=False)
    assert _wait(lambda: ran == ["A", "B", "C"] and _idle()), (ran, jq.queue_state())
    assert jq.queue_state()["persist_ok"] is True          # thử lại ghi được


def test_disk_full_queue_keeps_running_and_reports_then_recovers(root, monkeypatch, capsys):
    ran: list[str] = []
    jq.set_runner(lambda jid, o: (ran.append(jid), ("done", ""))[1])
    real = os.replace
    broken = {"on": True}

    def full(a, b):
        if broken["on"]:
            raise OSError(28, "No space left on device")
        return real(a, b)
    monkeypatch.setattr(os, "replace", full)
    r = jq.queue_add(["A", "B"], opts={}, sort_first=False)      # API xếp hàng cũng không được văng 500
    assert r["ok"] and r["queued"] == ["A", "B"]
    assert _wait(lambda: ran == ["A", "B"] and _idle()), (ran, jq.queue_state())
    assert jq.queue_state()["persist_ok"] is False
    assert "KHÔNG ghi được" in capsys.readouterr().out

    broken["on"] = False                                          # có chỗ lại → lần ghi kế ghi bù
    jq.queue_add(["C"], opts={})
    assert _wait(lambda: ran == ["A", "B", "C"] and _idle())
    assert jq.queue_state()["persist_ok"] is True
    assert json.loads((root / "_job_queue.json").read_text(encoding="utf-8"))["queue"] == []


def test_unexpected_error_inside_loop_is_logged_and_loop_continues(root, monkeypatch, capsys):
    ran: list[str] = []
    jq.set_runner(lambda jid, o: (ran.append(jid), ("done", ""))[1])
    real, n = jq._hist_push, {"c": 0}

    def boom(*a, **k):
        n["c"] += 1
        if n["c"] == 1:
            raise RuntimeError("lỗi không lường trước trong vòng worker")
        return real(*a, **k)
    monkeypatch.setattr(jq, "_hist_push", boom)
    monkeypatch.setattr(time, "sleep", lambda s: None)            # bỏ 2 s chờ của lưới an toàn
    jq.queue_add(["A", "B"], opts={}, sort_first=False)
    assert _wait(lambda: ran == ["A", "B"] and not jq.queue_state()["worker"]), (ran, jq.queue_state())
    out = capsys.readouterr().out
    assert "LỖI trong vòng worker" in out and "Traceback" in out


# ───────────── R7.2: job giữ ghế chạy lại trước, kể cả khi hàng chờ bị sắp lại ─────────────

def test_parked_job_keeps_its_seat_when_queue_is_reordered(root):
    ran: list[str] = []
    ready = {"A": False}

    def runner(jid, o):
        ran.append(jid)
        if jid == "A" and ran.count("A") == 1:
            return "held", "chờ người duyệt"
        return "done", ""
    jq.set_runner(runner)
    jq.set_park_ready_hook(lambda jid: ready.get(jid, True))
    jq.queue_add(["A", "B", "C"], opts={}, sort_first=False)
    assert _wait(lambda: ran == ["A"] and jq.queue_state()["current"] == "A")
    jq.queue_move("C", "top")                                     # job khác bị đẩy lên đầu trong lúc A giữ ghế
    assert jq.queue_state()["queue"][0] == "C"
    time.sleep(0.6)
    assert ran == ["A"], "đang park thì không được chạy job khác"
    ready["A"] = True
    assert _wait(lambda: len(ran) == 4 and _idle()), ran
    assert ran == ["A", "A", "C", "B"]                            # trước fix: ['A', 'C', 'A', 'B']


def test_parked_job_removed_by_user_frees_the_seat(root):
    ran: list[str] = []

    def runner(jid, o):
        ran.append(jid)
        return ("held", "x") if jid == "A" else ("done", "")
    jq.set_runner(runner)
    jq.set_park_ready_hook(lambda jid: False)
    jq.queue_add(["A", "B"], opts={}, sort_first=False)
    assert _wait(lambda: ran == ["A"] and jq.queue_state()["current"] == "A")
    jq.queue_remove(["A"])
    assert _wait(lambda: ran == ["A", "B"] and _idle()), (ran, jq.queue_state())
