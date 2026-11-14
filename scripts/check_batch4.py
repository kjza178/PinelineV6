"""Kiểm thử đợt 4 (tranh chấp ghi manifest, claim endpoint, single-instance, worker khởi động).
Chạy trên thư mục tạm; import server như pytest (không nạp hàng đợi thật)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest  # noqa: F401  — server không nạp queue / khởi worker khi có pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
os.chdir(ROOT)
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("  PASS" if cond else "  FAIL", name)


TMP = Path(tempfile.mkdtemp(prefix="chk_b4_"))

# ---------- 1. _merge3 ----------
print("[1] manifest._merge3")
import core.manifest as mf  # noqa: E402

base = {"a": 1, "stages": {"tts": {"p": 1, "v": "x"}}, "gone": 1}
mine = {"a": 2, "stages": {"tts": {"p": 1, "v": "y"}}, "gone": 1}
theirs = {"a": 1, "stages": {"tts": {"p": 5, "v": "x"}}, "gone": 1, "agent": {"held_at": "t"}}
r = mf._merge3(base, mine, theirs)
check("key mình đổi → lấy mình", r["a"] == 2)
check("dict cùng đổi → gộp sâu", r["stages"]["tts"] == {"p": 5, "v": "y"})
check("key người khác thêm → giữ", r.get("agent") == {"held_at": "t"})
r = mf._merge3({"k": 1, "j": 1}, {"j": 1}, {"k": 1, "j": 1})
check("mình xoá key → xoá", "k" not in r)
r = mf._merge3({"k": 1}, {"k": 1}, {})
check("người khác xoá key, mình không đổi → xoá", "k" not in r)
r = mf._merge3({"k": 1}, {"k": 2}, {"k": 3})
check("xung đột scalar → mình thắng", r["k"] == 2)

# ---------- 2. Manifest sống lâu ----------
print("[2] Manifest sống lâu không ghi đè cờ người khác")
wd = TMP / "job1"
wd.mkdir()
m0 = mf.Manifest(wd)
m0.data["stages"]["translate"] = {"status": "done"}
js.persist(m0)
long_lived = mf.Manifest(wd)
other = mf.Manifest(wd)
other.data["agent"] = {"hold_after_translate": True, "held_at": "now"}
js.persist(other)
long_lived.data["stages"]["mux"] = {"status": "running"}
js.persist(long_lived)
d = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))
check("cờ HOLD còn sau khi object cũ save", (d.get("agent") or {}).get("held_at") == "now")
check("thay đổi của object cũ cũng được ghi", d["stages"]["mux"]["status"] == "running")
long_lived.data["stages"]["mux"]["status"] = "done"
js.persist(long_lived)
d = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))
check("save lần 2 vẫn giữ cờ HOLD", (d.get("agent") or {}).get("held_at") == "now")
check("không sót file tạm", not list(wd.glob(".manifest.*.tmp")))

# ---------- 3. update() ----------
print("[3] Manifest.update")
mm = mf.Manifest(wd)
check("update trả True", mm.update(lambda x: x.__setitem__("n", 0)) is True)
check("update fn trả False → không ghi",
      mm.update(lambda x: (x.__setitem__("n", 99), False)[1]) is False
      and json.loads((wd / "manifest.json").read_text(encoding="utf-8"))["n"] == 0)
worker = (
    "import sys; sys.path.insert(0, r'%s'); import core.manifest as mf\n"
    "def inc(d): d['n'] = d.get('n', 0) + 1\n"
    "for _ in range(25):\n"
    "    mf.Manifest(r'%s').update(inc)\n"
) % (ROOT, wd)
procs = [subprocess.Popen([sys.executable, "-c", worker]) for _ in range(3)]
for p in procs:
    p.wait()
d = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))
check("3 tiến trình × 25 update = 75", d.get("n") == 75)
check("stages còn nguyên", d["stages"].get("translate", {}).get("status") == "done")
bad = TMP / "bad"
bad.mkdir()
(bad / "manifest.json").write_text("{hỏng", encoding="utf-8")
check("manifest hỏng → update False, không ghi",
      mf.Manifest(bad).update(lambda x: x.__setitem__("z", 1)) is False
      and (bad / "manifest.json").read_text(encoding="utf-8") == "{hỏng")

# ---------- 4. server ----------
print("[4] server")
import server  # noqa: E402

wr = TMP / "work"
wr.mkdir()
jid = "jobA"
jd = wr / jid
jd.mkdir()
(jd / "manifest.json").write_text(json.dumps({
    "stages": {"asr": {"status": "done", "model": "firered"}, "tts": {"voice": "a"}},
    "agent": {"held_at": "x"},
}), encoding="utf-8")
with mock.patch.object(server, "WORK_ROOT", wr):
    server._patch_tts_fields(jd, voice="b", speed=1.1, engine=None)
    d = json.loads((jd / "manifest.json").read_text(encoding="utf-8"))
    check("_patch_tts_fields ghi voice/speed", d["stages"]["tts"].get("voice") == "b"
          and d["stages"]["tts"].get("speed") == 1.1)
    check("_patch_tts_fields giữ cờ agent", d["agent"].get("held_at") == "x")
    check("tts_config.json được ghi",
          json.loads((jd / "tts_config.json").read_text(encoding="utf-8")).get("voice") == "b")
    (jd / "tts_config.json").unlink()
    server._patch_tts_fields(jd, voice="b")
    check("giá trị không đổi vẫn tạo lại tts_config.json", (jd / "tts_config.json").exists())
    server._patch_asr_fields(jd, engine="nano", model="")
    d = json.loads((jd / "manifest.json").read_text(encoding="utf-8"))
    check("_patch_asr_fields: model='' xoá key, engine ghi",
          "model" not in d["stages"]["asr"] and d["stages"]["asr"].get("engine") == "nano")

    # mark-done 409 khi job đang trong pipeline
    with mock.patch.object(server, "_job_in_pipeline", return_value=True):
        res = server.mark_job_done(jid, None)
    check("mark-done khi job đang chạy → 409", getattr(res, "status_code", None) == 409)

    # S8: claim endpoint
    (jd / "tts.json").write_text("[]", encoding="utf-8")
    req = server.MuxReq()
    calls = []

    class SyncExec:
        def submit(self, fn, *a, **k):
            calls.append(1)
            return fn(*a, **k)

    with mock.patch.object(server, "_gpu", SyncExec()), \
            mock.patch.object(server, "_do_render", lambda *a, **k: None):
        assert server._claim_stage(jid, "__all__")
        res = server.mux_job(jid, req)
        check("mux khi job đang claim → 409", getattr(res, "status_code", None) == 409 and not calls)
        server._release_stage(jid, "__all__")
        res = server.mux_job(jid, req)
        check("mux bình thường → started", res == {"started": True} and len(calls) == 1)
        check("claim mux được nhả sau khi chạy", server._job_any_busy(jid) is None)
        m = mf.Manifest(jd)
        m.data["stages"]["tts"]["status"] = "running"
        js.persist(m)
        res = server.mux_job(jid, req)
        check("mux khi manifest có stage running → 409", getattr(res, "status_code", None) == 409)
        res = server.assemble_job(jid)
        check("assemble khi stage running → 409", getattr(res, "status_code", None) == 409)
        m = mf.Manifest(jd)
        m.data["stages"]["tts"]["status"] = "done"
        js.persist(m)

    with mock.patch.object(server, "_gpu", SyncExec()):
        def boom(*a, **k):
            raise RuntimeError("x")
        try:
            server._submit_claimed(server._gpu, jid, "mux", boom)
        except RuntimeError:
            pass
        check("_submit_claimed nhả claim cả khi lỗi", server._job_any_busy(jid) is None)

    # khớp process engine theo đường dẫn work
    class P:
        def __init__(self, pid, cmd):
            self.info = {"pid": pid, "name": "python.exe", "cmdline": cmd}
            self._cmd = cmd

        def cmdline(self):
            # server._iter_job_engine_procs gọi p.cmdline(), không lấy từ info
            return self._cmd

    fake = [
        P(1, ["python", "clone_tts.py", str(jd)]),
        P(2, ["python", "clone_tts.py", r"Z:\Pineline\work\jobA"]),
        P(3, ["python", "asr.py", str(jd).replace("\\", "/")]),
    ]
    import psutil
    with mock.patch.object(psutil, "process_iter", lambda *a, **k: iter(fake)):
        pids = [p.info["pid"] for p in server._iter_job_engine_procs(jid)]
    check("chỉ khớp process của work V5 (bỏ path work khác cùng job_id)", sorted(pids) == [1, 3])

    killed = []

    class C:
        def __init__(self, pid, cmd):
            self.pid, self._cmd = pid, cmd

        def cmdline(self):
            return self._cmd

        def name(self):
            return "python.exe"

        def kill(self):
            killed.append(self.pid)

    kids = [C(11, ["py", "vieneu_batch.py", str(jd / "tts" / "_vieneu_job.json")]),
            C(12, ["py", "vieneu_batch.py", str(wr / "jobB" / "tts" / "_vieneu_job.json")])]

    class Me:
        def children(self, recursive=False):
            return kids

    with mock.patch.object(psutil, "Process", lambda *a: Me()), \
            mock.patch.object(psutil, "process_iter", lambda *a, **k: iter([])), \
            mock.patch.object(server, "_kill_job_engine_procs", lambda *a, **k: None):
        server._kill_job_children(jid)
    check("_kill_job_children chỉ kill worker TTS của job này", killed == [11])

# startup worker
print("[5] khởi động")
check("_start_queue_worker đăng ký startup",
      server._start_queue_worker in server.app.router.on_startup)
src = (ROOT / "server.py").read_text(encoding="utf-8")
tail = src[src.rfind('if __name__ == "__main__"'):]
check("__main__ có khoá msvcrt một bản", "msvcrt.locking" in tail and "LK_NBLCK" in tail)
lockf = TMP / ".server_test.lock"
if os.name != "nt":
    # msvcrt chỉ có trên Windows — phần khoá một-bản của `python server.py` không áp dụng ở hệ khác
    # (V6 chạy qua `python -m app`, khoá bằng app/__main__._single_instance_lock: msvcrt | fcntl).
    print("  SKIP bản thứ hai không lấy được khoá (msvcrt — chỉ Windows)")
else:
  holder = subprocess.Popen([sys.executable, "-c", (
    "import msvcrt, time\n"
    "f = open(r'%s', 'a+'); f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\n"
    "print('locked', flush=True); time.sleep(5)\n") % lockf], stdout=subprocess.PIPE, text=True)
  holder.stdout.readline()
  second = subprocess.run([sys.executable, "-c", (
    "import msvcrt, sys\n"
    "f = open(r'%s', 'a+'); f.seek(0)\n"
    "try:\n    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\nexcept OSError:\n    sys.exit(7)\n") % lockf])
  holder.kill()
  check("bản thứ hai không lấy được khoá", second.returncode == 7)

# ---------- 6. hàng đợi: đổi opts tại chỗ (X4) ----------
print("[6] queue_update_opts + /api/queue/jobs/opts")
import core.job_queue as job_queue  # noqa: E402

with mock.patch.object(job_queue, "_save", lambda: None):
    job_queue._q[:] = ["j1", "j2", "j3"]
    job_queue._opts_by_job.clear()
    job_queue._opts_by_job.update({"j1": {"a": 1}, "j2": {"a": 1}, "j3": {"a": 1}})
    r = job_queue.queue_update_opts({"j2": {"a": 2}, "zz": {"a": 9}})
    check("chỉ đổi job đang chờ, giữ thứ tự", r["updated"] == ["j2"] and r["skipped"] == ["zz"]
          and job_queue._q == ["j1", "j2", "j3"] and job_queue._opts_by_job["j2"] == {"a": 2})
    res = server.api_queue_jobs_opts({"opts_by_job": {"j3": {"audio_mode": "voice_only", "rác": 1}}})
    o3 = job_queue._opts_by_job.get("j3") or {}
    check("endpoint /opts lọc field lạ + resolve", isinstance(res, dict) and res.get("updated") == ["j3"]
          and o3.get("audio_mode") == "voice_only" and "rác" not in o3)
    res = server.api_queue_jobs_opts({})
    check("endpoint /opts thiếu body → 400", getattr(res, "status_code", None) == 400)
    job_queue._q.clear()
    job_queue._opts_by_job.clear()

import agents.agent_autopilot as ap  # noqa: E402

posts = []


def fake_post(base, path, body=None, timeout=0):
    posts.append(path)
    return {"ok": True, "updated": list((body or {}).get("opts_by_job", {}))}


qstate = {"queue": ["p1", "p2"], "current": "c0", "worker": True,
          "opts_by_job": {"p1": {"audio_mode": "x"}, "p2": {"audio_mode": "x"}}}
with mock.patch.object(ap, "_get", lambda *a, **k: qstate), mock.patch.object(ap, "_post", fake_post), \
        mock.patch.object(ap, "update_state", lambda fn: {}), \
        mock.patch.object(ap, "load_state", lambda: {"pinned": {}}):
    r = ap.sync_pending_to_batch("http://x")
check("sync_pending_to_batch dùng /opts, không remove+add",
      posts == ["/api/queue/jobs/opts"] and set(r.get("changed") or []) == {"p1", "p2"})

# ---------- 7. need_queue bỏ failed / HOLD (A6) ----------
print("[7] classify_jobs / tick_once")
import agents.agent_core as agent_core  # noqa: E402

wr2 = TMP / "work2"
for jid_, agent_ in (("pend", {}), ("held", {"held_at": "x"}), ("qa", {"qa_blocked": True}), ("bad", {})):
    (wr2 / jid_).mkdir(parents=True)
    (wr2 / jid_ / "manifest.json").write_text(json.dumps({"stages": {}, "agent": agent_}), encoding="utf-8")
jobs = [{"job_id": "pend", "status": "pending"}, {"job_id": "held", "status": "pending"},
        {"job_id": "qa", "status": "pending"}, {"job_id": "bad", "status": "failed"}]
with mock.patch.object(agent_core, "_rt", None):
    snap = agent_core.classify_jobs(jobs, wr2)
check("need_queue chỉ còn job pending không HOLD/QA", snap["need_queue"] == ["pend"])
added = []
brief = {"ok": True, "counts": {}, "need_queue": ["bad", "pend"], "failed_ids": ["bad"],
         "queue_len": 0, "current": None}
with mock.patch.object(ap, "status_brief", lambda b: brief), \
        mock.patch.object(ap, "load_state", lambda: {"enabled": True}), \
        mock.patch.object(ap, "update_state", lambda fn: {"enabled": True}), \
        mock.patch.object(ap, "queue_opts", lambda b: {}), \
        mock.patch.object(ap, "_post", lambda b, p, body=None, timeout=0:
                          (added.append((p, list((body or {}).get("job_ids") or []))) or {"ok": True})):
    ap.tick_once("http://x")
check("tick_once không xếp lại job failed", ("/api/queue/jobs", ["pend"]) in added
      and not any("bad" in ids for _, ids in added))

# ---------- 8. mixed_audio.json (M13) ----------
print("[8] render: mixed_audio.json")
import engines.render as render  # noqa: E402

rd = TMP / "mix"
rd.mkdir()
for n in ("v.mp4", "dub.wav", "no_vocals.wav", "vocals.wav"):
    (rd / n).write_bytes(b"x" * 2000)
ff_calls = []


def fake_baf(cmd, work_dir, dub, nv, voc, audio_mode, vv, ov):
    cmd += ["-i", "dub.wav"]
    return f"af:{audio_mode}:{vv}:{ov}", audio_mode


def fake_ff(cmd, total, m, cwd=None, hi=0):
    ff_calls.append(1)
    (Path(cwd) / cmd[-1]).write_bytes(b"\0" * 200_000)
    return 0, ""


class M:
    data = {"stages": {}}

    def save(self):
        pass


with mock.patch.object(render, "_build_audio_filter", fake_baf), \
        mock.patch.object(render, "_ffmpeg_progress", fake_ff), \
        mock.patch.object(render, "_attach_filter_complex", lambda c, af, wd: c.extend(["-filter_complex", af])):
    args = (rd, rd / "v.mp4", rd / "dub.wav", rd / "no_vocals.wav", rd / "vocals.wav")
    render._ensure_mixed_audio(*args, "duck_bgm", 1.0, 0.3, 10.0, M())
    check("lần đầu trộn + ghi mixed_audio.json", len(ff_calls) == 1 and (rd / "mixed_audio.json").exists())
    render._ensure_mixed_audio(*args, "duck_bgm", 1.0, 0.3, 10.0, M())
    check("cùng thiết lập → dùng lại", len(ff_calls) == 1)
    render._ensure_mixed_audio(*args, "duck_bgm", 1.0, 0.5, 10.0, M())
    check("đổi orig_vol → trộn lại", len(ff_calls) == 2)
    (rd / "mixed_audio.json").unlink()
    render._ensure_mixed_audio(*args, "duck_bgm", 1.0, 0.9, 10.0, M())
    check("bản cũ chưa có json → luật mtime cũ (dùng lại) + ghi json",
          len(ff_calls) == 2 and (rd / "mixed_audio.json").exists())

# ---------- 9. assemble_fit ghi .partial (M11) ----------
print("[9] assemble_fit")
import wave  # noqa: E402

import engines.assemble as assemble  # noqa: E402


def mk_wav(p, sec, sr=48000):
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\0\0" * int(sec * sr))


ad = TMP / "asm"
(ad / "tts").mkdir(parents=True)
mk_wav(ad / "tts" / "seg_0000.wav", 0.5)
(ad / "tts.json").write_text(json.dumps([{"i": 0, "start": 0.0, "end": 1.0, "vi": "a",
                                          "audio": str(ad / "tts" / "seg_0000.wav"), "dur": 0.5}]),
                             encoding="utf-8")
(ad / assemble.FIT_STAMP).write_text("old", encoding="utf-8")
try:
    r = assemble.assemble_fit(ad)
    check("dub_fit.wav có, không sót .partial, stamp mới",
          (ad / "dub_fit.wav").exists() and not (ad / "dub_fit.partial.wav").exists()
          and (ad / assemble.FIT_STAMP).read_text(encoding="utf-8") != "old")
except Exception as e:  # noqa: BLE001
    check(f"assemble_fit chạy được ({e})", False)

# ---------- 10. ensure_audio_wav (M10) ----------
print("[10] asr.ensure_audio_wav")
import engines.asr.asr as asr  # noqa: E402

ed = TMP / "aud"
ed.mkdir()
src_ = ed / "source.m4a"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=d=3", "-c:a", "aac", str(src_)], check=True)
wav_ = ed / "audio.wav"
check("thiếu wav → trích", asr.ensure_audio_wav(src_, wav_) is True and wav_.exists())
check("có marker cùng nguồn → dùng lại", asr.ensure_audio_wav(src_, wav_) is False)
check("không sót audio.partial.wav", not (ed / "audio.partial.wav").exists())
os.utime(src_, (1, 1))
check("nguồn đổi → trích lại", asr.ensure_audio_wav(src_, wav_) is True)
(ed / "audio.wav.json").unlink()
mk_wav(wav_, 1.0, 16000)
check("wav cũ không marker, ngắn hơn nguồn, resume → giữ", asr.ensure_audio_wav(src_, wav_, keep_legacy=True) is False)
check("wav cũ không marker, ngắn hơn nguồn → trích lại", asr.ensure_audio_wav(src_, wav_) is True)
(ed / "audio.wav.json").unlink()
mk_wav(wav_, 4.0, 16000)
check("wav cũ dài hơn nguồn (dồn mẫu) → giữ cho media_ts", asr.ensure_audio_wav(src_, wav_) is False)

# ---------- 11. clone_tts key (M12) ----------
print("[11] clone_tts: key clip")
import engines.clone_tts as clone_tts  # noqa: E402

cd = TMP / "tts_job"
(cd / "tts").mkdir(parents=True)
(cd / "manifest.json").write_text(json.dumps({"stages": {}}), encoding="utf-8")
segs = [{"i": 0, "start": 0, "end": 1, "vi": "xin chào bạn"},
        {"i": 1, "start": 1, "end": 2, "vi": "lời mới đã sửa"}]
(cd / "script.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
for i in (0, 1):
    mk_wav(cd / "tts" / f"seg_{i:04d}.wav", 0.6, 24000)
(cd / "tts.json").write_text(json.dumps([
    {"i": 0, "start": 0, "end": 1, "vi": "xin chào bạn", "audio": str(cd / "tts" / "seg_0000.wav"),
     "dur": 0.6, "engine": "vieneu"},
    {"i": 1, "start": 1, "end": 2, "vi": "lời cũ", "audio": str(cd / "tts" / "seg_0001.wav"),
     "dur": 0.6, "engine": "vieneu"},
], ensure_ascii=False), encoding="utf-8")
seen_todo = []


class _Boom(Exception):
    pass


def fake_worker(engine, ref, ref_text, out_dir, speed, todo, on_prog=None):
    seen_todo.extend(i for i, _ in todo)
    raise _Boom()


with mock.patch.object(clone_tts, "voice_ref_text", lambda v: ("ref.wav", "ref")), \
        mock.patch.object(clone_tts, "free_gpu_vram", lambda *a, **k: None), \
        mock.patch.object(clone_tts, "resolve_engine_python", lambda e: "python"), \
        mock.patch.object(clone_tts, "_run_worker", fake_worker):
    try:
        clone_tts.run_clone_tts(cd, "vieneu", "giọngA", 1.0)
    except _Boom:
        pass
check("clip đổi lời bị bỏ + đọc lại, clip cũ khớp lời được giữ", seen_todo == [1])
check("wav của clip lỗi thời đã xoá", not (cd / "tts" / "seg_0001.wav").exists())
keys = json.loads((cd / "tts" / "_keys.json").read_text(encoding="utf-8"))
check("_keys.json có key câu cần đọc, không gán key cho clip cũ không key",
      "1" in keys and "0" not in keys)
k_a = clone_tts._clip_key("x", "A", "vieneu", 1.0)
check("key đổi theo giọng / tốc độ", k_a != clone_tts._clip_key("x", "B", "vieneu", 1.0)
      and k_a != clone_tts._clip_key("x", "A", "vieneu", 1.1))
mk_wav(cd / "tts" / "seg_0001.wav", 0.6, 24000)
clone_tts._save_keys(cd / "tts", {"1": "khac"})
d0 = clone_tts._scan_done_from_disk(cd / "tts", segs, "vieneu", {}, disk_keys={"1": "khac"},
                                    keyof={0: "k0", 1: "k1"})
check("quét đĩa: wav có key khác → bỏ và xoá", 1 not in d0 and not (cd / "tts" / "seg_0001.wav").exists()
      and 0 in d0)

# ---------- 12. sidecar một bản (A5) ----------
print("[12] sidecar: khoá + bind độc quyền (cổng 18902)")
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_sidecar_lock.py")],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
print("   " + "\n   ".join(x for x in r.stdout.splitlines() if "PASS" in x or "FAIL" in x))
check("sidecar: bản 2 không bind / không lấy được khoá", r.returncode == 0)
(ROOT / ".sidecar_18902.lock").unlink(missing_ok=True)

print(f"\nTỔNG: {ok} PASS, {fail} FAIL")
sys.exit(1 if fail else 0)
