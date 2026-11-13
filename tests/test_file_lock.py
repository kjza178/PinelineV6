"""core.fileio.file_lock: loại trừ thật giữa tiến trình và giữa luồng (bug khoá rò 2026-11-11)."""
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from core import fileio

ROOT = Path(__file__).resolve().parents[1]


def test_processes_mutual_exclusion(tmp_path):
    cnt = tmp_path / "cnt.txt"
    cnt.write_text("0", encoding="utf-8")
    worker = (
        "import sys; sys.path.insert(0, r'%s'); from core import fileio; from pathlib import Path\n"
        "p = Path(r'%s')\n"
        "for _ in range(40):\n"
        "    with fileio.file_lock(p, timeout=30):\n"
        "        n = int(p.read_text()); fileio.atomic_write_text(p, str(n + 1), fsync=False)\n"
    ) % (ROOT, cnt)
    procs = [subprocess.Popen([sys.executable, "-c", worker]) for _ in range(3)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    assert cnt.read_text() == "120"


def test_threads_mutual_exclusion(tmp_path):
    target = tmp_path / "s.json"
    box = {"n": 0, "inside": 0, "max_inside": 0}

    def work():
        for _ in range(25):
            with fileio.file_lock(target, timeout=30):
                box["inside"] += 1
                box["max_inside"] = max(box["max_inside"], box["inside"])
                v = box["n"]
                box["n"] = v + 1
                box["inside"] -= 1
    ts = [threading.Thread(target=work) for _ in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert box["n"] == 250 and box["max_inside"] == 1


def test_leftover_lock_file_is_not_a_lock(tmp_path):
    stale = Path(str(tmp_path / "s.json") + ".lock")
    stale.write_text("12345 0")          # file sót từ bản cũ / tiến trình chết
    with fileio.file_lock(tmp_path / "s.json", timeout=2):
        pass


def test_timeout_when_held(tmp_path):
    held = threading.Event()
    release = threading.Event()

    def holder():
        with fileio.file_lock(tmp_path / "x", timeout=5):
            held.set()
            release.wait(5)
    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    try:
        with pytest.raises(TimeoutError):
            with fileio.file_lock(tmp_path / "x", timeout=0.3):
                pass
    finally:
        release.set()
        t.join()
