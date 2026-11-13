"""translate-simple — kịch bản bổ sung của review độc lập 2026-11-12 (LLM / NMT / plan đều GIẢ).

Model bỏ sót câu · trả i lạ (ngoài lô / câu ngữ cảnh) · trả i trùng · thiếu conf · spk/to không có cặp trong plan
(đề xuất cặp → plan_suggestions.json, KHÔNG chèn plan/bảng bộ) · 429 kéo dài (8 lô song song, có giới hạn) ·
dừng giữa chừng + chạy lại · resume khi script đã rv một phần · nhiều tập liên tiếp dùng chung bảng bộ ·
lexicon theo ngữ cảnh (我去) · dòng cặp xưng hô rõ ràng · script.json hỏng · trace xoay vòng.
"""
from __future__ import annotations

import json

import pytest

import engines.cloud_retry as CR
import engines.llm as llm
import engines.series_glossary as series_glossary
import engines.translate as T
from tests.test_translate_simple import (  # noqa: F401  (fixture autouse _iso + fastclock dùng lại)
    GOOD, PLAN, SEGS, WHO, FakeLLM, _iso, _items, _job, _man, _nmt, _read, _trace, fastclock,
)
from engines.translate_simple import lexicon, pipeline, stage_review, validate
from engines.translate_simple.plan_view import PlanView

_ORIG_SAVE_FROM = series_glossary.save_from   # fixture _iso thay bằng no-op; test bảng bộ cần bản thật


def _seg(n):
    return [{"i": i, "start": i * 1.5, "end": i * 1.5 + 1.4, "text": f"林凡，你好{i}。"} for i in range(n)]


class Scripted(FakeLLM):
    """FakeLLM «good» + hậu xử lý JSON theo hàm post(d, items, call_no)."""

    def __init__(self, post, **kw):
        super().__init__("good", **kw)
        self.post = post

    def __call__(self, messages, model=None, **kw):
        out = super().__call__(messages, model, **kw)
        if not out.startswith("{"):
            return out
        d = json.loads(out)
        r = self.post(d, _items(messages[1]["content"]), len(self.calls))
        return r if isinstance(r, str) else json.dumps(d, ensure_ascii=False)


# ───────────────────────── model bỏ sót / i lạ / i trùng ─────────────────────────

def test_omitted_line_recovered_by_retry_pass(tmp_path, monkeypatch):
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    fake = FakeLLM(script=["skip_last", "good"])
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert res["status"] == "done", res["gate"]
    assert fake.calls[1]["i"] == [4]                         # lượt thử lại chỉ gửi câu bị bỏ sót
    assert any(t.get("i") == 4 and "không trả" in (t.get("why") or "") for t in _trace(wd))
    assert _read(wd)[4]["vi"] == GOOD[4]


def test_foreign_i_ignored_never_touches_context_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_BATCH", "4")                  # lô [0..3] + lô [4]
    monkeypatch.setenv("SIMPLE_UNREVIEWED_TOLERANCE", "0")   # 2026-11-13: tắt nhận-nháp để kiểm đúng rv/park
    wd = _job(tmp_path)
    _nmt(monkeypatch)

    def post(d, items, n):
        if items[0]["i"] == 0:                               # lô 1 trả thêm i=4 (câu NGỮ CẢNH) và i=99
            d["lines"] += [{"i": 4, "vi": "HACK", "spk": "?", "to": "?", "conf": 1},
                           {"i": 99, "vi": "lạ", "spk": "?", "to": "?", "conf": 1}]
            return d
        return ""                                            # lô [4] luôn hỏng
    monkeypatch.setattr(llm, "chat", Scripted(post))
    res = pipeline.run(wd, "Tiếng Việt")
    rows = _read(wd)
    assert rows[4]["vi"] != "HACK" and not rows[4].get("rv") and 99 not in rows
    assert all(rows[i]["rv"] == 1 for i in range(4))
    assert res["status"] == "park"


def test_duplicate_i_conflict_not_rv_then_retried(tmp_path, monkeypatch):
    wd = _job(tmp_path)
    _nmt(monkeypatch)

    def post(d, items, n):
        if n == 1:
            d["lines"].append(dict(d["lines"][1], vi="Bản khác hẳn."))   # i=1 trùng, vi khác
            d["lines"].append(dict(d["lines"][2]))                        # i=2 trùng y hệt → chấp nhận
        return d
    fake = Scripted(post)
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert fake.calls[1]["i"] == [1]                         # chỉ câu trùng-khác bị làm lại
    assert res["status"] == "done" and _read(wd)[1]["vi"] != "Bản khác hẳn."
    got = stage_review.parse_lines('{"lines":[{"i":1,"vi":"a"},{"i":1,"vi":"b"},{"i":2,"vi":"c"},{"i":2,"vi":"c"}]}',
                                   {1, 2})
    assert set(got) == {2}


