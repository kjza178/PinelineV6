"""separate.py chia khúc theo mẫu + ASR dùng vocals mặc định (fix 2026-11-12 asr-demucs-default).

Demucs giả lập bằng phép biến đổi THEO TỪNG MẪU (vocals = x//2, no_vocals = x - x//2):
ghép đúng thì vocals.wav == f(audio gốc) từng byte -> chứng minh không lệch/lặp/mất mẫu.
"""
from __future__ import annotations

import json
import shutil
import struct
import wave
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

import engines.asr.asr as asr
import engines.separate as separate
from core.manifest import Manifest

SR = separate.SR
HAVE_FFMPEG = bool(shutil.which("ffmpeg"))


def _write_wav(path: Path, data: np.ndarray, rate: int = SR) -> None:
    data = np.asarray(data, dtype=np.int16)
    if data.ndim == 1:
        data = data[:, None]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(data.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.astype("<i2").tobytes())


def _read_wav(path: Path) -> np.ndarray:
    info = separate.wav_info(path)
    with open(path, "rb") as f:
        f.seek(info.data_offset)
        raw = f.read(info.n_frames * info.block_align)
    return np.frombuffer(raw, dtype="<i2").reshape(-1, info.channels)


def _synth(total_sec: float, silences: list[tuple[float, float]], seed: int = 0) -> np.ndarray:
    """Stereo 'thoại' liên tục (nhiễu + sin, ~-6dB) trừ các khoảng lặng tuyệt đối."""
    n = int(round(total_sec * SR)) + 7          # số mẫu lẻ, không tròn giây
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    sig = 9000 * np.sin(2 * np.pi * 220 * t) + rng.normal(0, 3000, n)
    for a, b in silences:
        sig[int(a * SR):int(b * SR)] = 0
    left = np.clip(sig, -32000, 32000).astype(np.int16)
    right = np.clip(sig * 0.7, -32000, 32000).astype(np.int16)
    return np.stack([left, right], axis=1)


