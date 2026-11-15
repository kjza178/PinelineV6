"""2026-11-15: Hongguo 1 (Downloader HTTP :8000, /dl/*) bỏ HẲN khỏi V6 — chỉ Hongguo 2 (engine RPC).
`/api/hg2/*` (trang Hongguo 2) là bí danh của `/api/hg/*`."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_no_hongguo1_http_code_left():
    src = (ROOT / "agents" / "hongguo_bridge.py").read_text(encoding="utf-8")
    assert "def _req(" not in src and "_auth_headers" not in src and "discover_ui_token" not in src
    assert not re.search(r'["\']/dl/\w', src)                 # không còn gọi API /dl/* của Hongguo 1
    assert not (ROOT / "agents" / "hg_ui_ctx.py").exists()    # không còn chọn backend theo trang
    for f in ("server.py", "http_routes/routes_hg.py", "core/machine.py", "agents/hongguo_rpc.py"):
        t = (ROOT / f).read_text(encoding="utf-8")
        assert "hg_ui_ctx" not in t and "HONGGUO_BASE_URL" not in t and "HG_BACKEND" not in t, f


def test_use_rpc_explains_when_hongguo2_missing(monkeypatch):
    import agents.hongguo_bridge as hg
    import agents.hongguo_rpc as rpc
    monkeypatch.setattr(rpc, "helper_available", lambda *a, **k: False)
    monkeypatch.setattr(hg, "_rpc_mod", lambda: rpc)
    with pytest.raises(RuntimeError) as ei:
        hg._use_rpc()
    assert "Hongguo 2" in str(ei.value)
    assert hg.ping()["ok"] is False and "Hongguo 2" in hg.ping()["error"]


def test_hg2_alias_rewrites_to_api_hg():
    server = pytest.importorskip("server")
    from fastapi.testclient import TestClient
    seen = []

    @server.app.get("/api/hg/__probe")
    def _probe():
        seen.append(1)
        return {"ok": True}
    try:
        c = TestClient(server.app, base_url="http://127.0.0.1:8806")
        assert c.get("/api/hg2/__probe").status_code == 200 and c.get("/api/hg/__probe").status_code == 200
        assert len(seen) == 2
    finally:
        server.app.router.routes[:] = [r for r in server.app.router.routes if getattr(r, "path", "") != "/api/hg/__probe"]


def test_merge_copy_concats_uniform_episodes_locally(tmp_path, monkeypatch):
    """Gộp tập giữ nguyên chất lượng tại chỗ (thay Hongguo 1 gộp hộ /dl/library/merge)."""
    import agents.hongguo_bridge as hg
    folder = tmp_path / "PhimA"
    folder.mkdir()
    eps = [folder / f"S01E{i:03d}.mp4" for i in (1, 2)]
    for e in eps:
        e.write_bytes(b"x" * 64)
    monkeypatch.setattr(hg, "_list_episode_frags", lambda f: eps)
    monkeypatch.setattr(hg, "_probe_wh", lambda p: (1920, 1080))
    calls = []

    class R:
        returncode = 0
        stdout = stderr = ""

    def run(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"merged")
        return R()
    monkeypatch.setattr(hg.subprocess, "run", run)
    out = hg.merge_series_copy(folder)
    assert out["ok"] and out["merged_file"] == "PhimA (merged).mp4" and (folder / out["merged_file"]).is_file()
    assert "-c" in calls[0] and calls[0][calls[0].index("-c") + 1] == "copy"
