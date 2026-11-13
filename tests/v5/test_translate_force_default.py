"""TranslateReq.force default + _translate_flow passes force through (no hardcode wipe)."""
from __future__ import annotations

from pathlib import Path

import server


def test_translate_req_force_defaults_false():
    req = server.TranslateReq()
    assert req.force is False
    assert server.TranslateReq(model="x").force is False
    assert server.TranslateReq(force=True).force is True


def test_translate_flow_force_false_does_not_pass_true(monkeypatch, tmp_path: Path):
    calls: list[dict] = []

    def fake_engine(work_dir, batch=None, target=None, force=False, model=None, mode=None,
                    review_only=False, review_force=False):
        calls.append({
            "force": force,
            "review_only": review_only,
            "model": model,
            "mode": mode,
        })

    monkeypatch.setattr(server, "_translate_batch_size", lambda: 20)
    monkeypatch.setattr(server, "_run_translate_engine", fake_engine)

    server._translate_flow(tmp_path, "Tiếng Việt", "m1", None, False, mode="batch", force=False)

    assert calls, "expected draft translate call"
    assert calls[0]["force"] is False
    assert calls[0]["review_only"] is False


def test_translate_flow_force_true_passes_true(monkeypatch, tmp_path: Path):
    calls: list[dict] = []

    def fake_engine(work_dir, batch=None, target=None, force=False, model=None, mode=None,
                    review_only=False, review_force=False):
        calls.append({"force": force, "review_only": review_only})

    monkeypatch.setattr(server, "_translate_batch_size", lambda: 20)
    monkeypatch.setattr(server, "_run_translate_engine", fake_engine)

    server._translate_flow(tmp_path, "Tiếng Việt", None, None, False, mode="batch", force=True)

    assert calls and calls[0]["force"] is True


def test_translate_flow_review_only_skips_draft(monkeypatch, tmp_path: Path):
    """review_only must not call draft translate (force irrelevant)."""
    calls: list[dict] = []

    def fake_engine(work_dir, batch=None, target=None, force=False, model=None, mode=None,
                    review_only=False, review_force=False):
        calls.append({"force": force, "review_only": review_only, "model": model})

    monkeypatch.setattr(server, "_translate_batch_size", lambda: 20)
    monkeypatch.setattr(server, "_run_translate_engine", fake_engine)

    server._translate_flow(
        tmp_path, "Tiếng Việt", "draft", "review-m", True, mode="batch", force=True
    )

    assert len(calls) == 1
    assert calls[0]["review_only"] is True
    assert calls[0]["model"] == "review-m"
