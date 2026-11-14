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


# ───────────── R7.4: nhiều job lỗi liên tiếp → tạm dừng, không đốt sạch hàng chờ ─────────────

def _mk(root: Path, *names: str) -> None:
    for j in names:
        (root / j).mkdir(exist_ok=True)
        (root / j / "manifest.json").write_text(json.dumps({"stages": {}, "request": {"title": j}}), encoding="utf-8")


def test_consecutive_failures_pause_the_queue_and_notify(root, monkeypatch):
    monkeypatch.setenv("QUEUE_MAX_CONSEC_FAIL", "3")
    _mk(root, "D", "E")
    ran: list[str] = []
    notes: list[str] = []
    jq.set_runner(lambda jid, o: (ran.append(jid), ("failed", f"GPU chết ở {jid}"))[1])
    jq.set_notify_hook(notes.append)
    jq.queue_add(["A", "B", "C", "D", "E"], opts={}, sort_first=False)
    assert _wait(lambda: jq.queue_state()["paused"] is not None and not jq.queue_state()["worker"])
    st = jq.queue_state()
    assert ran == ["A", "B", "C"], "sau 3 job lỗi liên tiếp phải dừng, không chạy tiếp D, E"
    assert st["queue"] == ["D", "E"] and st["paused"]["left"] == 2 and st["consec_fail"] == 3
    assert notes and "TẠM DỪNG" in notes[0] and "GPU chết ở C" in notes[0]
    # tạm dừng được ghi ra đĩa → restart không tự chạy lại
    assert json.loads((root / "_job_queue.json").read_text(encoding="utf-8"))["paused"]["reason"]

    # xếp thêm job (vd feeder tự động) KHÔNG được tự gỡ tạm dừng
    _mk(root, "F")
    r = jq.queue_add(["F"], opts={})
    assert r["paused"] and not jq.queue_state()["worker"]
    time.sleep(0.2)
    assert ran == ["A", "B", "C"]

    # người dùng bấm «Chạy tiếp hàng chờ»
    jq.set_runner(lambda jid, o: (ran.append(jid), ("done", ""))[1])
    out = jq.queue_resume()
    assert out["resumed"] is True
    assert _wait(lambda: ran == ["A", "B", "C", "D", "E", "F"] and _idle()), ran
    assert jq.queue_state()["paused"] is None and jq.queue_state()["consec_fail"] == 0


def test_a_success_resets_the_failure_streak(root, monkeypatch):
    monkeypatch.setenv("QUEUE_MAX_CONSEC_FAIL", "2")
    _mk(root, "D")
    ran: list[str] = []
    res = {"A": "failed", "B": "done", "C": "failed", "D": "done"}
    jq.set_runner(lambda jid, o: (ran.append(jid), (res[jid], ""))[1])
    jq.queue_add(["A", "B", "C", "D"], opts={}, sort_first=False)
    assert _wait(lambda: ran == ["A", "B", "C", "D"] and _idle()), (ran, jq.queue_state())
    assert jq.queue_state()["paused"] is None


def test_stopped_jobs_do_not_count_as_failures(root, monkeypatch):
    monkeypatch.setenv("QUEUE_MAX_CONSEC_FAIL", "2")
    ran: list[str] = []
    jq.set_runner(lambda jid, o: (ran.append(jid), ("stopped", "đã dừng"))[1])
    jq.queue_add(["A", "B", "C"], opts={}, sort_first=False)
    assert _wait(lambda: ran == ["A", "B", "C"] and _idle()), (ran, jq.queue_state())
    assert jq.queue_state()["paused"] is None


def test_breaker_can_be_switched_off(root, monkeypatch):
    monkeypatch.setenv("QUEUE_MAX_CONSEC_FAIL", "0")
    ran: list[str] = []
    jq.set_runner(lambda jid, o: (ran.append(jid), ("failed", "x"))[1])
    jq.queue_add(["A", "B", "C"], opts={}, sort_first=False)
    assert _wait(lambda: ran == ["A", "B", "C"] and _idle())
    assert jq.queue_state()["paused"] is None


def test_last_job_failing_does_not_leave_an_empty_queue_paused(root, monkeypatch):
    monkeypatch.setenv("QUEUE_MAX_CONSEC_FAIL", "1")
    jq.set_runner(lambda jid, o: ("failed", "x"))
    jq.queue_add(["A"], opts={})
    assert _wait(_idle)
    assert jq.queue_state()["paused"] is None      # không còn job nào → không có gì để tạm dừng


def test_paused_state_survives_restart_and_stop_clears_it(root, monkeypatch):
    monkeypatch.setenv("QUEUE_MAX_CONSEC_FAIL", "1")
    jq.set_runner(lambda jid, o: ("failed", "x"))
    jq.queue_add(["A", "B"], opts={}, sort_first=False)
    assert _wait(lambda: jq.queue_state()["paused"] is not None)
    jq._paused = None
    jq._q.clear()
    jq._load()                                      # mô phỏng khởi động lại
    assert jq.queue_state()["paused"] and jq.queue_state()["queue"] == ["B"]
    jq._ensure_worker()                             # autostart lúc khởi động không được vượt tạm dừng
    assert not jq.queue_state()["worker"]
    jq.queue_stop()
    assert jq.queue_state()["paused"] is None and jq.queue_state()["queue"] == []


