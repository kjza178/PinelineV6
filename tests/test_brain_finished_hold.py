"""Brain: job đã xong không bị heal + phân loại tạm dừng (park tự thử lại ≠ HOLD cần người).
fix 2026-11-13 agentN — dữ liệu giả lập từ manifest thật V2 work (2493d3c7cb29, ae9956bb8fb3, ec10e47767ed,
2937ffd7989f). Không LLM, không HTTP, không Telegram thật.
V6: port agent R6 2026-11-13 (import package agents.*; ghi manifest qua state/job_state)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import agents.agent_brain as ab
import agents.agent_brain_notify as nt
from agents import agent_heal
from agents import agent_hold

TZ7 = timezone(timedelta(hours=7))
W = ("download", "asr", "translate", "tts", "mux")


def _w(root: Path, jid: str, data: dict, files: tuple[str, ...] = ()) -> Path:
    d = root / jid
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for f in files:
        (d / f).write_bytes(b"x" * 4096)
    return d


def _done(**extra) -> dict:
    st = {s: {"status": "done"} for s in W}
    for k, v in extra.items():
        st.setdefault(k, {}).update(v)
    return st


def _real_three(root: Path) -> None:
    # 2493d3c7cb29: parent đã ghép 4 part, đã đăng; chỉ còn manifest/meta/output*.mp4 (không script.json)
    _w(root, "2493d3c7cb29", {
        "title_vi": "Xâm nhập trò chơi: Đánh thường hồi máu, một mũi tên sát phạt thần linh",
        "stages": _done(mux={"cur_text": "Hoàn tất bản Full (4 parts ghép lại) ✓", "is_full_merged": True}),
        "split_info": {"status": "merged", "sub_job_ids": [f"2493d3c7cb29_p0{i}" for i in range(1, 5)]},
        "upload_status": "uploaded", "uploaded_at": "2026-11-12T16:21:50Z",
    }, ("output.mp4", "output_full.mp4", "tts_config.json"))
    # ae9956bb8fb3: done, output.mp4, script.json/tts.json/asr.json đã dọn, chưa đăng
    _w(root, "ae9956bb8fb3", {"title_vi": "Thiên phú vô song, một tay quét sạch ba ngàn đế giả!",
                              "merged_from_jobs": ["a", "b"], "stages": _done()}, ("output.mp4",))
    # ec10e47767ed: đã đăng 08/11 (upload_error cũ còn sót), output.mp4
    _w(root, "ec10e47767ed", {"title_vi": "Tu vi mỗi giây tăng vạn năm, một niệm áp đảo Đông Hoang",
                              "stages": _done(), "upload_status": "uploaded",
                              "upload_error": "EOF occurred in violation of protocol"}, ("output.mp4",))


# ---------------------------------------------------------------------------
# 1. job_finished — một định nghĩa
# ---------------------------------------------------------------------------
def test_job_finished_real_three(tmp_path):
    _real_three(tmp_path)
    for jid in ("2493d3c7cb29", "ae9956bb8fb3", "ec10e47767ed"):
        assert agent_hold.job_finished(tmp_path / jid), jid
        assert ab.job_finished(tmp_path / jid), jid          # agent_brain dùng chung định nghĩa


def test_job_finished_variants(tmp_path):
    # đã đăng nhưng output đã dọn → vẫn xong
    assert agent_hold.job_finished(_w(tmp_path, "up", {"stages": {"mux": {"status": "pending"}},
                                                          "upload_status": "published"}))
    # parent đã ghép Full, chỉ còn output_full.mp4
    _w(tmp_path, "par", {"stages": {"mux": {"status": "done", "is_full_merged": True}},
                         "split_info": {"status": "merged"}}, ("output_full.mp4",))
    assert agent_hold.job_finished(tmp_path / "par")
    # part của parent đã ghép: mux part pending, script đã dọn → coi là xong
    assert agent_hold.job_finished(_w(tmp_path, "par_p02", {"stages": {"tts": {"status": "pending"}}}))
    # part đang chạy lại → KHÔNG xong
    assert not agent_hold.job_finished(_w(tmp_path, "par_p03", {"stages": {"tts": {"status": "running"}}}))
    # mux done + output nhưng translate bị đánh stale (người yêu cầu làm lại) → KHÔNG xong
    assert not agent_hold.job_finished(_w(tmp_path, "st", {"stages": _done(translate={"stale": True})},
                                          ("output.mp4",)))
    # mux done nhưng chưa có output, chưa đăng, còn stage pending → KHÔNG xong
    assert not agent_hold.job_finished(_w(tmp_path, "no", {"stages": {"mux": {"status": "done"},
                                                                       "tts": {"status": "pending"}}}))
    # part của parent CHƯA ghép → theo chính part
    _w(tmp_path, "p2", {"stages": {"mux": {"status": "pending"}}, "split_info": {"status": "split"}})
    assert not agent_hold.job_finished(_w(tmp_path, "p2_p01", {"stages": {"tts": {"status": "pending"}}}))


# ---------------------------------------------------------------------------
# 2. heal / scan / watch bỏ qua job đã xong (gốc lỗi «heal thất bại … thiếu script.json»)
# ---------------------------------------------------------------------------
def test_diagnose_scan_heal_skip_finished(tmp_path):
    _real_three(tmp_path)
    called: list = []
    hooks = {"tts_run": lambda j: called.append(j) or {"error": "chưa dịch xong (thiếu script.json)"}}
    scan = agent_heal.scan_problem_jobs(tmp_path)
    assert scan["n_problems"] == 0
    for jid in ("2493d3c7cb29", "ae9956bb8fb3", "ec10e47767ed"):
        d = agent_heal.diagnose_job(tmp_path, jid)
        assert d["finished"] and d["actions_safe"] == [] and d["actions_aggressive"] == []
        r = agent_heal.heal_job(tmp_path, jid, apply=True, level="aggressive", hooks=hooks)
        assert r["applied"] is False and r.get("finished")
    assert called == []
    b = agent_heal.heal_failed_batch(tmp_path, apply=True, hooks=hooks)
    assert b["n_healed"] == 0 and called == []


def test_detector_root_cause_not_finished(tmp_path):
    """Job CHƯA xong (mux pending) nhưng tts done + tts.json/asr.json đã dọn → không «tts_not_started»/«asr_bad»."""
    _w(tmp_path, "j", {"stages": {"download": {"status": "done"}, "asr": {"status": "done"},
                                  "translate": {"status": "done"}, "tts": {"status": "done"},
                                  "mux": {"status": "pending"}}}, ("script.json",))
    (tmp_path / "j" / "script.json").write_text("[]", encoding="utf-8")
    codes = {i["code"] for i in agent_heal.diagnose_job(tmp_path, "j")["issues"]}
    assert "tts_not_started" not in codes and "asr_bad" not in codes
    # thật sự chưa TTS → vẫn bắt
    _w(tmp_path, "k", {"stages": {"asr": {"status": "done"}, "translate": {"status": "done"},
                                  "tts": {"status": "pending"}}}, ("asr.json",))
    codes = {i["code"] for i in agent_heal.diagnose_job(tmp_path, "k")["issues"]}
    assert "tts_not_started" in codes


def test_brain_apply_and_sense_skip_finished(tmp_path, monkeypatch):
    _real_three(tmp_path)
    monkeypatch.setattr(ab, "log_line", lambda root, msg: None)
    import agents.agent_heal as ah
    monkeypatch.setattr(ah, "heal_job", lambda *a, **k: pytest.fail("không được heal job đã xong"))
    acts = [{"type": "heal_job", "job_id": j, "level": "safe", "fp": f"fp:{j}"}
            for j in ("2493d3c7cb29", "ae9956bb8fb3", "ec10e47767ed")]
    res = ab.apply_actions(tmp_path, acts, {})
    assert [r.get("skipped") for r in res] == ["finished"] * 3
    # job xong có stage failed cũ (vd separate) → sense_jobs không ra incident
    data = json.loads((tmp_path / "ae9956bb8fb3" / "manifest.json").read_text(encoding="utf-8"))
    data["stages"]["separate"] = {"status": "failed", "error": "old"}
    (tmp_path / "ae9956bb8fb3" / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    assert ab.sense_jobs(tmp_path) == []
    snap = {"jobs_focus": [{"job_id": "ae9956bb8fb3", "stages": data["stages"], "agent": {},
                            "qa_brain": {}, "phase": {"name": "post_tts"}}]}
    assert ab._deep_focus(snap, tmp_path)[0] == []      # deep tick LLM bỏ qua


# ---------------------------------------------------------------------------
# 3. Phân loại tạm dừng
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("agent,cls", [
    ({}, None),
    ({"held_at": "waiting_review", "simple_park": True, "review_retry_at": 1.0}, "park"),
    ({"held_at": "waiting_review"}, "park"),                                  # B2 legacy
    ({"held_at": "split_parts", "split_wait_parts": ["x_p01"]}, "split_wait"),
    ({"held_at": "review_failed", "hold_after_translate": True}, "review_failed"),
    ({"held_at": "qa_brain", "qa_blocked": True, "hold_after_translate": True}, "qa"),
    ({"held_at": "qa_gate", "qa_blocked": True, "hold_after_translate": True}, "qa"),
    ({"held_at": "simple_gate", "hold_after_translate": True}, "qa"),
    ({"hold_after_translate": True}, "after_translate"),
    ({"held_at": "waiting_review", "hold_after_translate": True}, "after_translate"),
    ({"held_at": "after_translate", "hold_after_translate": True}, "after_translate"),
])
def test_hold_class(agent, cls):
    assert agent_hold.hold_class(agent) == cls


@pytest.fixture
def sent(monkeypatch):
    box: list[str] = []
    monkeypatch.setattr(ab, "_tg_send", lambda msg, key: box.append(msg))
    monkeypatch.setattr(ab, "_tg_mode", lambda: "events")
    monkeypatch.setattr(ab, "log_line", lambda root, msg: None)
    monkeypatch.setattr(ab, "_tz", lambda: TZ7)
    return box


def _tick(root: Path, state: dict) -> None:
    ab._tg_begin_tick(root)
    ab.emit_events(ab.detect_stage_events(root, state))
    ab._tg_flush_tick()


MF_2937 = {"title_vi": "Lớp học huyền thoại bị lộ, các đại ca không thể giấu kín được nữa - Phần 3",
           "stages": {"download": {"status": "done"}, "asr": {"status": "done"},
                      "translate": {"status": "pending"}}}


def _set_agent(d: Path, agent: dict) -> None:
    data = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    data["agent"] = agent
    (d / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _at(h: int, m: int) -> float:
    return datetime(2026, 11, 13, h, m, tzinfo=TZ7).timestamp()


def test_simple_park_once_then_review_failed(tmp_path, sent):
    d = _w(tmp_path, "2937ffd7989f", dict(MF_2937))
    state: dict = {}
    _tick(tmp_path, state)                              # baseline
    _set_agent(d, {"held_at": "waiting_review", "simple_park": True, "review_retries": 1,
                   "review_retry_at": _at(19, 45),
                   "hold_reason": "soát dở (simple) — còn 3 câu chưa soát"})
    _tick(tmp_path, state)
    assert sent == ["⏸ Lớp học huyền thoại bị lộ P3 (2937ff) — tạm dừng soát dở 3 câu, "
                    "tự thử lại lúc 19:45 (lần 1/3)"]
    for _ in range(3):                                  # không nhắc lại
        _tick(tmp_path, state)
    assert len(sent) == 1
    _set_agent(d, {"held_at": "waiting_review", "simple_park": True, "review_retries": 2,
                   "review_retry_at": _at(19, 50), "hold_reason": "soát dở (simple) — còn 2 câu chưa soát"})
    _tick(tmp_path, state)                              # lượt park mới → 1 tin
    assert len(sent) == 2 and sent[1].endswith("tạm dừng soát dở 2 câu, tự thử lại lúc 19:50 (lần 2/3)")
    assert all("HOLD" not in m and "Cần bạn" not in m for m in sent)
    _set_agent(d, {"held_at": "review_failed", "hold_after_translate": True, "review_retries": 4,
                   "hold_reason": "dịch simple chưa xong sau 3 lượt — còn 2 câu chưa soát, cần người xem"})
    _tick(tmp_path, state)
    assert len(sent) == 3
    assert sent[2] == ("⛔ Lớp học huyền thoại bị lộ P3 (2937ff) — HOLD: dịch simple chưa xong sau 3 lượt — "
                       "còn 2 câu chưa soát, cần người xem.\n"
                       "Cần bạn: xem các câu chưa soát/dịch rồi bấm Chạy tiếp.")
    _tick(tmp_path, state)
    assert len(sent) == 3


def test_qa_hold_need_human_and_old_state_no_flood(tmp_path, sent):
    d = _w(tmp_path, "f0834acf0cc0", {"title_vi": "Độc Đế Vô Song 3D Phần 6",
                                      "stages": {"translate": {"status": "done"}}})
    state: dict = {}
    _tick(tmp_path, state)
    _set_agent(d, {"held_at": "qa_brain", "qa_blocked": True, "hold_after_translate": True,
                   "hold_reason": "Second Brain: 1 high QA — chặn TTS/mux"})
    _tick(tmp_path, state)
    assert sent == ["⛔ Độc Đế Vô Song 3D P6 (f0834a) — HOLD: Second Brain: 1 high QA — chặn TTS/mux.\n"
                    "Cần bạn: duyệt bản dịch rồi bấm Chạy tiếp (hoặc chạy tay Lồng tiếng)."]
    # state cũ (trước bản này: không có hold_cls) đang held → không báo lại sau restart
    for v in state["job_stages"].values():
        v.pop("hold_cls", None)
        v.pop("park_try", None)
    _tick(tmp_path, state)
    assert len(sent) == 1


def test_split_wait_message(tmp_path, sent):
    d = _w(tmp_path, "60892cc63cb7", {"title_vi": "Phim thử nghiệm dài", "stages": {"mux": {"status": "pending"}}})
    state: dict = {}
    _tick(tmp_path, state)
    _set_agent(d, {"held_at": "split_parts", "split_wait_parts": ["60892cc63cb7_p01"],
                   "hold_reason": "chờ part park/HOLD: 60892cc63cb7_p01 — hàng đợi tự chạy lại khi part tới hạn"})
    _tick(tmp_path, state)
    assert sent == ["⏸ Phim thử nghiệm dài (60892c) — tạm dừng chờ part P1 soát dở, tự chạy tiếp khi part tới hạn"]


def test_no_hold_event_on_finished(tmp_path, sent):
    _real_three(tmp_path)
    state: dict = {}
    _tick(tmp_path, state)
    _set_agent(tmp_path / "ae9956bb8fb3", {"held_at": "waiting_review", "simple_park": True})
    _tick(tmp_path, state)
    assert sent == []


# ---------------------------------------------------------------------------
# 4. Vòng đời: gỡ dấu park khi chạy tay / job xong
# ---------------------------------------------------------------------------
def test_manual_override_clears_park_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_hold, "_notify_override", lambda *a, **k: None)
    monkeypatch.setattr(agent_hold, "_log", lambda *a, **k: None)
    d = _w(tmp_path, "j1", {"stages": {}, "agent": {
        "held_at": "review_failed", "hold_after_translate": True, "review_retries": 4,
        "simple_park": True, "hold_reason": "x"}})
    assert agent_hold.manual_override(d, "translate")
    ag = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["agent"]
    assert not any(k in ag for k in ("held_at", "review_retries", "review_retry_at", "simple_park"))
    assert ag["hold_override"]["was"]["held_at"] == "review_failed"


def test_clear_stale_holds_finished_and_quiet_parts(tmp_path, sent):
    _real_three(tmp_path)
    _w(tmp_path, "2493d3c7cb29_p02", {"stages": {"translate": {"status": "pending"}},
                                      "agent": {"held_at": "waiting_review", "simple_park": True,
                                                "review_retry_at": 1.0, "review_retries": 1}})
    _set_agent(tmp_path / "ec10e47767ed", {"held_at": "qa_gate", "qa_blocked": True, "hold_after_translate": True})
    ab._tg_begin_tick(tmp_path)
    cleared = ab.clear_stale_holds(tmp_path, {})
    ab._tg_flush_tick()
    assert sorted(cleared) == ["2493d3c7cb29_p02", "ec10e47767ed"]
    ag = json.loads((tmp_path / "2493d3c7cb29_p02" / "manifest.json").read_text(encoding="utf-8"))["agent"]
    assert not any(k in ag for k in ("held_at", "simple_park", "review_retry_at", "review_retries"))
    assert len(sent) == 1 and "ec10e4" in sent[0] and "2493d3_p02" not in sent[0]   # part gỡ im lặng


def test_tts_missing_only_after_translate_done(tmp_path):
    """2937ffd7989f thật: asr pending, translate pending, tts n_missing=1466 cũ → không «tts_missing_clips»."""
    _w(tmp_path, "2937ffd7989f", {"stages": {"download": {"status": "done"}, "asr": {"status": "pending"},
                                             "translate": {"status": "pending"},
                                             "tts": {"status": "pending", "n_missing": 1466}}})
    d = agent_heal.diagnose_job(tmp_path, "2937ffd7989f")
    assert "tts_missing_clips" not in {i["code"] for i in d["issues"]}
    assert not any(a["action"] == "tts_run" for a in d["actions_safe"])
