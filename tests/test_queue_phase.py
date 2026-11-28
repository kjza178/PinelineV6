"""2026-11-21: job đang chạy phải nói LÀM GÌ / CHỜ GÌ (UI trước chỉ «Đang chạy» dù job giữ ghế chờ / chờ GPU)."""
import json
import time

import pytest

from core import job_queue as jq


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._phase.update(job=None, text="", ts=0.0)
    yield
    jq.set_phase_hook(None)
    jq._phase.update(job=None, text="", ts=0.0)


def test_set_phase_publishes_once_and_shows_for_current(monkeypatch):
    got = []
    jq.set_phase_hook(lambda j, t: got.append((j, t)))
    jq.set_phase("a", "dịch")
    jq.set_phase("a", "dịch")                 # trùng → không báo lại
    jq.set_phase("a", "lồng tiếng (TTS)")
    assert got == [("a", "dịch"), ("a", "lồng tiếng (TTS)")]
    monkeypatch.setattr(jq, "_current", "a")
    assert jq.queue_state()["phase"] == "lồng tiếng (TTS)"
    monkeypatch.setattr(jq, "_current", "b")   # phase của job khác không hiện
    assert jq.queue_state()["phase"] == ""


def _mk(tmp_path, agent):
    wd = tmp_path / "j"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"agent": agent}), encoding="utf-8")


def test_park_phase_explains_retry_and_human(tmp_path):
    _mk(tmp_path, {"held_at": "waiting_review", "hold_reason": "soát dở (simple) — AI lỗi",
                   "review_retry_at": time.time() + 300})
    t = jq._park_phase("j", "held", "")
    assert "soát dở (simple) — AI lỗi" in t and "tự thử lại sau ~" in t
    (tmp_path / "j" / "manifest.json").write_text(json.dumps({"agent": {
        "held_at": "review_failed", "hold_after_translate": True, "hold_reason": "REVIEW thất bại"}}), encoding="utf-8")
    t = jq._park_phase("j", "held", "")
    assert "CHỜ NGƯỜI DUYỆT" in t and "Chạy tiếp" in t


def test_read_job_keeps_real_translate_reason_when_pending(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    wd = tmp_path / "k"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"stages": {"translate": {
        "status": "pending", "cur_text": "soát dở — còn 12 câu chưa rv"}}}), encoding="utf-8")
    (wd / "script.json").write_text(json.dumps([{"i": 0, "zh": "你好", "vi": "Xin chào"}]), encoding="utf-8")
    server._job_cache.pop(str(wd), None)
    st = server.read_job(wd)["stages"]["translate"]
    assert st["cur_text"] == "soát dở — còn 12 câu chưa rv"


def test_watchdog_dumps_stuck_worker_stack(monkeypatch, capsys):
    import threading
    gate = threading.Event()
    t = threading.Thread(target=lambda: gate.wait(5), name="pineline-job-queue", daemon=True)
    t.start()
    try:
        jq._dump_stuck_stacks("x", "chuẩn bị", 75)
        out = capsys.readouterr().out
        assert "[queue-watchdog] job x đứng ở «chuẩn bị» 75 s" in out and "--- luồng pineline-job-queue ---" in out
    finally:
        gate.set()


def test_finish_hook_called_and_phase_cleared():
    got = []
    jq.set_finish_hook(lambda j, s, d: got.append((j, s, d)))
    try:
        jq._phase.update(job="a", text="chuẩn bị", ts=1.0)
        jq._finished("a", "failed", "giọng 'V9' không tồn tại")
        assert got == [("a", "failed", "giọng 'V9' không tồn tại")] and jq._phase["text"] == ""
    finally:
        jq.set_finish_hook(None)


def test_server_notes_early_failure_on_job(tmp_path, monkeypatch):
    import server
    from state.index import summarize
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    wd = tmp_path / "j1"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"stages": {"download": {"status": "done"}}}), encoding="utf-8")
    server._queue_finished("j1", "failed", "chưa chọn giọng")
    data = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))
    assert summarize("j1", data)["stop_note"] == "không chạy được: chưa chọn giọng"


def test_queue_add_starts_worker_for_job_already_queued(tmp_path, monkeypatch):
    """2026-11-21: sau restart (queue_autostart=false) job nằm sẵn trong hàng; bấm Chạy phải bật worker."""
    wd = tmp_path / "j9"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"stages": {"download": {"status": "done"}}}), encoding="utf-8")
    started = []
    monkeypatch.setattr(jq, "_q", ["j9"])
    monkeypatch.setattr(jq, "_paused", None)
    monkeypatch.setattr(jq, "_stop", True)
    monkeypatch.setattr(jq, "_save", lambda: True)
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: started.append(1))
    r = jq.queue_add(["j9"], opts={}, skip_done=False)
    assert r["queued"] == [] and started == [1] and jq._stop is False
