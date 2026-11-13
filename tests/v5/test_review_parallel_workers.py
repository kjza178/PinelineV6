"""Studio: aurora_parallel → fan-out workers; local → 1."""
from __future__ import annotations

import json
from pathlib import Path

import engines.translate as T


def test_parallel_workers_local_is_one(monkeypatch):
    monkeypatch.setattr(T, "_cfg_get", lambda k, d=None: {
        "REVIEW_BACKUP": "aurora_parallel",
        "REVIEW_PARALLEL": "8",
    }.get(k, d))
    assert T._review_parallel_workers(None, "llama/qwen36-turbo") == 1


def test_parallel_workers_aurora(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(T, "_cfg_get", lambda k, d=None: {
        "REVIEW_BACKUP": "aurora_parallel",
        "REVIEW_PARALLEL": "0",
        "REVIEW_PARALLEL_DEFAULT": "24",
        "REVIEW_PARALLEL_MAX": "32",
    }.get(k, d))
    wd = tmp_path / "j"
    wd.mkdir()
    (wd / "manifest.json").write_text(
        json.dumps({"queue_opts": {"review_backup": "aurora_parallel"}}),
        encoding="utf-8",
    )
    assert T._review_parallel_workers(wd, "chatgpt/gpt-6") == 24


def test_parallel_workers_hard_cap(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(T, "_cfg_get", lambda k, d=None: {
        "REVIEW_BACKUP": "aurora_parallel",
        "REVIEW_PARALLEL": "99",
        "REVIEW_PARALLEL_MAX": "32",
    }.get(k, d))
    wd = tmp_path / "j"
    wd.mkdir()
    (wd / "manifest.json").write_text(
        json.dumps({"queue_opts": {"review_backup": "aurora_parallel"}}),
        encoding="utf-8",
    )
    assert T._review_parallel_workers(wd, "chatgpt/gpt-6") == 32


def test_parallel_workers_env_override(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(T, "_cfg_get", lambda k, d=None: {
        "REVIEW_BACKUP": "off",
        "REVIEW_PARALLEL": "4",
    }.get(k, d))
    wd = tmp_path / "j"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"queue_opts": {}}), encoding="utf-8")
    assert T._review_parallel_workers(wd, "chatgpt/gpt-6") == 4
