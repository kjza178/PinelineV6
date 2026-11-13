"""
Auto-pilot — queue need_queue + heal safe qua HTTP Pineline (server của instance, PINELINE_BASE).
Chạy trong sidecar (agent_tele_cmd) — không cần restart server.
"""
from __future__ import annotations

import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import core.batch_config as batch_config
from core.fileio import atomic_write_text, file_lock

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "work" / "logs" / "autopilot_state.json"
# V6 F4: mặc định = server của instance (PINELINE_BASE / PINELINE_PORT / instances/v6.json) — không 8801.
from core.instance import server_base as _server_base  # noqa: E402
DEFAULT_BASE = _server_base()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_last_good_state: dict | None = None


def load_state() -> dict:
    global _last_good_state
    if not STATE_PATH.is_file():
        return {"enabled": False, "updated_at": None, "last_tick": None, "last_result": {}}
    err = None
    for _ in range(3):
        try:
            st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            _last_good_state = dict(st)
            return st
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(0.1)
    print(f"[autopilot] đọc {STATE_PATH} lỗi: {err} — dùng bản đọc tốt gần nhất", flush=True)
    if _last_good_state is not None:
        return dict(_last_good_state)
    return {"enabled": False, "updated_at": None, "last_tick": None, "last_result": {}}


def save_state(state: dict) -> None:
    atomic_write_text(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2))


def update_state(fn) -> dict:
    """Đọc-sửa-ghi trong khoá: tick sidecar và bật/tắt (server/script) không đè nhau."""
    with file_lock(STATE_PATH):
        st = load_state()
        fn(st)
        save_state(st)
    return st


def set_enabled(on: bool) -> dict:
    import inspect
    import threading

    caller = inspect.stack()[1]
    by = f"{Path(caller.filename).name}:{caller.function} [{threading.current_thread().name}]"

    def _fn(st: dict) -> None:
        st["enabled"] = bool(on)
        st["updated_at"] = _now()
        st["updated_by"] = by
        hist = list(st.get("toggle_log") or [])[-19:]
        hist.append({"at": st["updated_at"], "on": bool(on), "by": by})
        st["toggle_log"] = hist

    return update_state(_fn)


def _get(base: str, path: str, timeout: float = 30) -> dict:
    r = requests.get(base.rstrip("/") + path, timeout=timeout)
    r.raise_for_status()
    return r.json() if r.content else {}


def caller_headers(caller: str | None = None, source: str | None = None) -> dict:
    """fix 2026-11-12 agentI2: header nguồn lệnh. caller="user" CHỈ khi người gõ/bấm (Telegram);
    tick Auto-pilot / brain không gắn (hoặc gắn "brain") → server không coi là chạy tay."""
    h: dict = {}
    if caller:
        h["X-Pineline-Caller"] = str(caller)
    if source:
        h["X-Pineline-Source"] = str(source)
    return h


