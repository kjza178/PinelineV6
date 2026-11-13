"""kill_with_parent_cmd không được kill tiến trình gọi nó dù nằm trong cây con của tiến trình bị kill."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r"""
import sys, psutil
sys.path.insert(0, {root!r})
import tools.migrate_work as mw
parent = psutil.Process().parent()
mw.kill_with_parent_cmd(parent)
res = "alive" if not parent.is_running() else "parent-alive"
with open({out!r} + ".tmp", "w") as f:
    f.write(res)
import os
os.replace({out!r} + ".tmp", {out!r})
"""


def test_kill_dead_process_is_noop():
    import psutil
    sys.path.insert(0, str(ROOT))
    import tools.migrate_work as mw
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    proc = psutil.Process(p.pid)
    p.wait(timeout=30)
    mw.kill_with_parent_cmd(proc)


def test_kill_wrapper_after_child(tmp_path):
    """Mô phỏng restart: procs() trả cả cmd bọc + python con; kill con trước rồi tới cmd đã thoát."""
    import psutil
    sys.path.insert(0, str(ROOT))
    import tools.migrate_work as mw
    tag = f"restart_guard_{time.time_ns()}"
    w = subprocess.Popen(f'cmd.exe /c {sys.executable} -c "import time; time.sleep(60)" {tag}')
    t0 = time.time()
    while time.time() - t0 < 20:
        found = mw.procs(tag)
        if len(found) >= 2:
            break
        time.sleep(0.2)
    found = sorted(mw.procs(tag), key=lambda x: x.name().lower() == "cmd.exe")
    assert len(found) >= 2
    for p in found:
        mw.kill_with_parent_cmd(p)
    w.wait(timeout=30)
    assert not mw.procs(tag)


def test_kill_skips_self(tmp_path):
    out = tmp_path / "out.txt"
    h = tmp_path / "harness.py"
    h.write_text(HARNESS.format(root=str(ROOT), out=str(out)), encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-c", f"import subprocess,sys; subprocess.run([sys.executable, {str(h)!r}])"])
    p.wait(timeout=60)
    t0 = time.time()
    while not out.exists() and time.time() - t0 < 60:
        time.sleep(0.2)
    assert out.read_text() == "alive"
