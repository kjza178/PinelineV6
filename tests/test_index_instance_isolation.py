"""Fix 2026-11-12 agentJ: index job (state.index, dùng chung trong tiến trình) cách ly theo work root instance.

Lỗi: test port P6 (brain/hold, chạy trước test_app_integration) ghi manifest thư mục tạm qua state.job_state →
hook ghi đưa MỌI thư mục vào index; app startup `load_all` chỉ cộng thêm → /api/v6/jobs + snapshot SSE lẫn
'2937ffd7989f', 'f0834acf0cc0', 'jB'… Nay: load_all GẮN + nạp lại; hook chỉ nhận job trong work root đã gắn."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from state import job_state
from state.index import JobIndex, index


def _mk(root: Path, jid: str, **extra) -> Path:
    d = root / jid
    d.mkdir(parents=True, exist_ok=True)
    data = {"request": {"title": jid}, "stages": {"download": {"status": "done"}}}
    data.update(extra)
    (d / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    return d


@pytest.fixture
def shared_index(monkeypatch):
    """Index dùng chung thật (như app), publish gom lại; trả về trạng thái chưa gắn sau test."""
    got: list = []
    monkeypatch.setattr(index, "_publish", lambda t, d: got.append((t, d)))
    index.unbind()
    yield got
    index.unbind()


def test_foreign_writes_do_not_leak_into_instance_index(tmp_path, shared_index):
    real = tmp_path / "real_work"            # work root "thật" (máy user) — ngoài instance
    inst_root = tmp_path / "inst" / "work"
    foreign = _mk(real, "2937ffd7989f")
    _mk(inst_root, "job-a")
    # trước khi app start (chưa gắn): test/script khác ghi → vào index (hành vi cũ, vô hại khi chưa gắn)
    job_state.mutate(foreign, lambda d: d.setdefault("agent", {}).update(hold_reason="x"))
    job_state.create_job(tmp_path / "other" / "jB")
    assert {"2937ffd7989f", "jB"} <= {r["job_id"] for r in index.list()}
    # app startup: load_all gắn + nạp LẠI → chỉ job của instance
    assert index.load_all(inst_root) == 1
    assert [r["job_id"] for r in index.list()] == ["job-a"]
    # sau khi gắn: ghi ngoài instance bị bỏ qua, ghi trong instance vẫn lên index + event
    shared_index.clear()
    job_state.mutate(foreign, lambda d: d.setdefault("agent", {}).update(hold_reason="y"))
    job_state.create_job(real / "f0834acf0cc0", request={"title": "lạ"})
    assert [r["job_id"] for r in index.list()] == ["job-a"] and shared_index == []
    job_state.mutate(inst_root / "job-a", lambda d: d.update(title="A2"))
    assert index.get("job-a")["title"] == "A2"
    assert [t for t, _ in shared_index] == ["job.updated"]
    # job mới trong instance vẫn vào
    job_state.create_job(inst_root / "job-b", request={"title": "B"})
    assert [r["job_id"] for r in index.list()] == ["job-a", "job-b"]
    # shutdown: nhả gắn → lại nhận mọi thư mục (test khác dùng index chung không bị ảnh hưởng)
    index.unbind()
    assert index.root is None and index.owns(real / "x")


def test_load_all_replaces_not_accumulates(tmp_path):
    ix = JobIndex(publish=lambda t, d: None)
    _mk(tmp_path / "a", "j1")
    _mk(tmp_path / "b", "j2")
    assert ix.load_all(tmp_path / "a") == 1
    assert ix.load_all(tmp_path / "b") == 1
    assert [r["job_id"] for r in ix.list()] == ["j2"]
    assert ix.apply_dir(tmp_path / "a" / "j1", {"stages": {}}) is None
    assert ix.apply_dir(tmp_path / "b" / "j3", {"stages": {}})["job_id"] == "j3"


def test_owns_follows_symlink_or_junction(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "work_link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("không tạo được symlink ở môi trường này")
    ix = JobIndex(publish=lambda t, d: None)
    ix.load_all(real)
    assert ix.owns(link / "j1") and ix.owns(real / "j1")     # code V5 ghi qua ROOT/work (junction) vẫn nhận
    assert not ix.owns(tmp_path / "elsewhere" / "j1")


def test_watcher_root_is_instance_root(tmp_path):
    """ManifestWatcher của app đọc inst.work_root — cùng root đã gắn (không quét work thật)."""
    from state.watcher import ManifestWatcher
    ix = JobIndex(publish=lambda t, d: None)
    inst_root = tmp_path / "w"
    _mk(inst_root, "j1")
    ix.load_all(inst_root)
    w = ManifestWatcher(inst_root, index=ix)
    _mk(inst_root, "j2")
    w.scan_once(full=True)
    assert [r["job_id"] for r in ix.list()] == ["j1", "j2"]
