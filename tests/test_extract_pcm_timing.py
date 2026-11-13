"""extract_pcm_from_video — fix review 2026-11-12 (agentK).

(1) full/full_soft chỉ nhận khi thiếu ≤ EXTRACT_FULL_TOL_SEC; thiếu hơn → head + chunk.
(2) mỗi khúc chunk (và head) bị ép đúng số mẫu cửa sổ → mốc giữa phim không trôi.
Audio tổng hợp có tiếng bíp ở mốc biết trước + file AAC hỏng giả lập (ghi rác vào mdat).
"""
from __future__ import annotations

import hashlib
import random
import shutil
import subprocess
import types
import wave
from pathlib import Path
from unittest import mock

import pytest

import engines.media_ts as mt

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="cần ffmpeg/ffprobe")

BEEP_EVERY = 10.0
BEEP_FIRST = 3.0


def make_beep_video(p: Path, dur: float = 60.0) -> list[float]:
    """mp4 h264 + AAC stereo 44.1k: bíp 1 kHz 0.1 s tại 3, 13, 23… (nền 220 Hz rất nhỏ)."""
    expr = (f"if(lt(mod(t-{BEEP_FIRST}\\,{BEEP_EVERY})\\,0.1)*gte(t\\,{BEEP_FIRST})\\,"
            f"0.8*sin(2*PI*1000*t)\\,0.02*sin(2*PI*220*t))")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=gray:s=64x36:r=10:d={dur}",
        "-f", "lavfi", "-i", f"aevalsrc='{expr}|{expr}':s=44100:d={dur}",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "20",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(p),
    ], check=True, capture_output=True)
    n = int((dur - BEEP_FIRST) // BEEP_EVERY) + 1
    return [BEEP_FIRST + k * BEEP_EVERY for k in range(n) if BEEP_FIRST + k * BEEP_EVERY < dur - 0.2]


def corrupt_middle(src: Path, dst: Path, lo: float = 0.40, hi: float = 0.48, seed: int = 7) -> None:
    random.seed(seed)
    b = bytearray(src.read_bytes())
    s, e = int(len(b) * lo), int(len(b) * hi)
    for pos in range(s, e, 97):
        for k in range(40):
            b[pos + k] = random.randrange(256)
    dst.write_bytes(bytes(b))


def read_wav(p: Path):
    with wave.open(str(p), "rb") as w:
        ar, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    import array
    a = array.array("h")
    a.frombytes(raw)
    return a[::ch], ar, n


def beep_onsets(p: Path, thr: float = 0.3) -> list[float]:
    x, ar, _ = read_wav(p)
    lim = int(thr * 32768)
    out: list[float] = []
    last = -10.0
    for i, v in enumerate(x):
        if v > lim or v < -lim:
            t = i / ar
            if t - last > 1.0:
                out.append(t)
            last = t
    return out


def assert_beeps_at(found: list[float], expected: list[float], tol: float = 0.03, skip=()) -> None:
    for t in expected:
        if any(a <= t <= b for a, b in skip):
            continue
        near = [f for f in found if abs(f - t) <= tol]
        assert near, f"không thấy bíp ở {t}s (tìm được: {[round(f, 3) for f in found]})"


# ─────────────── (1) ngưỡng nhận full ───────────────

def _fake_runner(vdur: float, full_sec: float, full_rc: int, calls: list):
    def _write(p: Path, sec: float):
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * int(round(sec * 16000)))

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = ""
        if cmd[0] == "ffprobe":
            tgt = Path(cmd[-1])
            if tgt.suffix == ".mp4":
                R.stdout = f"{vdur}\n"
            elif tgt.exists():
                with wave.open(str(tgt), "rb") as w:
                    R.stdout = f"{w.getnframes() / w.getframerate()}\n"
            else:
                R.stdout = "0\n"
            return R()
        out = Path(cmd[-1])
        calls.append(cmd)
        if "concat" in cmd:
            lst = Path(cmd[cmd.index("-i") + 1]).read_text(encoding="utf-8").splitlines()
            tot = 0
            for ln in lst:
                f = ln[len("file '"):-1]
                with wave.open(f, "rb") as w:
                    tot += w.getnframes()
            _write(out, tot / 16000)
            return R()
        if "-ss" not in cmd and "lavfi" not in cmd and "atrim" not in " ".join(cmd):
            _write(out, full_sec)
            R.returncode = full_rc
            return R()
        if "-ss" in cmd:
            t = float(cmd[cmd.index("-t") + 1])
            _write(out, t)
        return R()
    return fake_run


