"""core/timeutil + một kiểu mốc thời gian (fix 2026-11-13: started_at/finished_at lẫn ISO và epoch)."""
import json
import time
from datetime import datetime

from core import timeutil as tu
from state import job_state as js


def test_to_epoch_accepts_every_legacy_shape():
    t = 1794362012.5
    assert tu.to_epoch(t) == t
    assert tu.to_epoch(str(t)) == t                              # cột TEXT của films.db
    assert tu.to_epoch("2026-11-10T12:00:00+00:00") == tu.to_epoch("2026-11-10T12:00:00Z")
    naive = "2026-11-10 12:00:00"                                # split_parts: giờ máy
    assert tu.to_epoch(naive) == datetime.fromisoformat(naive).timestamp()
    for bad in (None, "", "abc", 0, True):
        assert tu.to_epoch(bad) is None


def test_to_iso_roundtrip_and_sortable():
    a, b = tu.to_iso(1794362012.5), tu.to_iso("1794362999")
    assert a.startswith("2026-") and a.endswith("+00:00") and a < b
    assert abs(tu.to_epoch(tu.now_iso()) - time.time()) < 5
    assert tu.to_iso("rác") is None


def _stage(wd, name):
    return json.loads((wd / "manifest.json").read_text(encoding="utf-8"))["stages"][name]


def test_job_state_writes_iso_and_resets_on_rerun(job):
    js.mark_running(job, "asr")
    s1 = _stage(job, "asr")["started_at"]
    assert isinstance(s1, str) and tu.to_epoch(s1)
    js.mark_running(job, "asr", cur_text="50%")                   # gọi lặp khi đang chạy → giữ mốc
    assert _stage(job, "asr")["started_at"] == s1
    js.mark_done(job, "asr")
    st = _stage(job, "asr")
    assert isinstance(st["finished_at"], str) and tu.to_epoch(st["finished_at"]) >= tu.to_epoch(s1)
    time.sleep(0.01)
    js.mark_running(job, "asr")                                   # lượt MỚI → mốc mới, bỏ finished_at cũ
    st = _stage(job, "asr")
    assert st["started_at"] != s1 and "finished_at" not in st
    js.mark_failed(job, "asr", "lỗi")
    assert isinstance(_stage(job, "asr")["finished_at"], str)
    assert isinstance(_stage(job, "asr")["updated_at"], float)    # nhịp tim vẫn là epoch
