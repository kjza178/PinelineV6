"""Tool loop — điều khiển pipeline theo bước, chờ kết quả thật.

Khác `pineline_run_stage` (fire-and-forget): mỗi bước start → poll manifest
tới done|failed|stopped, rồi mới bước sau. Agent không spam GET API từ ngoài;
poll nằm **trong** tool.

Dùng:
  from agent_tool_loop import run_stage_wait, pipeline_loop, stage_snapshot
  # hoặc CLI HTTP (không cần bind server):
  python agent_tool_loop.py [--base <PINELINE_BASE>] --job ID --stages translate --dry
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

TERMINAL_OK = frozenset({"done"})
TERMINAL_BAD = frozenset({"failed"})
# pending sau khi đã thấy running = bị Stop / bỏ qua worker
WIRED = ("download", "asr", "translate", "tts", "mux")


def stage_snapshot(work_root: Path, job_id: str, stage: str) -> dict[str, Any]:
    """Đọc 1 stage từ manifest (disk) — không HTTP."""
    mf = Path(work_root) / job_id / "manifest.json"
    if not mf.is_file():
        return {"status": "missing", "error": "no manifest", "stage": stage, "job_id": job_id}
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e), "stage": stage, "job_id": job_id}
    st = dict((data.get("stages") or {}).get(stage) or {})
    return {
        "job_id": job_id,
        "stage": stage,
        "status": st.get("status") or "pending",
        "progress": st.get("progress"),
        "n_done": st.get("n_done"),
        "n_total": st.get("n_total") or st.get("n_segments") or st.get("n_clips"),
        "cur_text": st.get("cur_text"),
        "error": st.get("error") or st.get("last_error"),
        "updated_at": st.get("updated_at"),
    }


def _normalize_start(res: Any) -> dict[str, Any]:
    if res is None:
        return {"ok": True, "started": True}
    if hasattr(res, "body") and hasattr(res, "status_code"):
        try:
            body = json.loads(res.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {"error": f"HTTP {getattr(res, 'status_code', '?')}"}
        if getattr(res, "status_code", 200) >= 400:
            body.setdefault("ok", False)
            body.setdefault("http_status", res.status_code)
        else:
            body.setdefault("ok", True)
        return body if isinstance(body, dict) else {"ok": True, "raw": body}
    if isinstance(res, dict):
        out = dict(res)
        if "error" in out and "ok" not in out:
            out["ok"] = False
        else:
            out.setdefault("ok", True)
        return out
    return {"ok": True, "raw": res}


def _looks_like_stop(work_root: Path, job_id: str, snap: dict[str, Any]) -> bool:
    """True chỉ khi có .stop hoặc cur_text báo dừng — không nhầm salvage pending."""
    cur = str(snap.get("cur_text") or "").lower()
    if "dừng" in cur or "stopped" in cur or "stop" in cur:
        return True
    for base in {Path(work_root), Path(os.getenv("WORK_DIR") or "work")}:
        try:
            if (base / job_id / ".stop").exists():
                return True
        except OSError:
            pass
    return False


def wait_stage(
    work_root: Path,
    job_id: str,
    stage: str,
    *,
    timeout_sec: float = 3600,
    poll_sec: float = 2.0,
    stop_flag: Callable[[], bool] | None = None,
    pending_grace_sec: float = 90.0,
) -> dict[str, Any]:
    """Poll disk tới terminal. Trả final snapshot + timeline ngắn."""
    t0 = time.time()
    saw_running = False
    ticks: list[dict[str, Any]] = []
    last_key: tuple[Any, ...] | None = None

    while True:
        if stop_flag and stop_flag():
            snap = stage_snapshot(work_root, job_id, stage)
            return {
                "ok": False,
                "stopped": True,
                "final": snap,
                "elapsed_sec": round(time.time() - t0, 1),
                "ticks": ticks[-12:],
            }
        snap = stage_snapshot(work_root, job_id, stage)
        st = snap.get("status")
        if st == "error":
            return {
                "ok": False,
                "final": snap,
                "elapsed_sec": round(time.time() - t0, 1),
                "ticks": ticks[-12:],
                "error": snap.get("error") or "manifest error",
            }
        if st == "running":
            saw_running = True
        key = (st, snap.get("progress"), snap.get("n_done"), snap.get("cur_text"))
        if key != last_key:
            ticks.append({
                "t": round(time.time() - t0, 1),
                "status": st,
                "progress": snap.get("progress"),
                "n_done": snap.get("n_done"),
                "n_total": snap.get("n_total"),
                "cur_text": (snap.get("cur_text") or "")[:80],
            })
            last_key = key

        if st in TERMINAL_OK:
            return {
                "ok": True,
                "final": snap,
                "elapsed_sec": round(time.time() - t0, 1),
                "ticks": ticks[-12:],
            }
        if st in TERMINAL_BAD:
            return {
                "ok": False,
                "final": snap,
                "elapsed_sec": round(time.time() - t0, 1),
                "ticks": ticks[-12:],
                "error": snap.get("error") or "failed",
            }
        # running → pending: Stop thật vs salvage/resume
        if saw_running and st == "pending":
            if _looks_like_stop(work_root, job_id, snap):
                return {
                    "ok": False,
                    "stopped": True,
                    "final": snap,
                    "elapsed_sec": round(time.time() - t0, 1),
                    "ticks": ticks[-12:],
                    "error": "stopped by user",
                }
            return {
                "ok": False,
                "interrupted": True,
                "final": snap,
                "elapsed_sec": round(time.time() - t0, 1),
                "ticks": ticks[-12:],
                "error": snap.get("error") or "stage về pending sau running (không phải Stop)",
            }
        # Start ok nhưng không bao giờ running
        if not saw_running and st == "pending" and (time.time() - t0) >= pending_grace_sec:
            return {
                "ok": False,
                "final": snap,
                "elapsed_sec": round(time.time() - t0, 1),
                "ticks": ticks[-12:],
                "error": f"pending >{pending_grace_sec:.0f}s không vào running",
            }
        if time.time() - t0 >= timeout_sec:
            return {
                "ok": False,
                "timeout": True,
                "final": snap,
                "elapsed_sec": round(time.time() - t0, 1),
                "ticks": ticks[-12:],
                "error": f"timeout {timeout_sec}s",
            }
        time.sleep(max(0.5, float(poll_sec)))


def run_stage_wait(
    run_stage_fn: Callable[[str, str, dict], Any],
    work_root: Path,
    job_id: str,
    stage: str,
    opts: dict | None = None,
    *,
    timeout_sec: float = 3600,
    poll_sec: float = 2.0,
    stop_flag: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Start 1 stage (hoặc skip nếu đã done & !force) rồi chờ terminal."""
    opts = dict(opts or {})
    force = bool(opts.get("force"))
    stage = str(stage).strip().lower()
    before = stage_snapshot(work_root, job_id, stage)

    if before.get("status") == "missing":
        return {"ok": False, "error": "job/manifest missing", "final": before}

    if before.get("status") == "done" and not force:
        return {
            "ok": True,
            "skipped": True,
            "reason": "already done (force=false)",
            "final": before,
            "elapsed_sec": 0,
        }

    if before.get("status") == "running" and not force:
        # Đã chạy — chỉ chờ, không start trùng
        waited = wait_stage(
            work_root, job_id, stage,
            timeout_sec=timeout_sec, poll_sec=poll_sec, stop_flag=stop_flag,
        )
        return {
            "ok": bool(waited.get("ok")),
            "joined_running": True,
            "start": {"ok": True, "joined": True},
            **waited,
        }

    start = _normalize_start(run_stage_fn(job_id, stage, opts))
    # 409 đang chạy → join wait (giống HTTP CLI)
    busy = start.get("http_status") == 409 or (
        "đang chạy" in str(start.get("error") or "").lower()
    )
    if (not start.get("ok") or start.get("error")) and not busy:
        return {
            "ok": False,
            "error": start.get("error") or "start failed",
            "start": start,
            "final": stage_snapshot(work_root, job_id, stage),
        }

    waited = wait_stage(
        work_root, job_id, stage,
        timeout_sec=timeout_sec, poll_sec=poll_sec, stop_flag=stop_flag,
    )
    out = {
        "ok": bool(waited.get("ok")),
        "skipped": False,
        "start": start,
        **waited,
    }
    if busy:
        out["joined_running"] = True
    return out


