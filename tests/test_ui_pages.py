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


def test_ui_files_are_served_no_cache_but_api_untouched():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, PlainTextResponse
    from fastapi.testclient import TestClient
    from app.main import NoCacheUI
    app = FastAPI()
    app.add_middleware(NoCacheUI)

    @app.get("/v6/app.js")
    def js():
        return PlainTextResponse("x", headers={"Cache-Control": "max-age=9999"})

    @app.get("/api/v6/jobs")
    def jobs():
        return JSONResponse({"jobs": []})
    c = TestClient(app)
    assert c.get("/v6/app.js").headers["cache-control"] == "no-cache"
    assert "cache-control" not in c.get("/api/v6/jobs").headers


def test_desktop_index_assets_are_versioned(tmp_path):
    """fix 2026-11-15: app.css / app.js gắn ?v=<giờ sửa> → WebView2 không dùng app.js cũ trong cache sau khi cập nhật."""
    from app.main import versioned_index
    (tmp_path / "index.html").write_text('<link href="app.css"><script src="app.js"></script>', encoding="utf-8")
    (tmp_path / "app.css").write_text("x", encoding="utf-8")
    (tmp_path / "app.js").write_text("x", encoding="utf-8")
    import os
    os.utime(tmp_path / "app.js", (1700000000, 1700000000))
    html = versioned_index(tmp_path)
    assert 'src="app.js?v=1700000000"' in html and 'href="app.css?v=' in html
