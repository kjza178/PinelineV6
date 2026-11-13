"""F2 gate: core/job_opts.py — 1 job bấm từ UI / hàng chờ / agent → CÙNG opts. Không cần fastapi."""
from __future__ import annotations

import ast
import json
import types
from pathlib import Path

import pytest

import core.batch_config as batch_config
from core import job_opts

ROOT = Path(__file__).resolve().parents[1]

BATCH = {**batch_config.DEFAULTS, "audio_mode": "sfx_lib", "orig_vol": 0.28, "grain": "medium",
         "brand_mode": "drift", "brand_text": "Kênh A", "voice": "batch-voice"}


def _job(tmp_path: Path, queue_opts: dict | None = None, name: str = "j") -> Path:
    wd = tmp_path / name
    wd.mkdir(exist_ok=True)
    data = {"stages": {"tts": {"voice": "saved-voice"}}}
    if queue_opts is not None:
        data["queue_opts"] = queue_opts
    (wd / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    return wd


def _req(**sent):
    """Giả RunAllReq/MuxReq pydantic: có mặc định model nhưng model_fields_set = trường gửi thật."""
    base = {"audio_mode": "voice_bgm", "voice_vol": 0.2, "orig_vol": 0.6, "zoom": 1.12, "lut": True,
            "grain": "weak", "vbitrate": "8M", "hardsub_blur": True, "voice": None, "fit": True}
    base.update(sent)
    return types.SimpleNamespace(**base, model_fields_set=set(sent))


@pytest.fixture(autouse=True)
def _batch(monkeypatch):
    monkeypatch.setattr(batch_config, "load", lambda: dict(BATCH))


# ───────────── gate: 3 đầu vào → cùng opts ─────────────

def _via_ui(wd, sent):              # server.run_all / mux_job
    return job_opts.normalize(job_opts.for_job(wd, job_opts.explicit_from_req(_req(**sent)), fill_voice=False))


def _via_queue_ui(wd, sent):        # POST /api/queue/jobs (chụp lúc xếp) → _queue_run_one
    stored = job_opts.for_job(wd, dict(sent), batch=job_opts.load_batch(), fill_voice=False)
    return job_opts.normalize(job_opts.for_job(wd, stored, fill_voice=False))


def _via_agent_queue(wd, sent):     # routes_agent._agent_queue_add (opts thô) → _queue_run_one
    return job_opts.normalize(job_opts.for_job(wd, dict(sent), fill_voice=False))


@pytest.mark.parametrize("pinned", [None, {}, {"audio_mode": "duck_bgm", "orig_vol": 0.3, "zoom": "1.15",
                                            "brand_mode": "off", "voice": "old-pinned-voice"}])
@pytest.mark.parametrize("sent", [{}, {"audio_mode": "voice_only", "grain": "off", "hardsub_blur": False},
                                  {"voice": "v9", "speed": 1.1, "zoom": "off"}])
def test_ui_queue_agent_same_opts(tmp_path, pinned, sent):
    wd = _job(tmp_path, pinned)
    a, b, c = _via_ui(wd, sent), _via_queue_ui(wd, sent), _via_agent_queue(wd, sent)
    assert a == b == c
    assert job_opts.audio(a) == job_opts.audio(b) == job_opts.audio(c)
    assert job_opts.pipeline_render_kw(a) == job_opts.pipeline_render_kw(c)
    assert job_opts.render_kwargs(a) == job_opts.render_kwargs(c)


def test_agent_mux_run_matches_ui_mux(tmp_path):
    """agent mux_run: for_job(wd, opts) → render_kwargs → MuxReq → mux_job lại resolve; = UI Mux."""
    wd = _job(tmp_path, {"audio_mode": "duck_bgm", "lut": False})
    agent = job_opts.render_kwargs(job_opts.for_job(wd, {"grain": "strong"}))
    ui = job_opts.render_kwargs(job_opts.normalize(job_opts.for_job(wd, job_opts.explicit_from_req(
        _req(grain="strong")))))
    for k in ("audio_mode", "voice_vol", "orig_vol", "lut", "grain", "brand", "brand_mode", "vbitrate",
              "hardsub_blur", "resolution", "fit", "zoom"):
        assert agent[k] == ui[k], k


# ───────────── thứ tự ưu tiên / None / pin_opts ─────────────

def test_precedence_explicit_pinned_batch_default():
    o = job_opts.resolve({"grain": "off"}, {"grain": "strong", "lut": False, "orig_vol": 0.4},
                         {"grain": "medium", "lut": True, "orig_vol": 0.3, "vbitrate": "4M"})
    assert o["grain"] == "off"            # explicit
    assert o["lut"] is False and o["orig_vol"] == 0.4   # pinned > batch
    assert o["vbitrate"] == "4M"          # batch > default
    assert o["resolution"] == "source"    # default


def test_none_never_overrides():
    o = job_opts.resolve({"audio_mode": None, "zoom": None}, {"audio_mode": None}, {"audio_mode": "sfx_lib"})
    assert o["audio_mode"] == "sfx_lib" and o["zoom"] == batch_config.DEFAULTS["zoom"]


def test_pin_opts_dropped_everywhere(tmp_path):
    o = job_opts.resolve({"pin_opts": True}, {"pin_opts": True}, {"pin_opts": True})
    assert "pin_opts" not in o
    assert "pin_opts" not in job_opts.normalize({"pin_opts": 1})
    assert "pin_opts" not in batch_config.resolve_job_opts({"pin_opts": 1})
    assert "pin_opts" not in job_opts.explicit_from_req({"pin_opts": 1, "grain": "off"})


def test_voice_not_taken_from_pinned(tmp_path):
    """Giọng job lưu ở stage tts (UI chọn giọng); queue_opts có thể cũ → không lấy voice từ ghim."""
    wd = _job(tmp_path, {"voice": "old"})
    assert job_opts.for_job(wd, fill_voice=False)["voice"] is None
    assert job_opts.for_job(wd, {"voice": "new"}, fill_voice=False)["voice"] == "new"
    assert job_opts.for_job(wd)["voice"] == "batch-voice"


def test_explicit_from_req_uses_fields_set_only():
    r = _req(audio_mode="duck")
    assert job_opts.explicit_from_req(r) == {"audio_mode": "duck"}
    assert job_opts.explicit_from_req(r, keys=job_opts.AUDIO_KEYS) == {"audio_mode": "duck"}
    legacy = types.SimpleNamespace(audio_mode="x", voice_vol=0.5, orig_vol=0.1)   # không model_fields_set
    assert job_opts.explicit_from_req(legacy, keys=job_opts.AUDIO_KEYS) == {
        "audio_mode": "x", "voice_vol": 0.5, "orig_vol": 0.1}
    assert job_opts.explicit_from_req({"job_ids": ["a"], "skip_done": True, "lut": False}) == {"lut": False}


def test_read_pinned_missing_or_corrupt(tmp_path):
    assert job_opts.read_pinned(tmp_path / "nope") == {}
    wd = tmp_path / "bad"
    wd.mkdir()
    (wd / "manifest.json").write_text("{hỏng", encoding="utf-8")
    assert job_opts.read_pinned(wd) == {}


# ───────────── kiểu dữ liệu + mặc định cũ ─────────────

def test_normalize_types():
    o = job_opts.normalize({"speed": "1.2", "voice_vol": "0.3", "orig_vol": None, "ocr": "false",
                            "fit": "off", "zoom": "1.13", "motion": 0, "lut": "on", "hardsub_blur": "tắt",
                            "tts_engine": "lạ", "asr_engine": None, "flip": " h ", "brand": "1"})
    assert o["speed"] == 1.2 and isinstance(o["speed"], float)
    assert o["voice_vol"] == 0.3 and o["orig_vol"] == 0.22
    assert o["ocr"] is False and o["fit"] is False and o["motion"] is False and o["lut"] is True
    assert o["hardsub_blur"] is False and o["brand"] is True
    assert o["zoom"] == 1.12                 # clamp như engines.render_fx.clamp_zoom
    assert o["tts_engine"] == "vieneu" and o["asr_engine"] == "nano_fa" and o["flip"] == "h"
    assert job_opts.normalize({"speed": 0})["speed"] == 1.0
    assert job_opts.normalize({"speed": "abc"})["speed"] == 1.0


def test_clamp_zoom_matches_engine():
    from engines.render_fx import clamp_zoom
    for z in (None, "off", "1", "1.10", "1.13", 1.2, "abc", 1.0, "1.15"):
        assert job_opts.clamp_zoom(z) == clamp_zoom(z), z


def test_old_defaults_kept():
    o = job_opts.normalize(job_opts.resolve(batch={}))
    assert (o["audio_mode"], o["voice_vol"], o["orig_vol"]) == ("duck_bgm", 0.2, 0.22)
    assert o["vbitrate"] == "8M" and o["grain"] == "weak" and o["hardsub_blur"] is True
    assert o["resolution"] == "source" and o["translate_mode"] == "batch" and o["zoom"] == 1.12
    assert job_opts.audio({}) == ("duck_bgm", 0.2, 0.22)
    kw = job_opts.render_kwargs({})
    assert kw["vbitrate"] == "8M" and kw["grain"] == "weak" and kw["hardsub_blur"] is True
    assert kw["speed_factor"] == 1.0 and kw["add_sub"] is True and kw["brand"] is False


def test_render_kwargs_same_as_v5():
    """batch_config.render_kwargs (lớp mỏng) = job_opts.render_kwargs; giá trị như bản V5."""
    o = {"audio_mode": "voice_bgm", "voice_vol": "0.5", "zoom": "1.13", "brand_mode": "drift",
         "hardsub_blur": "false", "render_speed": 1.05, "flip": " v "}
    kw = batch_config.render_kwargs(o)
    assert kw == job_opts.render_kwargs(o)
    assert kw["zoom"] == 1.13 and kw["brand"] is True and kw["hardsub_blur"] is False
    assert kw["speed_factor"] == 1.05 and kw["flip"] == "v" and kw["voice_vol"] == 0.5


def test_legacy_resolve_job_opts_semantics():
    o = batch_config.resolve_job_opts({"grain": "off"}, {"grain": "strong", "x": None}, fill_voice=False)
    assert o["grain"] == "strong" and o["voice"] is None and o["audio_mode"] == "sfx_lib"


def test_pipeline_render_kw_brand():
    assert job_opts.pipeline_render_kw({"brand_mode": "drift"})["brand"] is True
    assert job_opts.pipeline_render_kw({"brand_mode": "off", "brand": False})["brand"] is False
    assert job_opts.pipeline_render_kw({})["zoom"] == 1.12


def test_pin_subset_only_output_keys():
    o = job_opts.normalize(job_opts.resolve({"voice": "v"}, batch={}))
    p = job_opts.pin_subset(o)
    assert "voice" not in p and "speed" not in p and "tts_engine" not in p
    assert {"audio_mode", "voice_vol", "orig_vol", "zoom", "grain", "hardsub_blur"} <= set(p)


# ───────────── hàng chờ: opts riêng từng job ─────────────

def test_queue_add_opts_for(tmp_path, monkeypatch):
    import core.job_queue as jq
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)
    monkeypatch.setattr(jq, "_save", lambda: None)
    _job(tmp_path, {"audio_mode": "duck_bgm"}, "a")
    _job(tmp_path, None, "b")
    explicit = {"grain": "off"}
    batch = job_opts.load_batch()
    r = jq.queue_add(["a", "b"], opts=job_opts.resolve(explicit, None, batch, fill_voice=False),
                     opts_for=lambda jid: job_opts.for_job(tmp_path / jid, explicit, batch=batch,
                                                           fill_voice=False))
    assert r["queued"] == ["a", "b"]
    assert jq._opts_by_job["a"]["audio_mode"] == "duck_bgm"     # ghim của job a thắng batch
    assert jq._opts_by_job["b"]["audio_mode"] == "sfx_lib"
    assert jq._opts_by_job["a"]["grain"] == jq._opts_by_job["b"]["grain"] == "off"
    jq._reset_for_tests()


