"""F3 tích hợp: 1 tiến trình V6 phục vụ cả API V5 (server.py) lẫn API V6 trên CÙNG app.
Cần fastapi + deps của server.py → chỉ chạy trên Windows (cloud: skip)."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from core.instance import load  # noqa: E402


@pytest.fixture(scope="module")
def env_inst(tmp_path_factory):
    keep = dict(os.environ)
    d = tmp_path_factory.mktemp("inst")
    work = d / "work"
    (work / "job-a").mkdir(parents=True)
    (work / "job-a" / "manifest.json").write_text(json.dumps(
        {"request": {"title": "A"}, "stages": {"download": {"status": "done"}}}), encoding="utf-8")
    (work / "job-a" / "script.json").write_text(json.dumps([{"i": 0, "vi": ""}, {"i": 1, "vi": "x"}]),
                                                 encoding="utf-8")
    (d / "itest.json").write_text(json.dumps({
        "name": "itest", "port": 8897, "work_root": str(work), "temp": str(d / "tmp"),
        "routers": [], "agent_brain": False, "agent_watch": False, "telegram": False}), encoding="utf-8")
    (d / "tmp").mkdir()
    # 11-12 agentJ: job NGOÀI work root instance ghi qua state.job_state (như test brain/hold chạy trước, hay
    # script ghi cây khác) — trước đây lọt vào index dùng chung → /api/v6/jobs + snapshot SSE lẫn job lạ.
    from state import job_state
    job_state.create_job(d / "outside" / "2937ffd7989f", request={"title": "ngoài instance"})
    inst = load("itest", directory=d)
    inst.apply_env()
    yield inst
    os.environ.clear()
    os.environ.update(keep)


@pytest.fixture(scope="module")
def app(env_inst):
    from app.main import create_app
    a = create_app(env_inst)
    assert create_app(env_inst) is a          # gắn lần 2 → không đăng ký startup lần nữa
    return a


@pytest.fixture(scope="module")
def client(app):
    with TestClient(app, base_url="http://127.0.0.1:8897") as c:   # chạy startup (index, watcher)
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["instance"] == "itest"


def test_v6_jobs_from_index(client):
    r = client.get("/api/v6/jobs")
    assert r.status_code == 200
    assert [j["job_id"] for j in r.json()["jobs"]] == ["job-a"]
    d = client.get("/api/v6/jobs/job-a")
    assert d.status_code == 200 and d.json()["request"]["title"] == "A"
    assert client.get("/api/v6/jobs/khong-co").status_code == 404


def test_legacy_routes_still_there(client):
    r = client.get("/api/jobs")
    assert r.status_code == 200 and isinstance(r.json(), list)
    assert r.json()[0]["job_id"] == "job-a"
    assert r.json()[0]["stages"]["translate"]["n_missing"] == 1
    assert client.get("/api/queue/jobs").status_code == 200
    assert client.get("/ui3").status_code == 200


def test_legacy_get_job_does_not_write(client, env_inst):
    mf = env_inst.work_root / "job-a" / "manifest.json"
    before = (mf.stat().st_mtime_ns, mf.read_bytes())
    r = client.get("/api/jobs/job-a")
    assert r.status_code == 200 and r.json()["stages"]["translate"]["n_missing"] == 1
    assert (mf.stat().st_mtime_ns, mf.read_bytes()) == before


def test_list_jobs_script_cache(client, env_inst, monkeypatch):
    import server
    first = client.get("/api/jobs").json()
    calls = []
    orig = Path.read_text

    def spy(self, *a, **k):
        if self.name == "script.json":
            calls.append(self)
        return orig(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", spy)
    assert client.get("/api/jobs").json() == first
    assert calls == []                            # không đọc lại script.json khi file không đổi
    monkeypatch.undo()
    sp = env_inst.work_root / "job-a" / "script.json"
    sp.write_text(json.dumps([{"i": 0, "vi": "a"}, {"i": 1, "vi": "x"}]), encoding="utf-8")
    os.utime(sp, ns=(sp.stat().st_atime_ns, sp.stat().st_mtime_ns + 10_000_000))
    assert client.get("/api/jobs").json()[0]["stages"]["translate"]["n_missing"] == 0
    assert server._script_missing(env_inst.work_root / "job-a") == (0, 2)


def test_v6_ui_mounted(client):
    r = client.get("/v6/")
    assert r.status_code == 200 and "Pineline V6" in r.text
    assert client.get("/static/live_sync.js").status_code == 200


def _find_route(app, path):
    """Tìm route theo path, kể cả khi FastAPI mới bọc router con (verify 11-11: app.routes
    trên Windows không liệt kê thẳng /api/events dù /api/health chạy được)."""
    stack = list(getattr(app, "routes", []) or []) + list(getattr(getattr(app, "router", None), "routes", []) or [])
    seen = set()
    while stack:
        r = stack.pop()
        if id(r) in seen:
            continue
        seen.add(id(r))
        if path in (getattr(r, "path", None), getattr(r, "path_format", None)) and getattr(r, "endpoint", None):
            return r
        for attr in ("routes", "app", "router"):
            sub = getattr(r, attr, None)
            if sub is None:
                continue
            subs = getattr(sub, "routes", None) if attr != "routes" else sub
            if subs:
                stack.extend(subs)
    raise AssertionError(f"không thấy route {path}")


def test_events_snapshot(app, client, env_inst):
    # Kiểm logic endpoint SSE trên router V6 (APIRouter thường, tra route chắc chắn). Việc route có
    # được gắn và trả `event: snapshot` qua HTTP thật do verify_all bước d) kiểm.
    from app.main import _v6_router
    assert client.get("/api/health").status_code == 200
    route = _find_route(_v6_router(env_inst), "/api/events")

    class Req:
        headers: dict = {}
    resp = route.endpoint(Req())

    async def first():
        it = resp.body_iterator
        chunk = await it.__anext__()
        await it.aclose()
        return chunk
    chunk = asyncio.run(first())
    chunk = chunk.decode() if isinstance(chunk, bytes) else chunk
    assert chunk.startswith("event: snapshot\n")
    assert json.loads(chunk.split("data: ", 1)[1])["jobs"][0]["job_id"] == "job-a"


def test_flags_off_in_this_instance():
    import server
    assert server._instance_flag("agent_brain") is False
    assert server._instance_flag("telegram") is False
    assert server._instance_flag("auto_publish") is False
