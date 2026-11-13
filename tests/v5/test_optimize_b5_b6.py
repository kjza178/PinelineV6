"""fix 2026-11-11 B5 (dừng REVIEW song song nhanh) + B6 (audio run-all/mux theo opts job/batch)."""
from __future__ import annotations

import json
import threading
import time
import types
from pathlib import Path

import pytest

import server
import engines.translate as T


def _req(fields_set=(), **kw):
    base = {"audio_mode": "voice_bgm", "voice_vol": 0.2, "orig_vol": 0.6}
    base.update(kw)
    return types.SimpleNamespace(**base, model_fields_set=set(fields_set))


def _wd(tmp_path: Path, queue_opts: dict | None = None) -> Path:
    wd = tmp_path / "j"
    wd.mkdir(exist_ok=True)
    (wd / "manifest.json").write_text(json.dumps({"stages": {}, "queue_opts": queue_opts or {}}), encoding="utf-8")
    return wd


def test_req_audio_uses_batch_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(server.batch_config, "load", lambda: {"audio_mode": "sfx_lib", "voice_vol": 0.2, "orig_vol": 0.28})
    assert server._req_audio(_wd(tmp_path), _req()) == ("sfx_lib", 0.2, 0.28)


def test_req_audio_pinned_beats_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(server.batch_config, "load", lambda: {"audio_mode": "sfx_lib", "voice_vol": 0.2, "orig_vol": 0.28})
    am, _vv, ov = server._req_audio(_wd(tmp_path, {"audio_mode": "duck_bgm", "orig_vol": 0.3}), _req())
    assert (am, ov) == ("duck_bgm", 0.3)


def test_req_audio_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(server.batch_config, "load", lambda: {"audio_mode": "sfx_lib", "voice_vol": 0.2, "orig_vol": 0.28})
    am, _vv, _ov = server._req_audio(_wd(tmp_path, {"audio_mode": "duck_bgm"}),
                                     _req(fields_set=("audio_mode",), audio_mode="voice_only"))
    assert am == "voice_only"


def test_parallel_review_stops_fast(tmp_path, monkeypatch):
    wd = tmp_path / "job"
    wd.mkdir()
    rows = [{"i": i, "start": i, "end": i + 1, "zh": f"zh{i}", "vi": f"vi{i}"} for i in range(12)]
    (wd / "script.json").write_text(json.dumps(rows), encoding="utf-8")
    (wd / "manifest.json").write_text(json.dumps({"stages": {"translate": {"status": "done"}}}), encoding="utf-8")

    def slow_batch(chunk_exp, *a, **k):
        time.sleep(30)
        return {}

    monkeypatch.setattr(T, "review_batch", slow_batch)
    monkeypatch.setattr(T, "run_review_pass1_full_vi", lambda wd_, rows_, plan, model=None: plan)
    monkeypatch.setattr(T, "_resolve_review_model", lambda wd_, m=None: "chatgpt/test")
    monkeypatch.setattr(T, "_review_parallel_workers", lambda wd_, m: 3)
    monkeypatch.setattr(T, "_is_llama_local_model", lambda m: False)
    monkeypatch.setattr(T, "is_local", lambda m: False)
    monkeypatch.setattr(T, "_cfg_get", lambda k, d=None: {"AUTO_SWITCH_PRESETS": "0"}.get(k, d))
    threading.Timer(1.0, lambda: (wd / ".stop").write_text("1")).start()
    t0 = time.time()
    with pytest.raises(RuntimeError, match="stopped by user"):
        T.run_review(wd, batch=4)
    assert time.time() - t0 < 10, "dừng phải < 10 s, không đợi request LLM 30 s"
