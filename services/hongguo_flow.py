"""services/hongguo_flow.py — điểm vào Hongguo cho server (V6 F4, khung).

Mục tiêu (thiết kế 3.4): `http_routes/routes_hg.py` gọi `services.hongguo_flow.bridge()` thay cho
~43 chỗ `importlib.reload(agents.hongguo_bridge)`. F4 chỉ tạo điểm vào (KHÔNG reload); chuyển routes_hg
sang đây là việc còn lại (cần chạy thật với Hongguo :8000 để kiểm — ghi trong docs/fixes/2026-11-11-f4-f5.md).
"""
from __future__ import annotations


def bridge():
    """Module agents.hongguo_bridge (import thường, không reload)."""
    import agents.hongguo_bridge as hg
    return hg
