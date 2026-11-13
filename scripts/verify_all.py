"""scripts/verify_all.py — kiểm toàn bộ V6 MỘT LẦN trên máy Windows (gọi từ verify_all.bat ở gốc cây).

KHÔNG chạy job thật, KHÔNG GPU/LLM, KHÔNG gọi cổng V2 (:8801/:8802), :8000, :8083. Chỉ dùng instance `test`
(cổng 8899, work ./scratch/test_work, mọi cờ gọi ra ngoài tắt).

Các bước:
  a) pytest test V6 (bỏ tests/v5)                 — gồm test_app_integration (cần fastapi)
  b) pytest tests/v5 (faulthandler 120 s)
  c) scripts/tree_audit.py                         — ERROR phải = 0
  d) smoke backend: `python -X utf8 -m app --instance test` ở nền → chờ cổng (≤120 s) → /api/health,
     /api/v6/jobs, /api/jobs, 3 s /api/events (phải có `event: snapshot`), /v6/, /, /ui3 (200; trang UI cũ
     không còn chuỗi :8801/:8802) → tắt ĐÚNG tiến trình đã khởi (PID lưu ở scratch/verify_all_backend.pid).
  e) bảng PASS/FAIL/SKIP.

Dùng:
  python scripts/verify_all.py                # chạy hết (Windows)
  python scripts/verify_all.py --only a,d     # chọn bước
  python scripts/verify_all.py --dry          # chế độ khô (cloud/CI): kiểm cấu hình + in lệnh, không chạy pytest/
                                              # tree_audit/backend; bước d tự SKIP khi thiếu fastapi
Mã thoát: 0 = không bước nào FAIL.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / "scratch"
FOREIGN_PORTS = {8000, 8083, 8801, 8802}
TEST_INSTANCE = "test"
WAIT_PORT_SEC = 120
SSE_READ_SEC = 3.0

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass


class Log:
    """In ra màn hình + ghi file log (UTF-8)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._fh = open(path, "a", encoding="utf-8") if path else None  # noqa: SIM115
        self._lock = threading.Lock()

    def __call__(self, msg: str = "") -> None:
        with self._lock:
            print(msg, flush=True)
            if self._fh:
                self._fh.write(msg + "\n")
                self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()


def _env() -> dict:
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "AGENT_BRAIN": "0", "AGENT_WATCH": "0",
                "AGENT_TELEGRAM": "0"})
    # Không để env của instance khác (vd shell đã chạy V2/V6) lọt vào test / backend thử.
    for k in list(env):
        if k.startswith("PINELINE_") or k in ("WORK_DIR", "AUTOPILOT_PORT", "AUTOPILOT_URL", "PORT"):
            env.pop(k, None)
    return env


