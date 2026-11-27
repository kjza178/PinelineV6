import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# films.db của phiên test: đặt TRƯỚC khi module nào import core.film_db (vd `import server` lúc thu thập test mở DB
# ngay) — fixture _isolate_film_db bên dưới chạy quá muộn cho các lần mở lúc import. fix 2026-11-14.
if not os.environ.get("FILM_DB"):
    import tempfile
    os.environ["FILM_DB"] = str(Path(tempfile.mkdtemp(prefix="pineline_test_db_")) / "films.db")


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


@pytest.fixture(autouse=True)
def _isolate_translate_ai_state():
    """2026-11-21: luồng dịch nhớ «model vừa hỏng» (stage_review._DEAD), «đổi model theo Cài đặt» (cfg._LIVE) và kết
    quả dò AI trên máy (cfg._LOCAL_PROBE) giữa các lần gọi — test phải độc lập, và KHÔNG được dò llama-server thật
    (máy dev Windows có llama chạy → test «không có dự phòng» sẽ lệch). Test cần AI máy tự đặt _LOCAL_PROBE."""
    from engines.translate_simple import cfg as ts_cfg
    from engines.translate_simple import stage_review as ts_rv
    ts_rv._DEAD.clear()
    ts_cfg._LIVE.update(seen=None, switch={})
    ts_cfg._LOCAL_PROBE.update(t=float("inf"), ok=False)
    yield
    ts_rv._DEAD.clear()
    ts_cfg._LIVE.update(seen=None, switch={})
    ts_cfg._LOCAL_PROBE.update(t=0.0, ok=False)


@pytest.fixture(autouse=True)
def _isolate_disk_library_state(tmp_path_factory, monkeypatch):
    """2026-11-27: services.disk_library nhớ thư mục Hongguo 2 vào data/hg_last_library.json — test không được ghi
    file thật của repo (và không được đọc kết quả test trước)."""
    from services import disk_library as dl
    monkeypatch.setattr(dl, "_LAST_HG", tmp_path_factory.mktemp("hglast") / "hg_last_library.json")
    yield
