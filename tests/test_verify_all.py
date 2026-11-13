"""scripts/verify_all.py — chế độ khô + logic smoke backend (với backend giả tests/fake_backend.py).

Smoke thật (`python -m app --instance test`) chỉ chạy trên Windows qua verify_all.bat.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("verify_all", ROOT / "scripts" / "verify_all.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_bat_exists_crlf_ascii():
    b = (ROOT / "verify_all.bat").read_bytes()
    assert b.isascii(), "bat chỉ nên ASCII (tránh lỗi codepage)"
    assert b"\r\n" in b and b.count(b"\n") == b.count(b"\r\n"), "bat phải CRLF"
    t = b.decode()
    assert "chcp 65001" in t and "PYTHONUTF8=1" in t and "scripts\\verify_all.py" in t


def test_test_instance_ok():
    assert _mod().check_test_instance() == []


def test_dry_run_passes_without_running_anything(capsys):
    m = _mod()
    rc = m.main(["--dry", "--no-log"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "pytest" in out and "tree_audit.py" in out and "TỔNG: PASS" in out
    assert "8801" not in out.replace("8801/:8802", "")   # không lệnh nào nhắm cổng V2


def test_smoke_logic_with_fake_backend():
    m = _mod()
    port = _free_port()
    cmd = [sys.executable, str(ROOT / "tests" / "fake_backend.py"), str(port)]
    st, note, subs = m.step_smoke(m.Log(None), False, backend_cmd=cmd, port=port, wait_sec=30)
    names = {n: s for n, s, _ in subs}
    assert st == "PASS", subs
    for k in ("/api/health", "/api/v6/jobs", "/api/jobs", "/api/events", "/v6/", "/", "/ui3", "tắt backend"):
        assert names.get(k) == "PASS", (k, subs)
    assert not m.port_open(port)                       # đã tắt đúng tiến trình
    assert not (ROOT / "scratch" / "verify_all_backend.pid").exists()


def test_smoke_detects_v2_port_in_old_ui():
    m = _mod()
    port = _free_port()
    cmd = [sys.executable, str(ROOT / "tests" / "fake_backend.py"), str(port), "bad"]
    st, _, subs = m.step_smoke(m.Log(None), False, backend_cmd=cmd, port=port, wait_sec=30)
    assert st == "FAIL"
    assert dict((n, s) for n, s, _ in subs)["/ui3"] == "FAIL"


def test_smoke_refuses_busy_port_without_killing():
    m = _mod()
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        st, note, subs = m.step_smoke(m.Log(None), False, backend_cmd=["false"], port=port, wait_sec=5)
    assert st == "FAIL" and "bận" in note


def test_smoke_refuses_foreign_port():
    m = _mod()
    st, note, _ = m.step_smoke(m.Log(None), False, backend_cmd=["false"], port=8801, wait_sec=1)
    assert st == "FAIL" and "8801" in note


def test_env_strips_other_instance(monkeypatch):
    m = _mod()
    monkeypatch.setenv("PINELINE_PORT", "8801")
    monkeypatch.setenv("WORK_DIR", "Z:/PinelineV2/work")
    e = m._env()
    assert "PINELINE_PORT" not in e and "WORK_DIR" not in e and e["AGENT_BRAIN"] == "0"
