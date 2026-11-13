"""services/publisher.py — worker tự đăng YouTube (agents.auto_publisher) — V6 F4.

Trước: worker chạy trong sidecar, còn server chỉ `enqueue_publish`. Nay server tự chạy worker khi
instance bật CẢ `feeder` (server sở hữu việc nền) VÀ `auto_publish`. v6/test: auto_publish=false → không bao giờ chạy.
"""
from __future__ import annotations

import os


def _flag(name: str) -> bool:
    return (os.getenv(f"PINELINE_FLAG_{name.upper()}") or "0").strip().lower() in ("1", "true", "yes", "on")


def start_worker() -> bool:
    """Trả True nếu đã khởi động. Cờ auto_publish tắt (hoặc không có env instance) → không làm gì."""
    if not _flag("auto_publish"):
        print("[auto-publish] OFF (instance auto_publish=false)", flush=True)
        return False
    try:
        import agents.auto_publisher as auto_publisher
        auto_publisher.start_worker()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[auto-publish] Không khởi động được worker: {e}", flush=True)
        return False
