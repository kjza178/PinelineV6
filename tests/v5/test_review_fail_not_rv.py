"""fix 2026-11-11 B1/B4/B7: lô REVIEW lỗi không được đánh rv; fix ngoài lô bị bỏ; không soát lại khi đã đủ."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import engines.translate as T


def _setup(monkeypatch, tmp_path: Path, n: int = 12, fail_i: set[int] | None = None, extra_fix: dict | None = None):
    wd = tmp_path / "job"
    wd.mkdir()
    rows = [{"i": i, "start": i, "end": i + 1, "zh": f"zh{i}", "vi": f"vi{i}"} for i in range(n)]
    (wd / "script.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    (wd / "manifest.json").write_text(json.dumps({"stages": {"translate": {"status": "done"}}}), encoding="utf-8")
    fail_i = fail_i or set()

    def fake_review_batch(chunk_exp, plan_block, prev_ctx, **kw):
        ids = {r["i"] for r in chunk_exp}
        if ids & fail_i:
            raise ValueError("boom (lỗi thường, không phải hết hạn mức)")
        out = {r["i"]: r["vi"] + "*" for r in chunk_exp}
        out.update(extra_fix or {})
        return out

    monkeypatch.setattr(T, "review_batch", fake_review_batch)
    monkeypatch.setattr(T, "run_review_pass1_full_vi", lambda wd_, rows_, plan, model=None: plan)
    monkeypatch.setattr(T, "_resolve_review_model", lambda wd_, m=None: "chatgpt/test")
    monkeypatch.setattr(T, "_review_parallel_workers", lambda wd_, m: 1)
    monkeypatch.setattr(T, "_is_llama_local_model", lambda m: False)
    monkeypatch.setattr(T, "is_local", lambda m: False)
    monkeypatch.setattr(T, "unload_local", lambda m: None)
    # 11-11: TUYỆT ĐỐI không đổi preset llama thật trong test (lần trước đã restart :8083 dùng chung V2)
    import tools.preset_switcher as _ps
    def _no_switch(*a, **k):
        raise AssertionError("test không được gọi switch_preset thật")
    monkeypatch.setattr(_ps, "switch_preset", _no_switch)
    monkeypatch.setattr(T, "_cfg_get", lambda k, d=None: {"REVIEW_RETRY_BACKOFF_SEC": "0",
                                                          "AUTO_SWITCH_PRESETS": "0"}.get(k, d))
    return wd


def test_failed_chunk_not_marked_rv(monkeypatch, tmp_path):
    wd = _setup(monkeypatch, tmp_path, n=12, fail_i={5})
    with pytest.raises(RuntimeError, match="REVIEW lỗi"):
        T.run_review(wd, batch=4)
    rows = json.loads((wd / "script.json").read_text(encoding="utf-8"))
    rv = {r["i"] for r in rows if r.get("rv")}
    # lô [4..7] (chunk_exp [3..8] chứa i=5) lỗi cả 2 lượt → 4 câu đó không được rv
    assert 5 not in rv
    for r in rows:
        if r["i"] in (4, 5, 6, 7):
            assert not r.get("rv")


def test_fix_outside_chunk_ignored(monkeypatch, tmp_path):
    wd = _setup(monkeypatch, tmp_path, n=8, extra_fix={99: "HALLUCINATED", 0: "vi0-from-other"})
    T.run_review(wd, batch=4)
    rows = {r["i"]: r for r in json.loads((wd / "script.json").read_text(encoding="utf-8"))}
    assert all(r.get("rv") for r in rows.values())
    assert 99 not in rows
    # lô [4..7] có chunk_exp = [3..7] → fix cho i=0 (ngoài lô) phải bị bỏ ở lô này
    assert rows[0]["vi"] in ("vi0*", "vi0-from-other")


def test_no_rereview_when_all_rv(monkeypatch, tmp_path):
    wd = _setup(monkeypatch, tmp_path, n=4)
    rows = json.loads((wd / "script.json").read_text(encoding="utf-8"))
    for r in rows:
        r["rv"] = 1
    (wd / "script.json").write_text(json.dumps(rows), encoding="utf-8")
    called = []
    monkeypatch.setattr(T, "review_batch", lambda *a, **k: called.append(1) or {})
    res = T.run_review(wd, batch=4)
    assert not called
    assert res.get("reviewed") is True


def test_fallback_returns_none_on_error(monkeypatch):
    monkeypatch.setattr(T, "review_batch", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    monkeypatch.setattr(T, "_is_llama_local_model", lambda m: False)
    fixes, _m = T._review_chunk_with_fallback([], [], "", [], target="vi", rmodel="chatgpt/x",
                                              plan={}, exemplars_pool=[])
    assert fixes is None
