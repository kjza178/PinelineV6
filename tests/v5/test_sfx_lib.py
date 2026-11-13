"""sfx_lib: nối khúc tách, chia mức nhạc, xếp bài nhạc kho, bộ lọc render."""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import engines.sfx_lib as sfx_lib  # noqa: E402

FF = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="không có ffmpeg")


def _stream(audio, fn, chunk_s, overlap_s, sr=100):
    pos = [0]
    out = []

    def read(n):
        blk = audio[:, pos[0]:pos[0] + n]
        pos[0] += blk.shape[1]
        return blk

    n = sfx_lib.separate_stream(read, out.append, fn, sr=sr, chunk_s=chunk_s, overlap_s=overlap_s)
    return n, np.concatenate(out, axis=-1)


@pytest.mark.parametrize("length", [50, 1000, 1234, 2001])
def test_separate_stream_reassembles_exactly(length):
    rng = np.random.default_rng(length)
    audio = rng.standard_normal((2, length)).astype(np.float32)
    n, y = _stream(audio, lambda x: np.stack([x, 2 * x]), chunk_s=3.0, overlap_s=0.5)
    assert n == length and y.shape == (2, 2, length)
    np.testing.assert_allclose(y[0], audio, atol=1e-6)
    np.testing.assert_allclose(y[1], 2 * audio, atol=1e-6)


def test_separate_stream_crossfades_chunk_edges():
    audio = np.ones((2, 1000), dtype=np.float32)
    calls = []

    def fn(x):
        calls.append(x.shape[1])
        return np.stack([np.full_like(x, len(calls))])

    _, y = _stream(audio, fn, chunk_s=4.0, overlap_s=1.0)
    assert calls[0] == 400 and all(c <= 500 for c in calls)
    seam = y[0, 0, 300:400]
    assert seam[0] == pytest.approx(1.0) and seam[-1] == pytest.approx(2.0, abs=0.02)
    assert np.all(np.diff(seam) >= -1e-6)


def test_classify_levels_and_min_length():
    win = 2.0
    quiet = np.full(60, -60.0)
    soft = np.full(60, -30.0)
    loud = np.full(60, -12.0)
    rms = np.concatenate([quiet, soft, loud, soft]).astype(np.float32)
    flux = np.concatenate([np.zeros(60), np.full(60, 0.1), np.full(60, 0.6), np.full(60, 0.1)]).astype(np.float32)
    rms[70:72] = -12.0
    segs = sfx_lib.classify(rms, flux, win_s=win)
    assert segs[0]["level"] == "none" and segs[0]["end"] == pytest.approx(120, abs=12)
    loud_seg = [s for s in segs if s["level"] == "intense"]
    assert loud_seg and loud_seg[0]["start"] == pytest.approx(240, abs=20)
    assert all(s["end"] - s["start"] >= 40 for s in segs)
    assert segs[-1]["end"] == pytest.approx(len(rms) * win)
    assert all(a["end"] == b["start"] for a, b in zip(segs, segs[1:]))


def test_plan_bgm_covers_segments_and_rotates():
    lib = {"calm": [("c1", 100.0), ("c2", 100.0)], "mid": [("m1", 60.0)], "intense": [("i1", 80.0), ("i2", 80.0)]}
    segs = [{"start": 0, "end": 50, "level": "none"}, {"start": 50, "end": 300, "level": "calm"},
            {"start": 300, "end": 400, "level": "intense"}]
    plan = sfx_lib.plan_bgm(segs, 400.0, lib, "job1")
    assert all(p["at"] >= 48.5 for p in plan)
    calm = [p for p in plan if p["file"].startswith("c")]
    assert [p["file"] for p in calm][:2] in (["c1", "c2"], ["c2", "c1"])
    end = max(p["at"] + p["dur"] for p in plan)
    assert end == pytest.approx(400.0, abs=0.01)
    for a, b in zip(calm, calm[1:]):
        assert b["at"] == pytest.approx(a["at"] + a["dur"] - sfx_lib.XFADE_S, abs=0.01)
    assert plan == sfx_lib.plan_bgm(segs, 400.0, lib, "job1")