def _fake_demucs_factory(calls: list, fail_on: set[int] | None = None, extra_frames: int = 0):
    def fake(src_audio, tmp_out, device, work_dir, m, chunk_idx=0, n_chunks=1):
        calls.append(chunk_idx)
        if fail_on and chunk_idx in fail_on:
            raise RuntimeError("CUDA out of memory (giả lập)")
        x = _read_wav(Path(src_audio)).astype(np.int32)
        voc = (x // 2).astype(np.int16)
        nv = (x - x // 2).astype(np.int16)
        if extra_frames:
            pad = np.zeros((extra_frames, x.shape[1]), dtype=np.int16)
            voc, nv = np.concatenate([voc, pad]), np.concatenate([nv, pad])
        d = Path(tmp_out) / "htdemucs" / Path(src_audio).stem
        d.mkdir(parents=True, exist_ok=True)
        _write_wav(d / "vocals.wav", voc)
        _write_wav(d / "no_vocals.wav", nv)
        return d / "no_vocals.wav", d / "vocals.wav"
    return fake


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMUCS_CHUNK_SEC", "60")
    monkeypatch.setenv("DEMUCS_LONG_THRESHOLD_SEC", "70")
    monkeypatch.setenv("DEMUCS_CHUNK_PAD_SEC", "2")
    monkeypatch.setenv("DEMUCS_CUT_SEARCH_SEC", "15")
    monkeypatch.setattr(separate, "free_gpu_vram", lambda *a, **k: None)
    silences = [(52.0, 53.5), (103.0, 104.2), (146.0, 147.0)]
    audio = _synth(200.0, silences)
    src = tmp_path / "_orig_src.wav"
    _write_wav(src, audio)
    vid = tmp_path / "source.mp4"
    vid.write_bytes(b"\x00" * 4096)
    Manifest(tmp_path).mark_done("download", video_path=str(vid))

    def fake_extract(video, out_wav):
        shutil.copyfile(src, out_wav)
    monkeypatch.setattr(separate, "_extract_orig_wav", fake_extract)
    return tmp_path, audio, silences


def _no_finder(monkeypatch):
    import engines.split_parts as split_parts
    monkeypatch.setattr(split_parts, "find_best_cut_point", lambda p, t, search_radius_sec=90.0: t)


def test_chunked_exact_samples_and_content(job, monkeypatch):
    wd, audio, _ = job
    _no_finder(monkeypatch)
    calls: list = []
    with mock.patch.object(separate, "_run_demucs_on_file", side_effect=_fake_demucs_factory(calls)):
        st = separate.run_separate(wd, device="cpu")
    assert st["n_chunks"] == 4 and calls == [0, 1, 2, 3]
    voc, nv = _read_wav(wd / "vocals.wav"), _read_wav(wd / "no_vocals.wav")
    assert voc.shape == audio.shape and nv.shape == audio.shape          # đúng tới từng mẫu
    x = audio.astype(np.int32)
    assert np.array_equal(voc, (x // 2).astype(np.int16))                 # không lệch/lặp mẫu ở ranh giới
    assert np.array_equal(nv, (x - x // 2).astype(np.int16))
    assert st["n_frames"] == audio.shape[0]
    assert not (wd / "_demucs_chunks").exists()
    assert not (wd / "audio.wav").exists()      # separate KHÔNG ghi/ghi đè audio.wav nữa


def test_short_audio_single_pass(job, monkeypatch):
    wd, audio, _ = job
    monkeypatch.setenv("DEMUCS_LONG_THRESHOLD_SEC", "600")
    calls: list = []
    with mock.patch.object(separate, "_run_demucs_on_file", side_effect=_fake_demucs_factory(calls)):
        st = separate.run_separate(wd, device="cpu")
    assert calls == [0] and st["n_chunks"] == 1
    assert _read_wav(wd / "vocals.wav").shape == audio.shape


def test_demucs_output_length_mismatch_trimmed(job, monkeypatch):
    wd, audio, _ = job
    _no_finder(monkeypatch)
    calls: list = []
    with mock.patch.object(separate, "_run_demucs_on_file",
                           side_effect=_fake_demucs_factory(calls, extra_frames=5)):
        separate.run_separate(wd, device="cpu")
    voc = _read_wav(wd / "vocals.wav")
    assert voc.shape == audio.shape
    assert np.array_equal(voc, (audio.astype(np.int32) // 2).astype(np.int16))


def test_plan_cuts_snap_to_silence_with_finder():
    silences = [(52.0, 53.5), (103.0, 104.2), (146.0, 147.0)]

    def finder(t):   # khoảng lặng gần nhất (như split_parts.find_best_cut_point)
        best = min(silences, key=lambda s: abs((s[0] + s[1]) / 2 - t))
        return (best[0] + best[1]) / 2 if abs((best[0] + best[1]) / 2 - t) <= 15 else t
    n = 200 * SR + 7
    plan = separate.plan_chunks(n, SR, {"chunk_sec": 60, "threshold_sec": 70, "pad_sec": 2, "search_sec": 15},
                                cut_finder=finder)
    assert plan[0][0] == 0 and plan[-1][1] == n
    assert all(plan[i][1] == plan[i + 1][0] for i in range(len(plan) - 1))
    for s, _ in plan[1:]:
        assert any(a * SR <= s <= b * SR for a, b in silences), s / SR


def test_plan_rejects_bad_cut_falls_back_to_target():
    n = 200 * SR
    plan = separate.plan_chunks(n, SR, {"chunk_sec": 60, "threshold_sec": 70, "pad_sec": 2, "search_sec": 15},
                                cut_finder=lambda t: 1.0)   # điểm vô lý -> dùng mục tiêu
    cuts = [s for s, _ in plan[1:]]
    assert cuts == sorted(cuts) and len(set(cuts)) == len(cuts)
    assert all(e > s for s, e in plan)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="cần ffmpeg cho silencedetect")
def test_real_find_best_cut_point_in_silence(job, monkeypatch):
    wd, audio, silences = job
    calls: list = []
    with mock.patch.object(separate, "_run_demucs_on_file", side_effect=_fake_demucs_factory(calls)):
        st = separate.run_separate(wd, device="cpu")
    assert st["cut_points_sec"], st
    for c in st["cut_points_sec"]:
        assert any(a <= c <= b for a, b in silences), (c, silences)
    assert _read_wav(wd / "vocals.wav").shape == audio.shape


def test_resume_after_chunk_failure(job, monkeypatch):
    wd, audio, _ = job
    _no_finder(monkeypatch)
    calls: list = []
    with mock.patch.object(separate, "_run_demucs_on_file",
                           side_effect=_fake_demucs_factory(calls, fail_on={2})):
        with pytest.raises(RuntimeError):
            separate.run_separate(wd, device="cpu")
    assert calls == [0, 1, 2]
    assert Manifest(wd).stage("separate")["status"] == "failed"
    calls2: list = []
    with mock.patch.object(separate, "_run_demucs_on_file", side_effect=_fake_demucs_factory(calls2)):
        separate.run_separate(wd, device="cpu")
    assert calls2 == [2, 3]          # khúc 0,1 dùng lại
    voc = _read_wav(wd / "vocals.wav")
    assert np.array_equal(voc, (audio.astype(np.int32) // 2).astype(np.int16))


def test_no_rerun_when_valid_and_rerun_when_truncated(job, monkeypatch):
    wd, audio, _ = job
    _no_finder(monkeypatch)
    calls: list = []
    fake = _fake_demucs_factory(calls)
    with mock.patch.object(separate, "_run_demucs_on_file", side_effect=fake):
        separate.run_separate(wd, device="cpu")
        n1 = len(calls)
        separate.run_separate(wd, device="cpu")
        assert len(calls) == n1                       # đã có -> không chạy lại
        # vocals.wav hỏng (cụt) -> tách lại
        raw = (wd / "vocals.wav").read_bytes()
        (wd / "vocals.wav").write_bytes(raw[: len(raw) // 2])
        separate.run_separate(wd, device="cpu")
        assert len(calls) > n1
    assert _read_wav(wd / "vocals.wav").shape == audio.shape


def test_legacy_outputs_reused_without_n_frames(job, monkeypatch):
    """Job cũ (separate done kiểu cũ, chưa có n_frames) -> độ dài khớp video thì dùng lại."""
    wd, audio, _ = job
    _write_wav(wd / "vocals.wav", audio)
    _write_wav(wd / "no_vocals.wav", audio)
    monkeypatch.setattr(separate, "_probe_duration", lambda p: audio.shape[0] / SR + 0.4)
    with mock.patch.object(separate, "_run_demucs_on_file") as dm:
        separate.run_separate(wd, device="cpu")
        assert not dm.called
    assert Manifest(wd).is_done("separate")


def test_wav_info_rf64_header(tmp_path):
    data = (np.arange(1000, dtype=np.int16)).reshape(-1, 2)
    fmt = struct.pack("<HHIIHH", 1, 2, SR, SR * 4, 4, 16)
    body = data.tobytes()
    p = tmp_path / "x.wav"
    ds64 = struct.pack("<QQQI", 0, len(body), 0, 0)
    blob = (b"RF64" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE" + b"ds64" + struct.pack("<I", 28) + ds64
            + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", 0xFFFFFFFF) + body)
    p.write_bytes(blob)
    info = separate.wav_info(p)
    assert info.n_frames == 500 and info.channels == 2
    out = tmp_path / "y.wav"
    separate.write_wav_slice(p, out, 100, 50)
    assert np.array_equal(_read_wav(out), data[100:150])
    with wave.open(str(out), "rb") as w:   # header RIFF chuẩn
        assert w.getnframes() == 50


# ─────────────── ASR chọn nguồn (asr.resolve_asr_source) ───────────────

def _job_with_video(tmp_path) -> Path:
    vid = tmp_path / "source.mp4"
    vid.write_bytes(b"FAKE")
    Manifest(tmp_path).mark_done("download", video_path=str(vid))
    return vid


def _fake_sep_ok(work_dir, device="cuda", force=False):
    wd = Path(work_dir)
    (wd / "vocals.wav").write_bytes(b"RIFF" + b"\x00" * 2000)
    (wd / "no_vocals.wav").write_bytes(b"RIFF" + b"\x00" * 2000)
    Manifest(wd).mark_done("separate")
    return {}


def test_asr_default_uses_vocals(tmp_path, monkeypatch):
    monkeypatch.delenv("ASR_USE_DEMUCS", raising=False)
    _job_with_video(tmp_path)
    with mock.patch("engines.separate.run_separate", side_effect=_fake_sep_ok) as sep, \
         mock.patch("engines.asr.asr.ensure_audio_wav") as ens:
        res = asr.resolve_asr_source(tmp_path, force_demucs=True)
    assert res == tmp_path / "vocals.wav"
    assert sep.call_args.kwargs.get("force") is False     # force ASR không ép tách lại Demucs
    assert not ens.called
    assert Manifest(tmp_path).stage("asr")["phase"] == "asr"


def test_asr_use_demucs_0_keeps_old_behavior(tmp_path, monkeypatch):
    monkeypatch.setenv("ASR_USE_DEMUCS", "0")
    vid = _job_with_video(tmp_path)
    with mock.patch("engines.separate.run_separate") as sep, mock.patch("engines.asr.asr.ensure_audio_wav") as ens:
        res = asr.resolve_asr_source(tmp_path)
    assert res == vid and not sep.called
    assert ens.call_args.args[0] == vid


def test_asr_demucs_failure_falls_back_to_original(tmp_path, monkeypatch):
    monkeypatch.delenv("ASR_USE_DEMUCS", raising=False)
    vid = _job_with_video(tmp_path)
    with mock.patch("engines.separate.run_separate", side_effect=RuntimeError("CUDA out of memory")), \
         mock.patch("engines.asr.asr.ensure_audio_wav") as ens:
        res = asr.resolve_asr_source(tmp_path)
    assert res == vid
    assert ens.call_args.args[0] == vid
    st = Manifest(tmp_path).stage("asr")
    assert "out of memory" in st.get("demucs_fallback", "")


def test_asr_demucs_missing_module_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("ASR_USE_DEMUCS", raising=False)
    vid = _job_with_video(tmp_path)
    with mock.patch("engines.separate.run_separate", side_effect=ModuleNotFoundError("No module named demucs")), \
         mock.patch("engines.asr.asr.ensure_audio_wav"):
        assert asr.resolve_asr_source(tmp_path) == vid


def test_asr_demucs_stop_is_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.delenv("ASR_USE_DEMUCS", raising=False)
    _job_with_video(tmp_path)
    (tmp_path / ".stop").write_text("1")
    with mock.patch("engines.separate.run_separate", side_effect=RuntimeError("Đã dừng tách nền")), \
         mock.patch("engines.asr.asr.ensure_audio_wav"):
        with pytest.raises(RuntimeError):
            asr.resolve_asr_source(tmp_path)


def test_asr_done_job_untouched(tmp_path, monkeypatch):
    monkeypatch.delenv("ASR_USE_DEMUCS", raising=False)
    vid = _job_with_video(tmp_path)
    (tmp_path / "asr.json").write_text("[]", encoding="utf-8")
    Manifest(tmp_path).mark_done("asr", asr_path=str(tmp_path / "asr.json"))
    before = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["stages"]["asr"]
    with mock.patch("engines.separate.run_separate") as sep, mock.patch("engines.asr.asr.ensure_audio_wav") as ens:
        res = asr.resolve_asr_source(tmp_path)
    assert not sep.called and res == vid
    assert ens.call_args.kwargs.get("force") is False
    after = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["stages"]["asr"]
    assert after == before


def test_asr_done_with_vocals_marker_keeps_vocals(tmp_path, monkeypatch):
    _job_with_video(tmp_path)
    (tmp_path / "asr.json").write_text("[]", encoding="utf-8")
    Manifest(tmp_path).mark_done("asr")
    (tmp_path / "vocals.wav").write_bytes(b"RIFF" + b"\x00" * 2000)
    (tmp_path / "audio.wav.json").write_text(json.dumps({"src": "vocals.wav"}), encoding="utf-8")
    with mock.patch("engines.separate.run_separate") as sep:
        assert asr.resolve_asr_source(tmp_path) == tmp_path / "vocals.wav"
    assert not sep.called


@pytest.mark.skipif(not HAVE_FFMPEG, reason="cần ffmpeg")
def test_audio_wav_from_vocals_same_length_as_original(job, monkeypatch):
    """audio.wav 16k (từ vocals.wav) dài bằng audio gốc — media_ts/ASR timing không đổi trục."""
    wd, audio, _ = job
    _no_finder(monkeypatch)
    with mock.patch.object(separate, "_run_demucs_on_file", side_effect=_fake_demucs_factory([])):
        separate.run_separate(wd, device="cpu")
    out = wd / "audio.wav"
    asr.extract_audio(wd / "vocals.wav", out)
    with wave.open(str(out), "rb") as w:
        d16 = w.getnframes() / w.getframerate()
    assert abs(d16 - audio.shape[0] / SR) < 0.01


@pytest.mark.skipif(not HAVE_FFMPEG, reason="cần ffmpeg")
def test_real_ffmpeg_extract_end_to_end(tmp_path, monkeypatch):
    """Video thật (ffmpeg lavfi, 150.37 s, 48k) -> WAV gốc 44.1k -> chia khúc -> ghép: vocals/no_vocals
    = đúng số mẫu audio gốc, và đúng nội dung theo mẫu."""
    import subprocess
    monkeypatch.setenv("DEMUCS_CHUNK_SEC", "60")
    monkeypatch.setenv("DEMUCS_LONG_THRESHOLD_SEC", "70")
    monkeypatch.setenv("DEMUCS_CHUNK_PAD_SEC", "3")
    monkeypatch.setattr(separate, "free_gpu_vram", lambda *a, **k: None)
    vid = tmp_path / "source.mkv"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000",
                    "-f", "lavfi", "-i", "color=c=black:s=64x36:r=5", "-t", "150.37",
                    "-c:a", "pcm_s16le", "-ac", "2", "-c:v", "ffv1", str(vid)], check=True)
    Manifest(tmp_path).mark_done("download", video_path=str(vid))
    ref = tmp_path / "_ref.wav"
    separate._extract_orig_wav(vid, ref)
    ref_a = _read_wav(ref)
    with mock.patch.object(separate, "_run_demucs_on_file", side_effect=_fake_demucs_factory([])):
        st = separate.run_separate(tmp_path, device="cpu")
    voc, nv = _read_wav(tmp_path / "vocals.wav"), _read_wav(tmp_path / "no_vocals.wav")
    assert st["n_chunks"] >= 2
    assert voc.shape == ref_a.shape == nv.shape
    assert np.array_equal(voc, (ref_a.astype(np.int32) // 2).astype(np.int16))
    assert abs(voc.shape[0] / SR - 150.37) < 0.05
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=duration_ts", "-of", "csv=p=0",
                        str(tmp_path / "vocals.wav")], capture_output=True, text=True)
    assert int(r.stdout.strip()) == voc.shape[0]      # ffmpeg đọc header ta ghi đúng số mẫu
