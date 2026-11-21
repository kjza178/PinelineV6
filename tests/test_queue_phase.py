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
