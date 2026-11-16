"""Cài đặt → Giọng đọc: danh sách giọng clone + thiếu file mẫu + xoá (services/voices) — registry TẠM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import voices


@pytest.fixture
def reg(tmp_path, monkeypatch):
    import engines.clone_tts as ct
    (tmp_path / "voices" / "clone_refs").mkdir(parents=True)
    (tmp_path / "voices" / "clone_refs" / "A.wav").write_bytes(b"RIFF")
    f = tmp_path / "voices" / "f5_voices.json"
    f.write_text(json.dumps({"A": {"ref": "voices/clone_refs/A.wav", "text": "xin chào"},
                             "B": {"ref": "voices\\f5_refs\\B.wav", "text": ""},
                             "VieNeu_X": {"ref": "voices/vieneu_bac/X.wav", "source": "vieneu_v3_turbo_preset"}}), encoding="utf-8")
    monkeypatch.setattr(ct, "_PROJ", tmp_path)
    monkeypatch.setattr(ct, "VOICES_FILE", f)
    return tmp_path


def test_list_reports_missing_files(reg):
    d = voices.list_voices()
    assert (d["ok"], d["missing"]) == (1, 2)
    by = {v["name"]: v for v in d["items"]}
    assert by["A"]["ok"] and by["A"]["has_text"] and not by["B"]["ok"]
    assert by["VieNeu_X"]["source"].startswith("VieNeu") and d["items"][0]["name"] == "A"      # dùng được lên đầu


def test_remove_entry_and_clone_ref(reg):
    assert voices.remove("A") is True and not (reg / "voices" / "clone_refs" / "A.wav").exists()
    assert voices.remove("khong_co") is False
    assert "A" not in json.loads((reg / "voices" / "f5_voices.json").read_text(encoding="utf-8"))


def test_clone_voice_accepts_typed_text():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert 'text: str = Form("")' in src and "text = (text or \"\").strip() or asr_text" in src


def test_desktop_voice_manager():
    js = (Path(__file__).resolve().parents[1] / "desktop" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "/api/v6/voices" in js and "vnew-ok" in js and 'fd.append("text"' in js and "import_voices.py" in js
