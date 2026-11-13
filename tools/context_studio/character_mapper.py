"""02 — Character + relation map: ai tự xưng gì khi nói với ai."""
from __future__ import annotations

import re
from typing import Any


# Role keywords trong thoại Trung → role vi + gợi ý xưng
# fix duplicate 师父|师父 in pattern - use 师父|师傅 only
_ROLE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"师父|师傅"), "sư phụ", "master"),
    (re.compile(r"师尊"), "sư tôn", "master"),
    (re.compile(r"弟子|徒弟"), "đệ tử", "disciple"),
    (re.compile(r"本座"), "bổn tọa", "noble"),
    (re.compile(r"掌门"), "chưởng môn", "sect_lead"),
    (re.compile(r"帮主"), "bang chủ", "sect_lead"),
    (re.compile(r"教主"), "giáo chủ", "sect_lead"),
    (re.compile(r"殿下"), "điện hạ", "royalty"),
    (re.compile(r"陛下|皇上"), "bệ hạ", "royalty"),
    (re.compile(r"姑娘"), "cô nương", "lady"),
    (re.compile(r"公子"), "công tử", "gentry"),
    (re.compile(r"小姐"), "tiểu thư", "lady"),
    (re.compile(r"少爷"), "thiếu gia", "gentry"),
]


def _lock_mode(glossary: dict) -> str:
    lock = glossary.get("style_lock") or {}
    mode = (lock.get("mode") or "").strip().lower()
    if mode in ("xianxia", "modern", "mixed"):
        return mode
    blob = f"{glossary.get('genre','')} {glossary.get('tone','')}".lower()
    if any(k in blob for k in ("tu tiên", "cổ trang", "xianxia", "wuxia", "kiếm hiệp")):
        return "xianxia"
    if any(k in blob for k in ("đô thị", "hiện đại", "dị năng", "hệ thống")):
        return "modern"
    return "unknown"


_NOT_NAME_CHARS = set("一二三四五六七八九十百千万亿两几全力们的了是在都也就被把给和与请快打杀轮次个")


def _plausible_name(name: str, blob: str) -> bool:
    """«弟子三轮全力一击» → «三轮全力» không phải tên: loại số/hư từ, cần xuất hiện ≥2 lần."""
    return not (set(name) & _NOT_NAME_CHARS) and blob.count(name) >= 2


def _char_key(c: dict) -> str:
    return (c.get("vi") or c.get("zh") or "").strip()


def _ensure_char(
    chars: list[dict],
    *,
    zh: str,
    vi: str,
    role: str = "",
    gender: str = "?",
    status: str = "",
    self_ref: str = "",
) -> dict:
    for c in chars:
        if (c.get("zh") or "") == zh or (c.get("vi") or "") == vi:
            if role and not c.get("role"):
                c["role"] = role
            if status and not c.get("status"):
                c["status"] = status
            if self_ref and not c.get("self_ref"):
                c["self_ref"] = self_ref
            return c
    row = {
        "zh": zh,
        "vi": vi,
        "gender": gender,
        "role": role,
        "aliases": [],
        "gender_hint": "",
        "self_ref": self_ref,
        "status": status,
        "addresses": {},
    }
    chars.append(row)
    return row


def infer_roles_from_segments(
    glossary: dict[str, Any],
    segments: list[dict] | None = None,
) -> dict[str, Any]:
    """Bổ sung nhân vật/role từ từ khóa thoại (heuristic, không diarization)."""
    chars: list[dict] = []
    for c in glossary.get("characters") or []:
        if isinstance(c, str) and c.strip():
            chars.append({
                "zh": c, "vi": c, "gender": "?", "role": "", "aliases": [],
                "gender_hint": "", "self_ref": "", "status": "", "addresses": {},
            })
        elif isinstance(c, dict):
            chars.append(dict(c))

    blob = "".join((s.get("text") or "") for s in (segments or [])[:400])
    # 弟子苏婉儿 → gắn role đệ tử cho 苏婉儿
    for m in re.finditer(r"弟子\s*([\u4e00-\u9fff]{2,4})", blob):
        name = m.group(1)
        if name in ("恭送", "拜见", "在此") or not _plausible_name(name, blob):
            continue
        _ensure_char(chars, zh=name, vi="", role="đệ tử", status="disciple",
                     self_ref="đệ tử")

    # Có tiếng gọi 师父 → thêm slot sư phụ nếu chưa có
    if re.search(r"师父|师傅|师尊", blob):
        master = next(
            (c for c in chars if (c.get("role") or "") in ("sư phụ", "sư tôn", "chưởng môn")
             or (c.get("status") or "") in ("master", "noble", "sect_lead")),
            None,
        )
        if master is None:
            # nếu có 本座 trong blob → coi speaker bổn tọa = sư phụ ẩn danh
            self_ref = "bổn tọa" if "本座" in blob else "ta"
            _ensure_char(
                chars, zh="师父", vi="Sư phụ", role="sư phụ",
                status="master", self_ref=self_ref,
            )
        elif "本座" in blob and not master.get("self_ref"):
            master["self_ref"] = "bổn tọa"
            if not master.get("role"):
                master["role"] = "sư phụ"
            master["status"] = master.get("status") or "master"

    # Gắn role theo pattern còn lại
    for c in chars:
        zh = c.get("zh") or ""
        text_hit = zh and zh in blob
        for pat, role_vi, status in _ROLE_PATTERNS:
            if pat.search(zh) or (text_hit and False):
                if not c.get("role"):
                    c["role"] = role_vi
                if not c.get("status"):
                    c["status"] = status
        # 儿 tên nữ gợi ý
        if (c.get("gender") or "?") == "?" and re.search(r"[婉琳雪梦月儿姐娘]$", zh):
            if zh.endswith("儿") or any(x in zh for x in ("婉", "琳", "雪", "梦")):
                c["gender"] = "nữ"
                c["gender_hint"] = c.get("gender_hint") or "tên nữ gợi ý (heuristic)"

    glossary["characters"] = chars
    return glossary


