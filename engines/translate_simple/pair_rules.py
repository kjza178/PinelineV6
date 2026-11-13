"""Bước 2 (2026-11-12 next) — điền cặp xưng hô CÒN TRỐNG theo bảng quy tắc quan hệ (cờ SIMPLE_PAIR_RULES=1).

Bảng: data/lexicon/pronoun_rules_zh_vi.json (viết tay theo quy ước lồng tiếng Việt — KHÔNG seed từ golden).
Quan hệ A↔B đọc từ plan (glossary.json):
  1) relationship_matrix — dạng lồng {A: {B: "mô tả"}} hoặc phẳng {"A—B": "mô tả"} (A là vế đầu: «Anh trai và em gái
     ruột» ⇒ A = anh trai). Mô tả chỉ có MỘT vai (vd «Sư phụ») mà không có «… của <tên>» ⇒ KHÔNG đoán (bỏ).
  2) role / relation của nhân vật: «Em gái ruột của Cố Thanh», «顾青的妹妹» ⇒ nhân vật này là vai «em».
Quan hệ chỉ có từ khoá hai chiều (vợ chồng, người yêu) ⇒ phân vai theo giới (hi_gender); thiếu giới ⇒ bỏ.
Thể loại: style_lock.mode modern|ancient|xianxia; mixed/không rõ ⇒ hợp dạng modern + (xianxia|ancient theo thể loại).

Chỉ THÊM cặp mà plan chưa có (PlanView.add_pair không đè) — mang src="rule", note «suy theo quan hệ …».
KHÔNG ghi glossary.json (chỉ ghi khi cổng done + SIMPLE_LEARN_INFERRED=1, xem pipeline).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .plan_view import PlanView, forms, nfc, norm_name

ROOT = Path(__file__).resolve().parents[2]  # V6: engines/translate_simple/ → gốc cây
RULES_PATH = ROOT / "data" / "lexicon" / "pronoun_rules_zh_vi.json"
_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")
_SEP_RE = re.compile(r"\s*(?:—|–|->|→|↔|<->|\||/|&|\s-\s)\s*")
_cache: dict = {"mtime": None, "data": None}
_lock = threading.Lock()


def load_rules(path: Path | None = None) -> dict:
    p = Path(path) if path else RULES_PATH
    with _lock:
        try:
            mt = p.stat().st_mtime
        except OSError:
            return {"relations": []}
        if path is None and _cache["mtime"] == mt and _cache["data"] is not None:
            return _cache["data"]
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            return {"relations": []}
        if not isinstance(data, dict) or not isinstance(data.get("relations"), list):
            return {"relations": []}
        if path is None:
            _cache.update(mtime=mt, data=data)
        return data


def _gender(c: dict | None) -> str:
    g = norm_name((c or {}).get("gender"))
    if g in ("nam", "male", "m", "男"):
        return "nam"
    if g in ("nữ", "nu", "female", "f", "女"):
        return "nữ"
    return "?"


def genre_classes(plan: dict, rules: dict) -> list[str]:
    lock = plan.get("style_lock") if isinstance(plan.get("style_lock"), dict) else {}
    mode = str(lock.get("mode") or "").strip().lower()
    if mode in ("modern", "ancient", "xianxia"):
        return [mode]
    blob = norm_name(" ".join(str(plan.get(k) or "") for k in ("genre", "tone")))
    kw = rules.get("genre_kw") or {}

    def has(cls: str) -> bool:
        return any(_find(blob, k) >= 0 for k in kw.get(cls) or [])
    old = "xianxia" if has("xianxia") else ("ancient" if has("ancient") else "")
    if mode == "mixed":
        return ["modern"] + ([old] if old else ["xianxia"])
    if old and not has("modern"):
        return [old]
    if old:
        return ["modern", old]
    return ["modern"]


def _find(text: str, kw: str) -> int:
    """Vị trí đầu tiên của từ khoá trong text (đã lower/NFC); từ khoá Việt khớp trọn từ. -1 nếu không có."""
    kw = nfc(kw).strip().lower()
    if not kw:
        return -1
    if _CJK_RE.search(kw):
        return text.find(kw)
    m = re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", text)
    return m.start() if m else -1


def _mask(text: str, kws) -> str:
    for kw in sorted((nfc(k).strip().lower() for k in kws or []), key=len, reverse=True):
        if not kw:
            continue
        if _CJK_RE.search(kw):
            text = text.replace(kw, " " * len(kw))
        else:
            text = re.sub(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", lambda m: " " * len(m.group(0)), text)
    return text


def _first(text: str, kws) -> int:
    pos = [p for p in (_find(text, k) for k in kws or []) if p >= 0]
    return min(pos) if pos else -1


def _by_gender(rel: dict, ga: str, gb: str) -> str | None:
    hg = rel.get("hi_gender")
    if not hg or ga == "?" or gb == "?" or ga == gb:
        return None
    return "hi" if ga == hg else "lo"


def _resolve(rel: dict, text: str, ga: str, gb: str, name_b: list[str], name_a: list[str]) -> str | None:
    """Vai của A trong quan hệ rel theo mô tả text (A là vế đầu). 'any' = ngang hàng."""
    if rel.get("sym"):
        return "any"
    if rel.get("needs_gender") and (ga == "?" or gb == "?" or ga == gb):
        return None
    has_pair = _first(text, rel.get("pair_kw")) >= 0
    text = _mask(text, rel.get("pair_kw"))   # «vợ chồng» là từ khoá hai chiều — không tính «vợ»/«chồng» trong đó
    hi, lo = _first(text, rel.get("hi_kw")), _first(text, rel.get("lo_kw"))
    if hi >= 0 and lo >= 0:
        return "hi" if hi < lo else "lo"
    one = "hi" if hi >= 0 else ("lo" if lo >= 0 else "")
    if one:
        # «<vai> … của <B>» ⇒ A là vai đó; «<vai> … của <A>» ⇒ B là vai đó
        kws = rel.get("hi_kw" if one == "hi" else "lo_kw") or []
        if _role_of(text, kws, name_b):
            return one
        if _role_of(text, kws, name_a):
            return "lo" if one == "hi" else "hi"
        return _by_gender(rel, ga, gb) if has_pair else None
    if has_pair:
        return _by_gender(rel, ga, gb)
    return None


def _role_of(text: str, kws, names: list[str]) -> bool:
    """text nói «<kw> [≤2 từ] của <tên>» hoặc «<名字>的<kw>»."""
    for nm in names:
        n = nfc(nm).strip().lower()
        if len(n) < 2:
            continue
        for kw in kws or []:
            k = nfc(kw).strip().lower()
            if not k:
                continue
            if _CJK_RE.search(k):
                if _CJK_RE.search(n) and (n + "的" + k) in text:
                    return True
                continue
            if re.search(r"(?<!\w)" + re.escape(k) + r"(?:\s+[^\s,.;]+){0,2}?\s+của\s+" + re.escape(n) + r"(?!\w)", text):
                return True
    return False


def _names(c: dict) -> list[str]:
    out = [str(c.get("vi") or ""), str(c.get("zh") or "")] + [str(x) for x in (c.get("aliases") or [])]
    return [x for x in dict.fromkeys(x.strip() for x in out) if len(x) >= 2]


def _matrix_texts(plan: dict, pv: PlanView) -> dict[tuple[str, str], list[str]]:
    """{(A, B) chuẩn hoá: [mô tả với A là vế đầu]}."""
    out: dict[tuple[str, str], list[str]] = {}
    mx = plan.get("relationship_matrix")
    if not isinstance(mx, dict):
        return out

    def add(a, b, t):
        a, b = pv.canon(a), pv.canon(b)
        if a and b and norm_name(a) != norm_name(b) and isinstance(t, str) and t.strip():
            out.setdefault((norm_name(a), norm_name(b)), []).append(nfc(t).lower())
    for k, v in mx.items():
        if isinstance(v, dict):
            for k2, t in v.items():
                add(k, k2, t if isinstance(t, str) else json.dumps(t, ensure_ascii=False))
        elif isinstance(v, str):
            parts = [x for x in _SEP_RE.split(str(k)) if x.strip()]
            if len(parts) == 2:
                add(parts[0], parts[1], v)
    return out


def relation_between(pv: PlanView, a: dict, b: dict, rules: dict,
                     mtx: dict | None = None) -> tuple[dict, str, str] | None:
    """(rel, vai của A: hi|lo|any, nguồn) hoặc None."""
    mtx = _matrix_texts(pv.plan, pv) if mtx is None else mtx
    ka, kb = norm_name(a.get("vi")), norm_name(b.get("vi"))
    ga, gb = _gender(a), _gender(b)
    na, nb = _names(a), _names(b)
    inv = {"hi": "lo", "lo": "hi", "any": "any"}
    cands: list[tuple[str, str, bool]] = []    # (text, nguồn, đảo vai)
    for t in mtx.get((ka, kb), []):
        cands.append((t, "relationship_matrix", False))
    for t in mtx.get((kb, ka), []):
        cands.append((t, "relationship_matrix", True))
    rels = [r for r in rules.get("relations") or [] if isinstance(r, dict) and isinstance(r.get("forms"), dict)]
    for text, src, flip in cands:
        g1, g2, n1, n2 = (gb, ga, nb, na) if flip else (ga, gb, na, nb)
        hits = []
        for r in rels:
            p = _first(text, list(r.get("pair_kw") or []) + list(r.get("hi_kw") or []) + list(r.get("lo_kw") or []))
            if p >= 0:
                hits.append((p, r))
        for _p, r in sorted(hits, key=lambda x: x[0]):
            side = _resolve(r, text, g1, g2, n2, n1)
            if side:
                return r, (inv[side] if flip else side), src
    # role / relation của nhân vật: «<vai> của <tên người kia>»
    for c, other, flip in ((a, b, False), (b, a, True)):
        text = nfc(" ; ".join(str(c.get(k) or "") for k in ("role", "relation"))).lower()
        if not text.strip():
            continue
        names_other = _names(other)
        for r in rels:
            for side, key in (("hi", "hi_kw"), ("lo", "lo_kw")):
                if _role_of(text, r.get(key), names_other):
                    if r.get("needs_gender") and (ga == "?" or gb == "?" or ga == gb):
                        continue
                    return r, (inv[side] if flip else side), "role"
            if _role_of(text, r.get("pair_kw"), names_other):
                if r.get("sym"):
                    return r, "any", "role"
                side = _by_gender(r, ga, gb)
                if side:
                    return r, side, "role"
    return None


def _pick(v, g: str) -> list[str]:
    if isinstance(v, dict):
        if g in v:
            return forms(v[g])
        if "*" in v:
            return forms(v["*"])
        return forms(list(v.values()))      # chưa rõ giới → nhận mọi dạng
    return forms(v)


def forms_for(rel: dict, side: str, classes: list[str], g_self: str, g_to: str) -> tuple[list[str], list[str]]:
    key = "any" if side == "any" else ("hi_lo" if side == "hi" else "lo_hi")
    tab = (rel.get("forms") or {}).get(key) or {}
    sf: list[str] = []
    cl: list[str] = []
    for cls in classes:
        ent = tab.get(cls) or tab.get("*")
        if not isinstance(ent, dict):
            continue
        sf += _pick(ent.get("self"), g_self)
        cl += _pick(ent.get("call"), g_to)
    return list(dict.fromkeys(sf)), list(dict.fromkeys(cl))


def fill(pv: PlanView, rules: dict | None = None) -> list[dict]:
    """Thêm vào pv các cặp CHƯA có mà suy được từ quan hệ. Trả danh sách cặp đã thêm (src=rule)."""
    rules = load_rules() if rules is None else rules
    if not rules.get("relations"):
        return []
    classes = genre_classes(pv.plan, rules)
    mtx = _matrix_texts(pv.plan, pv)
    chars = [c for c in pv.chars if nfc(c.get("vi") or "").strip()]
    added: list[dict] = []
    for a in chars:
        for b in chars:
            if a is b or norm_name(a.get("vi")) == norm_name(b.get("vi")):
                continue
            if pv.pair(a.get("vi"), b.get("vi")):
                continue
            hit = relation_between(pv, a, b, rules, mtx)
            if not hit:
                continue
            rel, side, src = hit
            sf, cl = forms_for(rel, side, classes, _gender(a), _gender(b))
            if not sf and not cl:
                continue
            p = {"who": pv.canon(a.get("vi")), "to": pv.canon(b.get("vi")), "self": sf, "call": cl,
                 "note": f"suy theo quan hệ «{rel.get('label') or rel.get('id')}» ({src}) — quy tắc",
                 "src": "rule", "rel": rel.get("id"), "side": side, "genre": "+".join(classes)}
            if pv.add_pair(p):
                added.append(p)
    return added