def pipeline_loop(
    run_stage_fn: Callable[[str, str, dict], Any],
    work_root: Path,
    job_id: str,
    stages: list[str],
    opts: dict | None = None,
    *,
    timeout_sec: float = 3600,
    poll_sec: float = 2.0,
    stop_on_fail: bool = True,
    stop_flag: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Chạy tuần tự nhiều stage; mỗi stage chờ xong mới sang bước sau."""
    opts = dict(opts or {})
    results: list[dict[str, Any]] = []
    t0 = time.time()
    for stage in stages:
        stage = str(stage).strip().lower()
        if not stage:
            continue
        if stop_flag and stop_flag():
            return {
                "ok": False,
                "stopped": True,
                "job_id": job_id,
                "results": results,
                "elapsed_sec": round(time.time() - t0, 1),
            }
        # Per-stage timeout: remaining budget
        left = max(30.0, timeout_sec - (time.time() - t0))
        r = run_stage_wait(
            run_stage_fn, work_root, job_id, stage, opts,
            timeout_sec=left, poll_sec=poll_sec, stop_flag=stop_flag,
        )
        results.append({"stage": stage, **r})
        if stop_on_fail and not r.get("ok") and not r.get("skipped"):
            return {
                "ok": False,
                "job_id": job_id,
                "failed_stage": stage,
                "results": results,
                "elapsed_sec": round(time.time() - t0, 1),
                "error": r.get("error") or (r.get("final") or {}).get("error"),
            }
    all_ok = all(x.get("ok") or x.get("skipped") for x in results)
    return {
        "ok": all_ok,
        "job_id": job_id,
        "results": results,
        "elapsed_sec": round(time.time() - t0, 1),
        "summary": [
            {
                "stage": x["stage"],
                "ok": x.get("ok"),
                "skipped": x.get("skipped"),
                "status": (x.get("final") or {}).get("status"),
                "progress": (x.get("final") or {}).get("progress"),
            }
            for x in results
        ],
    }


# ── HTTP client (test / CLI không cần bind agent runtime) ───────────────────

def http_job(base: str, job_id: str) -> dict[str, Any]:
    url = f"{base.rstrip('/')}/api/jobs/{job_id}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def http_run_stage(base: str, job_id: str, stage: str, opts: dict) -> dict[str, Any]:
    q = []
    if opts.get("force"):
        q.append("force=true")
    if opts.get("voice"):
        q.append("voice=" + urllib.parse.quote(str(opts["voice"]), safe=""))
    if opts.get("engine"):
        q.append("engine=" + urllib.parse.quote(str(opts["engine"]), safe=""))
    if opts.get("speed") is not None:
        q.append("speed=" + str(opts["speed"]))
    qs = ("?" + "&".join(q)) if q else ""
    url = f"{base.rstrip('/')}/api/jobs/{job_id}/run/{stage}{qs}"
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            data = json.loads(body)
        except Exception:  # noqa: BLE001
            data = {"error": body[:300]}
        data["ok"] = False
        data["http_status"] = e.code
        return data


def http_stage_snapshot(base: str, job_id: str, stage: str) -> dict[str, Any]:
    j = http_job(base, job_id)
    st = dict((j.get("stages") or {}).get(stage) or {})
    return {
        "job_id": job_id,
        "stage": stage,
        "status": st.get("status") or "pending",
        "progress": st.get("progress"),
        "n_done": st.get("n_done"),
        "n_total": st.get("n_total") or st.get("n_segments") or st.get("n_clips"),
        "cur_text": st.get("cur_text"),
        "error": st.get("error") or st.get("last_error"),
    }


def http_run_stage_wait(
    base: str,
    job_id: str,
    stage: str,
    opts: dict | None = None,
    *,
    timeout_sec: float = 3600,
    poll_sec: float = 2.0,
) -> dict[str, Any]:
    """Giống run_stage_wait nhưng qua HTTP + poll GET /api/jobs (cho smoke test)."""
    opts = dict(opts or {})
    force = bool(opts.get("force"))
    before = http_stage_snapshot(base, job_id, stage)
    if before.get("status") == "done" and not force:
        return {
            "ok": True,
            "skipped": True,
            "reason": "already done (force=false)",
            "final": before,
            "elapsed_sec": 0,
        }
    if before.get("status") != "running" or force:
        start = http_run_stage(base, job_id, stage, opts)
        if start.get("ok") is False or start.get("error"):
            # 409 đang chạy → join wait
            if start.get("http_status") != 409:
                return {
                    "ok": False,
                    "error": start.get("error") or "start failed",
                    "start": start,
                    "final": http_stage_snapshot(base, job_id, stage),
                }

    t0 = time.time()
    saw_running = before.get("status") == "running"
    ticks: list[dict] = []
    last = None
    while True:
        snap = http_stage_snapshot(base, job_id, stage)
        st = snap.get("status")
        if st == "running":
            saw_running = True
        key = (st, snap.get("progress"), snap.get("n_done"))
        if key != last:
            ticks.append({
                "t": round(time.time() - t0, 1),
                "status": st,
                "progress": snap.get("progress"),
                "n_done": snap.get("n_done"),
                "cur_text": (snap.get("cur_text") or "")[:60],
            })
            last = key
        if st == "done":
            return {"ok": True, "final": snap, "elapsed_sec": round(time.time() - t0, 1), "ticks": ticks[-12:]}
        if st == "failed":
            return {"ok": False, "final": snap, "elapsed_sec": round(time.time() - t0, 1), "ticks": ticks[-12:], "error": snap.get("error")}
        if saw_running and st == "pending":
            return {"ok": False, "stopped": True, "final": snap, "elapsed_sec": round(time.time() - t0, 1), "ticks": ticks[-12:]}
        if time.time() - t0 >= timeout_sec:
            return {"ok": False, "timeout": True, "final": snap, "elapsed_sec": round(time.time() - t0, 1), "ticks": ticks[-12:]}
        time.sleep(poll_sec)


def main() -> None:
    p = argparse.ArgumentParser(description="Pineline tool-loop CLI (HTTP)")
    from core.instance import server_base as _server_base  # V6 F4: không mặc định 8801
    p.add_argument("--base", default=_server_base())
    p.add_argument("--job", required=True)
    p.add_argument("--stages", default="translate", help="csv: translate,tts,mux")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry", action="store_true", help="chỉ snapshot, không start")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--poll", type=float, default=2)
    args = p.parse_args()
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]

    if args.dry:
        out = {s: http_stage_snapshot(args.base, args.job, s) for s in stages}
        print(json.dumps({"ok": True, "dry": True, "snapshots": out}, ensure_ascii=False, indent=2))
        return

    results = []
    for s in stages:
        r = http_run_stage_wait(
            args.base, args.job, s, {"force": args.force},
            timeout_sec=args.timeout, poll_sec=args.poll,
        )
        results.append({"stage": s, **r})
        if not r.get("ok") and not r.get("skipped"):
            break
    print(json.dumps({
        "ok": all(x.get("ok") or x.get("skipped") for x in results),
        "job_id": args.job,
        "results": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