@pytest.mark.parametrize("rc", [0, 1])
def test_full_short_beyond_tol_goes_chunked(tmp_path: Path, rc: int):
    """Bản full thiếu 10% (rc=0 hoặc ≠0) → KHÔNG nhận full nữa: head + chunk phần còn."""
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    calls: list = []
    with mock.patch.object(mt.subprocess, "run", side_effect=_fake_runner(100.0, 90.0, rc, calls)):
        info = mt.extract_pcm_from_video(src, tmp_path / "a.wav", ac=1, ar=16000)
    assert info["mode"] == "chunked"
    assert info["from_sec"] == pytest.approx(90.0, abs=0.01)
    _, ar, n = read_wav(tmp_path / "a.wav")
    assert n == 100 * 16000


@pytest.mark.parametrize("rc,mode", [(0, "full"), (1, "full_soft")])
def test_full_short_within_tol_kept(tmp_path: Path, rc: int, mode: str):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    calls: list = []
    with mock.patch.object(mt.subprocess, "run", side_effect=_fake_runner(100.0, 99.5, rc, calls)):
        info = mt.extract_pcm_from_video(src, tmp_path / "a.wav", ac=1, ar=16000)
    assert info["mode"] == mode
    assert len(calls) == 1          # file lành: đúng 1 lệnh ffmpeg như cũ


def test_full_tol_configurable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EXTRACT_FULL_TOL_SEC", "15")
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    with mock.patch.object(mt.subprocess, "run", side_effect=_fake_runner(100.0, 90.0, 0, [])):
        info = mt.extract_pcm_from_video(src, tmp_path / "a.wav", ac=1, ar=16000)
    assert info["mode"] == "full"


def test_force_wav_frames_pad_and_trim(tmp_path: Path):
    p = tmp_path / "x.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x01\x00\x02\x00" * 1000)
    assert mt._force_wav_frames(p, 1500, ac=2, ar=44100) == 1500
    x, _, n = read_wav(p)
    assert n == 1500 and x[999] == 1 and x[1000] == 0
    mt._force_wav_frames(p, 10, ac=2, ar=44100)
    assert read_wav(p)[2] == 10


# ─────────────── (2) mốc từng khúc (ffmpeg thật) ───────────────

@needs_ffmpeg
def test_chunk_pieces_keep_window_timing(tmp_path: Path):
    """Mỗi khúc ra thiếu 2–12% (giả lập) → vẫn ghép đúng mốc: bíp ±30 ms, tổng đúng số mẫu."""
    src = tmp_path / "beep.mp4"
    expected = make_beep_video(src, 60.0)
    real = mt._extract_window
    rnd = random.Random(3)

    def short_window(src_, out, *, ss, t, ac, ar, af, rf64, **kw):
        got = real(src_, out, ss=ss, t=t, ac=ac, ar=ar, af=af, rf64=rf64)
        keep = int(round(t * (1.0 - rnd.uniform(0.02, 0.12)) * ar))
        mt._force_wav_frames(out, keep, ac=ac, ar=ar)
        return keep / ar if got else 0.0

    out = tmp_path / "a.wav"
    with mock.patch.object(mt, "_extract_window", side_effect=short_window):
        info = mt._extract_pcm_chunked(src, out, start=0.0, end=60.0, ac=1, ar=16000,
                                       af=mt.AUDIO_TS_FIX, rf64=False, chunk_sec=7.0)
    assert info["n_fixed_len"] > 0
    _, ar, n = read_wav(out)
    assert n == 60 * 16000
    assert_beeps_at(beep_onsets(out), expected, tol=0.03)


