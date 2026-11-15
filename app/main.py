"""app/main.py — FastAPI app V6 (route mỏng; logic ở state/pipeline/services).

F3 (tích hợp): MỘT tiến trình, MỘT cổng instance phục vụ:
  - toàn bộ route V5 của `server.py` (legacy: /api/jobs, /api/queue/…, / = /ui3 = Studio (index.html) …);
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
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

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
    """Dung lượng trống ổ chứa `path` — dùng engines.split_parts.free_bytes (đã tự lần lên thư mục cha có thật)."""
    try:
        from engines.split_parts import free_bytes
        return {"path": str(path), "free_gb": round(free_bytes(path) / 1e9, 1)}
    except Exception as e:  # noqa: BLE001
        print(f"[health] không đọc được dung lượng ổ {path}: {e}", flush=True)
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
        except (OSError, subprocess.SubprocessError) as e:
            print(f"[health] không chạy được `ffmpeg -version`: {e}", flush=True)
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


def versioned_index(ui_dir: Path) -> str:
    """index.html của UI desktop với app.css / app.js gắn `?v=<giờ sửa file>` — fix 2026-11-15: WebView2 của app desktop
    giữ app.js CŨ trong cache (server cũ không gửi Cache-Control) nên `git pull` xong vẫn thấy giao diện cũ. Đổi file =
    đổi URL → cache cũ không bao giờ được dùng nhầm."""
    html = (ui_dir / "index.html").read_text(encoding="utf-8")
    for name in ("app.css", "app.js"):
        try:
            v = int((ui_dir / name).stat().st_mtime)
        except OSError as e:
            print(f"[v6] không đọc được giờ sửa {name}: {e}", flush=True)
            continue
        html = html.replace(f'"{name}"', f'"{name}?v={v}"')
    return html


_UI_PAGES = {"/", "/ui3", "/index3.html", "/studio", "/studio/ui", "/films/ui", "/film", "/hongguo2", "/v5-map", "/v6"}


def _is_ui_path(path: str) -> bool:
    return (path in _UI_PAGES or path.startswith("/v6/") or path.startswith("/static/")
            or path.endswith((".html", ".js", ".css")))


class NoCacheUI:
    """fix 2026-11-15: file giao diện (desktop /v6/, Studio, trang Phim, Hongguo 2, /static) trả kèm
    `Cache-Control: no-cache` — trước đây không có header nên WebView2 của app desktop / trình duyệt có thể dùng
    app.js CŨ sau khi cập nhật code («desktop vẫn dính build cũ»). no-cache = vẫn lưu nhưng hỏi lại server mỗi lần
    (ETag → 304, rẻ). ASGI thuần (không BaseHTTPMiddleware) nên không đụng luồng SSE /api/events."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not _is_ui_path(scope.get("path") or ""):
            return await self.app(scope, receive, send)

        async def send_nc(msg):
            if msg.get("type") == "http.response.start":
                hs = [(k, v) for k, v in msg.get("headers") or [] if k.lower() != b"cache-control"]
                hs.append((b"cache-control", b"no-cache"))
                msg = {**msg, "headers": hs}
            await send(msg)
        return await self.app(scope, receive, send_nc)


