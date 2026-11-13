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
