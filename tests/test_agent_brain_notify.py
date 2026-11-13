"""Second Brain — mẫu tin Telegram (A), gỡ HOLD khi chạy tay (B), race heal khởi động (C).
fix 2026-11-12 agentH. Không LLM thật, không HTTP, không Telegram thật."""
from __future__ import annotations

import ast
import json
import re
import time
from datetime import timedelta, timezone
from pathlib import Path

import pytest

import agents.agent_brain as ab
import agents.agent_brain_notify as nt
from agents import agent_hold

TZ7 = timezone(timedelta(hours=7))
ROOT = Path(__file__).resolve().parents[1]

# Dữ liệu giống manifest thật (V2 work 2026-11-12)
MF_2937 = {"title_vi": "Lớp học huyền thoại bị lộ, các đại ca không thể giấu kín được nữa - Phần 3",
           "request": {"title": "曝光神话班级,全班大佬藏不住了第三季"}, "stages": {}}
MF_F083 = {"title_vi": "Độc Đế Vô Song 3D Phần 6", "request": {"title": "毒帝无双3D版第六季"}, "stages": {}}
MF_2493 = {"title_vi": "Xâm nhập trò chơi: Đánh thường hồi máu, một mũi tên sát phạt thần linh", "stages": {}}
MF_2493_P02 = {"meta": {"title": "游戏入侵：普攻加血一箭亿万伤屠戮神明三 - Phần 2/4"},
               "request": {"title": "游戏入侵：普攻加血一箭亿万伤屠戮神明三 - Phần 2/4"}, "stages": {}}


def _assert_plain(msg: str) -> None:
    assert "|" not in msg and "<" not in msg and "**" not in msg and "`" not in msg
    for block in msg.split("\n\n"):
        assert len(block.splitlines()) <= 4          # tiêu đề + ≤ 3 dòng


# ---------------------------------------------------------------------------
# A — snapshot từng loại tin
# ---------------------------------------------------------------------------
def test_snapshot_failed_heal_noop():
    msg = nt.render_job("2937ffd7989f", MF_2937, [
        {"kind": "stage_failed", "stage": "asr", "error": "firered_asr.py lỗi (mã 1)",
         "at": "2026-11-11T22:57:08.816554+00:00"},
        {"kind": "heal", "by": "brain", "outcome": "noop", "actions": [], "reason": ""},
    ], tz=TZ7)
    assert msg == ("❌ Lớp học huyền thoại bị lộ P3 (2937ff)\n"
                   "ASR lỗi (firered mã 1) lúc 05:57.\n"
                   "Brain: chẩn đoán xong, heal an toàn không có việc để làm.\n"
                   "Cần bạn: xếp lại ASR khi GPU rảnh.")
    _assert_plain(msg)


def test_snapshot_job_done_hold_cleared():
    msg = nt.render_job("f0834acf0cc0", MF_F083,
                        [{"kind": "job_done"}, {"kind": "hold_cleared", "why": "job_done"}], tz=TZ7)
    assert msg == "✅ Độc Đế Vô Song 3D P6 (f0834a) — xong, có video. Đã gỡ HOLD cũ."


def test_snapshot_stage_start_part_uses_parent_title():
    msg = nt.render_job("2493d3c7cb29_p02", MF_2493_P02, [{"kind": "stage_start", "stage": "translate"}],
                        parent=MF_2493, tz=TZ7)
    assert msg == "▶️ Xâm nhập trò chơi P2 (2493d3_p02) — bắt đầu Dịch"


def test_snapshot_stage_done_and_start_same_tick():
    msg = nt.render_job("2493d3c7cb29_p02", MF_2493_P02, [
        {"kind": "stage_done", "stage": "asr"}, {"kind": "stage_start", "stage": "translate"}],
        parent=MF_2493, tz=TZ7)
    assert msg == "▶️ Xâm nhập trò chơi P2 (2493d3_p02) — xong ASR, bắt đầu Dịch"


def test_snapshot_hold_set():
    msg = nt.render_job("f0834acf0cc0", MF_F083, [
        {"kind": "hold_set", "reason": "Second Brain: 1 high QA — chặn TTS/mux"}], tz=TZ7)
    # 2026-11-13 agentN: HOLD cần người = «⛔ … — HOLD: lý do.» (⏸ dành cho park tự thử lại)
    assert msg == ("⛔ Độc Đế Vô Song 3D P6 (f0834a) — HOLD: Second Brain: 1 high QA — chặn TTS/mux.\n"
                   "Cần bạn: duyệt bản dịch rồi bấm Chạy tiếp (hoặc chạy tay Lồng tiếng).")