@FF
def test_render_bgm_silence_and_music(tmp_path):
    def gen(name, f, d):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={f}:d={d}", "-ac", "2",
                        str(tmp_path / name)], check=True)
    gen("a.wav", 220, 20)
    gen("b.wav", 330, 20)
    lib = {"calm": [(str(tmp_path / "a.wav"), 20.0), (str(tmp_path / "b.wav"), 20.0)], "mid": [], "intense": []}
    segs = [{"start": 0, "end": 10, "level": "none"}, {"start": 10, "end": 45, "level": "calm"}]
    plan = sfx_lib.plan_bgm(segs, 45.0, lib, "x")
    out = tmp_path / "bgm.flac"
    sfx_lib.render_bgm(plan, 45.0, out, block_s=7.0)
    import soundfile as sf
    a, sr = sf.read(str(out))
    assert a.shape == (45 * sr, 2)
    rms = lambda s, e: float(np.sqrt(np.mean(a[int(s * sr):int(e * sr)] ** 2)))  # noqa: E731
    assert rms(0, 8) < 1e-4
    assert 0.05 < rms(15, 40) < 0.2
    assert np.abs(a).max() <= 1.0


@FF
def test_render_filter_sfx_lib_runs(tmp_path):
    import engines.render as render

    def gen(name, src):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-ac", "2", name],
                       cwd=tmp_path, check=True)
    gen(sfx_lib.EFFECT, "anoisesrc=d=6:a=0.05")
    gen(sfx_lib.BGM, "sine=f=220:d=6")
    gen("dub.wav", "sine=f=440:d=2")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=6"]
    af, used = render._build_audio_filter(cmd, tmp_path, tmp_path / "dub.wav", tmp_path / "nv.wav",
                                          tmp_path / "voc.wav", "sfx_lib", 0.2, 0.28)
    assert used.startswith("sfx_lib")
    cmd += ["-filter_complex", af, "-map", "[aout]", "-vn", "out.wav"]
    import soundfile as sf
    for _ in range(8):
        subprocess.run(cmd, cwd=tmp_path, check=True, timeout=120)
        a, sr = sf.read(str(tmp_path / "out.wav"))
        assert a.shape[0] == pytest.approx(6 * sr, abs=sr * 0.05)
        assert float(np.sqrt(np.mean(a[int(5.5 * sr):] ** 2))) > 1e-3


def test_render_filter_sfx_lib_limits_peak(tmp_path):
    import engines.render as render

    def gen(name, src):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-ac", "2", name],
                       cwd=tmp_path, check=True)
    gen(sfx_lib.EFFECT, "sine=f=150:d=3")
    gen(sfx_lib.BGM, "sine=f=220:d=3")
    gen("dub.wav", "sine=f=440:d=3,volume=5")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=3"]
    af, _ = render._build_audio_filter(cmd, tmp_path, tmp_path / "dub.wav", tmp_path / "nv.wav",
                                       tmp_path / "voc.wav", "sfx_lib", 0.2, 0.28)
    assert "alimiter" in af
    cmd += ["-filter_complex", af, "-map", "[aout]", "-t", "3", "-c:a", "pcm_f32le", "out.wav"]
    subprocess.run(cmd, cwd=tmp_path, check=True, timeout=120)
    import soundfile as sf
    a, sr = sf.read(str(tmp_path / "out.wav"))
    assert float(np.abs(a[sr // 2:]).max()) <= 0.9


def test_render_filter_sfx_lib_requires_prepare(tmp_path):
    import engines.render as render
    (tmp_path / "dub.wav").write_bytes(b"x")
    with pytest.raises(RuntimeError, match="sfx_lib"):
        render._build_audio_filter(["ffmpeg", "-i", "v.mp4"], tmp_path, tmp_path / "dub.wav", tmp_path / "nv.wav",
                                   tmp_path / "voc.wav", "sfx_lib", 0.2, 0.28)


def test_mode_routing():
    import engines.render as render
    assert render._normalize_audio_mode("sfx_lib") == "sfx_lib"
    assert render._normalize_audio_mode("yt_lib") == "sfx_lib"
    assert render._normalize_audio_mode("sfx_bgm") == "duck_bgm"
    assert render._uses_sidechain("sfx_lib")
