"""F6 — lượt kiểm cuối: rủi ro chéo pha (F3 SSE ↔ F5 tiến độ worker), cờ instance nhất quán, không còn
đường dẫn cây V2/V4/V5 trong code chạy, B11 (code chết).

Chạy ở cloud (không fastapi/GPU). Phần chỉ kiểm được trên Windows: scripts/verify_all.py (verify_all.bat).
"""
from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


_TICK = [1_700_000_000.0]


def _write(wd: Path, data: dict) -> None:
    mf = wd / "manifest.json"
    mf.write_text(json.dumps(data), encoding="utf-8")
    _TICK[0] += 10                         # mtime chắc chắn đổi (FS có độ phân giải thô)
    os.utime(mf, (_TICK[0], _TICK[0]))


# ───────────── F3 ↔ F5: tiến độ tiến trình con lên index/SSE ngay ─────────────

@pytest.fixture
def watcher(tmp_path):
    from state.index import JobIndex
    from state.watcher import ManifestWatcher
    got: list = []
    idx = JobIndex(publish=lambda t, d: got.append((t, d)))
    root = tmp_path / "work"
    (root / "j1").mkdir(parents=True)
    _write(root / "j1", {"stages": {"download": {"status": "done"}, "mux": {"status": "pending"}}})
    idx.load_all(root)
    # interval rất lớn → vòng nền không tự quét: test gọi tay
    w = ManifestWatcher(root, index=idx, interval=3600, full_every=36000)
    w.start()
    yield w, idx, root, got
    w.stop()


def test_watch_job_picks_child_write_without_full_scan(watcher):
    from state import watcher as wmod
    w, idx, root, got = watcher
    assert w in wmod.active_watchers()
    # "tiến trình con" ghi mux running (server không biết — index vẫn pending)
    _write(root / "j1", {"stages": {"download": {"status": "done"},
                                    "mux": {"status": "running", "progress": 12, "cur_text": "encode"}}})
    # R5.2 (2026-11-14): watcher theo dõi mọi job CHƯA XONG → thấy ngay ở lượt quét thường, không cần watch_job
    # (trước: job không "running" trong index bị bỏ qua tới lượt quét đủ, chậm tới 30 s).
    assert w.scan_once() == 1
    assert idx.get("j1")["status"] == "running"
    assert wmod.watch_job("j1") == 1              # watch_job vẫn dùng được (job đã done mà tiến trình con ghi lại)
    assert w.scan_once() == 0
    assert any(t == "job.updated" for t, _ in got)


def test_done_job_is_not_polled_until_watched(watcher):
    """Job đã xong không bị stat mỗi giây (thư viện lớn); tiến trình con ghi lại lên nó → cần watch_job / quét đủ."""
    from state import watcher as wmod
    w, idx, root, _ = watcher
    done = {"stages": {k: {"status": "done"} for k in ("download", "asr", "translate", "tts", "mux")}}
    _write(root / "j1", done)
    w.scan_once(full=True)
    assert idx.get("j1")["status"] == "done"
    _write(root / "j1", {**done, "stages": {**done["stages"], "mux": {"status": "running"}}})
    assert w.scan_once() == 0
    assert wmod.watch_job("j1") == 1 and w.scan_once() == 1
    assert idx.get("j1")["status"] == "running"
    wmod.unwatch_job("j1")


def test_unwatch_job_final_check_applies_done(watcher):
    from state import watcher as wmod
    w, idx, root, _ = watcher
    with wmod.watching("j1"):
        _write(root / "j1", {"stages": {"download": {"status": "done"}, "mux": {"status": "running"}}})
        w.scan_once()
        assert idx.get("j1")["status"] == "running"
        _write(root / "j1", {"stages": {"download": {"status": "done"}, "mux": {"status": "done"}},
                             "updated_at": 2})
    # ra khỏi with: đọc lần cuối → done lên index dù vòng nền chưa chạy
    assert idx.get("j1")["stages"]["mux"] == "done"
    assert idx.get("j1")["status"] != "running"
    assert "j1" not in w._extra


def test_stopped_watcher_unregistered(tmp_path):
    from state.index import JobIndex
    from state import watcher as wmod
    w = wmod.ManifestWatcher(tmp_path, index=JobIndex(publish=lambda *a: None), interval=3600)
    w.start()
    assert w in wmod.active_watchers()
    w.stop()
    assert w not in wmod.active_watchers()
    assert wmod.watch_job("x") == len(wmod.active_watchers())


def test_run_job_watches_job_during_spawn(job, monkeypatch, watcher):
    from workers import render_worker as rw
    w = watcher[0]
    seen = {}

    def fake_spawn(work_dir, **kw):
        seen["watched"] = Path(work_dir).name in w._extra
        return {"ev": "done", "output": str(Path(work_dir) / "output.mp4")}
    monkeypatch.setattr(rw, "spawn", fake_spawn)
    out = rw.run_job(job, {})
    assert out.endswith("output.mp4")
    assert seen["watched"] is True
    assert job.name not in w._extra


