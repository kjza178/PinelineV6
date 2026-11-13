"""Bộ kiểm tất định — chỉ ĐÁNH CỜ, không sửa.

Cờ (row["s_flags"] = list mã):
  empty    vi trống
  cjk      vi còn chữ Hán
  pronoun  đại từ ngôi 1/2 lệch cặp plan spk→to (chỉ khi conf ≥ SIMPLE_CONF_MIN và plan có cặp)
  name     ZH có tên nhân vật trong plan nhưng VI không có tên VI (hay một phần tên) tương ứng

Bộ trích đại từ: bản gọn của scripts/eval_translation.py (V6) — khớp cụm dài nhất, loại danh từ /
ngôi 3 (anh ấy, con đường…), danh xưng + Tên riêng (anh Trần) không tính.
"""
from __future__ import annotations

import re

from .plan_view import PlanView, nfc, norm_name
from .script_store import has_cjk, is_manual

FIRST = {
    "tôi", "tao", "tớ", "ta", "lão phu", "lão tử", "bản tọa", "bổn tọa", "bản tôn", "bổn tôn", "vãn bối", "tại hạ",
    "vi sư", "bản cung", "trẫm", "bổn vương", "bản vương", "lão nương", "anh mày", "bố mày", "ông đây", "tiểu gia",
    "bổn cô nương", "thuộc hạ", "mình",
}
SECOND = {"cậu", "mày", "ngươi", "bạn", "ngài", "các người", "mi", "lão nhân gia", "chư vị", "các vị", "mọi người"}
KIN = {
    "anh", "em", "chị", "con", "bố", "ba", "mẹ", "cô", "chú", "bác", "ông", "bà", "cháu", "huynh", "đệ", "muội", "tỷ",
    "sư huynh", "sư đệ", "sư tỷ", "sư muội", "sư phụ", "sư tôn", "tiền bối", "đại ca", "lão đại", "học trưởng",
    "học muội", "học tỷ", "học đệ", "tiểu tử", "nhóc", "đồ nhi", "đệ tử", "chủ nhân", "đại tỷ", "cha",
}
SOFT = {"mình"}
ONLY_IF_EXPECTED = {"con", "ba", "bố", "mẹ", "cha", "đệ tử", "chủ nhân", "mọi người", "thuộc hạ", "bạn"}
_PLURAL = ("các ", "chúng ", "bọn ", "mấy ", "nhà ")
INCLUSIVE = {"chúng ta", "chúng mình", "bọn mình", "tụi mình", "chúng tôi", "bọn tôi", "chúng em", "bọn em",
             "chúng con", "chúng tao", "bọn tao", "tụi tao", "bọn ta"}
NOT_PRON = [
    "anh ấy", "anh ta", "anh trai", "anh em", "anh hùng", "anh minh", "anh tài", "anh linh", "anh rể", "anh họ",
    "em ấy", "em gái", "em trai", "em bé", "em út", "em họ", "em rể", "em dâu",
    "chị ấy", "chị ta", "chị gái", "chị dâu", "chị em", "chị họ",
    "cô ấy", "cô ta", "cô gái", "cô nương", "cô đơn", "cô nhi", "cô nàng", "cô bé", "cô dâu", "cô giáo",
    "cậu ấy", "cậu ta", "cậu bé", "cậu chủ", "cậu ruột", "cậu ấm",
    "ông ta", "ông ấy", "ông trời", "ông lão", "ông chủ", "đàn ông", "ông cụ", "ông bà",
    "bà ta", "bà ấy", "đàn bà", "bà con", "bà xã", "bà lão", "bà cụ",
    "hắn ta", "người ta", "gã ta", "lão ta", "lão ấy", "mụ ta",
    "con người", "con đường", "con số", "con gái", "con trai", "con cái", "con tin", "con mắt", "con ong", "con sâu",
    "con khỉ", "con dao", "con rắn", "con thú", "con cháu", "con dâu", "con rể", "con nít", "con ruột",
    "mẹ kiếp", "mẹ nó", "cha nó", "bố láo", "mẹ chồng", "mẹ vợ", "bố vợ", "bố chồng", "ba mẹ", "bố mẹ", "cha mẹ",
    "chú ý", "bác sĩ", "cháu gái", "cháu trai", "huynh đệ", "tỷ muội",
    "bạn bè", "bạn trai", "bạn gái", "bạn học", "bạn đời", "bạn cùng", "ngài ấy", "thằng nhóc", "tiểu tử kia",
    "ta đây", "ba chục", "ba mươi", "ba trăm", "ba nghìn", "ba ngàn", "ba người", "ba ngày", "ba năm", "ba lần",
]
_PUNCT = ".,!?;:…\"'“”‘’«»()[]{}-–—~"


