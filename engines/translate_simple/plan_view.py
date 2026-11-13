"""Đọc plan (glossary.json) cho luồng simple: nhân vật, cặp xưng hô, cảnh, thuật ngữ — và khối plan
thu gọn THEO LÔ (chỉ nhân vật có mặt / cặp liên quan / cảnh phủ lô / thuật ngữ xuất hiện)."""
from __future__ import annotations

import re
import unicodedata

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_SPLIT_RE = re.compile(r"\s*(?:/|,|;|\||\bhoặc\b|\bhay\b|\bor\b)\s*", re.I)


def nfc(s) -> str:
    return unicodedata.normalize("NFC", str(s or ""))


def norm_name(s) -> str:
    s = nfc(s).strip().lower()
    s = re.sub(r"[\"'«»“”()\[\]]", "", s)
    return re.sub(r"\s+", " ", s)


def forms(v) -> list[str]:
    """«anh / học trưởng» → ['anh', 'học trưởng'] (giữ thứ tự, bỏ trùng)."""
    if isinstance(v, (list, tuple)):
        parts = []
        for x in v:
            parts.extend(forms(x))
        return list(dict.fromkeys(parts))
    s = nfc(v).strip().strip("\"'«»“”")
    if not s:
        return []
    out = []
    for p in _SPLIT_RE.split(s):
        p = re.sub(r"\(.*?\)", "", p).strip().strip("\"'«»“”.").strip().lower()
        if p and len(p) <= 24 and not _CJK_RE.search(p):   # review 2026-11-12: bỏ dạng chữ Hán (徒儿)
            out.append(p)
    return list(dict.fromkeys(out))


def _alts(xs: list[str]) -> str:
    return " hoặc ".join(f"«{x}»" for x in xs) if xs else "?"


def pair_line(p: dict) -> str:
    """1 dòng cặp xưng hô cho prompt — review 2026-11-12: tách rõ tự xưng / gọi (trước đây
    «tôi / anh / ngươi» mơ hồ khi self/call có nhiều dạng) + ghi chú cặp (ngắn)."""
    s = f"  - {p['who']} → {p['to']}: tự xưng {_alts(p['self'])} · gọi {_alts(p['call'])}"
    note = re.sub(r"\s+", " ", str(p.get("note") or "")).strip()
    if note:
        s += f" ({note[:100]})"
    return s


