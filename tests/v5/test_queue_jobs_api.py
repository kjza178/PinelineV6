from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server


def _manifest(wd: Path) -> None:
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "manifest.json").write_text(
        json.dumps({"stages": {"download": {"status": "done"}}}, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(server.job_queue, "WORK_ROOT", tmp_path)
    server.job_queue._reset_for_tests()
    monkeypatch.setattr(server.job_queue, "_ensure_worker_unlocked", lambda: None)
    return TestClient(server.app, base_url="http://127.0.0.1:8801")   # Host lạ bị guard chặn 403


def test_queue_jobs_api_add_state_stop_clear(client, tmp_path):
    _manifest(tmp_path / "job-a")
    r = client.post(
        "/api/queue/jobs",
        json={"job_ids": ["job-a"], "voice": "v1", "audio_mode": "voice_bgm"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["queued"] == ["job-a"]

    st = client.get("/api/queue/jobs").json()
    assert st["queue"] == ["job-a"]
    assert st["opts"].get("voice") == "v1"

    stop = client.post("/api/queue/jobs/stop").json()
    assert stop["ok"] is True

    cleared = client.post("/api/queue/jobs/clear").json()
    assert cleared["ok"] is True
    assert cleared["queue"] == []


def test_queue_jobs_api_rejects_empty_ids(client):
    r = client.post("/api/queue/jobs", json={"job_ids": []})
    assert r.status_code == 400
    assert "chọn" in r.json()["error"]


def test_queue_jobs_api_sort_move_reorder(client, tmp_path):
    for name in ("m1", "m2", "m3"):
        _manifest(tmp_path / name)
    client.post("/api/queue/jobs", json={"job_ids": ["m1", "m2", "m3"]})

    # Test move
    r = client.post("/api/queue/jobs/move", json={"job_id": "m3", "action": "top"})
    assert r.status_code == 200
    assert r.json()["queue"] == ["m3", "m1", "m2"]

    # Test reorder
    r = client.post("/api/queue/jobs/reorder", json={"order": ["m2", "m1", "m3"]})
    assert r.status_code == 200
    assert r.json()["queue"] == ["m2", "m1", "m3"]

    # Test sort reverse
    r = client.post("/api/queue/jobs/sort", json={"by": "reverse"})
    assert r.status_code == 200
    assert r.json()["queue"] == ["m3", "m1", "m2"]

