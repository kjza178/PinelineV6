"""Luồng dịch simple — bug tái hiện 2026-11-14 (docs/fixes/2026-11-14-translate-simple-r1.md).

R1.1 «Soát lại» khi cloud chết không được báo xong giả.
R1.2 Lập plan hỏng không được lưu thành glossary.json trống rồi dùng lại mãi.
"""
from __future__ import annotations

import json

import pytest

import engines.translate as T
from engines.translate_simple import pipeline as P
from engines.translate_simple import stage_draft as SD
from engines.translate_simple import stage_review as SR

PLAN = {"genre": "đô thị", "characters": [{"zh": "林凡", "vi": "Lâm Phàm"}], "terms": [], "pronouns": [], "scenes": []}


@pytest.fixture
def wd(tmp_path, monkeypatch):
    for k, v in {"MT_ZH_VI": "0", "SIMPLE_WORKERS": "1", "SIMPLE_SUGGEST_PAIRS": "0", "AGENT_BRAIN": "0"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SIMPLE_REQUIRE_PLAN", raising=False)
    import engines.series_glossary as sg
    monkeypatch.setattr(sg, "apply", lambda work_dir, plan: plan)
    monkeypatch.setattr(sg, "save_from", lambda *a, **k: None)
    monkeypatch.setattr(SD, "_series_plan", lambda work_dir: None)
    d = tmp_path / "job_fix"
    d.mkdir()
    segs = [{"i": i, "start": i, "end": i + 1, "text": f"你好{i}"} for i in range(6)]
    (d / "asr.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"stages": {}}), encoding="utf-8")
    return d


def _ok_llm(calls):
    def fn(system, user, want, **kw):
        calls.append(len(want))
        return {i: {"vi": f"xin chào {i}", "spk": "?", "to": "?", "conf": 0.1, "ok": False} for i in want}
    return fn


def _dead_llm(calls):
    def fn(system, user, want, **kw):
        calls.append(len(want))
        raise RuntimeError("429 rate limit")
    return fn


def _stage(d):
    return json.loads((d / "manifest.json").read_text(encoding="utf-8"))["stages"]["translate"]


# ───────────── R1.1 ─────────────

def test_rereview_with_dead_cloud_is_not_reported_as_reviewed(wd, monkeypatch):
    monkeypatch.setattr(T, "build_plan", lambda *a, **k: dict(PLAN))
    calls: list = []
    monkeypatch.setattr(SR, "call_llm", _ok_llm(calls))
    r1 = P.run(wd)
    assert r1["status"] == "done" and r1["review"]["n_ok"] == 6

    calls.clear()
    monkeypatch.setattr(SR, "call_llm", _dead_llm(calls))
    r2 = P.run(wd, rereview=True)
    rv = r2["review"]
    assert rv["n_todo"] == 6 and rv["n_ok"] == 0 and rv["n_left"] == 6      # trước fix: n_ok=6, n_left=0
    assert len(calls) >= 2, "lô lỗi phải được thử lại (trước fix: 1 lần gọi, không thử lại)"
    assert r2["rereview_left"] == 6
    st = _stage(wd)
    assert "SOÁT LẠI CHƯA XONG" in st["cur_text"] and st["simple"]["rereview_left"] == 6
    # bản dịch cũ (đã soát ở lượt 1) được giữ nguyên
    rows = json.loads((wd / "script.json").read_text(encoding="utf-8"))
    assert [r["vi"] for r in rows] == [f"xin chào {i}" for i in range(6)]


def test_rereview_success_has_no_warning(wd, monkeypatch):
    monkeypatch.setattr(T, "build_plan", lambda *a, **k: dict(PLAN))
    monkeypatch.setattr(SR, "call_llm", _ok_llm([]))
    P.run(wd)
    r2 = P.run(wd, rereview=True)
    assert r2["status"] == "done" and r2["review"]["n_ok"] == 6 and "rereview_left" not in r2
    assert "SOÁT LẠI CHƯA XONG" not in _stage(wd)["cur_text"]


def test_first_review_stats_count_only_rows_reviewed_now(wd, monkeypatch):
    """Lượt đầu, cloud chết hẳn → n_ok=0 (pipeline bỏ bước vá), cổng park."""
    monkeypatch.setattr(T, "build_plan", lambda *a, **k: dict(PLAN))
    monkeypatch.setattr(SR, "call_llm", _dead_llm([]))
    r = P.run(wd)
    assert r["status"] == "park" and r["review"]["n_ok"] == 0 and r["review"]["n_left"] == 6


# ───────────── R1.2 ─────────────

def test_failed_plan_is_not_persisted_and_job_parks(wd, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("429 lúc lập plan")
    calls: list = []
    monkeypatch.setattr(T, "build_plan", boom)
    monkeypatch.setattr(SR, "call_llm", _ok_llm(calls))
    r = P.run(wd)
    assert r["status"] == "park" and "bảng nhân vật" in r["gate"]["reasons"][0]
    assert not (wd / "glossary.json").exists(), "plan hỏng không được ghi ra glossary.json"
    assert calls == [], "không soát khi chưa có bảng nhân vật (câu soát lúc này sẽ không được soát lại)"
    assert _stage(wd)["status"] == "pending" and _stage(wd)["simple"]["plan_failed"]

    # cloud ổn lại → lần chạy sau LẬP LẠI plan (trước fix: 0 lần gọi vì «dùng lại glossary.json có sẵn»)
    built: list = []
    monkeypatch.setattr(T, "build_plan", lambda *a, **k: built.append(1) or dict(PLAN))
    r = P.run(wd)
    assert built == [1] and r["status"] == "done"
    g = json.loads((wd / "glossary.json").read_text(encoding="utf-8"))
    assert g["characters"] and not SD.plan_failed(g)


def test_empty_plan_from_model_counts_as_failed(wd, monkeypatch):
    monkeypatch.setattr(T, "build_plan", lambda *a, **k: {"genre": "", "characters": [], "terms": [], "pronouns": []})
    monkeypatch.setattr(SR, "call_llm", _ok_llm([]))
    r = P.run(wd)
    assert r["status"] == "park" and not (wd / "glossary.json").exists()


def test_old_poisoned_glossary_is_rebuilt(wd, monkeypatch):
    """glossary.json mang dấu plan hỏng (hoặc do bản cũ để lại) không được dùng lại."""
    (wd / "glossary.json").write_text(json.dumps({"characters": [], "terms": [], "_meta": {"plan_failed": "x"}}),
                                      encoding="utf-8")
    built: list = []
    monkeypatch.setattr(T, "build_plan", lambda *a, **k: built.append(1) or dict(PLAN))
    monkeypatch.setattr(SR, "call_llm", _ok_llm([]))
    assert P.run(wd)["status"] == "done" and built == [1]


def test_require_plan_off_translates_with_temp_plan_but_still_does_not_persist(wd, monkeypatch):
    monkeypatch.setenv("SIMPLE_REQUIRE_PLAN", "0")

    def boom(*a, **k):
        raise RuntimeError("429 lúc lập plan")
    monkeypatch.setattr(T, "build_plan", boom)
    monkeypatch.setattr(SR, "call_llm", _ok_llm([]))
    r = P.run(wd)
    assert r["status"] == "done" and not (wd / "glossary.json").exists()
    assert _stage(wd)["simple"]["plan_failed"]


def test_stage_timestamps_are_iso_utc(wd, monkeypatch):
    """R1.4: started_at / finished_at của stage dịch cùng kiểu với cả hệ (ISO UTC)."""
    from core.timeutil import to_epoch
    monkeypatch.setattr(T, "build_plan", lambda *a, **k: dict(PLAN))
    monkeypatch.setattr(SR, "call_llm", _ok_llm([]))
    P.run(wd)
    st = _stage(wd)
    for k in ("started_at", "finished_at"):
        assert st[k].endswith("+00:00") and to_epoch(st[k])
