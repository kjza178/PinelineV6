"""F5 — render chạy tiến trình con (workers/render_worker.py), không importlib.reload ở đường render.

Chạy được ở cloud: render giả (tests/fake_render.py) chọn qua env PINELINE_RENDER_IMPL; không ffmpeg/GPU/fastapi.
"""
from __future__ import annotations

import ast
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from workers import render_worker as rw

ROOT = Path(__file__).resolve().parents[1]


def _mux(wd: Path) -> dict:
    return (json.loads((wd / "manifest.json").read_text(encoding="utf-8")).get("stages") or {}).get("mux") or {}


@pytest.fixture
def fake(monkeypatch):
    def use(name: str):
        monkeypatch.setenv("PINELINE_RENDER_IMPL", f"tests.fake_render:{name}")
    return use


# ───────────── worker + spawn ─────────────

def test_worker_progress_done_and_state_written_by_child(job, fake):
    fake("ok")
    evs, lines = [], []
    last = rw.spawn(job, opts={"audio_mode": "voice_bgm", "zoom": 1.1}, on_event=evs.append, on_line=lines.append)
    assert last["ev"] == "done" and last["output"].endswith("output.mp4")
    assert [e["ev"] for e in evs][0] == "start"
    assert [e["percent"] for e in evs if e["ev"] == "progress"] == [25, 75]
    assert any("ffmpeg giả" in ln for ln in lines)              # log thường → on_line, không lẫn sự kiện
    st = _mux(job)                                               # tiến trình CON ghi manifest qua state
    assert st["status"] == "done" and st["progress"] == 100
    assert json.loads((job / "kw.json").read_text(encoding="utf-8")) == {"audio_mode": "voice_bgm", "zoom": 1.1}


def test_worker_error_message(job, fake):
    fake("boom")
    last = rw.spawn(job, opts={})
    assert last["ev"] == "error" and last["etype"] == "RuntimeError" and "track giọng Việt" in last["error"]


def test_worker_crash_without_report(job, fake):
    fake("crash")
    last = rw.spawn(job, opts={})
    assert last["ev"] == "error" and "mã 3" in last["error"]


def test_worker_bad_args():
    assert rw.main(["render_worker"]) == 2


def test_spawn_stop_terminates_child(job, fake):
    fake("slow")
    flag = threading.Event()
    threading.Timer(1.0, flag.set).start()
    t0 = time.time()
    last = rw.spawn(job, opts={}, is_stopped=flag.is_set, poll_sec=0.1, stop_grace=2)
    assert last.get("stopped") is True and last["ev"] == "error"
    assert time.time() - t0 < 15
    import os
    pid = int((job / "slow.pid").read_text())
    if os.name != "nt":
        with pytest.raises(OSError):
            os.kill(pid, 0)                                       # tiến trình con đã chết


