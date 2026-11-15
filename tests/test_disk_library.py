"""«Phim trên ổ» (services/disk_library.py): quét THẲNG LIBRARY_DIR — không cần Hongguo trả lời."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from services import disk_library as dl


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = tmp_path / "Lib"
    (root / "BoA").mkdir(parents=True)
    for n in ("S01E001.mp4", "S01E002.mp4", "BoA (merged).mp4"):
        (root / "BoA" / n).write_bytes(b"x" * 10)
    (root / "BoB").mkdir()
    for n in ("第001集.mp4", "第002集.mp4", "第003集.mp4"):
        (root / "BoB" / n).write_bytes(b"x")
    (root / "BoB" / ".series.json").write_text(json.dumps({"title": "仙界篇 第二季", "series_id": "123"}), encoding="utf-8")
    (root / "BoC").mkdir()
    (root / "BoC" / "001.mp4").write_bytes(b"x")
    (root / "Rong").mkdir()
    (root / "le.mp4").write_bytes(b"x")
    work = tmp_path / "work"
    (work / "job1").mkdir(parents=True)
    (work / "job1" / "manifest.json").write_text(json.dumps({"request": {"orig_path": str(root / "le.mp4")}}), encoding="utf-8")
    monkeypatch.setenv("PINELINE_WORK_ROOT", str(work))
    monkeypatch.setattr(dl, "root", lambda: root)
    monkeypatch.setattr(dl, "_hg_library", lambda: ({}, None, "không có Hongguo 2 (test)"))
    dl._HG_CACHE["at"] = 0.0
    return root


def test_scan_states_from_disk_only(lib):
    r = dl.scan()
    assert r["ok"] and r["root"] == str(lib)
    by = {it["name"]: it for it in r["items"]}
    assert set(by) == {"BoA", "BoB", "BoC", "le.mp4"}                       # thư mục rỗng bị bỏ
    assert by["BoA"]["state"] == "ready" and by["BoA"]["path"].endswith("BoA (merged).mp4")
    assert by["BoB"]["state"] == "need_merge" and by["BoB"]["episodes"] == 3 and by["BoB"]["title"] == "仙界篇 第二季"
    assert by["BoC"]["state"] == "ready" and by["BoC"]["path"].endswith("001.mp4")
    assert by["le.mp4"]["state"] == "done"                                  # đã có job từ file này
    assert r["counts"] == {"ready": 2, "need_merge": 1, "done": 1}


def test_scan_missing_root(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "root", lambda: tmp_path / "khong_co")
    r = dl.scan()
    assert r["ok"] is False and "Cài đặt → Thư mục" in r["error"]


def test_add_jobs_only_ready_and_groups_via_feeder(lib, monkeypatch):
    import agents.agent_feeder as feeder
    seen = {}
    monkeypatch.setattr(feeder, "add_jobs", lambda base, chosen: seen.update(base=base, chosen=chosen) or
                        {"job_ids": ["j1"], "errors": []})
    r = dl.add_jobs(["BoA", "BoB", "le.mp4"], base="http://127.0.0.1:1")
    assert r["ok"] and r["job_ids"] == ["j1"]
    assert [c["name"] for c in seen["chosen"]] == ["BoA"] and seen["chosen"][0]["path"].endswith("(merged).mp4")
    assert any("chưa gộp tập" in e for e in r["errors"]) and any("đã làm" in e for e in r["errors"])


def test_merge_runs_in_background_and_reports_error(lib, monkeypatch):
    import agents.hongguo_bridge as hg
    events = []
    monkeypatch.setattr(dl, "_publish", lambda k, d: events.append(d))
    monkeypatch.setattr(hg, "episode_resolutions", lambda f: {"mixed": False})
    monkeypatch.setattr(hg, "merge_series_copy", lambda f, on_pct=None: {"ok": False, "error": "concat hỏng"})
    assert dl.merge(["BoB", "../ngoai"])["queued"] == ["BoB"]               # tên ra ngoài thư viện bị chặn
    for _ in range(100):
        if any(e.get("state") == "error" for e in events):
            break
        time.sleep(0.02)
    assert any(e.get("state") == "error" and e["error"] == "concat hỏng" for e in events)
    assert {it["name"]: it for it in dl.scan()["items"]}["BoB"]["merge_error"] == "concat hỏng"


def test_desktop_has_disk_view():
    ui = Path(__file__).resolve().parents[1] / "desktop" / "ui"
    html, js = (ui / "index.html").read_text(encoding="utf-8"), (ui / "app.js").read_text(encoding="utf-8")
    assert 'data-view="disk"' in html and 'id="view-disk"' in html and "Phim đã làm" in html and "Phim chờ làm" in html
    assert "/api/v6/disk-library/add" in js and "/api/v6/disk-library/merge" in js and "disk_library.merge" in js


def test_hongguo2_library_is_main_source(lib, tmp_path, monkeypatch):
    """Có Hongguo 2: dùng thư mục lưu + tên / series_id của nó; ổ vẫn quyết trạng thái."""
    (lib / "BoA" / "poster.jpg").write_bytes(b"j" * 64)
    monkeypatch.setattr(dl, "_hg_library", lambda: ({"BoA": {"name": "BoA", "title": "天赋 第八季", "series_id": "77"}}, lib, ""))
    dl._HG_CACHE["at"] = 0.0
    r = dl.scan(fresh=True)
    a = {it["name"]: it for it in r["items"]}["BoA"]
    assert r["source"] == "hongguo2" and a["title"] == "天赋 第八季" and a["series_id"] == "77" and a["in_hongguo"]
    assert a["has_poster"] and dl.poster("BoA") == lib / "BoA" / "poster.jpg" and dl.poster("../x") is None
    assert dl.lib_dir() == lib


def test_title_vi_from_cache_only(lib, monkeypatch):
    import engines.title_mt as tmt
    monkeypatch.setattr(tmt, "_load_cache", lambda: {"by_id": {}, "by_zh": {"仙界篇 第二季": {"zh": "仙界篇 第二季", "vi": "Tiên Giới Thiên 2"}}})
    by = {it["name"]: it for it in dl.scan()["items"]}
    assert by["BoB"]["title_vi"] == "Tiên Giới Thiên 2" and by["BoA"]["title_vi"] == ""
