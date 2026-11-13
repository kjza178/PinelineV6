"""01 — Context analyzer: SRT Trung → glossary draft (+ style_lock)."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from .schema import apply_style_lock_from_genre, empty_glossary_skeleton


def _heuristic_analyze(segments: list[dict]) -> dict[str, Any]:
    """Không cần API — scaffold để xem flow. Heuristic thô."""
    g = empty_glossary_skeleton()
    blob = "".join((s.get("text") or "") for s in segments[:400])
    # genre thô
    if re.search(r"(修炼|灵力|宗门|仙门|弟子|师父|本座)", blob) and not re.search(
        r"(异能|系统|星球|面板|觉醒)", blob
    ):
        g["genre"] = "tu tiên / xianxia"
        g["content_type"] = "doi_thoai"
        g["tone"] = "Cổ trang huyền ảo; xưng hô theo thứ bậc (ta/ngươi/tại hạ/cô nương…)."
    elif re.search(r"(异能|系统|等级|血量|技能|面板|星球|觉醒)", blob):
        g["genre"] = "Đô thị dị năng / Tinh cầu / Hệ thống"
        g["content_type"] = "doi_thoai"
        combat = bool(re.search(r"(战斗|打|杀|技能|血量|印记|觉醒)", blob))
        g["tone"] = (
            "Mạnh mẽ, kịch tính, chiến đấu/áp chế. "
            "Xưng hô đời thường hiện đại + danh phận quyền lực; "
            "thuật ngữ cứng & thành ngữ giữ CỔ PHONG Hán Việt."
            if combat else
            "Hiện đại hệ thống/game; xưng tôi/anh/em — CẤM Ta–Ngươi / tại hạ."
        )
        if combat:
            g.setdefault("style_lock", {})["mode"] = "mixed"
    elif re.search(r"(警察|嫌疑人|案件|刑侦)", blob):
        g["genre"] = "tội phạm / hình sự"
        g["content_type"] = "doi_thoai"
        g["tone"] = "Hiện đại nghiêm; xưng tôi/anh/cô."
    else:
        g["genre"] = "chưa xác định (heuristic)"
        g["content_type"] = "doi_thoai"
        g["tone"] = "Cần chạy LLM analyze để chốt."

    # Lexicon thô (scaffold) — LLM sẽ thay khi --llm
    from .address_lexicon import ZH_ADDRESS_MAP
    lex = list(ZH_ADDRESS_MAP) + [
        ("宗门", "tông môn", "thuật ngữ"),
        ("灵力", "linh lực", "thuật ngữ"),
        ("修炼", "tu luyện", "thuật ngữ"),
        ("血量", "máu / HP", "game"),
        ("伤害", "sát thương", "game"),
        ("技能", "kỹ năng", "game"),
        ("系统", "hệ thống", "game"),
    ]
    seen: set[str] = set()
    terms: list[dict] = []
    for zh, vi, typ in lex:
        if zh in blob and zh not in seen:
            seen.add(zh)
            terms.append({"zh": zh, "vi": vi, "type": typ})
    # Tên sau 弟子/徒弟 hoặc dạng X儿 (loại 哪儿/这儿…)
    _skip_er = {"哪儿", "这儿", "那儿", "点儿", "会儿", "女儿", "儿子"}
    for m in re.finditer(
        r"(?:弟子|徒弟)\s*([\u4e00-\u9fff]{2,3}(?:[儿子])?)",
        blob,
    ):
        name = m.group(1)
        if name in seen or name in _skip_er:
            continue
        seen.add(name)
        terms.append({"zh": name, "vi": "", "type": "nhân vật?"})
        g["characters"].append({
            "zh": name, "vi": "", "gender": "?", "role": "",
            "aliases": [], "gender_hint": "", "self_ref": "", "status": "",
        })
    for m in re.finditer(r"([\u4e00-\u9fff]{2}儿)", blob):
        name = m.group(1)
        if name in seen or name in _skip_er or name.endswith("哪儿"):
            continue
        if name[-2:] in ("哪儿",):
            continue
        seen.add(name)
        terms.append({"zh": name, "vi": "", "type": "nhân vật?"})
        g["characters"].append({
            "zh": name, "vi": "", "gender": "?", "role": "",
            "aliases": [], "gender_hint": "", "self_ref": "", "status": "",
        })
        if len(g["characters"]) >= 8:
            break
    g["terms"] = terms
    g["pronoun_notes"] = (
        "[scaffold heuristic] Chưa có character map đầy đủ — "
        "chạy với --llm hoặc dán kết quả analyze tay."
    )
    return apply_style_lock_from_genre(g)


def _sample_lines(segments: list[dict], max_chars: int = 28000) -> str:
    """Đầu phim + 10 cửa sổ rải đều cả tập — phim dài 3000+ câu có cast đổi theo arc,
    chỉ lấy đầu/giữa/cuối thì sót nhân vật và quan hệ (tránh nhét hết → timeout)."""
    n = len(segments)
    if n == 0:
        return ""
    idxs: list[int] = list(range(min(100, n)))
    n_win, win = 12, 70
    for w in range(n_win):
        c = int((w + 0.5) * n / n_win)
        idxs.extend(range(max(0, c - win // 2), min(n, c + win // 2)))
    seen: set[int] = set()
    lines: list[str] = []
    size = 0
    for i in idxs:
        if i in seen:
            continue
        seen.add(i)
        s = segments[i]
        t = (s.get("text") or "").strip()
        if not t:
            continue
        row = f"{s.get('i', i)}|{t}"
        if size + len(row) + 1 > max_chars:
            break
        lines.append(row)
        size += len(row) + 1
    return "\n".join(lines)


def _llm_analyze(segments: list[dict], model: str | None = None) -> dict[str, Any]:
    """Gọi LLM qua Pineline llm.chat nếu có; fallback heuristic."""
    try:
        # chạy từ repo root: python -m tools.context_studio …
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from engines.llm import chat, parse_json
    except Exception as e:  # noqa: BLE001
        print(f"[context_studio] LLM không dùng được ({e}) → heuristic")
        return _heuristic_analyze(segments)

    lines = _sample_lines(segments)
    print(f"[context_studio] LLM sample {lines.count(chr(10))+1 if lines else 0} lines "
          f"/ {len(segments)} total (~{len(lines)} chars)")
    sys_p = (
        "Bạn là biên tập PRE-SCAN phụ đề Trung→Việt cho lồng tiếng short-drama/manhua. "
        "KHÔNG dịch thoại. CHỈ trả JSON hợp lệ.\n"
        "\n"
        "NHIỆM VỤ: xác định ĐÚNG thể loại kịch bản + phương án xưng hô + bảng nhân vật/"
        "thuật ngữ cứng — để bước dịch bám pack (không tự đổi setting).\n"
        "\n"
        "PHÂN LOẠI style_lock.mode (hay nhầm — đọc kỹ):\n"
        "1) modern — đô thị đời thường / học đường / tội phạm / vlog; ÍT võ·KH·hệ thống. "
        "Xưng: tôi·anh·em·mày·tao. CẤM ta/ngươi/tại hạ/bổn tọa.\n"
        "2) mixed — ĐÔ THỊ DỊ NĂNG / TINH CẦU / HỆ THỐNG + chiến đấu / khoa huyễn / áp chế. "
        "Dấu hiệu: 异能, 觉醒, 星球, 印记, 等级, 血量, 技能, 面板, 奴隶/公主 chức vị… "
        "Xưng hô ĐỜI THƯỜNG = hiện đại (tôi/cậu/mày…); danh phận (công chúa, thiếu gia) giữ. "
        "BẮT BUỘC CỔ PHONG Hán Việt cho: 成语, chiêu thức, khí tức, ấn ký, tinh cầu, "
        "cấp bậc, thoại võ/áp chế. "
        "tone PHẢI ghi rõ «cổ phong / Hán Việt». CẤM tại hạ/trẫm/bệ hạ mặc định. "
        "CẤM mode=modern thuần nếu có chiến đấu + dị năng/tinh cầu.\n"
        "3) xianxia — tu tiên / cổ trang / wuxia. Dấu hiệu: 修炼, 灵力, 宗门, 仙, 弟子·师父. "
        "Mới dùng ta/ngươi + địa chỉ cổ theo thứ bậc.\n"
        "\n"
        "NHÂN VẬT: chỉ NGƯỜI có tên / danh xưng xuất hiện ≥2 lần (KHÔNG lấy cụm như 三轮全力, "
        "không lấy chiêu thức). ASR hay nghe nhầm tên đồng âm (苏林/苏灵/苏黎) — nếu cùng 1 người "
        "thì gộp vào aliases, nếu khác người thì ghi rõ role/gender. "
        "relation: quan hệ với nhân vật chính (đồng minh/kẻ thù/trưởng bối/thuộc hạ/người yêu…). "
        "ref3: người khác nhắc tới nhân vật này ở ngôi 3 bằng gì (他/她 → hắn / anh ta / cô ấy / "
        "cô ta / lão / ông ta / bà ta / nó) — theo phe và vai vế; KHÔNG dùng «ông tôi/cô tôi».\n"
        "\n"
        "XƯNG HÔ (pronouns[]):\n"
        "- CHỈ ghi cặp who→to THẬT SỰ nói với nhau trong transcript (có bằng chứng); "
        "thiếu bằng chứng thì BỎ, KHÔNG điền mặc định «tôi – anh».\n"
        "- self/call theo quan hệ: kẻ thù/khinh miệt → tao–mày; trưởng bối → ta/lão phu có thể "
        "hạ về tôi theo style_lock, gọi hậu bối «nhóc/con»; hậu bối gọi «lão tổ/tiền bối/gia chủ»; "
        "cấp dưới gọi «gia chủ/công chúa/thiếu gia»; bạn bè cùng lứa → tôi–cậu.\n"
        "- who/to dùng tên VI đã Hán Việt (khớp characters[].vi).\n"
        "- Không nhảy mẹ–con chỉ vì gặp 娘/妈 nếu cảnh đối đầu/nô lệ/chiến "
        "(thường ASR lệch 那→娘).\n"
        "- Công chúa / phụ vương = danh phận, không đổi cả cast sang cổ cung đình.\n"
        "\n"
        "TERMS: ưu tiên world-building cứng Hán Việt (tinh cầu, ấn ký, kỹ năng, HP/máu). "
        "type rõ: location|skill|system|rank|chengyu|status|khác.\n"
        "Tên: 苏林→Tô Lâm (Hán Việt), KHÔNG phiên âm bừa. gender thiếu bằng chứng → \"?\".\n"
    )
    user = f"""Transcript mẫu (i|zh — đầu/giữa/cuối; đủ để chốt genre + xưng hô):
