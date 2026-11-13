"""review2 F4/F5 — mỗi lỗi một test (chạy được ở cloud: không fastapi/ffmpeg/GPU, không mạng).

Xem docs/reviews/2026-11-11-review-f4-f5.md.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INST_ENV = ("PINELINE_BASE", "PINELINE_PORT", "PINELINE_INSTANCE", "AUTOPILOT_PORT", "AUTOPILOT_URL", "PORT",
            "PINELINE_FLAG_TELEGRAM", "PINELINE_FLAG_FEEDER", "AGENT_TELEGRAM", "WORK_DIR")


@pytest.fixture
def clean_env(monkeypatch):
    for k in INST_ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _restore_env(snap: dict) -> None:
    os.environ.clear()
    os.environ.update(snap)


# ───────────── R2-1: UI HTML cũ gọi thẳng sidecar/server V2 (:8802/:8801) ─────────────

FOREIGN_URL = re.compile(r"(?:127\.0\.0\.1|localhost):880[12]\b")


def test_localize_html_rewrites_v2_ports(clean_env):
    from core.instance import localize_html
    src = ("fetch('http://127.0.0.1:8802/config');const AP='http://127.0.0.1:8802';"
           "<a href=\"http://127.0.0.1:8801/hongguo\">x</a> http://localhost:8802/stop")
    out = localize_html(src, server="")
    assert not FOREIGN_URL.search(out)
    assert "http://127.0.0.1:8816/config" in out and "AP='http://127.0.0.1:8816'" in out
    assert 'href="/hongguo"' in out
    out2 = localize_html(src, server="http://127.0.0.1:8806", sidecar="")
    assert "fetch('/config')" in out2 and 'href="http://127.0.0.1:8806/hongguo"' in out2


def test_served_html_has_no_v2_urls(clean_env):
    """Mọi file HTML route UI phục vụ (gốc cây + static/ui) sau localize không còn URL :8801/:8802."""
    from core.instance import localize_html
    files = [p for p in list(ROOT.glob("*.html")) + list((ROOT / "static" / "ui").glob("*.html"))]
    assert files
    left = []
    for p in files:
        out = localize_html(p.read_text(encoding="utf-8", errors="replace"), server="")
        left += [f"{p.name}: {m.group(0)}" for m in FOREIGN_URL.finditer(out)]
    assert left == []


def test_ui_routes_use_localize():
    ui = (ROOT / "http_routes" / "routes_ui.py").read_text(encoding="utf-8")
    assert ui.count("_localize(") >= 3
    films = (ROOT / "http_routes" / "routes_films.py").read_text(encoding="utf-8")
    assert "localize_html(" in films
    tc = (ROOT / "agents" / "agent_tele_cmd.py").read_text(encoding="utf-8")
    assert "localize_html(html, server=BASE" in tc


def test_routes_ui_html_file_localized(clean_env):
    pytest.importorskip("fastapi")
    from http_routes import routes_ui
    body = routes_ui._html_file("index3.html").body.decode("utf-8")
    assert not FOREIGN_URL.search(body)


# ───────────── R2-2: restart_server / migrate_work nhắm nhầm tiến trình cây khác ─────────────

def test_server_cmd_matches_tokens(clean_env):
    from core.instance import server_cmd_matches as m
    assert m(["python", "-X", "utf8", "-m", "app", "--instance", "v6"], port=8806)
    assert m(["python.exe", "-m", "app", "--instance", "v6"], port=8806, instance="v6")
    assert not m(["python.exe", "-m", "app", "--instance", "v6"], port=8806, instance="test")
    assert m(["python", "server.py", "--host", "127.0.0.1", "--port", "8806"], port=8806)
    # server V2 production — không bao giờ khớp cổng V6
    assert not m(["python", "server.py", "--host", "127.0.0.1", "--port", "8801"], port=8806)
    assert not m(["python", "Z:\\PinelineV2\\server.py", "--port", "8801"], port=8806)
    assert not m(["python", "server.py", "--port", "88061"], port=8806)       # chuỗi con
    assert not m(["python", "agents/agent_tele_cmd.py", "--instance", "v6"], port=8806)
    assert not m(["python", "server.py"], port=8806)                         # không cổng → không đoán


def _sleeper(cwd: Path, tag: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time,sys; time.sleep(60)", tag], cwd=str(cwd))


def test_instance_procs_only_this_tree(tmp_path, clean_env):
    pytest.importorskip("psutil")
    import tools.migrate_work as mw
    tag = f"review2_tree_{time.time_ns()}"
    mine = _sleeper(ROOT, tag)
    other = _sleeper(tmp_path, tag)                        # "cây khác" (vd Z:\PinelineV2) cùng tên script
    try:
        t0 = time.time()
        while time.time() - t0 < 10 and len(mw.procs(tag)) < 2:
            time.sleep(0.1)
        assert {p.pid for p in mw.procs(tag)} >= {mine.pid, other.pid}
        assert [p.pid for p in mw.instance_procs(tag)] == [mine.pid]
    finally:
        for p in (mine, other):
            p.kill()
            p.wait(timeout=10)


def test_same_root_prefix_not_sibling_tree(tmp_path, clean_env):
    """Cmdline chứa '<ROOT>_codebak/...' (cây anh em) không được coi là cùng cây."""
    pytest.importorskip("psutil")
    import tools.migrate_work as mw

    class P:
        def __init__(self, cwd, cl):
            self._cwd, self._cl = cwd, cl

        def cwd(self):
            return self._cwd

        def cmdline(self):
            return self._cl

    assert mw.same_root(P(str(ROOT), []))
    assert not mw.same_root(P(str(tmp_path), ["python", str(ROOT) + "_codebak/agents/agent_tele_cmd.py"]))
    assert mw.same_root(P(str(tmp_path), ["python", str(ROOT / "agents" / "agent_tele_cmd.py")]))


def test_ops_telegram_off_for_v6(clean_env):
    pytest.importorskip("psutil")
    import tools.migrate_work as mw
    assert mw.telegram_allowed() is False                  # v6.json telegram=false
    clean_env.setenv("PINELINE_INSTANCE", "prod")
    assert mw.telegram_allowed() is True
    clean_env.setenv("PINELINE_FLAG_TELEGRAM", "0")
    assert mw.telegram_allowed() is False


def _restart_probe(env_extra: dict, argv: list[str]) -> dict:
    code = textwrap.dedent(f"""
        import json, os, sys
        sys.argv = ['restart_server.py'] + {argv!r}
        sys.path.insert(0, {str(ROOT)!r})
        import restart_server as r
        print('PROBE' + json.dumps({{'port': r.PORT, 'inst': r.INST.name, 'sidecar': r.SIDECAR,
              'tg': os.environ.get('AGENT_TELEGRAM'), 'guard_prod': r.guard_ports(r._load_instance('prod')),
              'guard_prod_allow': r.guard_ports(r._load_instance('prod'), True), 'guard': r.guard_ports(r.INST)}}))
    """)
    env = {k: v for k, v in os.environ.items() if k not in INST_ENV}
    env.update(env_extra)
    # 11-12 agentJ: restart_server in UTF-8 (reconfigure stdout; «[restart] bỏ qua env PORT…») → Windows
    # text=True mặc định cp1252 → UnicodeDecodeError (0x8f) → stdout None. Giải mã UTF-8 tường minh.
    env["PYTHONIOENCODING"] = "utf-8"
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("PROBE")]
    if not line:
        pytest.skip(f"restart_server không import được ở môi trường này: {out.stderr[-400:]}")
    return json.loads(line[0][5:])


def test_restart_server_ignores_stray_port_env():
    pytest.importorskip("psutil")
    pytest.importorskip("requests")
    d = _restart_probe({"PORT": "8801"}, [])               # shell còn PORT=8801 của V2
    assert d["port"] == "8806" and d["inst"] == "v6" and d["sidecar"].endswith(":8816")
    assert d["tg"] == "0"
    assert d["guard"] == "" and "từ chối" in d["guard_prod"] and d["guard_prod_allow"] == ""
    d2 = _restart_probe({}, ["--instance", "test"])
    assert d2["port"] == "8899" and d2["inst"] == "test"


def test_restart_server_static_safety():
    src = (ROOT / "restart_server.py").read_text(encoding="utf-8")
    assert '"run_v5.bat"' not in src and '"run_tele_cmd.bat"' not in src
    assert 'mw.procs("agent_tele_cmd.py")' not in src and "SERVER_CMDLINE" not in src
    assert "guard_ports(INST" in src and "mw.telegram_allowed()" in src
    mwsrc = (ROOT / "tools" / "migrate_work.py").read_text(encoding="utf-8")
    assert 'start_bat("run_v2.bat")' not in mwsrc and 'procs("agent_tele_cmd.py"):' not in mwsrc


# ───────────── R2-3: Dừng render chỉ giết python worker, cháu (ffmpeg/demucs) sống + giữ pipe ─────────────

WORKER_WITH_GRANDCHILD = '''
import json, subprocess, sys, time
from pathlib import Path
wd = Path(sys.argv[1])
print(json.dumps({"ev": "start", "work_dir": str(wd)}), flush=True)
# cháu KẾ THỪA stdout (như subprocess.run không chuyển hướng) — giữ đầu ghi pipe
g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
(wd / "grandchild.pid").write_text(str(g.pid))
time.sleep(60)
'''


def _alive(pid: int) -> bool:
    import psutil
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def test_stop_kills_grandchildren_and_returns_fast(tmp_path):
    pytest.importorskip("psutil")
    from workers import render_worker as rw
    mod_dir = tmp_path / "mods"
    mod_dir.mkdir()
    (mod_dir / "r2_fake_worker.py").write_text(WORKER_WITH_GRANDCHILD, encoding="utf-8")
    wd = tmp_path / "job_r2"
    wd.mkdir()
    env = dict(os.environ, PYTHONPATH=str(mod_dir))
    t0 = time.time()
    res = rw.spawn(wd, module="r2_fake_worker", env=env, poll_sec=0.1, stop_grace=1.0,
                   is_stopped=lambda: (wd / "grandchild.pid").exists())
    dt = time.time() - t0
    gpid = int((wd / "grandchild.pid").read_text())
    try:
        assert res.get("stopped") is True
        assert dt < 20, f"spawn kẹt {dt:.1f}s (cháu giữ pipe)"
        t1 = time.time()
        while _alive(gpid) and time.time() - t1 < 5:
            time.sleep(0.1)
        assert not _alive(gpid), "cháu (ffmpeg/demucs) còn sống sau Dừng"
    finally:
        try:
            os.kill(gpid, 9)
        except OSError:
            pass


def test_spawn_passes_absolute_work_dir(tmp_path, monkeypatch):
    from workers import render_worker as rw
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel_job").mkdir()
    seen = []
    res = rw.spawn(Path("rel_job"), opts={"dry_run": True}, on_event=seen.append)
    assert res["ev"] == "done"
    start = [e for e in seen if e["ev"] == "start"][0]
    assert Path(start["work_dir"]).is_absolute()
    assert Path(start["work_dir"]) == (tmp_path / "rel_job").resolve()


def test_server_stop_and_render_use_tree_kill():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    i = src.index("def _stop_job(")
    body = src[i:i + 3000]
    assert "kill_tree" in body
    j = src.index("def _render_subprocess(")
    assert '_kill_job_engine_procs(jid, "render_worker")' in src[j:j + 1500]


# ───────────── R2-4: sidecar chạy trần trong cây V6 = kiểu V5 (poll bot Telegram của V2) ─────────────

def test_sidecar_without_instance_defaults_to_v6(clean_env):
    try:
        import agents.agent_tele_cmd as tc
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_tele_cmd không import được: {e}")
    snap = dict(os.environ)
    try:
        tc._apply_instance([])
        assert os.environ.get("PINELINE_FLAG_TELEGRAM") == "0"
        assert not tc._telegram_allowed() and not tc.sidecar_owns_background()
        assert tc.CTRL_PORT == 8816 and tc.BASE == "http://127.0.0.1:8806"
    finally:
        _restore_env(snap)
        tc.BASE, tc.CTRL_PORT = tc._server_base(), tc._sidecar_port()


# ───────────── R2-5: services Auto-pilot gửi tin force=True (sidecar cũ force=False) ─────────────

def test_background_autopilot_notify_not_forced(monkeypatch):
    from services import autopilot, background
    calls = []
    monkeypatch.setattr(background, "_notify", lambda t, force=True: calls.append((t, force)))
    monkeypatch.setattr(autopilot, "autopilot_loop", lambda stop, base=None, notify=None: notify("a"))
    monkeypatch.setattr(autopilot, "config_watch_loop", lambda stop, base=None, notify=None: notify("c"))
    t = background._targets("http://x")
    t["autopilot"]()
    t["config-live"]()
    assert calls == [("a", False), ("c", True)]


# ───────────── R2-6: run-watch không tìm được server V6 (`-m app` không có số cổng) ─────────────

def test_feeder_server_cpu_uses_token_match():
    src = (ROOT / "agents" / "agent_feeder.py").read_text(encoding="utf-8")
    i = src.index("def _server_tree_cpu(")
    body = src[i:i + 1500]
    assert "server_cmd_matches(" in body and "_srv_port in cl" not in body