def test_server_run_engine_watches_job():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_engine")
    body = ast.get_source_segment(src, fn)
    assert "watch_job" in body and "unwatch_job" in body


# ───────────── Cờ instance nhất quán ─────────────

def _flag_names() -> set[str]:
    from core.instance import load
    return set(load("v6").flags())


@pytest.mark.parametrize("name", ["v6", "test", "prod"])
def test_instance_json_declares_every_flag(name):
    """Mọi cờ trong Instance.flags() phải GHI RÕ trong instances/<tên>.json (không dựa vào mặc định ngầm)."""
    data = json.loads((ROOT / "instances" / f"{name}.json").read_text(encoding="utf-8"))
    missing = _flag_names() - set(data)
    assert not missing, f"{name}.json thiếu cờ {sorted(missing)}"


@pytest.mark.parametrize("name", ["v6", "test"])
def test_non_prod_instances_external_flags_off(name):
    from core.instance import load
    fl = load(name).flags()
    for k in ("agent_brain", "agent_watch", "telegram", "auto_publish", "hg_merge_resume", "title_translate",
              "queue_autostart", "feeder"):
        assert fl[k] is False, f"{name}: {k} phải tắt"
    assert load(name).port not in (8000, 8083, 8801, 8802)
    assert load(name).sidecar_port not in (8000, 8083, 8801, 8802)


def test_test_instance_for_verify_all():
    from core.instance import load
    t = load("test")
    assert t.port == 8899
    assert str(t.work_root).replace("\\", "/").endswith("scratch/test_work")


def test_server_instance_flags_subset_and_documented():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    used = set(re.findall(r"_instance_flag\(\s*[\"']([a-z_]+)[\"']", src))
    names = _flag_names()
    assert used and used <= names, f"server dùng cờ không có trong core.instance: {sorted(used - names)}"
    doc = src[src.index("def _instance_flag"):][:1200]
    for n in used:
        assert n in doc, f"docstring _instance_flag thiếu {n}"


def test_desktop_reads_port_from_instance_json():
    rs = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    assert 'join("instances")' in rs and '"--instance"' in rs
    assert "8801" not in rs and "8802" not in rs


# ───────────── Không còn đường dẫn cây V2/V4/V5 trong code chạy ─────────────

_RUN_DIRS = ("app", "core", "state", "events", "pipeline", "runtime", "workers", "services", "sidecar",
             "http_routes", "engines", "agents")
_PAT = re.compile(r"[A-Za-z]:(?:\\\\|\\|/)+Pineline(?:V2|V4|V5)", re.I)
# Còn lại có lý do (chỉ ĐỌC / dò ứng viên, có env/TEMP instance đứng trước) — ghi trong docs/V6_HANDOFF.md.
_ALLOW = {
    "engines/asr/firered_asr.py",          # docstring ví dụ FIRERED_ROOT
    "agents/hongguo_license_ensure.py",    # dò HgLicActivate.exe (ứng viên, sau env HG_LIC_ACTIVATE_EXE)
    "agents/hongguo_rpc.py",               # dò thư mục player cũ (đọc) + TEMP thiếu (apply_env luôn đặt TEMP)
    "agents/agent_quality.py",             # docstring nguồn gốc
}


def test_no_v2_tree_paths_in_running_code():
    bad = []
    files = [ROOT / "server.py", ROOT / "restart_server.py"]
    for d in _RUN_DIRS:
        files += list((ROOT / d).rglob("*.py"))
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        if "__pycache__" in rel or rel in _ALLOW:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _PAT.search(line):
                bad.append(f"{rel}:{i}: {line.strip()[:100]}")
    assert not bad, "\n".join(bad)


def test_uploader_settings_defaults_follow_instance():
    src = (ROOT / "http_routes" / "routes_uploader_settings.py").read_text(encoding="utf-8")
    assert "PinelineV5_work" not in src and "PinelineV5_tmp" not in src
    assert 'os.getenv("WORK_DIR")' in src


# ───────────── B11: code chết ─────────────

def test_b11_dead_mux_module_gone_and_not_resynced():
    assert not (ROOT / "engines" / "mux.py").exists(), "engines/mux.py là code chết (B11) — đã chuyển _to_delete"
    sync = (ROOT / "scripts" / "sync_from_v2.py").read_text(encoding="utf-8")
    assert '"mux"' in sync
    for p in list(ROOT.glob("*.py")) + [q for d in _RUN_DIRS for q in (ROOT / d).rglob("*.py")]:
        s = p.read_text(encoding="utf-8", errors="replace")
        assert not re.search(r"^\s*(from engines(\.mux| import mux)|import engines\.mux)", s, re.M), p


def test_b11_server_no_unused_run_asr_import():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert not re.search(r"^from engines\.asr\.asr import run_asr", src, re.M)
    assert not re.search(r"(?<![\w.])run_asr\(", src)