def run_cmd(log: Log, cmd: list[str], *, timeout: float, dry: bool) -> tuple[str, str]:
    log("$ " + " ".join(cmd))
    if dry:
        return "SKIP", "khô (--dry)"
    t0 = time.time()
    try:
        p = subprocess.Popen(cmd, cwd=str(ROOT), env=_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return "FAIL", f"không chạy được: {e}"
    tail: list[str] = []

    def _pump() -> None:
        assert p.stdout is not None
        for line in p.stdout:
            line = line.rstrip("\r\n")
            log("  " + line)
            tail.append(line)
            del tail[:-5]
    th = threading.Thread(target=_pump, daemon=True)
    th.start()
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        th.join(5)
        return "FAIL", f"quá {int(timeout)} s"
    th.join(10)
    last = next((x for x in reversed(tail) if x.strip()), "")
    st = "PASS" if p.returncode == 0 else "FAIL"
    return st, f"mã {p.returncode} · {time.time() - t0:.0f} s · {last[:120]}"


# ───────────── bước a/b/c ─────────────

def step_pytest_v6(log: Log, dry: bool) -> tuple[str, str]:
    return run_cmd(log, [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                         "--basetemp=" + str(SCRATCH / "pytest_tmp"), "--ignore=" + str(Path("tests") / "v5"),
                         "tests"], timeout=1200, dry=dry)


def step_pytest_v5(log: Log, dry: bool) -> tuple[str, str]:
    return run_cmd(log, [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                         "-o", "faulthandler_timeout=120", "--basetemp=" + str(SCRATCH / "pytest_tmp_v5"),
                         str(Path("tests") / "v5")], timeout=2400, dry=dry)


def step_tree_audit(log: Log, dry: bool) -> tuple[str, str]:
    st, info = run_cmd(log, [sys.executable, "-X", "utf8", str(Path("scripts") / "tree_audit.py")],
                       timeout=900, dry=dry)
    return st, info


# ───────────── bước d: smoke backend ─────────────

def check_test_instance() -> list[str]:
    """instances/test.json phải: cổng 8899 (không cổng V2), work ./scratch/test_work, mọi cờ gọi ra ngoài tắt."""
    sys.path.insert(0, str(ROOT))
    from core.instance import load
    errs = []
    inst = load(TEST_INSTANCE)
    if inst.port != 8899:
        errs.append(f"port={inst.port} (cần 8899)")
    if inst.port in FOREIGN_PORTS or inst.sidecar_port in FOREIGN_PORTS:
        errs.append("cổng trùng V2/Hongguo/llama")
    wr = str(inst.work_root).replace("\\", "/")
    if not wr.rstrip("/").endswith("scratch/test_work"):
        errs.append(f"work_root={wr} (cần ./scratch/test_work)")
    fl = inst.flags()
    for k in ("agent_brain", "agent_watch", "telegram", "auto_publish", "hg_merge_resume", "title_translate",
              "queue_autostart", "feeder"):
        if fl.get(k):
            errs.append(f"cờ {k} đang BẬT")
    return errs


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


# Không đi qua proxy hệ thống (HTTP_PROXY…) — chỉ gọi 127.0.0.1.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url: str, timeout: float = 30.0) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "pineline-verify-all"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:  # noqa: S310 — chỉ 127.0.0.1
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace") if e.fp else ""
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def read_sse(url: str, seconds: float = SSE_READ_SEC) -> str:
    """Đọc luồng SSE tối đa `seconds` giây (dừng sớm khi đã thấy snapshot)."""
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream", "User-Agent": "pineline-verify-all"})
    buf: list[str] = []
    t0 = time.time()
    try:
        with _OPENER.open(req, timeout=seconds + 2) as r:
            while time.time() - t0 < seconds:
                line = r.readline()
                if not line:
                    break
                s = line.decode("utf-8", errors="replace")
                buf.append(s)
                if s.startswith("event: snapshot"):
                    break
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        buf.append(f"<lỗi {e}>")
    return "".join(buf)


def kill_backend(p: subprocess.Popen, log: Log) -> None:
    """Tắt ĐÚNG tiến trình đã khởi (+ con của nó). Không đụng python khác."""
    if p.poll() is not None:
        return
    try:
        import psutil  # type: ignore
        try:
            kids = psutil.Process(p.pid).children(recursive=True)
        except psutil.Error:
            kids = []
        for k in kids:
            try:
                k.kill()
            except psutil.Error:
                pass
    except ImportError:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, check=False)
    try:
        p.kill()
    except OSError:
        pass
    try:
        p.wait(15)
    except subprocess.TimeoutExpired:
        log(f"  ! backend pid={p.pid} chưa thoát sau 15 s")


def step_smoke(log: Log, dry: bool, *, backend_cmd: list[str] | None = None, port: int | None = None,
               wait_sec: float = WAIT_PORT_SEC) -> tuple[str, str, list[tuple[str, str, str]]]:
    """Trả (trạng thái, ghi chú, danh sách kiểm con)."""
    subs: list[tuple[str, str, str]] = []
    errs = check_test_instance()
    subs.append(("instances/test.json", "FAIL" if errs else "PASS", "; ".join(errs) or "8899 · scratch/test_work · cờ ngoài tắt"))
    if errs:
        return "FAIL", "instances/test.json sai", subs
    if backend_cmd is None:
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError:
            subs.append(("backend", "SKIP", "thiếu fastapi/uvicorn (cloud)"))
            return "SKIP", "thiếu fastapi/uvicorn", subs
        backend_cmd = [sys.executable, "-X", "utf8", "-m", "app", "--instance", TEST_INSTANCE]
    port = port or 8899
    log("$ " + " ".join(backend_cmd) + "   (nền)")
    if dry:
        subs.append(("backend", "SKIP", "khô (--dry)"))
        return "SKIP", "khô (--dry)", subs
    if port in FOREIGN_PORTS:
        return "FAIL", f"từ chối cổng {port}", subs
    if port_open(port):
        subs.append(("cổng", "FAIL", f":{port} đã có tiến trình khác — không khởi, không kill"))
        return "FAIL", f"cổng {port} bận", subs
    SCRATCH.mkdir(exist_ok=True)
    blog_path = SCRATCH / "verify_all_backend.log"
    blog = open(blog_path, "w", encoding="utf-8")  # noqa: SIM115
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = 0x0000_0200 | 0x0800_0000   # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    p = subprocess.Popen(backend_cmd, cwd=str(ROOT), env=_env(), stdout=blog, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, **kw)
    pid_file = SCRATCH / "verify_all_backend.pid"
    pid_file.write_text(str(p.pid), encoding="utf-8")
    log(f"  backend pid={p.pid} (lưu {pid_file.relative_to(ROOT)}) · log {blog_path.relative_to(ROOT)}")
    base = f"http://127.0.0.1:{port}"
    try:
        t0 = time.time()
        while not port_open(port):
            if p.poll() is not None:
                subs.append(("khởi động", "FAIL", f"backend thoát mã {p.returncode} — xem {blog_path.name}"))
                return "FAIL", "backend không lên", subs
            if time.time() - t0 > wait_sec:
                subs.append(("khởi động", "FAIL", f"cổng {port} chưa mở sau {int(wait_sec)} s"))
                return "FAIL", "quá thời gian chờ cổng", subs
            time.sleep(1)
        subs.append(("khởi động", "PASS", f"cổng mở sau {time.time() - t0:.0f} s"))
        time.sleep(1.0)    # startup event (index/watcher) xong

        def _json(path: str, check) -> None:
            code, body = http_get(base + path)
            ok, note = code == 200, f"HTTP {code}"
            if ok:
                try:
                    data = json.loads(body)
                    ok = bool(check(data))
                    note += "" if ok else f" · nội dung lạ: {body[:100]}"
                except ValueError:
                    ok, note = False, note + " · không phải JSON"
            subs.append((path, "PASS" if ok else "FAIL", note))

        _json("/api/health", lambda d: d.get("ok") is True and d.get("instance") == TEST_INSTANCE)
        _json("/api/v6/jobs", lambda d: isinstance(d.get("jobs"), list))
        _json("/api/jobs", lambda d: isinstance(d, (list, dict)))
        sse = read_sse(base + "/api/events")
        subs.append(("/api/events", "PASS" if "event: snapshot" in sse else "FAIL",
                     "có event: snapshot" if "event: snapshot" in sse else f"không thấy snapshot: {sse[:100]!r}"))
        for path, need_clean in (("/v6/", False), ("/", True), ("/ui3", True)):
            code, body = http_get(base + path)
            bad = [s for s in (":8801", ":8802") if s in body] if need_clean else []
            ok = code == 200 and not bad
            note = f"HTTP {code}" + (f" · còn {', '.join(bad)}" if bad else "") + (
                " · không còn :8801/:8802" if need_clean and ok else "")
            subs.append((path, "PASS" if ok else "FAIL", note))
    finally:
        kill_backend(p, log)
        blog.close()
        alive = p.poll() is None
        subs.append(("tắt backend", "FAIL" if alive else "PASS", f"pid={p.pid}" + (" còn sống" if alive else " đã tắt")))
        try:
            pid_file.unlink()
        except OSError:
            pass
    st = "FAIL" if any(s == "FAIL" for _, s, _ in subs) else "PASS"
    return st, f"{sum(s == 'PASS' for _, s, _ in subs)}/{len(subs)} kiểm con PASS", subs


