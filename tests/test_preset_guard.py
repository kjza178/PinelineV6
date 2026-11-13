"""11-11: test v5 từng gọi preset_switcher.switch_preset THẬT → restart llama :8083 dùng chung V2.
Khoá: đang pytest / instance tắt preset_switch → không bao giờ chạy start-model.ps1."""
import subprocess

import pytest

ps = pytest.importorskip("tools.preset_switcher")


def test_switch_refused_under_pytest(monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: called.append(a))
    monkeypatch.setattr(ps, "is_model_ready", lambda **k: False)
    assert ps.switch_preset("qwen36-turbo", max_wait_sec=1) is False
    assert called == []


def test_flag_off_refuses(monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("PINELINE_FLAG_PRESET_SWITCH", "0")
    ok, why = ps.switch_allowed()
    assert ok is False and "preset_switch" in why
    monkeypatch.setenv("PINELINE_FLAG_PRESET_SWITCH", "1")
    assert ps.switch_allowed() == (True, "")


def test_instances_preset_switch():
    from core.instance import load
    assert load("v6").flags()["preset_switch"] is False
    assert load("test").flags()["preset_switch"] is False
    assert load("prod").flags()["preset_switch"] is True


def test_localize_html_labels(monkeypatch):
    from core import instance
    monkeypatch.setattr(instance, "sidecar_url", lambda: "http://127.0.0.1:8816")
    monkeypatch.setattr(instance, "server_port", lambda: 8806)
    out = instance.localize_html("a http://127.0.0.1:8802/config b 'Sidecar :8802 tắt' c http://localhost:8801/api :8801")
    assert ":8802" not in out and ":8801" not in out
    assert "http://127.0.0.1:8816/config" in out and "Sidecar :8816 tắt" in out and ":8806" in out
