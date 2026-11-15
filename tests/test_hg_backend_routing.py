"""2026-11-15: Hongguo 1 (:8000) đã ngừng — `/api/hg/*` không còn bị ép sang HTTP :8000; theo HG_BACKEND (mặc định
auto = Hongguo 2 nếu có engine). `/api/hg2/*` vẫn ép RPC (Hongguo 2)."""
from __future__ import annotations

import pytest


@pytest.fixture
def seen(monkeypatch):
    server = pytest.importorskip("server")
    from fastapi.testclient import TestClient
    import agents.hg_ui_ctx as ctx
    got = {}

    @server.app.get("/api/hg/__probe_backend")
    def _probe():
        got["ctx"] = ctx.get()
        return {"ok": True}
    c = TestClient(server.app, base_url="http://127.0.0.1:8806")
    yield c, got
    server.app.router.routes[:] = [r for r in server.app.router.routes if getattr(r, "path", "") != "/api/hg/__probe_backend"]


def test_api_hg_not_forced_to_dead_hongguo1(seen):
    c, got = seen
    assert c.get("/api/hg/__probe_backend").status_code == 200
    assert got["ctx"] is None                     # không ép «http» → theo HG_BACKEND / auto


def test_api_hg2_still_forces_rpc(seen):
    c, got = seen
    c.get("/api/hg2/__probe_backend")
    assert got["ctx"] == "rpc"
