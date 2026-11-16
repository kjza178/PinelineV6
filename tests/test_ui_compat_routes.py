"""Nút trang Phim / Studio trước đây chỉ sidecar có (2026-11-15) — nay server V6 phục vụ (app/routes/ui_compat.py)."""
from __future__ import annotations

import pytest


@pytest.fixture
def client():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes import ui_compat
    app = FastAPI()
    app.include_router(ui_compat.router)
    return TestClient(app)


def test_baike(client, monkeypatch):
    import agents.hongguo_bridge as hg
    import engines.title_mt as tmt
    monkeypatch.setattr(hg, "baike_deep", lambda title: {"title": title, "intro": "简介", "tags": ["古装"]})
    monkeypatch.setattr(tmt, "translate_titles", lambda items, **k: {"items": [{"title_vi": "Giới thiệu"}]})
    d = client.get("/meta/baike?q=天赋").json()
    assert d["ok"] and d["title"] == "天赋" and d["intro_vi"] == "Giới thiệu"
    assert client.get("/meta/baike").status_code == 400


def test_ai_image(client, monkeypatch, tmp_path):
    import agents.meta_studio as ms
    monkeypatch.setattr(ms, "THUMB_DIR", tmp_path)
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), "red").save(buf, format="PNG")
    monkeypatch.setattr(ms, "generate_ai_image", lambda prompt, **k: buf.getvalue())
    d = client.post("/api/meta/ai-image", json={"prompt": "poster", "job_id": "j1"}).json()
    assert d["ok"] and (tmp_path / "thumb_j1.jpg").is_file()
    assert Image.open(tmp_path / "thumb_j1.jpg").size == (1280, 720)
    assert client.post("/meta/ai-image", json={}).status_code == 400


def test_config_get_and_set(client, monkeypatch):
    import core.batch_config as bc
    monkeypatch.setattr(bc, "load", lambda: dict(bc.DEFAULTS))
    monkeypatch.setattr(bc, "describe", lambda o=None: "mô tả")
    got = {}
    monkeypatch.setattr(bc, "update", lambda patch, by="": got.update(patch=patch, by=by) or
                        {"changed": patch, "opts": {**bc.DEFAULTS, **patch}})
    assert client.get("/config").json()["opts"]["voice"] == bc.DEFAULTS["voice"]
    d = client.post("/config", json={"opts": {"speed": 1.1, "khong_co": 1}, "by": "ui3"}).json()
    assert d["ok"] and got == {"patch": {"speed": 1.1}, "by": "ui3"}


def test_batch_stop(client, monkeypatch):
    # phải là tools.test_channel — cùng module (BATCH_STATE) với /test-channel/batch-start của routes_films
    import tools.test_channel as tc
    monkeypatch.setattr(tc, "stop_batch_test", lambda: {"ok": True, "stopped": True})
    assert client.post("/test-channel/batch-stop").json()["stopped"] is True
