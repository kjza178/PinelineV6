"""Đề xuất cặp xưng hô CHƯA có trong plan (review 2026-11-12) → work_dir/plan_suggestions.json.

Hai nguồn:
  model_pairs   : REVIEW tự khai trong khoá "new_pairs" (tự xưng / cách gọi model đã dùng)
  missing_pairs : tất định — câu đã rv, conf ≥ SIMPLE_CONF_MIN, spk và to đều là nhân vật CÓ TRONG plan
                  nhưng plan không có cặp spk→to (vd Hà Hải Khôn → Mị Nương) → bộ kiểm không kiểm được.

CHỈ ghi đề xuất: KHÔNG chèn vào glossary.json / bảng bộ (series_glossary). Người duyệt chép vào
glossary.json → pronouns nếu đúng (lần chạy sau review + bộ kiểm dùng cặp đó).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .plan_view import PlanView, forms, norm_name

FILE = "plan_suggestions.json"


def _known(pv: PlanView) -> set[str]:
    return {norm_name(v) for v in pv.alias.values()}


def missing_pairs(rows: list[dict], pv: PlanView, conf_min: float) -> list[dict]:
    known = _known(pv)
    acc: dict[tuple[str, str], dict] = {}
    for r in rows:
        if not r.get("rv"):
            continue
        try:
            conf = float(r.get("s_conf") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < conf_min:
            continue
        a, b = pv.canon(r.get("s_spk")), pv.canon(r.get("s_to"))
        if not a or not b or norm_name(a) == norm_name(b):
            continue
        if norm_name(a) not in known or norm_name(b) not in known or pv.pair(a, b):
            continue
        d = acc.setdefault((norm_name(a), norm_name(b)), {"who": a, "to": b, "n": 0, "i": []})
        d["n"] += 1
        if len(d["i"]) < 10:
            d["i"].append(r.get("i"))
    return sorted(acc.values(), key=lambda d: -d["n"])


def clean_model_pairs(raw: list[dict], pv: PlanView) -> list[dict]:
    """Chuẩn hoá tên theo plan, bỏ cặp đã có trong plan / tự-với-tự / dạng rỗng; gộp trùng (đếm n)."""
    acc: dict[tuple[str, str], dict] = {}
    for it in raw or []:
        a, b = pv.canon(it.get("who")), pv.canon(it.get("to"))
        if not a or not b or norm_name(a) == norm_name(b) or pv.pair(a, b):
            continue
        sf, cl = forms(it.get("self")), forms(it.get("call"))
        if not sf and not cl:
            continue
        key = (norm_name(a), norm_name(b))
        d = acc.setdefault(key, {"who": a, "to": b, "self": sf, "call": cl, "n": 0, "i": []})
        d["n"] += int(it.get("n") or 1)
        for x in ([it.get("i")] if not isinstance(it.get("i"), list) else it.get("i")):
            if x is not None and x not in d["i"] and len(d["i"]) < 10:
                d["i"].append(x)
    return sorted(acc.values(), key=lambda d: -d["n"])


def write(work_dir: str | Path, rows: list[dict], pv: PlanView, model_raw: list[dict], conf_min: float,
          extra: dict | None = None) -> dict:
    """Gộp với file cũ (model_pairs cộng dồn qua các lần chạy), tính lại missing_pairs; ghi atomic.
    Trả {"n_model": , "n_missing": }."""
    from core.fileio import atomic_write_json
    p = Path(work_dir) / FILE
    old = []
    if p.is_file():
        try:
            old = list((json.loads(p.read_text(encoding="utf-8")) or {}).get("model_pairs") or [])
        except Exception:  # noqa: BLE001
            old = []
    model = clean_model_pairs(old + list(model_raw or []), pv)
    miss = missing_pairs(rows, pv, conf_min)
    if not model and not miss and not p.is_file() and not (extra and any(extra.values())):
        return {"n_model": 0, "n_missing": 0}
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": ("ĐỀ XUẤT — không tự chèn vào glossary.json / bảng bộ. Đúng thì chép vào glossary.json → "
                 "pronouns {who, to, self, call}; lần chạy sau review + bộ kiểm dùng cặp đó."),
        "model_pairs": model, "missing_pairs": miss,
    }
    if extra:   # 2026-11-12 next: cặp suy luận (rule / llm_fill) — chỉ khi bật cờ
        payload.update(extra)
    atomic_write_json(p, payload, fsync=False)
    return {"n_model": len(model), "n_missing": len(miss)}
