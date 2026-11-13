"""F3: static/live_sync.js (thay polling 2 s của index3/index) — chạy logic thật trong Node.
Không có node → skip (Windows không bắt buộc có node)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

HARNESS = r"""
const L = require(process.argv[2]);
const out = {};
// đồng hồ giả
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
// EventSource giả
const sources = [];
class FakeES {
  constructor(url) { this.url = url; this.h = {}; this.closed = false; sources.push(this); }
  addEventListener(t, f) { (this.h[t] = this.h[t] || []).push(f); }
  emit(t) { (this.h[t] || []).forEach((f) => f({data: "{}"})); }
  close() { this.closed = true; }
}

(async () => {
  // 1) coalescer: 10 kick liền → 1 lần; đang chạy + kick → đúng 1 lần nữa, không chồng
  let running = 0, maxPar = 0, calls = 0, release = null;
  const slow = () => { calls++; running++; maxPar = Math.max(maxPar, running);
    return new Promise((r) => { release = () => { running--; r(); }; }); };
  const co = L.makeCoalescer(slow, 300, T);
  for (let i = 0; i < 10; i++) co.kick();
  await advance(299); out.before = calls;
  await advance(1); out.after1 = calls;
  for (let i = 0; i < 5; i++) { co.kick(); await advance(400); }
  out.whileInflight = calls;
  release(); await advance(0); await advance(300);
  out.afterRelease = calls; out.maxPar = maxPar;
  release();

  // 2) startLive: event → refresh gộp; fallback 30 s; lỗi liên tục → poll 5 s; open → hết poll
  let n = 0; const modes = [];
  const live = L.startLive({refresh: () => { n++; }, EventSource: FakeES, timers: T,
                            onMode: (m) => modes.push(m)});
  await advance(0); out.initial = n;                      // tải lần đầu ngay
  out.url = sources[0].url;
  sources[0].emit("job.updated"); sources[0].emit("stage.progress"); sources[0].emit("job.touched");
  await advance(300); out.afterEvents = n;               // 3 event → 1 refresh
  await advance(30000); out.afterFallback = n;           // dự phòng 30 s
  sources[0].onerror(); sources[0].onerror(); out.modeAfter2 = live.mode;
  sources[0].onerror(); out.modeAfter3 = live.mode; out.closed0 = sources[0].closed;
  const k0 = n; await advance(10400); out.pollRefreshes = n - k0;   // 2 lần poll 5 s (+300 ms gộp)
  await advance(60000); out.nSources = sources.length;  // thử nối SSE lại sau 60 s
  sources[1].onopen(); out.modeAfterOpen = live.mode;
  const k1 = n; await advance(12000); out.afterOpen12s = n - k1;    // không còn poll 5 s (≤ 1 = dự phòng 30 s)
  out.modes = modes;
  live.close();
  // 3) không có EventSource → poll 5 s
  let m = 0;
  const l2 = L.startLive({refresh: () => { m++; }, EventSource: null, timers: T});
  await advance(10400); out.noEsMode = l2.mode; out.noEsCalls = m; l2.close();
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(NODE is None, reason="không có node")
def test_live_sync_js(tmp_path):
    h = tmp_path / "h.js"
    h.write_text(HARNESS, encoding="utf-8")
    r = subprocess.run([NODE, str(h), str(ROOT / "static" / "live_sync.js")], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert o["before"] == 0 and o["after1"] == 1
    assert o["whileInflight"] == 1               # không chạy chồng
    assert o["afterRelease"] == 2 and o["maxPar"] == 1
    assert o["initial"] == 1 and o["url"] == "/api/events"
    assert o["afterEvents"] == 2
    assert o["afterFallback"] == 3
    assert o["modeAfter2"] == "sse" and o["modeAfter3"] == "poll" and o["closed0"] is True
    assert o["pollRefreshes"] == 2
    assert o["nSources"] == 2 and o["modeAfterOpen"] == "sse"
    assert o["afterOpen12s"] <= 1
    assert o["modes"] == ["poll", "sse"]
    assert o["noEsMode"] == "poll" and o["noEsCalls"] == 3   # 1 lần đầu + 2 poll
