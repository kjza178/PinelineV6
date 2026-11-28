"""core/timeutil.py — MỘT kiểu mốc thời gian cho manifest/film_db.

Quy ước (fix 2026-11-13): `started_at` / `finished_at` / `created_at` = chuỗi ISO-8601 UTC
(`2026-11-13T02:15:00.123456+00:00`, cùng dạng core.manifest ghi từ trước). `updated_at` của stage vẫn là
epoch float (nhịp tim, code so `time.time() - updated_at`).

Dữ liệu cũ lẫn nhiều dạng (epoch float từ state.job_state / đoạn heal, chuỗi giờ máy không múi giờ từ
split_parts / asr) → đọc qua `to_epoch` / `to_iso`, không tự `float()` hay cắt chuỗi.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_epoch(v: Any) -> float | None:
    """Mốc thời gian bất kỳ dạng nào → epoch giây. Không đọc được → None.
    Nhận: số, chuỗi số, ISO-8601 (có/không múi giờ, hậu tố Z), `YYYY-MM-DD HH:MM:SS`.
    Chuỗi không múi giờ = giờ máy."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    s = str(v).strip()
    if not s:
        return None
    try:
        f = float(s)
        return f if f > 0 else None
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def to_iso(v: Any) -> str | None:
    """Mốc thời gian bất kỳ dạng nào → chuỗi ISO UTC. Không đọc được → None."""
    t = to_epoch(v)
    if t is None:
        return None
    try:
        return datetime.fromtimestamp(t, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None