class PlanView:
    def __init__(self, plan: dict | None):
        self.plan = plan if isinstance(plan, dict) else {}
        self.chars: list[dict] = [c for c in self.plan.get("characters") or [] if isinstance(c, dict)]
        # tên (zh/vi/alias) → tên VI chuẩn
        self.alias: dict[str, str] = {}
        for c in self.chars:
            vi = nfc(c.get("vi") or "").strip()
            if not vi:
                continue
            for k in [vi, c.get("zh")] + list(c.get("aliases") or []):
                if k:
                    self.alias.setdefault(norm_name(k), vi)
        self.pairs: dict[tuple[str, str], dict] = {}
        for pr in self.plan.get("pronouns") or []:
            if not isinstance(pr, dict):
                continue
            who, to = self.canon(pr.get("who")), self.canon(pr.get("to"))
            if not who or not to:
                continue
            self.pairs.setdefault((norm_name(who), norm_name(to)), {
                "who": who, "to": to, "self": forms(pr.get("self")), "call": forms(pr.get("call")),
                "note": str(pr.get("note") or "")[:120],
            })
        self.scenes = [s for s in self.plan.get("scenes") or [] if isinstance(s, dict)]
        self.terms = [t for t in self.plan.get("terms") or [] if isinstance(t, dict) and t.get("zh") and t.get("vi")]

    def add_pair(self, p: dict) -> bool:
        """Thêm cặp suy luận (src rule / llm_fill) — KHÔNG đè cặp đã có (plan luôn thắng). Trả True nếu đã thêm.
        Không đụng self.plan["pronouns"] (glossary.json giữ nguyên)."""
        who, to = self.canon(p.get("who")), self.canon(p.get("to"))
        if not who or not to or norm_name(who) == norm_name(to):
            return False
        key = (norm_name(who), norm_name(to))
        if key in self.pairs:
            return False
        sf, cl = forms(p.get("self")), forms(p.get("call"))
        if not sf and not cl:
            return False
        rec = {"who": who, "to": to, "self": sf, "call": cl, "note": str(p.get("note") or "")[:120]}
        for k in ("src", "conf", "rel"):
            if p.get(k) is not None:
                rec[k] = p[k]
        self.pairs[key] = rec
        return True

    def inferred_pairs(self) -> list[dict]:
        """Cặp do luồng simple tự thêm (src rule / llm_fill) — không có trong glossary.json."""
        return [dict(p) for p in self.pairs.values() if p.get("src") in ("rule", "llm_fill")]

    def canon(self, name) -> str:
        n = norm_name(name)
        if not n or n in ("?", "none", "null", "không rõ", "unknown"):
            return ""
        return self.alias.get(n, nfc(name).strip())

    def pair(self, spk, to) -> dict | None:
        a, b = self.canon(spk), self.canon(to)
        if not a or not b:
            return None
        return self.pairs.get((norm_name(a), norm_name(b)))

    # ------------------------------------------------------------------ theo lô
    def scenes_for(self, i0: int, i1: int) -> list[dict]:
        out = []
        for sc in self.scenes:
            rng = sc.get("range") or [sc.get("start_i"), sc.get("end_i")]
            try:
                a, b = int(rng[0]), int(rng[1])
            except (TypeError, ValueError, IndexError):
                continue
            if a <= i1 and b >= i0:
                out.append(sc)
        return out

    def chars_for(self, zh_blob: str, scenes: list[dict], limit: int = 12) -> list[dict]:
        names = set()
        for sc in scenes:
            for n in sc.get("characters") or []:
                names.add(norm_name(self.canon(n)))
        hit = []
        for c in self.chars:
            keys = [c.get("zh")] + list(c.get("aliases") or [])
            in_text = any(k and str(k) in zh_blob for k in keys)
            if in_text or norm_name(c.get("vi")) in names:
                hit.append(c)
        if not hit:
            hit = self.chars[:4]
        return hit[:limit]

    def pairs_for(self, chars: list[dict], limit: int = 24) -> list[dict]:
        names = {norm_name(c.get("vi")) for c in chars}
        out = [p for (a, b), p in self.pairs.items() if a in names or b in names]
        # cặp mà CẢ HAI có mặt lên trước
        out.sort(key=lambda p: -((norm_name(p["who"]) in names) + (norm_name(p["to"]) in names)))
        return out[:limit]

    def terms_for(self, zh_blob: str, limit: int = 30) -> list[dict]:
        out = [t for t in self.terms if str(t["zh"]) in zh_blob
               and str(t.get("type") or "").lower() not in ("chengyu", "idiom", "style", "combat_taunt")]
        return sorted(out, key=lambda t: -len(str(t["zh"])))[:limit]

    def header_lines(self) -> list[str]:
        p = self.plan
        out = []
        g = " | ".join(x for x in (
            f"thể loại: {p.get('genre')}" if p.get("genre") else "",
            f"văn phong: {p.get('tone')}" if p.get("tone") else "",
        ) if x)
        if g:
            out.append("PHIM: " + g[:400])
        lock = p.get("style_lock") if isinstance(p.get("style_lock"), dict) else {}
        if lock.get("mode") and lock.get("mode") != "unknown":
            pol = str(lock.get("pronoun_policy") or "").strip()
            out.append(f"STYLE: mode={lock.get('mode')}" + (f" — {pol[:500]}" if pol else ""))
        if p.get("pronoun_notes"):
            out.append("GHI CHÚ XƯNG HÔ: " + str(p.get("pronoun_notes"))[:300])
        return out

    def block_for(self, rows: list[dict], ctx_rows: list[dict] | None = None) -> str:
        """Khối plan thu gọn cho 1 lô (rows = câu cần làm; ctx_rows = ngữ cảnh chỉ đọc)."""
        allr = list(rows) + list(ctx_rows or [])
        idx = [int(r["i"]) for r in rows if r.get("i") is not None]
        i0, i1 = (min(idx), max(idx)) if idx else (0, 0)
        zh_blob = "\n".join(str(r.get("zh") or "") for r in allr)
        scenes = self.scenes_for(i0, i1)
        chars = self.chars_for(zh_blob, scenes)
        pairs = self.pairs_for(chars)
        terms = self.terms_for(zh_blob)
        L = self.header_lines()
        if chars:
            L.append("NHÂN VẬT CÓ MẶT / LIÊN QUAN:")
            for c in chars:
                bits = [f"{c.get('zh')} = {c.get('vi')}"]
                if c.get("gender"):
                    bits.append(f"giới {c.get('gender')}")
                if c.get("role"):
                    bits.append(str(c.get("role"))[:90])
                L.append("  - " + " · ".join(bits))
        if pairs:
            L.append("XƯNG HÔ BẮT BUỘC (người nói → người nghe):")
            for p in pairs:
                L.append(pair_line(p))
        if scenes:
            L.append("CẢNH:")
            for sc in scenes[:3]:
                rng = sc.get("range") or [sc.get("start_i"), sc.get("end_i")]
                bits = [f"[câu {rng[0]}→{rng[1]}] {sc.get('location') or ''}".strip()]
                if sc.get("characters"):
                    bits.append("có mặt: " + ", ".join(str(x) for x in sc["characters"][:8]))
                if sc.get("tone"):
                    bits.append("tone: " + str(sc["tone"])[:120])
                if sc.get("pronouns_in_scene"):
                    bits.append("xưng hô cảnh: " + str(sc["pronouns_in_scene"])[:200])
                L.append("  - " + " · ".join(bits))
        if terms:
            L.append("TÊN / THUẬT NGỮ (dùng đúng chữ VI):")
            for t in terms:
                L.append(f"  - {t['zh']} = {t['vi']}")
        return "\n".join(L)