def test_new_code_takes_effect_next_job(tmp_path, monkeypatch):
    """Sửa code render → job KẾ dùng code mới (không reload, không restart tiến trình server)."""
    mod = tmp_path / "fake_render_live.py"
    monkeypatch.setenv("PINELINE_RENDER_IMPL", "fake_render_live:run")
    # PYTHONDONTWRITEBYTECODE: 2 lần ghi trong cùng 1 giây, cùng kích thước → .pyc cũ (mtime giây) sẽ bị dùng
    env = dict(__import__("os").environ, PYTHONPATH=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    wd = tmp_path / "j"
    wd.mkdir()
    for ver in ("v1", "v2"):
        mod.write_text(f"def run(work_dir, **kw):\n    return '{ver}'\n", encoding="utf-8")
        last = rw.spawn(wd, opts={}, env=env)
        assert last == {"ev": "done", "output": ver}


# ───────────── phía server: run_job (cái server._render_subprocess gọi) ─────────────

class _P:
    def __init__(self):
        self.pid, self.killed = 1, False

    def poll(self):
        return None

    def kill(self):
        self.killed = True


def test_run_job_registers_proc_and_cleans_up(job, fake):
    fake("ok")
    procs = {job.name: _P()}                                     # proc cũ cùng job còn sống → bị kill
    old = procs[job.name]
    seen = {}
    out = rw.run_job(job, {"force": True}, procs=procs,
                     on_line=lambda ln: seen.setdefault("pid", procs.get(job.name)))
    assert out.endswith("output.mp4")
    assert old.killed and seen["pid"] is not old                # đã đăng ký proc mới trong lúc chạy
    assert job.name not in procs                                 # gỡ khi xong


def test_run_job_progress_event_written(job, monkeypatch):
    monkeypatch.delenv("PINELINE_RENDER_IMPL", raising=False)
    last = rw.run_job(job, {"dry_run": True})                    # dry-run chỉ phát progress, không tự ghi
    assert last.endswith("output.mp4")
    st = _mux(job)
    assert st["progress"] == 99 and st["cur_text"] == "dry-run 3/3"


def test_run_job_error_marks_failed(job, fake):
    fake("crash")                                                # worker chết, không kịp mark_failed
    with pytest.raises(RuntimeError, match="mã 3"):
        rw.run_job(job, {})
    st = _mux(job)
    assert st["status"] == "failed" and "mã 3" in st["error"]


def test_run_job_stop(job, fake):
    fake("slow")
    procs: dict = {}
    flag = threading.Event()
    threading.Timer(0.8, flag.set).start()
    with pytest.raises(rw.RenderStopped):
        rw.run_job(job, {}, procs=procs, is_stopped=flag.is_set, poll_sec=0.1, stop_grace=2)
    assert procs == {}
    assert _mux(job).get("status") != "failed"                   # Dừng ≠ lỗi


def test_run_job_already_stopped_does_not_spawn(job, fake):
    fake("ok")
    with pytest.raises(rw.RenderStopped):
        rw.run_job(job, {}, is_stopped=lambda: True)
    assert not (job / "output.mp4").exists()


def test_run_job_skip_returns_none(job, fake):
    fake("skip")
    assert rw.run_job(job, {}) is None


# ───────────── tĩnh: 0 importlib.reload ở đường render ─────────────

def _func_src(path: Path, name: str) -> str:
    src = path.read_text(encoding="utf-8")
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n) or ""
    raise AssertionError(f"không thấy {name} trong {path}")


def test_no_reload_in_server_render_path():
    srv = ROOT / "server.py"
    for fn in ("_do_render", "_render_subprocess"):
        code = "\n".join(ln for ln in _func_src(srv, fn).splitlines() if not ln.lstrip().startswith("#"))
        body = code.split('"""', 2)[-1]                          # bỏ docstring
        assert "importlib" not in body and ".reload(" not in body, fn
    assert '_instance_flag("render_subprocess", False)' in _func_src(srv, "_do_render")


def test_no_reload_in_worker():
    src = (ROOT / "workers" / "render_worker.py").read_text(encoding="utf-8")
    assert ".reload(" not in src


def test_render_job_reload_only_in_legacy_branch():
    """engines/render_job: importlib.reload CHỈ nằm dưới `if hot_reload:` (đường in-process cũ, cờ tắt)."""
    path = ROOT / "engines" / "render_job.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    guarded = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "hot_reload":
            for c in ast.walk(n):
                guarded.add(id(c))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "reload"]
    assert calls and all(id(c) in guarded for c in calls)
    # worker gọi do_render với hot_reload mặc định False
    assert "hot_reload: bool = False" in path.read_text(encoding="utf-8")


def test_render_job_signature_matches_server():
    """Tham số do_render ≡ _do_render (cùng tên + mặc định) → worker nhận đúng kwargs từ job_opts.render_kwargs."""
    def params(path, name):
        src = path.read_text(encoding="utf-8")
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.FunctionDef) and n.name == name:
                a = n.args
                names = [x.arg for x in a.args]
                defs = [ast.unparse(d) for d in a.defaults]
                return dict(zip(names[len(names) - len(defs):], defs)), names
        raise AssertionError(name)
    d_srv, n_srv = params(ROOT / "server.py", "_do_render")
    d_job, n_job = params(ROOT / "engines" / "render_job.py", "do_render")
    assert n_srv == n_job and d_srv == d_job


def test_render_job_no_direct_persist():
    src = (ROOT / "engines" / "render_job.py").read_text(encoding="utf-8")
    assert "js.persist(" not in src and ".save()" not in src


def test_instance_flag_render_subprocess():
    from core.instance import load
    assert load("v6").flags()["render_subprocess"] is True
    assert load("test").flags()["render_subprocess"] is True
    assert load("prod").flags()["render_subprocess"] is False   # prod: in-process cho tới khi so job thật


def test_render_worker_counted_as_engine_for_stop():
    assert '"render_worker",' in (ROOT / "server.py").read_text(encoding="utf-8")   # _HEAVY_ENGINE_SCRIPTS


def test_python_exe_used(job, fake):
    fake("skip")
    assert rw.spawn(job, opts={}, python=sys.executable)["ev"] == "done"
