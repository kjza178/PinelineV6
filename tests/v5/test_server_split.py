"""server.py tách routes_ui / routes_hg / routes_agent: route còn đủ, tên cũ vẫn truy cập qua server."""
import sys
import types

sys.modules.setdefault("pytest", types.ModuleType("pytest"))

import server  # noqa: E402
import http_routes.routes_agent as routes_agent  # noqa: E402
import http_routes.routes_hg as routes_hg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_routes_registered():
    c = TestClient(server.app, base_url="http://127.0.0.1:8804")
    assert c.get("/ui3").status_code == 200
    assert c.get("/api/agent/pending").json()["ok"] is True
    assert c.get("/api/hg/merge-queue").status_code == 200
    assert c.post("/api/agent/confirm", json={"id": "nope", "approve": False}).status_code == 404
    assert c.get("/ui1").status_code == 404


def test_reexports_and_shared_module():
    assert routes_agent.S is server and routes_hg.S is server
    for name in ("_agent_mux_run", "_HEAL_HOOKS", "_AV_SYNC_HOOKS", "_agent_core", "api_agent_chat", "hg_merge", "find_index"):
        assert hasattr(server, name), name
    assert server._agent_mux_run is routes_agent._agent_mux_run