def test_snapshot_manual_override():
    msg = nt.render_job("f0834acf0cc0", MF_F083, [{"kind": "hold_cleared", "why": "manual", "stage": "tts"}])
    assert msg == "🔓 Độc Đế Vô Song 3D P6 (f0834a)\nĐã gỡ HOLD vì bạn chạy tay Lồng tiếng."


def test_snapshot_llm_action_fail_and_ok():
    out, det = nt.tool_outcome("pineline_heal_job", '{"error": "HTTP 409 job đang chạy"}')
    msg = nt.render_job("2937ffd7989f", MF_2937,
                        [{"kind": "llm_action", "tool": "pineline_heal_job", "outcome": out, "detail": det}])
    assert msg == ("🩹 Lớp học huyền thoại bị lộ P3 (2937ff)\n"
                   "Brain: heal job → thất bại: HTTP 409 job đang chạy.\n"
                   "Cần bạn: xem log brain/job.")
    out2, det2 = nt.tool_outcome("pineline_heal_job",
                                 '{"ok": true, "results": [{"action": "tts_run", "result": {"started": true}}]}')
    msg2 = nt.render_job("2937ffd7989f", MF_2937,
                         [{"kind": "llm_action", "tool": "pineline_heal_job", "outcome": out2, "detail": det2}])
    assert msg2.splitlines()[1] == "Brain: heal job (chạy lại lồng tiếng) → thành công."


def test_snapshot_remind_and_watch_heal():
    msg = nt.render_job("2937ffd7989f", MF_2937, [
        {"kind": "remind_failed", "stage": "asr", "error": "firered_asr.py lỗi (mã 1)",
         "at": "2026-11-11T22:57:08Z"}], tz=TZ7)
    assert msg.splitlines()[1] == "Vẫn lỗi ASR (firered mã 1) từ 05:57, chưa xử lý (nhắc lại)."
    hs = nt.heal_summary({"ok": True, "job_id": "f0834acf0cc0",
                          "results": [{"action": "mux_retry", "result": {"started": True}}]})
    msg2 = nt.render_job("f0834acf0cc0", MF_F083, [{"kind": "heal", "by": "watch", **hs}])
    assert msg2 == "🩹 Độc Đế Vô Song 3D P6 (f0834a)\nWatcher: tự sửa (render lại) → thành công."


def test_clean_strips_llm_markup():
    raw = "**Job … ĐÃ XÁC MINH** | | <badge color=\"success\">Đang chạy</badge> | Stage | Trạng thái |"
    c = nt.clean(raw, 200)
    assert "|" not in c and "<" not in c and "*" not in c
    assert "Đang chạy" in c


def test_label_fallbacks():
    assert nt.job_label("abcdef123456", {"request": {"title": "毒帝无双3D版第六季"}}) == "毒帝无双3D版第六季 (abcdef)"
    assert nt.job_label("abcdef123456", None) == "Job (abcdef)"
    assert nt.short_id("2493d3c7cb29_p02") == "2493d3_p02"
    # tiêu đề thật trong work V2
    assert nt.short_title("Tạo Thần Phần 1: Chương Hải Đại") == ("Tạo Thần", 1)
    assert nt.short_title("Thần Tài Vô Địch! Tôi thức tỉnh nghề nghiệp ẩn duy nhất (Phần 6)") == ("Thần Tài Vô Địch", 6)
    assert nt.short_title("Tu vi tăng vạn năm mỗi giây, một ý quét sạch Thiên Khư Thánh Điện - Đông Hoang phần 4") \
        == ("Tu vi tăng vạn năm mỗi giây", 4)


