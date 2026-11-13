"""
Chuyển Z:\\PinelineV2\\work sang ổ khác (mặc định D:\\PinelineV2_work) giữa 2 job, không mất tiến độ.

1. Copy trước (robocopy /MIR) trong lúc job hiện tại vẫn chạy.
2. Rút các job đang chờ khỏi hàng + tắt Auto-pilot → worker dừng sau job hiện tại.
3. Chờ không còn job/stage nào chạy.
4. Dừng server + sidecar, copy nốt phần chênh, đổi work → work_old_migrated, tạo junction work → đích.
5. Khởi động lại server + sidecar, xếp lại các job đã rút (đúng thứ tự + opts), trả Auto-pilot về như cũ.

Chạy:  python migrate_work.py [D:\\PinelineV2_work]
Log:   migrate_work.log (cạnh file này, ngoài work/)
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil
import requests

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "work"
from core.instance import server_base as _server_base, server_port as _server_port, sidecar_url as _sidecar_url  # noqa: E402

# V6 F4: mặc định theo instance (WORK_DIR / server / sidecar) — không cứng V2 (D:\…V2_work, :8801, :8802).
DST = Path(sys.argv[1] if len(sys.argv) > 1 else (os.getenv("WORK_DIR") or "work_migrated"))
OLD = ROOT / "work_old_migrated"
BASE = _server_base()
SIDECAR = _sidecar_url()
LOG = ROOT / "migrate_work.log"
PLAN = ROOT / "migrate_work_plan.json"

sys.path.insert(0, str(ROOT))


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        try:
            print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)
        except Exception:
            pass
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _instance():
    from core.instance import load
    return load(os.getenv("PINELINE_INSTANCE") or None)


def telegram_allowed() -> bool:
    """review2: script ops chỉ gửi Telegram khi instance bật telegram (v6/test: KHÔNG). Trước đây
    `setdefault(AGENT_TELEGRAM=1)` + force=True → chạy tay trong cây V6 vẫn bắn tin qua bot V2."""
    v = os.getenv("PINELINE_FLAG_TELEGRAM")
    if v is not None:
        return v.strip().lower() in ("1", "true", "yes", "on")
    try:
        return bool(_instance().telegram)
    except Exception:  # noqa: BLE001
        return False


def tg(msg: str) -> None:
    if not telegram_allowed():
        log(f"(telegram tắt theo instance) {msg[:200]}")
        return
    try:
        os.environ.setdefault("AGENT_TELEGRAM", "1")
        from agents.agent_watch import telegram_send
        telegram_send("Chuyển work: " + msg, force=True)
    except Exception as e:  # noqa: BLE001
        log(f"telegram lỗi: {e}")


def robocopy(label: str) -> None:
    DST.mkdir(parents=True, exist_ok=True)
    t = time.time()
    r = subprocess.run(
        ["robocopy", str(SRC), str(DST), "/MIR", "/COPY:DAT", "/DCOPY:DAT", "/R:2", "/W:2",
         "/MT:8", "/NP", "/NFL", "/NDL", "/XJ"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    tail = "\n".join((r.stdout or "").strip().splitlines()[-8:])
    log(f"robocopy {label}: exit={r.returncode} {time.time() - t:.0f}s\n{tail}")
    if r.returncode >= 8:
        raise RuntimeError(f"robocopy {label} lỗi exit={r.returncode}")


def api(method: str, url: str, body: dict | None = None, timeout: float = 30) -> dict:
    r = requests.request(method, url, json=body, timeout=timeout)
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return {"status": r.status_code, "raw": r.text[:300]}


def api_retry(method: str, url: str, body: dict | None = None, timeout: float = 30, tries: int = 10) -> dict:
    """Server đang render nặng có thể trễ >30s — không coi 1 lần timeout là lỗi chết."""
    last: Exception | None = None
    for i in range(tries):
        try:
            return api(method, url, body, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"API {url} lỗi ({e}) — thử lại {i + 1}/{tries}")
            time.sleep(15)
    raise RuntimeError(f"API {url} không trả lời sau {tries} lần: {last}")


def running_stages(root: Path | None = None) -> list[str]:
    out = []
    base = Path(root) if root is not None else SRC
    if not base.is_dir():
        return out
    for d in base.iterdir():
        mf = d / "manifest.json"
        if not mf.is_file():
            continue
        try:
            st = json.loads(mf.read_text(encoding="utf-8")).get("stages") or {}
        except Exception:  # noqa: BLE001
            continue
        for name, s in st.items():
            if isinstance(s, dict) and s.get("status") == "running":
                out.append(f"{d.name}:{name}")
    return out


def procs(pattern: str) -> list[psutil.Process]:
    res = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        cl = " ".join(p.info.get("cmdline") or [])
        if pattern in cl and p.pid != os.getpid():
            res.append(p)
    return res


def _norm(path) -> str:
    return os.path.normcase(os.path.realpath(str(path))).rstrip("\\/")


def same_root(p: psutil.Process, root: Path | None = None) -> bool:
    """review2: tiến trình có thuộc CÂY này không (cwd = ROOT, hoặc cmdline chứa đường ROOT).

    V2/V4/V5 chạy cùng tên script (server.py, agent_tele_cmd.py) ở cây khác — trước đây procs("agent_tele_cmd.py")
    + kill_with_parent_cmd giết luôn sidecar V2 :8802 production."""
    r = _norm(root or ROOT)
    try:
        cwd = p.cwd()
        if cwd and _norm(cwd) == r:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        cl = " ".join(p.cmdline() or [])
    except Exception:  # noqa: BLE001
        return False
    # có dấu phân cách cuối: Z:\\PinelineV6 không được khớp Z:\\PinelineV6_codebak
    return (r + os.sep) in os.path.normcase(cl.replace("/", os.sep))


def instance_procs(pattern: str, *, instance: str | None = None) -> list[psutil.Process]:
    """procs(pattern) nhưng CHỈ của cây này; `instance` → bỏ tiến trình chạy `--instance` khác."""
    res = []
    for p in procs(pattern):
        if not same_root(p):
            continue
        if instance is not None:
            try:
                toks = [str(x) for x in (p.cmdline() or [])]
            except Exception:  # noqa: BLE001
                continue
            if "--instance" in toks:
                i = toks.index("--instance")
                if i + 1 < len(toks) and toks[i + 1] != instance:
                    continue
        res.append(p)
    return res


def server_procs(port: int | None = None, instance: str | None = None) -> list[psutil.Process]:
    """Backend của instance (`-m app --instance X` / `server.py --port N`) — so token + cùng cây."""
    from core.instance import server_cmd_matches
    port = int(port or _server_port())
    res = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if p.pid == os.getpid():
            continue
        try:
            if server_cmd_matches(p.info.get("cmdline") or [], port=port, instance=instance) and same_root(p):
                res.append(p)
        except Exception:  # noqa: BLE001
            continue
    return res


def start_backend(name: str | None = None, env: dict | None = None) -> None:
    """Khởi động backend của instance (thay run_v2.bat/run_v5.bat — không có trong cây V6)."""
    name = name or os.getenv("PINELINE_INSTANCE") or "v6"
    subprocess.Popen([sys.executable, "-X", "utf8", "-m", "app", "--instance", name], cwd=str(ROOT), env=env,
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


def start_sidecar(name: str | None = None, env: dict | None = None) -> None:
    name = name or os.getenv("PINELINE_INSTANCE") or "v6"
    subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "agents" / "agent_tele_cmd.py"), "--instance", name],
                     cwd=str(ROOT), env=env, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


def kill_with_parent_cmd(p: psutil.Process) -> None:
    # procs() có thể trả cả cmd.exe bọc server (cùng chuỗi lệnh): kill python xong thì cmd tự
    # thoát → mọi lời gọi psutil sau đó trên tiến trình đã chết phải được bỏ qua.
    try:
        parent = p.parent()
        kids = p.children(recursive=True)
    except psutil.Error:
        return
    # psutil chỉ so create_time với gốc → PID tái dùng có thể làm tiến trình gọi thành "cháu".
    me = psutil.Process()
    try:
        keep = {me.pid, *(a.pid for a in me.parents())}
    except psutil.Error:
        keep = {me.pid}
    # worker con (TTS/ASR/ffmpeg) có thể giữ handle trong work/ → rename thất bại
    for c in kids:
        if c.pid in keep:
            continue
        try:
            c.kill()
        except psutil.Error:
            pass
    try:
        p.kill()
    except psutil.Error:
        pass
    try:
        if parent and parent.name().lower() == "cmd.exe" and ".bat" in " ".join(parent.cmdline()):
            parent.kill()
    except psutil.Error:
        pass


def start_bat(name: str) -> None:
    subprocess.Popen(
        ["cmd.exe", "/c", "start", "/min", "", str(ROOT / name)],
        cwd=str(ROOT), creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def wait_http(url: str, sec: int) -> bool:
    for _ in range(sec // 3):
        try:
            if requests.get(url, timeout=5).status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    return False


def start_services_and_requeue(pending: list[str], opts: dict, ap_was: bool) -> bool:
    from agents.agent_autopilot import set_enabled
    start_backend()        # review2: không còn run_v2.bat (cây V2) — backend của instance
    if not wait_http(BASE + "/api/agent/status", 180):
        tg("server không lên sau 3 phút — cần kiểm tra")
        return False
    start_sidecar()
    wait_http(SIDECAR + "/status", 60)
    i = 0
    while i < len(pending):
        o = opts[pending[i]]
        ids = [pending[i]]
        while i + len(ids) < len(pending) and opts[pending[i + len(ids)]] == o:
            ids.append(pending[i + len(ids)])
        api_retry("POST", BASE + "/api/queue/jobs", {"job_ids": ids, "skip_done": True, **o}, timeout=60)
        i += len(ids)
    if ap_was:
        set_enabled(True)
    PLAN.unlink(missing_ok=True)
    return True


def main() -> None:
    if SRC.is_junction() or SRC.is_symlink():
        log(f"work đã là junction → {os.path.realpath(SRC)}; không làm gì")
        return
    free = psutil.disk_usage(str(DST.anchor)).free
    need = sum(f.stat().st_size for f in SRC.rglob("*") if f.is_file())
    log(f"work={need / 1e9:.1f}GB · {DST.anchor} trống {free / 1e9:.0f}GB")
    if free < need * 1.1:
        raise SystemExit("ổ đích không đủ chỗ")

    from agents.agent_autopilot import load_state, set_enabled
    if PLAN.is_file():
        # chạy tiếp sau lần trước dừng giữa chừng: job đã rút + trạng thái Auto-pilot gốc nằm trong plan
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        pending = plan["pending"]
        ap_was = bool(plan["autopilot_was"])
        set_enabled(False)
        log(f"tiếp tục theo plan cũ: {len(pending)} job đã rút, Auto-pilot gốc={ap_was}")
        tg(f"chạy tiếp (lần trước dừng vì server trả lời chậm): giữ {len(pending)} job đã rút, chờ job hiện tại xong")
        robocopy("pre-copy (delta)")
    else:
        # 1. copy trước
        tg(f"bắt đầu copy trước {need / 1e9:.0f}GB sang {DST} (job hiện tại vẫn chạy)")
        robocopy("pre-copy")

        # 2. rút job đang chờ + tắt Auto-pilot
        ap_was = bool(load_state().get("enabled"))
        set_enabled(False)
        q = api_retry("GET", BASE + "/api/queue/jobs")
        pending = [str(x) for x in (q.get("queue") or [])]
        snaps = q.get("opts_by_job") or {}
        plan = {"pending": pending, "opts": {j: snaps.get(j) or q.get("opts") or {} for j in pending},
                "autopilot_was": ap_was, "current_at_start": q.get("current")}
        PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        if pending:
            api_retry("POST", BASE + "/api/queue/jobs/remove", {"job_ids": pending})
        log(f"rút {len(pending)} job chờ, Auto-pilot {ap_was}→off, đang chạy: {q.get('current')}")
        tg(f"đã tạm rút {len(pending)} job chờ + tắt Auto-pilot; chờ job {q.get('current') or '-'} chạy xong")

    # 3. chờ rảnh
    t0 = time.time()
    while True:
        q = api_retry("GET", BASE + "/api/queue/jobs", tries=40)
        rs = running_stages()
        if not q.get("current") and not q.get("worker") and not rs and not (q.get("queue") or []):
            break
        if int(time.time() - t0) % 600 < 20:
            log(f"chờ… current={q.get('current')} running={rs[:3]}")
        time.sleep(20)
    log(f"rảnh sau {(time.time() - t0) / 60:.0f} phút")

    # 4. dừng server + sidecar, copy nốt, junction
    tg("job xong — dừng server vài phút để chuyển nốt")
    # review2: chỉ tiến trình của CÂY này (không giết sidecar/server V2 trùng tên script)
    for p in instance_procs("agent_tele_cmd.py", instance=os.getenv("PINELINE_INSTANCE") or None):
        kill_with_parent_cmd(p)
    for p in server_procs():
        kill_with_parent_cmd(p)
    time.sleep(4)
    try:
        robocopy("final")
        renamed = False
        for _ in range(5):
            try:
                SRC.rename(OLD)
                renamed = True
                break
            except PermissionError as e:
                log(f"rename bận: {e} — thử lại")
                time.sleep(5)
        if not renamed:
            raise RuntimeError("không đổi tên được work/ (còn tiến trình giữ file)")
        r = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(SRC), str(DST)], capture_output=True, text=True)
        log(f"mklink: {r.returncode} {r.stdout.strip()} {r.stderr.strip()}")
        if r.returncode != 0:
            OLD.rename(SRC)
            raise RuntimeError("mklink lỗi — đã trả work về chỗ cũ")
    except Exception as e:  # noqa: BLE001
        log(f"chuyển thất bại, khởi động lại với work cũ: {e}")
        start_services_and_requeue(pending, plan["opts"], ap_was)
        tg(f"THẤT BẠI: {e}. Đã chạy lại server với work cũ ở Z: và xếp lại {len(pending)} job.")
        raise

    # 5. khởi động lại + xếp lại
    if not start_services_and_requeue(pending, plan["opts"], ap_was):
        raise RuntimeError("server không lên")
    q = api("GET", BASE + "/api/queue/jobs")
    log(f"xong · queue={q.get('queue')} current={q.get('current')} · Auto-pilot={'on' if ap_was else 'off'}")
    tg(f"XONG — work giờ ở {DST}. Đã xếp lại {len(pending)} job, Auto-pilot {'bật' if ap_was else 'tắt'}. "
       f"Bản cũ giữ tạm ở {OLD.name} để đối chiếu.")


if __name__ == "__main__":
    if LOG.is_file():
        LOG.replace(LOG.with_name("migrate_work.prev.log"))
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log(f"LỖI: {e}")
        tg(f"LỖI: {e} — xem migrate_work.log")
        raise
