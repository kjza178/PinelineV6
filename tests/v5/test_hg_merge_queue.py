from __future__ import annotations

import json

import pytest

import agents.hongguo_bridge as hg


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(hg, "_MERGE_STATE_PATH", tmp_path / "hg_merge_state.json")
    saved = (list(hg._merge_q), hg._merge_cur, list(hg._merge_hist), dict(hg._merge_done),
             dict(hg._merge_phase), dict(hg._merge_batch))
    hg._merge_q.clear(); hg._merge_hist.clear(); hg._merge_done.clear()
    hg._merge_phase.clear(); hg._merge_batch.clear()
    monkeypatch.setattr(hg, "_merge_cur", None)
    monkeypatch.setattr(hg, "_merge_stop", False)
    monkeypatch.setattr(hg, "_merge_thread", None)
    started = []
    monkeypatch.setattr(hg, "_start_worker", lambda base: started.append(base))
    lib = tmp_path / "Lib"
    for n in ("A", "B", "C"):
        (lib / n).mkdir(parents=True)
    monkeypatch.setattr(hg, "_output_dir", lambda base: lib)
    yield {"path": tmp_path / "hg_merge_state.json", "started": started, "lib": lib}
    hg._merge_q[:] = saved[0]; hg._merge_hist[:] = saved[2]
    hg._merge_done.clear(); hg._merge_done.update(saved[3])
    hg._merge_phase.clear(); hg._merge_phase.update(saved[4])
    hg._merge_batch.clear(); hg._merge_batch.update(saved[5])


def test_add_only_queues_and_persists(state, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("không được chuẩn hoá/dọn trong request enqueue")
    monkeypatch.setattr(hg, "merged_has_mixed_res", boom)
    monkeypatch.setattr(hg, "normalize_merged_file", boom)
    monkeypatch.setattr(hg, "cleanup_fragments", boom)
    r = hg.merge_queue_add(["A", "B", "C", "A"], base="http://x")
    assert r["queued"] == ["A", "B", "C"]
    assert state["started"] == ["http://x"]
    data = json.loads(state["path"].read_text(encoding="utf-8"))
    assert data["queue"] == ["A", "B", "C"]
    assert data["batch"]["total"] == 3 and data["base"] == "http://x"


def test_load_puts_current_back_in_front(state):
    state["path"].write_text(json.dumps({"queue": ["B", "C"], "current": "A",
                                         "batch": {"total": 3, "finished": 0}}), encoding="utf-8")
    hg._load_merge_state()
    assert hg._merge_q == ["A", "B", "C"]
    assert hg._merge_batch["total"] == 3


def test_resume_needs_hongguo(state, monkeypatch):
    hg._merge_q[:] = ["A"]
    monkeypatch.setattr(hg, "_output_dir", lambda base: None)
    assert hg.merge_resume("http://x") is False
    assert state["started"] == []
    monkeypatch.setattr(hg, "_output_dir", lambda base: state["lib"])
    assert hg.merge_resume("http://x") is True
    assert state["started"] == ["http://x"]


def test_finish_counts_batch(state, monkeypatch):
    hg._merge_batch.update({"total": 2, "finished": 0, "errors": 0})
    monkeypatch.setattr(hg, "_merge_cur", "A")
    hg._hist("A", "done", "ok")
    hg._finish("A")
    hg._hist("B", "error", "hỏng")
    hg._finish("B")
    assert hg._merge_batch["finished"] == 2 and hg._merge_batch["errors"] == 1
    assert hg._merge_cur is None
    data = json.loads(state["path"].read_text(encoding="utf-8"))
    assert data["batch"]["finished"] == 2


def test_phase_visible_in_state(state, monkeypatch):
    monkeypatch.setattr(hg, "merge_status", lambda base: {"running": False})
    hg._set_phase("A", "chuẩn hoá", 42.0)
    st = hg.merge_queue_state("http://x")
    assert st["phase"]["name"] == "A" and st["phase"]["pct"] == 42.0
