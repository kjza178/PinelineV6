"""A8 (ngữ cảnh cả phim, phần đầu cố định cho cache) + A10 (giữ nháp / máy đã sửa + lý do + lịch sử từng câu).

Không gọi model thật: thay engines.llm.chat bằng hàm giả ghi lại messages."""
from __future__ import annotations

import json

import pytest

from engines.translate_simple import film_context, repair, stage_review
from engines.translate_simple.plan_view import PlanView
from engines.translate_simple.script_store import ScriptStore

PLAN = {"genre": "đô thị", "characters": [{"zh": "林凡", "vi": "Lâm Phàm"}, {"zh": "苏烟", "vi": "Tô Yên"}],
        "pronouns": [{"who": "Lâm Phàm", "to": "Tô Yên", "self": "anh", "call": "em"}],
        "terms": [{"zh": "天宫", "vi": "Thiên Cung"}], "scenes": [{"range": [0, 200], "location": "nhà"}]}


@pytest.fixture
def env(monkeypatch):
    for k in ("SIMPLE_CONTEXT", "SIMPLE_FULL_BATCH", "SIMPLE_FULL_MAX_TOKENS", "SIMPLE_BATCH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SIMPLE_WORKERS", "1")
    monkeypatch.setattr("time.sleep", lambda s: None)


def _store(tmp_path, n=130, vi=True):
    st = ScriptStore(tmp_path)
    st.rows = [{"i": i, "start": i, "end": i + 1.5, "zh": f"林凡说第{i}句", "vi": (f"nháp {i}" if vi else "")}
               for i in range(n)]
    st.write()
    return st


class FakeChat:
    """Trả mọi câu trong DANH SÁCH; câu chia hết cho 3 giữ nguyên nháp, còn lại sửa kèm «sua»."""

    def __init__(self):
        self.calls: list[list[dict]] = []

    def __call__(self, messages, model=None, **kw):
        self.calls.append(messages)
        user = messages[-1]["content"]
        payload = json.loads(user.split("DANH SÁCH (JSON):\n", 1)[1].split("\n\n", 1)[0])
        lines = []
        for it in payload:
            i = it["i"]
            if it.get("nhap") and i % 3 == 0:
                lines.append({"i": i, "vi": it["nhap"], "spk": "Lâm Phàm", "to": "Tô Yên", "conf": 0.9})
            else:
                lines.append({"i": i, "vi": f"bản sửa {i}", "spk": "Lâm Phàm", "to": "Tô Yên", "conf": 0.9,
                              "sua": f"sai nghĩa câu {i}"})
        return json.dumps({"lines": lines}, ensure_ascii=False)


@pytest.fixture
def fake(monkeypatch):
    f = FakeChat()
    import engines.llm as llm
    monkeypatch.setattr(llm, "chat", f)
    return f


# ───────────── A8 ─────────────

def test_full_prefix_has_whole_film_and_full_plan_but_no_vi(tmp_path, env):
    st = _store(tmp_path)
    fc = film_context.build(st.rows, PlanView(PLAN))
    assert fc.n_lines == 130 and "129 | 1.5 | 林凡说第129句" in fc.text
    assert "Lâm Phàm → Tô Yên" in fc.text and "天宫 = Thiên Cung" in fc.text
    assert "nháp" not in fc.text                      # VI đổi liên tục → không nằm trong phần đầu (phá cache)
    st.rows[5]["vi"] = "đã đổi"
    assert film_context.build(st.rows, PlanView(PLAN)).text == fc.text


def test_auto_mode_uses_full_only_for_api_providers(tmp_path, env, monkeypatch):
    st = _store(tmp_path, 10)
    pv = PlanView(PLAN)
    assert film_context.for_stage(st.rows, pv, "deepseek/deepseek-flash") is not None
    assert film_context.for_stage(st.rows, pv, "alibaba/qwen-flash") is not None
    for m in ("chatgpt/gpt-6", "local", "local/gemma4"):
        assert film_context.for_stage(st.rows, pv, m) is None
    monkeypatch.setenv("SIMPLE_CONTEXT", "full")
    assert film_context.for_stage(st.rows, pv, "local") is not None
    monkeypatch.setenv("SIMPLE_CONTEXT", "window")
    assert film_context.for_stage(st.rows, pv, "deepseek/deepseek-flash") is None


def test_too_long_film_falls_back_to_window(tmp_path, env, monkeypatch):
    st = _store(tmp_path, 400)
    monkeypatch.setenv("SIMPLE_FULL_MAX_TOKENS", "2000")
    assert film_context.for_stage(st.rows, PlanView(PLAN), "deepseek/deepseek-flash") is None


