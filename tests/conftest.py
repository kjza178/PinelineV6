import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def job(tmp_path):
    wd = tmp_path / "job1"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"stages": {"download": {"status": "done"}}}), encoding="utf-8")
    return wd


@pytest.fixture
def events(monkeypatch):
    """Gom event do state phát ra (thay bus thật)."""
    from state import index as idx_mod
    got = []
    monkeypatch.setattr(idx_mod.index, "_publish", lambda t, d: got.append((t, d)))
    return got
