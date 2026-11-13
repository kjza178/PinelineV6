"""F4 — services + sidecar thuần: không cổng cứng, không vòng server→sidecar /cmd, services import nhẹ."""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV_KEYS = ("PINELINE_BASE", "PINELINE_PORT", "PINELINE_INSTANCE", "AUTOPILOT_PORT", "AUTOPILOT_URL", "PORT")


@pytest.fixture
def clean_env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


# ───────────── core.instance: cổng/URL cho agents/sidecar/tools ─────────────

def test_server_base_defaults_to_instance_not_8801(clean_env):
    from core import instance as ci
    assert ci.server_base() == "http://127.0.0.1:8806"
    assert ci.sidecar_port() == 8816 and ci.sidecar_url() == "http://127.0.0.1:8816"
    clean_env.setenv("PINELINE_PORT", "8899")
    assert ci.server_base() == "http://127.0.0.1:8899"
    clean_env.setenv("PINELINE_BASE", "http://127.0.0.1:8123/")
    assert ci.server_base() == "http://127.0.0.1:8123"
    clean_env.setenv("AUTOPILOT_PORT", "8833")
    assert ci.sidecar_port() == 8833 and ci.sidecar_url() == "http://127.0.0.1:8833"
    clean_env.setenv("PINELINE_INSTANCE", "test")
    clean_env.delenv("AUTOPILOT_PORT")
    assert ci.sidecar_port() == 8899 + 10


def test_apply_env_sets_sidecar_and_new_flags(clean_env):
    from core.instance import load
    snap = dict(os.environ)
    try:
        load("v6").apply_env()
        env = dict(os.environ)
    finally:
        os.environ.clear()
        os.environ.update(snap)
    assert env["AUTOPILOT_PORT"] == "8816" and env["PINELINE_BASE"] == "http://127.0.0.1:8806"
    assert env["PINELINE_FLAG_FEEDER"] == "0" and env["PINELINE_FLAG_RENDER_SUBPROCESS"] == "1"
    assert env["PINELINE_FLAG_TELEGRAM"] == "0" and env["AGENT_TELEGRAM"] == "0"


def test_instance_feeder_flag():
    from core.instance import load
    assert load("v6").flags()["feeder"] is False and load("test").flags()["feeder"] is False
    assert load("prod").flags()["feeder"] is True


# ───────────── không cổng cứng trong code chạy ─────────────

CODE_DIRS = ("agents", "core", "http_routes", "app", "services", "sidecar", "workers", "engines", "runtime",
             "state", "events", "pipeline", "tools")
CODE_FILES = ("server.py", "restart_server.py")
# Cổng của cây khác (V2 8801/8802, V4 8804, V5 8805, Pineline 8800) dưới dạng URL hoặc mặc định chuỗi.
PORT_PAT = re.compile(r"""(?:127\.0\.0\.1|localhost):88(?:0[0-5])\b|["']88(?:0[0-5])["']""")
# Ngoại lệ có lý do (ghi trong docs/fixes/2026-11-11-f4-f5.md):
ALLOW = {
    "agents/canva_client.py",   # redirect_uri OAuth phải khớp app Canva đã đăng ký (env CANVA_REDIRECT_URI ghi đè)
}


