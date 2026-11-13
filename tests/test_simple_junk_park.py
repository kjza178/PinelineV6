"""fix 2026-11-13 agentM — sự cố 60892cc63cb7_p01: câu ASR rác zh="None." (i=1039, 1904.45–1904.93 s) trong
5150 câu → GPT không soát → cổng simple park «còn 1 câu chưa soát» mãi → part kẹt, phim không ghép được.

Kiểm: text_junk, FireRed parse bỏ rác, simple tự soát câu rác (vi «…» → TTS slot im lặng), ngưỡng
SIMPLE_UNREVIEWED_TOLERANCE + lượt review bổ sung câu thiếu, parent auto-split giữ ghế & quay lại part park.
"""
from __future__ import annotations

import json
import time

import pytest

import engines.llm as llm
from tests.test_translate_simple import (  # noqa: F401  (fixture autouse _iso dùng lại)
    PLAN, _items, _iso, _man, _nmt, _read, _trace,
)
from engines.text_junk import is_junk_text
from engines.translate_simple import pipeline, stage_review, validate
from engines.translate_simple.plan_view import PlanView
from engines.translate_simple.script_store import ScriptStore

N_INC = 5150
I_JUNK = 1039


# ───────────────────────── text_junk ─────────────────────────

@pytest.mark.parametrize("s", ["None.", "None", "none", " NULL! ", "nan。", "", "   ", "。", "……", "?!", "—", None,
                               "♪♪", "「」"])
def test_junk_true(s):
    assert is_junk_text(s) is True


@pytest.mark.parametrize("s", ["哈哈。", "嗯。", "我要报案。", "OK.", "123", "None of that.", "Nonesuch", "啊？", "5"])
def test_junk_false(s):
    assert is_junk_text(s) is False


# ───────────────────────── FireRed parse ─────────────────────────

def test_firered_parse_drops_junk_sentences_and_logs(capsys):
    import engines.asr.firered_asr as firered_asr
    res = {"sentences": [
        {"start_ms": 1902430, "end_ms": 1904240, "text": "我们制服了一个行凶的歹徒。"},
        {"start_ms": 1904450, "end_ms": 1904930, "text": "None."},
        {"start_ms": 1905000, "end_ms": 1905560, "text": "哈哈。"},
        {"start_ms": 1906000, "end_ms": 1906300, "text": "。"},
        {"start_ms": 1906400, "end_ms": 1906600, "text": None},
    ]}
    rows = firered_asr._parse_firered_result(res)
    assert [r["text"] for r in rows] == ["我们制服了一个行凶的歹徒。", "哈哈。"]
    out = capsys.readouterr().out
    assert "bỏ 2 câu rác" in out and "None." in out


def test_firered_parse_words_and_text_fallback_drop_junk():
    import engines.asr.firered_asr as firered_asr
    words = {"words": [{"start_ms": 0, "end_ms": 300, "text": "None"}, {"start_ms": 300, "end_ms": 400, "text": "."},
                       {"start_ms": 500, "end_ms": 900, "text": "你好"}, {"start_ms": 900, "end_ms": 950, "text": "。"}]}
    assert [r["text"] for r in firered_asr._parse_firered_result(words)] == ["你好。"]
    assert firered_asr._parse_firered_result({"text": "None.", "dur_s": 1.0}) == []
    assert firered_asr._parse_firered_result({"text": "你好。", "dur_s": 1.0})[0]["text"] == "你好。"