# ───────────── nối dây trong server.py (tĩnh — không cần fastapi) ─────────────

def _funcs(path: Path) -> dict[str, str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    return {n.name: ast.get_source_segment(src, n) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_server_paths_use_job_opts():
    f = _funcs(ROOT / "server.py")
    for name in ("run_all", "_queue_run_one", "mux_job", "_req_audio"):
        assert "job_opts.for_job(" in f[name], name
        assert "resolve_job_opts" not in f[name], name
    for name in ("api_queue_jobs_add", "api_queue_jobs_add_group", "api_queue_jobs_opts"):
        assert "job_opts." in f[name] and "resolve_job_opts" not in f[name], name
    ra = _funcs(ROOT / "http_routes" / "routes_agent.py")
    assert "job_opts.for_job(" in ra["_agent_mux_run"]
    assert "job_opts.resolve(" in ra["_agent_resume"]


def test_run_all_route_decorator_on_run_all():
    """Bug cũ: @app.post(.../run-all) nằm trên _req_audio → route gọi nhầm hàm."""
    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
    deco = {}
    for n in tree.body:
        if isinstance(n, ast.FunctionDef):
            for d in n.decorator_list:
                if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant):
                    deco.setdefault(d.args[0].value, []).append(n.name)
    assert deco["/api/jobs/{job_id}/run-all"] == ["run_all"]
    assert "_req_audio" not in sum(deco.values(), [])
