"""Chạy kiểm thử review đợt 1–5 (scripts/check_batch*.py) dưới pytest.

Mỗi script tự dựng thư mục tạm / mock, không đụng server, sidecar, hàng đợi thật.
Bỏ qua: pytest -m "not batches"."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "scripts").glob("check_batch*.py"))


@pytest.mark.batches
@pytest.mark.parametrize("script", SCRIPTS, ids=[p.stem for p in SCRIPTS])
def test_review_batch(script):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(script)], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    fails = [ln for ln in r.stdout.splitlines() if "FAIL" in ln and "0 FAIL" not in ln]
    assert r.returncode == 0, "\n".join(fails[-20:]) or (r.stdout + r.stderr)[-3000:]
