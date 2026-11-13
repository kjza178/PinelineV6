"""Per-request Hongguo UI backend context (không reload — tránh mất override).

Hongguo1 vs Hongguo2 dùng chung process nhưng chọn backend khác nhau qua
`apply` từ routes_hg (header X-HG-UI / Referer).

QUAN TRỌNG: Hongguo1 (classic `/hongguo`) = HTTP Downloader :8000 ONLY.
Không RPC, không license.prove / HgLicActivate — license chỉ cho Hongguo2.
"""
from __future__ import annotations

from contextvars import ContextVar

# "rpc" | "http" | "auto" | "http_first" | None
backend_override: ContextVar[str | None] = ContextVar("hg_backend_override", default=None)


def apply(ui: str | None = None, referer: str | None = None) -> str | None:
    u = (ui or "").strip().lower()
    ref = (referer or "").lower()
    choice: str | None = None
    if u in ("2", "hg2", "hongguo2") or "/hongguo2" in ref:
        choice = "rpc"
    elif u in ("1", "hg1", "hongguo", "classic") or (
        "/hongguo" in ref and "/hongguo2" not in ref
    ):
        # Classic: tuyệt đối HTTP — cấm fallback RPC (tránh prove license)
        choice = "http"
    backend_override.set(choice)
    return choice


def force(choice: str) -> str:
    """Ép backend theo namespace URL (/api/hg vs /api/hg2) — nguồn sự thật tách UI."""
    c = (choice or "").strip().lower()
    if c not in ("rpc", "http", "auto", "http_first"):
        c = "http"
    backend_override.set(c)
    return c


def get() -> str | None:
    return backend_override.get()


def is_classic() -> bool:
    return get() == "http"