def test_tick_one_message_per_tick_blocks(tmp_path, monkeypatch):
    box: list[str] = []
    monkeypatch.setattr(ab, "_tg_send", lambda msg, key: box.append(msg))
    monkeypatch.setattr(ab, "_tg_mode", lambda: "events")
    monkeypatch.setattr(ab, "_cfg", lambda k, d="": "+7" if k == "AGENT_BRAIN_TZ" else d)
    for jid, mf in (("2937ffd7989f", MF_2937), ("f0834acf0cc0", MF_F083)):
        (tmp_path / jid).mkdir()
        (tmp_path / jid / "manifest.json").write_text(json.dumps(mf, ensure_ascii=False), encoding="utf-8")
    ab._tg_begin_tick(tmp_path)
    ab._emit("2937ffd7989f", "stage_failed", stage="asr", error="firered_asr.py lỗi (mã 1)",
             at="2026-11-11T22:57:08Z")
    ab._emit("f0834acf0cc0", "job_done", level="action")
    ab._emit("f0834acf0cc0", "hold_cleared", level="action", why="job_done")
    ab._emit("f0834acf0cc0", "job_done", level="action")          # trùng → bỏ
    ab._tg_flush_tick()
    assert len(box) == 1
    blocks = box[0].split("\n\n")
    assert blocks[0].startswith("❌ Lớp học huyền thoại bị lộ P3 (2937ff)")
    assert "lúc 05:57" in blocks[0]
    assert blocks[1] == "✅ Độc Đế Vô Song 3D P6 (f0834a) — xong, có video. Đã gỡ HOLD cũ."
    _assert_plain(box[0])


def test_failed_once_then_remind_every_2h(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "log_line", lambda r, m: None)
    d = tmp_path / "jf"
    d.mkdir()
    mf = {"title_vi": "Phim A", "stages": {"asr": {"status": "running"}}}
    (d / "manifest.json").write_text(json.dumps(mf), encoding="utf-8")
    state: dict = {}
    assert ab.detect_stage_events(tmp_path, state) == []          # baseline
    mf["stages"]["asr"] = {"status": "failed", "error": "firered_asr.py lỗi (mã 1)"}
    (d / "manifest.json").write_text(json.dumps(mf), encoding="utf-8")
    ev = ab.detect_stage_events(tmp_path, state)
    assert [e["kind"] for e in ev] == ["stage_failed"]
    for _ in range(3):                                            # vẫn lỗi, chưa đủ 2 h → im
        assert ab.detect_stage_events(tmp_path, state) == []
    state["fail_notes"]["jf:asr"]["at"] -= 7300                   # giả lập 2 h sau
    ev = ab.detect_stage_events(tmp_path, state)
    assert [e["kind"] for e in ev] == ["remind_failed"]
    assert ab.detect_stage_events(tmp_path, state) == []
    for _ in range(5):                                            # tối đa 3 lần nhắc
        state["fail_notes"]["jf:asr"]["at"] -= 7300
        ab.detect_stage_events(tmp_path, state)
    assert state["fail_notes"]["jf:asr"]["n"] == 3


def test_heal_noop_reported_once_per_window(tmp_path, monkeypatch):
    box: list[str] = []
    monkeypatch.setattr(ab, "_tg_send", lambda msg, key: box.append(msg))
    monkeypatch.setattr(ab, "_tg_mode", lambda: "events")
    monkeypatch.setattr(ab, "log_line", lambda r, m: None)
    d = tmp_path / "2937ffd7989f"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps(
        MF_2937 | {"stages": {"download": {"status": "done"},
                              "asr": {"status": "failed", "error": "firered_asr.py lỗi (mã 1)"}}},
        ensure_ascii=False), encoding="utf-8")
    state: dict = {}
    for i in range(3):
        ab._tg_begin_tick(tmp_path)
        act = {"type": "heal_job", "job_id": "2937ffd7989f", "level": "safe", "fp": f"l1:x{i}"}
        state.setdefault("fps", {})["l1agg:2937ffd7989f"] = {"at": time.time()}   # không escalate
        ab.apply_actions(tmp_path, [act], state, hooks={})
        ab._tg_flush_tick()
    assert len(box) == 1
    assert "heal an toàn không có việc để làm" in box[0]


def test_llm_free_text_never_sent(tmp_path, monkeypatch):
    box: list[str] = []
    monkeypatch.setattr(ab, "_tg_send", lambda msg, key: box.append(msg))
    monkeypatch.setattr(ab, "_tg_mode", lambda: "events")
    ab._tg_begin_tick(tmp_path)
    reply = "**Job ĐÃ XÁC MINH** | <badge color=\"success\">Đang chạy</badge> | Stage | Trạng thái |"
    ab._maybe_decision_tg({}, "2493d3c7cb29", {"failed": ["asr"], "high": 0, "held": False}, [], reply)
    ab._tg_flush_tick()
    assert box == []


# ---------------------------------------------------------------------------
# B — gỡ HOLD khi chạy tay
# ---------------------------------------------------------------------------
class _Req:
    def __init__(self, headers):
        self.headers = headers