def _v6_router(inst: Instance) -> APIRouter:
    r = APIRouter()

    @r.get("/api/health")
    def health():
        """Sức khoẻ backend cho người / giám sát (R3.1, R6.2). Chỉ đọc; mọi phần phụ lỗi → null, không làm hỏng cả câu trả lời."""
        rows = index.list()
        out = {"ok": True, "instance": inst.name, "port": inst.port, "jobs": len(rows),
               "running": sum(1 for r in rows if r.get("status") == "running"),   # app desktop hỏi trước khi đóng
               "routers": list(inst.routers), "sse_clients": bus.n_subscribers}
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

    # ── D4: cấu hình batch (giọng, tốc độ, âm nền, render…) — trước đây chỉ sửa được qua sidecar :8802/config ──
    @r.get(V6_PREFIX + "/config")
    def config_get():
        import core.batch_config as bc
        from core import audio_levels
        return {"opts": bc.load(), "defaults": bc.DEFAULTS, "choices": {k: list(v) for k, v in bc.CHOICES.items()},
                "labels": bc.LABELS, "levels": audio_levels.snapshot(), **bc.meta()}

    @r.post(V6_PREFIX + "/config")
    def config_set(body: dict):
        """{"patch": {khoá: giá trị}, "levels": {dub|bgm|lib_music|sfx: số|null}} — khoá lạ / giá trị sai → 400 kèm
        lý do, không ghi gì. Job ĐANG CHỜ tự theo config mới (services.autopilot.config_watch_loop) trừ job đã ghim
        config riêng. `levels` = mức trộn tiếng RENDER_* (core/audio_levels.py)."""
        import core.batch_config as bc
        from core import audio_levels
        b = body if isinstance(body, dict) else {}
        patch, levels = b.get("patch") or {}, b.get("levels") or {}
        if not isinstance(patch, dict) or not isinstance(levels, dict) or not (patch or levels):
            raise HTTPException(400, "thiếu patch")
        try:
            audio_levels.validate(levels)      # kiểm trước: mức âm lượng sai → không ghi cả cấu hình batch
            out = bc.update(patch, by="desktop") if patch else {"changed": {}, "opts": bc.load()}
            lv = audio_levels.update(levels) if levels else {"changed": []}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        changed = sorted(out.get("changed") or []) + lv["changed"]
        if changed:
            bus.publish("config.changed", {"changed": changed})
        return {"ok": True, **out, "levels_changed": lv["changed"], **bc.meta()}

    # ── Cài đặt AI: chức năng nào dùng model nào (core/ai_models.py) ──
    @r.get(V6_PREFIX + "/ai")
    def ai_get():
        from core import ai_models
        return ai_models.snapshot()

    @r.post(V6_PREFIX + "/ai")
    def ai_set(body: dict):
        """{"models": {id: model|""}, "plan_mode": "file|sample", "providers": {tên: {api_key, base_url, clear_key}},
        "preset": "deepseek|alibaba|aurora|local"} — sai → 400 kèm lý do, không ghi gì."""
        from core import ai_models
        b = body if isinstance(body, dict) else {}
        try:
            out = ai_models.apply(b.get("models"), plan_mode=b.get("plan_mode"), providers_cfg=b.get("providers"),
                                  preset=b.get("preset"))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if out.get("changed"):
            bus.publish("config.changed", {"changed": out["changed"]})
        return out

    @r.post(V6_PREFIX + "/ai/models")
    def ai_models_list(body: dict):
        """Danh sách model THẬT của nhà cung cấp (gọi GET /models ra ngoài — nên là POST, chỉ khi người dùng bấm).
        body: {provider, api_key?, base_url?} — key/URL gửi kèm chỉ dùng để hỏi, KHÔNG lưu."""
        from core import ai_models
        b = body or {}
        try:
            return ai_models.fetch_models(str(b.get("provider") or ""), api_key=b.get("api_key") or None,
                                          base_url=b.get("base_url") or None)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @r.post(V6_PREFIX + "/ai/test")
    def ai_test(body: dict):
        from core import ai_models
        return ai_models.test_model(str((body or {}).get("model") or ""))

    # ── D2: bản dịch của 1 job (chỉ đọc; sửa câu qua POST /api/jobs/{id}/script-line) ──
    @r.get(V6_PREFIX + "/jobs/{job_id}/script")
    def job_script(job_id: str):
        if "/" in job_id or "\\" in job_id or job_id in ("", ".", ".."):
            raise HTTPException(404, "không có job")
        wd = inst.work_root / job_id
        sp = wd / "script.json"
        if not (wd / "manifest.json").is_file():
            raise HTTPException(404, "không có job")
        if not sp.is_file():
            return {"rows": [], "exists": False, "wav": []}
        try:
            rows = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise HTTPException(409, f"script.json đang được ghi hoặc hỏng: {e}") from e
        keep = ("i", "start", "end", "zh", "vi", "rv", "manual", "s_flags", "s_spk", "s_to", "junk",
                "unreviewed_accepted", "s_act", "s_why", "s_step")
        out = [{k: r.get(k) for k in keep if k in r} for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        wav = []
        tdir = wd / "tts"
        if tdir.is_dir():
            for f in tdir.glob("seg_*.wav"):
                try:
                    if f.stat().st_size > 1000:
                        wav.append(int(f.stem.split("_")[1]))
                except (OSError, ValueError, IndexError):
                    continue
        return {"rows": out, "exists": True, "wav": sorted(wav)}

    # ── Xung đột bảng nhân vật của bộ (engines/series_glossary): tập mới khác quyết định đã chốt → người chọn ──
    @r.get(V6_PREFIX + "/series/conflicts")
    def series_conflicts(status: str = "open"):
        import engines.series_glossary as sgl
        items = sgl.list_conflicts(None if status == "all" else status)
        return {"items": items[:500], "n": len(items)}

    @r.post(V6_PREFIX + "/series/conflicts/resolve")
    def series_conflict_resolve(body: dict):
        import engines.series_glossary as sgl
        b = body if isinstance(body, dict) else {}
        try:
            out = sgl.resolve_conflict(str(b.get("core") or ""), str(b.get("id") or ""), str(b.get("action") or ""))
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\"")) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        n_open = len(sgl.list_conflicts("open"))
        bus.publish("series.conflict", {"core": b.get("core"), "n_open": n_open})
        return {**out, "n_open": n_open}

    # ── A10: lịch sử sửa 1 câu (trace.jsonl của luồng simple; chỉ đọc) ──
    @r.get(V6_PREFIX + "/jobs/{job_id}/script/{i}/history")
    def job_line_history(job_id: str, i: int, limit: int = 50):
        if "/" in job_id or "\\" in job_id or job_id in ("", ".", ".."):
            raise HTTPException(404, "không có job")
        wd = inst.work_root / job_id
        if not (wd / "manifest.json").is_file():
            raise HTTPException(404, "không có job")
        out: list[dict] = []
        for name in ("trace.1.jsonl", "trace.jsonl"):          # cũ → mới
            fp = wd / name
            if not fp.is_file():
                continue
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if f'"i": {i},' not in line and f'"i": {i}}}' not in line:
                            continue
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if rec.get("i") == i:
                            out.append({k: rec.get(k) for k in ("ts", "step", "old", "new", "why") if k in rec})
            except OSError as e:
                raise HTTPException(409, f"không đọc được {name}: {e}") from e
        limit = max(1, min(int(limit or 50), 500))
        return {"i": i, "history": out[-limit:], "n": len(out)}

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
        app.add_middleware(NoCacheUI)
    except RuntimeError as e:
        print(f"[v6] bỏ NoCacheUI middleware: {e}")
    try:
        app.add_middleware(CORSMiddleware, allow_origins=["tauri://localhost", "http://tauri.localhost",
                                                          inst.base_url], allow_methods=["*"], allow_headers=["*"])
    except RuntimeError as e:   # server.app đã chạy (vd test V5 dùng trước) → không thêm middleware được
        print(f"[v6] bỏ CORS middleware: {e}")
    app.include_router(_v6_router(inst))
    from app.routes import ui_compat           # nút trang Phim / Studio trước đây chỉ sidecar có (2026-11-15)
    app.include_router(ui_compat.router)
    _install_lifecycle(app, inst, rt, legacy=legacy)

    if UI_DIR.is_dir():
        from fastapi.staticfiles import StaticFiles
        if legacy:
            # "/" là UI cũ của server.py → UI desktop mới ở /v6/
            @app.get("/v6", include_in_schema=False)
            def _v6_slash():
                return RedirectResponse("/v6/")

            @app.get("/v6/", include_in_schema=False)
            @app.get("/v6/index.html", include_in_schema=False)
            def _v6_index():
                return HTMLResponse(versioned_index(UI_DIR))
            app.mount("/v6", StaticFiles(directory=str(UI_DIR), html=True), name="ui_v6")
        else:
            app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
    return app