def _code_lines(p: Path) -> list[tuple[int, str]]:
    src = p.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    skip: set[int] = set()
    for n in ast.walk(tree):          # docstring module/class/hàm = tài liệu, không phải code chạy
        body = getattr(n, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
            skip.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    return [(i, ln) for i, ln in enumerate(src.splitlines(), 1)
            if i not in skip and not ln.lstrip().startswith("#")]


def _iter_code():
    for d in CODE_DIRS:
        base = ROOT / d
        if base.is_dir():
            yield from sorted(base.rglob("*.py"))
    for f in CODE_FILES:
        if (ROOT / f).is_file():
            yield ROOT / f


def test_no_hardcoded_foreign_ports():
    hits = []
    for p in _iter_code():
        rel = p.relative_to(ROOT).as_posix()
        if rel in ALLOW or "__pycache__" in rel:
            continue
        for i, ln in _code_lines(p):
            code = ln.split("  #", 1)[0]
            if PORT_PAT.search(code):
                hits.append(f"{rel}:{i}: {ln.strip()[:120]}")
    assert hits == [], "Cổng cứng của cây khác — dùng core.instance.server_base()/sidecar_url():\n" + "\n".join(hits)


# ───────────── không còn vòng server → sidecar /cmd ─────────────

def test_agent_core_no_sidecar_cmd_loop():
    src = (ROOT / "agents" / "agent_core.py").read_text(encoding="utf-8")
    assert "_sidecar_cmd" not in src and '"/cmd"' not in src and "+ \"/cmd\"" not in src
    assert "services.feeder" in src


def test_sidecar_commands_use_services():
    src = (ROOT / "agents" / "agent_tele_cmd.py").read_text(encoding="utf-8")
    for needle in ("svc_feeder.running_text(BASE)", "svc_feeder.lib_command(rest, BASE)",
                   "svc_feeder.feed_command(rest, BASE)"):
        assert needle in src
    assert "if sidecar_owns_background():" in src and "_telegram_allowed()" in src


# ───────────── services import nhẹ (không fastapi) ─────────────

SERVICES = ("services", "services.feeder", "services.film_db_sync", "services.autopilot",
            "services.publisher", "services.hongguo_flow", "services.background")


def test_services_import_without_fastapi_or_requests():
    code = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('fastapi', 'starlette', 'requests', 'uvicorn', 'torch', 'numpy'):\n"
        "            raise ImportError('blocked ' + name)\n"
        "sys.meta_path.insert(0, _Block())\n"
        f"for m in {SERVICES!r}:\n"
        "    __import__(m)\n"
        "print('OK')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr


# ───────────── services.feeder dùng base của instance ─────────────

@pytest.fixture
def fake_agents(monkeypatch):
    calls = []
    st = {"enabled": False, "request_text": "", "spec": {}}
    fe = types.ModuleType("agents.agent_feeder")
    fe.running_text = lambda base: calls.append(("running", base)) or "R"
    fe.lib_command = lambda base, arg: calls.append(("lib", base, arg)) or ("L", [])
    fe.load_state = lambda: dict(st)
    fe.save_state = lambda s: (calls.append(("save", s["enabled"])), st.update(s))
    fe.describe_spec = lambda spec: "spec"
    fe.preview = lambda base: calls.append(("preview", base)) or "P"
    fe.feed_tick = lambda base, force=False: calls.append(("tick", base, force)) or {"notes": ["a"]}
    fe.normalize_spec = lambda d: {}
    fe.set_request = lambda t: calls.append(("req", t)) or {"spec": {}, "summary": "S"}
    fe.STUCK_MIN = 30.0
    fe.runwatch_tick = lambda base, heal, notify: calls.append(("rw", base, heal))
    ap = types.ModuleType("agents.agent_autopilot")
    ap.load_state = lambda: {"enabled": True}
    monkeypatch.setitem(sys.modules, "agents.agent_feeder", fe)
    monkeypatch.setitem(sys.modules, "agents.agent_autopilot", ap)
    import agents
    monkeypatch.setattr(agents, "agent_feeder", fe, raising=False)
    monkeypatch.setattr(agents, "agent_autopilot", ap, raising=False)
    return calls


def test_services_feeder_commands(clean_env, fake_agents):
    from services import feeder as sf
    clean_env.setenv("PINELINE_PORT", "8806")
    assert sf.running_text() == "R" and fake_agents[-1] == ("running", "http://127.0.0.1:8806")
    assert sf.lib_command("all")[0] == "L" and fake_agents[-1] == ("lib", "http://127.0.0.1:8806", "all")
    assert sf.feed_command("on").startswith("Tự thêm job ON") and ("save", True) in fake_agents
    assert "Thêm ngay" in sf.feed_command("now") and ("tick", "http://127.0.0.1:8806", True) in fake_agents
    assert sf.feed_command("").startswith("Tự thêm job:")
    assert "Hiểu là: S" in sf.feed_command("phim ngắn hongguo") and ("req", "phim ngắn hongguo") in fake_agents
    assert all("8801" not in str(c) for c in fake_agents)


def test_services_loops_run_and_stop(clean_env, fake_agents):
    from services import feeder as sf
    stop = threading.Event()
    notes = []
    t = threading.Thread(target=sf.feeder_loop, args=(stop,), kwargs={"base": "http://x", "notify": notes.append,
                                                                       "tick_sec": 0.05})
    t.start()
    import time
    time.sleep(0.2)
    stop.set()
    t.join(2)
    assert not t.is_alive() and ("tick", "http://x", False) in fake_agents and notes


def test_background_off_by_default(clean_env):
    from services import background as bg
    clean_env.delenv("PINELINE_FLAG_FEEDER", raising=False)
    assert bg.start() == []
    clean_env.setenv("PINELINE_FLAG_FEEDER", "0")
    assert bg.start() == []


def test_background_start_when_flag(clean_env, monkeypatch):
    from services import background as bg
    from services import publisher
    ran = []
    monkeypatch.setattr(bg, "_targets", lambda base: {n: (lambda n=n: ran.append(n)) for n in bg.LOOP_NAMES})
    monkeypatch.setattr(publisher, "start_worker", lambda: ran.append("publisher") or False)
    clean_env.setenv("PINELINE_FLAG_FEEDER", "1")
    names = bg.start(delay=0)
    for t in list(bg._THREADS):
        t.join(2)
    bg.stop()
    assert len(names) == len(bg.LOOP_NAMES) and sorted(ran) == sorted([*bg.LOOP_NAMES, "publisher"])


def test_publisher_respects_flag(clean_env):
    from services import publisher
    clean_env.setenv("PINELINE_FLAG_AUTO_PUBLISH", "0")
    assert publisher.start_worker() is False


def test_server_starts_services_only_with_flag():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert 'if not _instance_flag("feeder", False):' in src and "_svc_bg.start(force=True)" in src


# ───────────── sidecar theo instance ─────────────

def _load_sidecar():
    try:
        import agents.agent_tele_cmd as tc
    except Exception as e:  # noqa: BLE001 — thiếu deps (hongguo/PIL…) ở cloud
        pytest.skip(f"agent_tele_cmd không import được ở môi trường này: {e}")
    return tc


def test_sidecar_port_and_telegram_by_instance(clean_env):
    tc = _load_sidecar()
    for k in ("PINELINE_FLAG_TELEGRAM", "PINELINE_FLAG_FEEDER"):
        clean_env.delenv(k, raising=False)
    assert tc._telegram_allowed() and tc.sidecar_owns_background()          # kiểu V5: như cũ
    clean_env.setenv("PINELINE_FLAG_TELEGRAM", "0")
    clean_env.setenv("PINELINE_FLAG_FEEDER", "0")
    assert not tc._telegram_allowed() and not tc.sidecar_owns_background()
    assert tc.tg_send("x") is False                                          # không gửi khi cờ tắt
    clean_env.setenv("AUTOPILOT_PORT", "8816")
    clean_env.setenv("PINELINE_BASE", "http://127.0.0.1:8806")
    tc._apply_instance([])
    assert tc.CTRL_PORT == 8816 and tc.BASE == "http://127.0.0.1:8806"


def test_run_sidecar_bat_uses_instance():
    bat = (ROOT / "run_sidecar_v6.bat").read_text(encoding="utf-8")
    assert "--instance v6" in bat and "agent_tele_cmd" in bat
