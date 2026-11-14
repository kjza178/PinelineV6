"""app/main.py — FastAPI app V6 (route mỏng; logic ở state/pipeline/services).

F3 (tích hợp): MỘT tiến trình, MỘT cổng instance phục vụ:
  - toàn bộ route V5 của `server.py` (legacy: /api/jobs, /api/queue/…, /, /ui3 = index3.html …);
  - route V6: /api/health, /api/events (SSE), /api/v6/jobs, /api/v6/jobs/{id} (chỉ đọc, KHÔNG side effect);
  - UI desktop mới ở /v6/ (desktop/ui) — Tauri mở /v6/.

Cách gắn: `import server` (một lần, có kiểm soát: env instance đã đặt TRƯỚC khi import) rồi gắn route V6
lên chính `server.app`. Không có fastapi route nào trùng: route V6 cũ /api/jobs → /api/v6/jobs.

`create_app(inst, legacy=False)` = app V6 đứng riêng (như F0, UI mới ở "/") — dùng cho test / khi không
muốn nạp server.py.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse

from core.instance import Instance
from events.bus import bus
from runtime.context import Runtime
from state import job_state
from state.index import index
from state.watcher import ManifestWatcher

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "desktop" / "ui"
V6_PREFIX = "/api/v6"


DISK_WARN_GB = 20.0      # dưới mức này /api/health cảnh báo (một phim dài cần hàng chục GB tạm)
_VERSIONS: dict | None = None


def _disk_free(path) -> dict | None:
    """Dung lượng ổ chứa `path` (đi ngược lên thư mục cha gần nhất đang tồn tại). Lỗi → None."""
    import shutil
    try:
        p = Path(path).resolve()
        while not p.exists() and p != p.parent:
            p = p.parent
        u = shutil.disk_usage(p)
        return {"path": str(path), "free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1)}
    except OSError:
        return None


def _versions() -> dict:
    """Phiên bản Python / ffmpeg (dò 1 lần mỗi tiến trình). ffmpeg đổi bản từng làm hỏng render (R6.1)."""
    global _VERSIONS
    if _VERSIONS is None:
        import platform
        import subprocess
        ff = None
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            first = (r.stdout or "").splitlines()[0] if r.returncode == 0 and r.stdout else ""
            ff = first.replace("ffmpeg version", "").strip().split(" ")[0] or None
        except (OSError, subprocess.SubprocessError):
            ff = None
        _VERSIONS = {"python": platform.python_version(), "ffmpeg": ff, "os": platform.platform()}
    return _VERSIONS


def _import_legacy(inst: Instance):
    """Nạp server.py (V5) cho instance này. Env (WORK_DIR, PINELINE_FLAG_*, PINELINE_ROUTERS…) phải có
    TRƯỚC khi import: server đọc chúng lúc import (WORK_ROOT, router tuỳ chọn) và lúc startup (cờ nền)."""
    if os.environ.get("PINELINE_INSTANCE") != inst.name:
        inst.apply_env()
    import server  # noqa: PLC0415 — cố ý import muộn
    want = Path(inst.work_root)
    if Path(server.WORK_ROOT) != want:
        # server đã được import trước đó với work root khác (vd test) → căn lại cho instance này
        server.WORK_ROOT = want
        server.job_queue.WORK_ROOT = want
    return server


def _v6_router(inst: Instance) -> APIRouter:
    r = APIRouter()

    @r.get("/api/health")
    def health():
        """Sức khoẻ backend cho người / giám sát (R3.1, R6.2). Chỉ đọc; mọi phần phụ lỗi → null, không làm hỏng cả câu trả lời."""
        out = {"ok": True, "instance": inst.name, "port": inst.port, "jobs": len(index.list()),
               "sse_clients": bus.n_subscribers}
        try:
            import core.job_queue as job_queue
            st = job_queue.queue_state()
            out["queue"] = {"current": st.get("current"), "waiting": len(st.get("queue") or []),
                            "worker": bool(st.get("worker")), "paused": st.get("paused"),
                            "persist_ok": st.get("persist_ok"), "consec_fail": st.get("consec_fail")}
        except Exception as e:  # noqa: BLE001
            out["queue"] = None
            print(f"[health] queue: {e}", flush=True)
        out["disk"] = {"work": _disk_free(inst.work_root), "temp": _disk_free(inst.temp)}
        out["versions"] = _versions()
        # cảnh báo gọn để UI / giám sát chỉ cần nhìn một chỗ
        warn = []
        q = out.get("queue") or {}
        if q.get("paused"):
            warn.append("hàng chờ đang tạm dừng vì lỗi liên tiếp")
        if q.get("persist_ok") is False:
            warn.append("không ghi được hàng chờ ra đĩa")
        for name, d in out["disk"].items():
            if d and d["free_gb"] < DISK_WARN_GB:
                warn.append(f"ổ {name} còn {d['free_gb']} GB")
        out["warnings"] = warn
        return out

    @r.get(V6_PREFIX + "/jobs")
    def jobs():
        return {"jobs": index.list()}

    @r.get(V6_PREFIX + "/jobs/{job_id}")
    def job(job_id: str):
        # Chỉ đọc file — không heal, không ghép parent, không ghi gì (luật: không side effect ở GET).
        if "/" in job_id or "\\" in job_id or job_id in ("", ".", ".."):
            raise HTTPException(404, "không có job")
        mf = inst.work_root / job_id / "manifest.json"
        if not mf.is_file():
            raise HTTPException(404, "không có job")
        return json.loads(mf.read_text(encoding="utf-8"))

    # ── hàng chờ: tạm dừng khi lỗi liên tiếp (R7.4) + nhật ký ra file (R7.5) ──
    @r.post(V6_PREFIX + "/queue/resume")
    def queue_resume():
        """Người dùng đã xem lỗi → gỡ tạm dừng, chạy tiếp hàng chờ."""
        import core.job_queue as job_queue
        out = job_queue.queue_resume()
        bus.publish("queue.changed", {"text": "chạy tiếp hàng chờ"})
        return out

    @r.get(V6_PREFIX + "/queue/history")
    def queue_history(limit: int = 100):
        import core.job_queue as job_queue
        return {"history": job_queue.queue_history(limit)}

    @r.get("/api/events")
    def events(request: Request):
        # Generator ASYNC (fix 2026-11-13): chờ event bằng await trong event loop → KHÔNG giữ thread nào.
        # Bản generator đồng bộ trước đây chiếm 1 thread của threadpool (40) cho MỖI client suốt kết nối;
        # ~40 kết nối (nhiều tab + EventSource tự nối lại) là mọi route `def` của server.py đứng.
        # Client ngắt: Starlette huỷ generator → finally trong bus.sse_stream gỡ subscriber.
        # (Không gọi request.is_disconnected() ở đây: tranh receive() với StreamingResponse → stream đứt.)
        last = request.headers.get("last-event-id")
        since = int(last) if last and last.isdigit() else None

        async def gen():
            sub = bus.subscribe_async(since=since)       # đăng ký TRƯỚC khi chụp snapshot → không lọt event
            try:
                snap = json.dumps({"jobs": index.list()}, ensure_ascii=False)
                yield f"event: snapshot\ndata: {snap}\n\n"
                async for chunk in bus.sse_stream(sub, keepalive_sec=15.0):
                    yield chunk
            finally:                                     # client ngắt ngay sau snapshot cũng phải gỡ
                sub.closed = True
                bus.unsubscribe(sub)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return r


def _install_lifecycle(app: FastAPI, inst: Instance, rt: Runtime, *, legacy: bool) -> None:
    @app.on_event("startup")
    def _v6_startup() -> None:
        inst.work_root.mkdir(parents=True, exist_ok=True)
        if not legacy:
            # Legacy: server._reset_stale_running (chạy TRƯỚC, đăng ký lúc import) đã reset + sửa TTS
            # → không reset lần 2.
            for d in inst.work_root.iterdir():
                if (d / "manifest.json").is_file():
                    job_state.reset_running(d)
        n = index.load_all(inst.work_root)
        app.state.watcher = ManifestWatcher(inst.work_root)
        app.state.watcher.start()
        rt.start_pools()
        stop = threading.Event()
        app.state.flush_stop = stop

        def _flusher():
            while not stop.wait(0.5):
                bus.flush()
        threading.Thread(target=_flusher, name="bus-flush", daemon=True).start()
        print(f"[v6] {inst.name} :{inst.port} — {n} job trong index"
              + (" (+ route V5 server.py)" if legacy else ""))

    @app.on_event("shutdown")
    def _v6_shutdown() -> None:
        stop = getattr(app.state, "flush_stop", None)
        if stop is not None:
            stop.set()
        w = getattr(app.state, "watcher", None)
        if w is not None:
            w.stop()
        rt.shutdown()
        index.unbind()      # fix 11-12 agentJ: nhả work root instance (index dùng chung trong tiến trình)


def create_app(inst: Instance, *, legacy: bool = True):
    """App của instance. legacy=True (mặc định, F3): server.py + route V6 trên cùng app/cổng."""
    if legacy:
        server = _import_legacy(inst)
        app = server.app
        done = getattr(app.state, "v6_instance", None)
        if done is not None:
            # Gắn 2 lần = startup chạy 2 lần (watcher, flusher…) → chặn.
            if done != inst.name:
                raise RuntimeError(f"server.app đã gắn instance {done!r}, không gắn thêm {inst.name!r}")
            return app
        app.state.v6_instance = inst.name
    else:
        app = FastAPI(title=f"Pineline {inst.name}")

    rt = Runtime(inst)
    app.state.runtime = rt
    try:
        app.add_middleware(CORSMiddleware, allow_origins=["tauri://localhost", "http://tauri.localhost",
                                                          inst.base_url], allow_methods=["*"], allow_headers=["*"])
    except RuntimeError as e:   # server.app đã chạy (vd test V5 dùng trước) → không thêm middleware được
        print(f"[v6] bỏ CORS middleware: {e}")
    app.include_router(_v6_router(inst))
    _install_lifecycle(app, inst, rt, legacy=legacy)

    if UI_DIR.is_dir():
        from fastapi.staticfiles import StaticFiles
        if legacy:
            # "/" là UI cũ của server.py → UI desktop mới ở /v6/
            @app.get("/v6", include_in_schema=False)
            def _v6_slash():
                return RedirectResponse("/v6/")
            app.mount("/v6", StaticFiles(directory=str(UI_DIR), html=True), name="ui_v6")
        else:
            app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
    return app