\"\"\"{lines}\"\"\"

Trả JSON đúng schema:
{{
  "content_type": "doi_thoai|dan_chuyen|khac",
  "genre": "nhãn cụ thể, vd: Đô thị dị năng / Tinh cầu / Hệ thống",
  "tone": "2-3 câu: cảm xúc + xưng hô đời thường + (nếu mixed) cổ phong Hán Việt chỗ nào",
  "style_lock": {{
    "mode": "modern|xianxia|mixed",
    "pronoun_policy": "1-2 câu quy ước xưng hô + cấm/cho phép",
    "forbid": [],
    "prefer": [],
    "confidence": 0.0,
    "time_period": "contemporary|ancient|contemporary+combat-literary",
    "emotional_style": "..."
  }},
  "characters": [
    {{"zh":"","vi":"Hán Việt","gender":"nam|nữ|?","role":"vai ngắn",
      "aliases":[],"gender_hint":"bằng chứng ngắn hoặc rỗng","self_ref":"","status":"",
      "relation":"quan hệ với nhân vật chính","ref3":"hắn|anh ta|cô ấy|cô ta|lão|ông ta|…"}}
  ],
  "terms": [
    {{"zh":"","vi":"Hán Việt nếu cứng","type":"location|skill|system|rank|chengyu|status|khác"}}
  ],
  "pronouns": [
    {{"who":"tên vi","to":"tên vi hoặc chung","self":"tôi/cậu/…","call":"cách gọi"}}
  ],
  "pronoun_notes": "1-3 câu: phương án xưng hô chính + cảnh dễ nhầm"
}}"""
    out = chat(
        [{"role": "system", "content": sys_p}, {"role": "user", "content": user}],
        model=model or os.getenv("TRANSLATE_MODEL") or os.getenv("LLM_MODEL") or None,
        temperature=0.15,
        json_mode=True,
        max_tokens=8192,
        timeout=600,
        retries=3,
    )
    if not (out or "").strip():
        print("[context_studio] LLM trả rỗng → heuristic")
        return _heuristic_analyze(segments)
    try:
        data = parse_json(out)
        if not isinstance(data, dict):
            raise ValueError("not dict")
        base = empty_glossary_skeleton()
        base.update({k: data.get(k, base.get(k)) for k in base if k != "_meta"})
        if isinstance(data.get("style_lock"), dict):
            base["style_lock"] = {**base["style_lock"], **data["style_lock"]}
        base["_meta"]["source"] = "context_studio+llm"
        return apply_style_lock_from_genre(base)
    except Exception as e:  # noqa: BLE001
        print(f"[context_studio] parse LLM lỗi ({e}) → heuristic")
        return _heuristic_analyze(segments)


def analyze(segments: list[dict], *, use_llm: bool = False,
            model: str | None = None) -> dict[str, Any]:
    if use_llm:
        return _llm_analyze(segments, model=model)
    return _heuristic_analyze(segments)


def summarize(glossary: dict) -> dict[str, Any]:
    """Tóm tắt cho UI."""
    lock = glossary.get("style_lock") or {}
    return {
        "setting": lock.get("time_period") or "",
        "genre": glossary.get("genre") or "",
        "style": lock.get("mode") or "unknown",
        "characters": len(glossary.get("characters") or []),
        "terminology": len(glossary.get("terms") or []),
        "pronouns": len(glossary.get("pronouns") or []),
        "confidence": lock.get("confidence") or 0,
        "tone": (glossary.get("tone") or "")[:120],
    }
