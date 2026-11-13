"""F3 tích hợp — kiểm tĩnh / thuần (chạy được không cần fastapi):
UI cũ hết polling 2 s; GET không ghi; cờ instance; job.touched."""
from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UIS = ("index3.html", "index.html")


def _script(name: str) -> str:
    s = (ROOT / name).read_text(encoding="utf-8")
    return "\n".join(re.findall(r"<script>(.*?)</script>", s, re.S))


@pytest.mark.parametrize("ui", UIS)
def test_old_ui_no_2s_polling(ui):
    js = _script(ui)
    # không còn setInterval(..refresh..,2000) và poll thích ứng 2 s
    for m in re.finditer(r"setInterval\(", js):
        chunk = js[m.start():m.start() + 200]
        assert not ("refresh()" in chunk and "2000" in chunk), chunk
    assert "running?2000:5000" not in js
    assert not re.search(r"setTimeout\(\s*pollLoop\s*,\s*[^)]*2000", js)


@pytest.mark.parametrize("ui", UIS)
def test_old_ui_uses_sse(ui):
    html = (ROOT / ui).read_text(encoding="utf-8")
    assert '<script src="/static/live_sync.js"></script>' in html
    assert "PinelineLive.startLive({refresh" in html   # review1: + busy
    live = (ROOT / "static" / "live_sync.js").read_text(encoding="utf-8")
    assert 'url: "/api/events"' in live and "new ES(o.url)" in live
    for ev in ("snapshot", "job.updated", "job.removed", "stage.progress"):
        assert f'"{ev}"' in live
    assert "debounceMs: 300" in live and "fallbackMs: 30000" in live and "pollMs: 5000" in live


@pytest.mark.parametrize("ui", UIS)
def test_conn_banner_only_on_network_error(ui):
    js = _script(ui)
    body = js[js.index("async function refresh(){"):]
    body = body[:body.index("\n}\n") + 2]
    # lỗi vẽ → console.error, KHÔNG setConn(false)
    assert re.search(r"catch\(e\)\{ console\.error\('refresh: lỗi vẽ', e\); \}", body)
    # mỗi setConn(false) nằm ngay trong catch của fetch (lỗi mạng)
    for m in re.finditer(r"setConn\(false\)", body):
        before = body[max(0, m.start() - 80):m.start()]
        assert "lỗi mạng" in before, before


def _func_src(path: Path, names: set[str]) -> dict[str, str]:
    src = path.read_text(encoding="utf-8")
    out = {}
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names:
            out[n.name] = ast.get_source_segment(src, n)
    return out


WRITE_CALLS = ("js.persist(", "js.mutate(", "js.set_", "js.mark_", ".mark_running(", ".mark_done(",
               ".mark_failed(", "_patch_asr_fields(", "_queue_split_parent_merge_if_ready(",
               "_notify_parent_after_part_done(", "_maybe_translate_title(", ".write_text(", ".update(fn")


def test_legacy_get_job_has_no_side_effects():
    names = {"job_detail", "list_jobs", "read_job", "_apply_hybrid_asr_overlay", "_script_missing"}
    f = _func_src(ROOT / "server.py", names)
    assert set(f) == names
    for name, src in f.items():
        code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
        for w in WRITE_CALLS:
            assert w not in code, f"{name} gọi {w}"
    assert "_sync_asr_labels(work_dir, asr, persist=False)" in f["_apply_hybrid_asr_overlay"]


def test_v6_get_routes_have_no_side_effects():
    src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    f = _func_src(ROOT / "app" / "main.py", {"health", "jobs", "job", "events"})
    assert set(f) == {"health", "jobs", "job", "events"}
    for name, s in f.items():
        for w in WRITE_CALLS + ("job_state.",):
            assert w not in s, f"{name} gọi {w}"
    assert '"/api/v6/jobs"' in src.replace("V6_PREFIX + \"/jobs\"", '"/api/v6/jobs"')
    assert 'app.mount("/v6"' in src


