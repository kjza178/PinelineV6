from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server


def test_link_or_copy_hardlinks(tmp_path):
    src, dest = tmp_path / "a (merged).mp4", tmp_path / "job" / "source.mp4"
    dest.parent.mkdir()
    src.write_bytes(b"x" * 1000)
    assert server._link_or_copy(src, dest) == "link"
    assert os.stat(dest).st_nlink == 2
    assert os.path.samefile(src, dest)


def test_link_or_copy_falls_back_to_copy(tmp_path, monkeypatch):
    src, dest = tmp_path / "a.mp4", tmp_path / "source.mp4"
    src.write_bytes(b"y" * 1000)

    def no_link(*_a, **_k):
        raise OSError(17, "khác ổ")
    monkeypatch.setattr(server.os, "link", no_link)
    assert server._link_or_copy(src, dest) == "copy"
    assert dest.read_bytes() == src.read_bytes()
    assert os.stat(dest).st_nlink == 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path / "work")
    monkeypatch.setattr(server.job_queue, "WORK_ROOT", tmp_path / "work")
    monkeypatch.setattr(server, "_probe_video", lambda _p: {})
    monkeypatch.setattr(server, "_direct_source_roots", lambda: [tmp_path / "lib"])
    return TestClient(server.app, base_url="http://127.0.0.1:8804")


def _job(tmp_path, name="job-a"):
    wd = tmp_path / "work" / name
    wd.mkdir(parents=True)
    (wd / "manifest.json").write_text(json.dumps({"stages": {}}), encoding="utf-8")
    return wd


def _video(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"z" * 4096)
    return path


def test_source_path_from_library_is_direct(client, tmp_path):
    wd = _job(tmp_path)
    src = _video(tmp_path / "lib" / "show" / "show (merged).mp4")
    r = client.post("/api/jobs/job-a/source-path", json={"path": str(src), "move": False})
    assert r.status_code == 200, r.text
    assert not list(wd.glob("source.*"))
    dl = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))["stages"]["download"]
    assert Path(dl["video_path"]) == src.resolve()
    assert dl["source_mode"] == "direct" and dl["video_size"] == 4096
    assert server._find_video(wd) == src.resolve()


def test_source_path_outside_library_hardlinks(client, tmp_path):
    wd = _job(tmp_path)
    src = _video(tmp_path / "downloads" / "clip.mp4")
    r = client.post("/api/jobs/job-a/source-path", json={"path": str(src), "move": False})
    assert r.status_code == 200, r.text
    assert os.path.samefile(src, wd / "source.mp4")
    dl = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))["stages"]["download"]
    assert dl["source_mode"] == "link"


def test_reimport_library_file_replaces_old_copy(client, tmp_path):
    wd = _job(tmp_path)
    (wd / "source.mp4").write_bytes(b"old copy")
    src = _video(tmp_path / "lib" / "show" / "show (merged).mp4")
    r = client.post("/api/jobs/job-a/source-path", json={"path": str(src), "move": False})
    assert r.status_code == 200, r.text
    assert not (wd / "source.mp4").exists()
