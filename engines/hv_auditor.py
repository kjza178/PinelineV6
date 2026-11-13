"""
Hán-Việt auditor — chống glossary khóa sai âm (vd 逆→Nghị thay vì Nghịch).

1) Unihan kVietnamese: đối chiếu từng chữ Hán với âm VI trong tên/thuật ngữ kiểu HV.
2) Consistency: cùng một chữ Hán trong glossary phải cùng một âm VI.
3) Sửa tự động khi Unihan chỉ có 1 âm (hoặc âm ưu tiên rõ); gắn hv_suspect khi mơ hồ.

Bảng: data/unihan/kVietnamese.json (build từ Unicode Unihan.zip).
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
KV_PATH = ROOT / "data" / "unihan" / "kVietnamese.json"

_HAN_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_PAREN_RE = re.compile(r"[（(].*?[）)]")
# Tách âm tiết VI: chữ cái có dấu, giữ Đ/đ
_SYL_RE = re.compile(r"[A-Za-zÀ-ỹĐđ]+", re.UNICODE)

# Unihan kVietnamese thiếu / thiếu đa âm HV cổ-kim — bổ sung tay (không thay whitelist tên).
_EXTRA_READINGS: dict[str, list[str]] = {
    "行": ["hành", "hàng"],          # 逆天而行 = hành; ngân hàng = hàng
    "而": ["nhi"],
    "长": ["trường", "trưởng"],
    "長": ["trường", "trưởng"],
    "风": ["phong"],
    "应": ["ứng"],
    "议": ["nghị"],
    "干": ["can", "càn"],
    "乾": ["càn", "kiền"],
    "乐": ["lạc", "nhạc"],
    "樂": ["lạc", "nhạc"],
    "重": ["trọng", "trùng"],
    "传": ["truyền", "truyện"],
    "傳": ["truyền", "truyện"],
    "省": ["tỉnh", "tỉnh"],
    "中": ["trung"],
    "为": ["vi", "vị"],
    "為": ["vi", "vị"],
    "了": ["liễu", "liệu"],
}

# Giản→Phồn hay gặp khi Unihan chỉ có bản phồn
_SIMP_TO_TRAD: dict[str, str] = {
    "风": "風", "长": "長", "应": "應", "议": "議", "乐": "樂",
    "传": "傳", "为": "為", "国": "國", "门": "門", "阳": "陽",
    "阴": "陰", "龙": "龍", "马": "馬", "鸟": "鳥", "兽": "獸",
    "业": "業", "万": "萬", "与": "與", "义": "義", "干": "幹",
}

# CHỈ auto-fix các trap nhầm chữ (âm của chữ KHÁC). Unihan kVietnamese
# thiếu/sai nhiều âm tên riêng (沈≠đắm, 帝≠đấy, 司徒≠Ti…) → không đè mù.
# fold(wrong) → reading đúng (Unihan hoặc EXTRA đơn nghĩa).
_CONFUSION_TRAPS: dict[str, dict[str, str]] = {
    # 逆 nghịch ← hay đọc nhầm như 议/議 nghị
    "逆": {"nghi": "nghịch"},
}


def _fold(s: str) -> str:
    """Bỏ dấu để so âm (Nghịch≈nghich). đ→d; y/i cuối coi tương đương nhẹ."""
    s = (s or "").strip().lower().replace("đ", "d").replace("Đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    # Kỳ ≈ Kì (chính tả)
    if s.endswith("y") and len(s) > 1:
        s = s[:-1] + "i"
    return s


def _title_vi(syl: str) -> str:
    if not syl:
        return syl
    # Giữ nguyên nếu đã Title; capitalize kiểu Việt (đầu từ)
    return syl[:1].upper() + syl[1:].lower() if len(syl) > 1 else syl.upper()


@lru_cache(maxsize=1)
def load_k_vietnamese() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if KV_PATH.is_file():
        try:
            raw = json.loads(KV_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            raw = {}
        if isinstance(raw, dict):
            for ch, vals in raw.items():
                if not ch:
                    continue
                if isinstance(vals, str):
                    vals = vals.split()
                if isinstance(vals, list):
                    readings = [str(v).strip() for v in vals if str(v).strip()]
                    if readings:
                        out[str(ch)[0]] = readings
    # Merge EXTRA + giản thể fallback (không xoá Unihan; union)
    for ch, extra in _EXTRA_READINGS.items():
        cur = list(out.get(ch) or [])
        for r in extra:
            if _fold(r) not in {_fold(x) for x in cur}:
                cur.append(r)
        if cur:
            out[ch] = cur
    for simp, trad in _SIMP_TO_TRAD.items():
        if simp in out and trad not in out:
            out[trad] = list(out[simp])
        elif trad in out and simp not in out:
            out[simp] = list(out[trad])
        elif simp in out and trad in out:
            merged = list(out[trad])
            for r in out[simp]:
                if _fold(r) not in {_fold(x) for x in merged}:
                    merged.append(r)
            out[trad] = out[simp] = merged
    return out


def zh_chars(zh: str) -> list[str]:
    """Chữ Hán chính (bỏ ngoặc chú thích / punctuation)."""
    main = _PAREN_RE.split(zh or "", 1)[0]
    return _HAN_RE.findall(main)


def vi_syllables(vi: str) -> list[str]:
    """Tách âm tiết từ tên HV (bỏ dấu câu / · / /)."""
    # Chỉ phần trước chú thích kiểu « / Điện chủ…»
    head = (vi or "").split("/")[0].split("·")[0]
    head = head.split("(")[0].split("（")[0]
    return _SYL_RE.findall(head)


def looks_like_han_viet(zh: str, vi: str) -> bool:
    """Đủ điều kiện audit: số âm == số chữ Hán (khớp 1-1), VI dạng Title HV."""
    chars = zh_chars(zh)
    syls = vi_syllables(vi)
    if len(chars) < 2 or len(syls) < 2:
        return False
    # Bắt buộc khớp độ dài — lệch thứ tự/đệm dễ sửa bậy (圣女 Dao Trì…)
    if len(chars) != len(syls):
        return False
    if len(vi or "") > 64:
        return False
    titled = sum(1 for s in syls if s[:1].isupper())
    return titled >= max(2, len(syls) - 1)


def preferred_reading(ch: str) -> str | None:
    """Âm Unihan ưu tiên (đầu danh sách)."""
    readings = load_k_vietnamese().get(ch) or []
    return readings[0] if readings else None


def readings_fold(ch: str) -> set[str]:
    return {_fold(r) for r in (load_k_vietnamese().get(ch) or [])}


def align_pairs(zh: str, vi: str) -> list[tuple[str, str]] | None:
    """Ghép chữ↔âm khi độ dài khớp hoặc lệch 1 (bỏ âm đệm nhi/mà/chi…)."""
    chars = zh_chars(zh)
    syls = vi_syllables(vi)
    if not chars or not syls:
        return None
    if len(chars) == len(syls):
        return list(zip(chars, syls))
    # Lệch 1: thử bỏ 1 âm tiết VI không có trong Unihan của bất kỳ chữ nào
    if len(syls) == len(chars) + 1:
        for drop in range(len(syls)):
            kept = syls[:drop] + syls[drop + 1 :]
            return list(zip(chars, kept))
    if len(chars) == len(syls) + 1:
        # thiếu 1 âm — không auto-fix đủ
        return None
    return None


def audit_entry(zh: str, vi: str) -> dict[str, Any]:
    """
    Trả {ok, fixed_vi?, issues:[{ch, got, expect}], suspect:bool}.
    """
    result: dict[str, Any] = {
        "ok": True,
        "fixed_vi": None,
        "issues": [],
        "suspect": False,
        "warn": False,
        "skipped": False,
    }
    if not looks_like_han_viet(zh, vi):
        result["skipped"] = True
        return result
    pairs = align_pairs(zh, vi)
    if not pairs:
        result["skipped"] = True
        return result

    kv = load_k_vietnamese()
    if not kv:
        result["skipped"] = True
        result["suspect"] = True
        result["issues"].append({"error": "missing kVietnamese.json"})
        return result

    syls = [s for _, s in pairs]
    chars = [c for c, _ in pairs]
    changed = False
    new_syls = list(syls)

    for i, (ch, syl) in enumerate(pairs):
        expect = kv.get(ch) or []
        if not expect:
            continue
        got_f = _fold(syl)
        exp_f = {_fold(e) for e in expect}
        if got_f in exp_f:
            continue
        # Trap nhầm chữ? (逆→nghị) — đây mới được auto-fix
        trap = (_CONFUSION_TRAPS.get(ch) or {}).get(got_f)
        if trap:
            issue = {"ch": ch, "got": syl, "expect": [trap], "trap": True}
            result["issues"].append(issue)
            result["ok"] = False
            new_syls[i] = _title_vi(trap) if syl[:1].isupper() else trap
            changed = True
            continue
        # Unihan lệch nhưng KHÔNG chắc (tên riêng / đa âm) → cảnh báo, không đè
        if expect:
            result["issues"].append({
                "ch": ch, "got": syl, "expect": expect, "warn_only": True,
            })
            result["warn"] = True

    if changed:
        # Rebuild VI: thay khối âm tiết đầu; giữ suffix chú thích
        raw = vi or ""
        # Tìm các syllable gốc theo thứ tự và thay
        rebuilt = raw
        # Thay theo từ trái sang phải từng âm cũ → mới
        pos = 0
        parts: list[str] = []
        last = 0
        for old, new in zip(syls, new_syls):
            m = _SYL_RE.search(rebuilt, pos)
            if not m:
                break
            parts.append(rebuilt[last : m.start()])
            parts.append(new if _fold(m.group(0)) == _fold(old) else m.group(0))
            if _fold(m.group(0)) == _fold(old):
                parts[-1] = new
            last = m.end()
            pos = m.end()
        parts.append(rebuilt[last:])
        result["fixed_vi"] = "".join(parts)
        # Nếu vẫn lệch sau sửa đơn âm → ok hơn
        if not result["suspect"]:
            result["ok"] = True

    return result


def _iter_named_entries(plan: dict) -> list[tuple[str, dict]]:
    out = []
    for key in ("characters", "terms"):
        for item in plan.get(key) or []:
            if isinstance(item, dict) and (item.get("zh") or "").strip() and (item.get("vi") or "").strip():
                out.append((key, item))
    return out


def _replace_syllable_at(vi: str, idx: int, new_syl: str) -> str:
    """Thay âm tiết thứ idx (0-based) trong chuỗi VI, giữ khoảng trắng/dấu."""
    parts: list[str] = []
    last = 0
    si = 0
    for m in _SYL_RE.finditer(vi or ""):
        parts.append(vi[last:m.start()])
        if si == idx:
            old = m.group(0)
            parts.append(new_syl if old[:1].isupper() else new_syl.lower())
        else:
            parts.append(m.group(0))
        last = m.end()
        si += 1
    parts.append((vi or "")[last:])
    return "".join(parts)


def enforce_char_consistency(plan: dict) -> list[dict]:
    """
    Cùng chữ Hán → cùng âm VI (ưu tiên Unihan; nếu đã có âm đúng Unihan trong glossary thì dùng nó).
    """
    kv = load_k_vietnamese()
    # char -> list of (item, syl_index_in_vi, syllable)
    usage: dict[str, list[tuple[dict, int, str]]] = {}
    for _key, item in _iter_named_entries(plan):
        zh, vi = item.get("zh") or "", item.get("vi") or ""
        if not looks_like_han_viet(zh, vi):
            continue
        pairs = align_pairs(zh, vi)
        if not pairs:
            continue
        full_syls = vi_syllables(vi)
        used_idx: set[int] = set()
        for ch, syl in pairs:
            idx = next(
                (
                    i
                    for i, s in enumerate(full_syls)
                    if i not in used_idx and _fold(s) == _fold(syl)
                ),
                None,
            )
            if idx is None:
                continue
            used_idx.add(idx)
            usage.setdefault(ch, []).append((item, idx, syl))

    fixes: list[dict] = []
    for ch, rows in usage.items():
        folds = {_fold(s) for _, _, s in rows}
        if len(folds) <= 1:
            continue
        expect = kv.get(ch) or []
        exp_f = {_fold(e) for e in expect}
        # Đa âm hợp lệ (hành/hàng) cùng xuất hiện → KHÔNG ép một phía
        if exp_f and folds <= exp_f:
            continue
        invalid = [row for row in rows if _fold(row[2]) not in exp_f] if exp_f else list(rows)
        if not invalid:
            continue
        canon = None
        if len(exp_f) == 1:
            canon = expect[0]
        else:
            for _, _, s in rows:
                if _fold(s) in exp_f:
                    canon = s
                    break
            if canon is None and len(expect) == 1:
                canon = expect[0]
        if not canon:
            for item, _, _syl in invalid:
                item["hv_suspect"] = True
            fixes.append({"ch": ch, "action": "suspect", "variants": sorted(folds)})
            continue
        for item, idx, syl in invalid:
            if _fold(syl) == _fold(canon):
                continue
            vi = item.get("vi") or ""
            new_vi = _replace_syllable_at(vi, idx, _title_vi(canon))
            if new_vi == vi:
                continue
            fixes.append({
                "zh": item.get("zh"),
                "ch": ch,
                "before": vi,
                "after": new_vi,
                "action": "consistency",
            })
            item["vi"] = new_vi
            item.pop("hv_suspect", None)
    return fixes


def audit_and_fix_plan(plan: dict | None, *, fix: bool = True) -> dict:
    """
    Chạy auditor trên characters+terms.
    Trả summary; sửa in-place nếu fix=True.
    """
    if not isinstance(plan, dict):
        return {"ok": False, "error": "no plan"}
    if not load_k_vietnamese():
        return {"ok": False, "error": f"thiếu {KV_PATH}", "n_checked": 0}

    n_checked = n_fixed = n_suspect = n_ok = 0
    issues: list[dict] = []

    for key, item in _iter_named_entries(plan):
        zh, vi = item.get("zh") or "", item.get("vi") or ""
        if item.get("hv_skip") or item.get("type") in ("style",):
            continue
        # lock seed đã chốt tay: vẫn audit nhưng cho phép
        res = audit_entry(zh, vi)
        if res.get("skipped"):
            continue
        n_checked += 1
        if res.get("ok") and not res.get("issues"):
            n_ok += 1
            item.pop("hv_suspect", None)
            item.pop("hv_warn", None)
            continue
        if res.get("issues"):
            issues.append({
                "bucket": key,
                "zh": zh,
                "vi": vi,
                "issues": res["issues"],
            })
        if fix and res.get("fixed_vi") and res["fixed_vi"] != vi:
            item["vi"] = res["fixed_vi"]
            item["hv_fixed"] = True
            n_fixed += 1
            item.pop("hv_suspect", None)
            item.pop("hv_warn", None)
            n_ok += 1
            continue
        if res.get("suspect"):
            item["hv_suspect"] = True
            n_suspect += 1
        elif res.get("warn"):
            item["hv_warn"] = True
            # không chặn series — chỉ meta cảnh báo Unihan≠tên riêng

    cons = enforce_char_consistency(plan) if fix else []
    n_fixed += sum(1 for c in cons if c.get("action") == "consistency")

    meta = dict(plan.get("_meta") or {})
    meta["hv_auditor"] = {
        "n_checked": n_checked,
        "n_fixed": n_fixed,
        "n_suspect": n_suspect,
        "n_ok": n_ok,
        "n_consistency": len(cons),
    }
    plan["_meta"] = meta

    if n_fixed or n_suspect:
        print(
            f"[hv-auditor] checked={n_checked} fixed={n_fixed} "
            f"suspect={n_suspect} consistency={len(cons)}",
            flush=True,
        )

    return {
        "ok": True,
        "n_checked": n_checked,
        "n_fixed": n_fixed,
        "n_suspect": n_suspect,
        "n_ok": n_ok,
        "issues": issues[:30],
        "consistency": cons[:30],
    }


def filter_series_entries(entries: list, *, drop_suspect: bool = True) -> list:
    """Hygiene: auto-fix trap; bỏ mục hv_suspect cứng; giữ hv_warn (Unihan lệch tên riêng)."""
    out = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        e = dict(e)
        zh, vi = e.get("zh") or "", e.get("vi") or ""
        if zh and vi and looks_like_han_viet(zh, vi):
            res = audit_entry(zh, vi)
            if res.get("fixed_vi") and res["fixed_vi"] != vi:
                e["vi"] = res["fixed_vi"]
                e["hv_fixed"] = True
                e.pop("hv_suspect", None)
                e.pop("hv_warn", None)
            elif res.get("suspect"):
                e["hv_suspect"] = True
                if drop_suspect:
                    continue
            elif res.get("warn"):
                e["hv_warn"] = True
        if e.get("hv_suspect") and drop_suspect:
            continue
        out.append(e)
    return out
