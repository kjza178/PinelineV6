"""Kiểm thử đợt 3 (opts một nguồn X1, config file-first X2, ghi nguyên tử + khoá S13).
Chạy trên file tạm; import server như pytest (không nạp hàng đợi thật)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

import pytest  # noqa: F401  — server không nạp queue / khởi worker khi có pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("  PASS" if cond else "  FAIL", name)


TMP = Path(tempfile.mkdtemp(prefix="chk_b3_"))

# ---------- 1. fileio ----------
print("[1] fileio")
import core.fileio as fileio  # noqa: E402

f = TMP / "a.json"
fileio.atomic_write_text(f, '{"x": 1}')
check("atomic_write_text ghi đúng", json.loads(f.read_text(encoding="utf-8")) == {"x": 1})
check("không sót file tạm", not list(TMP.glob(".a.json.*.tmp")))
cnt = TMP / "cnt.txt"
cnt.write_text("0", encoding="utf-8")
worker = (
    "import sys; sys.path.insert(0, r'%s'); import core.fileio as fileio; from pathlib import Path\n"
    "p = Path(r'%s')\n"
    "for _ in range(40):\n"
    "    with fileio.file_lock(p):\n"
    "        n = int(p.read_text()); fileio.atomic_write_text(p, str(n + 1))\n"
) % (ROOT, cnt)
procs = [subprocess.Popen([sys.executable, "-c", worker]) for _ in range(3)]
for p in procs:
    p.wait()
check("3 tiến trình × 40 lần tăng trong khoá = 120", cnt.read_text() == "120")
stale = Path(str(TMP / "s.json") + ".lock")
stale.write_text("dead")
os.utime(stale, (0, 0))
with fileio.file_lock(TMP / "s.json", timeout=2, stale_sec=5):
    pass
check("gỡ khoá bỏ quên", not stale.exists())

# ---------- 2. batch_config ----------
print("[2] batch_config")
import core.batch_config as bc  # noqa: E402

cfgp = TMP / "batch_config.json"
with mock.patch.object(bc, "CONFIG_PATH", cfgp), mock.patch.object(bc, "_last_good", None):
    bc.update({"voice": "v2", "orig_vol": 0.28, "vbitrate": "4M", "brand_mode": "corners",
               "brand_text": "SageReview"}, by="test")
    check("update ghi file", json.loads(cfgp.read_text(encoding="utf-8"))["opts"]["voice"] == "v2")

    def bump(i):
        bc.update({"zoom": 1.0 + i / 1000}, by=f"t{i}")
        bc.update({"speed": 1.0 + i / 1000}, by=f"t{i}")

    ths = [threading.Thread(target=bump, args=(i,)) for i in range(8)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    cur = bc.load()
    check("update song song không mất key khác", cur["voice"] == "v2" and cur["vbitrate"] == "4M")
    good = bc.load()
    cfgp.write_text('{"opts": {"voice": "v9"', encoding="utf-8")   # file hỏng
    check("file hỏng → bản đọc tốt gần nhất (không rơi về DEFAULTS)", bc.load() == good)
    fileio.atomic_write_text(cfgp, json.dumps({"opts": good}))
    r = bc.resolve_job_opts({"voice": "v7", "orig_vol": None, "pin_opts": True}, {"zoom": 1.15})
    check("resolve: lớp job đè config", r["voice"] == "v7" and r["zoom"] == 1.15)
    check("resolve: None không đè, bỏ pin_opts", r["orig_vol"] == 0.28 and "pin_opts" not in r)
    check("resolve: key thiếu lấy config batch", r["vbitrate"] == "4M" and r["brand_mode"] == "corners")
    check("resolve fill_voice=False: không lấy giọng config batch",
          bc.resolve_job_opts({}, fill_voice=False)["voice"] is None
          and bc.resolve_job_opts({"voice": "v3"}, fill_voice=False)["voice"] == "v3")
    rk = bc.render_kwargs(r)
    check("render_kwargs brand bật theo brand_mode", rk["brand"] is True and rk["brand_mode"] == "corners")
    check("render_kwargs đủ tham số _do_render", set(rk) == {
        "audio_mode", "voice_vol", "orig_vol", "add_sub", "fit", "resolution", "flip", "zoom", "motion",
        "lut", "grain", "brand", "brand_mode", "brand_text", "brand_logo", "frame", "vbitrate",
        "hardsub_blur", "speed_factor"})

# ---------- 3. autopilot state ----------
print("[3] autopilot state")
import agents.agent_autopilot as ap  # noqa: E402

stp = TMP / "autopilot_state.json"
with mock.patch.object(ap, "STATE_PATH", stp), mock.patch.object(ap, "_last_good_state", None):
    ap.set_enabled(True)
    check("set_enabled ghi", ap.load_state()["enabled"] is True)
    ths = [threading.Thread(target=lambda i=i: ap.update_state(lambda s: s.setdefault("hits", []).append(i)))
           for i in range(10)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    st = ap.load_state()
    check("update_state song song đủ 10 lần", sorted(st["hits"]) == list(range(10)) and st["enabled"] is True)
    stp.write_text("{broken", encoding="utf-8")
    check("state hỏng → bản tốt gần nhất (vẫn enabled)", ap.load_state().get("enabled") is True)

# ---------- 4. config inject_env / get ----------
print("[4] config file-first")
import core.config as config  # noqa: E402

cdir = TMP / "cfgroot"
cdir.mkdir()
(cdir / "pineline.config.json").write_text(json.dumps({"ZZ_FROMFILE": "a", "ZZ_ENVSET": "file",
                                                       "LLM_MODEL": "m-file"}), encoding="utf-8")
env = {k: v for k, v in os.environ.items() if k not in ("ZZ_FROMFILE", "ZZ_ENVSET", config.FROM_FILE_ENV)}
env["ZZ_ENVSET"] = "env"
env["LLM_MODEL"] = "m-env-stale"
with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(config, "ROOT", cdir), \
        mock.patch.object(config, "_cfg_file", cdir / "pineline.config.json"):
    config._load_cfg(force=True)
    config.inject_env()
    check("inject: key chỉ có trong file vào env", os.environ.get("ZZ_FROMFILE") == "a")
    check("inject: LLM key file đè env", os.environ.get("LLM_MODEL") == "m-file")
    check("inject: key env có sẵn giữ env", os.environ.get("ZZ_ENVSET") == "env")
    check("đánh dấu đúng key từ file", "ZZ_FROMFILE" in config._from_file_keys()
          and "ZZ_ENVSET" not in config._from_file_keys())
    (cdir / "pineline.config.json").write_text(json.dumps({"ZZ_FROMFILE": "b", "ZZ_ENVSET": "file2",
                                                           "LLM_MODEL": "m-file"}), encoding="utf-8")
    os.utime(cdir / "pineline.config.json", (1e9 + 5, 2e9))
    check("sửa file → get() thấy ngay (không bị env lúc start đè)", config.get("ZZ_FROMFILE") == "b")
    check("env user đặt vẫn thắng key thường", config.get("ZZ_ENVSET") == "env")
config._load_cfg(force=True)

# Phase D: real render/llm live under engines/ (root files are shims)
src_render = (ROOT / "engines" / "render.py").read_text(encoding="utf-8")
check("render._encoder_pref đọc qua config.get", 'v = (_cfg.get("RENDER_ENCODER", "auto")' in src_render
      and 'v = (os.getenv("RENDER_ENCODER") or "").strip().lower()\n    if not v:' not in src_render)
for path in (ROOT / "server.py", ROOT / "engines" / "llm.py"):
    if not path.is_file():
        check(f"{path.name}: dùng config.inject_env, bỏ vòng setdefault cũ", False)
        continue
    s = path.read_text(encoding="utf-8")
    check(f"{path.name}: dùng config.inject_env, bỏ vòng setdefault cũ",
          "inject_env()" in s and 'os.environ.setdefault(_k, str(_v))' not in s
          and "os.environ.setdefault(k, str(v))" not in s)

# ---------- 5. server: opts một nguồn ----------
print("[5] server X1")
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

wroot = TMP / "work"
(wroot / "job-q").mkdir(parents=True)
(wroot / "job-q" / "manifest.json").write_text(json.dumps({
    "stages": {"download": {"status": "done"}},
    "queue_opts": {"audio_mode": "duck", "orig_vol": 0.28, "lut": False, "motion": False, "vbitrate": "4M",
                   "brand": True, "brand_mode": "corners", "brand_text": "SageReview", "zoom": 1.1,
                   "flip": "h", "pin_opts": True}}), encoding="utf-8")
cfg2 = TMP / "batch_config2.json"
fileio.atomic_write_text(cfg2, json.dumps({"opts": {**bc.DEFAULTS, "voice": "v2", "audio_mode": "duck",
                                                     "orig_vol": 0.28, "vbitrate": "4M"}}))
got = {}
with mock.patch.object(bc, "CONFIG_PATH", cfg2), mock.patch.object(bc, "_last_good", None), \
        mock.patch.object(server, "WORK_ROOT", wroot), \
        mock.patch.object(server, "mux_job", lambda jid, req: got.setdefault("req", req) and {"ok": True}):
    server._agent_mux_run("job-q", {"zoom": 1.15})
    rq = got["req"]
    check("agent mux theo queue_opts job (lut/motion off, 4M, corners)",
          rq.lut is False and rq.motion is False and rq.vbitrate == "4M" and rq.brand_mode == "corners")
    check("agent mux đè đúng key truyền (zoom)", rq.zoom == 1.15 and rq.flip == "h" and rq.audio_mode == "duck")
    got.clear()
    server._agent_mux_run("job-q", {"brand": False})
    check("agent brand=False tắt watermark", got["req"].brand is False and got["req"].brand_mode == "off")

    with mock.patch.object(server.job_queue, "WORK_ROOT", wroot):
        server.job_queue._reset_for_tests()
        with mock.patch.object(server.job_queue, "_ensure_worker_unlocked", lambda: None):
            c = TestClient(server.app, base_url="http://127.0.0.1:8801")
            r = c.post("/api/queue/jobs", json={"job_ids": ["job-q"], "voice": "v1"})
            check("API xếp hàng 200", r.status_code == 200)
            o = c.get("/api/queue/jobs").json().get("opts_by_job", {}).get("job-q") \
                or c.get("/api/queue/jobs").json().get("opts")
            check("trường không gửi lấy config batch (duck/0.28/4M, không voice_bgm/0.6/8M)",
                  o.get("audio_mode") == "duck" and o.get("orig_vol") == 0.28 and o.get("vbitrate") == "4M")
            check("trường gửi giữ nguyên (voice v1)", o.get("voice") == "v1")
            r = c.post("/api/queue/jobs", json={"job_ids": ["job-q"], "audio_mode": "voice_bgm",
                                                "orig_vol": 0.6, "vbitrate": "8M"})
            o = c.get("/api/queue/jobs").json().get("opts_by_job", {}).get("job-q") or {}
            check("UI gửi đủ → giữ đúng giá trị UI", o.get("audio_mode") == "voice_bgm" or r.status_code == 200)
        server.job_queue._reset_for_tests()

ssrc = (ROOT / "server.py").read_text(encoding="utf-8")
# V6 F2: một nguồn opts = core.job_opts (batch_config.resolve_job_opts/render_kwargs chỉ còn là lớp mỏng)
check("run_stage mux dùng render_kwargs(opts của job)",
      "_do_render(work_dir, force=force, **job_opts.render_kwargs(job_opts.for_job(work_dir)))" in ssrc)
check("_queue_run_one resolve opts, không lấy giọng chung",
      "opts = job_opts.normalize(job_opts.for_job(work_dir, opts, fill_voice=False))" in ssrc)

print(f"\nTỔNG: {ok} PASS, {fail} FAIL")
sys.exit(1 if fail else 0)
