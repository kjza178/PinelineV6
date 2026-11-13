"""services/autopilot.py — vòng Auto-pilot + config-live (sidecar cũ: autopilot_loop / config_watch_loop) — V6 F4.

Chuyển nguyên văn từ agents/agent_tele_cmd.py; `base` = server của instance, `notify` do bên gọi truyền
(server: agents.agent_watch.telegram_send — tôn trọng cờ telegram; sidecar: tg_send).
"""
from __future__ import annotations

import os
import threading
from typing import Callable

Notify = Callable[[str], object]
TICK_SEC = max(30, int(os.getenv("AUTOPILOT_TICK_SEC") or "90"))
LIVE_CONFIG = (os.getenv("QUEUE_LIVE_CONFIG") or "1").strip() not in ("0", "false", "no")


def _base(base: str | None) -> str:
    if base:
        return base.rstrip("/")
    from core.instance import server_base
    return server_base()


def autopilot_loop(stop: threading.Event, *, base: str | None = None, notify: Notify | None = None,
                   tick_sec: float | None = None) -> None:
    from agents.agent_autopilot import load_state, tick_once
    tick = tick_sec or TICK_SEC
    print(f"[autopilot] tick loop every {tick}s", flush=True)
    while not stop.is_set():
        try:
            st = load_state()
            if st.get("enabled"):
                r = tick_once(_base(base), force=False)
                acts = r.get("actions") or []
                # notify nếu có heal thật hoặc queue add
                note = []
                for a in acts:
                    if "queue_add" in a or "queue_topup" in a:
                        note.append(str(a))
                    wo = a.get("watch_once") if isinstance(a, dict) else None
                    if isinstance(wo, dict) and wo.get("n_healed"):
                        note.append(f"healed={wo.get('n_healed')}")
                if note and notify:
                    notify("Auto-pilot: " + " | ".join(note)[:500])
        except Exception as e:  # noqa: BLE001
            print(f"[autopilot] tick lỗi: {e}", flush=True)
        stop.wait(tick)


def sync_and_report(reason: str, *, base: str | None = None, notify: Notify | None = None) -> dict:
    from agents.agent_autopilot import sync_pending_to_batch
    r = sync_pending_to_batch(_base(base))
    ch = r.get("changed") or []
    if ch:
        msg = f"Hàng chờ theo config batch mới ({reason}): xếp lại {len(ch)} job chưa chạy"
        if r.get("pinned"):
            msg += f" · giữ {len(r['pinned'])} job ghim config riêng"
        print(f"[config-live] {msg}", flush=True)
        if notify:
            notify(msg)
    return r


def config_watch_loop(stop: threading.Event, *, base: str | None = None, notify: Notify | None = None) -> None:
    import core.batch_config as batch_config
    if not LIVE_CONFIG:
        print("[config-live] tắt (QUEUE_LIVE_CONFIG=0)", flush=True)
        return
    print("[config-live] job chưa chạy tự theo config batch mới", flush=True)
    last = None
    while not stop.is_set():
        try:
            cur = batch_config.meta().get("updated_at")
            if last is None or cur != last:
                sync_and_report("khởi động" if last is None else "config đổi", base=base, notify=notify)
                last = cur
        except Exception as e:  # noqa: BLE001
            print(f"[config-live] lỗi: {e}", flush=True)
        stop.wait(5)
