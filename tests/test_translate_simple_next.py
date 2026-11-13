"""translate-simple «next» 2026-11-12 — các bước sau cờ (MẶC ĐỊNH TẮT). LLM / NMT / GPU / video đều GIẢ.

Bước 0 lexicon src + SIMPLE_LEXICON_EXCLUDE_GOLDEN · Bước 2 bảng quy tắc xưng hô (SIMPLE_PAIR_RULES) ·
Bước 6 bổ sung cặp thiếu (SIMPLE_PAIR_FILL) · Bước 8 học lại (SIMPLE_LEARN_INFERRED) + tools chỉ đọc ·
Bước 1 nguồn ZH (SIMPLE_ZH_SOURCE: phát hiện hardsub bằng khung giả, OCR thắng ASR, sửa tên có bảo vệ) ·
«cờ tắt = giống hệt trước»: chạy song song bản GỐC (backup) và bản mới trên cùng kịch bản, so từng byte.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

import engines.cloud_retry as CR
import engines.llm as llm
import engines.series_glossary as series_glossary
from tests.test_translate_simple import (  # noqa: F401  (fixture autouse _iso + fastclock dùng lại)
    NMT, PLAN, SEGS, WHO, FakeLLM, _iso, _items, _job, _man, _nmt, _read, _trace, fastclock,
)
from engines.translate_simple import cfg, lexicon, pair_fill, pair_rules, pipeline, stage_review, zh_source
from engines.translate_simple.plan_view import PlanView

ROOT = Path(__file__).resolve().parents[1]
_ORIG_SAVE_FROM = series_glossary.save_from


# ───────────────────────── Bước 0: lexicon src ─────────────────────────

def test_lexicon_entries_have_src():
    data = json.loads((ROOT / "data" / "lexicon" / "idioms_zh_vi.json").read_text(encoding="utf-8"))
    srcs = {e.get("src") for e in data["entries"]}
    assert srcs <= {"translate_rules", "golden_derived", "expert"} and None not in srcs
    tsrc = (ROOT / "engines" / "translate.py").read_text(encoding="utf-8")   # V6: engines/translate.py
    for e in data["entries"]:   # translate_rules = THẬT SỰ có trong luật translate.py
        assert (e["src"] == "translate_rules") == (e["zh"] in tsrc), e["zh"]
    assert all(e["src"] for e in lexicon.load())


def test_lexicon_exclude_golden_flag(monkeypatch):
    def z(lines):
        return [e["zh"] for e in lexicon.hints_for(lines)]
    assert z(["我去。"]) == ["我去"] and z(["你没大没小！"]) == ["没大没小"]
    monkeypatch.setenv("SIMPLE_LEXICON_EXCLUDE_GOLDEN", "1")
    assert z(["我去。"]) == []                         # golden_derived bị loại
    assert z(["你没大没小！"]) == ["没大没小"]            # translate_rules vẫn giữ
    plan = {"terms": [{"zh": "我去", "vi": "trời ạ", "type": "idiom"}]}
    assert [e["zh"] for e in lexicon.hints_for(["我去。"], plan)] == ["我去"]   # thuật ngữ plan không bị loại


# ───────────────────────── Bước 2: bảng quy tắc xưng hô ─────────────────────────

def test_rules_file_shape():
    r = pair_rules.load_rules()
    ids = {x["id"] for x in r["relations"]}
    assert {"spouse", "lovers", "parent_child", "grand", "siblings", "master_disciple", "sect_siblings",
            "ruler_subject", "master_servant", "teacher_student", "boss_staff", "friends", "enemy"} <= ids
    for x in r["relations"]:
        keys = {"any"} if x.get("sym") else {"hi_lo", "lo_hi"}
        assert keys <= set(x["forms"]), x["id"]
    assert "KHÔNG lấy từ golden" in r["note"]


PLAN_R = {
    "genre": "đô thị hiện đại", "style_lock": {"mode": "modern"},
    "characters": [{"zh": "林凡", "vi": "Lâm Phàm", "gender": "nam"},
                   {"zh": "苏晴", "vi": "Tô Tình", "gender": "nữ"},
                   {"zh": "王婆", "vi": "Vương Bà", "gender": "nữ"},
                   {"zh": "林小雨", "vi": "Lâm Tiểu Vũ", "gender": "nữ", "role": "Em gái ruột của Lâm Phàm"},
                   {"zh": "张三", "vi": "Trương Tam", "gender": "nam"},
                   {"zh": "李四", "vi": "Lý Tứ", "gender": "?"}],
    "relationship_matrix": {
        "Lâm Phàm": {"Vương Bà": "Cháu nội và bà nội, sống cùng nhau", "Tô Tình": "Vợ chồng mới cưới",
                     "Lý Tứ": "Sư phụ"},
        "张三—林凡": "Bạn thân từ nhỏ, đồng đội",
        "Tô Tình": {"Lý Tứ": "Người yêu cũ"},
    },
    "pronouns": [{"who": "Lâm Phàm", "to": "Tô Tình", "self": "tôi", "call": "cô"}],   # plan có sẵn (cố ý khác luật)
    "terms": [], "scenes": [],
}


def _pairs(pv):
    return {(p["who"], p["to"]): (p["self"], p["call"], p.get("src")) for p in pv.pairs.values()}


def test_pair_rules_fill_only_empty_never_override():
    pv = PlanView(json.loads(json.dumps(PLAN_R)))
    added = pair_rules.fill(pv)
    P = _pairs(pv)
    assert P[("Lâm Phàm", "Tô Tình")] == (["tôi"], ["cô"], None)              # plan thắng, không đè
    assert P[("Tô Tình", "Lâm Phàm")] == (["em"], ["anh"], "rule")             # chiều trống: vợ→chồng
    assert P[("Lâm Phàm", "Vương Bà")] == (["cháu"], ["bà", "bà nội"], "rule")
    assert P[("Vương Bà", "Lâm Phàm")] == (["bà"], ["cháu"], "rule")
    assert P[("Lâm Tiểu Vũ", "Lâm Phàm")] == (["em"], ["anh"], "rule")         # role «Em gái ruột của …»
    assert P[("Lâm Phàm", "Lâm Tiểu Vũ")] == (["anh"], ["em"], "rule")
    assert P[("Trương Tam", "Lâm Phàm")][2] == "rule" and "cậu" in P[("Trương Tam", "Lâm Phàm")][1]
    # mơ hồ → KHÔNG đoán: «Sư phụ» không có «của <tên>», người yêu mà thiếu giới
    assert ("Lâm Phàm", "Lý Tứ") not in P and ("Lý Tứ", "Lâm Phàm") not in P
    assert ("Tô Tình", "Lý Tứ") not in P
    assert all(p["src"] == "rule" and "quy tắc" in p["note"] for p in added)
    assert pv.plan["pronouns"] == PLAN_R["pronouns"]                           # plan dict không bị chèn


def test_pair_rules_genre_forms():
    base = {"characters": [{"zh": "师父", "vi": "Thanh Vân", "gender": "nam"},
                           {"zh": "小白", "vi": "Tiểu Bạch", "gender": "nữ", "role": "Đồ đệ của Thanh Vân"}],
            "pronouns": []}
    pv = PlanView(dict(base, style_lock={"mode": "xianxia"}))
    pair_rules.fill(pv)
    assert _pairs(pv)[("Tiểu Bạch", "Thanh Vân")][:2] == (["đệ tử", "đồ nhi"], ["sư phụ", "sư tôn"])
    assert _pairs(pv)[("Thanh Vân", "Tiểu Bạch")][:2] == (["vi sư", "ta", "bản tọa"], ["đồ nhi", "con"])
    pv2 = PlanView(dict(base, genre="đô thị tu tiên", style_lock={"mode": "mixed"}))
    assert pair_rules.genre_classes(pv2.plan, pair_rules.load_rules()) == ["modern", "xianxia"]
    sib = {"characters": [{"zh": "甲", "vi": "Giáp", "gender": "nam"}, {"zh": "乙", "vi": "Ất", "gender": "nữ"}],
           "relationship_matrix": {"Giáp": {"Ất": "Anh em ruột"}}, "pronouns": []}
    pv3 = PlanView(sib)
    assert pair_rules.fill(pv3) == []          # anh em ruột nhưng không rõ ai lớn → bỏ


def test_pipeline_pair_rules_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_PAIR_RULES", "1")
    plan = json.loads(json.dumps(PLAN_R))
    plan["pronouns"] = list(PLAN["pronouns"])
    plan["scenes"] = [{"scene_id": 1, "range": [0, 99], "characters": ["Lâm Phàm", "Vương Bà", "Tô Tình"]}]
    wd = _job(tmp_path, plan=plan, segs=[
        {"i": 0, "start": 0.0, "end": 1.5, "text": "奶奶，我饿了。"},
        {"i": 1, "start": 1.5, "end": 3.0, "text": "奶奶，我回来了。"},
        {"i": 2, "start": 3.0, "end": 4.5, "text": "苏晴，我等你很久了。"}])
    _nmt(monkeypatch, table={0: "Bà ơi, tôi đói rồi.", 1: "Bà ơi, tôi về rồi.", 2: "Tô Tình, tôi đợi cậu lâu rồi."})
    calls = []

    def fake(messages, model=None, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        calls.append(user)
        lines = []
        for x in _items(user):
            vi = x["nhap"]
            if "loi" in x:
                vi = vi.replace("tôi", "cháu")
            elif x["i"] == 2:
                vi = "Tô Tình, anh đợi em lâu rồi."
            spk, to = (("Lâm Phàm", "Vương Bà") if x["i"] < 2 else ("Lâm Phàm", "Tô Tình"))
            lines.append({"i": x["i"], "vi": vi, "spk": spk, "to": to, "conf": 0.9})
        return json.dumps({"lines": lines}, ensure_ascii=False)
    monkeypatch.setattr(llm, "chat", fake)
    g_before = json.loads((wd / "glossary.json").read_text(encoding="utf-8"))["pronouns"]
    res = pipeline.run(wd, "Tiếng Việt")
    assert "Lâm Phàm → Vương Bà: tự xưng «cháu» · gọi «bà» hoặc «bà nội» (suy theo quan hệ" in calls[0]
    assert res["review"]["pair_rules"]["n_added"] >= 2
    assert res["repair"] and res["repair"][0]["sent"] == 2                    # câu lệch cặp rule bị cờ → vá
    rows = _read(wd)
    assert rows[0]["vi"] == "Bà ơi, cháu đói rồi." and res["status"] == "done"
    assert json.loads((wd / "glossary.json").read_text(encoding="utf-8"))["pronouns"] == g_before
    inf = json.loads((wd / "plan_inferred.json").read_text(encoding="utf-8"))
    assert {p["src"] for p in inf["pairs"]} == {"rule"}
    sg = json.loads((wd / "plan_suggestions.json").read_text(encoding="utf-8"))
    assert sg["inferred_pairs"] and sg["missing_pairs"] == []


# ───────────────────────── Bước 6: bổ sung cặp thiếu ─────────────────────────

PLAN_F = dict(PLAN, characters=PLAN["characters"] + [{"zh": "王婆", "vi": "Vương Bà", "gender": "nữ",
                                                       "role": "bà hàng xóm"}])
SEGS_F = [{"i": k, "start": k * 1.5, "end": k * 1.5 + 1.4, "text": f"王婆，我来了{k}。"} for k in range(4)] + \
    [{"i": 4, "start": 6.0, "end": 7.4, "text": "苏晴，谢谢你。"}]
NMT_F = {0: "Bà Vương, tôi tới rồi.", 1: "Bà Vương, cháu tới rồi.", 2: "Bà Vương, tôi tới đây.",
         3: "Bà Vương, cháu đến rồi.", 4: "Tô Tình, cảm ơn em."}


class FillFake:
    def __init__(self, fill_reply):
        self.fill_reply = fill_reply
        self.fill_calls: list[str] = []
        self.calls: list[str] = []

    def __call__(self, messages, model=None, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        if "chuyên gia xưng hô" in system:
            self.fill_calls.append(user)
            r = self.fill_reply
            if isinstance(r, Exception):
                raise r
            return r
        self.calls.append(system[:30])
        lines = []
        for x in _items(user):
            vi = x["nhap"]
            if "loi" in x:
                vi = vi.replace("tôi", "cháu")
            spk, to = ("Lâm Phàm", "Vương Bà") if x["i"] < 4 else ("Lâm Phàm", "Tô Tình")
            lines.append({"i": x["i"], "vi": vi, "spk": spk, "to": to, "conf": 0.9})
        return json.dumps({"lines": lines}, ensure_ascii=False)


GOOD_FILL = json.dumps({"pairs": [{"who": "Lâm Phàm", "to": "Vương Bà", "self": "cháu", "call": "bà", "conf": 0.9,
                                   "note": "hàng xóm lớn tuổi"},
                                  {"who": "Tô Tình", "to": "Vương Bà", "self": "x", "call": "y", "conf": 0.9}]},
                       ensure_ascii=False)


def test_pair_fill_one_call_adds_pair_and_repairs(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_PAIR_FILL", "1")
    monkeypatch.setenv("SIMPLE_PAIR_FILL_MODEL", "chatgpt/mini-test")
    wd = _job(tmp_path, plan=PLAN_F, segs=SEGS_F)
    _nmt(monkeypatch, table=NMT_F)
    fake = FillFake(GOOD_FILL)
    seen_models = []
    orig = fake.__call__

    def spy(messages, model=None, **kw):
        seen_models.append(model)
        return orig(messages, model, **kw)
    monkeypatch.setattr(llm, "chat", spy)
    res = pipeline.run(wd, "Tiếng Việt")
    assert len(fake.fill_calls) == 1 and "chatgpt/mini-test" in seen_models
    u = fake.fill_calls[0]
    assert "CẶP 1: Lâm Phàm → Vương Bà (4 câu)" in u and "Bà Vương, tôi tới rồi." in u and "bà hàng xóm" in u
    pf = res["review"]["pair_fill"]
    assert pf == {"asked": 1, "added": 1, "rows": 4}                         # cặp không được hỏi → bỏ
    assert res["repair"][0]["sent"] == 2                                     # chỉ 2 câu «tôi» lệch cặp mới
    rows = _read(wd)
    assert rows[0]["vi"] == "Bà Vương, cháu tới rồi." and res["status"] == "done"
    assert json.loads((wd / "glossary.json").read_text(encoding="utf-8"))["pronouns"] == PLAN["pronouns"]
    inf = json.loads((wd / "plan_inferred.json").read_text(encoding="utf-8"))
    assert [(p["who"], p["to"], p["src"], p["conf"]) for p in inf["pairs"]] == \
        [("Lâm Phàm", "Vương Bà", "llm_fill", 0.9)]
    sg = json.loads((wd / "plan_suggestions.json").read_text(encoding="utf-8"))
    assert sg["inferred_pairs"][0]["src"] == "llm_fill" and sg["missing_pairs"] == []
    # chạy lại: dùng lại cặp llm_fill cũ, KHÔNG gọi lại model bổ sung
    fake2 = FillFake(GOOD_FILL)
    monkeypatch.setattr(llm, "chat", fake2)
    res2 = pipeline.run(wd, "Tiếng Việt", rereview=True)
    assert fake2.fill_calls == [] and res2["review"]["pair_fill_prev"] == 1


@pytest.mark.parametrize("reply", [RuntimeError("Aurora/ChatGPT 429 (no-retry) too many requests"),
                                   "", '{"pairs": "rác"', '{"pairs":[{"who":"Lâm Phàm","to":"Vương Bà",'
                                   '"self":"cháu","call":"bà","conf":0.2}]}'])
def test_pair_fill_error_or_low_conf_skipped_no_park(tmp_path, monkeypatch, fastclock, reply):
    monkeypatch.setenv("SIMPLE_PAIR_FILL", "1")
    monkeypatch.setenv("REVIEW_CLOUD_RETRY", "4")
    wd = _job(tmp_path, plan=PLAN_F, segs=SEGS_F)
    _nmt(monkeypatch, table=NMT_F)
    fake = FillFake(reply)
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert len(fake.fill_calls) == 1                                         # 429 → KHÔNG thử lại
    assert res["review"]["pair_fill"]["added"] == 0 and res["status"] == "done"
    assert res["repair"] == []
    sg = json.loads((wd / "plan_suggestions.json").read_text(encoding="utf-8"))
    assert [(p["who"], p["to"], p["n"]) for p in sg["missing_pairs"]] == [("Lâm Phàm", "Vương Bà", 4)]


def test_pair_fill_below_min_no_call(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_PAIR_FILL", "1")
    monkeypatch.setenv("SIMPLE_PAIR_FILL_MIN", "5")
    wd = _job(tmp_path, plan=PLAN_F, segs=SEGS_F)
    _nmt(monkeypatch, table=NMT_F)
    fake = FillFake(GOOD_FILL)
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    assert fake.fill_calls == [] and res["review"]["pair_fill"] == {"asked": 0, "added": 0, "rows": 0}


# ───────────────────────── Bước 8a: học lại ─────────────────────────

@pytest.mark.parametrize("learn", ["0", "1"])
def test_learn_inferred_only_when_flag(tmp_path, monkeypatch, learn):
    monkeypatch.setenv("SIMPLE_PAIR_FILL", "1")
    monkeypatch.setenv("SIMPLE_LEARN_INFERRED", learn)
    saved = []
    monkeypatch.setattr(series_glossary, "save_from",
                        lambda wd, **k: saved.append(json.loads((Path(wd) / "glossary.json").read_text(
                            encoding="utf-8"))["pronouns"]) or {})
    wd = _job(tmp_path, plan=PLAN_F, segs=SEGS_F)
    _nmt(monkeypatch, table=NMT_F)
    monkeypatch.setattr(llm, "chat", FillFake(GOOD_FILL))
    res = pipeline.run(wd, "Tiếng Việt")
    assert res["status"] == "done" and saved
    prs = json.loads((wd / "glossary.json").read_text(encoding="utf-8"))["pronouns"]
    if learn == "1":
        assert prs[:2] == PLAN["pronouns"] and prs[2]["src"] == "llm_fill" and prs[2]["self"] == "cháu"
        assert saved[-1] == prs                                              # bảng bộ thấy cặp mới
    else:
        assert prs == PLAN["pronouns"] and saved[-1] == PLAN["pronouns"]


def test_learn_inferred_not_on_park(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_PAIR_RULES", "1")
    monkeypatch.setenv("SIMPLE_LEARN_INFERRED", "1")
    plan = json.loads(json.dumps(PLAN_R))
    wd = _job(tmp_path, plan=plan)
    _nmt(monkeypatch)
    monkeypatch.setattr(llm, "chat", FakeLLM("empty"))
    assert pipeline.run(wd, "Tiếng Việt")["status"] == "park"
    assert json.loads((wd / "glossary.json").read_text(encoding="utf-8"))["pronouns"] == PLAN_R["pronouns"]


# ───────────────────────── Bước 8b/8c: tools chỉ đọc ─────────────────────────

def _tree_hash(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.md5(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


def _tool(name):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(ROOT / "tools"))


def test_collect_manual_edits_readonly(tmp_path, monkeypatch):
    (tmp_path / "w").mkdir()
    wd = _job(tmp_path / "w")
    _nmt(monkeypatch)
    monkeypatch.setattr(llm, "chat", FakeLLM("good"))
    pipeline.run(wd, "Tiếng Việt")
    rows = json.loads((wd / "script.json").read_text(encoding="utf-8"))
    rows[0]["vi"], rows[0]["manual"] = "Tô Tình, anh chờ em mãi.", 1          # sửa tay thật
    rows[1]["manual"] = 1                                                     # chỉ dọn zh — vi không đổi
    rows[4].update(vi="Cảm ơn anh nhé.", manual=True, alt_pick=1)
    (wd / "script.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    before = _tree_hash(wd)
    C = _tool("collect_manual_edits")
    out = tmp_path / "cand"
    assert C.main(["--work", str(tmp_path / "w"), "--out", str(out)]) == 0
    assert _tree_hash(wd) == before                                           # chỉ đọc
    lines = [json.loads(l) for l in (out / "j1" / "lines.gold.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["i"] for x in lines] == [0, 4]
    l0 = lines[0]
    assert l0["vi_system"] == "Tô Tình, anh đã đợi em lâu rồi." and l0["vi_gold"] == "Tô Tình, anh chờ em mãi."
    assert l0["spk"] == "Lâm Phàm" and l0["pron_expected"]["self"] == ["anh"] and l0["evidence"] == "trace"
    assert set(l0) >= {"i", "scene", "zh", "vi_system", "spk", "to", "pron_expected", "vi_gold", "error_tags", "note"}
    meta = json.loads((out / "j1" / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "candidate" and meta["n_lines"] == 2
    assert C.main(["--work", str(tmp_path / "w"), "--out", str(wd / "x")]) == 2       # từ chối ghi trong work


def test_simple_stats_readonly(tmp_path, monkeypatch, capsys):
    (tmp_path / "w").mkdir()
    wd = _job(tmp_path / "w")
    _nmt(monkeypatch)
    monkeypatch.setattr(llm, "chat", FakeLLM("wrong"))
    monkeypatch.setenv("SIMPLE_REPAIR_ROUNDS", "0")
    pipeline.run(wd, "Tiếng Việt")
    before = _tree_hash(wd)
    S = _tool("simple_stats")
    js = tmp_path / "stats.json"
    assert S.main(["--work", str(tmp_path / "w"), "--json", str(js)]) == 0
    assert _tree_hash(wd) == before
    st = json.loads(js.read_text(encoding="utf-8"))[0]
    assert st["job"] == "j1" and st["gate"] in ("hold", "done") and st["flags_by_kind"].get("pronoun", 0) >= 1
    out = capsys.readouterr().out
    assert "| j1 |" in out and "Tổng 1 job" in out


# ───────────────────────── Bước 1: nguồn ZH ─────────────────────────

def _box(cx, cy, w=200, h=40):
    return [[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]]


def test_detect_uses_render_cache(tmp_path):
    (tmp_path / "hardsub_track.json").write_text('{"v":1,"bands":[]}', encoding="utf-8")
    assert zh_source.detect(tmp_path) == {"has_hardsub": False, "method": "cache:hardsub_track.json"}
    (tmp_path / "hardsub_track.json").unlink()
    (tmp_path / "hardsub_band.json").write_text('{"band":[0.8,0.9]}', encoding="utf-8")
    assert zh_source.detect(tmp_path)["has_hardsub"] is True


@pytest.mark.parametrize("sub_every,expect", [(1, True), (2, True), (10, False)])
def test_detect_sampling_fake_frames(tmp_path, sub_every, expect):
    """Khung giả 1280×720: chữ thoại giữa-dưới (vùng «sub») ở mỗi `sub_every` khung + watermark góc mọi khung."""
    grabbed = []

    def grab(video, t, out):
        grabbed.append(t)
        out.write_bytes(b"x")
        return out

    def ocr(img):
        k = int(Path(img).stem.split("_")[1])
        res = [(_box(100, 40), "抖音超高清", 0.9)]                           # watermark góc trên
        if k % sub_every == 0:
            res.append((_box(640, 650), "你到底想怎么样", 0.95))
        return res
    d = zh_source.detect(tmp_path, n=20, min_ratio=0.3, find_video=lambda wd: tmp_path / "v.mp4",
                         probe_dur=lambda v: 1000.0, grab=grab, ocr=ocr, img_size=lambda i: (1280, 720))
    assert d["has_hardsub"] is expect and d["n_samples"] == 20 and d["method"] == "sample_ocr"
    assert grabbed[0] == 25.0 and grabbed[-1] == 975.0                       # rải đều cả phim
    assert not (tmp_path / "_zhsrc_frames").exists()


def test_detect_no_video_keeps_asr(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    assert zh_source.detect(tmp_path)["has_hardsub"] is False


def test_prepare_ocr_wins_when_matched_asr_keeps_timing(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_ZH_NAMEFIX", "0")
    segs = [{"i": 0, "start": 0.0, "end": 2.0, "text": "我今天去学小"},          # ASR sai 校 → 小
            {"i": 1, "start": 2.0, "end": 4.0, "text": "你好"},
            {"i": 2, "start": 9.0, "end": 10.0, "text": "没有字幕"}]
    cues = [{"start": 0.1, "end": 1.9, "zh": "我今天去学校"}, {"start": 2.0, "end": 4.0, "zh": "完全不同的一句话啊"}]
    out, rep = zh_source.prepare(tmp_path, segs, detect_fn=lambda wd: {"has_hardsub": True, "method": "fake"},
                                 cues_fn=lambda wd: cues)
    assert [s["text"] for s in out] == ["我今天去学校", "你好", "没有字幕"]
    assert [s["zh_src"] for s in out] == ["ocr", "asr", "asr"] and out[0]["zh_asr"] == "我今天去学小"
    assert [(s["start"], s["end"]) for s in out] == [(0.0, 2.0), (2.0, 4.0), (9.0, 10.0)]
    assert rep["n_ocr"] == 1 and (tmp_path / "zh_source.json").is_file() and (tmp_path / "asr.zhsource.json").is_file()
    assert segs[0]["text"] == "我今天去学小"                                   # đầu vào không bị sửa


def test_fix_names_guarded():
    names = ["林凡天", "苏晴", "天墟宗", "林凡地"]
    segs = [{"i": 0, "text": "林帆天你来了"},          # lệch 1 chữ, đồng âm (帆/凡) → sửa
            {"i": 1, "text": "林凡天和林凡天"},        # đã đúng
            {"i": 2, "text": "苏青你好"},              # tên 2 chữ → không đụng
            {"i": 3, "text": "林饭店到了"},            # lệch 2 chữ → không
            {"i": 4, "text": "林凡地来了"},            # cửa sổ là tên khác đã biết → không
            {"i": 5, "text": "林凡山来了"}]            # lệch 1 chữ nhưng KHÔNG đồng âm → không (có pinyin)
    py = {"帆": "fan", "凡": "fan", "山": "shan", "天": "tian", "地": "di"}.get
    log = zh_source.fix_names(segs, names, min_score=0.75, pinyin=lambda ch: py(ch, ch))
    assert [s["text"] for s in segs] == ["林凡天你来了", "林凡天和林凡天", "苏青你好", "林饭店到了", "林凡地来了",
                                        "林凡山来了"]
    assert [(x["i"], x["from"], x["to"]) for x in log] == [(0, "林帆天", "林凡天")]
    assert segs[0]["zh_src"] == "asr+namefix" and segs[0]["zh_asr"] == "林帆天你来了"
    # không có pypinyin: KHÔNG sửa gì (review agent D — trước đây tên 4 chữ lệch 1 chữ bất kỳ bị thay)
    s2 = [{"i": 0, "text": "林帆天来"}, {"i": 1, "text": "天虚宗门来"}]
    log2 = zh_source.fix_names(s2, ["林凡天", "天墟宗门"], min_score=0.75, pinyin=False)
    assert log2 == [] and s2[0]["text"] == "林帆天来" and s2[1]["text"] == "天虚宗门来"


def test_pipeline_zh_source_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_ZH_SOURCE", "1")
    segs = [dict(s) for s in SEGS]
    segs[0]["text"] = "苏晴，我等你很久了吗。"
    wd = _job(tmp_path, segs=segs)
    asr_before = (wd / "asr.json").read_bytes()
    monkeypatch.setattr(zh_source, "detect", lambda wd: {"has_hardsub": True, "method": "fake"})
    import engines.asr.hardsub_asr as hardsub_asr
    monkeypatch.setattr(hardsub_asr, "extract_hardsub_cues",
                        lambda wd, **k: [{"start": 0.0, "end": 1.5, "zh": "苏晴，我等你很久了。"}])
    _nmt(monkeypatch)
    fake = FakeLLM("good")
    monkeypatch.setattr(llm, "chat", fake)
    res = pipeline.run(wd, "Tiếng Việt")
    rows = _read(wd)
    assert rows[0]["zh"] == "苏晴，我等你很久了。" and rows[0]["zh_src"] == "ocr"
    assert rows[1]["zh_src"] == "asr" and res["status"] == "done"
    assert (wd / "asr.json").read_bytes() == asr_before                       # asr.json KHÔNG bị ghi
    assert json.loads((wd / "hardsub_cues.json").read_text(encoding="utf-8"))[0]["zh"] == "苏晴，我等你很久了。"
    assert any(t["step"] == "zh_source" for t in _trace(wd))


# ───────────────────────── cờ tắt = y hệt trước ─────────────────────────

def test_flags_off_new_modules_never_called(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("module mới bị gọi khi cờ tắt")
    monkeypatch.setattr(pair_rules, "fill", boom)
    monkeypatch.setattr(pair_fill, "run", boom)
    monkeypatch.setattr(zh_source, "prepare", boom)
    monkeypatch.setattr(pipeline, "_learn_inferred", boom)
    monkeypatch.setattr(pipeline, "_write_inferred", boom)
    for k in ("SIMPLE_PAIR_RULES", "SIMPLE_PAIR_FILL", "SIMPLE_ZH_SOURCE", "SIMPLE_LEARN_INFERRED"):
        assert os.environ.get(k) is None
    wd = _job(tmp_path)
    _nmt(monkeypatch)
    monkeypatch.setattr(llm, "chat", FakeLLM("wrong"))
    res = pipeline.run(wd, "Tiếng Việt")
    assert set(res["review"]) == {"n_todo", "n_ok", "n_fail_lots", "n_left", "lots", "suggest"}
    assert not (wd / "plan_inferred.json").exists() and not (wd / "zh_source.json").exists()
    assert "inferred_pairs" not in json.loads((wd / "plan_suggestions.json").read_text(encoding="utf-8")) \
        if (wd / "plan_suggestions.json").exists() else True


def _backup_dir() -> Path | None:
    for c in (os.environ.get("TS_NEXT_BACKUP"), str(ROOT / ".backup_edit_20261112_agentB")):
        if c and (Path(c) / "translate_simple" / "pipeline.py").is_file():
            return Path(c)
    return None


def _orig_pipeline(tmp_path) -> object:
    """Dựng gói translate_simple GỐC = bản hiện tại + đè các file trong backup (file agent B đã sửa)."""
    bk = _backup_dir()
    base = tmp_path / "orig_pkg"
    pkg = base / "translate_simple_orig"
    shutil.copytree(ROOT / "translate_simple", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    for f in (bk / "translate_simple").glob("*.py"):
        shutil.copyfile(f, pkg / f.name)
    (base / "data" / "lexicon").mkdir(parents=True)
    lx = bk / "data" / "lexicon" / "idioms_zh_vi.json"
    shutil.copyfile(lx if lx.is_file() else ROOT / "data" / "lexicon" / "idioms_zh_vi.json",
                    base / "data" / "lexicon" / "idioms_zh_vi.json")
    sys.path.insert(0, str(base))
    try:
        for m in [m for m in sys.modules if m.startswith("translate_simple_orig")]:
            del sys.modules[m]
        return importlib.import_module("translate_simple_orig.pipeline")
    finally:
        sys.path.remove(str(base))


_VOLATILE = {"applied_at", "updated_at", "started_at", "finished_at", "at", "t_total", "generated", "ts", "plan_start", "plan_end",
             "nmt_start", "nmt_end", "t_plan", "t_nmt", "created_at"}


def _strip(o):
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if k not in _VOLATILE}
    if isinstance(o, list):
        return [_strip(x) for x in o]
    return o


def _snapshot(wd: Path, res: dict, prompts: list) -> dict:
    """Ảnh chụp đầu ra; đường dẫn job (khác nhau giữa 2 lần chạy) thay bằng <WD>."""
    raw = _snapshot0(wd, res, prompts)
    w1, w2 = str(wd).encode(), json.dumps(str(wd))[1:-1].encode()
    return {k: (v.replace(w2, b"<WD>").replace(w1, b"<WD>") if isinstance(v, bytes) else v) for k, v in raw.items()}


def _snapshot0(wd: Path, res: dict, prompts: list) -> dict:
    snap = {"prompts": json.dumps(prompts, ensure_ascii=False).encode(),
            "res": json.dumps(_strip({k: res.get(k) for k in ("status", "gate", "review", "repair", "model", "draft",
                                                              "script_path")}), ensure_ascii=False).encode()}
    for f in sorted(wd.iterdir()):
        if not f.is_file() or f.name.startswith("_"):
            continue
        b = f.read_bytes()
        if f.suffix == ".jsonl":
            b = json.dumps([_strip(json.loads(l)) for l in b.decode("utf-8").splitlines() if l.strip()],
                           ensure_ascii=False).encode()
        elif f.name in ("manifest.json", "plan_suggestions.json", "glossary.json"):
            b = json.dumps(_strip(json.loads(b)), ensure_ascii=False, sort_keys=True).encode()
        snap[f.name] = b
    return snap


def _scenarios():
    def post_pairs(d, items, n):
        for x in d["lines"]:
            if x["i"] in (1, 2):
                x.update(spk="Vương Bà", to="Lâm Phàm", conf=0.9)
        d["new_pairs"] = [{"who": "Vương Bà", "to": "Lâm Phàm", "self": "bà", "call": "cậu", "i": 1}]
        return d
    plan3 = dict(PLAN, characters=PLAN["characters"] + [{"zh": "王婆", "vi": "Vương Bà", "gender": "nữ"}],
                 relationship_matrix={"Vương Bà": {"Lâm Phàm": "Bà nội và cháu nội"}})
    manual = [{"i": 4, "start": 6.0, "end": 7.5, "zh": "谢谢你。", "vi": "Đa tạ huynh.", "manual": 1}]
    return [
        ("good", dict(), lambda: FakeLLM("good"), {}),
        ("wrong_repair", dict(), lambda: FakeLLM("wrong"), {}),
        ("hold", dict(), lambda: FakeLLM("wrong"), {"SIMPLE_GATE_FLAG_MAX": "0"}),
        ("park", dict(), lambda: FakeLLM("empty"), {}),
        ("manual", dict(script=manual), lambda: FakeLLM("good"), {}),
        ("echo_skip", dict(), lambda: FakeLLM(script=["skip_last", "echo"]), {}),
        ("pairs_matrix", dict(plan=plan3), lambda: _ScriptedPairs(post_pairs), {}),
        ("idiom_golden", dict(segs=SEGS + [{"i": 5, "start": 7.5, "end": 9.0, "text": "你没大没小！我去。"}]),
         lambda: FakeLLM("good"), {}),
    ]


class _ScriptedPairs(FakeLLM):
    def __init__(self, post):
        super().__init__("good")
        self.post = post

    def __call__(self, messages, model=None, **kw):
        out = super().__call__(messages, model, **kw)
        d = json.loads(out)
        return json.dumps(self.post(d, _items(messages[1]["content"]), len(self.calls)), ensure_ascii=False)


@pytest.mark.parametrize("explicit_zero", [False, True])
def test_flags_off_byte_identical_to_backup(tmp_path, monkeypatch, explicit_zero):
    """Cờ tắt (mặc định hoặc đặt «0» tường minh): bản mới cho kết quả TỪNG BYTE như bản gốc (backup trước khi sửa)
    — prompt gửi LLM, script.json, script.display.json, sub.vi.srt, glossary.json, style_lock, trace (bỏ ts),
    manifest + plan_suggestions (bỏ mốc giờ), kết quả run."""
    if _backup_dir() is None:
        pytest.skip("không có backup bản gốc (.backup_edit_20261112_agentB hoặc TS_NEXT_BACKUP)")
    P0 = _orig_pipeline(tmp_path)
    if explicit_zero:
        for k in ("SIMPLE_LEXICON_EXCLUDE_GOLDEN", "SIMPLE_PAIR_RULES", "SIMPLE_PAIR_FILL", "SIMPLE_LEARN_INFERRED",
                  "SIMPLE_ZH_SOURCE"):
            monkeypatch.setenv(k, "0")
    n = 0
    for name, jkw, mk, env in _scenarios():
        snaps = []
        for tag, P in (("orig", P0), ("new", pipeline)):
            for k, v in env.items():
                monkeypatch.setenv(k, v)
            CR.reset_gates()
            root = tmp_path / f"{name}_{tag}"
            root.mkdir()
            wd = _job(root, **jkw)
            _nmt(monkeypatch)
            fake = mk()
            prompts = []

            def spy(messages, model=None, _f=fake, **kw):
                prompts.append([m["content"] for m in messages])
                return _f(messages, model, **kw)
            monkeypatch.setattr(llm, "chat", spy)
            res = P.run(wd, "Tiếng Việt")
            snaps.append(_snapshot(wd, res, prompts))
            for k in env:
                monkeypatch.delenv(k, raising=False)
        a, b = snaps
        assert sorted(a) == sorted(b), (name, sorted(set(a) ^ set(b)))
        for k in a:
            assert a[k] == b[k], f"{name}: {k} khác"
        n += 1
    assert n == len(_scenarios())


# ───────────────────────── sửa lỗi: gỡ lặp ASR như luồng cũ ─────────────────────────

def test_simple_heals_asr_stutter_like_old_flow(tmp_path, monkeypatch):
    """Agent A (A/B f0834acf0cc0 i=542): «杀吧杀吧，» giữ lặp ở simple. Nay dùng CÙNG hàm luồng cũ
    (translate.heal_obvious_internal_stutter) cho zh (câu chưa rv, không manual) và vi (post)."""
    import engines.translate as T
    segs = [{"i": 0, "start": 0.0, "end": 1.5, "text": "杀吧杀吧，"},
            {"i": 1, "start": 1.5, "end": 3.0, "text": "哈哈哈哈。"},          # tượng thanh — luồng cũ giữ
            {"i": 2, "start": 3.0, "end": 4.5, "text": "不是不是"},            # luồng cũ cũng gỡ (2 nửa giống)
            {"i": 3, "start": 4.5, "end": 6.0, "text": "我不对不对"}]          # lặp NỘI câu không phải mẫu → giữ
    script = [{"i": 3, "start": 4.5, "end": 6.0, "zh": "我不对不对", "vi": "Tay sửa sửa.", "manual": 1}]
    wd = _job(tmp_path, segs=segs, script=script)
    _nmt(monkeypatch, table={0: "Giết đi giết đi,", 1: "Ha ha.", 2: "Không phải không phải"})
    monkeypatch.setattr(llm, "chat", FakeLLM("echo"))
    res = pipeline.run(wd, "Tiếng Việt")
    rows = _read(wd)
    old = [dict(r, zh=r.get("zh") or r.get("text")) for r in segs]
    T.apply_asr_stutter_heal(old)                                            # kỳ vọng = luồng cũ
    assert [rows[i]["zh"] for i in (0, 2)] == [old[i]["zh"] for i in (0, 2)] == ["杀吧，", "不是"]
    assert rows[1]["zh"] == T.clean_zh_spam("哈哈哈哈。") == T.heal_obvious_internal_stutter(T.clean_zh_spam("哈哈哈哈。"))
    assert rows[0]["vi"] == "Giết đi," and rows[2]["vi"] == "Không phải"
    assert rows[3]["zh"] == "我不对不对" and rows[3]["vi"] == "Tay sửa sửa."  # manual không đụng
    tr = _trace(wd)
    assert any(t["step"] == "asr_heal" and t["i"] == 0 for t in tr)
    assert any(t["step"] == "post" and t.get("why") == "gỡ lặp" and t["i"] == 0 for t in tr)
    assert res["status"] in ("done", "hold", "park")
    # câu đã rv: chạy lại không đổi zh (zh/vi không lệch)
    rows_l = json.loads((wd / "script.json").read_text(encoding="utf-8"))
    rows_l[1]["zh"] = "好好好好"
    (wd / "script.json").write_text(json.dumps(rows_l, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(llm, "chat", FakeLLM("echo"))
    pipeline.run(wd, "Tiếng Việt")
    assert _read(wd)[1]["zh"] == "好好好好"
