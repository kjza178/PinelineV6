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


@pytest.fixture(autouse=True)
def _isolate_film_db(monkeypatch, tmp_path_factory):
    """Test không được ghi vào data/films.db thật: code gọi film_db.connect() ngoài ý muốn (vd
    auto_publisher._revert_job_to_done) sẽ ghi vào DB tạm của phiên test. Test tự đặt DB_PATH vẫn thắng."""
    import core.film_db as fdb
    real = ROOT / "data" / "films.db"
    if Path(fdb.DB_PATH) == real:
        monkeypatch.setattr(fdb, "DB_PATH", tmp_path_factory.getbasetemp() / "films_isolated.db")
    yield
