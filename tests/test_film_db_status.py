"""2026-11-20: thư viện báo «lỗi» dù job đã xong — trạng thái phải theo 5 bước chính như danh sách job."""
import json

import core.film_db as fdb


def _st(**kw):
    base = {n: {"status": "done"} for n in fdb.CORE_STAGES}
    for k, v in kw.items():
        base[k] = {"status": v}
    return base


def test_side_stage_failed_does_not_mark_done_job_failed():
    assert fdb._status_of(_st(separate="failed", review="error")) == ("done", "mux")


def test_core_failed_and_running():
    assert fdb._status_of(_st(tts="failed", mux="pending"))[0] == "failed"
    assert fdb._status_of(_st(asr="running", mux="pending"))[0] == "running"
    assert fdb._status_of(_st(tts="pending", mux="pending")) == ("pending", "translate")


def test_write_hook_syncs_only_on_status_change(tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "j1").mkdir(parents=True)
    monkeypatch.setattr(fdb, "_work", lambda: work)
    calls = []
    monkeypatch.setattr(fdb, "sync_one", lambda jid: calls.append(jid))
    fdb._LAST_STATUS.clear()
    data = {"stages": _st(mux="running")}
    fdb.write_hook(work / "j1", data)
    fdb.write_hook(work / "j1", data)                      # cùng trạng thái → không đồng bộ lại
    fdb.write_hook(work / "j1", {"stages": _st()})          # running → done
    fdb.write_hook(tmp_path / "other" / "j1", {"stages": _st()})   # ngoài work root → bỏ
    assert calls == ["j1", "j1"]
    fdb._LAST_STATUS.clear()
