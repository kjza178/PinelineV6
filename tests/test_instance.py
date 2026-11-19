import json
import os

import pytest

from core.instance import load


def test_load_bundled_v6():
    inst = load("v6")
    assert inst.port == 8806 and inst.desktop and inst.base_url == "http://127.0.0.1:8806"


def test_load_custom(tmp_path, monkeypatch):
    (tmp_path / "x.json").write_text(json.dumps({"port": 9001, "work_root": str(tmp_path / "w"),
                                                 "temp": str(tmp_path / "t"), "foo": 1}))
    inst = load("x", directory=tmp_path)
    assert inst.name == "x" and inst.extra == {"foo": 1}
    # mọi khoá apply_env ghi → monkeypatch khôi phục sau test (không rò WORK_DIR/cờ sang test khác)
    for k in ("PINELINE_PORT", "PINELINE_BASE", "PINELINE_INSTANCE", "TEMP", "TMP", "PORT", "WORK_DIR",
              "PINELINE_WORK_ROOT", "AUTOPILOT_PORT", "AUTOPILOT_URL", "AGENT_BRAIN", "AGENT_WATCH",
              "AGENT_TELEGRAM", "PINELINE_ROUTERS", *(f"PINELINE_FLAG_{n.upper()}" for n in inst.flags())):
        monkeypatch.delenv(k, raising=False)
    inst.apply_env()
    assert os.environ["PINELINE_PORT"] == "9001"
    assert os.environ["PINELINE_BASE"] == "http://127.0.0.1:9001"


def test_missing():
    with pytest.raises(FileNotFoundError):
        load("không-có")


# ── 2026-11-19: V6 portable — đường dẫn tương đối theo gốc cây + <tên>.local.json đè cấu hình máy ──
def test_v6_bundled_paths_are_portable():
    from core.instance import ROOT
    inst = load("v6")
    assert inst.work_root == ROOT / "work" and inst.temp == ROOT / "scratch" / "tmp"


def test_local_override_and_relative_paths(tmp_path):
    d = tmp_path / "instances"
    d.mkdir()
    (d / "x.json").write_text(json.dumps({"port": 9001, "work_root": "./work", "temp": "./scratch/tmp",
                                          "feeder": False}), encoding="utf-8")
    inst = load("x", directory=d)
    assert inst.work_root == tmp_path / "work" and inst.temp == tmp_path / "scratch" / "tmp"
    other = tmp_path / "E" / "PinelineV6_work"
    (d / "x.local.json").write_text("\ufeff" + json.dumps({"work_root": str(other), "feeder": True}),
                                    encoding="utf-8")       # Notepad hay lưu kèm BOM
    inst = load("x", directory=d)
    assert inst.work_root == other and inst.temp == tmp_path / "scratch" / "tmp" and inst.extra["feeder"] is True
    (d / "x.local.json").write_text("{hỏng", encoding="utf-8")
    with pytest.raises(ValueError, match="x.local.json"):
        load("x", directory=d)
