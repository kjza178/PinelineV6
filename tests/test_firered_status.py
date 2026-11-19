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
    # 2026-11-19 portable: gốc trỏ máy khác (không tồn tại) → dùng vendor/FireRedASR2S trong cây; vẫn chỉ tới bộ cài
    # khi thiếu mã nguồn/model.
    vendor = machine.Path(machine.__file__).resolve().parents[1] / "vendor" / "FireRedASR2S"
    if vendor.is_dir():
        assert machine.Path(st["root"]) == vendor
    else:
        assert "trỏ máy khác" in st["why"]
    assert st["ok"] or "install_firered_asr.bat" in st["why"]


def test_models_fallback_to_installer_download_dir(fr):
    tmp, vals = fr
    root = tmp / "vendor" / "FireRedASR2S"
    for sub in ("fireredasr2", "fireredlid", "fireredpunc"):            # mã nguồn đủ (có code models/)
        (root / "fireredasr2s" / sub / "models").mkdir(parents=True)
        (root / "fireredasr2s" / sub / "models" / "__init__.py").write_text("", encoding="utf-8")
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


def test_vendor_firered_has_python_models_packages():
    """fix 2026-11-16: .gitignore «models/» trần loại code vendor/FireRedASR2S/**/models → «No module named
    fireredasr2s.fireredasr2.models» trên máy cài từ git. Nay /models/ chỉ ở gốc; code models có trong repo."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    v = root / "vendor" / "FireRedASR2S" / "fireredasr2s"
    for f in ("fireredasr2/models/fireredasr_aed.py", "fireredlid/models/fireredlid_aed.py", "fireredpunc/models/fireredpunc_bert.py"):
        assert (v / f).is_file(), f
    gi = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/models/" in gi and "models/" not in gi


def test_status_reports_broken_vendor_source(fr):
    tmp, vals = fr
    root = tmp / "vendor" / "FireRedASR2S"
    (root / "fireredasr2s" / "fireredasr2").mkdir(parents=True)
    vals["FIRERED_ROOT"] = str(root)
    st = machine.firered_status()
    assert not st["ok"] and "fireredasr2/models" in st["why"]


def test_vendor_forced_align_casts_bf16_to_float32():
    """fix 2026-11-16: FireRed use_half=BF16 → torchaudio.forced_align «log_probs must be float64, float32 or float16»
    → mất mốc thời gian từng chữ (chia đều). Bản vendor ép .float() trước forced_align."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "vendor" / "FireRedASR2S" / "fireredasr2s" / "fireredasr2" / "models"
           / "fireredasr_aed.py").read_text(encoding="utf-8")
    assert "n_ctc_logits.float().unsqueeze(0)" in src


def test_firered_speed_defaults(monkeypatch):
    """2026-11-16: tắt LID (không dùng kết quả, model 3,3 GB) + ASR theo lô trên GPU + dấu câu theo lô; hết VRAM → giảm lô."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "engines" / "asr" / "firered_asr.py").read_text(encoding="utf-8")
    assert '_cfg_flag("FIRERED_LID", False)' in src and "asr_batch_size=asr_bs" in src and "punc_batch_size=punc_bs" in src
    assert "transcribe_sliced(_process," in src and "out of memory" in src
    import importlib
    import sys
    import types
    # nạp module không cần torch / FireRed: chỉ kiểm hàm đọc cấu hình
    for m in ("engines.asr.asr", "engines.asr.fun_asr_nano", "engines.subs", "engines.vram"):
        if m not in sys.modules:
            try:
                importlib.import_module(m)
            except Exception:  # noqa: BLE001
                return                                  # môi trường thiếu thư viện nặng → đủ với kiểm tĩnh ở trên
    fa = importlib.import_module("engines.asr.firered_asr")
    monkeypatch.setenv("FIRERED_ASR_BATCH", "99")
    assert fa._cfg_int("FIRERED_ASR_BATCH", 4, 1, 32) == 32
    monkeypatch.setenv("FIRERED_ASR_BATCH", "abc")
    assert fa._cfg_int("FIRERED_ASR_BATCH", 4, 1, 32) == 4
    monkeypatch.setenv("FIRERED_LID", "1")
    assert fa._cfg_flag("FIRERED_LID", False) is True
