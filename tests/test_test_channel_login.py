"""Đăng nhập kênh test từ app desktop (2026-11-16): WebView2 chặn cửa sổ mới → server mở trình duyệt ngoài (chỉ host
được phép); callback Google báo app qua SSE; script chép cấu hình kênh test từ V2 (không ghi đè)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from services import ui_tools

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def opened(monkeypatch):
    seen = []
    import os
    import webbrowser
    monkeypatch.setattr(os, "startfile", lambda u: seen.append(u), raising=False)
    monkeypatch.setattr(webbrowser, "open", lambda u: seen.append(u) or True)
    return seen


@pytest.mark.parametrize("url", ["https://accounts.google.com/o/oauth2/auth?x=1", "http://127.0.0.1:8806/films/ui",
                                 "https://youtu.be/abc"])
def test_open_external_allowed(opened, url):
    assert ui_tools.open_external(url) == {"ok": True} and opened == [url]


@pytest.mark.parametrize("url", ["https://evil.example.com/", "file:///C:/Windows/system32/calc.exe", "http://accounts.google.com/",
                                 "javascript:alert(1)", ""])
def test_open_external_refused(opened, url):
    with pytest.raises(ValueError):
        ui_tools.open_external(url)
    assert opened == []


def test_import_test_channel_no_overwrite(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import import_test_channel as itc
    s = tmp_path / "v2" / "data" / "test_channel"
    (s / "tokens").mkdir(parents=True)
    (s / "projects.json").write_text("[]", encoding="utf-8")
    (s / "tokens" / "p1.json").write_text("{tok}", encoding="utf-8")
    (s / "code_verifier_p1.txt").write_text("x", encoding="utf-8")
    d = tmp_path / "v6" / "data" / "test_channel"
    d.mkdir(parents=True)
    (d / "projects.json").write_text("[v6]", encoding="utf-8")
    assert itc.main(["--src", str(tmp_path / "v2"), "--dst", str(tmp_path / "v6")]) == 0
    assert not (d / "tokens").exists()                                   # không --apply → không chép
    itc.main(["--src", str(tmp_path / "v2"), "--dst", str(tmp_path / "v6"), "--apply"])
    assert (d / "tokens" / "p1.json").read_text(encoding="utf-8") == "{tok}"
    assert (d / "projects.json").read_text(encoding="utf-8") == "[v6]"   # không ghi đè
    assert not (d / "code_verifier_p1.txt").exists()


def test_test_channel_data_not_in_git():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/test_channel/" in gi                                     # client_secret + token Google


def test_desktop_login_flow_wired():
    js = (ROOT / "desktop" / "ui" / "app.js").read_text(encoding="utf-8")
    for needle in ("/api/v6/open-external", "/test-channel/auth-url", '"test_channel.changed"', "window.chrome.webview",
                   "import_test_channel_v2.bat"):
        assert needle in js, needle
    src = (ROOT / "http_routes" / "routes_films.py").read_text(encoding="utf-8")
    assert 'bus.publish("test_channel.changed"' in src
