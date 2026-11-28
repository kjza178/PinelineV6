"""Bảng nhân vật của bộ không bị tập mới âm thầm ghi đè (2026-11-15, ý tưởng llm-subs): khác bản đã chốt → giữ bản kho,
ghi <bộ>.conflicts.json, người chọn giữ / dùng bản mới / bỏ qua. Kho đặt trong thư mục tạm."""
from __future__ import annotations

import json

import pytest

import engines.series_glossary as sgl

EP1 = {"genre": "đô thị",
       "characters": [{"zh": "林凡", "vi": "Lâm Phàm", "gender": "nam", "aliases": ["凡哥"]},
                      {"zh": "苏晴", "vi": "Tô Tình", "gender": "nữ"}],
       "pronouns": [{"who": "Lâm Phàm", "to": "Tô Tình", "self": "anh", "call": "em"}],
       "terms": [{"zh": "天墟", "vi": "Thiên Khư", "type": "lock"}]}
EP2 = {"genre": "đô thị",
       "characters": [{"zh": "林凡", "vi": "Lâm Phiền", "gender": "nam", "aliases": ["小凡"]},     # tên khác
                      {"zh": "苏晴", "vi": "Tô Tình", "gender": "nam"},                           # giới khác
                      {"zh": "王婆", "vi": "Vương Bà", "gender": "nữ"}],                          # mới
       "pronouns": [{"who": "Lâm Phàm", "to": "Tô Tình", "self": "tôi", "call": "em"},          # tự xưng khác
                    {"who": "Tô Tình", "to": "Lâm Phàm", "self": "em", "call": "anh"}],         # mới
       "terms": [{"zh": "天墟", "vi": "Thiên Hư", "type": "lock"}]}                               # chữ VI khác


