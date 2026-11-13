"""F1: một đường ghi manifest — hook, persist, watcher."""
import json
import os
import threading
import time

from core.manifest import Manifest, register_write_hook, unregister_write_hook
from state import job_state as js
from state.index import JobIndex
from state.watcher import ManifestWatcher


def disk(wd):
    return json.loads((wd / "manifest.json").read_text(encoding="utf-8"))


def test_hook_runs_for_mark_methods_and_persist(job):
    seen = []
    hook = lambda wd, d: seen.append(wd.name)  # noqa: E731
    register_write_hook(hook)
    try:
        m = Manifest(job)
        m.mark_running("asr")
        m.data["title_vi"] = "Phim"
        assert js.persist(m) is True
    finally:
        unregister_write_hook(hook)
    assert seen == ["job1", "job1"]
    assert disk(job)["title_vi"] == "Phim"


def test_hook_error_does_not_break_write(job):
    def bad(wd, d):
        raise RuntimeError("x")
    register_write_hook(bad)
    try:
        js.set_stage(job, "tts", n=1)
    finally:
        unregister_write_hook(bad)
    assert disk(job)["stages"]["tts"]["n"] == 1


def test_persist_from_stale_snapshot_keeps_hold(job, events):
    """Kịch bản B9: object sống lâu (m_early render) giữ snapshot cũ, luồng khác đặt HOLD."""
    m_early = Manifest(job)
    js.set_hold(job, reason="qa_gate", qa_blocked=True)
    m_early.data.setdefault("stages", {})["mux"] = {"status": "running"}
    js.persist(m_early)
    d = disk(job)
    assert d["agent"]["held_at"] == "qa_gate" and d["stages"]["mux"]["status"] == "running"


def test_persist_load_failed_refuses(job):
    (job / "manifest.json").write_text("{hỏng", encoding="utf-8")
    m = Manifest(job)
    m.data["x"] = 1
    assert js.persist(m) is False
    assert (job / "manifest.json").read_text(encoding="utf-8") == "{hỏng"


def test_index_updated_via_hook(job, events):
    m = Manifest(job)
    m.mark_done("asr")
    assert any(t == "job.updated" and d["job_id"] == "job1" for t, d in events)


def test_watcher_picks_up_external_write(tmp_path):
    wd = tmp_path / "j"
    wd.mkdir()
    (wd / "manifest.json").write_text('{"stages":{"asr":{"status":"running"}}}', encoding="utf-8")
    got = []
    ix = JobIndex(publish=lambda t, d: got.append((t, d)))
    ix.load_all(tmp_path)
    w = ManifestWatcher(tmp_path, index=ix, interval=0.05)
    w.start()
    try:
        time.sleep(0.15)
        assert got == [] or all(d["status"] == "running" for _, d in got)
        # "tiến trình con" ghi thẳng file
        (wd / "manifest.json").write_text('{"stages":{"asr":{"status":"done"}}}', encoding="utf-8")
        st = os.stat(wd / "manifest.json")
        os.utime(wd / "manifest.json", (st.st_atime, st.st_mtime + 5))
        deadline = time.time() + 2
        while time.time() < deadline and ix.get("j")["stages"]["asr"] != "done":
            time.sleep(0.05)
    finally:
        w.stop()
    assert ix.get("j")["stages"]["asr"] == "done"


def test_watcher_full_scan_new_and_removed(tmp_path):
    ix = JobIndex(publish=lambda *a: None)
    w = ManifestWatcher(tmp_path, index=ix)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "manifest.json").write_text('{"stages":{}}', encoding="utf-8")
    assert w.scan_once(full=True) == 1 and ix.get("a")
    (tmp_path / "a" / "manifest.json").rename(tmp_path / "a" / "x.json")
    w.scan_once(full=True)
    assert ix.get("a") is None


def test_concurrent_persist_and_semantic(job, events):
    """persist (snapshot cũ) chạy song song set_stage: không mất khoá của bên nào."""
    def legacy(i):
        for k in range(10):
            m = Manifest(job)
            m.data.setdefault("legacy", {})[f"{i}_{k}"] = k
            js.persist(m)

    def semantic(i):
        for k in range(10):
            js.set_stage(job, "translate", **{f"s{i}_{k}": k})
    ts = [threading.Thread(target=legacy, args=(i,)) for i in range(3)]
    ts += [threading.Thread(target=semantic, args=(i,)) for i in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    d = disk(job)
    assert len(d["legacy"]) == 30
    assert sum(1 for k in d["stages"]["translate"] if k.startswith("s")) == 30
