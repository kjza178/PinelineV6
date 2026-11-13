"""Bước 6: cổng của luồng simple (hàm thuần — test được).

done  : đủ câu so ASR (translate_coverage_gap = 0) + không câu trống + soát hết (mọi câu rv) +
        không còn chữ Hán + số câu còn cờ ≤ ngưỡng (SIMPLE_GATE_FLAG_MAX / SIMPLE_GATE_FLAG_RATIO)
park  : còn câu chưa soát / trống / còn chữ Hán / thiếu câu — lỗi tạm (cloud) → server park
        waiting_review (cơ chế B2: tự thử lại sau QUEUE_REVIEW_RETRY_SEC, tối đa QUEUE_REVIEW_RETRY_MAX)
hold  : soát đủ nhưng còn quá nhiều cờ (xưng hô/tên) sau vá → cần người xem (HOLD sau dịch)
"""
from __future__ import annotations

from . import cfg


def decide(*, n_rows: int, n_asr: int, coverage_gap: int, n_missing: int, n_unreviewed: int,
           flags: dict[int, list[dict]]) -> dict:
    n_cjk = sum(1 for fl in flags.values() if any(f["code"] == "cjk" for f in fl))
    soft = {i: fl for i, fl in flags.items() if not any(f["code"] in ("empty", "cjk") for f in fl)}
    by_kind: dict[str, int] = {}
    for fl in flags.values():
        for f in fl:
            by_kind[f["code"]] = by_kind.get(f["code"], 0) + 1
    limit = cfg.gate_flag_max(n_rows)
    reasons = []
    if n_rows <= 0:
        reasons.append("chưa có script")
    if coverage_gap > 0:
        reasons.append(f"thiếu ~{coverage_gap} câu so với ASR")
    if n_missing > 0:
        reasons.append(f"còn {n_missing} câu VI trống")
    if n_unreviewed > 0:
        reasons.append(f"còn {n_unreviewed} câu chưa soát")
    if n_cjk > 0:
        reasons.append(f"còn {n_cjk} câu có chữ Hán")
    if reasons:
        status = "park"
    elif len(soft) > limit:
        status = "hold"
        reasons.append(f"còn {len(soft)} câu bị cờ (> {limit}): "
                       + ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
    else:
        status = "done"
    return {"status": status, "reasons": reasons, "n_rows": n_rows, "n_asr": n_asr,
            "coverage_gap": coverage_gap, "n_missing": n_missing, "n_unreviewed": n_unreviewed,
            "n_flagged": len(flags), "n_flag_soft": len(soft), "flag_limit": limit, "flags_by_kind": by_kind,
            "flagged_i": sorted(flags)[:200]}
