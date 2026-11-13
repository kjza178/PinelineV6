"""services/background.py — khởi động việc nền TRONG server khi instance bật cờ `feeder` (V6 F4).

Gồm: autopilot tick, config-live, runwatch, feeder, film_db sync (+ worker auto-publish nếu cờ
auto_publish). Trước đây toàn bộ chạy trong sidecar :8802 (agents/agent_tele_cmd.main). Một tiến trình
sở hữu queue + DB → không còn 2 tiến trình cùng tick.

Mặc định TẮT (v6/test feeder=false). Vòng đầu chờ `delay` giây để server bind cổng xong (các tick gọi
API của chính server qua HTTP — giữ nguyên code agents, chưa đổi sang gọi hàm).
"""
from __future__ import annotations

import os
import threading

LOOP_NAMES = ("autopilot", "config-live", "runwatch", "feeder", "film-db")
_STOP = threading.Event()
_THREADS: list[threading.Thread] = []


def enabled() -> bool:
    return (os.getenv("PINELINE_FLAG_FEEDER") or "0").strip().lower() in ("1", "true", "yes", "on")


def _notify(text: str, force: bool = True) -> None:
    """Telegram qua agents.agent_watch.telegram_send — tự tắt khi instance telegram=false (AGENT_TELEGRAM=0)."""
    try:
        from agents.agent_watch import telegram_send
        telegram_send(text, force=force)
    except Exception as e:  # noqa: BLE001
        print(f"[services] notify lỗi: {e}", flush=True)


def _targets(base: str | None):
    from services import autopilot, feeder, film_db_sync
    return {
        # review2: sidecar cũ gửi tin Auto-pilot force=False (chống trùng 1h) — giữ đúng ngữ nghĩa
        "autopilot": lambda: autopilot.autopilot_loop(_STOP, base=base,
                                                      notify=lambda t: _notify(t, force=False)),
        "config-live": lambda: autopilot.config_watch_loop(_STOP, base=base, notify=_notify),
        "runwatch": lambda: feeder.runwatch_loop(_STOP, base=base, notify=_notify),
        "feeder": lambda: feeder.feeder_loop(_STOP, base=base, notify=_notify),
        "film-db": lambda: film_db_sync.loop(_STOP, base=base),
    }


def start(*, base: str | None = None, delay: float = 15.0, force: bool = False) -> list[str]:
    """Khởi động các vòng (1 lần / tiến trình). Trả tên vòng đã chạy; [] nếu cờ tắt."""
    if not (force or enabled()):
        print("[services] việc nền OFF (instance feeder=false)", flush=True)
        return []
    if any(t.is_alive() for t in _THREADS):
        return [t.name for t in _THREADS]
    _THREADS.clear()
    _STOP.clear()
    targets = _targets(base)

    def _run(fn):
        if _STOP.wait(delay):
            return
        fn()

    for name in LOOP_NAMES:
        t = threading.Thread(target=_run, args=(targets[name],), name=f"svc-{name}", daemon=True)
        t.start()
        _THREADS.append(t)
    from services import publisher
    publisher.start_worker()
    print(f"[services] việc nền ON trong server: {', '.join(LOOP_NAMES)}", flush=True)
    return [t.name for t in _THREADS]


def stop() -> None:
    _STOP.set()