def build_relation_pronouns(glossary: dict[str, Any]) -> dict[str, Any]:
    """Chuẩn hóa pronouns[] + relation_matrix — CHỈ cặp có bằng chứng (từ LLM/tay).

    Không tự điền N×N theo giới tính: prompt dịch coi pronouns là BẮT BUỘC, cặp bịa
    («lão tổ gọi nhân vật chính là anh», «kẻ thù gọi nhau anh») làm xưng hô loạn.
    """
    mode = _lock_mode(glossary)
    pronouns = [
        p for p in (glossary.get("pronouns") or [])
        if isinstance(p, dict) and p.get("who") and p.get("to") and p.get("call")
    ]
    glossary["pronouns"] = pronouns
    glossary["relation_matrix"] = _matrix_from_pronouns(pronouns)

    if not glossary.get("pronoun_notes"):
        if mode == "xianxia":
            glossary["pronoun_notes"] = (
                "[relation map] Xưng hô theo cặp who→to (sư–đồ / khiêm–tôn). "
                "Bám pronouns + style_lock.address_bank; không đổ ta/ngươi mọi câu."
            )
        else:
            glossary["pronoun_notes"] = (
                "[relation map] Xưng hô hiện đại theo cặp; CẤM Ta–Ngươi / địa chỉ cổ."
            )
    return glossary


def _matrix_from_pronouns(pronouns: list[dict]) -> list[dict]:
    """Dạng phẳng tiện UI: who | to | self | call."""
    out = []
    for p in pronouns:
        if not isinstance(p, dict):
            continue
        out.append({
            "who": p.get("who") or "",
            "to": p.get("to") or "",
            "self": p.get("self") or "",
            "call": p.get("call") or "",
        })
    return out


def sanitize_pronouns(glossary: dict[str, Any]) -> dict[str, Any]:
    """Tách pronouns.to kiểu 'A, B, C' → nhiều rule; modern: tẩy self/call cổ."""
    import re

    mode = _lock_mode(glossary)
    raw = [p for p in (glossary.get("pronouns") or []) if isinstance(p, dict)]
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    ancient_self = {"ta", "ngươi", "tại hạ", "bổn tọa", "bản tôn", "nô tài", "nô tỳ"}
    no_ancient = mode in ("modern", "mixed")

    def _strip_ancient(v: str, repl: str) -> str:
        opts = [x.strip() for x in re.split(r"[/|]", v) if x.strip()]
        keep = [x for x in opts if x.casefold() not in ancient_self]
        if keep:
            return "/".join(keep)
        return repl if opts else ""

    for p in raw:
        who = (p.get("who") or "").strip()
        to_raw = (p.get("to") or "").strip()
        self_ = (p.get("self") or "").strip()
        call0 = (p.get("call") or "").strip()
        if no_ancient:
            self_ = _strip_ancient(self_, "tôi")
            call0 = _strip_ancient(call0, "mày" if self_ == "tao" else "cậu")
            if call0.casefold() == "tôi":
                call0 = ""
        parts = [x.strip() for x in re.split(r"[,，/;]+", to_raw) if x.strip()]
        if not parts:
            parts = ["chung"]
        for to in parts:
            key = (who, to)
            if not who or key in seen or not call0:
                continue
            seen.add(key)
            out.append({
                "who": who, "to": to,
                "self": self_ or ("tôi" if no_ancient else "ta"),
                "call": call0,
            })

    glossary["pronouns"] = out
    glossary["relation_matrix"] = _matrix_from_pronouns(out)
    return glossary


