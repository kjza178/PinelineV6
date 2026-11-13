"""workers/render_worker.py — Render/mux chạy TIẾN TRÌNH CON (quyết định 11-11: render subprocess).

Vì sao: V5 render trong tiến trình server và `importlib.reload` core.config/engines.subs/render/assemble
mỗi lần mux để sửa code có hiệu lực không cần restart (reload không thay được object cũ đang được
module khác giữ, dễ lệch phiên bản). V6 F5: server spawn
`python -m workers.render_worker <work_dir> <opts_json>` → mỗi job là tiến trình Python mới, import
code render MỚI NHẤT → sửa render có hiệu lực ở job kế, không reload, không restart :8806.

Logic render: `engines.render_job.do_render` — CÙNG hàm mà đường in-process cũ (cờ `render_subprocess`
tắt) gọi. Tiến độ: run_render tự ghi manifest qua state.job_state (tiến trình con) → watcher của server
bắt mtime → index + SSE. stdout chỉ thêm sự kiện điều khiển:

Giao thức stdout (mỗi dòng 1 JSON; dòng khác là log thường, server in ra):
  {"ev":"start","pid":123,"work_dir":"..."}
  {"ev":"progress","percent":42,"cur_text":"..."}      (tuỳ chọn — render thật ghi manifest)
  {"ev":"done","output":"D:/.../output.mp4"}            (output=null: bỏ qua vì mux đã xong)
  {"ev":"error","error":"...","etype":"RuntimeError"}

Test (không ffmpeg/GPU): env `PINELINE_RENDER_IMPL=module:func` thay hàm render thật; opts
`{"dry_run": true}` giả lập 3 bước tiến độ.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parents[1]
MODULE = "workers.render_worker"
STOPPED_MSG = "đã dừng theo yêu cầu"


def emit(ev: str, **kw) -> None:
    print(json.dumps({"ev": ev, **kw}, ensure_ascii=False), flush=True)


def parse_lines(lines: Iterator[str], on_line: Callable[[str], None] | None = None) -> Iterator[dict]:
    """Chỉ lấy JSON có 'ev'; dòng log thường (ffmpeg, print) → on_line (nếu có)."""
    for raw in lines:
        line = raw.strip()
        obj = None
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except ValueError:
                obj = None
        if isinstance(obj, dict) and "ev" in obj:
            yield obj
        elif line and on_line is not None:
            on_line(line)


def child_env(base: dict | None = None) -> dict:
    """Env cho tiến trình con: root trên PYTHONPATH, stdout UTF-8, bỏ PYTHONHASHSEED hỏng (như _run_engine)."""
    env = dict(os.environ if base is None else base)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = os.pathsep.join(x for x in (str(ROOT), env.get("PYTHONPATH", "")) if x)
    hs = env.get("PYTHONHASHSEED")
    if hs is not None and not (hs == "random" or (hs.isdigit() and 0 <= int(hs) <= 4294967295)):
        env.pop("PYTHONHASHSEED", None)
    env.setdefault("TQDM_DISABLE", "1")
    return env


def kill_tree(proc, *, grace: float = 0.0) -> None:
    """Kill tiến trình + MỌI hậu duệ (review2).

    `Popen.terminate()` trên Windows = TerminateProcess CHỈ giết python worker: ffmpeg/demucs cháu sống
    tiếp (render chạy ngầm, ghi output, giữ GPU) và — nếu cháu kế thừa stdout — giữ đầu ghi của pipe →
    `readline()` phía server không bao giờ EOF → luồng render/GPU kẹt tới khi cháu tự xong.
    Thứ tự: chụp danh sách cháu TRƯỚC (cha chết rồi thì không lần ra được nữa) → kill cháu → kill cha.
    psutil (có sẵn ở server); không có psutil: Windows `taskkill /T /F`, POSIX killpg (spawn tạo session riêng)."""
    pid = getattr(proc, "pid", None)
    alive = False
    try:
        alive = proc.poll() is None
    except Exception:  # noqa: BLE001
        alive = False
    # Chỉ lần cây khi proc là Popen THẬT còn sống (PID chưa bị tái dùng) — không bao giờ PID 0/1/chính mình
    # (đối tượng giả/hỏng có pid=1 → children(PID 1) = cả máy).
    if (not isinstance(proc, subprocess.Popen) or not alive or not isinstance(pid, int)
            or pid <= 1 or pid == os.getpid()):
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        import psutil
    except Exception:  # noqa: BLE001
        psutil = None
    if psutil is not None:
        try:
            root = psutil.Process(pid)
            kids = root.children(recursive=True)
        except Exception:  # noqa: BLE001
            kids = []
        if grace > 0:
            try:
                proc.terminate()
                proc.wait(timeout=grace)
            except Exception:  # noqa: BLE001
                pass
        for c in reversed(kids):
            try:
                c.kill()
            except Exception:  # noqa: BLE001
                pass
    elif os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            import signal
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def spawn(work_dir: Path, *, opts: dict | None = None, python: str | None = None,
          on_event: Callable[[dict], None] | None = None,
          is_stopped: Callable[[], bool] = lambda: False, module: str = MODULE,
          env: dict | None = None, on_start: Callable[[subprocess.Popen], None] | None = None,
          on_line: Callable[[str], None] | None = None,
          poll_sec: float = 0.5, stop_grace: float = 3.0) -> dict:
    """Phía server: chạy worker, chuyển sự kiện cho on_event; trả sự kiện cuối (done/error).

    Dừng: luồng phụ hỏi `is_stopped()` mỗi `poll_sec` (không phụ thuộc worker có in gì) →
    terminate, quá `stop_grace` giây thì kill. Kết quả: {"ev":"error","stopped":True,...}.
    `on_start(proc)`: server đăng ký proc (vd `_PROCS[job_id]`) để nút Dừng kill được.
    """
    # review2: đường TUYỆT ĐỐI — con chạy cwd=ROOT; WORK_ROOT tương đối ("work", kiểu V5) mà server
    # chạy cwd khác thì con sẽ render nhầm thư mục.
    cmd = [python or sys.executable, "-m", module, str(Path(work_dir).resolve()),
           json.dumps(opts or {}, ensure_ascii=False)]
    extra: dict = {}
    if os.name != "nt":
        extra["start_new_session"] = True     # nhóm tiến trình riêng → killpg được cả cây (fallback)
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=child_env(env), stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", bufsize=1, **extra)
    if on_start is not None:
        on_start(p)
    stopped = threading.Event()
    finished = threading.Event()

    def _watch_stop() -> None:
        while not finished.wait(poll_sec):
            try:
                hit = is_stopped()
            except Exception:  # noqa: BLE001
                hit = False
            if hit:
                stopped.set()
                kill_tree(p, grace=stop_grace)   # review2: cả cây (ffmpeg/demucs cháu), không chỉ python
                return

    watcher = threading.Thread(target=_watch_stop, name=f"render-stop-{Path(work_dir).name}", daemon=True)
    watcher.start()
    last: dict = {"ev": "error", "error": "worker thoát không báo kết quả"}
    try:
        assert p.stdout is not None
        for ev in parse_lines(iter(p.stdout.readline, ""), on_line):
            if on_event:
                on_event(ev)
            if ev["ev"] in ("done", "error"):
                last = ev
    finally:
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            kill_tree(p)
            p.wait(timeout=10)
        finished.set()
    if stopped.is_set() or is_stopped():
        return {"ev": "error", "error": STOPPED_MSG, "stopped": True}
    if p.returncode not in (0, None) and last["ev"] != "error":
        last = {"ev": "error", "error": f"render worker thoát mã {p.returncode}"}
    elif p.returncode not in (0, None) and last.get("error") == "worker thoát không báo kết quả":
        last = {"ev": "error", "error": f"render worker thoát mã {p.returncode} không báo kết quả"}
    return last


class RenderStopped(Exception):
    """Người dùng bấm Dừng trong lúc worker chạy (server đổi thành _Stopped)."""


def run_job(work_dir: Path, kw: dict, *, procs: dict | None = None,
            is_stopped: Callable[[], bool] = lambda: False,
            on_line: Callable[[str], None] | None = None, python: str | None = None,
            env: dict | None = None, **spawn_kw) -> str | None:
    """Phía server (không cần fastapi → test được): chạy 1 lần render ở tiến trình con.

    - `procs` (vd server._PROCS): đăng ký proc theo job_id → nút Dừng (_stop_job) terminate/kill được;
      gỡ khi xong. Còn proc cũ cùng job đang sống → kill trước (chống 2 render cùng work).
    - Sự kiện `progress` → state.job_state.set_stage('mux', progress, cur_text).
    - Dừng → raise RenderStopped. Lỗi → js.mark_failed('mux', thông báo worker) rồi raise RuntimeError
      (worker cũng tự mark_failed trong do_render; ghi lại cần cho trường hợp worker chết không kịp ghi).
    Trả output (đường output.mp4, hoặc None nếu bỏ qua vì mux đã xong)."""
    from state import job_state as js
    work_dir = Path(work_dir)
    jid = work_dir.name
    if is_stopped():
        raise RenderStopped(jid)
    holder: dict = {}

    def _on_start(proc) -> None:
        holder["p"] = proc
        if procs is None:
            return
        old = procs.get(jid)
        if old is not None and old is not proc and getattr(old, "poll", lambda: 0)() is None:
            print(f"[proc] render: còn tiến trình cũ của {jid} pid={old.pid} — kill", flush=True)
            kill_tree(old)
        procs[jid] = proc
        print(f"[proc] render_worker {jid} pid={proc.pid}", flush=True)

    def _on_event(ev: dict) -> None:
        if ev.get("ev") == "progress":
            try:
                js.set_stage(work_dir, "mux", progress=ev.get("percent"),
                             cur_text=ev.get("cur_text") or "đang render…")
            except Exception:  # noqa: BLE001
                pass

    from state.watcher import watching
    try:
        # F6: watcher của server theo dõi job NGAY (worker ghi tiến độ thẳng manifest) — không chờ quét 30 s.
        with watching(jid):
            last = spawn(work_dir, opts=kw, python=python, env=env, on_event=_on_event, on_line=on_line,
                         on_start=_on_start, is_stopped=is_stopped, **spawn_kw)
    finally:
        p = holder.get("p")
        if procs is not None and p is not None and procs.get(jid) is p:
            procs.pop(jid, None)
    if last.get("stopped"):
        raise RenderStopped(jid)
    if last.get("ev") != "done":
        msg = str(last.get("error") or "render worker lỗi")
        try:
            js.mark_failed(work_dir, "mux", msg)
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(msg)
    return last.get("output")


def _impl():
    """Hàm render: mặc định engines.render_job.do_render; test đặt PINELINE_RENDER_IMPL=module:func."""
    spec = (os.getenv("PINELINE_RENDER_IMPL") or "").strip()
    if spec:
        import importlib
        mod, _, fn = spec.partition(":")
        return getattr(importlib.import_module(mod), fn or "do_render")
    from engines.render_job import do_render
    return do_render


def main(argv: list[str]) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if len(argv) < 2:
        emit("error", error="usage: render_worker <work_dir> [opts_json]")
        return 2
    wd = Path(argv[1])
    try:
        opts = json.loads(argv[2]) if len(argv) > 2 else {}
    except ValueError as e:
        emit("error", error=f"opts_json hỏng: {e}")
        return 2
    emit("start", pid=os.getpid(), work_dir=str(wd))
    try:
        if opts.pop("dry_run", False):
            for i in range(1, 4):
                emit("progress", percent=i * 33, cur_text=f"dry-run {i}/3")
            emit("done", output=str(wd / "output.mp4"))
            return 0
        out = _impl()(wd, **opts)
        emit("done", output=(str(out) if out else None))
        return 0
    except BaseException as e:  # noqa: BLE001 — cả KeyboardInterrupt/SystemExit: luôn báo lý do
        emit("error", error=str(e)[:1000] or type(e).__name__, etype=type(e).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
