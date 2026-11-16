"""Server + bộ cài dùng CÙNG một python (2026-11-16: run_v6.bat chạy python hệ thống thiếu demucs → ASR bỏ tách giọng);
install.py không ghi đè pineline.config.json (mất key API)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _bat(name: str) -> str:
    return (ROOT / name).read_bytes().decode("utf-8")


def test_launchers_use_shared_python_picker():
    pick = _bat("scripts/pick_python.bat")
    assert ".pineline_env_path" in pick and r"pineline_env\Scripts\python.exe" in pick and "PINELINE_PYTHON" in pick
    for name in ("run_v6.bat", "run_sidecar_v6.bat", "scripts/install_firered_asr.bat"):
        s = _bat(name)
        assert r"call scripts\pick_python.bat" in s and '"%PY%"' in s, name
        assert "\r\n" in s, f"{name} phải CRLF"
        assert "\npython " not in s.replace("\r\n", "\n"), f"{name} còn gọi python trần"


def test_install_merges_existing_config_and_records_venv():
    src = (ROOT / "scripts" / "install" / "install.py").read_text(encoding="utf-8")
    assert 'config = json.loads(cfg_path.read_text(encoding="utf-8"))' in src
    assert 'config = {"_note"' not in src                      # trước: tạo mới → ghi đè cả file
    assert '".pineline_env_path"' in src
    assert ".pineline_env_path" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_check_env_reports_missing_demucs(monkeypatch, capsys):
    import importlib.util
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_env
    rows = []
    monkeypatch.setattr(check_env, "row", lambda *a, **k: rows.append(a))
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda m, *a: None if m == "demucs" else real(m, *a))
    check_env.check_python_pkgs()
    d = {r[0]: r for r in rows}
    assert d["thư viện demucs"][1] == check_env.WARN and "requirements-main.txt" in d["thư viện demucs"][3]