def enrich_characters(
    glossary: dict[str, Any],
    segments: list[dict] | None = None,
) -> dict[str, Any]:
    """Chuẩn hóa characters + suy role + sinh pronouns quan hệ."""
    if segments:
        glossary = infer_roles_from_segments(glossary, segments)

    chars = glossary.get("characters") or []
    if not isinstance(chars, list):
        return glossary
    pronouns = glossary.get("pronouns") or []
    self_by_who: dict[str, str] = {}
    for pr in pronouns if isinstance(pronouns, list) else []:
        if not isinstance(pr, dict):
            continue
        who = (pr.get("who") or "").strip()
        self_ = (pr.get("self") or "").strip()
        if who and self_:
            self_by_who.setdefault(who, self_)

    out = []
    for c in chars:
        if isinstance(c, str):
            out.append({
                "zh": c, "vi": c, "gender": "?", "role": "", "aliases": [],
                "gender_hint": "", "self_ref": "", "status": "", "addresses": {},
            })
            continue
        if not isinstance(c, dict):
            continue
        vi = c.get("vi") or ""
        row = {
            "zh": c.get("zh") or "",
            "vi": vi,
            "gender": c.get("gender") or "?",
            "role": c.get("role") or "",
            "aliases": c.get("aliases") or [],
            "gender_hint": c.get("gender_hint") or "",
            "self_ref": c.get("self_ref") or self_by_who.get(vi or "", "")
            or self_by_who.get(c.get("zh") or "", ""),
            "status": c.get("status") or "",
            "relation": c.get("relation") or "",
            "ref3": c.get("ref3") or "",
            "addresses": c.get("addresses") or {},
        }
        # modern: đừng để self_ref = ta/ngươi
        if _lock_mode(glossary) == "modern":
            sr = (row.get("self_ref") or "").strip().casefold()
            if sr in ("ta", "ngươi", "tại hạ", "bổn tọa"):
                row["self_ref"] = "tôi"
        addr = dict(row["addresses"]) if isinstance(row["addresses"], dict) else {}
        who_keys = {vi, row["zh"], _char_key(row)}
        who_keys.discard("")
        for pr in pronouns if isinstance(pronouns, list) else []:
            if not isinstance(pr, dict):
                continue
            if (pr.get("who") or "") in who_keys and pr.get("to") and pr.get("call"):
                addr[str(pr["to"])] = str(pr["call"])
        row["addresses"] = addr
        out.append(row)
    glossary["characters"] = out

    glossary = sanitize_pronouns(glossary)
    glossary = build_relation_pronouns(glossary)

    # sync addresses từ pronouns mới
    pronouns = glossary.get("pronouns") or []
    for c in glossary["characters"]:
        key_vi = _char_key(c)
        addr = dict(c.get("addresses") or {})
        for pr in pronouns:
            if not isinstance(pr, dict):
                continue
            if (pr.get("who") or "") in {key_vi, c.get("zh"), c.get("vi")} and pr.get("to"):
                if pr.get("call"):
                    addr[str(pr["to"])] = str(pr["call"])
                if pr.get("self") and not c.get("self_ref"):
                    c["self_ref"] = str(pr["self"])
        c["addresses"] = addr

    # tone modern: cắt cụm cổ nếu LLM vẫn ghi
    if _lock_mode(glossary) == "modern":
        tone = glossary.get("tone") or ""
        if "ta" in tone.lower() or "ngươi" in tone.lower():
            glossary["tone"] = (
                "Hiện đại / hệ thống game hóa; xưng hô tôi·anh·em·mày·tao. "
                "CẤM Ta–Ngươi và địa chỉ cổ."
            )
    return glossary


def validate_pronouns(glossary: dict[str, Any]) -> list[str]:
    """Cảnh báo who/to không khớp tên trong characters (vi hoặc zh)."""
    names = set()
    for c in glossary.get("characters") or []:
        if not isinstance(c, dict):
            continue
        for k in (c.get("vi"), c.get("zh"), _char_key(c)):
            if k and str(k).strip():
                names.add(str(k).strip())
    warns = []
    for pr in glossary.get("pronouns") or []:
        if not isinstance(pr, dict):
            continue
        who, to = (pr.get("who") or "").strip(), (pr.get("to") or "").strip()
        if who and who not in names:
            warns.append(f"pronouns.who={who!r} không có trong characters")
        if to and to not in names and to not in ("chung", "narrator", "*"):
            warns.append(f"pronouns.to={to!r} không có trong characters")
        if not (pr.get("self") or "").strip():
            warns.append(f"pronouns thiếu self: {who!r}→{to!r}")
        if not (pr.get("call") or "").strip():
            warns.append(f"pronouns thiếu call: {who!r}→{to!r}")
    return warns
