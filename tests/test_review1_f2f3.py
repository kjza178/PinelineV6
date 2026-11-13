"""Review độc lập F2+F3 (2026-11-11, review1) — test cho từng lỗi đã sửa. Không cần fastapi.

R1  dịch tiêu đề (LLM :8083 dùng chung V2) không có cờ instance → V6 khởi động là gọi LLM.
R2  hàng gộp Hongguo: state ở <ổ>/work/logs (chung V2) + GET /api/hg/merge-queue tự chạy tiếp, lách cờ.
R3  hàng chờ còn job lúc khởi động → tự chạy (GPU) ở V6/test.
R4  khoá 1 backend chỉ theo cổng → 2 backend khác cổng cùng work kill engine / reset stage của nhau.
R5  live_sync.js: EventSource CLOSED không bao giờ đủ 3 lỗi; progress dồn /api/jobs ~3 lần/s;
    tiến độ ASR hybrid (file, không event) chỉ cập nhật 30 s/lần; tab ẩn vẫn gọi API.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def _funcs(path: Path, names: set[str]) -> dict[str, str]:
    src = path.read_text(encoding="utf-8")
    out = {}
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names:
            out[n.name] = ast.get_source_segment(src, n)
    assert set(out) == names, set(names) - set(out)
    return out


def _server_ns(**extra) -> dict:
    """Namespace có _instance_flag thật (chép từ server.py) để exec hàm server không cần import server."""
    ns: dict = {"os": os, "Path": Path, "json": json}
    exec(_funcs(ROOT / "server.py", {"_instance_flag"})["_instance_flag"], ns)
    ns.update(extra)
    return ns


# ───────────── R1: dịch tiêu đề ─────────────

class _Rec:
    def __init__(self):
        self.calls = []

    def submit(self, *a):
        self.calls.append(a)


@pytest.mark.parametrize("flag,expect", [("0", 0), ("1", 1), (None, 1)])
def test_r1_title_translate_respects_flag(monkeypatch, flag, expect):
    if flag is None:
        monkeypatch.delenv("PINELINE_FLAG_TITLE_TRANSLATE", raising=False)   # python server.py kiểu V5
    else:
        monkeypatch.setenv("PINELINE_FLAG_TITLE_TRANSLATE", flag)
    rec = _Rec()
    ns = _server_ns(_has_cjk=lambda t: True, _title_pending=set(), _title_tried={}, _TITLE_MAX_RETRY=3,
                    _PROCS={}, _misc=rec, _translate_title=lambda j: None)
    exec(_funcs(ROOT / "server.py", {"_maybe_translate_title"})["_maybe_translate_title"], ns)
    ns["_maybe_translate_title"]("job1", "标题")
    assert len(rec.calls) == expect


def test_r1_title_startup_hook_off_when_flag_off(monkeypatch):
    monkeypatch.setenv("PINELINE_FLAG_TITLE_TRANSLATE", "0")

    class _NoThread:
        def Thread(self, *a, **k):  # noqa: N802
            raise AssertionError("không được tạo luồng quét tiêu đề khi cờ tắt")
    ns = _server_ns(_UNDER_PYTEST=False, threading=_NoThread(), WORK_ROOT=Path("/nonexistent"),
                    _title_write_hook=lambda *a: None)
    exec(_funcs(ROOT / "server.py", {"_start_title_translate"})["_start_title_translate"], ns)
    import core.manifest as cm
    before = list(cm._write_hooks)
    ns["_start_title_translate"]()
    assert cm._write_hooks == before          # không đăng ký write hook


def test_r1_r3_bundled_instances_default_off():
    from core.instance import load
    for name in ("v6", "test"):
        f = load(name).flags()
        assert f["title_translate"] is False and f["queue_autostart"] is False, name
    p = load("prod").flags()
    assert p["title_translate"] is True and p["queue_autostart"] is True


def test_r1_r3_apply_env_sets_new_flags(tmp_path, monkeypatch):
    from core.instance import load
    (tmp_path / "x.json").write_text(json.dumps({"port": 9999, "work_root": str(tmp_path / "w"),
                                                 "temp": str(tmp_path / "t")}), encoding="utf-8")
    keys = [k for k in os.environ if k.startswith("PINELINE_") or k in (
        "WORK_DIR", "TEMP", "TMP", "PORT", "AUTOPILOT_PORT", "AUTOPILOT_URL", "AGENT_BRAIN", "AGENT_WATCH",
        "AGENT_TELEGRAM")]
    saved = {k: os.environ[k] for k in keys}
    try:
        load("x", directory=tmp_path).apply_env()
        assert os.environ["PINELINE_FLAG_TITLE_TRANSLATE"] == "0"
        assert os.environ["PINELINE_FLAG_QUEUE_AUTOSTART"] == "0"
    finally:
        for k in [k for k in os.environ if k.startswith("PINELINE_") or k in (
                "WORK_DIR", "PORT", "AUTOPILOT_PORT", "AUTOPILOT_URL", "AGENT_BRAIN", "AGENT_WATCH",
                "AGENT_TELEGRAM")]:
            os.environ.pop(k, None)
        os.environ.update(saved)


# ───────────── R3: hàng chờ không tự chạy ─────────────

@pytest.mark.parametrize("flag,expect", [("0", 0), ("1", 1), (None, 1)])
def test_r3_queue_autostart_flag(monkeypatch, flag, expect):
    if flag is None:
        monkeypatch.delenv("PINELINE_FLAG_QUEUE_AUTOSTART", raising=False)
    else:
        monkeypatch.setenv("PINELINE_FLAG_QUEUE_AUTOSTART", flag)
    started = []
    jq = types.SimpleNamespace(queue_state=lambda: {"queue": ["a"]}, _ensure_worker=lambda: started.append(1))
    ns = _server_ns(_UNDER_PYTEST=False, job_queue=jq)
    exec(_funcs(ROOT / "server.py", {"_start_queue_worker"})["_start_queue_worker"], ns)
    ns["_start_queue_worker"]()
    assert len(started) == expect


# ───────────── R2: hàng gộp Hongguo ─────────────

def test_r2_hg_state_paths_follow_work_dir(tmp_path):
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import agents.hongguo_bridge as h; "
            "print(h._MERGE_STATE_PATH); print(h._TITLE_CACHE_PATH)")
    env = dict(os.environ, WORK_DIR=str(tmp_path / "w"))
    r = subprocess.run([sys.executable, "-c", code, str(ROOT)], env=env, capture_output=True, text=True,
                       timeout=60, cwd=str(tmp_path))
    if r.returncode != 0:
        pytest.skip(f"không import được hongguo_bridge ở máy này: {r.stderr[-300:]}")
    merge, title = r.stdout.strip().splitlines()[-2:]
    assert Path(merge) == tmp_path / "w" / "logs" / "hg_merge_state.json"
    assert Path(title) == tmp_path / "w" / "hg_title_cache.json"


def test_r2_hg_merge_queue_get_respects_flag(monkeypatch):
    src = _funcs(ROOT / "agents" / "hongguo_bridge.py", {"merge_queue_state"})["merge_queue_state"]
    import threading
    for flag, expect in (("0", 0), ("1", 1), (None, 1)):
        if flag is None:
            monkeypatch.delenv("PINELINE_FLAG_HG_MERGE_RESUME", raising=False)
        else:
            monkeypatch.setenv("PINELINE_FLAG_HG_MERGE_RESUME", flag)
        called = []
        ns = {"os": os, "DEFAULT_BASE": "http://x", "merge_status": lambda b: {},
              "merge_resume": lambda b: called.append(b), "_merge_lock": threading.Lock(),
              "_merge_q": [], "_merge_cur": None, "_merge_hist": [], "_merge_done": {}, "_merge_batch": {},
              "_merge_base": None, "_merge_thread": None, "_merge_stop": False, "time": __import__("time")}
        exec(src, ns)
        try:
            ns["merge_queue_state"]("http://x")
        except (NameError, KeyError, AttributeError, TypeError):
            pass   # phần dựng kết quả phía sau không quan trọng ở đây
        assert len(called) == expect, flag


# ───────────── R4: khoá theo work root ─────────────

def test_r4_work_lock_same_work_different_spelling(tmp_path):
    from app.__main__ import _single_instance_lock, _work_lock_path
    w = tmp_path / "Work"
    a = _work_lock_path(tmp_path, w)
    b = _work_lock_path(tmp_path, Path(str(w) + os.sep))
    assert a == b
    assert _work_lock_path(tmp_path, tmp_path / "other") != a
    h1 = _single_instance_lock(a)
    assert h1 is not None
    try:
        assert _single_instance_lock(a) is None          # backend thứ 2 cùng work → bị chặn
    finally:
        h1.close()
    h3 = _single_instance_lock(a)                        # nhả khi đóng (tiến trình thoát) → lấy lại được
    assert h3 is not None
    h3.close()


def test_r4_main_takes_work_lock():
    src = (ROOT / "app" / "__main__.py").read_text(encoding="utf-8")
    assert "_LOCK_WORK = _single_instance_lock(_work_lock_path(root, inst.work_root))" in src


# ───────────── R5: live_sync.js ─────────────

HARNESS = r"""
const L = require(process.argv[2]);
const out = {};
let now = 0, seq = 0; const q = [];
const T = {
  setTimeout: (f, ms) => { const id = ++seq; q.push({id, at: now + ms, f}); return id; },
  clearTimeout: (id) => { const i = q.findIndex((x) => x.id === id); if (i >= 0) q.splice(i, 1); },
  setInterval: (f, ms) => { const id = ++seq; q.push({id, at: now + ms, f, every: ms}); return id; },
  clearInterval: (id) => T.clearTimeout(id),
};
async function advance(ms) {
  const end = now + ms;
  for (;;) {
    q.sort((a, b) => a.at - b.at);
    const t = q[0];
    if (!t || t.at > end) break;
    now = t.at;
    if (t.every) t.at += t.every; else q.shift();
    t.f();
    await new Promise((r) => setImmediate(r));
  }
  now = end;
  await new Promise((r) => setImmediate(r));
}
const sources = [];
class FakeES {
  constructor(url) { this.url = url; this.h = {}; this.closed = false; this.readyState = 0; sources.push(this); }
  addEventListener(t, f) { (this.h[t] = this.h[t] || []).push(f); }
  emit(t) { (this.h[t] || []).forEach((f) => f({data: "{}"})); }
  close() { this.closed = true; this.readyState = 2; }
}
class FakeDoc {
  constructor() { this.hidden = false; this.h = []; }
  addEventListener(t, f) { if (t === "visibilitychange") this.h.push(f); }
  removeEventListener(t, f) { this.h = this.h.filter((x) => x !== f); }
  set(h) { this.hidden = h; this.h.forEach((f) => f()); }
}
(async () => {
  // a) progress dồn dập → ≤ 1 refresh / 2 s; job.updated vẫn 300 ms
  let n = 0;
  const doc = new FakeDoc();
  const live = L.startLive({refresh: () => { n++; }, EventSource: FakeES, timers: T, document: doc});
  await advance(0); const s = sources[0]; const n0 = n;
  for (let i = 0; i < 20; i++) { s.emit("stage.progress"); s.emit("job.touched"); await advance(100); }
  out.progress2s = n - n0;                                        // 2000 ms dồn dập → 1 refresh
  const n1 = n; s.emit("job.touched"); await advance(300); s.emit("job.updated"); await advance(300);
  out.fastAfterSlow = n - n1;                                     // job.updated kéo hẹn 2 s về 300 ms
  // b) tab ẩn → không refresh; hiện lại → refresh ngay 1 lần
  doc.set(true); const n2 = n;
  s.emit("job.updated"); await advance(31000); out.hiddenRefresh = n - n2; out.stale = live.stale;
  doc.set(false); await advance(0); out.visibleRefresh = n - n2;
  // c) readyState CLOSED ở lỗi ĐẦU TIÊN → poll ngay (trước: chờ đủ 3 lỗi, không bao giờ tới)
  s.readyState = 2; s.onerror(); out.modeClosed = live.mode;
  // sự kiện muộn của nguồn cũ không làm hỏng nguồn mới
  await advance(60000); out.nSources = sources.length; sources[1].onopen(); out.modeOpen = live.mode;
  s.onerror(); s.onerror(); s.onerror(); out.modeAfterStale = live.mode;
  live.close(); out.docHandlers = doc.h.length;
  // d) busy → dự phòng 5 s; rảnh → 30 s
  let busy = true, m = 0;
  const l2 = L.startLive({refresh: () => { m++; }, EventSource: FakeES, timers: T, busy: () => busy, document: null});
  await advance(0); const m0 = m; await advance(10400); out.busy10s = m - m0;
  busy = false; const m1 = m; await advance(25000); out.idle25s = m - m1;
  l2.close();
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(NODE is None, reason="không có node")
def test_r5_live_sync_review(tmp_path):
    h = tmp_path / "h.js"
    h.write_text(HARNESS, encoding="utf-8")
    r = subprocess.run([NODE, str(h), str(ROOT / "static" / "live_sync.js")], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert o["progress2s"] == 1, o
    assert o["fastAfterSlow"] == 1, o
    assert o["hiddenRefresh"] == 0 and o["stale"] is True, o
    assert o["visibleRefresh"] == 1, o
    assert o["modeClosed"] == "poll", o
    assert o["nSources"] == 2 and o["modeOpen"] == "sse" and o["modeAfterStale"] == "sse", o
    assert o["docHandlers"] == 0, o
    assert o["busy10s"] == 2, o
    assert o["idle25s"] == 0, o


@pytest.mark.parametrize("ui", ("index3.html", "index.html"))
def test_r5_ui_passes_busy(ui):
    s = (ROOT / ui).read_text(encoding="utf-8")
    assert "PinelineLive.startLive({refresh, busy:()=>" in s
