"""services/translate_wake.py — đổi cấu hình AI → job đang chờ thử lại soát chạy lại ngay (2026-11-21)."""
from __future__ import annotations


def _ai_key(k: str) -> bool:
    k = str(k or "").upper()
    return "MODEL" in k or "API_KEY" in k or "BASE_URL" in k or k.startswith(("PROVIDER", "PRESET"))


def on_ai_changed(changed) -> list[str]:
    keys = [str(k) for k in (changed or [])]
    if not any(_ai_key(k) for k in keys):
        return []
    try:
        import server
        return server._wake_translate_parks("đổi cấu hình AI: " + ", ".join(keys[:6]))
    except Exception as e:  # noqa: BLE001
        print(f"[ai] đánh thức job chờ soát lỗi: {e}", flush=True)
        return []
