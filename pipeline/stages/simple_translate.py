"""pipeline/stages/simple_translate.py — Stage «translate» theo luồng dịch simple (port V2 2026-11-12, F6).

Thân luồng ở `engines/translate_simple/` (plan ∥ NMT → review cloud theo lô → hậu kỳ + kiểm → vá → cổng).
Stage này CHỈ gọi engine và để kết quả cho Gate `SimpleTranslateGate` (pipeline/gates/simple_translate_gate.py);
không ghi manifest trực tiếp (engine ghi stages.translate qua state.job_state.mutate; Gate ghi agent qua
state.job_state.apply_simple_gate).

  ctx.opts:  target (mặc định «Tiếng Việt»), force (dịch lại từ đầu), rereview (soát lại câu đã rv)
  ctx.extra["simple_result"]: dict trả về của engines.translate_simple.pipeline.run
             (lỗi không phải Dừng → {"status": "park", "gate": {"reasons": ["lỗi: …"]}} — như V2: park, không fail)
"""
from __future__ import annotations

import json

from pipeline.base import Context, Stage, Stopped

RESULT_KEY = "simple_result"


def _translate_stage(ctx: Context) -> dict:
    try:
        d = json.loads((ctx.work_dir / "manifest.json").read_text(encoding="utf-8"))
        return ((d.get("stages") or {}).get("translate")) or {}
    except Exception:  # noqa: BLE001
        return {}


class TranslateSimple(Stage):
    name = "translate"

    def __init__(self, runner=None) -> None:
        # runner(work_dir, target, *, force, rereview, stop_check) — test tiêm hàm giả; mặc định engine thật
        self._runner = runner

    def done(self, ctx: Context) -> bool:
        """Mặc định LUÔN chạy (như V2: pipeline.run tự resume — câu đã rv giữ nguyên, chỉ soát phần còn thiếu).
        ctx.opts["skip_if_done"]=True: đã xong bằng simple (và không force/rereview) → bỏ qua; Gate đọc cổng
        đã ghi trong manifest."""
        if not ctx.opts.get("skip_if_done") or ctx.opts.get("force") or ctx.opts.get("rereview"):
            return False
        st = _translate_stage(ctx)
        return st.get("status") == "done" and st.get("mode") == "simple"

    def run(self, ctx: Context) -> None:
        run = self._runner
        if run is None:
            from engines.translate_simple import pipeline as _sp
            run = _sp.run

        def stop_check() -> bool:
            return bool(ctx.is_stopped()) or (ctx.work_dir / ".stop").exists()

        try:
            res = run(ctx.work_dir, ctx.opts.get("target") or "Tiếng Việt",
                      force=bool(ctx.opts.get("force")), rereview=bool(ctx.opts.get("rereview")),
                      stop_check=stop_check)
        except Exception as e:  # noqa: BLE001
            if "stopped by user" in str(e).lower() or stop_check():
                raise Stopped(ctx.job_id) from e
            ctx.log(f"[auto] {ctx.job_id}: dịch simple lỗi: {e} → park waiting_review")
            res = {"status": "park", "gate": {"reasons": [f"lỗi: {str(e)[:120]}"]}}
        ctx.extra[RESULT_KEY] = res if isinstance(res, dict) else {"status": "park"}
