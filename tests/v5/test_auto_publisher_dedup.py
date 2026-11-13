import os
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import agents.auto_publisher as auto_publisher
import core.film_db as film_db

def test_auto_state_sync(tmp_path, monkeypatch):
    state_file = tmp_path / "auto_publish_state.json"
    monkeypatch.setattr(auto_publisher, "STATE_FILE", state_file)
    monkeypatch.setattr(auto_publisher, "DATA_DIR", tmp_path)

    auto_publisher.update_auto_state(
        current_job_id="test_job_1",
        current_stage="metadata",
        progress_percent=15,
    )
    assert state_file.exists()
    st = auto_publisher.get_auto_state()
    assert st["current_job_id"] == "test_job_1"
    assert st["current_stage"] == "metadata"
    assert st["progress_percent"] == 15


def test_cross_process_lock_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_publisher, "DATA_DIR", tmp_path)
    monkeypatch.setattr(auto_publisher, "ROOT", tmp_path)

    # Thư mục work giả lập
    work_dir = tmp_path / "work" / "job_abc"
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = work_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"request": {"title": "Test Film"}}), encoding="utf-8")

    # Mock film_db.query
    monkeypatch.setattr(film_db, "query", lambda **kw: [{"job_id": "job_abc", "title_vi": "Test Film", "work_exists": 1}])

    # Mock _publish_job_internal
    called = []
    def mock_publish_internal(job_id, **kw):
        called.append(job_id)
        # Giữ lock trong lúc đang chạy
        assert auto_publisher.is_job_uploading(job_id) is True
        # Thử gọi publish_job lần 2 từ luồng/tiến trình khác trong lúc đang chạy
        r2 = auto_publisher.publish_job(job_id)
        assert r2.get("skipped") is True
        return {"ok": True, "job_id": job_id}

    monkeypatch.setattr(auto_publisher, "_publish_job_internal", mock_publish_internal)

    # Chạy publish_job lần 1
    r1 = auto_publisher.publish_job("job_abc")
    assert r1.get("ok") is True
    assert len(called) == 1

    # Sau khi xong, lock được giải phóng
    assert auto_publisher.is_job_uploading("job_abc") is False


def test_skip_if_already_uploaded(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_publisher, "DATA_DIR", tmp_path)
    monkeypatch.setattr(auto_publisher, "ROOT", tmp_path)

    work_dir = tmp_path / "work" / "job_done"
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = work_dir / "manifest.json"
    manifest_path.write_text(json.dumps({
        "request": {"title": "Done Film"},
        "yt_url": "https://youtu.be/dummy123"
    }), encoding="utf-8")

    monkeypatch.setattr(film_db, "query", lambda **kw: [{
        "job_id": "job_done",
        "title_vi": "Done Film",
        "yt_url": "https://youtu.be/dummy123",
        "work_exists": 1
    }])

    # Không truyền force -> phải trả already_uploaded
    r = auto_publisher.publish_job("job_done", force=False)
    assert r.get("already_uploaded") is True
    assert r.get("yt_url") == "https://youtu.be/dummy123"


def test_scanner_skips_uploading_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_publisher, "DATA_DIR", tmp_path)
    monkeypatch.setattr(auto_publisher, "ROOT", tmp_path)

    # Tạo 2 job: job1 đang uploading (có lock), job2 done
    w1 = tmp_path / "work" / "job1"
    w1.mkdir(parents=True, exist_ok=True)
    (w1 / "manifest.json").write_text(json.dumps({"request": {"title": "Film 1"}}), encoding="utf-8")

    w2 = tmp_path / "work" / "job2"
    w2.mkdir(parents=True, exist_ok=True)
    (w2 / "manifest.json").write_text(json.dumps({"request": {"title": "Film 2"}}), encoding="utf-8")

    # Khóa job1 bằng lock
    fd, lp, pid = auto_publisher.acquire_job_lock("job1")
    assert fd is not None

    try:
        # Mock film_db.query trả về 2 job status=done
        monkeypatch.setattr(film_db, "query", lambda **kw: [
            {"job_id": "job1", "title_vi": "Film 1", "work_exists": 1},
            {"job_id": "job2", "title_vi": "Film 2", "work_exists": 1},
        ])

        published = []
        monkeypatch.setattr(auto_publisher, "publish_job", lambda jid, **kw: (published.append(jid), {"ok": True})[1])

        count = auto_publisher._scan_and_publish_impl()
        assert count == 1
        assert published == ["job2"] # job1 bị bỏ qua hoàn toàn vì đang uploading!
    finally:
        auto_publisher.release_job_lock(fd, lp)
