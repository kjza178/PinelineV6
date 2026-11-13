from __future__ import annotations
import json
from pathlib import Path
import core.job_queue as jq


def _manifest(wd: Path, stages: dict) -> None:
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "manifest.json").write_text(
        json.dumps({"stages": stages}, ensure_ascii=False), encoding="utf-8"
    )


def test_add_skips_missing_done_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)
    _manifest(tmp_path / "a", {s: {"status": "done"} for s in jq.WIRED})
    (tmp_path / "a" / "output.mp4").write_bytes(b"x" * 100)
    _manifest(tmp_path / "b", {"download": {"status": "done"}})
    r = jq.queue_add(["missing", "a", "b", "b"], opts={"voice": "v5"}, skip_done=True)
    assert r["queued"] == ["b"]
    assert "a" in r["skipped_done"]
    assert "missing" in r["skipped_missing"]
    assert jq.queue_state()["queue"] == ["b"]


def test_clear_empties_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)
    _manifest(tmp_path / "b", {"download": {"status": "done"}})
    jq.queue_add(["b"], opts={})
    jq.queue_clear()
    st = jq.queue_state()
    assert st["queue"] == []
    assert st["current"] is None


def test_persist_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)
    _manifest(tmp_path / "b", {"download": {"status": "done"}})
    jq.queue_add(["b"], opts={"voice": "v5", "orig_vol": 0.22})
    raw = json.loads((tmp_path / "_job_queue.json").read_text(encoding="utf-8"))
    assert raw["queue"] == ["b"]
    assert raw["opts"]["voice"] == "v5"
    jq._reset_for_tests(load_disk=False)
    jq._load()
    assert jq.queue_state()["queue"] == ["b"]
    assert jq.queue_state()["opts"]["orig_vol"] == 0.22


def test_duplicate_enqueue_does_not_change_options_or_report_queued(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)
    _manifest(tmp_path / "a", {"download": {"status": "done"}})
    _manifest(tmp_path / "b", {"download": {"status": "done"}})

    jq.queue_add(["a", "b"], opts={"voice": "v1"})
    result = jq.queue_add(["b", "a"], opts={"voice": "v2"})

    assert result["queued"] == []
    assert jq.queue_state()["queue"] == ["a", "b"]
    assert jq.queue_state()["opts"]["voice"] == "v1"
    assert jq.queue_state()["opts_by_job"]["a"]["voice"] == "v1"
    assert jq.queue_state()["opts_by_job"]["b"]["voice"] == "v1"


def test_second_batch_opts_only_apply_to_new_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)
    _manifest(tmp_path / "a", {"download": {"status": "done"}})
    _manifest(tmp_path / "c", {"download": {"status": "done"}})
    jq.queue_add(["a"], opts={"voice": "v1"})
    jq.queue_add(["c"], opts={"voice": "v2", "audio_mode": "duck_bgm"})
    ob = jq.queue_state()["opts_by_job"]
    assert ob["a"]["voice"] == "v1"
    assert ob["c"]["voice"] == "v2"
    assert ob["c"]["audio_mode"] == "duck_bgm"


def test_queue_add_group_orders_by_index(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)

    def _g(name, idx):
        wd = tmp_path / name
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "manifest.json").write_text(
            json.dumps({
                "stages": {"download": {"status": "done"}},
                "request": {"group_id": "g1", "group_index": idx, "group_title": "S"},
            }),
            encoding="utf-8",
        )

    _g("p3", 3)
    _g("p1", 1)
    _g("p2", 2)
    r = jq.queue_add_group("g1", opts={"voice": "v5"})
    assert r["ok"] is True
    assert r["group_order"] == ["p1", "p2", "p3"]
    assert r["queued"] == ["p1", "p2", "p3"]


def test_queue_busy_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)
    assert jq.queue_busy() is False
    _manifest(tmp_path / "a", {"download": {"status": "done"}})
    jq.queue_add(["a"], opts={})
    assert jq.queue_busy() is True