def test_missing_conf_still_checked_by_validator(tmp_path, monkeypatch):
    wd = _job(tmp_path)
    _nmt(monkeypatch)

    def post(d, items, n):
        for x in d["lines"]:
            x.pop("conf", None)
            if x["i"] == 0:
                x["vi"] = "Tô Tình, tôi đợi cậu lâu rồi."    # sai cặp anh/em
        return d
    fake = Scripted(post)
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    rows = _read(wd)
    assert rows[1]["s_conf"] is None and rows[1]["rv"] == 1
    assert res["repair"] and fake.calls[1]["i"] == [0]       # thiếu conf vẫn bị bộ kiểm bắt
    assert res["status"] == "done"                           # 1 cờ ≤ ngưỡng


# ───────────────────────── spk/to không có cặp → đề xuất ─────────────────────────

PLAN3 = dict(PLAN, characters=PLAN["characters"] + [{"zh": "王婆", "vi": "Vương Bà", "gender": "nữ"}])


def test_unknown_pair_suggestions_written_not_inserted(tmp_path, monkeypatch):
    wd = _job(tmp_path, plan=PLAN3)
    _nmt(monkeypatch)
    pron_before = PLAN3["pronouns"]

    def post(d, items, n):
        for x in d["lines"]:
            if x["i"] in (1, 2):
                x.update(spk="Vương Bà", to="Lâm Phàm", conf=0.9)    # 2 nhân vật có trong plan, CHƯA có cặp
            if x["i"] == 3:
                x.update(spk="Ông chủ quán", to="Tô Tình", conf=0.9)  # tên không có trong plan
        d["new_pairs"] = [{"who": "Vương Bà", "to": "Lâm Phàm", "self": "bà", "call": "cậu", "i": 1},
                          {"who": "Lâm Phàm", "to": "Tô Tình", "self": "anh", "call": "em", "i": 0},  # đã có → bỏ
                          {"who": "", "to": "x"}, "rác"]
        return d
    monkeypatch.setattr(llm, "chat", Scripted(post))
    res = pipeline.run(wd, "Tiếng Việt")
    assert res["status"] == "done", res["gate"]
    sg = json.loads((wd / "plan_suggestions.json").read_text(encoding="utf-8"))
    assert [(p["who"], p["to"], p["self"], p["call"]) for p in sg["model_pairs"]] == \
        [("Vương Bà", "Lâm Phàm", ["bà"], ["cậu"])]
    assert [(p["who"], p["to"], p["n"]) for p in sg["missing_pairs"]] == [("Vương Bà", "Lâm Phàm", 2)]
    assert json.loads((wd / "glossary.json").read_text(encoding="utf-8"))["pronouns"] == pron_before  # KHÔNG chèn plan
    assert res["review"]["suggest"] == {"n_model": 1, "n_missing": 1}
    assert "new_pairs" in stage_review.SYSTEM_PROMPT
    # chạy lại (rereview) → model_pairs cộng dồn, không nhân bản
    monkeypatch.setattr(llm, "chat", Scripted(post))
    pipeline.run(wd, "Tiếng Việt", rereview=True)
    sg2 = json.loads((wd / "plan_suggestions.json").read_text(encoding="utf-8"))
    assert len(sg2["model_pairs"]) == 1 and sg2["model_pairs"][0]["n"] == 2


