"""Jobs API: /api/jobs/* — LIVE registry trong khung pineline V5.

Thân handler vẫn trong root `server.py` (xen helper). Module này chỉ
đăng ký path → hàm trên `S`, để bản đồ API nằm trong `pineline/`.

Đợt sau có thể chuyển từng body sang đây (pattern queue.py + lazy S).
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["jobs"])

# (methods, path, attr trên server module)
_ROUTES: list[tuple[list[str], str, str]] = [
    (["POST"], "/api/jobs", "create_job"),
    (["POST"], "/api/jobs/local-path", "create_local_job_path"),
    (["POST"], "/api/jobs/local-batch", "create_local_job_batch"),
    (["POST"], "/api/jobs/local", "create_local_job_upload"),
    (["POST"], "/api/jobs/{job_id}/start", "start_job"),
    (["POST"], "/api/jobs/{job_id}/run/{stage}", "run_stage"),
    (["POST"], "/api/jobs/{job_id}/stop", "stop_job"),
    (["POST"], "/api/jobs/{job_id}/mark-done", "mark_job_done"),
    (["POST"], "/api/jobs/{job_id}/translate-fill", "translate_fill"),
    (["POST"], "/api/jobs/{job_id}/script-line", "edit_script_line"),
    (["POST"], "/api/jobs/{job_id}/script-clean-spam", "clean_script_spam"),
    (["POST"], "/api/jobs/{job_id}/tts-voice", "set_tts_voice"),
    (["POST"], "/api/jobs/{job_id}/run-all", "run_all"),
    (["POST"], "/api/jobs/{job_id}/asr-engine", "set_asr_engine"),
    (["GET"], "/api/jobs/{job_id}/asr-sources", "list_asr_sources"),
    (["POST"], "/api/jobs/{job_id}/asr-select", "select_asr_source"),
    (["GET"], "/api/jobs/{job_id}/context-studio", "get_context_studio"),
    (["POST"], "/api/jobs/{job_id}/context-studio", "run_context_studio_api"),
    (["GET"], "/api/jobs/{job_id}/translate-alts", "get_translate_alts"),
    (["POST"], "/api/jobs/{job_id}/translate-alts", "run_translate_alts_api"),
    (["POST"], "/api/jobs/{job_id}/apply-alt", "apply_alt_pick"),
    (["POST"], "/api/jobs/{job_id}/hardsub-asr", "run_hardsub_asr_api"),
    (["GET"], "/api/jobs/{job_id}/hardsub-asr", "get_hardsub_asr"),
    (["GET"], "/api/jobs", "list_jobs"),
    (["GET"], "/api/jobs/{job_id}", "job_detail"),
    (["GET"], "/api/jobs/{job_id}/tts-wav-ok", "tts_wav_ok"),
    (["POST"], "/api/jobs/{job_id}/tts", "regen_tts"),
    (["POST"], "/api/jobs/{job_id}/tts-clip", "regen_clip"),
    (["GET"], "/api/jobs/{job_id}/srt/{lang}", "job_srt"),
    (["POST"], "/api/jobs/{job_id}/source", "upload_source"),
    (["POST"], "/api/jobs/{job_id}/source-path", "import_source_path"),
    (["POST"], "/api/jobs/{job_id}/audio", "upload_audio"),
    (["DELETE"], "/api/jobs/{job_id}/audio", "delete_audio"),
    (["POST"], "/api/jobs/{job_id}/translate", "retranslate_job"),
    (["POST"], "/api/jobs/{job_id}/mux", "mux_job"),
    (["POST"], "/api/jobs/{job_id}/assemble", "assemble_job"),
    (["GET"], "/api/jobs/{job_id}/split-plan", "get_split_plan"),
    (["POST"], "/api/jobs/{job_id}/split-parts", "api_split_job_parts"),
    (["POST"], "/api/jobs/{job_id}/merge-parts", "api_merge_job_parts"),
    (["POST"], "/api/jobs/{job_id}/reveal", "reveal_job"),
    (["GET"], "/api/jobs/{job_id}/file/{path:path}", "job_file"),
    (["GET", "HEAD"], "/api/jobs/{job_id}/video", "job_video"),
    (["DELETE"], "/api/jobs/{job_id}", "delete_job"),
]


def attach(server_mod) -> None:
    """Gắn handler từ server module lên router; gọi trước include_router.

    GET+HEAD tách route + operation_id riêng — tránh Duplicate Operation ID OpenAPI.
    """
    for methods, path, name in _ROUTES:
        fn = getattr(server_mod, name, None)
        if fn is None:
            raise AttributeError(f"server thiếu handler jobs '{name}' cho {methods} {path}")
        if len(methods) == 1:
            router.add_api_route(path, fn, methods=methods, operation_id=name)
            continue
        for m in methods:
            oid = name if m.upper() == "GET" else f"{name}_{m.lower()}"
            router.add_api_route(path, fn, methods=[m], operation_id=oid)


__all__ = ["router", "attach", "_ROUTES"]