def test_server_background_starts_respect_instance_flags():
    f = _func_src(ROOT / "server.py", {"_start_agent_watch", "_start_agent_brain", "_resume_hg_merge",
                                       "_queue_run_one"})
    assert '_instance_flag("agent_watch")' in f["_start_agent_watch"]
    assert '_instance_flag("agent_brain")' in f["_start_agent_brain"]
    assert '_instance_flag("hg_merge_resume")' in f["_resume_hg_merge"]
    q = f["_queue_run_one"]
    assert q.index('_instance_flag("auto_publish")') < q.index("auto_publisher.enqueue_publish(job_id)")
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert 'WORK_ROOT = Path(os.getenv("WORK_DIR") or "work")' in src
    assert 'os.getenv("PINELINE_ROUTERS")' in src


def test_instance_flags_env(tmp_path, monkeypatch):
    from core.instance import load
    keep = dict(os.environ)
    try:
        (tmp_path / "x.json").write_text(json.dumps({
            "port": 9002, "work_root": str(tmp_path / "w"), "temp": str(tmp_path / "t"),
            "routers": ["films"], "agent_brain": False, "telegram": False, "auto_publish": False}))
        os.environ["AGENT_BRAIN"] = "1"
        os.environ["AGENT_TELEGRAM"] = "1"
        inst = load("x", directory=tmp_path)
        inst.apply_env()
        assert os.environ["PINELINE_FLAG_AGENT_BRAIN"] == "0"
        assert os.environ["PINELINE_FLAG_TELEGRAM"] == "0"
        assert os.environ["PINELINE_FLAG_AUTO_PUBLISH"] == "0"
        assert os.environ["PINELINE_FLAG_HG_MERGE_RESUME"] == "0"
        assert os.environ["AGENT_BRAIN"] == "0" and os.environ["AGENT_TELEGRAM"] == "0"
        assert os.environ["PINELINE_ROUTERS"] == "films"
        assert os.environ["WORK_DIR"] == str(tmp_path / "w")
    finally:
        os.environ.clear()
        os.environ.update(keep)


def test_bundled_instances_flags():
    from core.instance import load
    v6 = load("v6").flags()
    assert v6 == {"agent_brain": False, "agent_watch": False, "telegram": False,
                  "auto_publish": False, "hg_merge_resume": False,
                  "title_translate": False, "queue_autostart": False,   # review1
                  "render_subprocess": True, "feeder": False,            # F5 / F4
                  "preset_switch": False,                                 # 11-11: không restart llama dùng chung
                  "asr_use_demucs": True}                                 # F6 port 11-12: Demucs trước ASR (mặc định bật)
    assert load("prod").flags()["auto_publish"] is True


def test_index_touched_event_when_summary_unchanged():
    from state.index import JobIndex
    got = []
    ix = JobIndex(publish=lambda t, d: got.append((t, d)))
    data = {"stages": {"asr": {"status": "running", "cur_text": "ASR"}}}
    ix.apply("a", data)
    data["stages"]["asr"]["progress"] = 40        # tiến độ đổi, tóm tắt không đổi
    ix.apply("a", data)
    assert [t for t, _ in got] == ["job.updated", "job.touched"]
    assert got[1][1] == {"job_id": "a", "status": "running"}


def test_bus_throttles_job_touched():
    from events.bus import EventBus
    b = EventBus(throttle_sec=10)
    s = b.subscribe()
    for _ in range(20):
        b.publish("job.touched", {"job_id": "a"})
    b.publish("job.touched", {"job_id": "b"})
    got = []
    while (ev := s.get(timeout=0.01)) is not None:
        got.append(ev.data["job_id"])
    assert got == ["a", "b"]


def test_tauri_opens_v6_ui():
    rs = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    assert 'format!("http://127.0.0.1:{port}/v6/")' in rs
    js = (ROOT / "desktop" / "ui" / "app.js").read_text(encoding="utf-8")
    assert 'new EventSource("/api/events")' in js and "setInterval" not in js
