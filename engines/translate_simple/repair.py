"""Bước 5: vá ĐÚNG các câu bị bộ kiểm đánh cờ — tối đa SIMPLE_REPAIR_ROUNDS (≤2) lượt.

Mỗi lượt: gom câu có cờ thành lô (≤ SIMPLE_BATCH, ngữ cảnh ±SIMPLE_CTX chỉ đọc) → 1 lần gọi/lô
(prompt vá: trường «loi» cho từng câu) → ghi vi/spk/to/conf → hậu kỳ tất định trên đúng các câu đó →
kiểm lại đúng các câu đó. Lô lỗi → giữ nguyên (cờ còn, cổng quyết). Không gửi câu không có cờ.
Câu manual không bao giờ bị cờ nên không bao giờ được gửi.
"""
from __future__ import annotations

from . import cfg, post, stage_review, validate
from .plan_view import PlanView
from .script_store import ScriptStore


def run(store: ScriptStore, pv: PlanView, flags: dict[int, list[dict]], *, model: str | None,
        stop_check=None, progress=None, rounds: int | None = None) -> tuple[dict[int, list[dict]], list[dict]]:
    """Trả (cờ còn lại, nhật ký từng lượt [{round, sent, fixed, left}])."""
    rows = store.rows
    rounds = cfg.repair_rounds() if rounds is None else max(0, min(2, int(rounds)))
    log: list[dict] = []
    pos_of = {int(r["i"]): p for p, r in enumerate(rows) if r.get("i") is not None}
    for rnd in range(1, rounds + 1):
        if not flags:
            break
        if stop_check and stop_check():
            raise RuntimeError("stopped by user")
        idx = sorted(pos_of[i] for i in flags if i in pos_of)
        jobs = stage_review.make_jobs(rows, idx, cfg.batch(), cfg.ctx())
        sent = [int(rows[p]["i"]) for p in idx]
        print(f"[simple-repair] lượt {rnd}/{rounds}: {len(sent)} câu bị cờ · {len(jobs)} lô", flush=True)
        for j in jobs:
            lot = [rows[p] for p in j["pos"]]
            want = {int(r["i"]) for r in lot}
            system, user = stage_review.build_prompt(lot, j["before"], j["after"], pv, repair_flags=flags)
            try:
                res = stage_review.call_llm(system, user, want, model=model, stop_check=stop_check,
                                            label="[simple-repair]")
            except Exception as e:  # noqa: BLE001
                if "stopped by user" in str(e).lower():
                    raise
                for r in lot:
                    store.note(f"repair{rnd}", r.get("i"), f"lô vá lỗi: {str(e)[:160]}")
                continue
            stage_review.apply_result(store, rows, j, res, f"repair{rnd}")
        touched = [rows[p] for p in idx]
        post.apply(store, pv.plan, touched)
        new_flags = validate.validate(rows, pv, cfg.conf_min(), only=set(sent))
        for i in sent:
            if i not in new_flags:
                store.note(f"repair{rnd}", i, "hết cờ")
        store.write()
        log.append({"round": rnd, "sent": len(sent), "fixed": len(sent) - len(new_flags), "left": len(new_flags)})
        flags = new_flags
        if progress:
            progress(f"vá lượt {rnd}: còn {len(flags)} cờ")
    return flags, log