def test_save_uses_atomic_sibling_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)
    _manifest(tmp_path / "b", {"download": {"status": "done"}})
    real_replace = jq.os.replace
    replaced = []

    def tracked_replace(source, target):
        replaced.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(jq.os, "replace", tracked_replace)
    jq.queue_add(["b"], opts={"voice": "v1"})

    source, target = replaced[-1]
    assert source.name == "._job_queue.json.tmp"
    assert target == tmp_path / "_job_queue.json"
    assert not source.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["queue"] == ["b"]


def test_queue_move_and_reorder(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)
    for name in ("j1", "j2", "j3", "j4"):
        _manifest(tmp_path / name, {"download": {"status": "done"}})
    jq.queue_add(["j1", "j2", "j3", "j4"], opts={}, sort_first=False)
    assert jq.queue_state()["queue"] == ["j1", "j2", "j3", "j4"]

    # Test move up
    jq.queue_move("j3", "up")
    assert jq.queue_state()["queue"] == ["j1", "j3", "j2", "j4"]

    # Test move top
    jq.queue_move("j4", "top")
    assert jq.queue_state()["queue"] == ["j4", "j1", "j3", "j2"]

    # Test move down
    jq.queue_move("j4", "down")
    assert jq.queue_state()["queue"] == ["j1", "j4", "j3", "j2"]

    # Test move bottom
    jq.queue_move("j1", "bottom")
    assert jq.queue_state()["queue"] == ["j4", "j3", "j2", "j1"]

    # Test reorder
    jq.queue_reorder(["j2", "j4", "j1", "j3"])
    assert jq.queue_state()["queue"] == ["j2", "j4", "j1", "j3"]


def test_queue_smart_sort_series_season(tmp_path, monkeypatch):
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)

    # Tạo các manifest với Season 1, 2, 4
    for jid, s_no, title in [
        ("job_p4", 4, "Sinh tồn sa mạc P4"),
        ("job_p1", 1, "Sinh tồn sa mạc P1"),
        ("job_p2", 2, "Sinh tồn sa mạc P2"),
    ]:
        wd = tmp_path / jid
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "manifest.json").write_text(
            json.dumps({
                "stages": {"download": {"status": "done"}},
                "title_vi": title,
                "request": {"title": title, "group_index": s_no, "group_title": "Sinh tồn sa mạc"}
            }, ensure_ascii=False), encoding="utf-8"
        )

    # Khi add với sort_first=True, P1 -> P2 -> P4
    r = jq.queue_add(["job_p4", "job_p1", "job_p2"], opts={})
    assert r["queue"] == ["job_p1", "job_p2", "job_p4"]


def test_queue_sort_prefers_title_phan_over_group_index(tmp_path, monkeypatch):
    """group_index lệch badge — vẫn sort theo Phần trên title_vi + gom bằng group_id."""
    monkeypatch.setattr(jq, "WORK_ROOT", tmp_path)
    jq._reset_for_tests()
    monkeypatch.setattr(jq, "_ensure_worker_unlocked", lambda: None)

    specs = [
        # jid, group_index (SAI), title_vi (ĐÚNG phần)
        ("a", 4, "Thiên phú vô song Phần 2"),
        ("b", 7, "Thiên phú vô song Phần 1"),
        ("c", 2, "Thiên phú vô song Phần 3"),
        ("d", 1, "Bộ khác Phần 1"),
    ]
    for jid, gi, title in specs:
        wd = tmp_path / jid
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "manifest.json").write_text(
            json.dumps({
                "stages": {"download": {"status": "done"}},
                "title_vi": title,
                "request": {
                    "title": title,
                    "group_index": gi,
                    "group_id": "g_thien" if jid != "d" else "g_other",
                    "group_title": "Thiên phú" if jid != "d" else "Bộ khác",
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    jq.queue_add(["a", "b", "c", "d"], opts={}, sort_first=False)
    r = jq.queue_sort("series_season")
    # group_id khác nhau → 2 cụm; trong g_thien: P1,P2,P3 bất chấp group_index
    q = r["queue"]
    assert q.index("b") < q.index("a") < q.index("c")
    assert jq.get_job_info("a")["season"] == 2
    assert jq.get_job_info("b")["season"] == 1

