"""extract_pcm_from_video: full OK; rematrix/hỏng → salvage + chunk + silence."""
from __future__ import annotations

import wave
from pathlib import Path
from unittest import mock

import engines.media_ts as mt


def _write_wav(p: Path, seconds: float = 1.0, rate: int = 16000, ch: int = 1) -> None:
    n = int(seconds * rate)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * n * ch)


def test_extract_full_success(tmp_path: Path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    out = tmp_path / "a.wav"

    def fake_run(cmd, **kw):
        if cmd[0] == "ffprobe":
            class R:
                stdout = "10.0\n"
            return R()
        # ffmpeg full extract
        _write_wav(Path(cmd[-1]), 10.0)
        class R:
            returncode = 0
        return R()

    with mock.patch.object(mt.subprocess, "run", side_effect=fake_run):
        info = mt.extract_pcm_from_video(src, out, ac=1, ar=16000)
    assert info["mode"] == "full"
    assert out.exists()


def test_extract_chunked_after_rematrix(tmp_path: Path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    out = tmp_path / "a.wav"
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = ""

        if cmd[0] == "ffprobe":
            target = Path(cmd[-1])
            # video duration
            if target == src or str(target).endswith("v.mp4"):
                R.stdout = "100.0\n"
                return R()
            # wav duration via name heuristics
            if not target.exists():
                R.stdout = "0\n"
                return R()
            # use actual wave length
            with wave.open(str(target), "rb") as w:
                R.stdout = f"{w.getnframes() / float(w.getframerate())}\n"
            return R()

        out_p = Path(cmd[-1])
        # lavfi silence
        if "-f" in cmd and "lavfi" in cmd:
            # -t DUR
            t = 5.0
            if "-t" in cmd:
                t = float(cmd[cmd.index("-t") + 1])
            _write_wav(out_p, t)
            return R()
        # concat copy
        if "-f" in cmd and "concat" in cmd:
            # produce ~100s by writing long wav
            _write_wav(out_p, 100.0)
            return R()

        calls["n"] += 1
        # first full extract: rematrix fail after 40s
        if calls["n"] == 1 and "-ss" not in cmd:
            _write_wav(out_p, 40.0)
            R.returncode = 1
            return R()
        # window extracts: fail around 40-50, ok elsewhere
        ss = float(cmd[cmd.index("-ss") + 1]) if "-ss" in cmd else 0.0
        t = float(cmd[cmd.index("-t") + 1]) if "-t" in cmd else 30.0
        if 40.0 <= ss < 50.0:
            R.returncode = 1
            # empty / tiny
            return R()
        _write_wav(out_p, t)
        return R()

    with mock.patch.object(mt.subprocess, "run", side_effect=fake_run):
        info = mt.extract_pcm_from_video(src, out, ac=1, ar=16000, chunk_sec=30.0)
    assert info["mode"] == "chunked"
    assert info.get("silence_sec", 0) > 0
    assert out.exists()
