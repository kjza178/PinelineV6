"""Brain Telegram events / gỡ HOLD cũ / DECISION dedupe (fix 2026-11-12 agentG).
Cập nhật 2026-11-12 agentH: tin dựng bằng mẫu agent_brain_notify (không văn LLM). Không LLM, không HTTP."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import agents.agent_brain as ab


def _mk_job(root: Path, jid: str, stages: dict, agent: dict | None = None, output: bool = False) -> Path:
    d = root / jid
    d.mkdir(parents=True, exist_ok=True)
    data = {"stages": stages, "title": f"T-{jid}"}
    if agent is not None:
        data["agent"] = agent
    (d / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if output:
        (d / "output.mp4").write_bytes(b"x" * 2048)
    return d


def _set_stages(d: Path, **st) -> None:
    data = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    for k, v in st.items():
        data["stages"].setdefault(k, {})["status"] = v
    (d / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def sent(monkeypatch):
    box: list[str] = []
    monkeypatch.setattr(ab, "_tg_send", lambda msg, key: box.append(msg))
    monkeypatch.setattr(ab, "_tg_mode", lambda: "events")
    monkeypatch.setattr(ab, "log_line", lambda root, msg: None)
    return box


def _run_events(root: Path, state: dict) -> None:
    ab._tg_begin_tick(root)
    ab.emit_events(ab.detect_stage_events(root, state))
    ab._tg_flush_tick()


def test_stage_transition_one_message(tmp_path, sent):
    d = _mk_job(tmp_path, "job1", {"download": {"status": "done"}, "asr": {"status": "running"}})
    state: dict = {}
    _run_events(tmp_path, state)          # baseline — không báo
    assert sent == []
    _set_stages(d, asr="done", translate="running")
    _run_events(tmp_path, state)
    assert len(sent) == 1                  # gộp 2 sự kiện → 1 tin (1 khối job)
    assert "xong ASR" in sent[0] and "bắt đầu Dịch" in sent[0]
    _set_stages(d, translate="failed")
    _run_events(tmp_path, state)
    assert len(sent) == 2 and sent[1].startswith("❌") and "Dịch lỗi" in sent[1]


def test_healthy_running_no_message(tmp_path, sent):
    _mk_job(tmp_path, "job2", {"asr": {"status": "running", "progress": 10}})
    state: dict = {}
    for _ in range(3):
        _run_events(tmp_path, state)
    assert sent == []
    snap = {"jobs_focus": [{
        "job_id": "job2", "stages": {"asr": {"status": "running"}},
        "agent": {}, "qa_brain": {}, "phase": {"name": "idle"},
    }]}
    focus, _ = ab._deep_focus(snap, tmp_path)
    assert focus == []                     # deep tick bỏ qua job running khoẻ


def test_job_complete_event(tmp_path, sent):
    d = _mk_job(tmp_path, "job3", {s: {"status": "done"} for s in ("download", "asr", "translate", "tts")}
                | {"mux": {"status": "running"}})
    state: dict = {}
    _run_events(tmp_path, state)
    _set_stages(d, mux="done")
    (d / "output.mp4").write_bytes(b"x" * 10)
    _run_events(tmp_path, state)
    assert len(sent) == 1 and "xong, có video" in sent[0] and sent[0].startswith("✅")


def test_done_job_with_hold_is_cleared(tmp_path, sent):
    stages = {s: {"status": "done"} for s in ab._WIRED_STAGES}
    stages["translate"]["cur_text"] = "HOLD Brain — 1 high QA flags"
    d = _mk_job(tmp_path, "f0834acf0cc0", stages,
                agent={"hold_after_translate": True, "qa_blocked": True, "held_at": "qa_brain",
                       "hold_reason": "Second Brain: 1 high QA"}, output=True)
    ab._tg_begin_tick(tmp_path)
    cleared = ab.clear_stale_holds(tmp_path, {})
    ab._tg_flush_tick()
    assert cleared == ["f0834acf0cc0"]
    ag = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["agent"]
    assert ag["qa_blocked"] is False and ag["hold_after_translate"] is False
    assert "held_at" not in ag and "hold_reason" not in ag
    assert ag["hold_cleared"]["by"] == "brain:job_done"
    assert len(sent) == 1 and "Đã gỡ HOLD cũ" in sent[0] and "xong, có video" in sent[0]
    # lần 2: không còn gì để gỡ, không tin mới
    assert ab.clear_stale_holds(tmp_path, {}) == []
    assert len(sent) == 1


def test_hold_kept_when_not_finished(tmp_path, sent):
    _mk_job(tmp_path, "j5", {"translate": {"status": "done"}, "tts": {"status": "pending"},
                             "mux": {"status": "pending"}},
            agent={"hold_after_translate": True, "qa_blocked": True, "held_at": "qa_brain"})
    _mk_job(tmp_path, "j6", {"mux": {"status": "done"}, "tts": {"status": "running"}},
            agent={"qa_blocked": True}, output=True)
    assert ab.clear_stale_holds(tmp_path, {}) == []
    assert sent == []


def test_decision_dedupe_and_filter(sent):
    """agentH: DECISION (văn LLM) KHÔNG lên Telegram; chỉ tool mutate → tin mẫu kèm kết quả, khử lặp 2 h."""
    state: dict = {}
    healthy = {"failed": [], "high": 0, "held": False}
    ab._tg_begin_tick()
    assert ab._maybe_decision_tg(state, "jr", healthy, [{"tool": "pineline_job", "args": {}}], "ok") is False
    failed = {"failed": ["asr"], "err": {"asr": "firered_asr.py lỗi (mã 1)"}, "high": 0, "held": False}
    assert ab._maybe_decision_tg(state, "jf", failed, [], "DECISION jf: plan=báo user") is False
    assert ab._maybe_decision_tg(state, "jf", failed, [], "DECISION jf: diễn đạt khác") is False
    steps = [{"tool": "pineline_heal_job", "args": {"job_id": "jf", "apply": True, "level": "safe"},
              "result_preview": '{"ok": true, "results": []}'}]
    assert ab._maybe_decision_tg(state, "jf", failed, steps, "healed") is True
    assert ab._maybe_decision_tg(state, "jf", failed, steps, "healed lần 2") is False   # cùng kết quả
    ab._tg_flush_tick()
    assert len(sent) == 1 and "DECISION" not in sent[0]
    assert "Brain: heal job → không có việc để làm." in sent[0]


def test_tg_mode_levels():
    assert ab._tg_allowed("events", "event")
    assert ab._tg_allowed("events", "decision")
    assert not ab._tg_allowed("events", "info")
    assert not ab._tg_allowed("events", "progress")
    assert ab._tg_allowed("report", "progress")
    assert not ab._tg_allowed("off", "hold")


def test_digest_min_interval_and_change(monkeypatch, tmp_path, sent):
    monkeypatch.setattr(ab, "_tg_mode", lambda: "report")
    monkeypatch.setattr(ab, "_cfg", lambda k, d="": "120" if k == "AGENT_BRAIN_TG_SEC" else d)
    assert ab._tg_report_sec() == 600
    msgs = iter(["🧠 Brain báo cáo\nRUNNING:\n• a1 [asr] T 10% x",
                 "🧠 Brain báo cáo\nRUNNING:\n• a1 [asr] T 55% y",
                 "🧠 Brain báo cáo\nRUNNING:\n• a1 [translate] T 1%"])
    monkeypatch.setattr(ab, "_progress_digest", lambda root: next(msgs))
    st: dict = {}
    ab._maybe_tg_progress(tmp_path, st)
    st["last_tg_report"] = 0
    ab._maybe_tg_progress(tmp_path, st)      # chỉ % đổi → bỏ
    st["last_tg_report"] = 0
    ab._maybe_tg_progress(tmp_path, st)      # stage đổi → gửi
    assert len(sent) == 2


def test_full_tick_merges_into_one_message(monkeypatch, tmp_path, sent):
    monkeypatch.setattr(ab, "sense_infra", lambda: [])
    monkeypatch.setattr(ab, "_gpu_or_queue_busy", lambda root: True)
    monkeypatch.setattr(ab, "_llm_enabled", lambda: False)
    monkeypatch.setattr(ab, "_save_state", lambda root, st: None)
    d = _mk_job(tmp_path, "jA", {"asr": {"status": "running"}})
    stages = {s: {"status": "done"} for s in ab._WIRED_STAGES}
    _mk_job(tmp_path, "jB", stages, agent={"qa_blocked": True, "held_at": "qa_brain"}, output=True)
    state = {"job_stages": ab._scan_job_states(tmp_path)}
    monkeypatch.setattr(ab, "_load_state", lambda root: state)
    _set_stages(d, asr="done")
    ab.tick(tmp_path)
    assert len(sent) == 1
    assert "xong ASR" in sent[0] and "Đã gỡ HOLD cũ" in sent[0] and "(jB)" in sent[0]
    sent.clear()
    ab.tick(tmp_path)                       # không đổi gì → không tin
    assert sent == []


def test_heal_empty_plan_has_note(tmp_path):
    from agents.agent_heal import heal_job
    _mk_job(tmp_path, "jz", {"download": {"status": "done"},
                             "asr": {"status": "failed", "error": "firered_asr.py lỗi (mã 1)"}})
    r = heal_job(tmp_path, "jz", apply=False)
    assert r["plan"] == [] and "KHÔNG có action safe" in r["note"]
    r2 = heal_job(tmp_path, "jz", apply=True, hooks={})
    assert r2["results"] == [] and "asr_run_force" in r2["note"]


def test_llm_tick_deep_only_problem_jobs(monkeypatch, tmp_path, sent):
    import agents.agent_brain_context as abc
    _mk_job(tmp_path, "run1", {"asr": {"status": "running"}})
    _mk_job(tmp_path, "bad1", {"asr": {"status": "failed", "error": "firered_asr.py lỗi (mã 1)"}})
    snap = {"jobs_focus": [
        {"job_id": "run1", "stages": {"asr": {"status": "running"}}, "agent": {}, "qa_brain": {},
         "phase": {"name": "idle"}},
        {"job_id": "bad1", "stages": {"asr": {"status": "failed", "error": "firered_asr.py lỗi (mã 1)"}},
         "agent": {}, "qa_brain": {}, "phase": {"name": "idle"}},
    ]}
    monkeypatch.setattr(abc, "build_system_snapshot", lambda root, max_jobs=30: snap)
    monkeypatch.setattr(abc, "snapshot_to_prompt", lambda s, max_chars=4500: "SNAP")
    monkeypatch.setattr(ab, "_job_evidence", lambda jid, root=None: "EVID")
    calls: list[dict] = []

    def fake_llm(message, **kw):
        calls.append({"first_args": kw.get("force_first_args"), "msg": message})
        return {"ok": True, "via": "mock", "reply": "DECISION bad1: plan=báo user", "steps": [], "model": "m"}

    monkeypatch.setattr(ab, "_run_agent_llm", fake_llm)
    state: dict = {}
    ab._tg_begin_tick()
    ab.llm_tick(tmp_path, state)
    deep = [c for c in calls if c["first_args"]]
    assert [c["first_args"]["job_id"] for c in deep] == ["bad1"]
    assert "EVID" in deep[0]["msg"] and "pineline_heal_job" in deep[0]["msg"]
    ab.llm_tick(tmp_path, state)            # cùng tình trạng → không deep lại (cooldown)
    assert len([c for c in calls if c["first_args"]]) == 1
    ab._tg_flush_tick()
    assert sent == []                       # agentH: văn DECISION chỉ vào log