def _post(base: str, path: str, body: dict | None = None, timeout: float = 120,
          headers: dict | None = None) -> dict:
    r = requests.post(
        base.rstrip("/") + path,
        json=body or {},
        timeout=timeout,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        data = {"raw": r.text[:500]}
    if r.status_code >= 400:
        data = dict(data) if isinstance(data, dict) else {"error": data}
        data.setdefault("error", f"HTTP {r.status_code}")
        data["status_code"] = r.status_code
    return data if isinstance(data, dict) else {"ok": False, "data": data}


def queue_opts(base: str) -> dict:
    # Không lấy opts toàn cục của server (có thể còn sót từ lần xếp tay trước)
    out = batch_config.load()
    out["brand"] = out.get("brand_mode", "off") != "off"
    out["skip_done"] = True
    return out


def _opts_differ(a: dict, b: dict) -> bool:
    for k in list(batch_config.DEFAULTS) + ["brand"]:
        x, y = a.get(k), b.get(k)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
            if abs(float(x) - float(y)) > 1e-9:
                return True
        elif x != y:
            return True
    return False


def sync_pending_to_batch(base: str = DEFAULT_BASE) -> dict:
    """Job chưa bắt đầu trong hàng chờ → theo config batch mới nhất (giữ thứ tự).
    Job ghim (pin_opts, do Agent xếp kèm config riêng) và job đang chạy giữ nguyên."""
    q = _get(base, "/api/queue/jobs", timeout=15)
    pending = [str(x) for x in (q.get("queue") or [])]
    if not pending or not (q.get("worker") or q.get("current")):
        return {"ok": True, "skipped": "no_pending_or_stopped"}
    snaps = q.get("opts_by_job") or {}
    fallback = q.get("opts") or {}
    target = queue_opts(base)
    target.pop("skip_done", None)
    # HTTP add bỏ field lạ → cờ pin_opts mất sau lần xếp lại; nhớ danh sách ghim trong state
    st = load_state()
    pins: dict = dict(st.get("pinned") or {})
    live = set(pending) | ({str(q["current"])} if q.get("current") else set())
    pins = {k: v for k, v in pins.items() if k in live}
    plan: list[tuple[str, dict]] = []
    changed: list[str] = []
    for jid in pending:
        snap = snaps.get(jid) or fallback
        if snap.get("pin_opts") or jid in pins:
            if snap.get("pin_opts"):
                pins[jid] = {k: v for k, v in snap.items() if k != "pin_opts"}
            plan.append((jid, dict(pins[jid])))
            continue
        if _opts_differ(snap, target):
            changed.append(jid)
        plan.append((jid, target))
    update_state(lambda s: s.__setitem__("pinned", pins))
    if not changed:
        return {"ok": True, "changed": [], "pinned": list(pins)}
    r = _post(base, "/api/queue/jobs/opts", {"opts_by_job": {j: target for j in changed}}, timeout=30)
    if r.get("status_code") not in (404, 405):
        if r.get("error"):
            return {"ok": False, "error": r.get("error"), "changed": changed}
        return {"ok": True, "changed": r.get("updated") or [], "pending": len(pending), "pinned": list(pins)}
    # server cũ chưa có /opts
    _post(base, "/api/queue/jobs/remove", {"job_ids": pending}, timeout=30)
    # gom các job liên tiếp cùng opts thành 1 lần add để giữ thứ tự
    i = 0
    while i < len(plan):
        o = plan[i][1]
        ids = [plan[i][0]]
        while i + len(ids) < len(plan) and plan[i + len(ids)][1] is o:
            ids.append(plan[i + len(ids)][0])
        _post(base, "/api/queue/jobs", {"job_ids": ids, "skip_done": True, **o}, timeout=60)
        i += len(ids)
    return {"ok": True, "changed": changed, "pending": len(pending), "pinned": list(pins)}


def status_brief(base: str) -> dict:
    try:
        d = _get(base, "/api/agent/status", timeout=20)
        snap = d.get("snapshot") or {}
        counts = snap.get("counts") or {}
        q = _get(base, "/api/queue/jobs", timeout=15)
        return {
            "ok": True,
            "counts": counts,
            "need_queue": snap.get("need_queue") or [],
            "failed_ids": [x.get("job_id") for x in (snap.get("failed") or []) if isinstance(x, dict)],
            "queue_len": len(q.get("queue") or []),
            "current": q.get("current"),
            "busy": bool(q.get("busy") or q.get("worker")),
            "autopilot": load_state().get("enabled"),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def tick_once(base: str = DEFAULT_BASE, *, force: bool = False) -> dict:
    """1 vòng: queue need_queue (nếu rỗng/thiếu) + watch-once heal safe."""
    st = load_state()
    if not force and not st.get("enabled"):
        return {"ok": True, "skipped": "autopilot_off"}

    result: dict[str, Any] = {"ok": True, "at": _now(), "actions": []}
    brief = status_brief(base)
    if not brief.get("ok"):
        result["ok"] = False
        result["error"] = brief.get("error")
        return result
    result["before"] = brief.get("counts")

    failed = set(brief.get("failed_ids") or [])
    need = [j for j in (brief.get("need_queue") or []) if j not in failed]
    qlen = int(brief.get("queue_len") or 0)
    current = brief.get("current")

    # Chỉ enqueue khi queue trống (không current và không pending) — tránh spam add
    if need and qlen == 0 and not current:
        opts = queue_opts(base)
        body = {"job_ids": need[:40], **{k: v for k, v in opts.items() if k != "skip_done"}, "skip_done": True}
        add = _post(base, "/api/queue/jobs", body, timeout=60)
        result["actions"].append({"queue_add": len(need[:40]), "result": {
            "ok": add.get("ok", "error" not in add),
            "error": add.get("error"),
            "added": add.get("added") or add.get("queue"),
        }})
    elif need and qlen == 0 and current:
        # đang chạy 1 job nhưng queue rỗng — bổ sung phần còn lại
        opts = queue_opts(base)
        body = {"job_ids": need[:40], **{k: v for k, v in opts.items() if k != "skip_done"}, "skip_done": True}
        add = _post(base, "/api/queue/jobs", body, timeout=60)
        result["actions"].append({"queue_topup": len(need[:40]), "result": {
            "ok": add.get("ok", "error" not in add),
            "error": add.get("error"),
        }})
    else:
        result["actions"].append({"queue": "ok_or_busy", "queue_len": qlen, "need": len(need)})

    # Heal khi GPU/queue có thể rảnh — watch-once tự skip nếu busy
    heal = _post(base, "/api/agent/watch-once", {}, timeout=180)
    result["actions"].append({"watch_once": {
        "skipped": heal.get("skipped"),
        "n_healed": heal.get("n_healed"),
        "n_raw": heal.get("n_raw_problems") or heal.get("n_problems"),
        "error": heal.get("error"),
    }})

    after = status_brief(base)
    result["after"] = after.get("counts") if after.get("ok") else None

    last = {"actions": result["actions"], "before": result.get("before"), "after": result.get("after")}

    def _fn(s: dict) -> None:
        s["last_tick"] = _now()
        s["last_result"] = last

    st = update_state(_fn)
    result["enabled"] = bool(st.get("enabled"))
    return result


def agent_chat(
    base: str,
    message: str,
    *,
    history: list[dict] | None = None,
    allow_mutate: bool = True,
    require_confirm: bool = False,
    caller: str | None = None,
    source: str | None = None,
) -> dict:
    """caller="user" (+ source="telegram"): tin do NGƯỜI gõ → tool chạy stage/dịch/tts/mux tính là chạy tay."""
    return _post(
        base,
        "/api/agent/chat",
        {
            "message": message,
            "history": history or [],
            "allow_mutate": allow_mutate,
            "max_steps": 10,
            "require_confirm": require_confirm,
        },
        timeout=180,
        headers=caller_headers(caller, source),
    )


def agent_confirm(base: str, cid: str, approve: bool, caller: str | None = None,
                  source: str | None = None) -> dict:
    return _post(base, "/api/agent/confirm", {"id": cid, "approve": approve}, timeout=120,
                 headers=caller_headers(caller, source))


def hard_stop(base: str = DEFAULT_BASE) -> dict:
    """Dừng queue + mọi stage running + tắt ý định re-queue."""
    out: dict[str, Any] = {"ok": True, "actions": []}
    try:
        q = _post(base, "/api/queue/jobs/stop", {}, timeout=30)
        out["actions"].append({"queue_stop": q})
    except Exception as e:  # noqa: BLE001
        out["actions"].append({"queue_stop_error": str(e)})

    # Stop mọi job đang running trên disk
    stopped: list[str] = []
    work = Path(__file__).resolve().parents[1] / "work"
    if work.is_dir():
        for d in work.iterdir():
            if not d.is_dir() or not (d / "manifest.json").is_file():
                continue
            try:
                data = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            stages = data.get("stages") or {}
            if any(isinstance(st, dict) and st.get("status") == "running" for st in stages.values()):
                try:
                    r = _post(base, f"/api/jobs/{d.name}/stop", {}, timeout=30)
                    stopped.append(d.name)
                    out.setdefault("job_stops", []).append({d.name: r})
                except Exception as e:  # noqa: BLE001
                    out.setdefault("job_stop_errors", []).append({d.name: str(e)})
    out["stopped_jobs"] = stopped

    st = set_enabled(False)
    out["autopilot"] = st
    return out


def format_status(brief: dict) -> str:
    if not brief.get("ok"):
        return f"Status lỗi: {brief.get('error')}"
    c = brief.get("counts") or {}
    ap = "ON" if brief.get("autopilot") else "OFF"
    cur = brief.get("current") or "—"
    return (
        f"Auto-pilot {ap}\n"
        f"running {c.get('running', 0)} · pending {c.get('pending', 0)} · "
        f"failed {c.get('failed', 0)} · done {c.get('done', 0)} · need_queue {c.get('need_queue', 0)}\n"
        f"queue {brief.get('queue_len', 0)} · current {cur}"
    )