def test_firered_transcribe_sliced_no_junk(tmp_path, monkeypatch):
    """transcribe_sliced (bản agent L) đi qua _parse_firered_result → câu rác không vào rows tuyệt đối."""
    import engines.asr.firered_asr as firered_asr
    import engines.separate as separate
    from types import SimpleNamespace
    monkeypatch.setattr(separate, "wav_info", lambda p: SimpleNamespace(rate=16000, channels=1, width=2, frames=16000 * 10))
    monkeypatch.setattr(firered_asr, "_is_pcm16_mono16k", lambda info: True)
    monkeypatch.setattr(firered_asr, "plan_slices", lambda src, conf=None: [
        {"start": 0, "end": 160000, "core0": 0, "core1": 160000}])
    monkeypatch.setattr(firered_asr, "describe_plan", lambda plan, rate: "1 slice")
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"")

    def proc(path, uttid):
        return {"sentences": [{"start_ms": 0, "end_ms": 900, "text": "你好。"},
                              {"start_ms": 1000, "end_ms": 1480, "text": "None."}]}
    rows = firered_asr.transcribe_sliced(proc, wav, tmp_path)
    assert [r["text"] for r in rows] == ["你好。"]


# ───────────────────────── helpers luồng simple ─────────────────────────

def _segs(n: int, junk: dict[int, str] | None = None) -> list[dict]:
    junk = junk or {}
    out = []
    for i in range(n):
        st = round(i * 1.833, 2)
        out.append({"i": i, "start": st, "end": round(st + 0.8, 2), "text": junk.get(i, f"这是第{i}句话。")})
    return out