@needs_ffmpeg
def test_salvage_head_then_chunk_keeps_timing(tmp_path: Path):
    """Full chết ở 40% (giả lập rc≠0 + partial) → head + chunk; bíp sau chỗ nối đúng mốc."""
    src = tmp_path / "beep.mp4"
    expected = make_beep_video(src, 60.0)
    real_run = subprocess.run

    def run(cmd, *a, **kw):
        if cmd and cmd[0] == "ffmpeg" and "-ss" not in cmd and str(src) in cmd and "-t" not in cmd:
            cmd = cmd[:cmd.index("-i")] + ["-t", "24.37"] + cmd[cmd.index("-i"):]
            r = real_run(cmd, *a, **kw)
            r.returncode = 1
            return r
        return real_run(cmd, *a, **kw)

    out = tmp_path / "a.wav"
    with mock.patch.object(mt.subprocess, "run", side_effect=run):
        info = mt.extract_pcm_from_video(src, out, ac=1, ar=16000, chunk_sec=9.0)
    assert info["mode"] == "chunked"
    _, ar, n = read_wav(out)
    assert abs(n - 60 * 16000) <= 1
    assert_beeps_at(beep_onsets(out), expected, tol=0.03)


@needs_ffmpeg
def test_corrupt_aac_full_length_and_timing(tmp_path: Path):
    good = tmp_path / "good.mp4"
    expected = make_beep_video(good, 60.0)
    bad = tmp_path / "bad.mp4"
    corrupt_middle(good, bad)
    out = tmp_path / "a.wav"
    info = mt.extract_pcm_from_video(bad, out, ac=1, ar=16000)
    vdur = mt._ffprobe_duration(bad)
    _, ar, n = read_wav(out)
    assert abs(n / ar - vdur) <= 1.0, info
    # vùng rác ~40–48% file ≈ 24–29 s → bỏ qua bíp quanh đó
    assert_beeps_at(beep_onsets(out), expected, tol=0.03, skip=[(20.0, 35.0)])


@needs_ffmpeg
def test_healthy_file_same_as_old_and_discardcorrupt_neutral(tmp_path: Path):
    """File lành: 1 lệnh ffmpeg, kết quả byte-giống lệnh cũ; +discardcorrupt không đổi mẫu nào."""
    src = tmp_path / "good.mp4"
    make_beep_video(src, 30.0)
    out = tmp_path / "new.wav"
    info = mt.extract_pcm_from_video(src, out, ac=1, ar=16000)
    assert info["mode"] == "full"
    ref = tmp_path / "ref.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn",
                    "-af", mt.AUDIO_TS_FIX, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(ref)],
                   check=True)

    def md5_pcm(p):
        x, _, _ = read_wav(p)
        return hashlib.md5(x.tobytes()).hexdigest()
    assert md5_pcm(out) == md5_pcm(ref)


def test_full_overlong_garbage_rechunked(tmp_path: Path):
    """Full dài hơn hình > tol và hơn cả luồng tiếng khai báo → AAC rác → chunk lại từ 0."""
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    calls: list = []
    with mock.patch.object(mt.subprocess, "run", side_effect=_fake_runner(100.0, 103.0, 0, calls)):
        info = mt.extract_pcm_from_video(src, tmp_path / "a.wav", ac=1, ar=16000)
    assert info["mode"] == "chunked" and info["from_sec"] == 0.0
    assert info["overlong_full_sec"] == pytest.approx(103.0, abs=0.01)
    assert read_wav(tmp_path / "a.wav")[2] == 100 * 16000


