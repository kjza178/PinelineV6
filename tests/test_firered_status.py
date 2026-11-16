"""ASR FireRed: kiểm cài đặt trước khi chạy + lý do lỗi thật thay vì «firered_asr.py lỗi (mã 1)» (2026-11-16)."""
from __future__ import annotations

import pytest

from core import machine


@pytest.fixture
def fr(tmp_path, monkeypatch):
    for k in ("FIRERED_ROOT", "FIRERED_MODELS"):
        monkeypatch.delenv(k, raising=False)
    vals = {}
    monkeypatch.setattr(machine, "setting", lambda k, d=None: vals.get(k, "" if d is None else d))
    monkeypatch.setattr(machine, "DEFAULT_FIRERED_MODELS", str(tmp_path / "AI_CACHE" / "firered"))
    return tmp_path, vals


def test_missing_source_points_to_installer(fr):
    tmp, vals = fr
    vals["FIRERED_ROOT"] = str(tmp / "Z" / "PinelineV2" / "vendor" / "FireRedASR2S")
    st = machine.firered_status()
    assert not st["ok"] and "install_firered_asr.bat" in st["why"] and "trỏ máy khác" in st["why"]


def test_models_fallback_to_installer_download_dir(fr):
    tmp, vals = fr
    root = tmp / "vendor" / "FireRedASR2S"
    (root / "fireredasr2s").mkdir(parents=True)
    vals["FIRERED_ROOT"] = str(root)
    st = machine.firered_status()
    assert not st["ok"] and "FireRedASR2-AED" in st["why"]
    dl = tmp / "AI_CACHE" / "firered"                       # script cài tải model về đây nhưng không ghi config
    for d in machine.FIRERED_MODEL_DIRS:
        (dl / d).mkdir(parents=True)
    st = machine.firered_status()
    assert st["ok"] and st["models"] == str(dl)


def test_engine_reason_picks_python_error_line():
    import server
    tail = ["[firered_asr] bắt đầu", "Traceback (most recent call last):", '  File "x.py", line 3, in <module>',
            "    run()", "RuntimeError: Thiếu model FireRed: FireRedVAD trong D:\\AI_CACHE\\firered."]
    assert server._engine_reason(tail).startswith("RuntimeError: Thiếu model FireRed")
    assert server._engine_reason(["chỉ log thường", "dòng cuối"]) == "dòng cuối"
    assert server._engine_reason([]) == ""


def test_short_error_still_parses_with_reason():
    from agents.agent_brain_notify import short_error
    assert short_error("asr", "firered_asr.py lỗi (mã 1): RuntimeError: Chưa có FireRedASR2S") == "firered mã 1"
