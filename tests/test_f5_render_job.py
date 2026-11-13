"""F5 — engines/render_job.do_render (thân _do_render cũ) với engine giả: luồng assemble → run_render → done/fail.

Không ffmpeg/soundfile: thay engines.assemble/render/subs/separate/render_fx bằng module giả trong sys.modules.
"""
from __future__ import annotations

import json
import sys
import types

import pytest


@pytest.fixture
def stubs(monkeypatch):
    import core  # noqa: F401 — gói cha phải có trong sys.modules trước khi gắn module giả
    import engines  # noqa: F401
    rec = {"render": [], "assemble": [], "separate": []}

    asm = types.ModuleType("engines.assemble")
    asm.NAIVE_MAX_SEC = 1800.0

    def assemble_fit(wd):
        rec["assemble"].append("fit")
        if rec.get("assemble_ok", True):
            (wd / "dub_fit.wav").write_bytes(b"0" * 2000)
    asm.assemble_fit = assemble_fit
    asm.assemble = lambda wd: rec["assemble"].append("naive")

    rnd = types.ModuleType("engines.render")

    def run_render(wd, **kw):
        rec["render"].append(kw)
        (wd / "output.mp4").write_bytes(b"0" * 1_000_001)
    rnd.run_render = run_render

    sep = types.ModuleType("engines.separate")
    sep.run_separate = lambda wd: rec["separate"].append(str(wd))
    fx = types.ModuleType("engines.render_fx")
    fx.clamp_zoom = lambda z: float(z)
    subs = types.ModuleType("engines.subs")
    cfg = types.ModuleType("core.config")
    for name, mod in (("engines.assemble", asm), ("engines.render", rnd), ("engines.separate", sep),
                      ("engines.render_fx", fx), ("engines.subs", subs), ("core.config", cfg)):
        monkeypatch.setitem(sys.modules, name, mod)
        pkg, _, attr = name.rpartition(".")
        monkeypatch.setattr(sys.modules[pkg], attr, mod, raising=False)
    return rec


def _mux(wd):
    return json.loads((wd / "manifest.json").read_text(encoding="utf-8"))["stages"].get("mux") or {}


def test_do_render_assemble_then_render_then_done(job, stubs):
    from engines.render_job import do_render
    (job / "tts.json").write_text(json.dumps([{"end": 10.0}]), encoding="utf-8")
    (job / "dub_tts.wav").write_bytes(b"0" * 2000)
    out = do_render(job, force=False, zoom="1.2", audio_mode="voice_bgm", hardsub_blur=False)
    assert out.endswith("output.mp4")
    assert stubs["assemble"] == ["fit"] and stubs["separate"] == []
    kw = stubs["render"][0]
    assert kw["force"] is True and kw["zoom"] == 1.2 and kw["brand_text"] is None and kw["vbitrate"] == "8M"
    assert not (job / "dub_tts.wav").exists()                     # bỏ track kia để _pick_dub đúng
    st = _mux(job)
    assert st["status"] == "done" and st["output_path"].endswith("output.mp4")
    assert st["phase"] == "encode" and "started_at" not in st


def test_do_render_demucs_runs_separate_first(job, stubs):
    from engines.render_job import do_render
    do_render(job, audio_mode="demucs_keep")
    assert stubs["separate"] == [str(job)] and stubs["render"]


def test_do_render_missing_dub_fails(job, stubs):
    from engines.render_job import do_render
    stubs["assemble_ok"] = False
    (job / "tts.json").write_text(json.dumps([{"end": 10.0}]), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Chưa có track giọng Việt"):
        do_render(job)
    st = _mux(job)
    assert st["status"] == "failed" and "track giọng" in st["error"] and stubs["render"] == []


def test_do_render_skip_when_done(job, stubs):
    from engines.render_job import do_render
    from state import job_state as js
    (job / "output.mp4").write_bytes(b"0" * 1_000_001)
    (job / "dub_fit.wav").write_bytes(b"0" * 2000)
    js.mark_done(job, "mux")
    assert do_render(job, force=False) is None and stubs["render"] == []
    assert _mux(job)["status"] == "done"
