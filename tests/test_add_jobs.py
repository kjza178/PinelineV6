"""Hộp «Thêm job»: xem trước link → chọn tập → tạo (services/add_jobs) + chọn file trên máy."""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from services import add_jobs


@pytest.fixture
def srv(tmp_path, monkeypatch):
    created = []

    def _create(url, target, cfb, group=None):
        from engines.download import job_id_from_url
        jid = job_id_from_url(url)
        (tmp_path / jid).mkdir(exist_ok=True)
        (tmp_path / jid / "manifest.json").write_text(json.dumps({"request": {"url": url, **(group or {})}}), encoding="utf-8")
        created.append((url, group))
        return jid
    fake = types.SimpleNamespace(WORK_ROOT=tmp_path, _create=_create, created=created)
    monkeypatch.setattr(add_jobs, "_server", lambda: fake)
    return fake


def test_inspect_marks_existing_parts_and_cookie(srv, monkeypatch):
    import engines.playlist as pl
    from engines.bili import cookies
    parts = [{"url": "https://www.bilibili.com/video/BV1xx411c7mD", "title": "P1", "duration": 60},
             {"url": "https://www.bilibili.com/video/BV1xx411c7mD?p=2", "title": "P2", "duration": 90}]
    monkeypatch.setattr(pl, "expand_meta", lambda url: {"title": "Bộ A", "kind": "multi_p", "parts": [dict(p) for p in parts]})
    monkeypatch.setattr(cookies, "status", lambda check=True: {"has_cookie": False})
    srv._create(parts[1]["url"], "vi", None)
    d = add_jobs.inspect("BV1xx411c7mD")
    assert d["url"].startswith("https://www.bilibili.com/video/") and d["kind"] == "multi_p" and d["total_sec"] == 150
    assert [p["exists"] for p in d["parts"]] == [False, True] and d["cookie"] == {"has_cookie": False}
    assert "Bilibili gốc" in d["downloader"]
    with pytest.raises(ValueError):
        add_jobs.inspect("D:\\phim\\a.mp4")


def test_create_selected_parts_grouped_skip_existing(srv):
    parts = [{"url": f"https://www.bilibili.com/video/BV1xx411c7mD?p={i}", "title": f"P{i}"} for i in (2, 3, 4)]
    srv._create(parts[0]["url"], "vi", None)
    srv.created.clear()
    r = add_jobs.create("https://www.bilibili.com/video/BV1xx411c7mD", parts, "Bộ A")
    assert r["count"] == 2 and len(r["skipped"]) == 1
    g = srv.created[0][1]
    assert g["group_title"] == "Bộ A" and g["group_total"] == 3 and g["part_title"] == "P3"
    with pytest.raises(ValueError):
        add_jobs.create("x", [], "")


def test_pick_files_keeps_only_real_videos(tmp_path, monkeypatch):
    import subprocess
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.txt").write_bytes(b"x")
    out = "\n".join([str(tmp_path / "a.mp4"), str(tmp_path / "b.txt"), str(tmp_path / "khong.mp4"), ""])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=out, stderr=""))
    assert add_jobs.pick_files(str(tmp_path)) == [str(tmp_path / "a.mp4")]


def test_desktop_add_dialog_wired():
    ui = Path(__file__).resolve().parents[1] / "desktop" / "ui"
    js, html = (ui / "app.js").read_text(encoding="utf-8"), (ui / "index.html").read_text(encoding="utf-8")
    for n in ("/api/v6/source/inspect", "/api/v6/jobs/from-link", "/api/v6/fs/pick-files", "/api/fs/pick-dirs",
              "/api/jobs/local-batch", "/api/v6/bili/cookie", "new-ck-save"):
        assert n in js, n
    assert 'name="new-tab"' in html and 'data-pane="bili"' in html and 'id="new-pick-files"' in html
