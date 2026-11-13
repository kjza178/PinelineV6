"""Hậu kỳ tất định của luồng simple — KHÔNG regex đổi đại từ:

1) chuẩn dấu nghìn (translate.normalize_vi_thousands)
2) gỡ lặp ASR/VI tất định (translate.heal_obvious_internal_stutter — CÙNG hàm luồng cũ dùng trong
   apply_asr_stutter_heal; sửa lỗi 2026-11-12: simple trước đây không gỡ «Giết đi giết đi»).
   Review agent D 2026-11-12: CHỈ gỡ VI của câu mà ZH có lặp ASR (ZH hiện tại còn lặp, hoặc ASR gốc lặp —
   store.stutter_i do pipeline đặt) — «Đi mau, đi mau!» của 快走，快走！ là nhấn giọng có chủ ý, giữ; và dọn
   dấu phẩy treo hàm gỡ lặp để lại («Giết đi,!» → «Giết đi!»).
3) khoá thuật ngữ cả câu (translate.apply_hard_term_locks — chỉ type=lock, bỏ câu manual)

Mọi thay đổi ghi trace qua ScriptStore.set_vi.
"""
from __future__ import annotations

import copy
import re

from .script_store import ScriptStore, is_manual

_DANGLE_RE = re.compile(r"\s*[,，]+\s*(?=[.!?…。！？])")


def _zh_stutter(T, r: dict, stutter_i) -> bool:
    """ZH của câu có lặp ASR: còn lặp (câu rv giữ zh) hoặc ASR gốc lặp (pipeline đã gỡ ở bước dựng script)."""
    zh = str(r.get("zh") or "")
    if zh and T.heal_obvious_internal_stutter(zh) != zh:
        return True
    try:
        return bool(stutter_i) and int(r.get("i")) in stutter_i
    except (TypeError, ValueError):
        return False


def heal_vi(T, vi: str) -> str:
    """translate.heal_obvious_internal_stutter + dọn dấu phẩy treo («Giết đi,!» → «Giết đi!», «,,» → «,»)."""
    hv = T.heal_obvious_internal_stutter(vi)
    if hv == vi:
        return vi
    hv = _DANGLE_RE.sub("", hv)
    hv = re.sub(r"[,，]\s*[,，]+", ",", hv)
    if not re.search(r"[,，]\s*$", vi):
        hv = re.sub(r"\s*[,，]+\s*$", "", hv)
    return hv.strip() or vi


def apply(store: ScriptStore, plan: dict | None, rows: list[dict] | None = None) -> int:
    import engines.translate as T

    target = rows if rows is not None else store.rows
    stutter_i = getattr(store, "stutter_i", None)
    n = 0
    for r in target:
        if is_manual(r):
            continue
        vi = (r.get("vi") or "").strip()
        if not vi:
            continue
        nv = T.normalize_vi_thousands(vi)
        if store.set_vi(r, nv, "post", "chuẩn số"):
            n += 1
        if _zh_stutter(T, r, stutter_i):
            hv = heal_vi(T, r.get("vi") or "")
            if store.set_vi(r, hv, "post", "gỡ lặp"):
                n += 1
    # khoá thuật ngữ: chạy trên bản sao rồi ghi qua set_vi (để có trace, không đụng manual)
    shadow = [{"i": r.get("i"), "zh": r.get("zh"), "vi": r.get("vi"), "manual": r.get("manual")}
              for r in target]
    try:
        T.apply_hard_term_locks(shadow, copy.deepcopy(plan or {}))
    except Exception as e:  # noqa: BLE001
        print(f"[simple-post] hard-lock lỗi: {e}", flush=True)
        return n
    for r, s in zip(target, shadow):
        if (s.get("vi") or "") != (r.get("vi") or "") and store.set_vi(r, s.get("vi") or "", "post", "khoá thuật ngữ (type=lock)"):
            n += 1
    return n
