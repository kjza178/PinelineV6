"""pipeline/stages/simple_translate.py — Stage «translate» theo luồng dịch simple (port V2 2026-11-12, F6).

Thân luồng ở `engines/translate_simple/` (plan ∥ NMT → review cloud theo lô → hậu kỳ + kiểm → vá → cổng).
Stage này CHỈ gọi engine và để kết quả cho Gate `SimpleTranslateGate` (pipeline/gates/simple_translate_gate.py);
không ghi manifest trực tiếp (engine ghi stages.translate qua state.job_state.mutate; Gate ghi agent qua
state.job_state.apply_simple_gate).

  ctx.opts:  target (mặc định «Tiếng Việt»), force (dịch lại từ đầu), rereview (soát lại câu đã rv)
  ctx.extra["simple_result"]: dict trả về của engines.translate_simple.pipeline.run
             lỗi tạm (cloud/mạng) → {"status": "park", …}: hàng chờ tự thử lại;
             lỗi khác (bug code, file hỏng) → {"status": "error", …}: Gate đánh stage failed ngay (R1.3)
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


def _is_transient(e: BaseException) -> bool:
    """Lỗi đáng thử lại: cloud_retry nhận là limit/5xx/timeout, hoặc lỗi mạng/timeout chuẩn."""
    try:
        from engines.cloud_retry import classify_cloud_retry
        if classify_cloud_retry(e):
            return True
    except Exception:  # noqa: BLE001 — không phân loại được thì xét theo kiểu lỗi bên dưới
        pass
    return isinstance(e, (TimeoutError, ConnectionError))


def _save_traceback(ctx: Context, e: BaseException) -> str:
    """Ghi traceback đầy đủ vào <work_dir>/translate_error.log (nối thêm). Trả tên file ("" nếu không ghi được)."""
    import time
    import traceback
    try:
        p = ctx.work_dir / "translate_error.log"
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} job={ctx.job_id} =====\n")
            f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
        return p.name
    except OSError as e2:
        ctx.log(f"[auto] {ctx.job_id}: không ghi được translate_error.log: {e2}")
        return ""


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
            if _is_transient(e):
                # lỗi tạm (cloud 429/5xx/timeout, mạng) → park: hàng chờ tự thử lại (B2)
                ctx.log(f"[auto] {ctx.job_id}: dịch simple lỗi tạm: {e} → park waiting_review")
                res = {"status": "park", "gate": {"reasons": [f"lỗi tạm: {str(e)[:120]}"]}}
            else:
                # R1.3 (fix 2026-11-14): lỗi KHÔNG phải cloud (bug code, file hỏng…) thử lại cũng vô ích. Trước đây
                # vẫn park 3 lượt × 5 phút (giữ ghế hàng chờ ~15 phút) rồi mới HOLD, traceback mất. Nay: ghi đủ
                # traceback ra <job>/translate_error.log và báo «error» → Gate đánh stage failed ngay.
                tb = _save_traceback(ctx, e)
                ctx.log(f"[auto] {ctx.job_id}: dịch simple LỖI (không thử lại): {type(e).__name__}: {e}"
                        + (f" — chi tiết: {tb}" if tb else ""))
                res = {"status": "error", "error_type": type(e).__name__,
                       "gate": {"reasons": [f"lỗi {type(e).__name__}: {str(e)[:160]}"
                                            + (" (xem translate_error.log)" if tb else "")]}}
        ctx.extra[RESULT_KEY] = res if isinstance(res, dict) else {"status": "park"}