def _held_job(root: Path, jid: str = "f0834acf0cc0", high_i=(12,)) -> Path:
    d = root / jid
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(MF_F083 | {
        "stages": {"translate": {"status": "done", "cur_text": "HOLD Brain — 1 high QA flags"},
                   "tts": {"status": "pending"}},
        "agent": {"hold_after_translate": True, "qa_blocked": True, "held_at": "qa_brain",
                  "hold_reason": "Second Brain: 1 high QA — chặn TTS/mux"}}, ensure_ascii=False), encoding="utf-8")
    (d / "qa_brain.json").write_text(json.dumps({"high_open": len(high_i), "high_i": list(high_i)}), encoding="utf-8")
    return d


def test_is_manual_request():
    assert agent_hold.is_manual_request(_Req({"user-agent": "Mozilla/5.0 WebView2"}))
    assert not agent_hold.is_manual_request(None)                              # gọi Python trực tiếp
    assert not agent_hold.is_manual_request(_Req({"user-agent": "Python-urllib/3.11"}))
    assert not agent_hold.is_manual_request(_Req({"x-pineline-caller": "brain", "user-agent": "Mozilla"}))


def test_manual_override_clears_hold_and_notifies(tmp_path, monkeypatch):
    sent: list = []
    monkeypatch.setattr(agent_hold, "_notify_override", lambda wd, st, data: sent.append((wd.name, st)))
    d = _held_job(tmp_path)
    ov = agent_hold.maybe_manual_override(_Req({"user-agent": "Mozilla"}), d, "tts", source="run_stage")
    assert ov and ov["by"] == "manual" and ov["stage"] == "tts" and ov["high"] == 1
    data = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    ag = data["agent"]
    assert ag["qa_blocked"] is False and ag["hold_after_translate"] is False and "held_at" not in ag
    assert ag["hold_override"]["was"]["held_at"] == "qa_brain"
    assert data["stages"]["translate"]["cur_text"].startswith("HOLD đã gỡ")
    assert sent == [("f0834acf0cc0", "tts")]
    assert "Đã gỡ HOLD vì bạn chạy tay" in (tmp_path / "logs" / "agent_brain.log").read_text(encoding="utf-8") \
        or "gỡ HOLD vì chạy tay" in (tmp_path / "logs" / "agent_brain.log").read_text(encoding="utf-8")
    # lần 2: không còn HOLD → không làm gì
    assert agent_hold.maybe_manual_override(_Req({"user-agent": "Mozilla"}), d, "mux") is None
    assert len(sent) == 1


def test_auto_call_does_not_override(tmp_path):
    d = _held_job(tmp_path)
    assert agent_hold.maybe_manual_override(None, d, "tts") is None
    assert json.loads((d / "manifest.json").read_text(encoding="utf-8"))["agent"]["qa_blocked"] is True


def test_override_render_message(tmp_path):
    d = _held_job(tmp_path)
    msg = agent_hold.render_override_msg(d, "tts", MF_F083)
    assert msg == "🔓 Độc Đế Vô Song 3D P6 (f0834a)\nĐã gỡ HOLD vì bạn chạy tay Lồng tiếng."


def test_hold_suppressed_until_new_high(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_hold, "_notify_override", lambda *a: None)
    d = _held_job(tmp_path, high_i=(12,))
    agent_hold.manual_override(d, "tts")
    assert agent_hold.hold_suppressed(d) is True                       # cùng high → không HOLD lại
    (d / "qa_brain.json").write_text(json.dumps({"high_open": 1, "high_i": [40]}), encoding="utf-8")
    assert agent_hold.hold_suppressed(d) is False                      # high mới (câu khác)
    (d / "qa_brain.json").write_text(json.dumps({"high_open": 2, "high_i": [12, 13]}), encoding="utf-8")
    assert agent_hold.hold_suppressed(d) is False                      # nhiều high hơn


def test_brain_runtime_set_hold_respects_override(tmp_path, monkeypatch):
    import sys
    import types
    monkeypatch.setattr(agent_hold, "_notify_override", lambda *a: None)
    d = _held_job(tmp_path)
    agent_hold.manual_override(d, "tts")
    fake = types.SimpleNamespace(_rt=None)
    captured = {}
    fake.Runtime = lambda **kw: kw
    fake.bind = lambda rt: captured.update(rt)
    import agents
    monkeypatch.setitem(sys.modules, "agents.agent_core", fake)   # V6: package agents/
    monkeypatch.setattr(agents, "agent_core", fake, raising=False)
    ab._ensure_brain_runtime(tmp_path)
    r = captured["set_hold_after_translate"]("f0834acf0cc0", True)
    assert r["ok"] is False and "gỡ HOLD" in r["error"]
    ag = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["agent"]
    assert not ag.get("hold_after_translate")
    r2 = captured["set_hold_after_translate"]("f0834acf0cc0", False)     # gỡ thì luôn được (Manifest.update)
    assert r2["ok"] is True