# ───────────── R7.5: nhật ký hàng chờ ra file ─────────────

def test_history_is_written_to_disk_and_read_newest_first(root):
    res = {"A": ("done", "hoàn tất"), "B": ("failed", "bước asr lỗi: hết VRAM")}
    jq.set_runner(lambda jid, o: res[jid])
    jq.queue_add(["A", "B"], opts={}, sort_first=False)
    assert _wait(_idle)
    h = jq.queue_history(10)
    assert [(x["job_id"], x["status"]) for x in h] == [("B", "failed"), ("A", "done")]
    assert h[0]["detail"] == "bước asr lỗi: hết VRAM" and h[0]["title"] == "B" and h[0]["at"]
    jq._hist.clear()                                # «restart»: bộ nhớ mất, file còn
    assert len(jq.queue_history(10)) == 2
    assert jq.queue_history(1)[0]["job_id"] == "B"


def test_history_write_failure_does_not_break_the_queue(root, monkeypatch, capsys):
    monkeypatch.setattr(jq, "_HISTORY_NAME", "khong/ton/tai\x00/x.jsonl")     # đường dẫn không ghi được
    ran: list[str] = []
    jq.set_runner(lambda jid, o: (ran.append(jid), ("done", ""))[1])
    jq.queue_add(["A", "B"], opts={}, sort_first=False)
    assert _wait(lambda: ran == ["A", "B"] and _idle()), (ran, jq.queue_state())
    assert "không ghi được nhật ký" in capsys.readouterr().out


def test_history_rotates_when_large(root, monkeypatch):
    monkeypatch.setattr(jq, "_HISTORY_MAX_BYTES", 200)
    for n in range(12):
        jq._hist_push("A", "done", "x" * 40)
    assert (root / (jq._HISTORY_NAME + ".1")).is_file()
    n_main = len((root / jq._HISTORY_NAME).read_text(encoding="utf-8").splitlines())
    assert len(jq.queue_history(100)) > n_main      # đọc cả file đã xoay, không chỉ file hiện tại


# ───────────── R7.3: HOLD cần người quá lâu → nhường ghế (tuỳ chọn) ─────────────

def _hold_runner(ran):
    def runner(jid, o):
        ran.append(jid)
        return ("held", "chờ người duyệt") if jid == "A" else ("done", "")
    return runner


def test_hold_keeps_the_seat_forever_by_default(root, monkeypatch):
    monkeypatch.delenv("QUEUE_HOLD_YIELD_SEC", raising=False)
    ran: list[str] = []
    jq.set_runner(_hold_runner(ran))
    jq.set_park_ready_hook(lambda jid: False)
    jq.set_needs_human_hook(lambda jid: True)
    jq.queue_add(["A", "B"], opts={}, sort_first=False)
    assert _wait(lambda: ran == ["A"])
    time.sleep(1.3)
    assert ran == ["A"] and jq.queue_state()["current"] == "A"      # luật Studio: không nhảy job


def test_hold_needing_a_human_yields_the_seat_when_enabled(root, monkeypatch):
    monkeypatch.setenv("QUEUE_HOLD_YIELD_SEC", "0.6")
    ran: list[str] = []
    notes: list[str] = []
    jq.set_runner(_hold_runner(ran))
    jq.set_park_ready_hook(lambda jid: False)
    jq.set_needs_human_hook(lambda jid: True)
    jq.set_notify_hook(notes.append)
    jq.queue_add(["A", "B", "C"], opts={}, sort_first=False)
    assert _wait(lambda: ran == ["A", "B", "C"] and _idle()), (ran, jq.queue_state())
    assert "A" not in jq.queue_state()["queue"]
    assert any("nhường ghế" in n for n in notes)
    assert [x["status"] for x in jq.queue_history(10) if x["job_id"] == "A"][0] == "sidelined"


def test_auto_retry_park_never_yields_the_seat(root, monkeypatch):
    """Park TỰ thử lại (soát dở, chờ hạn) không phải «cần người» → giữ ghế dù đã bật nhường ghế."""
    monkeypatch.setenv("QUEUE_HOLD_YIELD_SEC", "0.5")
    ran: list[str] = []
    jq.set_runner(_hold_runner(ran))
    jq.set_park_ready_hook(lambda jid: False)
    jq.set_needs_human_hook(lambda jid: False)
    jq.queue_add(["A", "B"], opts={}, sort_first=False)
    assert _wait(lambda: ran == ["A"])
    time.sleep(1.4)
    assert ran == ["A"] and jq.queue_state()["current"] == "A"


def test_lone_held_job_is_not_sidelined(root, monkeypatch):
    """Không có job nào khác chờ → nhường ghế vô nghĩa, giữ nguyên."""
    monkeypatch.setenv("QUEUE_HOLD_YIELD_SEC", "0.5")
    ran: list[str] = []
    jq.set_runner(_hold_runner(ran))
    jq.set_park_ready_hook(lambda jid: False)
    jq.set_needs_human_hook(lambda jid: True)
    jq.queue_add(["A"], opts={})
    assert _wait(lambda: ran == ["A"])
    time.sleep(1.3)
    assert jq.queue_state()["queue"] == ["A"] and jq.queue_state()["current"] == "A"
