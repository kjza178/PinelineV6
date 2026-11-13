"""Bước 6 (2026-11-12 next) — bổ sung cặp xưng hô THIẾU bằng 1 lần gọi model (cờ SIMPLE_PAIR_FILL=1).

Sau review lượt 1: cặp spk→to mà cả hai là nhân vật plan, CHƯA có cặp (kể cả sau bảng quy tắc bước 2), xuất hiện
≥ SIMPLE_PAIR_FILL_MIN câu (rv, conf ≥ SIMPLE_CONF_MIN — đếm như suggest.missing_pairs) → gom các câu thoại THẬT
giữa 2 người (cả hai chiều) + giới / vai / quan hệ → MỘT lần gọi model «mini» (SIMPLE_PAIR_FILL_MODEL, trống = model
REVIEW) qua cloud_retry.call_with_cloud_retry(retries=0) → cặp mới (src="llm_fill", conf).
Cặp nhận (conf ≥ SIMPLE_PAIR_FILL_CONF) được thêm vào PlanView cho bộ kiểm + vòng vá (câu lệch cặp mới bị cờ
pronoun → repair). KHÔNG ghi glossary.json.
Lỗi / 429 / phản hồi hỏng → bỏ qua bước này (không park, không retry chờ).
"""
from __future__ import annotations

import json

from . import cfg, suggest
from .plan_view import PlanView, forms, norm_name

MAX_PAIRS = 8
MAX_LINES = 12

SYSTEM = (
    "Bạn là chuyên gia xưng hô lồng tiếng phim Trung → Việt. Với MỖI cặp (người nói → người nghe) dưới đây, dựa trên "
    "giới, vai, quan hệ, thể loại và các câu thoại THẬT giữa hai người, chọn cách TỰ XƯNG (self) và cách GỌI người "
    "nghe (call) tự nhiên, nhất quán cho cả phim. Có thể cho 2 dạng cách bằng « / » nếu cả hai đều đúng. "
    "conf = độ chắc 0..1 (đoán mò → < 0.5).\n"
    "Trả về DUY NHẤT JSON: {\"pairs\":[{\"who\":\"<tên VI>\",\"to\":\"<tên VI>\",\"self\":\"…\",\"call\":\"…\","
    "\"conf\":<0..1>,\"note\":\"<≤15 từ lý do>\"}]} — đúng các cặp được hỏi, không markdown."
)


def candidates(rows: list[dict], pv: PlanView, conf_min: float, min_n: int) -> list[dict]:
    return [d for d in suggest.missing_pairs(rows, pv, conf_min) if d["n"] >= min_n][:MAX_PAIRS]


def _char(pv: PlanView, name: str) -> dict:
    k = norm_name(name)
    for c in pv.chars:
        if norm_name(c.get("vi")) == k:
            return c
    return {}


def _lines_between(rows: list[dict], pv: PlanView, a: str, b: str) -> list[dict]:
    ka, kb = norm_name(a), norm_name(b)
    out = []
    for r in rows:
        s, t = norm_name(pv.canon(r.get("s_spk"))), norm_name(pv.canon(r.get("s_to")))
        if (s, t) in ((ka, kb), (kb, ka)) and (r.get("vi") or "").strip():
            out.append({"i": r.get("i"), "spk": pv.canon(r.get("s_spk")), "to": pv.canon(r.get("s_to")),
                        "zh": r.get("zh") or "", "vi": r.get("vi") or ""})
    return out[:MAX_LINES]


def build_prompt(rows: list[dict], pv: PlanView, cands: list[dict]) -> str:
    L = pv.header_lines()
    for n, d in enumerate(cands, 1):
        a, b = d["who"], d["to"]
        L.append(f"\nCẶP {n}: {a} → {b} ({d['n']} câu)")
        for nm in (a, b):
            c = _char(pv, nm)
            bits = [f"{c.get('zh') or ''} = {nm}"]
            if c.get("gender"):
                bits.append(f"giới {c.get('gender')}")
            for k in ("role", "relation"):
                if c.get(k):
                    bits.append(str(c.get(k))[:120])
            L.append("  - " + " · ".join(bits))
        rev = pv.pair(b, a)
        if rev:
            L.append(f"  (chiều ngược đã có: {b} → {a}: tự xưng {'/'.join(rev['self'])} · gọi {'/'.join(rev['call'])})")
        L.append("  Thoại (i | người nói → người nghe | zh | vi hiện tại):")
        for x in _lines_between(rows, pv, a, b):
            L.append(f"    {x['i']} | {x['spk']} → {x['to']} | {x['zh']} | {x['vi']}")
    return "\n".join(L)


def parse(text: str | None, want: set[tuple[str, str]], pv: PlanView, conf_min: float) -> list[dict]:
    from .stage_review import _decode
    data = _decode(text)
    arr = data.get("pairs") if isinstance(data, dict) else None
    out = []
    for it in arr if isinstance(arr, list) else []:
        if not isinstance(it, dict):
            continue
        a, b = pv.canon(it.get("who")), pv.canon(it.get("to"))
        if (norm_name(a), norm_name(b)) not in want:
            continue
        try:
            conf = max(0.0, min(1.0, float(it.get("conf"))))
        except (TypeError, ValueError):
            continue
        sf, cl = forms(it.get("self")), forms(it.get("call"))
        if conf < conf_min or (not sf and not cl):
            continue
        out.append({"who": a, "to": b, "self": sf, "call": cl, "conf": round(conf, 3),
                    "note": ("bổ sung bởi model: " + str(it.get("note") or "")).strip()[:100], "src": "llm_fill"})
    return out


def run(rows: list[dict], pv: PlanView, *, model: str | None, conf_review: float, min_n: int,
        conf_min: float, stop_check=None) -> dict:
    """Trả {"asked": [...], "added": [...], "rows": [i…], "error"?}. Không ném (trừ «stopped by user»)."""
    cands = candidates(rows, pv, conf_review, min_n)
    info: dict = {"asked": [{"who": d["who"], "to": d["to"], "n": d["n"]} for d in cands], "added": [], "rows": []}
    if not cands:
        return info
    want = {(norm_name(d["who"]), norm_name(d["to"])) for d in cands}
    user = build_prompt(rows, pv, cands)
    try:
        import engines.cloud_retry as cloud_retry
        from engines.llm import chat

        def _once():
            return chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                        model=model, temperature=0.1, json_mode=True,
                        max_tokens=min(cfg.max_tokens(), 2048), timeout=cfg.timeout())
        out = cloud_retry.call_with_cloud_retry(_once, model=model, label="[simple-pairfill]",
                                                what=f"{len(cands)} cặp", stop_check=stop_check,
                                                retries=0, cfg_get=cfg.get)
        got = parse(out, want, pv, conf_min)
    except Exception as e:  # noqa: BLE001
        if "stopped by user" in str(e).lower():
            raise
        info["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        print(f"[simple-pairfill] bỏ qua ({info['error']})", flush=True)
        return info
    for p in got:
        if pv.add_pair(p):
            info["added"].append(p)
    keys = {(norm_name(p["who"]), norm_name(p["to"])) for p in info["added"]}
    for r in rows:
        if (norm_name(pv.canon(r.get("s_spk"))), norm_name(pv.canon(r.get("s_to")))) in keys:
            info["rows"].append(r.get("i"))
    return info
