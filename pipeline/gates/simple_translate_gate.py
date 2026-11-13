"""pipeline/gates/simple_translate_gate.py — Gate sau Stage translate (simple): done → PASS / park → PARK / hold → HOLD.

Dùng cơ chế park/hold sẵn có của V6:
  • PARK  = agent.held_at=waiting_review + simple_park + review_retries/review_retry_at (B2; queue
            `_queue_park_ready` / state.job_state.park_ready chờ tới hạn rồi chạy lại cùng job, không nhả ghế).
  • HOLD  = held_at=simple_gate + hold_after_translate (cần người; agent.qa_override → cho qua),
            hoặc review_failed khi quá QUEUE_REVIEW_RETRY_MAX lượt.
  • PASS  = gỡ dấu park/hold của simple → đi tiếp cổng TTS cũ (translate_ready_for_tts → Brain → HOLD → QA gate → TTS).
Ghi manifest DUY NHẤT qua state.job_state.apply_simple_gate (một Manifest.update).
Port Q6 11-13: câu ZH rác / lượt review câu thiếu / nhận nháp ≤ tolerance xử lý TRONG engine
(engines/translate_simple/pipeline.py) trước khi ra status → Gate chỉ ghi nhận vào ctx.extra["simple_gate"].
"""
from __future__ import annotations

import os

from pipeline.base import Context, Gate, GateResult
from pipeline.stages.simple_translate import RESULT_KEY, _translate_stage


def _retry_sec() -> float:
    try:
        return float(os.getenv("QUEUE_REVIEW_RETRY_SEC", "300") or 300)
    except ValueError:
        return 300.0


def _retry_max() -> int:
    try:
        return int(os.getenv("QUEUE_REVIEW_RETRY_MAX", "3") or 3)
    except ValueError:
        return 3


class SimpleTranslateGate(Gate):
    name = "translate_simple_gate"

    def __init__(self, *, retry_sec: float | None = None, max_tries: int | None = None) -> None:
        self.retry_sec = retry_sec
        self.max_tries = max_tries

    def check(self, ctx: Context) -> GateResult:
        from state import job_state as js
        res = ctx.extra.get(RESULT_KEY)
        if not isinstance(res, dict):
            # Stage bỏ qua (resume) → đọc cổng đã ghi trong manifest
            st = _translate_stage(ctx)
            res = {"status": "done" if st.get("status") == "done" else ((st.get("simple") or {}).get("status") or "park"),
                   "gate": {"reasons": (st.get("simple") or {}).get("reasons") or []}}
        status = str(res.get("status") or "park")
        why = "; ".join((res.get("gate") or {}).get("reasons") or []) or status
        out = js.apply_simple_gate(
            ctx.work_dir, status=status, why=why,
            retry_sec=self.retry_sec if self.retry_sec is not None else _retry_sec(),
            max_tries=self.max_tries if self.max_tries is not None else _retry_max())
        r = out.get("result")
        ctx.extra["simple_gate"] = {"status": status, "why": why, **out}
        # port Q6 11-13 (V2 agentM): engine đã tự soát câu rác (junk) + lượt review câu thiếu + nhận nháp
        # ≤ SIMPLE_UNREVIEWED_TOLERANCE → Gate chỉ ghi nhận để log/UI (quyết định vẫn theo status engine).
        rv = res.get("review") if isinstance(res.get("review"), dict) else {}
        acc = list(rv.get("unreviewed_accepted") or [])
        if acc or rv.get("junk") or rv.get("review_missing"):
            ctx.extra["simple_gate"].update(junk=int(rv.get("junk") or 0), unreviewed_accepted=acc,
                                            review_missing=rv.get("review_missing"))
            if acc:
                ctx.log(f"[auto] {ctx.job_id}: dịch simple nhận {len(acc)} câu nháp chưa soát "
                        f"(≤ {rv.get('unreviewed_tolerance')})")
        if r == "pass":
            return GateResult.ok()
        if r == "hold":
            return GateResult.hold("simple_gate", f"dịch simple {status.upper()} — {why}")
        return GateResult.park("waiting_review", f"dịch simple PARK — {why}")


def simple_translate_steps(*, runner=None, retry_sec: float | None = None, max_tries: int | None = None) -> list:
    """[TranslateSimple, SimpleTranslateGate] — đoạn pipeline dịch dùng trong server._auto_gpu / Runner."""
    from pipeline.stages.simple_translate import TranslateSimple
    return [TranslateSimple(runner), SimpleTranslateGate(retry_sec=retry_sec, max_tries=max_tries)]