def test_suggest_off_by_config(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_SUGGEST_PAIRS", "0")
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    monkeypatch.setattr(llm, "chat", FakeLLM("good"))
    pipeline.run(wd, "Tiếng Việt")
    assert not (wd / "plan_suggestions.json").exists()      # plan đủ cặp + tắt đề xuất → không ghi


# ───────────────────────── 429 dài / dừng / resume ─────────────────────────

def test_long_429_eight_workers_bounded_and_parks(tmp_path, monkeypatch, fastclock):
    monkeypatch.setenv("SIMPLE_WORKERS", "8")
    monkeypatch.setenv("SIMPLE_BATCH", "4")
    monkeypatch.setenv("REVIEW_CLOUD_RETRY", "6")
    segs = _seg(32)                                          # 8 lô × 4 câu
    wd = _job(tmp_path, segs=segs)
    _nmt(monkeypatch, table={i: f"Lâm Phàm, xin chào {i}." for i in range(32)})
    err = llm.LLMUnavailable("Aurora/ChatGPT thất bại: 429 Too Many Requests")
    fake = FakeLLM(script=[err] * 10_000)
    monkeypatch.setattr(llm, "chat", fake)
    t0 = CR._now()
    res = pipeline.run(wd, "Tiếng Việt")
    waited = CR._now() - t0
    print(f"[429-long] calls={len(fake.calls)} waited_virtual={waited:.0f}s")
    assert res["status"] == "park" and res["review"]["n_ok"] == 0
    assert res["repair"] == []                               # cloud chết → không vá
    # có giới hạn: 8 lô × ≤(1+6) lần + lượt thử lại lô ½ (tuần tự) — không lặp vô hạn
    assert len(fake.calls) <= 8 * 7 + 16 * 7, len(fake.calls)
    budget = CR.outage_budget_sec(6, CR.backoff_schedule(None))
    assert waited <= 4 * budget + 60, (waited, budget)
    assert all(c["model"] == "chatgpt/gpt-test" for c in fake.calls)


def test_stop_mid_review_then_resume_only_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_BATCH", "4")
    wd = _job(tmp_path)
    _nmt(monkeypatch)

    def post(d, items, n):
        if n == 1:
            (wd / ".stop").write_text("1", encoding="utf-8")  # người bấm Dừng trong lúc lô 1 chạy
        return d
    fake = Scripted(post)
    monkeypatch.setattr(llm, "chat", fake)
    with pytest.raises(RuntimeError, match="stopped by user"):
        pipeline.run(wd, "Tiếng Việt")
    assert len(fake.calls) == 1                             # lô 2 không được gọi sau khi dừng
    rows = _read(wd)
    assert all(rows[i]["rv"] == 1 for i in range(4)) and not rows[4].get("rv")
    tr = _man(wd)["stages"]["translate"]
    assert tr["status"] == "pending" and "dừng" in tr["cur_text"] and "last_error" not in tr
    kept = {i: rows[i]["vi"] for i in range(4)}
    (wd / ".stop").rename(wd / ".stop.old")
    fake2 = FakeLLM("good")
    monkeypatch.setattr(llm, "chat", fake2)
    res = pipeline.run(wd, "Tiếng Việt")
    assert [c["i"] for c in fake2.calls] == [[4]]           # resume: chỉ câu chưa rv
    assert res["status"] == "done" and all(_read(wd)[i]["vi"] == kept[i] for i in range(4))


def test_stop_during_429_wait_aborts(tmp_path, monkeypatch, fastclock):
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    err = llm.LLMUnavailable("429 Too Many Requests")
    calls = []

    def chat(messages, model=None, **kw):
        calls.append(1)
        if len(calls) == 2:
            (wd / ".stop").write_text("1", encoding="utf-8")
        raise err
    monkeypatch.setattr(llm, "chat", chat)
    with pytest.raises(RuntimeError, match="stopped by user"):
        pipeline.run(wd, "Tiếng Việt")
    assert len(calls) <= 3


def test_resume_with_partial_rv_reviews_only_rest(tmp_path, monkeypatch):
    script = [{"i": s["i"], "start": s["start"], "end": s["end"], "zh": s["text"], "vi": GOOD[s["i"]], "rv": 1,
               "s_spk": WHO[s["i"]][0], "s_to": WHO[s["i"]][1], "s_conf": 0.9, "custom": "giữ"}
              for s in SEGS[:3]]
    wd = _job(tmp_path, script=script)
    seen = _nmt(monkeypatch)
    fake = FakeLLM("good")
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    rows = _read(wd)
    assert seen["rows"] == [3, 4]                            # NMT chỉ câu chưa có vi
    assert [c["i"] for c in fake.calls] == [[3, 4]]          # review chỉ câu chưa rv
    assert "NGỮ CẢNH TRƯỚC" in fake.calls[0]["user"] and GOOD[2] in fake.calls[0]["user"]
    assert rows[0]["custom"] == "giữ" and rows[0]["vi"] == GOOD[0]
    assert res["status"] == "done"


# ───────────────────────── nhiều tập liên tiếp — bảng bộ chung ─────────────────────────

def _ep(tmp_path, name, segs):
    wd = tmp_path / name
    wd.mkdir()
    (wd / "asr.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
    (wd / "manifest.json").write_text(json.dumps({"stages": {}, "request": {"group_title": "测试剧"}},
                                                 ensure_ascii=False), encoding="utf-8")
    return wd


def test_consecutive_episodes_share_series_table(tmp_path, monkeypatch):
    store = tmp_path / "series_store"
    monkeypatch.setattr(series_glossary, "STORE", store)
    monkeypatch.setattr(series_glossary, "save_from", _ORIG_SAVE_FROM)
    monkeypatch.setattr(series_glossary, "_core_of", lambda t: t)
    import core.film_db as film_db
    monkeypatch.setattr(film_db, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db in test")))
    plans_seen = []

    def fake_build_plan(segments, target="Tiếng Việt", model=None, name_hints=None, series_plan=None,
                        work_dir=None, before_local_llm=None):
        plans_seen.append(series_plan)
        if len(plans_seen) == 1:
            return json.loads(json.dumps(PLAN))
        # tập 2: plan tập mới KHÔNG có cặp Lâm→Tô (phải thừa kế từ bảng bộ)
        return {"genre": "đô thị", "characters": [{"zh": "王婆", "vi": "Vương Bà"}], "terms": [], "pronouns": [],
                "scenes": []}
    monkeypatch.setattr(T, "build_plan", fake_build_plan)
    monkeypatch.setattr(T, "_ocr_name_hints", lambda wd: None)
    ep1 = _ep(tmp_path, "ep1", SEGS)
    seen = _nmt(monkeypatch)
    monkeypatch.setattr(llm, "chat", FakeLLM("good"))
    r1 = pipeline.run(ep1, "Tiếng Việt")
    assert r1["status"] == "done"
    sg = json.loads((store / "测试剧.json").read_text(encoding="utf-8"))
    assert {c["vi"] for c in sg["characters"]} >= {"Lâm Phàm", "Tô Tình"} and len(sg["pronouns"]) == 2
    # tập 2
    ep2 = _ep(tmp_path, "ep2", SEGS)
    seen = _nmt(monkeypatch)
    fake2 = FakeLLM("good")
    monkeypatch.setattr(llm, "chat", fake2)
    r2 = pipeline.run(ep2, "Tiếng Việt")
    assert plans_seen[1] and len(plans_seen[1]["pronouns"]) == 2       # plan tập 2 nhận bảng bộ
    assert seen["plan"] and seen["plan"].get("pronouns")               # NMT ∥: flavor theo bảng bộ
    assert "Lâm Phàm → Tô Tình: tự xưng «anh» · gọi «em»" in fake2.calls[0]["user"]
    assert r2["status"] == "done" and _read(ep2)[0]["vi"] == "Tô Tình, anh đã đợi em lâu rồi."
    sg2 = json.loads((store / "测试剧.json").read_text(encoding="utf-8"))
    assert "Vương Bà" in {c["vi"] for c in sg2["characters"]} and len(sg2["pronouns"]) == 2
    assert sg2["seasons"] == ["ep1", "ep2"]


def test_series_learns_plan_even_when_review_parks(tmp_path, monkeypatch):
    """Lỗi cũ: save_from chạy TRƯỚC khi ghi glossary.json → tập mới không vào bảng bộ."""
    store = tmp_path / "series_store"
    monkeypatch.setattr(series_glossary, "STORE", store)
    monkeypatch.setattr(series_glossary, "save_from", _ORIG_SAVE_FROM)
    monkeypatch.setattr(series_glossary, "_core_of", lambda t: t)
    import core.film_db as film_db
    monkeypatch.setattr(film_db, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(T, "build_plan", lambda *a, **k: json.loads(json.dumps(PLAN)))
    monkeypatch.setattr(T, "_ocr_name_hints", lambda wd: None)
    ep = _ep(tmp_path, "ep1", SEGS)
    _nmt(monkeypatch)
    monkeypatch.setattr(llm, "chat", FakeLLM("empty"))      # cloud hỏng → park
    assert pipeline.run(ep, "Tiếng Việt")["status"] == "park"
    sg = json.loads((store / "测试剧.json").read_text(encoding="utf-8"))
    assert len(sg["pronouns"]) == 2


# ───────────────────────── lexicon / plan_view / store ─────────────────────────

def test_lexicon_context_regex_woqu():
    def z(lines):
        return [e["zh"] for e in lexicon.hints_for(lines)]
    assert z(["我去。"]) == ["我去"] and z(["我去，这么强？"]) == ["我去"]
    assert z(["我去一趟。"]) == [] and z(["嗯我去。"]) == [] and z(["明天我去总部观战。"]) == []
    assert "大爷的" in z(["大爷的有点东西。"]) and z(["去你大爷的！"]) == ["大爷的"]
    assert z(["这是王大爷的房子。"]) == []
    assert all(isinstance(e["zh"], str) and e["vi"] for e in lexicon.load())


def test_lexicon_bad_regex_entry_skipped(tmp_path):
    p = tmp_path / "lx.json"
    p.write_text(json.dumps({"entries": [{"zh": "我去", "vi": "vãi", "re": "([bad"},
                                         {"zh": "活该", "vi": "đáng đời"}]}, ensure_ascii=False), encoding="utf-8")
    assert [e["zh"] for e in lexicon.load(p)] == ["活该"]


def test_pair_line_unambiguous_with_note_and_no_cjk():
    pv = PlanView({"characters": [{"zh": "甲", "vi": "Giáp"}, {"zh": "乙", "vi": "Ất"}],
                   "pronouns": [{"who": "Giáp", "to": "Ất", "self": "tôi / anh", "call": ["ngươi", "徒儿"],
                                 "note": "thù địch,  chuyển\nsang anh khi làm hoà"}]})
    blk = pv.block_for([{"i": 0, "zh": "甲乙"}])
    assert "  - Giáp → Ất: tự xưng «tôi» hoặc «anh» · gọi «ngươi» (thù địch, chuyển sang anh khi làm hoà)" in blk
    assert "徒儿" not in blk


def test_name_ok_reset_when_vi_changes(tmp_path, monkeypatch):
    script = [{"i": 0, "start": 0.0, "end": 1.5, "zh": "苏晴，我等你很久了。", "vi": "x", "s_name_ok": 1}]
    wd = _job(tmp_path, script=script)
    _nmt(monkeypatch)
    monkeypatch.setattr(llm, "chat", FakeLLM("good"))
    pipeline.run(wd, "Tiếng Việt")
    assert "s_name_ok" not in _read(wd)[0]
    # force: dịch lại → cũng xoá s_name_ok
    rows = json.loads((wd / "script.json").read_text(encoding="utf-8"))
    rows[1]["s_name_ok"] = 1
    (wd / "script.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    from engines.translate_simple.script_store import ScriptStore
    st = ScriptStore(wd)
    st.ensure_from_asr(SEGS, force=True)
    assert "s_name_ok" not in st.by_i()[1]


def test_corrupt_script_backed_up_before_rebuild(tmp_path, monkeypatch):
    wd = _job(tmp_path)
    (wd / "script.json").write_text('[{"i":0,"vi":"tay sửa","manual":1}, {"i":1', encoding="utf-8")
    _nmt(monkeypatch)
    monkeypatch.setattr(llm, "chat", FakeLLM("good"))
    res = pipeline.run(wd, "Tiếng Việt")
    baks = list(wd.glob("script.corrupt.*.json"))
    assert len(baks) == 1 and "tay sửa" in baks[0].read_text(encoding="utf-8")
    assert res["status"] == "done"


def test_trace_rotates(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_TRACE_MAX_MB", "0.0001")     # ~100 byte
    wd = _job(tmp_path)
    (wd / "trace.jsonl").write_text("x" * 500 + "\n", encoding="utf-8")
    _nmt(monkeypatch)
    monkeypatch.setattr(llm, "chat", FakeLLM("good"))
    pipeline.run(wd, "Tiếng Việt")
    assert (wd / "trace.1.jsonl").read_text(encoding="utf-8").startswith("xxx")
    assert not (wd / "trace.jsonl").read_text(encoding="utf-8").startswith("xxx")


def test_validate_conf_semantics():
    pv = PlanView(PLAN)
    base = {"i": 0, "zh": "苏晴。", "vi": "Tô Tình, tôi đợi cậu.", "s_spk": "Lâm Phàm", "s_to": "Tô Tình", "rv": 1}
    assert validate.check_row(dict(base, s_conf=None), pv, 0.7)              # thiếu conf → vẫn kiểm
    assert not validate.check_row(dict(base, s_conf=0.3), pv, 0.7)           # conf thấp → bỏ qua
    assert not [f for f in validate.check_row({k: v for k, v in base.items() if k != "rv"}, pv, 0.7)
                if f["code"] == "pronoun"]                                   # hàng luồng cũ (không s_conf)