def test_server_routes_wire_manual_override():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    for fn, stage in (("run_stage", "stage"), ("translate_fill", '"translate"'), ("regen_tts", '"tts"'),
                      ("retranslate_job", '"translate"'), ("mux_job", '"mux"')):
        m = re.search(rf"\ndef {fn}\((.*?)\):(.*?)\n(?:@app\.|def )", src, re.S)
        assert m, fn
        assert "request: Request = None" in m.group(1), fn
        assert f"_manual_hold_override(request, job_id, {stage}" in m.group(2), fn
    assert "_ah.hold_suppressed(wd, _ag_now)" in src                    # _auto_gpu không HOLD lại
    ra = (ROOT / "http_routes" / "routes_agent.py").read_text(encoding="utf-8")   # V6: http_routes/
    assert "agent_hold.auto_caller() and agent_hold.hold_suppressed(work_dir)" in ra


# ---------------------------------------------------------------------------
# C — race heal lúc khởi động + đuôi log engine
# ---------------------------------------------------------------------------
def test_start_delay_config(monkeypatch):
    import agents.agent_heal as agent_heal
    monkeypatch.setenv("AGENT_HEAL_START_DELAY_SEC", "240")
    monkeypatch.setitem(__import__("sys").modules, "core.config", None)     # không đọc config.json thật
    assert agent_heal.start_delay_sec() == 240
    monkeypatch.delenv("AGENT_HEAL_START_DELAY_SEC")
    assert agent_heal.start_delay_sec() == 180


def test_brain_grace_skips_heal_and_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "_started_at", time.time())
    monkeypatch.setattr(ab, "sense_infra", lambda: [])
    monkeypatch.setattr(ab, "_gpu_or_queue_busy", lambda root: False)
    monkeypatch.setattr(ab, "_save_state", lambda r, s: None)
    monkeypatch.setattr(ab, "_load_state", lambda r: {})
    monkeypatch.setattr(ab, "_llm_enabled", lambda: True)
    monkeypatch.setattr(ab, "_tg_send", lambda m, key: None)
    logs: list[str] = []
    monkeypatch.setattr(ab, "log_line", lambda r, m: logs.append(m))
    called = {"heal": 0, "llm": 0}
    import agents.agent_heal as agent_heal
    monkeypatch.setattr(agent_heal, "scan_problem_jobs", lambda root, list_jobs=None: {
        "problems": [{"job_id": "2937ffd7989f", "failed_stages": ["asr"], "issue_codes": ["asr_failed"]}]})
    monkeypatch.setattr(agent_heal, "heal_job", lambda *a, **k: called.__setitem__("heal", called["heal"] + 1) or {})
    monkeypatch.setattr(ab, "llm_tick", lambda *a, **k: called.__setitem__("llm", called["llm"] + 1) or {"ok": True})
    ab.tick(tmp_path)
    assert called == {"heal": 0, "llm": 0}
    assert any("hoãn heal" in x for x in logs)
    monkeypatch.setattr(ab, "_started_at", time.time() - 1000)          # hết hoãn
    ab.tick(tmp_path)
    assert called["llm"] == 1


def test_heal_job_skips_active_job(tmp_path):
    import agents.agent_heal as agent_heal
    d = tmp_path / "2937ffd7989f"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"stages": {
        "download": {"status": "done"}, "asr": {"status": "failed", "error": "firered_asr.py lỗi (mã 1)"}}}),
        encoding="utf-8")
    calls: list = []
    hooks = {"job_active": lambda jid: "engine", "asr_force": lambda jid: calls.append(jid)}
    r = agent_heal.heal_job(tmp_path, "2937ffd7989f", apply=True, level="aggressive", hooks=hooks)
    assert r["applied"] is False and "đang chạy" in r["skipped"] and calls == []
    assert nt.heal_summary(r)["outcome"] == "skipped"