def test_full_slightly_long_kept(tmp_path: Path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    with mock.patch.object(mt.subprocess, "run", side_effect=_fake_runner(100.0, 100.6, 0, [])):
        assert mt.extract_pcm_from_video(src, tmp_path / "a.wav", ac=1, ar=16000)["mode"] == "full"


# ─────────────── decoder chết → ffmpeg đệm im lặng (hành vi ffmpeg 8.x) ───────────────
# Máy production: «ffmpeg version 8.1.2-full_build-www.gyan.dev» (Windows) — AAC hỏng giữa file:
# rc=0, độ dài ĐỦ, nhưng sau điểm hỏng toàn mẫu 0. VM test: ffmpeg 4.4 (rc=0 nhưng dài thừa).
# Mô phỏng độc lập phiên bản: chạy lượt full thật rồi xoá tiếng từ mốc chết + gắn stderr kiểu 8.x.

FFMPEG8_ERR = (
    b"[aac @ 000001c0de4f1a80] channel element 2.0 is not allocated\r\n"
    b"[aist#0:1/aac @ 000001c0de4b2c40] Error submitting packet to decoder: Invalid data found when processing input\r\n"
)
FFMPEG44_ERR = (
    b"[aac @ 0x55a76aa05400] Prediction is not allowed in AAC-LC.\n"
    b"[aac @ 0x55a76aa05400] Error while decoding stream #0:1: Invalid data found when processing input\n"
)


def _zero_from(p: Path, t0: float) -> None:
    with wave.open(str(p), "rb") as r:
        prm = r.getparams()
        data = bytearray(r.readframes(r.getnframes()))
    fb = prm.nchannels * prm.sampwidth
    k = int(t0 * prm.framerate) * fb
    data[k:] = bytes(len(data) - k)
    with wave.open(str(p), "wb") as w:
        w.setparams(prm)
        w.writeframes(bytes(data))


def _full_run_dead(src: Path, dead_at: float, stderr: bytes):
    real_run = subprocess.run

    def run(cmd, *a, **kw):
        r = real_run(cmd, *a, **kw)
        if (cmd and cmd[0] == "ffmpeg" and "-ss" not in cmd and str(src) in cmd
                and str(cmd[-1]).endswith(".partial.wav")):
            _zero_from(Path(cmd[-1]), dead_at)
            r.returncode = 0
            r.stderr = stderr
        return r
    return run


@needs_ffmpeg
@pytest.mark.parametrize("stderr", [FFMPEG8_ERR, FFMPEG44_ERR, b""], ids=["ff8_err", "ff44_err", "no_stderr"])
def test_full_dead_decoder_zero_tail_rechunked(tmp_path: Path, stderr: bytes):
    src = tmp_path / "beep.mp4"
    expected = make_beep_video(src, 60.0)
    out = tmp_path / "a.wav"
    with mock.patch.object(mt.subprocess, "run", side_effect=_full_run_dead(src, 24.98, stderr)):
        info = mt.extract_pcm_from_video(src, out, ac=1, ar=16000, chunk_sec=10.0)
    assert info["mode"] == "chunked", info
    assert info["dead_at"] == pytest.approx(24.98, abs=0.15)
    _, ar, n = read_wav(out)
    assert abs(n - 60 * 16000) <= 1
    assert_beeps_at(beep_onsets(out), expected, tol=0.03)        # 33/43/53 phải còn


@needs_ffmpeg
def test_full_dead_mid_gap_with_errors_rechunked(tmp_path: Path):
    """Có lỗi + đoạn 0 tuyệt đối 4 s giữa phim rồi có tiếng lại → vẫn chunk từ điểm chết."""
    src = tmp_path / "beep.mp4"
    expected = make_beep_video(src, 40.0)
    real_run = subprocess.run

    def run(cmd, *a, **kw):
        r = real_run(cmd, *a, **kw)
        if (cmd and cmd[0] == "ffmpeg" and "-ss" not in cmd and str(src) in cmd
                and str(cmd[-1]).endswith(".partial.wav")):
            p = Path(cmd[-1])
            with wave.open(str(p), "rb") as rr:
                prm = rr.getparams()
                d = bytearray(rr.readframes(rr.getnframes()))
            a, b = int(12.5 * 16000) * 2, int(16.5 * 16000) * 2
            d[a:b] = bytes(b - a)
            with wave.open(str(p), "wb") as w:
                w.setparams(prm)
                w.writeframes(bytes(d))
            r.stderr = FFMPEG8_ERR
        return r
    with mock.patch.object(mt.subprocess, "run", side_effect=run):
        info = mt.extract_pcm_from_video(src, tmp_path / "a.wav", ac=1, ar=16000, chunk_sec=10.0)
    assert info["mode"] == "chunked" and info["dead_at"] == pytest.approx(12.5, abs=0.15)
    assert_beeps_at(beep_onsets(tmp_path / "a.wav"), expected, tol=0.03)


@needs_ffmpeg
def test_full_errors_without_dead_run_kept(tmp_path: Path):
    """Lỗi lẻ tẻ, decoder hồi phục (không có đoạn 0 dài) → giữ full như cũ."""
    src = tmp_path / "beep.mp4"
    make_beep_video(src, 20.0)
    real_run = subprocess.run

    def run(cmd, *a, **kw):
        r = real_run(cmd, *a, **kw)
        if cmd and cmd[0] == "ffmpeg" and "-ss" not in cmd:
            r.stderr = FFMPEG8_ERR
        return r
    with mock.patch.object(mt.subprocess, "run", side_effect=run):
        info = mt.extract_pcm_from_video(src, tmp_path / "a.wav", ac=1, ar=16000)
    assert info["mode"] == "full"


def test_decode_error_classifier_44_and_8x():
    assert mt._decode_errors(FFMPEG8_ERR) == 2
    assert mt._decode_errors(FFMPEG44_ERR) == 2
    assert mt._decode_errors(b"[aac @ 0x1] Number of bands (45) exceeds limit (26).\n") == 1
    assert mt._decode_errors(b"[aist#0:1/aac @ 0x2] Rematrix is needed between 21 channels and mono\n"
                             b"Error reinitializing filters!\n") == 2
    # cảnh báo mốc của muxer KHÔNG tính là lỗi giải mã
    assert mt._decode_errors(b"[null @ 0x3] Application provided invalid, non monotonically increasing "
                             b"dts to muxer in stream 0: 1 >= 0\n") == 0
    assert mt._decode_errors(b"") == 0 and mt._decode_errors(None) == 0


def test_dead_zero_run_detector(tmp_path: Path):
    p = tmp_path / "z.wav"
    ar = 16000
    snd = b"\x10\x00" * ar           # 1 s có tiếng
    z = b"\x00\x00"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(ar)
        w.writeframes(z * ar * 3 + snd + z * int(2.5 * ar) + snd + z * (6 * ar))
    # đầu file im lặng (chưa có tiếng) không tính; đoạn 2.5 s sau tiếng: ≥2 s → mốc 4.0
    assert mt._dead_zero_run(p, 2.0) == pytest.approx(4.0, abs=0.01)
    assert mt._dead_zero_run(p, 3.0) == pytest.approx(7.5, abs=0.01)
    assert mt._dead_zero_run(p, 5.0, tail_only=True) == pytest.approx(7.5, abs=0.01)
    assert mt._dead_zero_run(p, 7.0, tail_only=True) is None


def test_window_with_errors_and_zero_run_rejected(tmp_path: Path):
    out = tmp_path / "w.wav"

    def fake_run(cmd, **kw):
        if cmd[0] == "ffprobe":
            return types.SimpleNamespace(stdout="10.0\n", returncode=0)
        with wave.open(str(cmd[-1]), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x10\x00" * 16000 + b"\x00\x00" * 9 * 16000)
        return types.SimpleNamespace(returncode=0, stderr=FFMPEG8_ERR)
    with mock.patch.object(mt.subprocess, "run", side_effect=fake_run):
        assert mt._extract_window(tmp_path / "v.mp4", out, ss=0, t=10, ac=1, ar=16000,
                                  af=mt.AUDIO_TS_FIX, rf64=False) == 0.0


def test_window_natural_silence_mid_kept_at_sub_level(tmp_path: Path):
    """Có lỗi + đoạn 0 GIỮA khúc (im lặng tự nhiên): giữ ở cả khúc 30 s lẫn khúc con."""
    out = tmp_path / "w.wav"

    def fake_run(cmd, **kw):
        if cmd[0] == "ffprobe":
            return types.SimpleNamespace(stdout="10.0\n", returncode=0)
        with wave.open(str(cmd[-1]), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x10\x00" * 16000 + b"\x00\x00" * 4 * 16000 + b"\x10\x00" * 5 * 16000)
        return types.SimpleNamespace(returncode=0, stderr=FFMPEG8_ERR)
    kw = dict(ss=0, t=10, ac=1, ar=16000, af=mt.AUDIO_TS_FIX, rf64=False)
    with mock.patch.object(mt.subprocess, "run", side_effect=fake_run):
        assert mt._extract_window(tmp_path / "v.mp4", out, **kw) == 10.0
        assert mt._extract_window(tmp_path / "v.mp4", out, dead_check=False, **kw) == 10.0
        assert mt._LAST_WIN["errs"] == 2


def test_window_all_zero_from_start_with_errors_rejected(tmp_path: Path):
    """ffmpeg 8.1.2 mở file giữa vùng hỏng: cả khúc = 0 TỪ ĐẦU + lỗi → loại (K2 nhận nhầm)."""
    out = tmp_path / "w.wav"

    def fake_run(cmd, **kw):
        if cmd[0] == "ffprobe":
            return types.SimpleNamespace(stdout="10.0\n", returncode=0)
        with wave.open(str(cmd[-1]), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 10 * 16000)
        return types.SimpleNamespace(returncode=0, stderr=FFMPEG8_ERR)
    with mock.patch.object(mt.subprocess, "run", side_effect=fake_run):
        assert mt._extract_window(tmp_path / "v.mp4", out, ss=30, t=10, ac=1, ar=16000,
                                  af=mt.AUDIO_TS_FIX, rf64=False) == 0.0
    assert mt._zero_tail_sec(out) == 0.0 or not out.exists()


# ─────── ffmpeg 8.1.2: mở file GIỮA vùng hỏng → cả khúc = 0 từ đầu (K3) ───────
DAMAGE = (24.0, 29.5)     # vùng AAC hỏng giả lập (s)
GOP_PREROLL = 2.0         # seek trên file CÓ HÌNH lùi về keyframe → giải mã tiếng từ ss − 2 s


def _sim_ff812(src: Path, real_run):
    """Mô phỏng 8.1.2 độc lập phiên bản: lượt full chết ở 25 s (đệm 0 tới hết); mỗi lần mở mà
    điểm bắt đầu GIẢI MÃ rơi vào vùng hỏng → cả khúc = 0 + stderr lỗi. File có hình: giải mã từ
    ss − GOP_PREROLL; file chỉ-tiếng (.m4a/.mka): từ đúng ss."""
    def run(cmd, *a, **kw):
        if not cmd or cmd[0] != "ffmpeg" or "-ac" not in cmd:
            return real_run(cmd, *a, **kw)            # ffprobe, copy luồng tiếng, concat, …
        r = real_run(cmd, *a, **kw)
        inp = cmd[cmd.index("-i") + 1]
        out = Path(cmd[-1])
        if "-ss" not in cmd:
            if inp == str(src) and out.exists():
                _zero_from(out, 25.0)
                r.stderr = FFMPEG8_ERR
            return r
        ss = float(cmd[cmd.index("-ss") + 1])
        dec0 = ss if inp != str(src) else max(0.0, ss - GOP_PREROLL)
        overlap = dec0 < DAMAGE[1] and ss + float(cmd[cmd.index("-t") + 1]) > DAMAGE[0]
        if (DAMAGE[0] <= dec0 < DAMAGE[1] or (overlap and dec0 < DAMAGE[0])) and out.exists():
            _zero_from(out, 0.0 if DAMAGE[0] <= dec0 else max(0.0, DAMAGE[0] - ss))
            r.stderr = FFMPEG8_ERR
        return r
    return run


@needs_ffmpeg
def test_ff812_window_opened_in_damage_all_zero(tmp_path: Path):
    src = tmp_path / "beep.mp4"
    expected = make_beep_video(src, 60.0)
    out = tmp_path / "a.wav"
    with mock.patch.object(mt.subprocess, "run", side_effect=_sim_ff812(src, subprocess.run)):
        info = mt.extract_pcm_from_video(src, out, ac=1, ar=16000)
    assert info["mode"] == "chunked" and info["dead_at"] == pytest.approx(25.0, abs=0.15), info
    assert info["seek_src"] == "audio_only"
    _, ar, n = read_wav(out)
    assert abs(n - 60 * 16000) <= 1
    assert_beeps_at(beep_onsets(out), expected, tol=0.03)        # 33/43/53 phải còn


@needs_ffmpeg
def test_audio_only_seek_source_sample_aligned(tmp_path: Path):
    """Seek trên file chỉ-tiếng (copy) ra ĐÚNG mẫu như seek trên file gốc (file lành)."""
    import array
    src = tmp_path / "beep.mp4"
    make_beep_video(src, 30.0)
    seek_src, shift = mt._audio_seek_source(src, tmp_path)
    assert seek_src != src and seek_src.suffix == ".m4a"
    for ss in (1.0, 11.0, 12.3, 21.9):
        a, b = tmp_path / "a.wav", tmp_path / "b.wav"
        mt._extract_window(src, a, ss=ss, t=5.0, ac=1, ar=16000, af=mt.AUDIO_TS_FIX, rf64=False)
        mt._extract_window(seek_src, b, ss=ss + shift, t=5.0, ac=1, ar=16000, af=mt.AUDIO_TS_FIX, rf64=False)
        xa, xb = read_wav(a)[0], read_wav(b)[0]
        oa, ob = beep_onsets(a), beep_onsets(b)
        assert oa and ob and abs(oa[0] - ob[0]) <= 1.5 / 16000, (ss, oa, ob)
        assert isinstance(xa, array.array) and len(xa) == len(xb)
