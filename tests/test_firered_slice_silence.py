"""firered_asr: chia slice tại khoảng lặng + đệm chồng lấn (fix 2026-11-13).

Không cần FireRed thật: asr_system.process được mock bằng bộ "nhận dạng" đọc chính file
slice — mỗi câu là sóng vuông biên độ riêng (|x| = A_j hằng) => tách câu chính xác tới MẪU.
Câu bị cắt cụt ở mép clip -> mock trả "S{j}~" (giống FireRed nghe nửa câu).
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

import engines.asr.firered_asr as fa
import engines.separate as separate

RATE = 16000


def _amp(j: int) -> int:
    return 1500 + 250 * j


def _write(path: Path, x: np.ndarray, rate: int = RATE, ch: int = 1) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(x, dtype="<i2").tobytes())


def _synth(sents: list[tuple[float, float]], total: float) -> np.ndarray:
    x = np.zeros(int(round(total * RATE)), dtype=np.int16)
    sq = np.where((np.arange(len(x)) // 40) % 2 == 0, 1, -1).astype(np.int32)   # 200 Hz vuông
    for j, (a, b) in enumerate(sents):
        ia, ib = int(round(a * RATE)), int(round(b * RATE))
        x[ia:ib] = (sq[ia:ib] * _amp(j)).astype(np.int16)
    return x


def _read(path: Path) -> np.ndarray:
    info = separate.wav_info(path)
    with open(path, "rb") as f:
        f.seek(info.data_offset)
        return np.frombuffer(f.read(info.n_frames * 2), dtype="<i2")


class FakeFireRed:
    def __init__(self, sents, src: np.ndarray | None = None):
        self.lens = {j: int(round(b * RATE)) - int(round(a * RATE)) for j, (a, b) in enumerate(sents)}
        self.calls: list[tuple[str, np.ndarray]] = []

    def process(self, path, uttid):
        x = _read(Path(path)).astype(np.int32)
        self.calls.append((path, x.copy()))
        ab = np.abs(x)
        out = []
        i, n = 0, len(ab)
        while i < n:
            if ab[i] == 0:
                i += 1
                continue
            j = i
            while j < n and ab[j] == ab[i]:
                j += 1
            sid = int(round((ab[i] - 1500) / 250))
            full = (j - i) == self.lens.get(sid, -1)
            out.append({"start_ms": i * 1000.0 / RATE, "end_ms": j * 1000.0 / RATE,
                        "text": f"S{sid}" + ("" if full else "~")})
            i = j
        return {"sentences": out}


def _sents_with_gaps(total: float, gap: float, seed: int = 1, cross=()):
    """Câu 1.5–4 s, cách nhau `gap`; ép có câu vắt qua từng mốc trong `cross`."""
    rng = np.random.default_rng(seed)
    out, t = [], 0.5
    cross = list(cross)
    while t < total - 5:
        ln = float(np.round(rng.uniform(1.5, 4.0), 3))
        if cross and t + ln + gap > cross[0] - 0.3:
            t = round(max(t, cross[0] - ln / 2), 3)  # câu nằm vắt đúng mốc
            assert t < cross[0] < t + ln
            cross.pop(0)
        out.append((round(t, 3), round(t + ln, 3)))
        t = round(t + ln + gap, 3)
    return out


def _check(rows, sents):
    texts = [r["text"] for r in rows]
    assert texts == [f"S{j}" for j in range(len(sents))], texts      # không cắt/lặp/mất
    for r, (a, b) in zip(rows, sents):
        assert abs(r["start"] - a) <= 0.0015 and abs(r["end"] - b) <= 0.0015, (r, a, b)


CONF = {"slice_sec": 30.0, "search_sec": 8.0, "pad_sec": 2.0, "pad_nosil_sec": 10.0,
        "min_sil_sec": 0.3, "sil_db": -40.0}


def test_hard_cut_would_split(tmp_path):
    """Đối chứng: cắt cứng tại 30/60/90 s => có câu "~" (lỗi cũ)."""
    sents = _sents_with_gaps(100.0, 0.6, cross=(30.0, 60.0, 90.0))
    assert sum(any(a < c < b for a, b in sents) for c in (30, 60, 90)) == 3
    x = _synth(sents, 100.0)
    fake = FakeFireRed(sents)
    bad = 0
    for k in range(4):
        clip = tmp_path / f"h{k}.wav"
        _write(clip, x[k * 30 * RATE:(k + 1) * 30 * RATE])
        bad += sum(s["text"].endswith("~") for s in fake.process(clip, "h")["sentences"])
    assert bad >= 6


def test_silence_cuts_no_split_dup_loss(tmp_path, capsys):
    sents = _sents_with_gaps(100.0, 0.6, cross=(30.0, 60.0, 90.0))
    x = _synth(sents, 100.0)
    wav = tmp_path / "audio.wav"
    _write(wav, x)
    fake = FakeFireRed(sents)
    plan = fa.plan_slices(wav, CONF)
    assert len(plan) == 4
    for p in plan[1:]:
        c = p["core0"] / RATE
        assert p["sil_left"] and p["sil_left"] >= 0.3
        # điểm cắt nằm trong khoảng lặng (không trong câu nào)
        assert not any(a < c < b for a, b in sents), c
    rows = fa.transcribe_sliced(fake.process, wav, tmp_path, conf=CONF)
    _check(rows, sents)
    assert "4 slice, cắt tại khoảng lặng" in capsys.readouterr().out
    # cắt theo mẫu chính xác: clip == src[start:end] từng mẫu
    for (path, data), p in zip(fake.calls, plan):
        assert np.array_equal(data, x[p["start"]:p["end"]].astype(np.int32))
    # lõi phủ kín, không chồng
    assert plan[0]["core0"] == 0 and plan[-1]["core1"] == len(x)
    assert all(plan[i]["core1"] == plan[i + 1]["core0"] for i in range(3))


def test_no_silence_falls_back_to_overlap(tmp_path, capsys):
    """Nói liên tục (không khe lặng): cắt ở năng lượng thấp nhất + đệm lớn, vẫn đủ câu."""
    sents, t = [], 0.0
    rng = np.random.default_rng(7)
    while t < 95.0:
        ln = float(np.round(rng.uniform(1.5, 4.0), 3))
        sents.append((round(t, 3), round(t + ln, 3)))
        t = round(t + ln, 3)
    total = sents[-1][1]
    x = _synth(sents, total)
    wav = tmp_path / "audio.wav"
    _write(wav, x)
    plan = fa.plan_slices(wav, CONF)
    assert len(plan) >= 3
    assert all(p["sil_left"] == 0.0 for p in plan[1:])
    assert plan[1]["start"] == plan[1]["core0"] - 10 * RATE
    rows = fa.transcribe_sliced(FakeFireRed(sents).process, wav, tmp_path, conf=CONF)
    _check(rows, sents)
    assert "KHÔNG lặng" in capsys.readouterr().out


def test_slice_sec_zero_single_pass(tmp_path):
    sents = _sents_with_gaps(100.0, 0.6, cross=(30.0, 60.0))
    x = _synth(sents, 100.0)
    wav = tmp_path / "audio.wav"
    _write(wav, x)
    fake = FakeFireRed(sents)
    rows = fa.transcribe_sliced(fake.process, wav, tmp_path, conf=dict(CONF, slice_sec=0))
    assert len(fake.calls) == 1 and fake.calls[0][0] == str(wav)
    _check(rows, sents)


def test_slice_sec_zero_from_env(monkeypatch):
    monkeypatch.setenv("FIRERED_SLICE_SEC", "0")
    assert fa.slice_cfg()["slice_sec"] == 0.0
    monkeypatch.delenv("FIRERED_SLICE_SEC")
    monkeypatch.setattr(fa, "cfg", lambda k, d=None: d)
    c = fa.slice_cfg()
    assert c["slice_sec"] == 1200.0 and c["search_sec"] == 60.0 and c["pad_sec"] == 2.0


def test_prefers_silence_near_target(tmp_path):
    # tiếng liên tục mức cao, 2 khoảng lặng: 0.5 s tại 28 s và 0.4 s tại 30.6 s
    x = (np.where((np.arange(60 * RATE) // 40) % 2 == 0, 1, -1) * 4000).astype(np.int16)
    x[int(27.75 * RATE):int(28.25 * RATE)] = 0
    x[int(30.4 * RATE):int(30.8 * RATE)] = 0
    wav = tmp_path / "a.wav"
    _write(wav, x)
    info = separate.wav_info(wav)
    with open(wav, "rb") as f:
        c, sil = fa.find_cut_frame(info, f, 30.0, 22.0, 38.0)
    assert abs(c / RATE - 30.6) < 0.03 and sil >= 0.3


def test_non16k_input_converted(tmp_path):
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("không có ffmpeg")
    x = (np.where((np.arange(5 * 44100) // 100) % 2 == 0, 1, -1) * 3000).astype(np.int16)
    st = np.repeat(x, 2)
    wav = tmp_path / "st.wav"
    _write(wav, st, rate=44100, ch=2)
    seen = []
    fa.transcribe_sliced(lambda p, u: seen.append(separate.wav_info(p)) or {}, wav, tmp_path,
                         conf=dict(CONF, slice_sec=0))
    assert seen and seen[0].rate == 16000 and seen[0].channels == 1


def test_run_asr_firered_end_to_end_mocked(tmp_path, monkeypatch):
    """run_asr_firered với package fireredasr2s giả: asr.json đủ câu, offset đúng."""
    import json
    import sys
    import types

    sents = _sents_with_gaps(100.0, 0.6, cross=(30.0, 60.0, 90.0))
    x = _synth(sents, 100.0)
    work = tmp_path / "job"
    work.mkdir()
    _write(work / "audio.wav", x)
    models = tmp_path / "models"
    for d in ("FireRedASR2-AED", "FireRedVAD", "FireRedLID", "FireRedPunc"):
        (models / d).mkdir(parents=True)
    fake = FakeFireRed(sents)

    class _Cfg:
        def __init__(self, *a, **k):
            pass

    class _System:
        def __init__(self, *a, **k):
            pass

        def process(self, path, uttid):
            return fake.process(path, uttid)

    pkg = types.ModuleType("fireredasr2s")
    pkg.FireRedAsr2System, pkg.FireRedAsr2SystemConfig = _System, _Cfg
    mods = {"fireredasr2s": pkg}
    for sub, name in (("fireredasr2", "FireRedAsr2Config"), ("fireredlid", "FireRedLidConfig"),
                      ("fireredpunc", "FireRedPuncConfig"), ("fireredvad", "FireRedVadConfig")):
        m = types.ModuleType(f"fireredasr2s.{sub}")
        setattr(m, name, _Cfg)
        mods[f"fireredasr2s.{sub}"] = m
    for k, v in mods.items():
        monkeypatch.setitem(sys.modules, k, v)
    monkeypatch.setattr(fa, "_roots", lambda: (tmp_path, models))
    monkeypatch.setattr(fa, "_ensure_firered_importable", lambda root: None)
    monkeypatch.setattr(fa, "resolve_asr_source", lambda *a, **k: work / "audio.wav")
    monkeypatch.setattr(fa, "ensure_audio_wav", lambda *a, **k: False)
    monkeypatch.setattr(fa, "free_gpu_vram", lambda *a, **k: None)
    monkeypatch.setattr(fa, "split_long_segments", lambda rows, **k: rows)
    for k, v in (("FIRERED_SLICE_SEC", "30"), ("FIRERED_CUT_SEARCH_SEC", "8")):
        monkeypatch.setenv(k, v)
    fa.run_asr_firered(work, device="cpu", force=True)
    rows = json.loads((work / "asr.json").read_text(encoding="utf-8"))
    _check(rows, sents)
    assert len(fake.calls) == 4


def test_boundary_scan_tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "asr_boundary_scan", Path(__file__).resolve().parents[1] / "tools" / "asr_boundary_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = [{"i": 0, "start": 1197.6, "end": 1198.5, "text": "就是现在。"},
            {"i": 1, "start": 1199.0, "end": 1200.0, "text": "您已成为天。"},
            {"i": 2, "start": 1200.0, "end": 1200.8, "text": "命之子。"},
            {"i": 3, "start": 2398.0, "end": 2400.0, "text": "我想学这个。"},
            {"i": 4, "start": 2400.0, "end": 2401.0, "text": "个猫咪后空翻。"},
            {"i": 5, "start": 3590.0, "end": 3592.0, "text": "好。"},
            {"i": 6, "start": 3605.0, "end": 3700.0, "text": "嗯。"}]
    hits = mod.scan_rows(rows)
    assert [h["boundary"] for h in hits] == [1200.0, 2400.0]
    assert "cắt đôi" in hits[0]["kind"] and "lặp chữ" in hits[1]["kind"]
