"""Mức âm lượng trộn tiếng (core/audio_levels.py) + /api/v6/config `levels`.

2026-11-15: Cài đặt desktop từng gắn «Âm lượng giọng Việt» vào voice_vol — mà render dùng voice_vol cho GIỌNG TRUNG nền
(demucs_keep). Giọng Việt thật = RENDER_DUB_VOL. Dùng pineline.config.json TẠM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.config as cfg
from core import audio_levels

KEYS = [lv.key for lv in audio_levels.LEVELS.values()]


@pytest.fixture
def conf(tmp_path, monkeypatch):
    f = tmp_path / "pineline.config.json"
    f.write_text(json.dumps({"OTHER": "giữ", "RENDER_BGM_VOL": "0.2"}), encoding="utf-8")
    monkeypatch.setattr(cfg, "_cfg_file", f)
    monkeypatch.setattr(cfg, "_CFG", {})
    monkeypatch.setattr(cfg, "_cfg_mtime", 0.0)
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    cfg._load_cfg(force=True)
    return f


def test_levels_read_real_render_keys(conf):
    from engines.render_fx import bgm_volume, dub_volume
    snap = audio_levels.snapshot()
    assert snap["dub"]["key"] == "RENDER_DUB_VOL" and snap["dub"]["value"] == dub_volume() == 1.25
    assert snap["bgm"]["value"] == bgm_volume() == 0.2
    assert snap["dub"]["modes"] == ["*"] and "sfx_lib" in snap["sfx"]["modes"]


def test_update_writes_config_and_render_sees_it(conf):
    from engines.render_fx import dub_volume
    assert audio_levels.update({"dub": 1.5, "bgm": None}) == {"changed": ["RENDER_BGM_VOL", "RENDER_DUB_VOL"]}
    data = json.loads(conf.read_text(encoding="utf-8"))
    assert data["RENDER_DUB_VOL"] == 1.5 and "RENDER_BGM_VOL" not in data and data["OTHER"] == "giữ"
    assert dub_volume() == 1.5


@pytest.mark.parametrize("patch", [{"dub": 9}, {"dub": "abc"}, {"khong_co": 1}])
def test_bad_levels_rejected_without_writing(conf, patch):
    before = conf.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        audio_levels.update(patch)
    assert conf.read_text(encoding="utf-8") == before


def test_desktop_audio_pane_uses_real_dub_level():
    js = (Path(__file__).resolve().parents[1] / "desktop" / "ui" / "app.js").read_text(encoding="utf-8")
    assert 'label: "Âm lượng giọng Việt"' not in js          # nhãn sai cũ gắn vào voice_vol
    assert "levelsPatch" in js and "{ patch, levels }" in js
    assert "translate_mode: { hidden: true }" in js
