"""2026-11-15: Studio (UI3) là trang chính `/`; UI0 / UI4 / Hongguo 1 bỏ; đường cũ không gãy."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from http_routes import routes_ui
    app = FastAPI()
    app.include_router(routes_ui.router)
    return TestClient(app)


def test_root_and_old_studio_paths_serve_studio(client):
    home = client.get("/").text
    assert 'id="btn-agent"' in home and "PinelineLive.startLive" in home     # Studio (UI3 cũ)
    for p in ("/ui3", "/index3.html", "/studio"):
        r = client.get(p)
        assert r.status_code == 200 and r.text == home, p


def test_removed_pages_redirect(client):
    for p in ("/ui4", "/index4.html"):
        r = client.get(p, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/", p
    r = client.get("/hongguo?sid=1&q=abc", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/hongguo2?sid=1&q=abc"
    assert client.get("/ui-hg", follow_redirects=False).headers["location"] == "/hongguo2"


def test_studio_nav_has_no_ui0_ui4_and_points_to_hongguo2_and_local_films():
    s = (ROOT / "index.html").read_text(encoding="utf-8")
    assert ">UI0<" not in s and ">UI4" not in s and 'href="/ui4"' not in s
    assert 'href="/hongguo"' not in s and 'href="/hongguo2"' in s
    assert "8802/films/ui" not in s and 'href="/films/ui?tab=seasons"' in s
    assert not (ROOT / "index3.html").exists() and not (ROOT / "hongguo.html").exists()