def _wd(tmp_path, segs, script=None, manifest=None):
    wd = tmp_path / "60892cc63cb7_p01"
    wd.mkdir()
    (wd / "asr.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
    (wd / "glossary.json").write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    if script is not None:
        (wd / "script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    (wd / "manifest.json").write_text(json.dumps(manifest or {"stages": {}}), encoding="utf-8")
    return wd


class Picky:
    """Giả GPT: trả đủ mọi câu, TRỪ các i trong `never` (như GPT bỏ qua câu «None.»).
    `until`: {i: số lần gọi chứa i mà vẫn bỏ} — sau đó trả bình thường."""

    def __init__(self, never=(), until=None):
        self.never = set(never)
        self.until = dict(until or {})
        self.calls: list[list[int]] = []

    def __call__(self, messages, model=None, **kw):
        items = _items(messages[1]["content"])
        ids = [x["i"] for x in items]
        self.calls.append(ids)
        lines = []
        for x in items:
            i = x["i"]
            if i in self.never:
                continue
            if self.until.get(i, 0) > 0:
                self.until[i] -= 1
                continue
            lines.append({"i": i, "vi": x["nhap"] or f"Câu {i}.", "spk": "?", "to": "*", "conf": 0.5})
        return json.dumps({"lines": lines}, ensure_ascii=False)


# ───────────────────────── tái hiện đúng sự cố ─────────────────────────

def test_incident_replay_5150_rows_one_none_parked_job_passes(tmp_path, monkeypatch):
    """Trạng thái đĩa y như job kẹt: 5149 câu rv, i=1039 zh/vi «None.» mt=1 chưa rv, agent park.
    Chạy lại (retry) → câu rác tự soát (vi «…», junk, skip_tts) → cổng done, không gửi i=1039 cho GPT."""
    segs = _segs(N_INC, {I_JUNK: "None."})
    segs[I_JUNK].update({"start": 1904.45, "end": 1904.93})
    script = []
    for s in segs:
        r = {"i": s["i"], "start": s["start"], "end": s["end"], "zh": s["text"], "vi": f"Câu {s['i']}.", "mt": 1,
             "s_spk": "?", "s_to": "*", "s_conf": 0.5, "rv": 1}
        if s["i"] == I_JUNK:
            r = {"i": I_JUNK, "start": 1904.45, "end": 1904.93, "zh": "None.", "vi": "None.", "mt": 1}
        script.append(r)
    man = {"stages": {"translate": {"status": "pending", "mode": "simple", "cur_text": "soát dở — còn 1 câu chưa soát"}},
           "agent": {"held_at": "waiting_review", "simple_park": True, "review_retries": 1,
                     "hold_reason": "soát dở (simple) — còn 1 câu chưa soát"},
           "translate_pipeline": "simple"}
    wd = _wd(tmp_path, segs, script, man)
    _nmt(monkeypatch)
    fake = Picky(never={I_JUNK})
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert res["status"] == "done", res["gate"]
    assert not any(I_JUNK in c for c in fake.calls)          # câu rác không bao giờ gửi GPT
    rows = _read(wd)
    r = rows[I_JUNK]
    assert r["vi"] == "…" and r["rv"] == 1 and r["junk"] == 1 and r["skip_tts"] == 1
    assert not r.get("unreviewed_accepted")
    st = _man(wd)["stages"]["translate"]
    assert st["status"] == "done" and st["simple"]["review"]["junk"] == 1
    assert any(t["step"] == "junk" and t["i"] == I_JUNK for t in _trace(wd))
    # cổng TTS server: không câu trống, coverage đủ
    import engines.translate as T
    assert T.translate_ready_for_tts(wd) == (True, "ok")
    # TTS hiểu «…» = trivial → slot im lặng đúng độ dài (không gọi engine, không lệch timeline)
    import engines.clone_tts as clone_tts
    assert clone_tts._is_trivial_vi(r["vi"])
    ent = clone_tts._silence_entry(r, tmp_path, "vieneu")
    assert ent["silence"] and abs(ent["dur"] - 0.48) < 0.05


def test_incident_fresh_run_5150_rows_gpt_ignores_none(tmp_path, monkeypatch):
    """Chạy từ đầu 5150 câu, GPT không bao giờ trả câu «None.» → vẫn done (trước fix: park «còn 1 câu chưa soát»)."""
    monkeypatch.setenv("SIMPLE_BATCH", "60")
    segs = _segs(N_INC, {I_JUNK: "None."})
    wd = _wd(tmp_path, segs)
    _nmt(monkeypatch, table={i: f"Nháp {i}." for i in range(N_INC)})
    fake = Picky(never={I_JUNK})
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert res["status"] == "done", res["gate"]
    rows = _read(wd)
    assert rows[I_JUNK]["vi"] == "…" and rows[I_JUNK]["rv"] == 1
    assert sum(1 for r in rows.values() if r.get("rv")) == N_INC


def test_before_fix_behaviour_would_park_without_junk_rule(tmp_path, monkeypatch):
    """Đối chứng: tắt nhận diện rác + tolerance 0 → đúng kịch bản cũ (park «còn 1 câu chưa soát»)."""
    monkeypatch.setenv("SIMPLE_UNREVIEWED_TOLERANCE", "0")
    monkeypatch.setattr(pipeline, "_mark_junk", lambda store: 0)
    segs = _segs(200, {50: "None."})
    wd = _wd(tmp_path, segs)
    _nmt(monkeypatch, table={i: f"Nháp {i}." for i in range(200)})
    monkeypatch.setattr(llm, "chat", Picky(never={50}))
    res = pipeline.run(wd, "Tiếng Việt")
    assert res["status"] == "park" and "còn 1 câu chưa soát" in res["gate"]["reasons"]


# ───────────────────────── câu rác khác ─────────────────────────

def test_junk_variants_auto_reviewed_and_never_flagged(tmp_path, monkeypatch):
    junk = {3: "。", 7: "", 9: "null", 12: "……"}
    segs = _segs(30, junk)
    wd = _wd(tmp_path, segs)
    _nmt(monkeypatch, table={i: f"Nháp {i}." for i in range(30) if i not in junk})
    fake = Picky()
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt", rereview=True)
    assert res["status"] == "done"
    rows = _read(wd)
    for i in junk:
        assert rows[i]["junk"] == 1 and rows[i]["vi"] == "…" and rows[i]["rv"] == 1 and not rows[i].get("s_flags")
        assert not any(i in c for c in fake.calls)            # kể cả rereview
    assert not rows[4].get("junk")


def test_junk_flag_cleared_when_zh_no_longer_junk(tmp_path):
    wd = tmp_path / "j"
    wd.mkdir()
    st = ScriptStore(wd)
    st.rows = [{"i": 0, "zh": "我要报案。", "vi": "…", "rv": 1, "junk": 1, "skip_tts": 1},
               {"i": 1, "zh": "None.", "vi": "Không.", "manual": 1}]
    assert pipeline._mark_junk(st) == 0
    assert "junk" not in st.rows[0] and not st.rows[0].get("rv") and st.rows[0]["vi"] == ""
    assert st.rows[1]["vi"] == "Không." and "junk" not in st.rows[1]   # manual không đụng


def test_validate_and_review_skip_junk():
    pv = PlanView(PLAN)
    r = {"i": 0, "zh": "None.", "vi": "", "junk": 1, "rv": 1}
    assert validate.check_row(r, pv, 0.7) == []


# ───────────────────────── ngưỡng câu chưa soát ─────────────────────────

def _run_unrev(tmp_path, monkeypatch, n, never, *, until=None, drafts=True, tol=None):
    if tol is not None:
        monkeypatch.setenv("SIMPLE_UNREVIEWED_TOLERANCE", str(tol))
    segs = _segs(n)
    wd = _wd(tmp_path, segs)
    _nmt(monkeypatch, table=({i: f"Nháp {i}." for i in range(n)} if drafts else {}))
    fake = Picky(never=never, until=until)
    monkeypatch.setattr(llm, "chat", fake)
    return wd, fake, pipeline.run(wd, "Tiếng Việt")


def test_tolerance_default_formula():
    from engines.translate_simple import cfg
    assert cfg.unreviewed_tolerance(5150) == 10
    assert cfg.unreviewed_tolerance(100) == 3
    assert cfg.unreviewed_tolerance(0) == 3


def test_tolerance_accepts_drafts_within_limit(tmp_path, monkeypatch):
    wd, fake, res = _run_unrev(tmp_path, monkeypatch, 300, never={10, 200})
    assert res["status"] == "done", res["gate"]
    rows = _read(wd)
    for i in (10, 200):
        assert rows[i]["unreviewed_accepted"] == 1 and rows[i]["rv"] == 1 and rows[i]["vi"] == f"Nháp {i}."
    assert sum(1 for r in rows.values() if r.get("unreviewed_accepted")) == 2
    st = _man(wd)["stages"]["translate"]
    assert st["status"] == "done" and st["simple"]["unreviewed_accepted"] == [10, 200]
    assert "nhận 2 câu nháp chưa soát" in st["simple"]["accept_reason"] and "nhận 2 câu" in st["cur_text"]
    tr = _trace(wd)
    assert any(t["step"] == "unreviewed_accepted" and t["i"] == 10 for t in tr)
    assert any(t["step"] == "gate" and "nhận 2 câu nháp" in t.get("why", "") for t in tr)
    # lượt bổ sung CHỈ câu thiếu, lô nhỏ (≤ SIMPLE_MISSING_BATCH=4) — chạy trước khi nhận nháp
    small = [c for c in fake.calls if set(c) <= {10, 200}]
    assert small and all(len(c) <= 4 for c in small)


def test_tolerance_exceeded_parks_unchanged(tmp_path, monkeypatch):
    never = {5, 50, 100, 150}                              # 4 > max(3, 0.6) = 3
    wd, _, res = _run_unrev(tmp_path, monkeypatch, 300, never=never)
    assert res["status"] == "park" and "còn 4 câu chưa soát" in res["gate"]["reasons"]
    rows = _read(wd)
    assert not any(r.get("unreviewed_accepted") for r in rows.values())
    assert all(not rows[i].get("rv") for i in never)


def test_tolerance_needs_draft(tmp_path, monkeypatch):
    """Câu chưa soát KHÔNG có bản nháp vi → không nhận (park như cũ)."""
    wd, _, res = _run_unrev(tmp_path, monkeypatch, 50, never={7}, drafts=False)
    assert res["status"] == "park"
    assert not _read(wd)[7].get("unreviewed_accepted")


def test_tolerance_zero_disables(tmp_path, monkeypatch):
    _, _, res = _run_unrev(tmp_path, monkeypatch, 50, never={7}, tol=0)
    assert res["status"] == "park"


def test_missing_pass_recovers_before_accept(tmp_path, monkeypatch):
    """GPT bỏ câu 20 ở review + review-retry nhưng trả ở lượt bổ sung → rv thật, KHÔNG unreviewed_accepted."""
    wd, fake, res = _run_unrev(tmp_path, monkeypatch, 120, never=(), until={20: 2})
    assert res["status"] == "done"
    r = _read(wd)[20]
    assert r["rv"] == 1 and not r.get("unreviewed_accepted")
    assert res["review"]["review_missing"] == {"n": 1, "ok": 1, "lots": 1}


# ───────────────────────── part / parent (server) ─────────────────────────

@pytest.fixture
def srv(tmp_path, monkeypatch):
    server = pytest.importorskip("server")
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    return server


def _mk(tmp_path, jid, data):
    d = tmp_path / jid
    d.mkdir(exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def _part_parked(tmp_path, jid, due):
    d = _mk(tmp_path, jid, {"stages": {}, "agent": {"held_at": "waiting_review", "simple_park": True,
                                                     "review_retries": 1, "review_retry_at": due}})
    (d / "script.json").write_text(json.dumps([{"i": 0, "vi": "x"}]), encoding="utf-8")
    return d


def test_parent_waits_and_park_ready_follows_part(srv, tmp_path, monkeypatch):
    P = "60892cc63cb7"
    _mk(tmp_path, P, {"stages": {"mux": {"status": "running"}},
                      "split_info": {"sub_job_ids": [f"{P}_p01", f"{P}_p02"]}})
    _part_parked(tmp_path, f"{P}_p01", time.time() + 999)
    _mk(tmp_path, f"{P}_p02", {"stages": {}})
    monkeypatch.setattr(srv, "_part_pipeline_complete", lambda wd: wd.name.endswith("_p02"))
    assert srv._split_wait_parts(P, tmp_path / P, [f"{P}_p01", f"{P}_p02"]) is True
    pm = json.loads((tmp_path / P / "manifest.json").read_text(encoding="utf-8"))
    assert pm["agent"]["held_at"] == "split_parts" and pm["agent"]["split_wait_parts"] == [f"{P}_p01"]
    assert pm["stages"]["mux"]["status"] == "pending"
    assert srv._queue_park_ready(P) is False                   # part chưa tới hạn → parent giữ ghế, chưa chạy
    _part_parked(tmp_path, f"{P}_p01", time.time() - 1)        # tới hạn retry
    assert srv._queue_park_ready(P) is True                    # → hàng đợi chạy lại parent → vòng part chạy p01
    srv._split_wait_clear(tmp_path / P)
    pm = json.loads((tmp_path / P / "manifest.json").read_text(encoding="utf-8"))
    assert "held_at" not in pm["agent"] and "split_wait_parts" not in pm["agent"]


def test_parent_wait_part_hold_blocks_until_cleared(srv, tmp_path, monkeypatch):
    P = "abc123"
    _mk(tmp_path, P, {"stages": {}, "agent": {"held_at": "split_parts", "split_wait_parts": [f"{P}_p01"]}})
    _mk(tmp_path, f"{P}_p01", {"stages": {}, "agent": {"held_at": "review_failed", "hold_after_translate": True}})
    monkeypatch.setattr(srv, "_part_pipeline_complete", lambda wd: False)
    assert srv._queue_park_ready(P) is False
    _mk(tmp_path, f"{P}_p01", {"stages": {}, "agent": {}})       # người gỡ HOLD
    assert srv._queue_park_ready(P) is True


def test_split_wait_no_park_keeps_old_path(srv, tmp_path, monkeypatch):
    P = "x1"
    _mk(tmp_path, P, {"stages": {}})
    _mk(tmp_path, f"{P}_p01", {"stages": {}})
    monkeypatch.setattr(srv, "_part_pipeline_complete", lambda wd: False)
    assert srv._split_wait_parts(P, tmp_path / P, [f"{P}_p01"]) is False
    assert not (json.loads((tmp_path / P / "manifest.json").read_text(encoding="utf-8")).get("agent") or {})


def test_auto_split_loop_part_park_no_merge_parent_held(srv, tmp_path, monkeypatch):
    """Vòng «Part n/N»: p01 park, p02 xong → KHÔNG gọi merge, parent held_at=split_parts (queue → «held»)."""
    import engines.split_parts as split_parts
    P = "60892cc63cb7"
    subs = [f"{P}_p01", f"{P}_p02"]
    _mk(tmp_path, P, {"stages": {}, "split_info": {"status": "split_done", "sub_job_ids": subs}})
    _mk(tmp_path, subs[0], {"stages": {}})
    _mk(tmp_path, subs[1], {"stages": {}})
    monkeypatch.setattr(srv, "_find_video", lambda wd: tmp_path / P / "manifest.json")
    monkeypatch.setattr(split_parts, "probe_duration", lambda v: 5.5 * 3600)
    merged = []
    monkeypatch.setattr(split_parts, "merge_job_parts", lambda *a, **k: merged.append(a) or {"ok": False})
    done = set()
    monkeypatch.setattr(srv, "_part_pipeline_complete", lambda wd: wd.name in done)
    ran = []

    def fake_gpu(s_id, *a, **k):
        ran.append(s_id)
        if s_id.endswith("_p01"):
            _part_parked(tmp_path, s_id, time.time() + 300)    # dịch simple park
        else:
            done.add(s_id)
    monkeypatch.setattr(srv, "_auto_gpu", fake_gpu)
    assert srv._check_and_run_auto_split_parts(P, "Tiếng Việt", "duck", 0.2, 0.22, False) is True
    assert ran == subs and merged == []
    ag = json.loads((tmp_path / P / "manifest.json").read_text(encoding="utf-8"))["agent"]
    assert ag["held_at"] == "split_parts" and ag["split_wait_parts"] == [subs[0]]
    # lần chạy lại (part tới hạn, giả sử dịch qua): chỉ chạy p01, gỡ dấu chờ, rồi ghép
    ran.clear()

    def fake_gpu2(s_id, *a, **k):
        ran.append(s_id)
        done.add(s_id)
    monkeypatch.setattr(srv, "_auto_gpu", fake_gpu2)
    assert srv._check_and_run_auto_split_parts(P, "Tiếng Việt", "duck", 0.2, 0.22, False) is True
    ag = json.loads((tmp_path / P / "manifest.json").read_text(encoding="utf-8")).get("agent") or {}
    assert ag.get("held_at") is None and "split_wait_parts" not in ag


def test_background_merge_clears_parent_wait(srv, tmp_path, monkeypatch):
    """Part chạy riêng xong → ghép Full nền OK → parent hết held_at=split_parts."""
    import engines.split_parts as split_parts
    P = "pp1"
    subs = [f"{P}_p01", f"{P}_p02"]
    _mk(tmp_path, P, {"stages": {}, "split_info": {"sub_job_ids": subs},
                      "agent": {"held_at": "split_parts", "split_wait_parts": [subs[0]], "hold_reason": "chờ"}})
    monkeypatch.setattr(srv, "_part_pipeline_complete", lambda wd: True)
    monkeypatch.setattr(split_parts, "merge_job_parts", lambda *a, **k: {"ok": True, "output_path": "x", "size_mb": 1})

    class Now:
        def submit(self, fn):
            fn()
    monkeypatch.setattr(srv, "_misc", Now())
    assert srv._queue_split_parent_merge_if_ready(P, reason="t")["queued"] is True
    ag = json.loads((tmp_path / P / "manifest.json").read_text(encoding="utf-8")).get("agent") or {}
    assert "held_at" not in ag and "split_wait_parts" not in ag
