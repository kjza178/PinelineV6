# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import core.film_db as film_db
import core.job_queue as jq
import core.job_meta as job_meta


def test_row_from_manifest_fills_series_ui(tmp_path, monkeypatch):
    monkeypatch.setattr(film_db, "WORK", tmp_path)
    monkeypatch.setattr(film_db, "DB_PATH", tmp_path / "films.db")
    jid = "job_ui1"
    wd = tmp_path / jid
    wd.mkdir()
    m = {
        "title_vi": "Thiên phú vô song Phần 2",
        "request": {
            "title": "天赋万古无双第二季",
            "group_id": "g1",
            "group_title": "Thiên phú vô song",
            "group_index": 9,  # lệch — UI season phải 2
        },
        "stages": {"download": {"status": "done", "title": "天赋万古无双第二季"}},
    }
    (wd / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    row = film_db.row_from_manifest(jid, m)
    assert row["group_index"] == 9
    assert row["season_ui"] == 2
    assert "Thiên" in (row["series_ui"] or "") or "thien" in (row["series_ui"] or "").lower() or row["series_ui"]


def test_sync_and_get_many_db_first(tmp_path, monkeypatch):
    monkeypatch.setattr(film_db, "WORK", tmp_path)
    monkeypatch.setattr(film_db, "DB_PATH", tmp_path / "films.db")
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)

    for jid, title, gi in [
        ("a", "Sa mạc Phần 1", 1),
        ("b", "Sa mạc Phần 3", 3),
        ("c", "Sa mạc Phần 2", 2),
    ]:
        wd = tmp_path / jid
        wd.mkdir()
        (wd / "manifest.json").write_text(
            json.dumps({
                "title_vi": title,
                "request": {"title": title, "group_id": "g_sm", "group_title": "Sa mạc", "group_index": gi},
                "stages": {"download": {"status": "done"}},
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    r = film_db.sync()
    assert r["total"] >= 3
    many = film_db.get_many(["c", "a", "b"])
    assert set(many) == {"a", "b", "c"}
    assert many["a"]["season_ui"] == 1
    assert many["b"]["season_ui"] == 3
    assert many["c"]["season_ui"] == 2

    info = jq.get_job_info("b")
    assert info["season"] == 3
    assert info["series"]

    batch = jq.get_jobs_info(["a", "b", "c"])
    assert batch["a"]["season"] == 1
    assert batch["c"]["season"] == 2


def test_job_meta_prefers_title_over_group_index():
    info = job_meta.extract_ui_meta(
        job_id="x",
        title_vi="Bộ ABC Phần 2",
        group_index=7,
        group_id="g",
        group_title="Bộ ABC",
    )
    assert info["season"] == 2
    assert info["season_ui"] == 2
