"""Phase E–F + unity + core/ layout: packages live; root shims/TARGET shells gone."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Former root shims (Phase B–D); must be absent after Phase E.
_GONE_SHIMS = (
    "render.py",
    "translate.py",
    "asr.py",
    "agent_core.py",
    "routes_ui.py",
    "download.py",
    "clone_tts.py",
    "sfx_lib.py",
    "hongguo_bridge.py",
    "fun_asr_nano.py",
    "lib_check.py",
    "test_channel.py",
)

# Kernel modules moved into core/ (2026-11-05); no root copies / shims.
_GONE_CORE_ROOT = (
    "config.py",
    "batch_config.py",
    "job_queue.py",
    "job_meta.py",
    "film_db.py",
    "manifest.py",
    "fileio.py",
)
_CORE_PACKAGE = (
    "config.py",
    "batch_config.py",
    "job_queue.py",
    "job_meta.py",
    "film_db.py",
    "manifest.py",
    "fileio.py",
)

# Empty TARGET shells removed in unity (2026-11-04).
_GONE_PINELINE_SHELLS = (
    "agents",
    "pipeline",
    "sidecar",
    "desktop",
    "ops",
)


def test_films_ui_root_resolves_to_project():
    from http_routes.routes_films import ROOT as films_root

    assert films_root.resolve() == ROOT.resolve()
    ui = films_root / "static" / "ui" / "films_ui.html"
    assert ui.is_file(), f"missing films_ui.html at {ui}"


def test_find_index_absolute_even_if_cwd_windows():
    from http_routes.routes_ui import _HERE, find_index

    assert _HERE.resolve() == ROOT.resolve()
    old = os.getcwd()
    try:
        os.chdir(r"C:\Windows")
        idx = find_index()
    finally:
        os.chdir(old)
    assert idx is not None
    assert idx.is_file()
    assert idx.resolve() == (ROOT / "static" / "ui" / "index3.html").resolve()


def test_meta_thumbnail_dir_is_project_data():
    """routes_hg must serve thumbnails from project data/, not http_routes/data/."""
    routes_hg = ROOT / "http_routes" / "routes_hg.py"
    root = routes_hg.resolve().parent.parent
    thumb_dir = (root / "data" / "thumbnails").resolve()
    assert root == ROOT.resolve()
    assert thumb_dir == (ROOT / "data" / "thumbnails").resolve()
    # Wrong layout would resolve under http_routes/data/
    assert thumb_dir != (routes_hg.resolve().parent / "data" / "thumbnails").resolve()
    src = routes_hg.read_text(encoding="utf-8")
    assert "def meta_thumbnail_get" in src
    assert "parent.parent" in src


def test_openapi_has_ui_and_films():
    import server

    paths = set(server.app.openapi().get("paths", {}))
    assert "/" in paths
    assert "/films/ui" in paths


def test_phase_e_packages_exist_shims_gone():
    """Phase E: real modules under packages; root shims deleted."""
    assert (ROOT / "engines" / "render.py").is_file()
    assert (ROOT / "engines" / "translate.py").is_file()
    assert (ROOT / "engines" / "asr" / "asr.py").is_file()
    assert (ROOT / "agents" / "agent_core.py").is_file()
    assert (ROOT / "http_routes" / "routes_ui.py").is_file()
    assert (ROOT / "tools" / "lib_check.py").is_file()
    for name in _GONE_SHIMS:
        assert not (ROOT / name).exists(), f"root shim still present: {name}"


def test_core_package_layout():
    """App kernel lives in core/; root must not keep copies or shims."""
    assert (ROOT / "core" / "__init__.py").is_file()
    for name in _CORE_PACKAGE:
        assert (ROOT / "core" / name).is_file(), f"missing core/{name}"
    for name in _GONE_CORE_ROOT:
        assert not (ROOT / name).exists(), f"root core module still present: {name}"
    import core.config as config
    import core.job_queue as job_queue

    assert config.ROOT.resolve() == ROOT.resolve()
    assert job_queue.WORK_ROOT.resolve() == (ROOT / "work").resolve()
    # Bare import must fail (no root shim). Note: root `config/` is a preset
    # JSON folder (namespace package) — assert against job_queue instead.
    sys.modules.pop("job_queue", None)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("core.job_queue")
    # core.config must be the real module, not the preset namespace dir.
    assert hasattr(config, "get") and hasattr(config, "ROOT")


def test_unity_apps_stub_and_pineline_shells_gone():
    """Unity: launchers in apps/; empty pineline TARGET shells deleted."""
    apps_bat = ROOT / "apps" / "run_v5.bat"
    root_bat = ROOT / "run_v5.bat"
    assert apps_bat.is_file(), "apps/run_v5.bat missing"
    assert root_bat.is_file(), "root run_v5.bat stub missing"
    stub = root_bat.read_text(encoding="utf-8", errors="replace")
    assert len(stub) < 200, "root run_v5.bat should stay a small stub"
    assert "apps\\" in stub or "apps/" in stub, "stub must call apps\\run_v5.bat"

    assert not (ROOT / "pineline" / "agents").exists()
    assert not (ROOT / "pineline" / "server" / "services").exists()
    for name in _GONE_PINELINE_SHELLS:
        assert not (ROOT / "pineline" / name).exists(), f"TARGET shell still present: pineline/{name}"


def test_engines_phase_e_roots():
    """Package imports resolve; Path roots stay project root; bare render fails."""
    import engines.assemble as assemble
    import engines.clone_tts as clone_tts
    import engines.download as download
    import engines.llm as llm
    import engines.ocr as ocr
    import engines.render as render
    import engines.render_fx as render_fx
    import engines.separate as separate
    import engines.sfx_lib as sfx_lib
    import engines.translate as translate
    import agents.agent_core as agent_core
    import http_routes.routes_ui as routes_ui

    assert render_fx.ROOT.resolve() == ROOT.resolve()
    assert sfx_lib.ROOT.resolve() == ROOT.resolve()
    assert clone_tts._PROJ.resolve() == ROOT.resolve()
    assert render._NVENC_STATE.resolve().parent == ROOT.resolve()
    assert sys.modules["engines.render"].__name__ == "engines.render"
    assert sys.modules["engines.translate"].__name__ == "engines.translate"
    assert "render" not in sys.modules or sys.modules["render"] is sys.modules["engines.render"]
    assert download and llm and ocr and separate and assemble and agent_core and routes_ui

    # Bare `import render` must fail (no root shim).
    sys.modules.pop("render", None)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("engines.render")
