"""Preflight / chuẩn hoá nguồn video / resolve nguồn duy nhất (agentK 2026-11-12)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import engines.source_preflight as sp

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="cần ffmpeg/ffprobe")

ROOT = Path(__file__).resolve().parents[1]


def _tp():
    spec = importlib.util.spec_from_file_location("t_pcm_timing", ROOT / "tests" / "test_extract_pcm_timing.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _job(tmp: Path, video: Path | None, **dl) -> Path:
    wd = tmp / "job"
    wd.mkdir(parents=True, exist_ok=True)
    st = {"status": "done", **dl}
    if video is not None:
        st["video_path"] = str(video)
    (wd / "manifest.json").write_text(json.dumps({"stages": {"download": st}}, ensure_ascii=False),
                                      encoding="utf-8")
    return wd


def _dl(wd: Path) -> dict:
    return json.loads((wd / "manifest.json").read_text(encoding="utf-8"))["stages"]["download"]


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    for k in ("VIDEO_PREFLIGHT", "VIDEO_NORMALIZE", "VIDEO_NORMALIZE_DRIFT"):
        monkeypatch.delenv(k, raising=False)


# ───────────── resolve ─────────────

def test_resolve_prefers_norm_then_video_path_then_glob(tmp_path: Path):
    src = tmp_path / "lib.mp4"
    src.write_bytes(b"v")
    wd = _job(tmp_path, src)
    assert sp.resolve_source_video(wd) == src
    norm = wd / "source_norm.mp4"
    norm.write_bytes(b"n")
    d = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))
    d["stages"]["download"]["video_path_norm"] = str(norm)
    (wd / "manifest.json").write_text(json.dumps(d), encoding="utf-8")
    assert sp.resolve_source_video(wd) == norm
    assert sp.original_source_video(wd) == src
    norm.unlink()                                  # file chuẩn hoá mất → quay về gốc
    assert sp.resolve_source_video(wd) == src
    wd2 = _job(tmp_path / "b", None)
    (wd2 / "source.mkv").write_bytes(b"v")
    assert sp.resolve_source_video(wd2) == wd2 / "source.mkv"


def test_all_stage_finders_use_norm(tmp_path: Path):
    """asr/separate/ocr/hardsub/render/server đều trả nguồn chuẩn hoá (V6: mux.py đã bỏ — render_job/render thay)."""
    src = tmp_path / "lib.mp4"
    src.write_bytes(b"v")
    norm = tmp_path / "job" / "source_norm.mp4"
    wd = _job(tmp_path, src)
    norm.write_bytes(b"n")
    d = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))
    d["stages"]["download"]["video_path_norm"] = str(norm)
    (wd / "manifest.json").write_text(json.dumps(d), encoding="utf-8")
    import importlib
    import engines.render as render
    import engines.separate as separate
    assert separate._find_video(wd) == norm
    assert render._find_video(wd) == norm
    for name in ("engines.ocr", "engines.asr.hardsub_asr"):
        try:
            mod = importlib.import_module(name)
        except Exception:  # noqa: BLE001 — thiếu gói nặng ở máy test
            continue
        assert mod._find_video(wd) == norm


def test_asr_resolve_source_uses_norm(tmp_path: Path, monkeypatch):
    import engines.asr.asr as asr
    src = tmp_path / "lib.mp4"
    src.write_bytes(b"v")
    wd = _job(tmp_path, src, video_path_norm=str(tmp_path / "job" / "source_norm.mp4"))
    (wd / "source_norm.mp4").write_bytes(b"n")
    monkeypatch.setenv("ASR_USE_DEMUCS", "0")
    seen = {}
    monkeypatch.setattr(asr, "ensure_audio_wav", lambda s, w, force=False: seen.setdefault("src", Path(s)))
    assert asr.resolve_asr_source(wd) == wd / "source_norm.mp4"
    assert seen["src"] == wd / "source_norm.mp4"


# ───────────── ensure / cờ ─────────────

def test_old_job_not_rechecked(tmp_path: Path, monkeypatch):
    src = tmp_path / "lib.mp4"
    src.write_bytes(b"v")
    wd = _job(tmp_path, src)
    monkeypatch.setattr(sp, "run_preflight", lambda *a, **k: pytest.fail("job cũ không được chạy lại"))
    assert sp.ensure_preflight(wd) is None
    assert sp.stage_source_video(wd) == src


def test_pending_runs_once_and_fatal_raises(tmp_path: Path, monkeypatch):
    src = tmp_path / "lib.mp4"
    src.write_bytes(b"v")
    wd = _job(tmp_path, src, preflight_pending=True)
    n = {"k": 0}

    def fake_check(video, quick=False):
        n["k"] += 1
        return {"ok": False, "fatal": "nguồn KHÔNG có luồng tiếng (audio stream)", "issues": [], "warnings": []}
    monkeypatch.setattr(sp, "check_source", fake_check)
    with pytest.raises(sp.PreflightError, match="luồng tiếng"):
        sp.ensure_preflight(wd)
    dl = _dl(wd)
    assert "preflight_pending" not in dl and dl["preflight"]["action"] == "fatal"
    with pytest.raises(sp.PreflightError):          # lần sau: vẫn fail, KHÔNG kiểm lại
        sp.stage_source_video(wd)
    assert n["k"] == 1
    assert not (wd / sp.LOCK_NAME).exists()


def test_flags_off(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEO_PREFLIGHT", "0")
    assert sp.pending_payload() == {}
    src = tmp_path / "lib.mp4"
    src.write_bytes(b"v")
    wd = _job(tmp_path, src, preflight_pending=True)
    monkeypatch.setattr(sp, "run_preflight", lambda *a, **k: pytest.fail("tắt rồi"))
    assert sp.after_download(wd, src) is None
    assert sp.ensure_preflight(wd) is None
    monkeypatch.setenv("VIDEO_PREFLIGHT", "1")
    assert sp.pending_payload() == {"preflight_pending": True}


def test_inherit_payload():
    assert sp.inherit_payload({"preflight": {"action": "normalized"}}, "p")["preflight"]["action"] == "inherited"
    assert sp.inherit_payload({"preflight": {"action": "none"}}, "p")["preflight"]["inherited_from"] == "p"
    assert sp.inherit_payload({"preflight": {"action": "normalize_failed"}}, "p") == {"preflight_pending": True}
    assert sp.inherit_payload({}, "p") == {}                       # job cha cũ → giữ hành vi cũ
    assert sp.inherit_payload({"preflight_pending": True}, "p") == {"preflight_pending": True}


# ───────────── ffmpeg thật ─────────────

@needs_ffmpeg
def test_check_good_bad_noaudio_novideo(tmp_path: Path):
    T = _tp()
    good = tmp_path / "good.mp4"
    T.make_beep_video(good, 30.0)
    bad = tmp_path / "bad.mp4"
    T.corrupt_middle(good, bad)
    c = sp.check_source(good)
    assert c["ok"] and not c["issues"] and c["decode"]["rc"] == 0
    c = sp.check_source(bad)
    assert not c["ok"] and not c["fatal"] and any("AAC" in s for s in c["issues"])
    noa = tmp_path / "noa.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(good), "-an", "-c", "copy", str(noa)], check=True)
    assert "luồng tiếng" in sp.check_source(noa)["fatal"]
    nov = tmp_path / "nov.m4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(good), "-vn", "-c", "copy", str(nov)], check=True)
    assert "luồng hình" in sp.check_source(nov)["fatal"]
    q = sp.check_source(bad, quick=True)
    assert "decode" not in q


@needs_ffmpeg
def test_run_preflight_healthy_writes_ok_only(tmp_path: Path):
    T = _tp()
    good = tmp_path / "good.mp4"
    T.make_beep_video(good, 20.0)
    md5 = hashlib.md5(good.read_bytes()).hexdigest()
    wd = _job(tmp_path, good, preflight_pending=True)
    sp.ensure_preflight(wd)
    dl = _dl(wd)
    assert dl["preflight"]["ok"] is True and dl["preflight"]["action"] == "none"
    assert "video_path_norm" not in dl and "preflight_pending" not in dl
    assert not list(wd.glob("source_norm*"))
    assert sp.resolve_source_video(wd) == good
    assert hashlib.md5(good.read_bytes()).hexdigest() == md5


@needs_ffmpeg
def test_run_preflight_normalizes_bad_audio(tmp_path: Path):
    T = _tp()
    good = tmp_path / "lib" / "good.mp4"
    good.parent.mkdir()
    expected = T.make_beep_video(good, 40.0)
    bad = tmp_path / "lib" / "bad.mp4"
    T.corrupt_middle(good, bad)
    md5 = hashlib.md5(bad.read_bytes()).hexdigest()
    wd = _job(tmp_path, bad)
    pf = sp.after_download(wd, bad)
    assert pf["action"] == "normalized", pf
    dl = _dl(wd)
    norm = Path(dl["video_path_norm"])
    assert norm.is_file() and norm.parent == wd and dl["norm_reason"]
    assert sp.resolve_source_video(wd) == norm
    assert hashlib.md5(bad.read_bytes()).hexdigest() == md5            # gốc không bị đụng
    c2 = sp.check_source(norm)
    assert c2["ok"], c2
    vdur = sp.probe(bad)["duration"]
    assert abs(c2["probe"]["duration"] - vdur) <= 0.1
    wav = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(norm), "-ac", "1", "-ar", "16000", str(wav)], check=True)
    T.assert_beeps_at(T.beep_onsets(wav), expected, tol=0.03, skip=[(12.0, 25.0)])
    from engines.media_ts import audio_ts_drift
    assert abs(audio_ts_drift(norm)) < 0.05
    # video copy nguyên: cùng số gói hình
    def nb_v(p):
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                            "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(p)],
                           capture_output=True, text=True)
        return r.stdout.strip()
    assert nb_v(norm) == nb_v(bad)


@needs_ffmpeg
def test_normalize_off_keeps_original(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEO_NORMALIZE", "0")
    T = _tp()
    good = tmp_path / "good.mp4"
    T.make_beep_video(good, 20.0)
    bad = tmp_path / "bad.mp4"
    T.corrupt_middle(good, bad)
    wd = _job(tmp_path, bad, preflight_pending=True)
    pf = sp.ensure_preflight(wd)
    assert pf["action"] == "normalize_off" and pf["issues"]
    assert sp.resolve_source_video(wd) == bad


@needs_ffmpeg
def test_scan_is_read_only(tmp_path: Path):
    T = _tp()
    root = tmp_path / "work"
    good = tmp_path / "good.mp4"
    T.make_beep_video(good, 15.0)
    bad = tmp_path / "bad.mp4"
    T.corrupt_middle(good, bad)
    for name, v in (("j1", good), ("j2", bad), ("j3", tmp_path / "missing.mp4")):
        (root / name).mkdir(parents=True)
        (root / name / "manifest.json").write_text(
            json.dumps({"stages": {"download": {"status": "done", "video_path": str(v)}}}), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("preflight_scan", ROOT / "tools" / "preflight_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    snap = lambda: {p: hashlib.md5(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()}  # noqa: E731
    before = snap()
    rows = {r["job"]: r for r in mod.scan(root)}
    assert snap() == before
    assert rows["j1"]["status"] == "ok"
    assert rows["j2"]["status"] == "audio_bad"
    assert rows["j3"]["status"] == "no_source"


def test_server_import_marks_pending_and_find_video_read_only(tmp_path: Path, monkeypatch):
    import server
    src = tmp_path / "lib.mp4"
    src.write_bytes(b"v")
    wd = tmp_path / "job"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"request": {}, "stages": {}}), encoding="utf-8")
    monkeypatch.setattr(server, "_probe_video", lambda p: {})
    monkeypatch.setattr(sp, "run_preflight", lambda *a, **k: pytest.fail("import không được chạy preflight đồng bộ"))
    server._finalize_imported_source(wd, src, orig_name=src.name, source_kind="imported_path", source_mode="direct")
    dl = _dl(wd)
    assert dl["preflight_pending"] is True and dl["video_path"] == str(src.resolve())
    assert server._find_video(wd) == src.resolve()      # GET/preview: chỉ đọc, không chạy preflight
    monkeypatch.setenv("VIDEO_PREFLIGHT", "0")
    server._finalize_imported_source(wd, src, orig_name=src.name, source_kind="imported_path")
    assert "preflight_pending" not in _dl(wd)


# ───────────── V6 (port Q6 2026-11-13) ─────────────

def test_v6_instance_flag_maps_preflight(monkeypatch):
    """Cờ instance video_preflight/video_normalize → PINELINE_FLAG_* (apply_env); env VIDEO_* đặt tay thắng."""
    from core.instance import Instance
    for k in ("VIDEO_PREFLIGHT", "VIDEO_NORMALIZE", "PINELINE_FLAG_VIDEO_PREFLIGHT", "PINELINE_FLAG_VIDEO_NORMALIZE"):
        monkeypatch.delenv(k, raising=False)
    assert sp.preflight_enabled() and sp.normalize_enabled()
    import os
    snap = dict(os.environ)
    try:
        Instance(name="t", port=1, work_root=Path("."), temp=Path("."),
                 extra={"video_preflight": False, "video_normalize": False}).apply_env()
        assert os.environ["PINELINE_FLAG_VIDEO_PREFLIGHT"] == "0"
        assert not sp.preflight_enabled() and not sp.normalize_enabled()
        os.environ["VIDEO_PREFLIGHT"] = "1"
        assert sp.preflight_enabled()
    finally:
        os.environ.clear()
        os.environ.update(snap)


def test_v6_preflight_write_goes_through_job_state(tmp_path: Path, monkeypatch):
    """Ghi download.preflight qua state.job_state.mutate (không Manifest.update/save trực tiếp)."""
    from state import job_state as js
    calls = []
    real = js.mutate
    monkeypatch.setattr(js, "mutate", lambda wd, fn: calls.append(Path(wd)) or real(wd, fn))
    src = tmp_path / "lib.mp4"
    src.write_bytes(b"v")
    wd = _job(tmp_path, src)
    sp._write_download(wd, {"preflight": {"ok": True, "action": "none"}}, drop=("preflight_pending",))
    assert calls == [wd]
    assert _dl(wd)["preflight"]["ok"] is True