@pytest.fixture
def store(tmp_path, monkeypatch):
    st = tmp_path / "series_store"
    monkeypatch.setattr(sgl, "STORE", st)
    monkeypatch.setattr(sgl, "_core_of", lambda t: t)
    monkeypatch.setattr(sgl, "_hygiene_chars_terms", lambda c, t: (c, t))
    import core.film_db as film_db
    monkeypatch.setattr(film_db, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db in test")))
    events = []
    from events.bus import bus
    monkeypatch.setattr(bus, "publish", lambda t, d: events.append((t, d)))
    return {"dir": st, "events": events}


def _ep(tmp_path, name, plan, *, human=False):
    wd = tmp_path / name
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"request": {"group_title": "测试剧", "title": name}}),
                                      encoding="utf-8")
    (wd / "glossary.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    if human:
        (wd / "context_pack.json").write_text(json.dumps({"skip_plan": True}), encoding="utf-8")
    return wd


def _sg():
    return sgl.load("测试剧")


def _char(sg, zh):
    return next(c for c in sg["characters"] if c["zh"] == zh)


def test_new_episode_never_silently_overwrites_and_records_conflicts(tmp_path, store):
    assert sgl.save_from(_ep(tmp_path, "ep1", EP1))["n_conflicts_new"] == 0
    r = sgl.save_from(_ep(tmp_path, "ep2", EP2))
    assert r["n_conflicts_new"] == 4
    sg = _sg()
    assert _char(sg, "林凡")["vi"] == "Lâm Phàm" and _char(sg, "苏晴")["gender"] == "nữ"      # giữ bản đã chốt
    assert set(_char(sg, "林凡")["aliases"]) == {"凡哥", "小凡"}                                # bí danh: hợp
    assert _char(sg, "王婆")["vi"] == "Vương Bà"                                                # mục mới: thêm
    assert next(p for p in sg["pronouns"] if p["who"] == "Lâm Phàm")["self"] == "anh"
    assert len(sg["pronouns"]) == 2 and sg["terms"][0]["vi"] == "Thiên Khư"
    items = sgl.list_conflicts()
    got = {(c["kind"], c["key"], c["field"], json.dumps(c["suggested"], ensure_ascii=False)) for c in items}
    assert got == {("character", "林凡", "vi", '"Lâm Phiền"'), ("character", "苏晴", "gender", '"nam"'),
                   ("pronoun", "Lâm Phàm→Tô Tình", "self", '"tôi"'), ("term", "天墟", "vi", '"Thiên Hư"')}
    assert all(c["status"] == "open" and c["episodes"] == ["ep2"] for c in items)
    assert store["events"] and store["events"][-1][0] == "series.conflict"
    # sau đó tập mới kế thừa bản đã chốt
    plan = sgl.apply(_ep(tmp_path, "ep3x", {}), {"characters": [{"zh": "林凡", "vi": "Lâm Phiền"}]})
    assert _char(plan, "林凡")["vi"] == "Lâm Phàm"


def test_same_suggestion_again_is_merged_not_duplicated(tmp_path, store):
    sgl.save_from(_ep(tmp_path, "ep1", EP1))
    sgl.save_from(_ep(tmp_path, "ep2", EP2))
    assert sgl.save_from(_ep(tmp_path, "ep3", EP2))["n_conflicts_new"] == 0
    it = next(c for c in sgl.list_conflicts() if c["key"] == "林凡")
    assert it["count"] == 2 and it["episodes"] == ["ep2", "ep3"] and len(sgl.list_conflicts()) == 4


def test_resolve_use_new_keep_skip(tmp_path, store):
    sgl.save_from(_ep(tmp_path, "ep1", EP1))
    sgl.save_from(_ep(tmp_path, "ep2", EP2))
    by = {(c["key"], c["field"]): c for c in sgl.list_conflicts()}
    sgl.resolve_conflict("测试剧", by[("林凡", "vi")]["id"], "use_new")
    assert _char(_sg(), "林凡")["vi"] == "Lâm Phiền"
    sgl.resolve_conflict("测试剧", by[("苏晴", "gender")]["id"], "keep")
    assert _char(_sg(), "苏晴")["gender"] == "nữ"
    sgl.resolve_conflict("测试剧", by[("天墟", "vi")]["id"], "skip")
    assert _sg()["terms"][0]["vi"] == "Thiên Khư"
    assert [c["key"] for c in sgl.list_conflicts()] == ["Lâm Phàm→Tô Tình"]
    with pytest.raises(ValueError):
        sgl.resolve_conflict("测试剧", by[("林凡", "vi")]["id"], "keep")       # đã xử lý
    with pytest.raises(ValueError):
        sgl.resolve_conflict("测试剧", by[("Lâm Phàm→Tô Tình", "self")]["id"], "xoá")
    with pytest.raises(KeyError):
        sgl.resolve_conflict("测试剧", "khongco", "keep")


def test_use_new_closes_other_suggestions_for_same_field(tmp_path, store):
    sgl.save_from(_ep(tmp_path, "ep1", EP1))
    sgl.save_from(_ep(tmp_path, "ep2", EP2))
    ep3 = json.loads(json.dumps(EP2))
    ep3["characters"][0]["vi"] = "Lâm Phạm"
    sgl.save_from(_ep(tmp_path, "ep3", ep3))
    names = [c for c in sgl.list_conflicts() if c["key"] == "林凡"]
    assert {c["suggested"] for c in names} == {"Lâm Phiền", "Lâm Phạm"}
    sgl.resolve_conflict("测试剧", names[0]["id"], "use_new")
    assert not [c for c in sgl.list_conflicts() if c["key"] == "林凡"]


def test_human_reviewed_episode_overwrites_without_open_conflict(tmp_path, store):
    sgl.save_from(_ep(tmp_path, "ep1", EP1))
    r = sgl.save_from(_ep(tmp_path, "ep2", EP2, human=True))
    assert r["trusted"] and r["n_conflicts_new"] == 0
    assert _char(_sg(), "林凡")["vi"] == "Lâm Phiền"
    assert not sgl.list_conflicts() and {c["status"] for c in sgl.list_conflicts(None)} == {"auto"}


def test_empty_store_field_is_filled_not_conflict(tmp_path, store):
    ep1 = {"characters": [{"zh": "林凡", "vi": "Lâm Phàm"}], "pronouns": [], "terms": []}
    sgl.save_from(_ep(tmp_path, "ep1", ep1))
    sgl.save_from(_ep(tmp_path, "ep2", {"characters": [{"zh": "林凡", "vi": "Lâm Phàm", "gender": "nam",
                                                        "role": "nam chính"}]}))
    c = _char(_sg(), "林凡")
    assert c["gender"] == "nam" and c["role"] == "nam chính" and not sgl.list_conflicts()


def test_api_list_and_resolve(tmp_path, store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.main as M
    sgl.save_from(_ep(tmp_path, "ep1", EP1))
    sgl.save_from(_ep(tmp_path, "ep2", EP2))

    class _Inst:
        work_root = tmp_path
    app = FastAPI()
    app.include_router(M._v6_router(_Inst()))
    c = TestClient(app)
    d = c.get("/api/v6/series/conflicts").json()
    assert d["n"] == 4 and all(x["core"] == "测试剧" and x["kind_vi"] for x in d["items"])
    cid = next(x["id"] for x in d["items"] if x["key"] == "林凡")
    r = c.post("/api/v6/series/conflicts/resolve", json={"core": "测试剧", "id": cid, "action": "use_new"})
    assert r.status_code == 200 and r.json()["n_open"] == 3
    assert c.post("/api/v6/series/conflicts/resolve", json={"core": "测试剧", "id": cid, "action": "keep"}).status_code == 400
    assert c.post("/api/v6/series/conflicts/resolve", json={"core": "测试剧", "id": "x", "action": "keep"}).status_code == 404
    assert c.get("/api/v6/series/conflicts?status=all").json()["n"] == 4
