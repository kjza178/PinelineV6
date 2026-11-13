"""Heuristic number / name flags — không sửa transcript."""
from __future__ import annotations

import re
from typing import Any

# 三万一千一 / 800亿 / 3100亿 …
_NUM_RE = re.compile(
    r"[零一二三四五六七八九十百千万亿兆两壹贰叁肆伍陆柒捌玖拾佰仟萬億兩\d\.]+"
    r"(?:[万萬亿億兆]\d*)*"
)


def scan_number_flags(segments: list[dict], window: int = 8) -> list[dict]:
    """Cờ số đáng ngờ: dòng có 万/亿/兆 hoặc cụm số lạ so với lân cận có 亿."""
    flags: list[dict] = []
    texts = [(s.get("i"), s.get("text") or "") for s in segments]
    has_yi = [bool(re.search(r"[亿億兆]", t)) for _, t in texts]

    for idx, (line_i, text) in enumerate(texts):
        if not re.search(r"[万萬亿億兆]", text) and not re.search(r"\d{4,}", text):
            continue
        nums = _NUM_RE.findall(text)
        if not nums:
            continue
        # xung đột: dòng có 万/千 nhỏ trong cửa sổ có 亿 lớn
        neighbor_yi = any(
            has_yi[j]
            for j in range(max(0, idx - window), min(len(texts), idx + window + 1))
            if j != idx
        )
        small_wan = bool(re.search(r"[一二三四五六七八九十\d]+万(?![亿億])", text))
        suspicious = neighbor_yi and small_wan
        flags.append({
            "line": int(line_i) if line_i is not None else idx,
            "category": "number",
            "original": text,
            "detected": nums[:8],
            "problem": (
                "Cụm số nhỏ (万) nằm gần ngữ cảnh có 亿/兆 — có thể ASR lệch; "
                "KHÔNG tự sửa transcript."
                if suspicious
                else "Có đơn vị 万/亿/兆 — giữ nguyên bậc số khi dịch."
            ),
            "suspicious": bool(suspicious),
            "correction": None,
        })
    return flags


def empty_glossary_skeleton() -> dict[str, Any]:
    """Schema khớp Pineline translate.build_plan (+ style_lock)."""
    return {
        "content_type": "",
        "genre": "",
        "tone": "",
        "style_lock": {
            "mode": "unknown",  # modern | xianxia | mixed | unknown
            "pronoun_policy": "",
            "forbid": [],
            "prefer": [],
            "address_bank": {},  # nhóm ngữ dụng — xem address_lexicon.py
            "confidence": 0.0,
            "time_period": "",
            "emotional_style": "",
        },
        "characters": [],
        "terms": [],
        "pronouns": [],
        "pronoun_notes": "",
        "_meta": {
            "source": "context_studio",
            "version": "0.1.0-scaffold",
        },
    }


def apply_style_lock_from_genre(glossary: dict) -> dict:
    """Suy style_lock từ genre/tone + đổ lexicon đầy đủ (address_lexicon)."""
    from .address_lexicon import enrich_style_lock

    g = glossary if isinstance(glossary, dict) else {}
    lock = dict(g.get("style_lock") or {})
    blob = f"{g.get('genre','')} {g.get('tone','')} {g.get('content_type','')}".lower()
    ancient_keys = (
        "tu tiên", "cổ trang", "xianxia", "wuxia", "kiếm hiệp",
        "huyền ảo cổ", "tiên hiệp", "võ hiệp", "cung đấu", "cổ đại",
    )
    modern_keys = (
        "đô thị", "hiện đại", "crime", "thriller", "đời thường", "học đường",
        "dị năng", "hệ thống", "tinh cầu", "khoa huyễn", "game",
    )
    mode = lock.get("mode") or "unknown"
    mode_forced = False
    combatish = any(k in blob for k in (
        "võ", "chiến", "combat", "khoa huyễn", "khoa học viễn tưởng", "sci-fi",
        "tinh cầu", "dị năng", "giác tỉnh", "ấn ký", "huyền ảo",
    ))
    urbanish = any(k in blob for k in ("đô thị", "hiện đại", "dị năng", "hệ thống", "tinh cầu"))
    # LLM gắn xianxia thuần cho dị năng/tinh cầu → hạ xuống mixed (không modern phẳng)
    if urbanish and mode == "xianxia":
        if not any(k in blob for k in ("tu tiên", "cổ trang", "xianxia", "wuxia", "kiếm hiệp")):
            mode = "mixed" if combatish else "modern"
            mode_forced = True
    if mode in ("", "unknown"):
        if any(k in blob for k in ancient_keys) and not any(k in blob for k in ("đô thị", "hiện đại", "dị năng")):
            mode = "xianxia"
        elif urbanish and combatish:
            mode = "mixed"
        elif any(k in blob for k in modern_keys):
            mode = "modern"
        elif "hệ thống" in blob or "dị năng" in blob:
            mode = "mixed" if combatish else "modern"
    # Đô thị dị năng + võ/KH: nâng modern → mixed (giữ xưng hô đời thường + sắc cổ võ)
    if mode == "modern" and urbanish and combatish:
        mode = "mixed"
    lock["mode"] = mode
    if mode_forced and mode == "modern":
        # bỏ lexicon cổ do LLM nhầm mode thuần xianxia
        lock["forbid"] = []
        lock["prefer"] = []
        lock["pronoun_policy"] = ""
        lock.pop("address_bank", None)
    lock = enrich_style_lock(lock, mode)

    if not lock.get("confidence"):
        lock["confidence"] = 0.5 if mode != "unknown" else 0.0
    g["style_lock"] = lock
    return g
