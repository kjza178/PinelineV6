"""pipeline/base.py — Stage + Gate tường minh (thay `server._auto_gpu` 300 dòng if/return).

    runner = Runner(STUDIO_PIPELINE)
    result = runner.run(ctx)        # → RunResult(status="done"|"park"|"hold"|"fail"|"stopped", ...)

- Stage: `done(ctx)` True → bỏ qua (resume sau crash/restart); `run(ctx)` làm việc.
- Gate: `check(ctx)` → GateResult PASS / PARK(retry_at) / HOLD(reason) / FAIL(msg).
- Kiểm cờ dừng giữa mọi bước. Kết quả trả về queue worker → quyết định giữ ghế / job kế.
- Không còn dò chuỗi tiếng Việt trong cur_text để biết park (server.py:4496 cũ).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class Stopped(Exception):
    """Người dùng bấm Dừng."""


@dataclass
class Context:
    job_id: str
    work_dir: Path
    opts: dict = field(default_factory=dict)
    is_stopped: Callable[[], bool] = lambda: False
    log: Callable[[str], None] = print
    extra: dict = field(default_factory=dict)

    def check_stop(self) -> None:
        if self.is_stopped() or (self.work_dir / ".stop").exists():
            raise Stopped(self.job_id)


@dataclass(frozen=True)
class GateResult:
    kind: str                      # pass | park | hold | fail
    reason: str = ""
    detail: str = ""
    retry_at: float | None = None

    @staticmethod
    def ok() -> "GateResult":
        return GateResult("pass")

    @staticmethod
    def park(reason: str, detail: str = "", retry_at: float | None = None) -> "GateResult":
        return GateResult("park", reason, detail, retry_at)

    @staticmethod
    def hold(reason: str, detail: str = "") -> "GateResult":
        return GateResult("hold", reason, detail)

    @staticmethod
    def fail(detail: str) -> "GateResult":
        return GateResult("fail", "fail", detail)


class Stage:
    name = "stage"

    def done(self, ctx: Context) -> bool:          # noqa: ARG002
        return False

    def run(self, ctx: Context) -> None:           # pragma: no cover - interface
        raise NotImplementedError


class Gate:
    name = "gate"

    def check(self, ctx: Context) -> GateResult:   # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class RunResult:
    status: str                     # done | park | hold | fail | stopped
    at: str = ""                    # tên stage/gate dừng lại
    detail: str = ""
    retry_at: float | None = None
    steps: list[tuple[str, str, float]] = field(default_factory=list)   # (tên, kết quả, giây)

    @property
    def keeps_seat(self) -> bool:
        """Studio SOP: park/hold giữ ghế hàng đợi, không lấy job kế."""
        return self.status in ("park", "hold")


class Runner:
    def __init__(self, steps: list[Stage | Gate], *, on_step: Callable[[str, str], Any] | None = None) -> None:
        self.steps = steps
        self.on_step = on_step or (lambda name, what: None)

    def run(self, ctx: Context) -> RunResult:
        res = RunResult("done")
        try:
            for step in self.steps:
                ctx.check_stop()
                t0 = time.monotonic()
                if isinstance(step, Gate):
                    g = step.check(ctx)
                    res.steps.append((step.name, g.kind, time.monotonic() - t0))
                    self.on_step(step.name, f"gate:{g.kind}")
                    if g.kind != "pass":
                        status = {"park": "park", "hold": "hold", "fail": "fail"}[g.kind]
                        return RunResult(status, step.name, g.detail or g.reason, g.retry_at, res.steps)
                    continue
                if step.done(ctx):
                    res.steps.append((step.name, "skip", 0.0))
                    self.on_step(step.name, "skip")
                    continue
                self.on_step(step.name, "run")
                step.run(ctx)
                res.steps.append((step.name, "ok", time.monotonic() - t0))
            return res
        except Stopped:
            return RunResult("stopped", res.steps[-1][0] if res.steps else "", "đã dừng theo yêu cầu", None, res.steps)
        except Exception as e:  # noqa: BLE001 — trả về cho queue, không nuốt im lặng
            name = getattr(locals().get("step"), "name", "?")
            ctx.log(f"[pipeline] {ctx.job_id}: lỗi ở {name}: {e}")
            return RunResult("fail", name, f"{type(e).__name__}: {e}"[:300], None, res.steps)