def test_job_active_queue_current(monkeypatch):
    import sys
    import types
    import agents.agent_heal as agent_heal
    monkeypatch.setitem(sys.modules, "core.job_queue", types.SimpleNamespace(_current="2493d3c7cb29"))   # V6: core.job_queue
    monkeypatch.delitem(sys.modules, "server", raising=False)
    assert agent_heal.job_active("2493d3c7cb29") == "queue.current"
    assert agent_heal.job_active("2493d3c7cb29_p02") == "queue.current"
    assert agent_heal.job_active("2937ffd7989f") is None


def test_watcher_delays_first_heal(tmp_path, monkeypatch):
    import agents.agent_watch as agent_watch
    monkeypatch.setenv("AGENT_HEAL_START_DELAY_SEC", "180")
    monkeypatch.setitem(__import__("sys").modules, "core.config", None)
    calls: list = []
    monkeypatch.setattr(agent_watch, "watch_once", lambda *a, **k: calls.append(1))
    waits: list = []

    class _Ev:
        def __init__(self):
            self.n = 0

        def is_set(self):
            return self.n >= 2

        def wait(self, t):
            waits.append(t)
            self.n += 1
            return self.n >= 2

    monkeypatch.setattr(agent_watch, "_stop", _Ev())
    agent_watch._loop(tmp_path, None, {}, 300, 10)
    assert waits[0] == 180 and len(calls) == 1          # chờ 180 s rồi mới heal lần đầu


def _load_save_engine_tail():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "_save_engine_tail")
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("_ENGINE_TAIL_LINES", "_ENGINE_STAGE")
                                                  for t in n.targets))]
    assert len(keep) == 3
    from core.manifest import Manifest
    from state import job_state as js
    ns = {"Path": Path, "time": time, "Manifest": Manifest, "js": js}   # V6: ghi qua state.job_state
    exec(compile(ast.Module(body=keep, type_ignores=[]), "server_extract", "exec"), ns)
    return ns["_save_engine_tail"]


def test_engine_tail_saved_on_failure(tmp_path):
    save = _load_save_engine_tail()
    d = tmp_path / "2937ffd7989f"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"stages": {"asr": {"status": "running"}}}), encoding="utf-8")
    lines = [f"line {i}" for i in range(40)] + ["Traceback (most recent call last):", "CUDA error: out of memory"]
    save(d, "firered_asr.py", 1, lines[-40:])
    st = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["stages"]["asr"]
    assert st["engine_rc"] == 1 and st["engine_tail"][-1] == "CUDA error: out of memory"
    assert len(st["engine_tail"]) == 40 and st["status"] == "running"
    log = (d / "engine_fail_firered_asr.log").read_text(encoding="utf-8")
    assert log.startswith("# firered_asr.py rc=1") and "out of memory" in log


def test_run_engine_keeps_tail_source():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    body = src[src.index("def _run_engine("):src.index("_ENGINE_TAIL_LINES = 40")]
    assert "_tail.append(" in body and "_save_engine_tail(Path(work_dir), script_name, proc.returncode" in body


def test_render_worker_tail_saved_v6():
    """V6 (port P6): render chạy workers/render_worker (tiến trình con) → đuôi log lưu stage mux."""
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    body = src[src.index("def _render_subprocess("):]
    body = body[:body.index("\n@app.")]
    assert "_tail.append(" in body and '_save_engine_tail(Path(work_dir), "render_worker"' in body
    assert '"render_worker": "mux"' in src


def test_v6_server_manual_override_clears_hold_and_updates_index(tmp_path, monkeypatch):
    """V6 (port P6): route chạy tay → server._manual_hold_override → agent_hold → state.job_state
    (Manifest.update) → state.index cập nhật (UI nhận job.updated qua SSE, không polling)."""
    server = pytest.importorskip("server")
    from state.index import index
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(agent_hold, "_notify_override", lambda *a: None)
    d = _held_job(tmp_path)
    server._manual_hold_override(None, d.name, "tts", "tts")                     # gọi Python (agent/queue)
    assert json.loads((d / "manifest.json").read_text(encoding="utf-8"))["agent"]["qa_blocked"] is True
    server._manual_hold_override(_Req({"user-agent": "Mozilla/5.0 WebView2"}), d.name, "tts", "tts")
    ag = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["agent"]
    assert ag["qa_blocked"] is False and ag["hold_override"]["stage"] == "tts" and "held_at" not in ag
    row = index.get(d.name) or {}
    assert not row.get("hold_reason")