def _base(f: str) -> str:
    f = f.lower().strip()
    if f in INCLUSIVE:
        return f
    for p in _PLURAL:
        if f.startswith(p) and len(f) > len(p):
            return f[len(p):]
    return f


def _lex() -> dict[str, str]:
    lex: dict[str, str] = {}
    for f in FIRST:
        lex[f] = "FIRST"
    for f in SECOND:
        lex[f] = "SECOND"
    for f in KIN:
        lex.setdefault(f, "KIN")
    for f in INCLUSIVE:
        lex[f] = "INCL"
    for f in ("các cậu", "các ngươi", "các anh", "các chị", "các em", "các con", "các cháu", "nhà ngươi",
              "mấy người", "bọn mày", "chúng mày", "tụi mày", "các bạn", "các ngài"):
        lex[f] = lex.get(_base(f), "SECOND")
    for f in NOT_PRON:
        lex[f] = "NOT"
    for f in ("hắn", "nó", "họ", "gã", "nàng", "ả", "mụ", "bọn họ", "bọn chúng", "chúng nó"):
        lex[f] = "REF3"
    return lex


LEX = _lex()
_MAXN = max(len(k.split()) for k in LEX)


def scan(text: str, protect: list[str] | tuple = ()) -> list[dict]:
    toks = []
    for w in nfc(text).split():
        w2 = w.strip(_PUNCT)
        if w2:
            toks.append((w2.lower(), w2))
    words = [t[0] for t in toks]
    prot = sorted({p.lower() for p in protect if p and len(p.split()) > 1}, key=lambda x: -len(x.split()))
    res, i = [], 0
    while i < len(words):
        hit = None
        for p in prot:
            pw = p.split()
            if words[i:i + len(pw)] == pw:
                hit = (p, "ACCEPT", len(pw))
                break
        if not hit:
            for n in range(min(_MAXN, len(words) - i), 0, -1):
                cand = " ".join(words[i:i + n])
                if cand in LEX:
                    hit = (cand, LEX[cand], n)
                    break
        if hit:
            form, cat, n = hit
            nxt = toks[i + n][1] if i + n < len(toks) else ""
            res.append({"form": form, "base": _base(form), "cat": cat, "next": nxt,
                        "next_l": nxt.lower(), "prev": words[i - 1] if i > 0 else "",
                        "orig": " ".join(t[1] for t in toks[i:i + n])})
            i += n
        else:
            i += 1
    return res


