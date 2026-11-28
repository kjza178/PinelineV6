"""Thêm tài khoản kênh test trên máy mới (services/test_channel_setup) — data/test_channel TẠM."""
from __future__ import annotations

import json

import pytest

from services import test_channel_setup as tcs

CID = "123456789012-abc.apps.googleusercontent.com"


@pytest.fixture
def tc_dir(tmp_path, monkeypatch):
    import tools.test_channel as tc
    d = tmp_path / "test_channel"
    monkeypatch.setattr(tc, "DATA_DIR", d)
    monkeypatch.setattr(tc, "PROJECTS_FILE", d / "projects.json")
    return d


def test_add_project_from_web_or_installed_json(tc_dir):
    r = tcs.add_project("Kênh 1", json.dumps({"web": {"client_id": CID, "client_secret": "s1"}}))
    assert r == {"id": "proj_1", "name": "Kênh 1", "client_id": CID}
    r2 = tcs.add_project("", {"installed": {"client_id": "9-x.apps.googleusercontent.com", "client_secret": "s2"}})
    assert r2["id"] == "proj_2" and r2["name"] == "Project 2"
    tcs.add_project("Đổi tên", {"web": {"client_id": CID, "client_secret": "s1b"}})        # trùng client → cập nhật
    projs = json.loads((tc_dir / "projects.json").read_text(encoding="utf-8"))
    assert len(projs) == 2 and projs[0] == {"id": "proj_1", "name": "Đổi tên", "client_id": CID, "client_secret": "s1b"}
    import tools.test_channel as tc
    assert [p["id"] for p in tc.list_projects()] == ["proj_1", "proj_2"]              # tools/test_channel đọc được


@pytest.mark.parametrize("raw", ["không phải json", "{}", json.dumps({"web": {"client_id": "abc", "client_secret": "x"}})])
def test_bad_client_json(tc_dir, raw):
    with pytest.raises(ValueError):
        tcs.add_project("x", raw)
    assert not (tc_dir / "projects.json").exists()


def test_candidates_from_yt_uploader_only(tc_dir, tmp_path, monkeypatch):
    from core import machine
    up = tmp_path / "yt_uploader"
    (up / "accounts" / "a").mkdir(parents=True)
    (up / "client_secret.json").write_text(json.dumps({"installed": {"client_id": CID, "client_secret": "z"}}), encoding="utf-8")
    (up / "accounts" / "a" / "client_secret_x.json").write_text("{}", encoding="utf-8")     # hỏng → bỏ
    monkeypatch.setattr(machine, "yt_uploader_dir", lambda: up)
    c = tcs.candidates()
    assert [x["label"] for x in c] == ["client_secret.json"] and "z" not in json.dumps(c)       # không lộ secret
    assert tcs.add_from_candidate(c[0]["path"])["client_id"] == CID
    assert tcs.candidates()[0]["added"] is True
    with pytest.raises(ValueError):
        tcs.add_from_candidate(str(tmp_path / "khac.json"))


def test_remove_project(tc_dir):
    tcs.add_project("a", {"web": {"client_id": CID, "client_secret": "s"}})
    (tc_dir / "tokens").mkdir()
    (tc_dir / "tokens" / "proj_1.json").write_text("{}", encoding="utf-8")
    assert tcs.remove_project("proj_1") and not (tc_dir / "tokens" / "proj_1.json").exists()
    assert tcs.remove_project("proj_9") is False


def test_fresh_films_db_has_recheck_at(tmp_path, monkeypatch):
    """fix 2026-11-16: DB mới (máy mới) — migrate recheck_at chạy TRƯỚC khi SCHEMA tạo bảng → bảng thiếu cột,
    lần ghi kết quả test bản quyền đầu tiên lỗi «no such column: recheck_at»."""
    import core.film_db as fdb
    monkeypatch.setattr(fdb, "DB_PATH", tmp_path / "films.db")
    rec = fdb.upsert_library_block(name="Bo", status="testing", recheck_at="2026-11-16T10:00:00Z")
    assert rec["recheck_at"] == "2026-11-16T10:00:00Z"