# ───────────── main ─────────────

STEPS = {
    "a": ("pytest V6 (bỏ tests/v5)", step_pytest_v6),
    "b": ("pytest tests/v5", step_pytest_v5),
    "c": ("tree_audit", step_tree_audit),
    "d": ("smoke backend instance test", None),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry", action="store_true", help="chế độ khô: không chạy pytest/tree_audit/backend")
    ap.add_argument("--only", default="abcd", help="bước cần chạy, vd a,d")
    ap.add_argument("--no-log", action="store_true", help="không ghi scratch/verify_all_*.log")
    ap.add_argument("--backend-cmd", default=None, help="(test) lệnh backend thay `-m app --instance test`, JSON list")
    ap.add_argument("--port", type=int, default=None, help="(test) cổng backend thay 8899")
    ap.add_argument("--wait", type=float, default=WAIT_PORT_SEC)
    a = ap.parse_args(argv)
    only = [c for c in a.only.replace(",", "").lower() if c in STEPS]
    SCRATCH.mkdir(exist_ok=True)
    log_path = None if a.no_log else SCRATCH / f"verify_all_{datetime.now():%Y%m%d_%H%M%S}.log"
    log = Log(log_path)
    log(f"== Pineline V6 verify_all · {datetime.now():%Y-%m-%d %H:%M:%S} · python {sys.version.split()[0]}"
        f" · {'KHÔ' if a.dry else 'THẬT'} · bước {','.join(only)}")
    log(f"   gốc {ROOT}" + (f" · log {log_path}" if log_path else ""))
    rows: list[tuple[str, str, str]] = []
    subs: list[tuple[str, str, str]] = []
    for key in only:
        title, fn = STEPS[key]
        log(f"\n── {key}) {title}")
        if key == "d":
            cmd = json.loads(a.backend_cmd) if a.backend_cmd else None
            st, note, subs = step_smoke(log, a.dry, backend_cmd=cmd, port=a.port, wait_sec=a.wait)
            for name, s, n in subs:
                log(f"   [{s}] {name} — {n}")
        else:
            st, note = fn(log, a.dry)
        rows.append((f"{key}) {title}", st, note))
    log("\n== KẾT QUẢ")
    w = max(len(r[0]) for r in rows) if rows else 10
    for name, st, note in rows:
        log(f"  {name.ljust(w)}  {st:<4}  {note}")
    if subs:
        log("  d) chi tiết:")
        for name, s, n in subs:
            log(f"       {s:<4} {name} — {n}")
    n_fail = sum(st == "FAIL" for _, st, _ in rows)
    log(f"\nTỔNG: {'PASS' if not n_fail else 'FAIL'} ({n_fail} bước FAIL)" + (f" · log {log_path}" if log_path else ""))
    log.close()
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
