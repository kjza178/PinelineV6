"""Gợi ý thành ngữ / lóng ZH→VI cho prompt review (dò chuỗi con trong ZH của lô).

Nguồn: data/lexicon/idioms_zh_vi.json ({"entries":[{zh, vi, forbid[], note?, re?}]}) + terms type
chengyu/idiom/style trong plan (glossary.json). Chỉ là GỢI Ý trong prompt — không ghi đè VI.

`re` (tuỳ chọn, review 2026-11-12): regex áp TỪNG câu ZH — mục chỉ được gợi ý khi khớp. Dùng cho mục
đa nghĩa: 我去 chỉ là thán từ khi đứng riêng (「我去。」「我去，…」), còn 「我去一趟」 = «tôi đi một chuyến».
Regex hỏng → bỏ qua mục (không ném).

`src` (2026-11-12 next): nguồn mục — translate_rules | golden_derived | expert (thiếu = golden_derived, an toàn).
SIMPLE_LEXICON_EXCLUDE_GOLDEN=1 → bỏ mục golden_derived khỏi gợi ý (A/B đo trung thực, biến thể simple_noleak).
Mặc định 0 = như cũ (mọi mục).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from . import cfg

ROOT = Path(__file__).resolve().parents[2]  # V6: engines/translate_simple/ → gốc cây
LEXICON_PATH = ROOT / "data" / "lexicon" / "idioms_zh_vi.json"
_cache: dict = {"mtime": None, "entries": []}
_lock = threading.Lock()


def load(path: Path | None = None) -> list[dict]:
    p = Path(path) if path else LEXICON_PATH
    with _lock:
        try:
            mt = p.stat().st_mtime
        except OSError:
            return []
        if path is None and _cache["mtime"] == mt:
            return _cache["entries"]
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            return []
        ents = data.get("entries") if isinstance(data, dict) else data
        out = []
        for e in ents or []:
            if isinstance(e, dict) and (e.get("zh") or "").strip() and (e.get("vi") or "").strip():
                rx = None
                if str(e.get("re") or "").strip():
                    try:
                        rx = re.compile(str(e["re"]))
                    except re.error:
                        continue   # regex hỏng → bỏ mục (không gợi ý sai ngữ cảnh)
                out.append({"zh": e["zh"].strip(), "vi": e["vi"].strip(),
                            "forbid": [str(x) for x in (e.get("forbid") or []) if str(x).strip()],
                            "note": str(e.get("note") or ""), "re": rx,
                            "src": str(e.get("src") or "golden_derived").strip() or "golden_derived"})
        if path is None:
            _cache.update(mtime=mt, entries=out)
        return out


def _plan_idioms(plan: dict | None) -> list[dict]:
    out = []
    for t in (plan or {}).get("terms") or []:
        if not isinstance(t, dict):
            continue
        typ = str(t.get("type") or "").lower()
        if typ in ("chengyu", "idiom", "style", "combat_taunt") and t.get("zh") and t.get("vi"):
            out.append({"zh": str(t["zh"]).strip(), "vi": str(t["vi"]).strip(), "forbid": [], "note": "", "re": None,
                        "src": "plan"})
    return out


def hints_for(zh_texts: list[str], plan: dict | None = None, *, limit: int = 12) -> list[dict]:
    """Mục từ điển có zh là chuỗi con của bất kỳ câu ZH nào trong lô (dài trước, không trùng).
    Mục có `re` chỉ tính khi regex khớp ít nhất 1 câu ZH (không dò trên blob ghép)."""
    lines = [z or "" for z in zh_texts]
    blob = "\n".join(lines)
    if not blob.strip():
        return []
    seen, out = set(), []
    lex = load()
    if cfg.lexicon_exclude_golden():
        lex = [e for e in lex if e.get("src") != "golden_derived"]
    for e in sorted(_plan_idioms(plan) + lex, key=lambda x: -len(x["zh"])):
        z = e["zh"]
        if z in seen or z not in blob:
            continue
        rx = e.get("re")
        if rx is not None and not any(rx.search(ln) for ln in lines):
            continue
        # mục ngắn nằm trong mục dài đã chọn → bỏ (vd 怕个屁 vs 屁)
        if any(z in s for s in seen):
            continue
        seen.add(z)
        out.append(e)
        if len(out) >= limit:
            break
    return out


def hint_lines(hints: list[dict]) -> list[str]:
    lines = []
    for e in hints:
        s = f"  - {e['zh']} → «{e['vi']}»"
        if e.get("forbid"):
            s += " (CẤM: " + ", ".join(f"«{x}»" for x in e["forbid"]) + ")"
        if e.get("note"):
            s += f" — {e['note']}"
        lines.append(s)
    return lines
