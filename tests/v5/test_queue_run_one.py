import json
from pathlib import Path

import pytest

import server


class _SyncExecutor:
    def submit(self, fn):
        fn()


def _job(tmp_path: Path, job_id: str = "job-1", voice: str | None = None) -> Path:
    work_dir = tmp_path / job_id
    work_dir.mkdir()
    tts = {"status": "pending"}
    if voice:
        tts["voice"] = voice
    (work_dir / "manifest.json").write_text(
        json.dumps(
            {
                "request": {"url": "https://example.invalid/video", "target": "vi"},
                "stages": {"tts": tts},
            }
        ),
        encoding="utf-8",
    )
    return work_dir


@pytest.fixture(autouse=True)
def _isolated_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(server.job_queue, "WORK_ROOT", tmp_path)
    server._STOP_JOBS.clear()
    server._STAGE_BUSY.clear()
    yield
    server._STOP_JOBS.clear()
    server._STAGE_BUSY.clear()


def test_missing_job_voice_fails_without_global_fallback(tmp_path, monkeypatch):
    work_dir = _job(tmp_path)
    video = work_dir / "source.mp4"
    video.write_bytes(b"video")
    claimed = []
    done_checks = iter((False, True))

    monkeypatch.setattr(
        server.job_queue, "is_wired_done", lambda _wd: next(done_checks)
    )
    monkeypatch.setattr(server, "list_clone_voices", lambda: ["global-voice"])
    monkeypatch.setattr(
        server, "_claim_stage", lambda *_args: claimed.append(True) or True
    )
    monkeypatch.setattr(server, "_job_any_busy", lambda _job_id: None)
    monkeypatch.setattr(server, "_manifest_running", lambda _wd: None)
    monkeypatch.setattr(server, "_next_incomplete_stage", lambda _wd: "asr")
    monkeypatch.setattr(server, "_find_video", lambda _wd: video)
    monkeypatch.setattr(server, "_auto_gpu", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_gpu", _SyncExecutor())

    assert server._queue_run_one("job-1", {}) == ("failed", "chưa chọn giọng")
    assert claimed == []


def test_stop_after_claim_is_not_cleared(tmp_path, monkeypatch):
    work_dir = _job(tmp_path, voice="saved-voice")
    video = work_dir / "source.mp4"
    video.write_bytes(b"video")

    def claim_and_stop(job_id, _stage):
        server._STOP_JOBS.add(job_id)
        return True

    monkeypatch.setattr(server.job_queue, "is_wired_done", lambda _wd: False)
    monkeypatch.setattr(server, "list_clone_voices", lambda: ["saved-voice"])
    monkeypatch.setattr(server, "_job_any_busy", lambda _job_id: None)
    monkeypatch.setattr(server, "_manifest_running", lambda _wd: None)
    monkeypatch.setattr(server, "_claim_stage", claim_and_stop)
    monkeypatch.setattr(server, "_next_incomplete_stage", lambda _wd: "asr")
    monkeypatch.setattr(server, "_find_video", lambda _wd: video)
    monkeypatch.setattr(server, "_auto_gpu", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_gpu", _SyncExecutor())

    assert server._queue_run_one("job-1", {}) == (
        "stopped",
        "đã dừng trước khi chạy",
    )
    assert "job-1" in server._STOP_JOBS


def test_already_wired_done_skips_gpu_claim(tmp_path, monkeypatch):
    work_dir = _job(tmp_path)
    claimed = []

    monkeypatch.setattr(server.job_queue, "is_wired_done", lambda wd: wd == work_dir)
    monkeypatch.setattr(
        server, "_claim_stage", lambda *_args: claimed.append(True) or True
    )

    assert server._queue_run_one("job-1", {}) == ("done", "đã hoàn tất trước đó")
    assert claimed == []


def _prepare_claimed_runner(monkeypatch, video: Path | None, voices=None):
    monkeypatch.setattr(server.job_queue, "is_wired_done", lambda _wd: False)
    monkeypatch.setattr(server, "list_clone_voices", lambda: voices or ["saved-voice"])
    monkeypatch.setattr(server, "_job_any_busy", lambda _job_id: None)
    monkeypatch.setattr(server, "_manifest_running", lambda _wd: None)
    monkeypatch.setattr(server, "_claim_stage", lambda *_args: True)
    monkeypatch.setattr(server, "_next_incomplete_stage", lambda _wd: "asr")
    monkeypatch.setattr(server, "_find_video", lambda _wd: video)


def test_invalid_voice_returns_detail_without_claim(tmp_path, monkeypatch):
    _job(tmp_path, voice="missing-voice")
    claimed = []
    monkeypatch.setattr(server.job_queue, "is_wired_done", lambda _wd: False)
    monkeypatch.setattr(server, "list_clone_voices", lambda: ["other-voice"])
    monkeypatch.setattr(
        server, "_claim_stage", lambda *_args: claimed.append(True) or True
    )

    status, detail = server._queue_run_one("job-1", {})

    assert status == "failed"
    assert "missing-voice" in detail
    assert claimed == []


def test_missing_url_finalizes_running_stage(tmp_path, monkeypatch):
    work_dir = _job(tmp_path, voice="saved-voice")
    manifest = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["request"].pop("url")
    (work_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _prepare_claimed_runner(monkeypatch, video=None)

    result = server._queue_run_one("job-1", {})

    assert result == ("failed", "manifest không có URL nguồn")
    saved = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    assert saved["stages"]["asr"]["status"] == "failed"


def test_download_failure_returns_detail_and_finalizes(tmp_path, monkeypatch):
    work_dir = _job(tmp_path, voice="saved-voice")
    _prepare_claimed_runner(monkeypatch, video=None)
    monkeypatch.setattr(
        server, "download_video", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("network down"))
    )

    status, detail = server._queue_run_one("job-1", {})

    assert status == "failed"
    assert "network down" in detail
    saved = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    assert saved["stages"]["asr"]["status"] == "failed"


def test_executor_submission_failure_returns_detail(tmp_path, monkeypatch):
    work_dir = _job(tmp_path, voice="saved-voice")
    video = work_dir / "source.mp4"
    video.write_bytes(b"video")
    _prepare_claimed_runner(monkeypatch, video=video)

    class _BrokenExecutor:
        def submit(self, fn):
            raise RuntimeError("executor closed")

    monkeypatch.setattr(server, "_gpu", _BrokenExecutor())
    status, detail = server._queue_run_one("job-1", {})

    assert status == "failed"
    assert "executor closed" in detail


def test_incomplete_output_returns_detail(tmp_path, monkeypatch):
    work_dir = _job(tmp_path, voice="saved-voice")
    video = work_dir / "source.mp4"
    video.write_bytes(b"video")
    _prepare_claimed_runner(monkeypatch, video=video)
    monkeypatch.setattr(server, "_auto_gpu", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_gpu", _SyncExecutor())

    result = server._queue_run_one("job-1", {})

    assert result == ("failed", "pipeline kết thúc nhưng output chưa hoàn chỉnh")
