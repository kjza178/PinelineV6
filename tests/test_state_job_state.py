import json
import threading
import time

import pytest

from state import job_state as js
from state.index import JobIndex, summarize


def disk(wd):
    return json.loads((wd / "manifest.json").read_text(encoding="utf-8"))


def test_mark_running_done_failed(job, events):
    js.mark_running(job, "asr", cur_text="nghe")
    assert disk(job)["stages"]["asr"]["status"] == "running"
    assert events[-1][0] == "job.updated" and events[-1][1]["status"] == "running"
    js.mark_done(job, "asr", n_segments=12)
    st = disk(job)["stages"]["asr"]
    assert st["status"] == "done" and st["n_segments"] == 12 and "error" not in st
    js.mark_failed(job, "translate", ValueError("x" * 900))
    assert len(disk(job)["stages"]["translate"]["error"]) == 500


def test_concurrent_writers_do_not_lose_keys(job, events):
    """B9: 8 luồng ghi khoá khác nhau cùng lúc — không mất khoá nào (save() cũ sẽ mất)."""
    def worker(i):
        for k in range(15):
            js.set_stage(job, "tts", **{f"w{i}_{k}": k})
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    st = disk(job)["stages"]["tts"]
    assert sum(1 for k in st if k.startswith("w")) == 8 * 15
    assert disk(job)["stages"]["download"]["status"] == "done"


def test_hold_survives_progress_writes(job, events):
    js.set_hold(job, reason="agent_quality", detail="BLEU thấp")
    js.set_progress(job, "translate", publish=lambda *a: None, review_done=5)
    ag = disk(job)["agent"]
    assert ag["held_at"] == "agent_quality" and ag["hold_after_translate"] is True
    assert disk(job)["stages"]["translate"]["review_done"] == 5


def test_clear_hold_filtered(job, events):
    js.set_hold(job, reason="qa_gate", qa_blocked=True)
    js.clear_hold(job, reasons={"waiting_review"})          # không khớp → giữ nguyên
    assert disk(job)["agent"]["held_at"] == "qa_gate"
    js.clear_hold(job, reasons={"qa_gate"}, override_qa=True)
    ag = disk(job)["agent"]
    assert "held_at" not in ag and "qa_blocked" not in ag and ag["qa_override"] is True


def test_review_retry_then_hold(job, events):
    outs = [js.set_review_retry(job, n_unrev=3, retry_sec=60, max_tries=2)["result"] for _ in range(3)]
    assert outs == ["park", "park", "hold"]
    ag = disk(job)["agent"]
    assert ag["held_at"] == "review_failed" and ag["hold_after_translate"] and "review_retry_at" not in ag


def test_review_retry_matches_legacy_fields(job, events):
    js.mark_running(job, "tts")
    js.set_review_retry(job, n_unrev=4, retry_sec=60, max_tries=3)
    d = disk(job)
    assert d["stages"]["translate"]["status"] == "pending"
    assert d["stages"]["tts"]["status"] == "pending" and d["stages"]["tts"]["cur_text"] == "chờ soát xong"
    assert d["agent"]["held_at"] == "waiting_review" and d["agent"]["review_retries"] == 1


def test_mutate_and_decline(job, events):
    js.mutate(job, lambda d: d.setdefault("agent", {}).update(x=1))
    assert disk(job)["agent"]["x"] == 1
    before = (job / "manifest.json").stat().st_mtime_ns
    js.mutate(job, lambda d: False)
    assert (job / "manifest.json").stat().st_mtime_ns == before


def test_park_ready_pure():
    now = 1000.0
    assert js.park_ready({"agent": {"held_at": "waiting_review", "review_retry_at": 999}}, 3, now=now)
    assert not js.park_ready({"agent": {"held_at": "waiting_review", "review_retry_at": 1001}}, 3, now=now)
    assert js.park_ready({"agent": {"held_at": "waiting_review"}}, 0, now=now)
    assert not js.park_ready({"agent": {"held_at": "waiting_review", "hold_after_translate": True}}, 0)
    assert js.park_ready({}, 5)


def test_queue_opts_strips_pin(job, events):
    js.set_queue_opts(job, {"voice": "a", "pin_opts": True})
    js.set_queue_opts(job, {"speed": 1.1}, merge=True)
    assert disk(job)["queue_opts"] == {"voice": "a", "speed": 1.1}


def test_reset_running(job, events):
    js.mark_running(job, "tts")
    assert js.reset_running(job) == ["tts"]
    assert disk(job)["stages"]["tts"]["status"] == "pending"
    assert js.reset_running(job) == []          # không đổi → không ghi, không lỗi


def test_corrupt_manifest_raises_and_keeps_file(job, events):
    (job / "manifest.json").write_text("{hỏng", encoding="utf-8")
    with pytest.raises(js.StateError):
        js.mark_running(job, "asr")
    assert (job / "manifest.json").read_text(encoding="utf-8") == "{hỏng"


def test_create_job(tmp_path, events):
    wd = tmp_path / "new"
    js.create_job(wd, request={"url": "u"}, title="T")
    d = disk(wd)
    assert d["request"] == {"url": "u"} and d["title"] == "T" and d["stages"] == {}


def test_index_summarize_and_list(tmp_path):
    got = []
    ix = JobIndex(publish=lambda t, d: got.append(t))
    ix.apply("b", {"stages": {n: {"status": "done"} for n in ("download", "asr", "translate", "tts", "mux")}})
    ix.apply("a", {"stages": {"asr": {"status": "running", "cur_text": "x"}}})
    ix.apply("c", {"stages": {}, "agent": {"held_at": "qa_gate"}})
    assert [r["job_id"] for r in ix.list()] == ["a", "c", "b"]
    assert ix.get("a")["running_stage"] == "asr"
    n = len(got)
    ix.apply("a", {"stages": {"asr": {"status": "running", "cur_text": "x"}}})
    # không đổi → không phát job.updated; F3: chỉ job.touched (bus gộp theo job) cho UI cũ refresh
    assert got[n:] == ["job.touched"]
    ix.remove("a")
    assert got[-1] == "job.removed" and ix.get("a") is None
    assert summarize("z", {"stages": {"asr": {"status": "failed"}}})["status"] == "failed"


def test_index_load_all(tmp_path):
    for name in ("j1", "j2"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "manifest.json").write_text('{"stages":{}}', encoding="utf-8")
    (tmp_path / "j3").mkdir()
    (tmp_path / "j3" / "manifest.json").write_text("{hỏng", encoding="utf-8")
    ix = JobIndex(publish=lambda *a: None)
    assert ix.load_all(tmp_path) == 2


def test_list_is_fast_no_disk(tmp_path):
    ix = JobIndex(publish=lambda *a: None)
    for i in range(500):
        ix.apply(f"j{i:03}", {"stages": {"asr": {"status": "done"}}})
    t0 = time.perf_counter()
    for _ in range(100):
        ix.list()
    assert time.perf_counter() - t0 < 3.0     # 100 lần list 500 job < 1 s (cũ: 500 lần đọc file / lần)
