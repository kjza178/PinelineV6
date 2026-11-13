"""fix 2026-11-11 B2: job park waiting_review được chạy lại khi tới hạn review_retry_at."""
from __future__ import annotations

import json
import time
from pathlib import Path

import server


def _job(tmp_path: Path, agent: dict, unrev: int) -> str:
    wd = tmp_path / "j1"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"stages": {}, "agent": agent}), encoding="utf-8")
    rows = [{"i": i, "vi": "x", **({} if i < unrev else {"rv": 1})} for i in range(5)]
    (wd / "script.json").write_text(json.dumps(rows), encoding="utf-8")
    return "j1"


def test_waiting_review_not_ready_before_due(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    jid = _job(tmp_path, {"held_at": "waiting_review", "review_retry_at": time.time() + 999}, unrev=2)
    assert server._queue_park_ready(jid) is False


def test_waiting_review_ready_when_due(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    jid = _job(tmp_path, {"held_at": "waiting_review", "review_retry_at": time.time() - 1}, unrev=2)
    assert server._queue_park_ready(jid) is True


def test_waiting_review_ready_when_all_rv(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    jid = _job(tmp_path, {"held_at": "waiting_review"}, unrev=0)
    assert server._queue_park_ready(jid) is True


def test_review_failed_hold_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    jid = _job(tmp_path, {"held_at": "review_failed", "hold_after_translate": True}, unrev=2)
    assert server._queue_park_ready(jid) is False
