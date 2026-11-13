"""Review độc lập (agent D, 2026-11-12) đợt translate-simple «next» — mỗi ca dưới đây FAIL trên bản agent B
(trước sửa) và PASS sau sửa. LLM / NMT / video đều GIẢ. Xem docs/reviews/2026-11-12-translate-simple-next-review.md.

D1 gỡ lặp VI ở hậu kỳ làm hỏng dấu câu («Đi mau,!») và gỡ cả lặp nhấn giọng khi ZH không lặp (快走，快走！).
D2 SIMPLE_ZH_SOURCE=1: zh_source.sync_rows chạy SAU gỡ lặp ZH → trả lại chữ lặp (gỡ lặp mất tác dụng).
D3 SIMPLE_ZH_SOURCE=1: ASR tách 1 dòng hardsub thành 2 câu → cả hai câu nhận NGUYÊN dòng OCR (lồng tiếng đọc 2 lần).
D4 sửa tên khi KHÔNG có pypinyin: tên/thuật ngữ 4 chữ lệch 1 chữ bất kỳ bị thay (天下第二 → 天下第一).
D5 bảng quy tắc: 兄弟 / «huynh đệ» (anh em ruột) bị xếp vào «bạn bè» (tao/mày) thay vì anh/chị–em.
D6 tools/collect_manual_edits.py: --work <job> --out <thư mục cha của job> → ghi file VÀO thư mục job.
"""
from __future__ import annotations

import json
from pathlib import Path

import engines.llm as llm
import engines.series_glossary as series_glossary
from tests import test_translate_simple_next as N  # noqa: F401  (import sớm: giữ save_from thật)
from tests.test_translate_simple import (  # noqa: F401  (fixture autouse _iso + fastclock dùng lại)
    SEGS, FakeLLM, _iso, _job, _nmt, _read, _trace, fastclock,
)
from engines.translate_simple import pair_rules, pipeline, post, zh_source
from engines.translate_simple.plan_view import PlanView
from engines.translate_simple.script_store import ScriptStore

ROOT = Path(__file__).resolve().parents[1]
_ORIG_SAVE_FROM = series_glossary.save_from


# ───────────────────────── D1: gỡ lặp VI ở hậu kỳ ─────────────────────────

def _store(tmp_path, rows):
    wd = tmp_path / "j"
    wd.mkdir()
    st = ScriptStore(wd)
    st.rows = rows
    return st


def test_post_vi_heal_keeps_intentional_repeat_and_punct(tmp_path):
    rows = [
        {"i": 0, "zh": "快走，快走！", "vi": "Đi mau, đi mau!", "rv": 1},          # ZH lặp có chủ ý → VI giữ
        {"i": 1, "zh": "救命，救命啊！", "vi": "Cứu tôi với, cứu tôi với!", "rv": 1},
        {"i": 2, "zh": "杀吧杀吧！", "vi": "Giết đi, giết đi!", "rv": 1},           # ZH lặp ASR (câu rv giữ zh)
        {"i": 3, "zh": "杀吧杀吧，", "vi": "Giết đi giết đi,", "rv": 1},
        {"i": 4, "zh": "不对。", "vi": "Không đúng không đúng.", "rv": 1},          # ZH không lặp, VI lặp → giữ
    ]
    st = _store(tmp_path, rows)
    post.apply(st, {})
    assert [r["vi"] for r in st.rows] == ["Đi mau, đi mau!", "Cứu tôi với, cứu tôi với!", "Giết đi!",
                                         "Giết đi,", "Không đúng không đúng."]
    assert not any(",!" in r["vi"] or ",." in r["vi"] for r in st.rows)


def test_post_vi_heal_rows_whose_asr_had_stutter(tmp_path):
    """Câu mà ZH đã được gỡ lặp ở bước dựng script (ASR gốc lặp) → VI vẫn được gỡ (ý định của agent B)."""
    rows = [{"i": 0, "zh": "杀吧，", "vi": "Giết đi giết đi,"}, {"i": 1, "zh": "走吧。", "vi": "Đi thôi đi thôi."}]
    st = _store(tmp_path, rows)
    st.stutter_i = {0}
    post.apply(st, {})
    assert [r["vi"] for r in st.rows] == ["Giết đi,", "Đi thôi đi thôi."]


# ───────────────────────── D2: zh_source không được trả lại chữ lặp ─────────────────────────

