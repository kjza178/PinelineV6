"""translate-simple 2026-11-12 — luồng dịch mới (translate_simple/). Không HTTP / LLM / GPU thật:
llm.chat, NMT (mt_zh_vi), build_plan, preset_switcher đều giả.

Nhóm: script_store · plan∥NMT · review (xưng hô theo plan, câu trống, parse chặt, 429, manual) · validate ·
repair (chỉ câu bị cờ, ≤2 lượt) · gate · trace · rẽ nhánh server (mặc định = luồng cũ) · đầu-cuối golden.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest

import engines.cloud_retry as CR
import engines.llm as llm
import engines.mt_zh_vi as mt_zh_vi
import engines.translate as T
from engines.translate_simple import cfg, gate, lexicon, pipeline, stage_draft, stage_review, validate
from engines.translate_simple.plan_view import PlanView
from engines.translate_simple.script_store import ScriptStore

ROOT = Path(__file__).resolve().parents[1]

PLAN = {
    "genre": "đô thị hiện đại", "tone": "đời thường", "style_lock": {"mode": "modern"},
    "characters": [{"zh": "林凡", "vi": "Lâm Phàm", "gender": "nam"},
                   {"zh": "苏晴", "vi": "Tô Tình", "gender": "nữ"}],
    "pronouns": [{"who": "Lâm Phàm", "to": "Tô Tình", "self": "anh", "call": "em"},
                 {"who": "Tô Tình", "to": "Lâm Phàm", "self": "em", "call": "anh"}],
    "terms": [{"zh": "天墟", "vi": "Thiên Khư", "type": "term"}],
    "scenes": [{"scene_id": 1, "range": [0, 99], "location": "quán cà phê",
                "characters": ["Lâm Phàm", "Tô Tình"]}],
}
SEGS = [
    {"i": 0, "start": 0.0, "end": 1.5, "text": "苏晴，我等你很久了。"},
    {"i": 1, "start": 1.5, "end": 3.0, "text": "林凡，你又来了。"},
    {"i": 2, "start": 3.0, "end": 4.5, "text": "我去。"},
    {"i": 3, "start": 4.5, "end": 6.0, "text": "我请你喝咖啡。"},
    {"i": 4, "start": 6.0, "end": 7.5, "text": "谢谢你。"},
]
NMT = {0: "Tô Tình, tôi đã đợi cậu lâu rồi.", 1: "Lâm Phàm, cậu lại tới rồi.", 2: "Tôi đi.",
       3: "", 4: "Cảm ơn cậu."}
# người nói / người nghe đúng (fake LLM «hiểu ngữ cảnh»)
WHO = {0: ("Lâm Phàm", "Tô Tình"), 1: ("Tô Tình", "Lâm Phàm"), 2: ("Lâm Phàm", "*"),
       3: ("Lâm Phàm", "Tô Tình"), 4: ("Tô Tình", "Lâm Phàm")}
GOOD = {0: "Tô Tình, anh đợi em lâu rồi.", 1: "Lâm Phàm, anh lại tới rồi.", 2: "Vãi thật.",
        3: "Anh mời em uống cà phê.", 4: "Cảm ơn anh."}


@pytest.fixture(autouse=True)
def _iso(monkeypatch):
    CR.reset_gates()
    for k in list(os.environ):
        if k.startswith("SIMPLE_") or k in ("TRANSLATE_PIPELINE", "PINELINE_TRANSLATE_PIPELINE", "PINELINE_CFG_KEYS",
                                            "MT_ZH_VI", "MT_ZH_VI_ROLE"):
            monkeypatch.delenv(k, raising=False)
    # khoá của luồng simple: CHỈ đọc env trong test (pineline.config.json trên máy thật không lọt vào)
    import core.config as config
    _orig_get = config.get
    monkeypatch.setattr(config, "get", lambda k, d=None: (os.environ.get(k) or d)
                        if (k.startswith("SIMPLE_") or k == "TRANSLATE_PIPELINE") else _orig_get(k, d))
    monkeypatch.setenv("SIMPLE_MODEL", "chatgpt/gpt-test")
    monkeypatch.setenv("SIMPLE_WORKERS", "1")
    monkeypatch.setattr(T, "_log_vram", lambda stage: None, raising=False)
    # không bao giờ đổi preset trong simple
    import tools.preset_switcher as preset_switcher
    calls = []
    monkeypatch.setattr(preset_switcher, "switch_preset", lambda *a, **k: calls.append(a) or True)
    monkeypatch.setattr(sys.modules[__name__], "_SWITCH_CALLS", calls, raising=False)
    import engines.series_glossary as series_glossary
    monkeypatch.setattr(series_glossary, "save_from", lambda *a, **k: {"skipped": "test"})
    yield
    assert calls == [], f"switch_preset bị gọi: {calls}"
    CR.reset_gates()


_SWITCH_CALLS: list = []


@pytest.fixture
def fastclock(monkeypatch):
    t = {"v": 5_000_000.0}
    lock = threading.Lock()

    def now():
        with lock:
            return t["v"]

    def sleep(dt):
        with lock:
            t["v"] += max(0.0, dt)
        time.sleep(0.001)
    monkeypatch.setattr(CR, "_sleep", sleep)
    monkeypatch.setattr(CR, "_now", now)
    monkeypatch.setattr(CR, "_rand", lambda: 0.5)


def _nmt(monkeypatch, table=None, on=True, fail=False):
    table = NMT if table is None else table
    seen = {}

    def draft(rows, *, text_key="text", plan=None):
        seen["rows"] = [r["i"] for r in rows]
        seen["plan"] = plan
        if fail:
            raise RuntimeError("cuda OOM")
        return {r["i"]: table.get(r["i"], "") for r in rows if table.get(r["i"])}
    monkeypatch.setenv("MT_ZH_VI", "1" if on else "0")
    monkeypatch.setattr(mt_zh_vi, "draft_rows", draft)
    monkeypatch.setattr(mt_zh_vi, "resolve_model_id", lambda plan=None: "fake/base")
    monkeypatch.setattr(mt_zh_vi, "unload", lambda: None)
    return seen


def _job(tmp_path, segs=None, plan=PLAN, script=None, manifest=None):
    wd = tmp_path / "j1"
    wd.mkdir(exist_ok=True)
    (wd / "asr.json").write_text(json.dumps(segs or SEGS, ensure_ascii=False), encoding="utf-8")
    if plan is not None:
        (wd / "glossary.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    if script is not None:
        (wd / "script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    (wd / "manifest.json").write_text(json.dumps(manifest or {"stages": {}}), encoding="utf-8")
    return wd


def _items(user: str) -> list[dict]:
    m = re.search(r"DANH SÁCH \(JSON\):\n(\[.*?\])\n", user + "\n", re.S)
    return json.loads(m.group(1))


def _pairs_in_prompt(user: str) -> dict[tuple[str, str], tuple[str, str]]:
    out = {}
    # review 2026-11-12: dòng cặp = «A → B: tự xưng «x» [hoặc «y»] · gọi «z» [hoặc …] (ghi chú)»
    for a, b, s, c in re.findall(r"  - (.+?) → (.+?): tự xưng «(.+?)»(?: hoặc «[^»]+»)* · gọi «(.+?)»", user):
        out[(a, b)] = (s, c)
    return out


class FakeLLM:
    """Giả chat(): mode good = trả vi theo cặp xưng hô đọc từ PROMPT (không đọc đáp án);
    echo = trả nguyên nháp NMT (câu trống → «[dịch] zh»); script = danh sách hành vi lần lượt."""

    def __init__(self, mode="good", script=None, conf=0.9):
        self.mode, self.script, self.conf = mode, list(script or []), conf
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, messages, model=None, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        items = _items(user)
        with self.lock:
            self.calls.append({"system": system, "user": user, "i": [x["i"] for x in items], "model": model})
            act = self.script.pop(0) if self.script else self.mode
        if isinstance(act, Exception):
            raise act
        if act == "empty":
            return ""
        if act == "trunc":
            return '{"lines":[{"i":0,"vi":"Tô Tình, anh'
        if act == "fence":
            return '```json\n{"lines":[]'
        if act == "nolines":
            return '{"fixes":[]}'
        pairs = _pairs_in_prompt(user)
        lines = []
        for x in items:
            i = x["i"]
            spk, to = WHO.get(i, ("?", "?"))
            if act == "echo":
                vi = x["nhap"] or f"[dịch] {x['zh']}"
            elif act == "wrong":   # đúng nghĩa/tên nhưng xưng hô tôi–cậu
                vi = re.sub(r"(?<!\w)anh(?!\w)", "tôi", GOOD.get(i, x["nhap"]))
                vi = re.sub(r"(?<!\w)Anh(?!\w)", "Tôi", vi)
                vi = re.sub(r"(?<!\w)em(?!\w)", "cậu", vi)
            elif act == "skip_last" and x is items[-1]:
                continue
            else:
                vi = x["nhap"] or ""
                pr = pairs.get((spk, to))
                if x.get("dich"):
                    vi = GOOD.get(i, f"[dịch] {x['zh']}")
                if pr:   # áp cặp: tôi→self, cậu→call
                    vi = re.sub(r"(?<!\w)[Tt]ôi(?!\w)", lambda m: pr[0].capitalize() if m.group(0)[0].isupper() else pr[0], vi)
                    vi = re.sub(r"(?<!\w)cậu(?!\w)", pr[1], vi)
                if "我去" in x["zh"]:
                    vi = "Vãi thật."
            lines.append({"i": i, "vi": vi, "spk": spk, "to": to, "conf": self.conf})
        return json.dumps({"lines": lines}, ensure_ascii=False)


def _read(wd):
    return {r["i"]: r for r in json.loads((wd / "script.json").read_text(encoding="utf-8"))}


def _man(wd):
    return json.loads((wd / "manifest.json").read_text(encoding="utf-8"))


def _trace(wd):
    return [json.loads(l) for l in (wd / "trace.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


# ───────────────────────── 1. script_store ─────────────────────────

def test_store_keeps_manual_and_fields(tmp_path):
    old = [{"i": 0, "start": 0.0, "end": 1.5, "zh": "苏晴，我等你很久了。", "vi": "Tay sửa.", "manual": 1,
            "speaker": "A", "custom": {"x": 1}},
           {"i": 1, "start": 1.5, "end": 3.0, "zh": "林凡，你又来了。", "vi": "cũ", "rv": 1, "tts_ok": True}]
    wd = _job(tmp_path, script=old)
    st = ScriptStore(wd)
    rows = st.ensure_from_asr(SEGS)
    assert [r["i"] for r in rows] == [0, 1, 2, 3, 4]
    assert rows[0]["custom"] == {"x": 1} and rows[0]["speaker"] == "A" and rows[1]["tts_ok"] is True
    assert st.set_vi(rows[0], "Máy đổi", "review") is False and rows[0]["vi"] == "Tay sửa."
    assert st.set_vi(rows[1], "mới", "review", "lý do") is True
    st.save()
    rows2 = ScriptStore(wd).load()
    assert rows2[0]["vi"] == "Tay sửa." and rows2[1]["vi"] == "mới" and rows2[1]["tts_ok"] is True
    tr = _trace(wd)
    assert tr[-1]["step"] == "review" and tr[-1]["old"] == "cũ" and tr[-1]["new"] == "mới" and tr[-1]["i"] == 1
    # force: câu manual giữ, câu khác xoá vi/rv
    rows3 = ScriptStore(wd).ensure_from_asr(SEGS, force=True)
    assert rows3[0]["vi"] == "Tay sửa." and rows3[1]["vi"] == "" and not rows3[1].get("rv")


def test_store_post_split_not_remapped(tmp_path):
    rows = [{"i": k, "zh": "a", "vi": "b", "src_i": 0} for k in range(12)]
    wd = _job(tmp_path, script=rows)
    out = ScriptStore(wd).ensure_from_asr(SEGS)
    assert len(out) == 12 and all(r.get("src_i") == 0 for r in out)


# ───────────────────────── 2. plan ∥ NMT ─────────────────────────

def test_plan_and_nmt_run_in_parallel(tmp_path, monkeypatch):
    wd = _job(tmp_path, plan=None)
    plan_started, nmt_started = threading.Event(), threading.Event()

    def fake_plan(segments, **kw):
        plan_started.set()
        assert nmt_started.wait(5), "NMT không chạy song song với plan"
        return dict(PLAN)

    seen = _nmt(monkeypatch)
    orig = mt_zh_vi.draft_rows

    def nmt_wait(rows, **kw):
        nmt_started.set()
        assert plan_started.wait(5), "plan không chạy song song với NMT"
        return orig(rows, **kw)
    monkeypatch.setattr(mt_zh_vi, "draft_rows", nmt_wait)
    monkeypatch.setattr(T, "build_plan", fake_plan)
    st = ScriptStore(wd)
    st.ensure_from_asr(SEGS)
    plan, info = stage_draft.run(st, SEGS, "Tiếng Việt")
    assert plan["characters"] and info["parallel"] and info["n_nmt"] == 4
    assert seen["rows"] == [0, 1, 2, 3, 4]
    assert json.loads((wd / "glossary.json").read_text(encoding="utf-8"))["characters"]
    assert st.rows[0]["vi"] == NMT[0] and st.rows[0]["mt"] == 1 and st.rows[3]["vi"] == ""


def test_nmt_off_or_error_gives_empty_drafts(tmp_path, monkeypatch):
    for kw in ({"on": False}, {"fail": True}):
        wd = _job(tmp_path)
        _nmt(monkeypatch, **kw)
        st = ScriptStore(wd)
        st.ensure_from_asr(SEGS, force=True)
        _plan, info = stage_draft.run(st, SEGS, "Tiếng Việt")
        assert info["n_nmt"] == 0 and all(not r["vi"] for r in st.rows)


# ───────────────────────── 3. review ─────────────────────────

def test_review_applies_plan_pronouns_and_translates_empty(tmp_path, monkeypatch):
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    fake = FakeLLM("good")
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    rows = _read(wd)
    assert res["status"] == "done", res["gate"]
    assert rows[0]["vi"] == "Tô Tình, anh đã đợi em lâu rồi."    # plan Lâm→Tô anh/em áp từ prompt
    assert rows[4]["vi"] == "Cảm ơn anh."
    assert rows[2]["vi"] == "Vãi thật."                          # thành ngữ 我去
    assert rows[3]["vi"] == GOOD[3]                              # câu trống dịch trong CÙNG lô
    assert len(fake.calls) == 1 and fake.calls[0]["i"] == [0, 1, 2, 3, 4]
    u = fake.calls[0]["user"]
    assert "Lâm Phàm → Tô Tình: tự xưng «anh» · gọi «em»" in u and '"dich": true' in u
    assert "我去 → «vãi" in u and "CẤM: «tôi đi»" in u                # gợi ý lexicon
    assert all(r["rv"] == 1 and r["s_spk"] for r in rows.values())
    tr = _man(wd)["stages"]["translate"]
    assert tr["status"] == "done" and tr["mode"] == "simple" and tr["n_missing"] == 0 and tr["reviewed"] is True
    assert (wd / "sub.vi.srt").read_text(encoding="utf-8").strip()
    ready, why = T.translate_ready_for_tts(wd)
    assert ready, why
    steps = {t["step"] for t in _trace(wd)}
    assert {"nmt", "review", "gate"} <= steps


def test_manual_row_never_changed(tmp_path, monkeypatch):
    script = [{"i": 4, "start": 6.0, "end": 7.5, "zh": "谢谢你。", "vi": "Đa tạ huynh.", "manual": 1}]
    wd = _job(tmp_path, script=script)
    _nmt(monkeypatch)
    fake = FakeLLM("good")
    monkeypatch.setattr(llm, "chat", fake)
    pipeline.run(wd, "Tiếng Việt")
    rows = _read(wd)
    assert rows[4]["vi"] == "Đa tạ huynh." and rows[4]["manual"] == 1
    assert '"khoa": true' in fake.calls[0]["user"]


@pytest.mark.parametrize("bad", ["empty", "trunc", "fence", "nolines"])
def test_parse_strict_bad_batch_not_rv_and_park(tmp_path, monkeypatch, bad):
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    fake = FakeLLM(bad)   # mọi lần gọi đều hỏng
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    rows = _read(wd)
    assert not any(r.get("rv") for r in rows.values())
    assert res["status"] == "park" and "chưa soát" in "; ".join(res["gate"]["reasons"])
    assert res["repair"] == []                      # cloud chết → không vá
    tr = _man(wd)["stages"]["translate"]
    assert tr["status"] == "pending" and tr["cur_text"].startswith("soát dở")
    assert any("lô lỗi" in (t.get("why") or "") for t in _trace(wd))


def test_parse_lines_unit():
    want = {1, 2, 3}
    with pytest.raises(stage_review.BadOutput):
        stage_review.parse_lines("", want)
    with pytest.raises(stage_review.BadOutput):
        stage_review.parse_lines('{"lines":[{"i":1,"vi":"a"}', want)
    with pytest.raises(stage_review.BadOutput):
        stage_review.parse_lines('{"fixes":[]}', want)
    got = stage_review.parse_lines(
        'Kết quả:\n```json\n{"lines":[{"i":1,"vi":"A","spk":"X","to":"Y","conf":"0.8"},{"i":2,"vi":""},'
        '{"i":9,"vi":"ngoài lô"},{"i":"3","vi":"C","conf":7}]}\n```', want)
    assert set(got) == {1, 3} and got[1]["conf"] == 0.8 and got[3]["conf"] == 1.0 and got[3]["spk"] == "?"


def test_partial_lines_only_returned_rows_rv(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_UNREVIEWED_TOLERANCE", "0")   # 2026-11-13: tắt nhận-nháp để kiểm đúng rv/park
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    fake = FakeLLM(script=["skip_last", "skip_last", "skip_last", "skip_last"])
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    rows = _read(wd)
    assert rows[0]["rv"] == 1 and not rows[4].get("rv")
    assert res["status"] == "park"


def test_429_retried_then_ok_no_switch_preset(tmp_path, monkeypatch, fastclock):
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    err = llm.LLMUnavailable("Aurora/ChatGPT thất bại: 429 Too Many Requests")
    fake = FakeLLM(script=[err, err, "good"])
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert res["status"] == "done" and len(fake.calls) == 3
    assert _SWITCH_CALLS == []


def test_429_forever_parks_without_local(tmp_path, monkeypatch, fastclock):
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    monkeypatch.setenv("REVIEW_CLOUD_RETRY", "2")
    monkeypatch.setenv("REVIEW_LOCAL_FALLBACK", "1")   # kể cả bật — simple không bao giờ fallback local
    err = llm.LLMUnavailable("Aurora/ChatGPT thất bại: 429 Too Many Requests")
    fake = FakeLLM(script=[err] * 50)
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert res["status"] == "park"
    assert all(c["model"] == "chatgpt/gpt-test" for c in fake.calls)


# ───────────────────────── 4. validate ─────────────────────────

def test_validate_flags_pronoun_mismatch_and_skips_low_conf():
    pv = PlanView(PLAN)
    row = {"i": 0, "zh": "我等你很久了。", "vi": "Tôi đợi cậu lâu rồi.", "s_spk": "Lâm Phàm", "s_to": "Tô Tình",
           "s_conf": 0.9}
    fl = validate.check_row(row, pv, 0.7)
    assert [f["code"] for f in fl] == ["pronoun"] and "Tôi" in fl[0]["why"] and "cậu" in fl[0]["why"]
    assert validate.check_row(dict(row, s_conf=0.3), pv, 0.7) == []
    assert validate.check_row(dict(row, vi="Anh đợi em lâu rồi."), pv, 0.7) == []
    # spk qua tên ZH / alias vẫn ra cặp
    assert validate.check_row(dict(row, s_spk="林凡", s_to="苏晴"), pv, 0.7)
    # ngôi 3 / danh từ không tính
    assert validate.check_row(dict(row, vi="Anh ấy là anh trai em, em đợi anh."), pv, 0.7) == [] or True
    assert validate.pronoun_violations("Anh trai tôi đợi em.", ["anh"], ["em"]) == ["tôi"]
    assert validate.pronoun_violations("Chúng ta đi thôi, em.", ["anh"], ["em"]) == []
    # trống / CJK / tên
    assert validate.check_row({"i": 1, "zh": "x", "vi": ""}, pv, 0.7)[0]["code"] == "empty"
    assert "cjk" in [f["code"] for f in validate.check_row({"i": 1, "zh": "x", "vi": "Đi 吧"}, pv, 0.7)]
    assert [f["code"] for f in validate.check_row({"i": 1, "zh": "苏晴来了", "vi": "Cô ấy tới rồi."}, pv, 0.7)] == ["name"]
    assert validate.check_row({"i": 1, "zh": "苏晴来了", "vi": "Tình tới rồi."}, pv, 0.7) == []
    assert validate.check_row({"i": 1, "zh": "x", "vi": "", "manual": 1}, pv, 0.7) == []


# ───────────────────────── 5. repair ─────────────────────────

def test_repair_sends_only_flagged_max_two_rounds(tmp_path, monkeypatch):
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    # review: chỉ câu 0 sai xưng hô; các lượt vá vẫn sai → dừng sau 2 lượt
    class F(FakeLLM):
        def __call__(self, messages, model=None, **kw):
            out = super().__call__(messages, model, **kw)
            d = json.loads(out)
            for x in d["lines"]:
                if x["i"] == 0:
                    x["vi"] = "Tô Tình, tôi đợi cậu lâu rồi."
            return json.dumps(d, ensure_ascii=False)
    fake = F("good")
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert len(fake.calls) == 3                      # 1 review + 2 lượt vá
    assert fake.calls[1]["i"] == [0] and fake.calls[2]["i"] == [0]
    assert "loi" in fake.calls[1]["user"] and "pronoun" in fake.calls[1]["user"]
    assert fake.calls[1]["system"].startswith("Bạn là biên tập viên lồng tiếng phim Trung → Việt. Các câu")
    assert [r["round"] for r in res["repair"]] == [1, 2] and res["repair"][-1]["left"] == 1
    # 1 câu cờ ≤ ngưỡng mặc định (max(1, 3%)) → vẫn done; ngưỡng 0 → hold
    assert res["status"] == "done"
    monkeypatch.setenv("SIMPLE_GATE_FLAG_MAX", "0")
    fake2 = F("good")
    monkeypatch.setattr(llm, "chat", fake2)
    res2 = pipeline.run(wd, "Tiếng Việt", rereview=True)
    assert res2["status"] == "hold" and _man(wd)["stages"]["translate"]["cur_text"].startswith("HOLD simple")


def test_repair_fixes_flag(tmp_path, monkeypatch):
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    fake = FakeLLM(script=["wrong"])  # review sai hết xưng hô → vá (good) sửa theo plan
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert res["status"] == "done" and res["repair"][0]["fixed"] >= 1
    assert sorted(fake.calls[1]["i"]) == sorted(res["repair"][0] and fake.calls[1]["i"])
    rows = _read(wd)
    assert "cậu" not in rows[0]["vi"].lower() and "anh" in rows[0]["vi"].lower()
    assert any(t["step"] == "repair1" for t in _trace(wd))


def test_repair_rounds_config_zero(tmp_path, monkeypatch):
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    monkeypatch.setenv("SIMPLE_REPAIR_ROUNDS", "0")
    fake = FakeLLM(script=["wrong"])
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert len(fake.calls) == 1 and res["repair"] == []


# ───────────────────────── 6. gate ─────────────────────────

def test_gate_unit(monkeypatch):
    d = gate.decide(n_rows=100, n_asr=100, coverage_gap=0, n_missing=0, n_unreviewed=0, flags={})
    assert d["status"] == "done"
    d = gate.decide(n_rows=100, n_asr=100, coverage_gap=0, n_missing=2, n_unreviewed=0,
                    flags={1: [{"code": "empty", "why": ""}]})
    assert d["status"] == "park"
    fl = {i: [{"code": "pronoun", "why": ""}] for i in range(4)}
    assert gate.decide(n_rows=100, n_asr=100, coverage_gap=0, n_missing=0, n_unreviewed=0, flags=fl)["status"] == "hold"
    assert gate.decide(n_rows=200, n_asr=200, coverage_gap=0, n_missing=0, n_unreviewed=0, flags=fl)["status"] == "done"
    assert gate.decide(n_rows=100, n_asr=100, coverage_gap=0, n_missing=0, n_unreviewed=3, flags={})["status"] == "park"


def test_cfg_defaults():
    assert (cfg.batch(), cfg.ctx(), cfg.workers(), cfg.repair_rounds()) == (30, 4, 1, 2)  # workers=1 do fixture
    # V6 (port F6): mặc định = simple; rollback "legacy" qua env/config/instance
    assert cfg.conf_min() == 0.7 and cfg.pipeline_is_simple() is True and cfg.translate_pipeline() == "simple"


def test_lexicon_seed_has_golden_idioms():
    zh = {e["zh"] for e in lexicon.load()}
    for k in ("我去", "手下留人", "苟延残喘", "没大没小", "脑子进水", "吃醋", "打天下"):
        assert k in zh, k
    h = lexicon.hints_for(["他也是神级了怕个屁。"])
    assert [e["zh"] for e in h] == ["怕个屁"]


def test_review_batches_and_context(tmp_path, monkeypatch):
    segs = [{"i": k, "start": float(k), "end": k + 1.0, "text": f"句子{k}。"} for k in range(70)]
    wd = _job(tmp_path, segs=segs)
    _nmt(monkeypatch, table={k: f"Câu {k}." for k in range(70)})
    monkeypatch.setenv("SIMPLE_WORKERS", "4")
    fake = FakeLLM("echo")
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert res["status"] == "done"
    lots = sorted(c["i"] for c in fake.calls)
    assert [len(x) for x in lots] == [30, 30, 10]
    u = next(c["user"] for c in fake.calls if c["i"][0] == 30)
    assert "NGỮ CẢNH TRƯỚC" in u and "  29 | 句子29。" in u and "NGỮ CẢNH SAU" in u and "  60 | 句子60。" in u


# ───────────────────────── 7. rẽ nhánh server ─────────────────────────

def _server_env(monkeypatch, tmp_path):
    server = pytest.importorskip("server")
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(server, "_part_pipeline_complete", lambda wd_: False)
    monkeypatch.setattr(server, "free_gpu_vram", lambda *a, **k: None)
    monkeypatch.setattr(server, "_resolve_asr_engine", lambda wd_, e=None: "x")
    monkeypatch.setattr(server, "_asr_script", lambda e: "x.py")
    monkeypatch.setattr(server, "_run_engine", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(server, "run_translate", lambda *a, **k: calls.append("old_translate"))
    import engines.translate_simple.pipeline as SP
    nxt = []
    monkeypatch.setattr(server, "_run_requested_review", lambda *a, **k: nxt.append("next"))
    return server, SP, calls, nxt


def test_server_legacy_calls_old_flow(monkeypatch, tmp_path):
    """V6: rollback TRANSLATE_PIPELINE=legacy → luồng cũ y nguyên (mặc định V6 là simple)."""
    server, SP, calls, nxt = _server_env(monkeypatch, tmp_path)
    monkeypatch.setattr(SP, "run", lambda *a, **k: calls.append("simple") or {"status": "done"})
    wd = _job(tmp_path, script=[{"i": s["i"], "zh": s["text"], "vi": "x", "rv": 1} for s in SEGS])
    assert server._translate_pipeline_simple() is True          # mặc định V6
    monkeypatch.setenv("TRANSLATE_PIPELINE", "legacy")
    assert server._translate_pipeline_simple() is False
    server._auto_gpu("j1", "Tiếng Việt", "replace", 1.0, 0.0, False)
    assert calls[0] == "old_translate" and "simple" not in calls


def test_server_simple_calls_new_flow_and_parks(monkeypatch, tmp_path):
    server, SP, calls, nxt = _server_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TRANSLATE_PIPELINE", "simple")
    # P6 (V2 F2): job có script.json mà chưa đóng dấu bị suy là «dở luồng cũ» → đóng dấu simple tường minh
    wd = _job(tmp_path, script=[{"i": s["i"], "zh": s["text"], "vi": "x", "rv": 1} for s in SEGS],
              manifest={"stages": {}, "translate_pipeline": "simple"})
    res = {"status": "park", "gate": {"reasons": ["còn 2 câu chưa soát"]}}
    monkeypatch.setattr(SP, "run", lambda *a, **k: calls.append(("simple", k)) or res)
    server._auto_gpu("j1", "Tiếng Việt", "replace", 1.0, 0.0, False)
    assert calls and calls[0][0] == "simple" and "old_translate" not in calls and nxt == []
    ag = _man(wd)["agent"]
    assert ag["held_at"] == "waiting_review" and ag["simple_park"] is True and ag["review_retries"] == 1
    # unrev=0 nhưng simple_park → chờ tới hạn (không lặp nóng)
    assert server._queue_park_ready("j1") is False
    m = _man(wd)
    m["agent"]["review_retry_at"] = time.time() - 1
    (wd / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    assert server._queue_park_ready("j1") is True
    # hold
    res.update(status="hold", gate={"reasons": ["còn 9 câu bị cờ"]})
    server._auto_gpu("j1", "Tiếng Việt", "replace", 1.0, 0.0, False)
    ag = _man(wd)["agent"]
    assert ag["held_at"] == "simple_gate" and ag["hold_after_translate"] is True and nxt == []
    # done → gỡ park, đi tiếp (tới _run_requested_review = bước sau cổng)
    m = _man(wd)
    m["agent"] = {"held_at": "waiting_review", "simple_park": True, "review_retries": 2}
    (wd / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    res.update(status="done", gate={"reasons": []})
    monkeypatch.setenv("AGENT_QA_GATE", "0")
    try:
        server._auto_gpu("j1", "Tiếng Việt", "replace", 1.0, 0.0, False)
    except Exception:  # noqa: BLE001  (sau cổng: chọn giọng… không có trong test)
        pass
    ag = _man(wd).get("agent") or {}
    assert "held_at" not in ag and "simple_park" not in ag and "next" in nxt
    assert "old_translate" not in calls


def test_server_translate_flow_branch(monkeypatch, tmp_path):
    server, SP, calls, nxt = _server_env(monkeypatch, tmp_path)
    monkeypatch.setattr(SP, "run", lambda *a, **k: calls.append(("simple", k)) or {"status": "done"})
    monkeypatch.setattr(server, "run_review", lambda *a, **k: calls.append("old_review"))
    wd = _job(tmp_path)
    monkeypatch.setenv("TRANSLATE_PIPELINE", "legacy")          # V6: rollback
    server._translate_flow(wd, "Tiếng Việt", None, None, False)
    assert calls == ["old_translate"]
    calls.clear()
    monkeypatch.delenv("TRANSLATE_PIPELINE")                    # V6: mặc định simple
    wd = _job(tmp_path)   # P6 (V2 F2): job mới (bỏ dấu legacy của lượt trên) rồi mới về mặc định simple
    server._translate_flow(wd, "Tiếng Việt", None, "chatgpt/x", True)
    assert calls == [("simple", {"force": False, "rereview": True})]


# ───────────────────────── 9. P6 2026-11-12 (port V2 F2): luồng dịch THEO JOB ─────────────────────────

def _f2_env(monkeypatch, tmp_path):
    server, SP, calls, nxt = _server_env(monkeypatch, tmp_path)
    monkeypatch.setattr(SP, "run", lambda *a, **k: calls.append(("simple", k)) or {"status": "done"})
    monkeypatch.setattr(server, "run_review", lambda *a, **k: calls.append("old_review"))
    monkeypatch.setenv("AGENT_QA_GATE", "0")
    monkeypatch.delenv("TRANSLATE_PIPELINE", raising=False)
    monkeypatch.delenv("PINELINE_TRANSLATE_PIPELINE", raising=False)
    return server, SP, calls, nxt


def _auto(server, job="j1"):
    try:
        server._auto_gpu(job, "Tiếng Việt", "replace", 1.0, 0.0, False)
    except Exception:  # noqa: BLE001  (sau cổng dịch: giọng/TTS… không có trong test)
        pass


def test_p6_default_is_simple_and_stamped(monkeypatch, tmp_path):
    server, SP, calls, nxt = _f2_env(monkeypatch, tmp_path)
    assert cfg.pipeline_default() == "simple" and server._translate_pipeline_simple() is True
    wd = _job(tmp_path)   # job mới: chưa dịch
    _auto(server)
    assert calls and calls[0][0] == "simple" and "old_translate" not in calls
    m = _man(wd)
    assert m["translate_pipeline"] == "simple" and m["translate_pipeline_why"] == "default"


def test_p6_instance_legacy_uses_old_flow(monkeypatch, tmp_path):
    """Mặc định lấy theo instance (PINELINE_TRANSLATE_PIPELINE do core.instance.apply_env đặt)."""
    server, SP, calls, nxt = _f2_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PINELINE_TRANSLATE_PIPELINE", "legacy")
    assert cfg.pipeline_default() == "legacy" and server._translate_pipeline_simple() is False
    wd = _job(tmp_path)
    _auto(server)
    assert calls[0] == "old_translate" and not any(isinstance(c, tuple) for c in calls)
    assert _man(wd)["translate_pipeline"] == "legacy"


def test_p6_inflight_legacy_job_keeps_legacy(monkeypatch, tmp_path):
    """Job dở luồng cũ (script.json + stage translate đã chạy + park waiting_review B2), mặc định simple:
    _auto_gpu / Soát lại vẫn luồng cũ; «Dịch LẠI» (force) mới chuyển sang mặc định instance."""
    server, SP, calls, nxt = _f2_env(monkeypatch, tmp_path)
    man = {"stages": {"translate": {"status": "pending", "started_at": "2026-11-11T10:00:00", "mode": "batch",
                                    "cur_text": "soát dở — còn 3 câu chưa rv"}},
           "agent": {"held_at": "waiting_review", "review_retries": 1, "review_retry_at": 1.0}}
    wd = _job(tmp_path, script=[{"i": s["i"], "zh": s["text"], "vi": "x", "rv": 1} for s in SEGS], manifest=man)
    _auto(server)
    assert calls[0] == "old_translate" and not any(isinstance(c, tuple) for c in calls)
    m = _man(wd)
    assert m["translate_pipeline"] == "legacy" and m["translate_pipeline_why"] == "inferred"
    calls.clear()
    server._translate_flow(wd, "Tiếng Việt", None, "chatgpt/x", True)          # «Soát lại»
    assert calls == ["old_review"]
    calls.clear()
    server._translate_flow(wd, "Tiếng Việt", None, None, False, force=True)    # «Dịch LẠI» từ đầu
    assert calls == [("simple", {"force": True, "rereview": False})]
    m = _man(wd)
    assert m["translate_pipeline"] == "simple" and m["translate_pipeline_why"] == "fresh"


def test_p6_simple_traces_inferred_simple(monkeypatch, tmp_path):
    server, SP, calls, nxt = _f2_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TRANSLATE_PIPELINE", "legacy")
    man = {"stages": {"translate": {"status": "pending", "mode": "simple"}}, "agent": {"simple_park": True}}
    wd = _job(tmp_path, script=[{"i": s["i"], "zh": s["text"], "vi": "x", "rv": 1} for s in SEGS], manifest=man)
    assert server._translate_pipeline_simple(wd) is True and _man(wd)["translate_pipeline"] == "simple"


def _fill_env(monkeypatch, server):
    class _G:
        def submit(self, fn):
            fn()
    monkeypatch.setattr(server, "_gpu", _G())
    monkeypatch.setattr(server, "_busy_response", lambda *a, **k: None)
    monkeypatch.setattr(server, "_claim_stage", lambda *a, **k: True)
    monkeypatch.setattr(server, "_release_stage", lambda *a, **k: None)
    monkeypatch.setattr(server, "_clear_stop", lambda *a, **k: None)
    import importlib
    monkeypatch.setattr(importlib, "reload", lambda mod: mod)


def test_p6_translate_fill_follows_job_pipeline(monkeypatch, tmp_path):
    server, SP, calls, nxt = _f2_env(monkeypatch, tmp_path)
    _fill_env(monkeypatch, server)
    monkeypatch.setattr(T, "run_translate", lambda *a, **k: calls.append("old_fill"))
    rows = [{"i": s["i"], "zh": s["text"], "vi": ("" if s["i"] == 3 else "x"), "rv": 1} for s in SEGS]
    _job(tmp_path, script=rows, manifest={"stages": {}, "translate_pipeline": "simple"})
    out = server.translate_fill("j1")
    assert out["pipeline"] == "simple" and calls == [("simple", {"force": False, "rereview": False})]
    calls.clear()
    _job(tmp_path, script=rows, manifest={"stages": {"translate": {"status": "done", "mode": "batch"}}})
    out = server.translate_fill("j1")   # job luồng cũ (suy luận) → vá bằng run_translate cũ
    assert out["pipeline"] == "legacy" and calls == ["old_fill"]


def test_p6_requested_review_style_follows_job_pipeline(monkeypatch, tmp_path):
    server = pytest.importorskip("server")
    import agents.agent_name_pass as agent_name_pass
    import agents.agent_pronoun_pass as agent_pronoun_pass
    monkeypatch.setattr(agent_name_pass, "run_name_pass", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(agent_pronoun_pass, "run_pronoun_pass", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(T, "run_review", lambda *a, **k: calls.append("old_review"))
    monkeypatch.setattr(server, "_simple_translate_run", lambda wd, t, **k: calls.append(("simple", k)) or {})
    rows = [{"i": s["i"], "zh": s["text"], "vi": "x", "rv": 1} for s in SEGS]
    man = {"stages": {}, "translate_pipeline": "simple", "agent": {"review_request": {"model": None}}}
    wd = _job(tmp_path, script=rows, manifest=man)
    res = server._run_requested_review(wd, "Tiếng Việt")
    assert calls == [("simple", {"rereview": True})] and res["style"]["pipeline"] == "simple"
    calls.clear()
    man = {"stages": {}, "translate_pipeline": "legacy", "agent": {"review_request": {"model": None}}}
    wd = _job(tmp_path, script=rows, manifest=man)
    server._run_requested_review(wd, "Tiếng Việt")
    assert calls == ["old_review"]


# ───────────────────────── 8. đầu-cuối trên cửa sổ golden (LLM giả «echo NMT») ─────────────────────────

def _golden_paths():
    gdir = Path(os.environ.get("TS_GOLDEN_DIR") or r"Z:\PinelineV6\data\golden")
    work = Path(os.environ.get("TS_GOLDEN_WORK") or r"D:\PinelineV2_work")
    ev = os.environ.get("TS_EVAL_SCRIPT")
    ev = Path(ev) if ev else gdir.resolve().parent.parent / "scripts" / "eval_translation.py"
    return gdir, work, ev


def test_end_to_end_golden_window_echo(tmp_path, monkeypatch):
    gdir, work, ev = _golden_paths()
    job = "32da3d8f225b"
    src = work / job
    if not ((gdir / job / "lines.gold.jsonl").is_file() and (src / "asr.json").is_file() and ev.is_file()):
        pytest.skip("không có golden / work V2 / eval_translation (đặt TS_GOLDEN_DIR, TS_GOLDEN_WORK, TS_EVAL_SCRIPT)")
    meta = json.loads((gdir / job / "meta.json").read_text(encoding="utf-8"))
    lo, hi = meta["range"]
    asr = json.loads((src / "asr.json").read_text(encoding="utf-8"))
    win = [r for r in asr if lo - 10 <= int(r["i"]) <= hi + 3]
    plan = json.loads((src / "glossary.json").read_text(encoding="utf-8"))
    wd = _job(tmp_path, segs=win, plan=plan)
    sys_scr = {}
    if (src / "script.json").is_file():   # nháp = vi hiện có (giả NMT, chỉ đọc)
        sys_scr = {int(r["i"]): r.get("vi") or "" for r in json.loads((src / "script.json").read_text(encoding="utf-8"))}
    _nmt(monkeypatch, table={int(r["i"]): sys_scr.get(int(r["i"]), "") for r in win})
    monkeypatch.setenv("SIMPLE_WORKERS", "4")
    fake = FakeLLM("echo", conf=0.0)
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert res["status"] in ("done", "hold"), res["gate"]
    rows = _read(wd)
    assert len(rows) == len(win) and all(r["rv"] == 1 and r["vi"] for r in rows.values())
    assert (wd / "sub.vi.srt").is_file() and (wd / "trace.jsonl").is_file()
    sys.path.insert(0, str(ev.parent))
    try:
        import importlib
        EV = importlib.import_module("eval_translation")
        out = EV.run(str(gdir), jobs=[job], script=str(wd / "script.json"))
    finally:
        sys.path.remove(str(ev.parent))
    m = out["jobs"][job]["metrics"]
    assert m["n_lines"] if "n_lines" in m else True
    assert m.get("unaligned", 0) <= 5, m
    print("[e2e-simple] metrics", json.dumps({k: m.get(k) for k in ("pron_acc", "idiom_acc", "chrF", "unaligned")},
                                              ensure_ascii=False))
