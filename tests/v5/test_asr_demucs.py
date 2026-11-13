import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock
import pytest

from engines.asr.asr import ensure_audio_wav, resolve_asr_source
from core.manifest import Manifest


def test_resolve_asr_source_existing_vocals(tmp_path):
    # Job has vocals.wav already
    voc = tmp_path / "vocals.wav"
    voc.write_bytes(b"RIFF" + b"\x00" * 2000)
    res = resolve_asr_source(tmp_path)
    assert res == voc


def test_resolve_asr_source_audio_only_fallback(tmp_path):
    # Job has only audio.wav (no video, no vocals)
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 2000)
    res = resolve_asr_source(tmp_path)
    assert res == wav


def test_resolve_asr_source_triggers_separate_when_video_present(tmp_path):
    # Video exists, but no vocals.wav yet
    vid = tmp_path / "source.mp4"
    vid.write_bytes(b"FAKE_VIDEO_CONTENT")
    m = Manifest(tmp_path)
    m.mark_done("download", video_path=str(vid))

    def fake_run_separate(work_dir, device="cuda", force=False):
        # Fake Demucs output
        (Path(work_dir) / "no_vocals.wav").write_bytes(b"RIFF_NO_VOCALS" + b"\x00" * 1500)
        (Path(work_dir) / "vocals.wav").write_bytes(b"RIFF_VOCALS" + b"\x00" * 1500)
        m_inner = Manifest(work_dir)
        m_inner.mark_done("separate", no_vocals_path=str(Path(work_dir) / "no_vocals.wav"), vocals_path=str(Path(work_dir) / "vocals.wav"))

    with mock.patch.dict(os.environ, {"ASR_USE_DEMUCS": "1"}), \
         mock.patch("engines.separate.run_separate", side_effect=fake_run_separate) as mock_sep:
        res = resolve_asr_source(tmp_path)
        assert mock_sep.called
        assert res == tmp_path / "vocals.wav"
        assert res.exists()
