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
    for k in ("SIMPLE_CONTEXT", "SIMPLE_FULL_BATCH", "SIMPLE_FULL_MAX_TOKENS", "SIMPLE_BATCH", "SIMPLE_CHAPTER_MARGIN",
              "SIMPLE_OUT_TOKENS_PER_LINE", "DEEPSEEK_MAX_TOKENS", "DEEPSEEK_CONCURRENCY", "ALIBABA_CONCURRENCY",
              "SIMPLE_LOT_MIN", "SIMPLE_LOT_MAX"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SIMPLE_WORKERS", "1")
    monkeypatch.setenv("DEEPSEEK_CONCURRENCY", "1")         # thứ tự gọi xác định trong test
    monkeypatch.setenv("ALIBABA_CONCURRENCY", "1")
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
    assert fc.n_lines == 130 and "\n129|林凡说第129句" in fc.text and "1.5" not in fc.text   # gọn: i|zh
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


def test_too_long_film_that_cannot_be_split_falls_back_to_window(tmp_path, env, monkeypatch):
    st = _store(tmp_path, 300)                      # < 2 chương × 200 câu tối thiểu
    monkeypatch.setenv("SIMPLE_FULL_MAX_TOKENS", "2000")
    assert film_context.for_stage(st.rows, PlanView(PLAN), "deepseek/deepseek-flash") is None


# ───────────── phim rất dài: chia chương ─────────────

def _long(tmp_path, n=2400):
    st = ScriptStore(tmp_path)
    names = ["林凡", "苏烟", "王婆"]
    st.rows = [{"i": i, "start": i, "end": i + 1.0, "zh": f"{names[i * 3 // n]}说了第{i}句很长很长的话", "vi": f"nháp {i}"}
               for i in range(n)]
    return st


PLAN3 = {**PLAN, "characters": PLAN["characters"] + [{"zh": "王婆", "vi": "Vương Bà"}]}


def test_long_film_is_split_into_chapters_under_cap(tmp_path, env, monkeypatch):
    st = _long(tmp_path)
    monkeypatch.setenv("SIMPLE_FULL_MAX_TOKENS", "12000")
    fc = film_context.for_stage(st.rows, PlanView(PLAN3), "deepseek/deepseek-flash")
    assert fc is not None and fc.mode == "chapters" and len(fc.chapters) >= 2
    assert all(c.est_tokens <= 12000 for c in fc.chapters)
    assert fc.chapters[0].lo == 0 and fc.chapters[-1].hi == 2400
    assert all(a.hi == b.lo for a, b in zip(fc.chapters, fc.chapters[1:]))          # phủ kín, không chồng
    first, last = fc.chapters[0].text, fc.chapters[-1].text
    assert "ĐOẠN PHIM chương 1/" in first and "王婆 = Vương Bà" not in first           # plan lọc theo chương
    assert "王婆 = Vương Bà" in last and "林凡 = Lâm Phàm" not in last
    m = 100
    assert f"\n{fc.chapters[1].lo - m}|" in fc.chapters[1].text                       # lề ngữ cảnh trước chương


def test_chapter_mode_lots_never_cross_chapters_and_warm_cache_first(tmp_path, env, fake, monkeypatch):
    st = _long(tmp_path, 1200)
    monkeypatch.setenv("SIMPLE_FULL_MAX_TOKENS", "8000")
    monkeypatch.setenv("DEEPSEEK_CONCURRENCY", "4")
    stats = stage_review.run(st, PlanView(PLAN3), model="deepseek/deepseek-flash")
    assert stats["context"] == "chapters" and stats["chapters"] >= 2 and stats["n_left"] == 0
    fc = film_context.for_stage(st.rows, PlanView(PLAN3), "deepseek/deepseek-flash")
    for msgs in fake.calls:
        ids = [x["i"] for x in json.loads(msgs[-1]["content"].split("DANH SÁCH (JSON):\n", 1)[1].split("\n\n", 1)[0])]
        ch = fc.chapter_at(ids[0])
        assert all(ch.lo <= i < ch.hi for i in ids)                                     # lô không vắt chương
        assert msgs[1]["content"] == ch.text                                            # đúng phần đầu chương
    n_ch = len(fc.chapters)
    first_prefixes = [m[1]["content"] for m in fake.calls[:n_ch]]
    assert len(set(first_prefixes)) == n_ch                                             # lô đầu mỗi chương chạy trước


def test_lot_size_auto_from_concurrency_and_output_cap(tmp_path, env, monkeypatch):
    fc = film_context.build(_store(tmp_path, 10).rows, PlanView(PLAN))
    ds = "deepseek/deepseek-flash"
    monkeypatch.setenv("DEEPSEEK_CONCURRENCY", "32")
    monkeypatch.setenv("DEEPSEEK_MAX_TOKENS", "32768")
    assert film_context.lot_size(ds, fc, 1500) == 47              # 1500 ÷ 32 → mọi lô trong 1 đợt
    assert film_context.lot_size(ds, fc, 10000) == 200            # kẹp trần 200 câu
    assert film_context.lot_size(ds, fc, 300) == 40               # kẹp sàn 40 câu
    monkeypatch.delenv("DEEPSEEK_MAX_TOKENS")
    assert film_context.lot_size(ds, fc, 10000) == (8192 - 600) // 90   # trần output 8192 → 84 câu
    monkeypatch.setenv("SIMPLE_FULL_BATCH", "60")
    assert film_context.lot_size(ds, fc, 10000) == 60             # ép cỡ lô
    assert film_context.lot_size("chatgpt/gpt-6", None, 10000) == 30   # window: như cũ


def test_workers_follow_provider_concurrency(env, monkeypatch):
    from engines.translate_simple import cfg as scfg
    monkeypatch.delenv("DEEPSEEK_CONCURRENCY")
    assert scfg.workers_for("deepseek/deepseek-flash") == 32
    monkeypatch.setenv("DEEPSEEK_CONCURRENCY", "200")
    assert scfg.workers_for("deepseek/deepseek-flash") == 200
    assert scfg.workers_for("chatgpt/gpt-6") == 1                 # SIMPLE_WORKERS (fixture = 1)


def test_parallel_lots_actually_run_concurrently(tmp_path, env, monkeypatch):
    import threading
    import engines.llm as llm
    monkeypatch.setenv("DEEPSEEK_CONCURRENCY", "8")
    live = {"now": 0, "max": 0}
    lk = threading.Lock()
    base = FakeChat()

    def chat(messages, model=None, **kw):
        with lk:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        threading.Event().wait(0.05)          # time.sleep bị fixture thay bằng no-op
        try:
            return base(messages, model=model)
        finally:
            with lk:
                live["now"] -= 1
    monkeypatch.setattr(llm, "chat", chat)
    stats = stage_review.run(_store(tmp_path, 400), PlanView(PLAN), model="deepseek/deepseek-flash")
    assert stats["n_left"] == 0 and stats["lots"] == 8 and live["max"] >= 4     # 400 ÷ 8 = 50 câu/lô, chạy cùng lúc


def test_output_cap_sent_to_provider(tmp_path, env, monkeypatch):
    seen = {}
    import engines.llm as llm

    def chat(messages, model=None, max_tokens=None, **kw):
        seen["max_tokens"] = max_tokens
        return FakeChat()(messages, model=model)
    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setenv("DEEPSEEK_MAX_TOKENS", "16384")
    stage_review.run(_store(tmp_path, 5), PlanView(PLAN), model="deepseek/deepseek-flash")
    assert seen["max_tokens"] == 16384


def test_review_full_mode_same_prefix_every_lot_and_bigger_lots(tmp_path, env, fake):
    st = _store(tmp_path, 130)
    stats = stage_review.run(st, PlanView(PLAN), model="deepseek/deepseek-flash")
    assert stats["context"] == "full" and stats["n_left"] == 0 and stats["n_ok"] == 130
    assert len(fake.calls) == 2                                    # 130 câu, 1 luồng → lô to nhất vừa trần output (84)
    prefixes = {c[1]["content"] for c in fake.calls}
    assert len(prefixes) == 1                                      # giống nhau từng byte → cache được
    assert [m["role"] for m in fake.calls[0]] == ["system", "user", "user"]
    assert "TOÀN BỘ PHIM" in fake.calls[0][1]["content"] and "LÔ NÀY: câu 0→83" in fake.calls[0][2]["content"]


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
