"""Phân loại lỗi theo log / cur_text / error (ASR · TTS · mux · …)."""
from __future__ import annotations

import re
from typing import Any


# class -> policy
# safe_action: action name in heal OR notify_only / wait_vram / none
CLASS_RULES: list[tuple[str, re.Pattern[str], dict[str, Any]]] = [
    ("missing_voice", re.compile(r"chưa chọn giọng|không có giọng|voice.*missing|no voice", re.I), {
        "safe_action": "notify_only",
        "aggressive_action": None,
        "hint": "Chọn/thêm giọng clone rồi TTS lại",
    }),
    ("cuda_oom", re.compile(r"out of memory|cuda.*oom|oom\b|cudnn.*alloc|hiperroroutofmemory", re.I), {
        "safe_action": "wait_vram_retry",
        "aggressive_action": None,
        "hint": "Hết VRAM — đợi free_gpu rồi retry 1 lần, không spam",
    }),
    ("disk_full", re.compile(r"no space|enospc|disk.*(full|quota)|not enough space", re.I), {
        "safe_action": "notify_only",
        "aggressive_action": None,
        "hint": "Ổ đĩa đầy — xóa rác rồi mux/tts lại",
    }),
    ("source_corrupt", re.compile(
        r"invalid data|moov atom|error while decoding|does not contain any stream|failed to open|cannot open",
        re.I,
    ), {
        "safe_action": "notify_only",
        "aggressive_action": "download_or_import",
        "hint": "Source hỏng — import/tải lại, đừng asr force mù",
    }),
    ("nvenc_fail", re.compile(r"nvenc|h264_nvenc|hevc_nvenc|nvcuda", re.I), {
        "safe_action": "mux_retry_cpu_hint",
        "aggressive_action": None,
        "hint": "NVENC lỗi — thử lại mux hoặc đổi encoder",
    }),
    ("file_missing", re.compile(r"no such file|filenotfound|không tìm thấy|missing.*\.(wav|mp4|json)", re.I), {
        "safe_action": "repair_missing_artifact",
        "aggressive_action": None,
        "hint": "Thiếu artifact — chạy lại bước tạo file đó",
    }),
    ("stopped", re.compile(r"đã dừng|stopped|cancelled|keyboardinterrupt", re.I), {
        "safe_action": "none",
        "aggressive_action": None,
        "hint": "User dừng — không auto heal",
    }),
]


def classify_error(stage: str | None, text: str | None, *, extra: str | None = None) -> dict:
    blob = " ".join(x for x in (stage or "", text or "", extra or "") if x)
    if not blob.strip():
        return {
            "class": "unknown",
            "confidence": 0.2,
            "safe_action": "generic_stage_retry",
            "aggressive_action": None,
            "hint": "Không đọc được log — chỉ heal generic nếu có",
            "matched": None,
        }
    for name, pat, pol in CLASS_RULES:
        if pat.search(blob):
            return {
                "class": name,
                "confidence": 0.85,
                "stage": stage,
                "safe_action": pol.get("safe_action"),
                "aggressive_action": pol.get("aggressive_action"),
                "hint": pol.get("hint"),
                "matched": pat.pattern[:80],
            }
    return {
        "class": "unknown",
        "confidence": 0.4,
        "stage": stage,
        "safe_action": "generic_stage_retry",
        "aggressive_action": None,
        "hint": "Lỗi chưa nhận dạng — dùng heal generic thận trọng",
        "matched": None,
    }


def classify_stage_status(stage: str, st: dict) -> dict | None:
    if (st or {}).get("status") != "failed":
        return None
    text = " ".join(
        str(st.get(k) or "") for k in ("error", "cur_text", "detail", "message")
    )
    return classify_error(stage, text)


# Map classifier safe_action → heal action names (agent_heal)
CLASS_TO_HEAL = {
    "notify_only": None,  # skip auto
    "none": None,
    "wait_vram_retry": "tts_run",  # or mux — caller picks by stage
    "mux_retry_cpu_hint": "mux_retry",
    "repair_missing_artifact": "generic",  # fall through to existing safe actions
    "generic_stage_retry": "generic",
}


def filter_actions_by_class(
    actions_safe: list[dict],
    classifications: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Loại action oan khi class bảo notify_only / none."""
    block_auto = False
    notes = []
    for c in classifications:
        sa = c.get("safe_action")
        if sa in ("notify_only", "none"):
            block_auto = True
            notes.append(f"{c.get('stage')}:{c.get('class')} → {c.get('hint')}")
    if not block_auto:
        return actions_safe, notes
    # giữ heal_stale; bỏ tts/mux/asr spam
    kept = [a for a in actions_safe if a.get("action") == "heal_stale_running"]
    return kept, notes
