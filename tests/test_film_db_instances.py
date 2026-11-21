"""films.db dùng chung giữa các instance — mỗi instance chỉ đánh dấu / xoá job thuộc work root của nó
(fix 2026-11-14, docs/fixes/2026-11-14-review-fixes.md)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import core.film_db as fdb


def _job(root: Path, jid: str, title: str) -> None:
    d = root / jid
    d.mkdir(parents=True)
    st = {k: {"status": "done"} for k in ("download", "asr", "translate", "tts", "mux")}
    (d / "manifest.json").write_text(json.dumps({"title_vi": title, "request": {"title": title}, "stages": st}),
                                     encoding="utf-8")


@pytest.fixture
def two(tmp_path, monkeypatch):
    monkeypatch.setattr(fdb, "DB_PATH", tmp_path / "films.db")
    prod, v6 = tmp_path / "prod_work", tmp_path / "v6_work"
    _job(prod, "p1", "Phim prod 1")
    _job(prod, "p2", "Phim prod 2")
    _job(v6, "v1", "Phim v6")
    return prod, v6, monkeypatch


def _rows():
    return {r["job_id"]: r for r in fdb.query(limit=100)}


def test_sync_from_one_instance_does_not_mark_other_instance_jobs_missing(two):
    prod, v6, mp = two
    mp.setenv("PINELINE_WORK_ROOT", str(prod))
    fdb.sync()
    assert {j: r["work_exists"] for j, r in _rows().items()} == {"p1": 1, "p2": 1}
    mp.setenv("PINELINE_WORK_ROOT", str(v6))
    fdb.sync()                                   # trước fix: p1, p2 → work_exists=0 + event «work_removed»
    rows = _rows()
    assert rows["p1"]["work_exists"] == 1 and rows["p2"]["work_exists"] == 1 and rows["v1"]["work_exists"] == 1
    con = sqlite3.connect(fdb.DB_PATH)
    assert con.execute("SELECT COUNT(*) FROM events WHERE kind='work_removed'").fetchone()[0] == 0
    # purge-missing của v6 không được xoá lịch sử prod
    assert fdb.purge_missing()["count"] == 0
    assert set(_rows()) == {"p1", "p2", "v1"}


def test_owner_instance_still_detects_its_own_removed_jobs(two):
    prod, v6, mp = two
    mp.setenv("PINELINE_WORK_ROOT", str(prod))
    fdb.sync()
    import shutil
    shutil.rmtree(prod / "p2")
    fdb.sync()
    rows = _rows()
    assert rows["p1"]["work_exists"] == 1 and rows["p2"]["work_exists"] == 0
    r = fdb.purge_missing()
    assert r["count"] == 1 and r["ids"] == ["p2"]


def test_legacy_rows_without_owner_belong_to_default_work(two, monkeypatch):
    """Dòng tạo trước bản này (work_root NULL) = ROOT/work: instance khác không đụng."""
    prod, v6, mp = two
    con = fdb.connect()
    con.execute("INSERT INTO films(job_id, title, status, work_exists) VALUES ('old', 'Phim cũ', 'done', 1)")
    con.commit()
    con.close()
    mp.setenv("PINELINE_WORK_ROOT", str(v6))
    fdb.sync()
    assert _rows()["old"]["work_exists"] == 1
    # instance mặc định (work = ROOT/work) mới là chủ dòng cũ: giả lập ROOT/work = v6
    monkeypatch.setattr(fdb, "WORK", v6)
    monkeypatch.setattr(fdb, "_DEFAULT_WORK", v6)
    mp.delenv("PINELINE_WORK_ROOT")
    mp.delenv("WORK_DIR", raising=False)
    fdb.sync()
    assert _rows()["old"]["work_exists"] == 0