def pronoun_violations(vi: str, exp_self: list[str], exp_call: list[str]) -> list[str]:
    """Dạng đại từ ngôi 1/2 trong vi KHÔNG thuộc cặp kỳ vọng. [] = ổn / không đủ chứng."""
    if not exp_self and not exp_call:
        return []
    acc = {_base(x) for x in list(exp_self) + list(exp_call)}
    items = scan(vi, list(exp_self) + list(exp_call))
    pron_words = {it["form"] for it in items if it["cat"] in ("FIRST", "SECOND", "KIN", "ACCEPT", "INCL")}
    hits, viol, kin_unknown = [], [], []
    for it in items:
        cat, b = it["cat"], it["base"]
        if cat in ("NOT", "INCL", "REF3"):
            continue
        if cat == "ACCEPT" or b in acc:
            hits.append(it["orig"])
            continue
        if b in SOFT or b in ONLY_IF_EXPECTED:
            continue
        if cat == "KIN":
            nxt = it["next"]
            if nxt and nxt[:1].isupper():
                continue  # anh Trần, cô Tần — danh xưng + tên
            if it["next_l"] in pron_words or it["next_l"] in ("ấy", "ta", "kia", "này", "đó", "nó", "họ", "hắn", "của"):
                continue
            if it["prev"] in ("nhà", "là", "làm", "một", "những", "người", "vị", "hai", "ba"):
                continue
            if not (exp_self and exp_call):
                kin_unknown.append(it["orig"])
                continue
            viol.append(it["orig"])
            continue
        if cat == "FIRST" and exp_self:
            viol.append(it["orig"])
        elif cat == "SECOND" and exp_call:
            viol.append(it["orig"])
    if not hits and kin_unknown:
        viol.extend(kin_unknown)
    return viol


def _name_missing(zh: str, vi: str, pv: PlanView) -> list[str]:
    """Tên VI kỳ vọng (theo ZH) không xuất hiện trong vi (cả tên đầy đủ lẫn 1–2 âm tiết cuối)."""
    out = []
    low = nfc(vi).lower()
    for c in pv.chars:
        zhn = str(c.get("zh") or "").strip()
        vin = nfc(c.get("vi") or "").strip()
        if len(zhn) < 2 or not vin or zhn not in (zh or ""):
            continue
        parts = vin.lower().split()
        cands = {vin.lower(), " ".join(parts[-2:]), parts[-1]} if parts else {vin.lower()}
        if not any(re.search(r"(?<!\w)" + re.escape(cnd) + r"(?!\w)", low) for cnd in cands if cnd):
            out.append(vin)
    return out


def check_row(r: dict, pv: PlanView, conf_min: float) -> list[dict]:
    """Trả list cờ {code, why} cho 1 hàng (manual / câu rác junk=1 → không cờ)."""
    if is_manual(r) or r.get("junk"):
        return []
    vi = (r.get("vi") or "").strip()
    if not vi:
        return [{"code": "empty", "why": "vi trống"}]
    flags = []
    if has_cjk(vi):
        flags.append({"code": "cjk", "why": "vi còn chữ Hán"})
    if "s_conf" in r and r.get("s_conf") is None and r.get("rv"):
        conf = conf_min   # model không trả conf (review 2026-11-12) → vẫn kiểm nếu spk/to khớp cặp plan
    else:
        try:
            conf = float(r.get("s_conf") or 0)
        except (TypeError, ValueError):
            conf = 0.0
    if conf >= conf_min:
        pr = pv.pair(r.get("s_spk"), r.get("s_to"))
        if pr:
            bad = pronoun_violations(vi, pr["self"], pr["call"])
            if bad:
                flags.append({"code": "pronoun", "why": (
                    f"{pr['who']}→{pr['to']} phải «{'/'.join(pr['self'])}»–«{'/'.join(pr['call'])}», "
                    f"vi có «{', '.join(bad)}»")})
    miss = _name_missing(r.get("zh") or "", vi, pv)
    if miss and not r.get("s_name_ok"):
        flags.append({"code": "name", "why": "thiếu/sai tên: " + ", ".join(miss)})
    return flags


def validate(rows: list[dict], pv: PlanView, conf_min: float, *, only: set | None = None) -> dict[int, list[dict]]:
    """Gắn row['s_flags'] (mã) và trả {i: [cờ]} cho các câu có cờ. only = chỉ kiểm các i này."""
    out: dict[int, list[dict]] = {}
    for r in rows:
        try:
            ii = int(r.get("i"))
        except (TypeError, ValueError):
            continue
        if only is not None and ii not in only:
            continue
        fl = check_row(r, pv, conf_min)
        if fl:
            r["s_flags"] = [f["code"] for f in fl]
            out[ii] = fl
        else:
            r.pop("s_flags", None)
    return out


def speaker_known(r: dict) -> bool:
    return bool(norm_name(r.get("s_spk")) not in ("", "?"))