def test_review_full_mode_same_prefix_every_lot_and_bigger_lots(tmp_path, env, fake):
    st = _store(tmp_path, 130)
    stats = stage_review.run(st, PlanView(PLAN), model="deepseek/deepseek-flash")
    assert stats["context"] == "full" and stats["n_left"] == 0 and stats["n_ok"] == 130
    assert len(fake.calls) == 3                                    # 130 câu / lô 60 (cũ: 5 lô × 30)
    prefixes = {c[1]["content"] for c in fake.calls}
    assert len(prefixes) == 1                                      # giống nhau từng byte → cache được
    assert [m["role"] for m in fake.calls[0]] == ["system", "user", "user"]
    assert "TOÀN BỘ PHIM" in fake.calls[0][1]["content"] and "LÔ NÀY: câu 0→59" in fake.calls[0][2]["content"]


def test_review_window_mode_unchanged_for_other_models(tmp_path, env, fake):
    st = _store(tmp_path, 40)
    stats = stage_review.run(st, PlanView(PLAN), model="chatgpt/gpt-6")
    assert "context" not in stats and len(fake.calls) == 2         # lô 30 như cũ
    assert [m["role"] for m in fake.calls[0]] == ["system", "user"]


def test_repair_reuses_same_prefix_as_review(tmp_path, env, fake):
    st = _store(tmp_path, 20)
    pv = PlanView(PLAN)
    stage_review.run(st, pv, model="deepseek/deepseek-flash")
    flags = {3: [{"code": "pronoun", "why": "x"}], 7: [{"code": "name", "why": "y"}]}
    repair.run(st, pv, flags, model="deepseek/deepseek-flash", rounds=1)
    assert fake.calls[-1][1]["content"] == fake.calls[0][1]["content"]


# ───────────── A10 ─────────────

def test_rows_record_pass_fixed_new_with_reason(tmp_path, env, fake):
    st = _store(tmp_path, 6)
    st.rows[4]["vi"] = ""                                          # chưa có nháp → dịch mới
    stage_review.run(st, PlanView(PLAN), model="deepseek/deepseek-flash")
    by = {r["i"]: r for r in st.rows}
    assert by[0]["s_act"] == "pass" and "s_why" not in by[0] and by[0]["vi"] == "nháp 0"
    assert by[1]["s_act"] == "fixed" and by[1]["s_why"] == "sai nghĩa câu 1" and by[1]["vi"] == "bản sửa 1"
    assert by[4]["s_act"] == "new"
    assert all(r["s_step"] == "review" for r in st.rows)
    trace = [json.loads(x) for x in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    t1 = [t for t in trace if t.get("i") == 1 and t["step"] == "review"]
    assert t1 and t1[0]["old"] == "nháp 1" and t1[0]["new"] == "bản sửa 1" and "sai nghĩa câu 1" in t1[0]["why"]


def test_parse_lines_reads_reason_field():
    out = stage_review.parse_lines('{"lines":[{"i":1,"vi":"a","sua":"  xưng hô   theo cặp "},{"i":2,"vi":"b"}]}', {1, 2})
    assert out[1]["why"] == "xưng hô theo cặp" and out[2]["why"] == ""


def test_force_clears_review_marks(tmp_path, env, fake):
    st = _store(tmp_path, 3)
    stage_review.run(st, PlanView(PLAN), model="deepseek/deepseek-flash")
    st2 = ScriptStore(tmp_path)
    st2.ensure_from_asr([{"i": i, "start": i, "end": i + 1, "text": "x"} for i in range(3)], force=True)
    assert not any(k in r for r in st2.rows for k in ("s_act", "s_why", "s_step"))


def test_history_api_returns_only_that_line(tmp_path, env, fake, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.main as M
    st = _store(tmp_path / "job1", 4) if (tmp_path / "job1").mkdir() is None else None
    (tmp_path / "job1" / "manifest.json").write_text("{}", encoding="utf-8")
    stage_review.run(st, PlanView(PLAN), model="deepseek/deepseek-flash")

    class _Inst:
        work_root = tmp_path
    app = FastAPI()
    app.include_router(M._v6_router(_Inst()))
    c = TestClient(app)
    d = c.get("/api/v6/jobs/job1/script/1/history").json()
    assert d["n"] == 1 and d["history"][0]["new"] == "bản sửa 1" and d["history"][0]["step"] == "review"
    assert c.get("/api/v6/jobs/job1/script/2/history").json()["history"][0]["new"] == "bản sửa 2"
    rows = c.get("/api/v6/jobs/job1/script").json()["rows"]
    assert rows[1]["s_act"] == "fixed" and rows[1]["s_why"] == "sai nghĩa câu 1"
    assert c.get("/api/v6/jobs/..%2Fx/script/1/history").status_code == 404
