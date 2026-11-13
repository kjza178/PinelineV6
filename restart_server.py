"""Restart server Pineline (giữ sidecar), khôi phục hàng đợi + Auto-pilot.

Instance: `--instance <tên>` (hoặc env PINELINE_INSTANCE, mặc định v6) → cổng/work/sidecar lấy từ
instances/<tên>.json (apply_env). review2: env PORT lạc (vd 8801 của V2 trong shell) KHÔNG còn quyết định
tiến trình bị kill; chỉ kill backend/sidecar có cwd = cây này + khớp token cổng/instance. Instance có cổng
của tiến trình ngoài V6 (8801/8802/8000/8083 — prod) bị TỪ CHỐI trừ khi `--allow-foreign-port`.

--after-current: rút job chờ, đợi job đang chạy xong rồi mới restart (không ngắt job nào).
--hold-file F:    sau khi job xong, chờ tới khi file F biến mất (code đang sửa dở). Chờ quá
                  --hold-max-h giờ → xếp lại job chờ trên server cũ, KHÔNG restart.
--clean-env:      start server bằng python server.py với env chụp từ process cũ
                  (giữ AGENT_WATCH/AGENT_QA_GATE…). Legacy start_8801.py chỉ khi PORT=8801.
--sidecar:        restart cả sidecar của instance (AUTOPILOT_URL; env chụp từ sidecar cũ).
Trước khi kill luôn py_compile + import thử; lỗi → không restart, xếp lại job chờ."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from core.instance import FOREIGN_PORTS, load as _load_instance  # noqa: E402


def _pre_instance(argv: list[str]) -> str:
    """Tên instance từ argv (`--instance X` / `--instance=X`) → env PINELINE_INSTANCE → v6."""
    for i, t in enumerate(argv):
        if t == "--instance" and i + 1 < len(argv):
            return argv[i + 1]
        if t.startswith("--instance="):
            return t.split("=", 1)[1]
    return os.getenv("PINELINE_INSTANCE") or "v6"


# review2: đặt env instance TRƯỚC khi import migrate_work/agent_autopilot (BASE của chúng đọc env lúc import).
_ENV_PORT_BEFORE = os.getenv("PORT")
INST = _load_instance(_pre_instance(sys.argv[1:]))
INST.apply_env()
if _ENV_PORT_BEFORE and _ENV_PORT_BEFORE.strip() != str(INST.port):
    print(f"[restart] bỏ qua env PORT={_ENV_PORT_BEFORE} — dùng cổng instance {INST.name}={INST.port}", flush=True)

import tools.migrate_work as mw  # noqa: E402
from agents.agent_autopilot import load_state, set_enabled  # noqa: E402

from core.instance import sidecar_url as _sidecar_url  # noqa: E402

PORT = str(INST.port)
WORK = Path(os.environ.get("WORK_DIR") or (ROOT / "work"))
if not WORK.is_absolute():
    WORK = ROOT / WORK
# V6 F4: bỏ đường cứng V2 (D:\…V2_tmp\start_8801.py); script khởi động legacy chỉ khi đặt env.
_LEGACY = (os.getenv("PINELINE_LEGACY_START") or "").strip()
START_LEGACY = Path(_LEGACY) if _LEGACY else None
SIDECAR = _sidecar_url()
CHECK_MODULES = (
    "server.py",
    "core/job_queue.py", "core/config.py", "core/manifest.py",
    "core/batch_config.py", "core/fileio.py", "core/job_meta.py", "core/film_db.py",
    "tools/preset_switcher.py",
    "http_routes/routes_ui.py", "http_routes/routes_hg.py", "http_routes/routes_agent.py",
    "http_routes/routes_films.py", "http_routes/routes_uploader_settings.py",
    "engines/llm.py", "engines/translate.py", "engines/translate_alts.py",
    "engines/render.py", "engines/sfx_lib.py", "engines/assemble.py", "engines/media_ts.py",
    "engines/clone_tts.py", "engines/separate.py", "engines/split_parts.py",
    "engines/asr/asr.py", "engines/asr/fun_asr_nano.py", "engines/asr/nano_fa_asr.py",
    "engines/asr/hybrid_asr.py", "engines/asr/firered_asr.py", "engines/asr/sensevoice_asr.py",
    "engines/asr/paraformer_zh.py",
    "agents/hongguo_bridge.py", "agents/agent_core.py", "agents/agent_hongguo.py",
    "agents/agent_feeder.py", "agents/agent_tele_cmd.py", "agents/agent_autopilot.py",
)
# Giả "pytest" để import server không nạp hàng đợi thật / khởi worker song song server đang chạy.
SMOKE = (
    "import sys, types; sys.modules.setdefault('pytest', types.ModuleType('pytest')); "
    "import server; import agents.agent_tele_cmd as agent_tele_cmd; "
    "import engines.translate as translate; import engines.translate_alts as translate_alts; "
    "print('import ok')"
)


def _manifest_opts(job_id: str) -> dict:
    try:
        m = json.loads((WORK / job_id / "manifest.json").read_text(encoding="utf-8"))
        return {k: v for k, v in (m.get("queue_opts") or {}).items() if k != "pin_opts"}
    except Exception:  # noqa: BLE001
        return {}


def _tg(msg: str) -> None:
    if not mw.telegram_allowed():      # review2: v6/test telegram=false → không gửi
        mw.log(f"(telegram tắt theo instance) {msg[:200]}")
        return
    try:
        os.environ.setdefault("AGENT_TELEGRAM", "1")
        from agents.agent_watch import telegram_send
        telegram_send("Restart: " + msg, force=True)
    except Exception as e:  # noqa: BLE001
        mw.log(f"telegram lỗi: {e}")


def _server_procs() -> list:
    """Backend CỦA instance này (cwd = cây này + token cổng/instance khớp) — không bao giờ server V2."""
    return mw.server_procs(INST.port, INST.name)


def _sidecar_procs() -> list:
    return mw.instance_procs("agent_tele_cmd.py", instance=INST.name)


def _proc_env(procs: list) -> dict | None:
    for p in procs:
        try:
            return dict(p.environ())
        except Exception:  # noqa: BLE001
            continue
    return None


def _precheck() -> str:
    """'' nếu code mới compile + import được; ngược lại là lỗi (không restart)."""
    r = subprocess.run([sys.executable, "-m", "py_compile", *CHECK_MODULES], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        return "py_compile: " + (r.stderr or r.stdout)[-400:]
    r = subprocess.run([sys.executable, "-c", SMOKE], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    if r.returncode or "import ok" not in (r.stdout or ""):
        return "import: " + (r.stderr or r.stdout)[-400:]
    return ""


def _job_parked(job_id: str) -> bool:
    try:
        m = json.loads((WORK / str(job_id) / "manifest.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    ag = m.get("agent") or {}
    return bool(ag.get("held_at") or ag.get("hold_after_translate") or ag.get("qa_blocked"))


def _requeue(order: list[str], opts: dict) -> list[str]:
    q2 = mw.api_retry("GET", mw.BASE + "/api/queue/jobs")
    have = set(([q2["current"]] if q2.get("current") else []) + [str(x) for x in (q2.get("queue") or [])])
    missing = [j for j in order if j not in have]
    i = 0
    while i < len(missing):
        o = opts[missing[i]]
        ids = [missing[i]]
        while i + len(ids) < len(missing) and opts[missing[i + len(ids)]] == o:
            ids.append(missing[i + len(ids)])
        mw.api_retry("POST", mw.BASE + "/api/queue/jobs", {"job_ids": ids, "skip_done": True, **o}, timeout=60)
        i += len(ids)
    return missing


def _start_server(clean_env: bool, env: dict | None) -> None:
    e = dict(env or os.environ)
    e.setdefault("PORT", PORT)
    e.pop("PYTHONPATH", None)
    if clean_env:
        # Prefer in-tree server (V4); legacy start_8801 only if still present
        if START_LEGACY is not None and START_LEGACY.is_file():
            subprocess.Popen([sys.executable, str(START_LEGACY)], cwd=str(ROOT), env=e,
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            return
        mw.start_backend(INST.name, env=e)
        return
    # review2: trước gọi run_v5.bat (KHÔNG có trong cây V6 → SystemExit SAU khi đã kill server).
    mw.start_backend(INST.name, env=e)


def _restart_sidecar(env: dict | None) -> bool:
    # review2: trước kill MỌI agent_tele_cmd.py trên máy (gồm sidecar V2 :8802 production) rồi chạy
    # run_tele_cmd.bat (không có trong V6). Nay: chỉ sidecar của cây + instance này.
    for p in _sidecar_procs():
        mw.kill_with_parent_cmd(p)
    time.sleep(3)
    mw.start_sidecar(INST.name, env=env or None)
    return mw.wait_http(SIDECAR + "/status", 90)


def guard_ports(inst, allow_foreign: bool = False) -> str:
    """'' nếu được phép restart; ngược lại lý do từ chối (cổng thuộc tiến trình ngoài V6)."""
    bad = sorted({inst.port, inst.sidecar_port} & FOREIGN_PORTS)
    if bad and not allow_foreign:
        return (f"instance {inst.name} dùng cổng {bad} của tiến trình ngoài V6 (V2 production/Hongguo/llama) — "
                "từ chối restart (thêm --allow-foreign-port nếu THẬT SỰ cutover)")
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--after-current", action="store_true")
    ap.add_argument("--hold-file", default="")
    ap.add_argument("--hold-max-h", type=float, default=3.0)
    ap.add_argument("--clean-env", action="store_true")
    ap.add_argument("--sidecar", action="store_true")
    ap.add_argument("--instance", default=None, help="instance (instances/<tên>.json); mặc định env/v6")
    ap.add_argument("--allow-foreign-port", action="store_true",
                    help="cho restart instance dùng cổng 8801/8802/8000/8083 (cutover) — mặc định từ chối")
    ap.add_argument("--ignore-hg", action="store_true",
                    help="restart dù hàng đợi gộp Hongguo đang chạy (series đang gộp sẽ làm lại từ đầu)")
    a = ap.parse_args()
    why = guard_ports(INST, a.allow_foreign_port)
    if why:
        mw.log("KHÔNG restart: " + why)
        raise SystemExit(2)

    ap_was = bool(load_state().get("enabled"))
    set_enabled(False)
    q = mw.api_retry("GET", mw.BASE + "/api/queue/jobs")
    cur = q.get("current")
    pending = [str(x) for x in (q.get("queue") or [])]
    order = pending if a.after_current else ([cur] if cur else []) + pending
    snaps = q.get("opts_by_job") or {}
    opts = {j: snaps.get(j) or {**(q.get("opts") or {}), **_manifest_opts(j)} for j in order}
    mw.log(f"restart server · current={cur} · queue={order} · Auto-pilot={ap_was} · args={vars(a)}")
    (WORK / "logs").mkdir(parents=True, exist_ok=True)
    (WORK / "logs" / "restart_pending.json").write_text(
        json.dumps({"order": order, "opts": opts, "ap_was": ap_was, "at": time.time()}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    def _abort(why: str) -> None:
        got = _requeue(order, opts)
        if ap_was:
            set_enabled(True)
        mw.log(f"KHÔNG restart: {why} · xếp lại {got}")
        _tg(f"không restart ({why[:200]}); đã xếp lại {len(got)} job trên server cũ")
        raise SystemExit(1)

    if not a.ignore_hg:
        try:
            mq = mw.api("GET", mw.BASE + "/api/hg/merge-queue")
        except Exception:  # noqa: BLE001
            mq = {}
        if mq.get("current") or (mq.get("worker") and mq.get("queue")):
            _abort(f"đang gộp Hongguo «{mq.get('current') or '?'}», còn {len(mq.get('queue') or [])} series chờ "
                   "(chờ xong hoặc chạy lại với --ignore-hg)")

    if a.after_current:
        if pending:
            mw.api_retry("POST", mw.BASE + "/api/queue/jobs/remove", {"job_ids": pending})
        _tg(f"hẹn: tạm rút {len(pending)} job chờ, đợi job {cur or '-'} xong rồi restart server")
        while True:
            q = mw.api_retry("GET", mw.BASE + "/api/queue/jobs", tries=40)
            if not q.get("current") and not q.get("worker") and not mw.running_stages(WORK) and not (q.get("queue") or []):
                break
            # fix 2026-11-11 B8: job đang PARK (HOLD/soát dở) giữ ghế vô hạn → coi là rảnh (state đã bền trên đĩa)
            if q.get("current") and _job_parked(q["current"]) and not mw.running_stages(WORK) and not (q.get("queue") or []):
                mw.log(f"job {q['current']} đang park HOLD/soát — restart không chờ")
                break
            time.sleep(20)
        mw.log(f"job {cur} xong")

    if a.hold_file:
        hold = Path(a.hold_file)
        t0 = time.time()
        if hold.exists():
            _tg("job xong nhưng code còn đang sửa — chờ gỡ cờ hold")
        while hold.exists():
            if time.time() - t0 > a.hold_max_h * 3600:
                _abort(f"cờ {hold.name} còn sau {a.hold_max_h} giờ")
            time.sleep(30)

    err = _precheck()
    if err:
        _abort(err)
    mw.log("precheck ok")

    srv_procs = _server_procs()
    srv_env = _proc_env(srv_procs) if a.clean_env else None
    side_env = _proc_env(_sidecar_procs()) if a.sidecar else None
    for k in ("AGENT_BASE_URL", "AGENT_API_KEY"):   # key giờ chỉ lấy từ pineline.config.json
        (side_env or {}).pop(k, None)
    if not srv_procs:
        mw.log(f"không thấy backend instance {INST.name} (cổng {PORT}) trong cây {ROOT} — chỉ khởi động")
    for p in srv_procs:
        mw.log(f"kill server PID {p.pid}")
        mw.kill_with_parent_cmd(p)
    time.sleep(4)
    _start_server(a.clean_env, srv_env)
    mw.log("đã gọi start server, chờ /api/agent/status")
    if not mw.wait_http(mw.BASE + "/api/agent/status", 180):
        mw.log("server không lên sau 3 phút")
        _tg(f"server không lên sau 3 phút — port {PORT}")
        raise SystemExit("server không lên")
    mw.log("server lên")
    side_ok = _restart_sidecar(side_env) if a.sidecar else None

    time.sleep(5)
    missing = _requeue(order, opts)
    if ap_was:
        set_enabled(True)
    q3 = mw.api("GET", mw.BASE + "/api/queue/jobs")
    mw.log(f"restart xong · current={q3.get('current')} · queue={q3.get('queue')} · xếp bù {len(missing)} · sidecar={side_ok}")
    if a.after_current or a.hold_file:
        _tg(f"xong, đã xếp lại {len(missing)} job, sidecar "
            f"{'-' if side_ok is None else ('lên' if side_ok else 'KHÔNG lên')}, Auto-pilot {'bật' if ap_was else 'tắt'}")
    print(json.dumps({"current": q3.get("current"), "queue": q3.get("queue"), "worker": q3.get("worker"),
                      "requeued": missing}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        import traceback
        mw.log("restart lỗi: " + traceback.format_exc()[-1500:])
        _tg(f"restart lỗi giữa chừng ({e!r:.200}) — job chờ lưu ở work/logs/restart_pending.json")
        raise