def test_zh_source_sync_does_not_undo_stutter_heal(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_ZH_SOURCE", "1")
    monkeypatch.setenv("SIMPLE_ZH_NAMEFIX", "0")
    segs = [dict(s) for s in SEGS]
    segs[2] = {"i": 2, "start": 3.0, "end": 4.5, "text": "不对不队"}             # ASR sai 1 chữ, OCR đúng nhưng lặp
    wd = _job(tmp_path, segs=segs)
    monkeypatch.setattr(zh_source, "detect", lambda wd: {"has_hardsub": True, "method": "fake"})
    import engines.asr.hardsub_asr as hardsub_asr
    monkeypatch.setattr(hardsub_asr, "extract_hardsub_cues",
                        lambda wd, **k: [{"start": 3.0, "end": 4.5, "zh": "不对不对"}])
    _nmt(monkeypatch)
    monkeypatch.setattr(llm, "chat", FakeLLM("good"))
    pipeline.run(wd, "Tiếng Việt")
    r2 = _read(wd)[2]
    assert r2["zh_src"] == "ocr"
    assert r2["zh"] == "不对"                                                   # gỡ lặp vẫn có hiệu lực
    assert any(t["step"] == "asr_heal" and t["i"] == 2 for t in _trace(wd))


# ───────────────────────── D3: một dòng OCR không được nhân đôi sang 2 câu ASR ─────────────────────────

def test_prepare_ocr_line_not_duplicated_over_split_asr(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_ZH_NAMEFIX", "0")
    cue = [{"start": 0.0, "end": 3.0, "zh": "我今天一定要把他抓回来"}]
    for segs in ([{"i": 0, "start": 0.0, "end": 1.5, "text": "我今天一定要"},
                  {"i": 1, "start": 1.5, "end": 3.0, "text": "把他抓回来"}],
                 [{"i": 0, "start": 0.0, "end": 1.5, "text": "我今天一定药"},
                  {"i": 1, "start": 1.5, "end": 3.0, "text": "把她抓回来"}]):
        out, rep = zh_source.prepare(tmp_path, segs, detect_fn=lambda wd: {"has_hardsub": True, "method": "fake"},
                                     cues_fn=lambda wd: cue)
        txt = "".join(s["text"] for s in out)
        assert txt.count("抓回来") == 1, [s["text"] for s in out]              # không đọc 2 lần
        assert [s["text"] for s in out] == [s["text"] for s in segs]          # mảnh ASR → giữ ASR
        assert [(s["start"], s["end"]) for s in out] == [(0.0, 1.5), (1.5, 3.0)]
    # khớp 1-1 bình thường vẫn thắng
    out, _ = zh_source.prepare(tmp_path, [{"i": 0, "start": 0.0, "end": 3.0, "text": "我今天一定要把他抓回来了"}],
                               detect_fn=lambda wd: {"has_hardsub": True, "method": "fake"},
                               cues_fn=lambda wd: [{"start": 0.0, "end": 3.0, "zh": "我今天一定要把她抓回来了"}])
    assert out[0]["text"] == "我今天一定要把她抓回来了" and out[0]["zh_src"] == "ocr"


# ───────────────────────── D4: không có pypinyin → không sửa tên ─────────────────────────

def test_fix_names_without_pinyin_never_replaces():
    segs = [{"i": 0, "text": "我是天下第二"}, {"i": 1, "text": "九阳神剑出鞘"}, {"i": 2, "text": "天虚宗门来"}]
    log = zh_source.fix_names(segs, ["天下第一", "九阳神功", "天墟宗门"], min_score=0.75, pinyin=False)
    assert log == []
    assert [s["text"] for s in segs] == ["我是天下第二", "九阳神剑出鞘", "天虚宗门来"]


# ───────────────────────── D5: anh em ruột không phải «bạn bè» ─────────────────────────

def test_pair_rules_blood_brothers_not_friends():
    plan = {"style_lock": {"mode": "modern"},
            "characters": [{"zh": "林大", "vi": "Lâm Đại", "gender": "nam"},
                           {"zh": "林二", "vi": "Lâm Nhị", "gender": "nam"},
                           {"zh": "张三", "vi": "Trương Tam", "gender": "nam"},
                           {"zh": "李四", "vi": "Lý Tứ", "gender": "nam"}],
            "relationship_matrix": {"Lâm Đại": {"Lâm Nhị": "亲兄弟"}, "Trương Tam": {"Lý Tứ": "Huynh đệ ruột thịt"}},
            "pronouns": []}
    pv = PlanView(plan)
    pair_rules.fill(pv)
    for a, b in (("Lâm Đại", "Lâm Nhị"), ("Trương Tam", "Lý Tứ")):
        p = pv.pair(a, b)
        assert p is None or p.get("rel") != "friends", (a, b, p)
    # «好兄弟» / «kết nghĩa» vẫn là bạn bè ngang hàng
    plan2 = dict(plan, relationship_matrix={"Lâm Đại": {"Lâm Nhị": "好兄弟"}, "Trương Tam": {"Lý Tứ": "Huynh đệ kết nghĩa"}})
    pv2 = PlanView(plan2)
    pair_rules.fill(pv2)
    assert pv2.pair("Lâm Đại", "Lâm Nhị")["rel"] == "friends" and pv2.pair("Trương Tam", "Lý Tứ")["rel"] == "friends"
    # «tớ – cậu» là dạng bạn bè hiện đại phổ biến (không bị cờ pronoun)
    assert "tớ" in pv2.pair("Lâm Đại", "Lâm Nhị")["self"]


# ───────────────────────── D6: collect_manual_edits không ghi vào work ─────────────────────────

def test_collect_refuses_out_that_contains_work(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("cme", ROOT / "tools" / "collect_manual_edits.py")
    cme = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cme)
    work = tmp_path / "work"
    job = work / "job1"
    job.mkdir(parents=True)
    (job / "script.json").write_text(json.dumps([{"i": 0, "zh": "你好", "vi": "Chào anh.", "manual": 1}],
                                                ensure_ascii=False), encoding="utf-8")
    (job / "glossary.json").write_text("{}", encoding="utf-8")
    before = sorted(p.name for p in job.iterdir())
    rc = cme.main(["--work", str(job), "--out", str(work)])          # out = thư mục cha của job → od = job
    assert rc == 2
    assert sorted(p.name for p in job.iterdir()) == before
    assert not (work / "summary.json").exists()


# ───────────────────────── Kiểm 1 bổ sung: cờ tắt = byte-identical (bảng bộ 2 tập, chạy lại sau park) ──────────

def test_flags_off_byte_identical_series_and_rerun(tmp_path, monkeypatch):
    """Bổ sung kịch bản cho test_flags_off_byte_identical_to_backup: (a) bảng bộ thật (series_glossary, 2 tập —
    tập 2 thừa kế cặp, done → save_from ghi bảng bộ); (b) job park (cloud trống) rồi CHẠY LẠI → done."""
    import pytest
    import engines.translate as T
    import engines.cloud_retry as CR
    from tests.test_translate_simple import PLAN
    if N._backup_dir() is None:
        pytest.skip("không có backup bản gốc")
    P0 = N._orig_pipeline(tmp_path)
    import core.film_db as film_db
    monkeypatch.setattr(film_db, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(series_glossary, "save_from", _ORIG_SAVE_FROM)
    monkeypatch.setattr(series_glossary, "_core_of", lambda t: t)
    monkeypatch.setattr(T, "_ocr_name_hints", lambda wd: None)

    def ep(root, name):
        wd = root / name
        wd.mkdir(parents=True)
        (wd / "asr.json").write_text(json.dumps(SEGS, ensure_ascii=False), encoding="utf-8")
        (wd / "manifest.json").write_text(json.dumps({"stages": {}, "request": {"group_title": "测试剧"}},
                                                     ensure_ascii=False), encoding="utf-8")
        return wd

    def strip_snap(wd, res, prompts):
        return N._snapshot(wd, res, prompts)

    out = {}
    for tag, P in (("orig", P0), ("new", pipeline)):
        root = tmp_path / tag
        store = root / "series_store"
        monkeypatch.setattr(series_glossary, "STORE", store)
        n_plan = {"n": 0}

        def fake_build_plan(segments, target="Tiếng Việt", model=None, name_hints=None, series_plan=None,
                            work_dir=None, before_local_llm=None):
            n_plan["n"] += 1
            if n_plan["n"] == 1:
                return json.loads(json.dumps(PLAN))
            return {"genre": "đô thị", "characters": [{"zh": "王婆", "vi": "Vương Bà", "gender": "nữ"}],
                    "terms": [], "pronouns": [], "scenes": [],
                    "relationship_matrix": {"Vương Bà": {"Lâm Phàm": "Bà nội và cháu nội"}}}
        monkeypatch.setattr(T, "build_plan", fake_build_plan)
        snaps = []
        for name, modes in (("ep1", ["good"]), ("ep2", ["empty", "good"])):   # ep2: park rồi chạy lại
            wd = ep(root, name)
            for mode in modes:
                CR.reset_gates()
                N._nmt(monkeypatch)
                prompts = []
                fake = FakeLLM(mode)

                def spy(messages, model=None, _f=fake, **kw):
                    prompts.append([m["content"] for m in messages])
                    return _f(messages, model, **kw)
                monkeypatch.setattr(llm, "chat", spy)
                res = P.run(wd, "Tiếng Việt")
                snaps.append((name, mode, res["status"], strip_snap(wd, res, prompts)))
        sg = N._strip(json.loads((store / "测试剧.json").read_text(encoding="utf-8")))
        out[tag] = (snaps, json.dumps(sg, ensure_ascii=False, sort_keys=True))
    (sa, ga), (sb, gb) = out["orig"], out["new"]
    assert [s[:3] for s in sa] == [s[:3] for s in sb]
    assert [s[2] for s in sb] == ["done", "park", "done"]
    for (name, mode, _st, a), (_n, _m, _s, b) in zip(sa, sb):
        assert sorted(a) == sorted(b), (name, mode)
        for k in a:
            assert a[k] == b[k].replace(b"/new/", b"/orig/") or a[k] == b[k], f"{name}/{mode}: {k} khác"
    assert ga == gb.replace("/new/", "/orig/")
