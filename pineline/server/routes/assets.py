"""Assets + LLM models: cookies / voices / frames / brand / clone — LIVE registry.

Thân handler giữ trong root `server.py`. Module này chỉ path → hàm.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["assets"])

_ROUTES: list[tuple[list[str], str, str]] = [
    (["GET"], "/api/cookies", "cookies_status"),
    (["POST"], "/api/cookies", "import_cookies"),
    (["GET"], "/api/voices", "list_voices"),
    (["GET"], "/api/frames", "list_frames"),
    (["GET"], "/api/brand-logos", "list_brand_logos"),
    (["POST"], "/api/clone-voice", "clone_voice"),
    (["GET"], "/api/voice-sample", "voice_sample"),
    (["GET"], "/api/models", "get_available_llm_models"),
    (["GET"], "/api/llm/models", "get_available_llm_models"),
]


def attach(server_mod) -> None:
    for methods, path, name in _ROUTES:
        fn = getattr(server_mod, name, None)
        if fn is None:
            raise AttributeError(f"server thiếu handler assets '{name}' cho {methods} {path}")
        router.add_api_route(path, fn, methods=methods)


__all__ = ["router", "attach", "_ROUTES"]
