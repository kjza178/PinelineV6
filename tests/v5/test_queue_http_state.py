# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import core.film_db as film_db
import core.job_queue as jq


def test_http_queue_state_batch_and_current_detail(tmp_path, monkeypatch):
    monkeypatch.setattr(film_db, "WORK", tmp_path)
    monkeypatch.setattr(film_db, "DB_PATH", tmp_path / "films.db")
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)

    for jid, title, gi in [("p1", "Bộ X Phần 1", 1), ("p2", "Bộ X Phần 2", 2)]:
        wd = tmp_path / jid
        wd.mkdir()
        (wd / "manifest.json").write_text(
            json.dumps({
                "title_vi": title,
                "request": {
                    "title": title,
                    "group_id": "gx",
                    "group_title": "Bộ X",
                    "group_index": gi,
                },
                "stages": {
                    "download": {"status": "done"},
                    "tts": {
                        "status": "running" if jid == "p1" else "pending",
                        "progress": 40 if jid == "p1" else None,
                        "started_at": 1_700_000_000.0,
                    },
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    film_db.sync()
    items = jq.build_queue_items(["p2", "p1"])
    assert [x["job_id"] for x in items] == ["p2", "p1"]
    assert items[0]["season"] == 2
    assert items[1]["season"] == 1

    detail = jq.current_detail("p1", work_root=tmp_path)
    assert detail and detail.get("stage") == "tts"
    assert detail.get("progress") == 40

    # Simulate pending queue without starting worker thread
    with jq._lock:
        jq._q[:] = ["p2"]
        jq._current = "p1"
    try:
        st = jq.http_queue_state(work_root=tmp_path)
        assert st["current"] == "p1"
        assert st["current_detail"]["stage"] == "tts"
        assert st["queue_items"][0]["job_id"] == "p2"
        assert st["queue_labels"]
    finally:
        with jq._lock:
            jq._q.clear()
            jq._current = None


def test_queue_attach_routes_registered():
    from pineline.server.routes import jobs, queue

    assert len(queue._ROUTES) >= 11
    assert len(jobs._ROUTES) >= 40
    paths = {p for _, p, _ in queue._ROUTES}
    assert "/api/queue/jobs" in paths
    assert "/api/jobs/group" in paths
