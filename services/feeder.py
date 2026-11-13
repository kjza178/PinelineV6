"""services/feeder.py — Feeder / Run-watch / thư viện (V6 F4): lớp mỏng trên agents.agent_feeder.

Trước: agent_core (trong server) POST sidecar `/cmd` "/running" | "/lib …" | "/feed …" → sidecar gọi
agents.agent_feeder(BASE) → HTTP ngược về server (vòng server→sidecar→server). Nay server và sidecar
cùng gọi các hàm dưới; `base` mặc định = server của instance (core.instance.server_base), không 8801.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

Notify = Callable[[str], object]


def _base(base: str | None) -> str:
    if base:
        return base.rstrip("/")
    from core.instance import server_base
    return server_base()


def _feeder():
    import agents.agent_feeder as feeder
    return feeder


def _autopilot_enabled() -> bool:
    from agents.agent_autopilot import load_state
    return bool(load_state().get("enabled"))


# ───────────── lệnh (dùng chung server agent_core + sidecar Telegram/HTTP) ─────────────

def running_text(base: str | None = None) -> str:
    return _feeder().running_text(_base(base))


def lib_command(rest: str = "", base: str | None = None) -> tuple[str, list]:
    return _feeder().lib_command(_base(base), rest)


def feed_command(rest: str = "", base: str | None = None) -> str:
    """Lệnh /feed [status|on|off|preview|now|reset|<yêu cầu tự do>] — chuyển nguyên văn từ sidecar."""
    feeder = _feeder()
    b = _base(base)
    sub = (rest or "").strip().lower()
    if sub in ("", "status", "show"):
        st = feeder.load_state()
        return (
            f"Tự thêm job: {'ON' if st['enabled'] else 'OFF'}"
            + ("" if _autopilot_enabled() else " (Auto-pilot OFF → chưa chạy, /on để bật)")
            + (f"\nYêu cầu: «{st['request_text']}»" if st["request_text"] else "")
            + "\n" + feeder.describe_spec(st["spec"])
            + "\n\n" + feeder.preview(b)
        )
    if sub in ("on", "off"):
        st = feeder.load_state()
        st["enabled"] = sub == "on"
        feeder.save_state(st)
        return f"Tự thêm job {sub.upper()}\n" + feeder.describe_spec(st["spec"])
    if sub in ("preview", "xem"):
        return feeder.preview(b)
    if sub in ("now", "ngay"):
        r = feeder.feed_tick(b, force=True)
        return "Thêm ngay:\n" + ("\n".join(r.get("notes") or []) or "không có gì để thêm")
    if sub in ("reset",):
        st = feeder.load_state()
        st["spec"], st["request_text"] = feeder.normalize_spec({}), ""
        feeder.save_state(st)
        return "Yêu cầu về mặc định:\n" + feeder.describe_spec(st["spec"])
    r = feeder.set_request(rest.strip())
    warn = f"\n(LLM lỗi, dùng luật đơn giản: {r['_llm_error']})" if r.get("_llm_error") else ""
    return (
        "Đã lưu yêu cầu tự thêm job (ON)"
        + (f"\nHiểu là: {r['summary']}" if r.get("summary") else "")
        + warn
        + "\n" + feeder.describe_spec(r["spec"])
        + ("" if _autopilot_enabled() else "\n⚠ Auto-pilot đang OFF — /on để bắt đầu chạy")
        + "\n\n" + feeder.preview(b)
    )


# ───────────── vòng nền (sidecar cũ: feeder_loop / runwatch_loop) ─────────────

TICK_SEC = max(30, int(os.getenv("AUTOPILOT_TICK_SEC") or "90"))
RUNWATCH_SEC = max(30, int(os.getenv("RUNWATCH_SEC") or "90"))


def feeder_loop(stop: threading.Event, *, base: str | None = None, notify: Notify | None = None,
                tick_sec: float | None = None) -> None:
    tick = tick_sec or TICK_SEC
    print(f"[feeder] mỗi {tick}s khi Auto-pilot ON + feeder ON", flush=True)
    while not stop.is_set():
        try:
            if _autopilot_enabled():
                r = _feeder().feed_tick(_base(base))
                notes = r.get("notes") or []
                if notes:
                    print(f"[feeder] {notes}", flush=True)
                    if notify:
                        notify("Tự thêm job: " + "\n".join(notes)[:1500])
        except Exception as e:  # noqa: BLE001
            print(f"[feeder] lỗi: {e}", flush=True)
        stop.wait(tick)


def runwatch_loop(stop: threading.Event, *, base: str | None = None, notify: Notify | None = None,
                  tick_sec: float | None = None) -> None:
    """Luôn theo dõi job chạy + báo xong/treo; chỉ tự xử lý treo khi Auto-pilot ON."""
    tick = tick_sec or RUNWATCH_SEC
    feeder = _feeder()
    print(f"[runwatch] mỗi {tick}s · treo sau {feeder.STUCK_MIN:.0f} phút", flush=True)
    while not stop.is_set():
        try:
            feeder.runwatch_tick(_base(base), heal=_autopilot_enabled(),
                                 notify=(notify or (lambda _t: None)))
        except Exception as e:  # noqa: BLE001
            print(f"[runwatch] lỗi: {e}", flush=True)
        stop.wait(tick)
