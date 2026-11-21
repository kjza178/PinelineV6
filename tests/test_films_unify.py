"""2026-11-15: gom Thư viện phim về /v6/; trang Phim cũ bỏ hộp «Cài Đặt Hệ Thống V4» (ghi đè khoá AI);
/films trả `ok` (thiếu → films_ui «Lỗi: Không đọc được dữ liệu»); thư mục máy sửa ở /api/v6/paths."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.config as cfg
from core import machine

ROOT = Path(__file__).resolve().parents[1]


def test_films_ui_has_no_v4_settings_modal():
    html = (ROOT / "films_ui.html").read_text(encoding="utf-8")
    for gone in ("settings-modal", "openSettingsModal", "saveSettings", "Cài Đặt Hệ Thống Pineline V4", "cfg-llm-key"):
        assert gone not in html, gone


def test_list_films_reports_ok(monkeypatch):
    from http_routes import routes_films
    monkeypatch.setattr(routes_films.film_db, "sync_uploads", lambda: None)
    monkeypatch.setattr(routes_films.film_db, "query", lambda **k: [])
    monkeypatch.setattr(routes_films.film_db, "stats", lambda: {"total": 0})
    d = routes_films.list_films(q="", status="", limit=5, order="recent", work_exists=None, block_status="")
    assert d["ok"] is True and d["films"] == []


def test_desktop_library_has_publish_and_block_tools():
    js = (ROOT / "desktop" / "ui" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "desktop" / "ui" / "index.html").read_text(encoding="utf-8")
    for needle in ("/api/auto-publish/config", "/api/auto-publish/scan", "/api/uploader/skip-current", "/films/check-block",
                   "/films/check-block-all", "/films/purge-missing", "/api/v6/paths"):
        assert needle in js, needle
    for el in ('id="pub-auto"', 'id="films-block"', 'id="films-csv"', 'data-pane="paths"'):
        assert el in html, el


@pytest.fixture
def conf(tmp_path, monkeypatch):
    f = tmp_path / "pineline.config.json"
    f.write_text(json.dumps({"OTHER": "giữ"}), encoding="utf-8")
    monkeypatch.setattr(cfg, "_cfg_file", f)
    monkeypatch.setattr(cfg, "_CFG", {})
    monkeypatch.setattr(cfg, "_cfg_mtime", 0.0)
    for k in machine.PATH_KEYS:
        monkeypatch.delenv(k, raising=False)
    cfg._load_cfg(force=True)
    return f


def test_paths_save_and_reject_unknown(conf, tmp_path):
    lib = tmp_path / "Lib"
    lib.mkdir()
    assert machine.save_paths({"LIBRARY_DIR": f'"{lib}"'}) == ["LIBRARY_DIR"]
    snap = machine.paths_snapshot()
    assert snap["LIBRARY_DIR"]["value"] == str(lib) and snap["LIBRARY_DIR"]["exists"] is True
    assert json.loads(conf.read_text(encoding="utf-8"))["OTHER"] == "giữ"
    with pytest.raises(ValueError):
        machine.save_paths({"LLM_API_KEY": "x"})            # chỉ thư mục, không phải khoá AI
    assert "LLM_API_KEY" not in json.loads(conf.read_text(encoding="utf-8"))
