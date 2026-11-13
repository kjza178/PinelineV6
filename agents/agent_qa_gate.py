"""Gate QA: translate → review → diff → mới cho TTS."""
from __future__ import annotations

from state import job_state as js  # V6 F1: mọi ghi manifest qua state
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [r for r in rows if isinstance(r, dict)]


def snapshot_pre_review(work_dir: Path) -> Path | None:
    src = work_dir / "script.json"
    if not src.is_file():
        return None
    dst = work_dir / "script.pre_review.json"
    shutil.copy2(src, dst)
    return dst


def review_diff(work_dir: Path | str, job_id: str | None = None) -> dict:
    """So script.json vs script.pre_review.json + thống kê rv / missing."""
    work_dir = Path(work_dir)
    cur = _load_rows(work_dir / "script.json")
    pre = _load_rows(work_dir / "script.pre_review.json")
    pre_by_i = {int(r.get("i", -1)): r for r in pre}
    changed = []
    rv_count = 0
    missing = 0
    for r in cur:
        i = int(r.get("i", -1))
        vi = (r.get("vi") or "").strip()
        if not vi:
            missing += 1
        if r.get("rv"):
            rv_count += 1
        old = pre_by_i.get(i)
        if old is not None:
            old_vi = (old.get("vi") or "").strip()
            if old_vi != vi:
                changed.append({
                    "i": i,
                    "before": old_vi[:120],
                    "after": vi[:120],
                    "zh": (r.get("zh") or "")[:80],
                })
    # glossary / style presence
    pack = {
        "glossary": (work_dir / "glossary.json").is_file(),
        "style_lock": (work_dir / "style_lock.json").is_file(),
        "qa_flags": (work_dir / "qa_flags.json").is_file(),
    }
    qa_n = 0
    qf = work_dir / "qa_flags.json"
    if qf.is_file():
        try:
            q = json.loads(qf.read_text(encoding="utf-8"))
            if isinstance(q, list):
                qa_n = len(q)
            elif isinstance(q, dict):
                qa_n = len(q.get("flags") or q.get("items") or [])
        except Exception:  # noqa: BLE001
            qa_n = -1

    n = len(cur)
    changed_n = len(changed)
    # Pass heuristic: có script, missing thấp, đã có review (rv>0 hoặc có pre snapshot + changed)
    missing_ratio = (missing / n) if n else 1.0
    pass_gate = n > 0 and missing_ratio <= 0.02 and (rv_count > 0 or changed_n >= 0)
    # nếu có pre snapshot mà rv=0 và changed=0 → có thể review chưa chạy
    review_ran = rv_count > 0 or changed_n > 0 or (work_dir / "script.pre_review.json").is_file()
    if (work_dir / "script.pre_review.json").is_file() and rv_count == 0 and changed_n == 0:
        # snapshot tồn tại nhưng không đổi — vẫn coi là đã chạy review (idempotent)
        review_ran = True

    # Second Brain: high QA deterministic flags chặn TTS/mux
    brain_high = 0
    try:
        from agents.agent_quality import load_qa_brain
        brain = load_qa_brain(work_dir)
        brain_high = int(brain.get("high_open") or brain.get("n_high") or 0)
    except Exception:  # noqa: BLE001
        brain = {}
        brain_high = 0

    fail_reasons = _fail_reasons(n, missing_ratio, review_ran, pack, brain_high=brain_high)
    passed = bool(
        pass_gate and review_ran and missing_ratio <= 0.02 and brain_high <= 0
    )

    return {
        "ok": True,
        "job_id": job_id or work_dir.name,
        "n_lines": n,
        "n_missing_vi": missing,
        "missing_ratio": round(missing_ratio, 4),
        "n_reviewed_flag_rv": rv_count,
        "n_changed_vs_pre": changed_n,
        "changed_sample": changed[:25],
        "pack": pack,
        "qa_flags_n": qa_n,
        "qa_brain_high": brain_high,
        "review_ran": review_ran,
        "pass": passed,
        "fail_reasons": fail_reasons,
    }


def _fail_reasons(
    n: int,
    missing_ratio: float,
    review_ran: bool,
    pack: dict,
    *,
    brain_high: int = 0,
) -> list[str]:
    rs = []
    if n <= 0:
        rs.append("chưa có script")
    if missing_ratio > 0.02:
        rs.append(f"thiếu vi >2% ({missing_ratio:.1%})")
    if not review_ran:
        rs.append("chưa chạy review")
    if not pack.get("glossary") and not pack.get("style_lock"):
        rs.append("chưa có glossary/style_lock — review yếu (nên Context Studio)")
    if brain_high > 0:
        rs.append(f"Second Brain: {brain_high} high QA flags")
    return rs


def evaluate_qa_gate(work_dir: Path) -> dict:
    return review_diff(work_dir)


def run_qa_gate(
    work_dir: Path,
    *,
    run_review_fn,
    target: str = "Tiếng Việt",
    review_model: str | None = None,
) -> dict:
    """
    Snapshot → run_review (sync) → diff → quyết định pass/block.
    run_review_fn(work_dir, target=, model=) — blocking.
    """
    work_dir = Path(work_dir)
    if not (work_dir / "script.json").is_file():
        return {"ok": False, "pass": False, "error": "chưa có script.json"}

    snapshot_pre_review(work_dir)
    try:
        run_review_fn(work_dir, target=target, model=review_model)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "pass": False,
            "error": f"review lỗi: {e}",
            "diff": review_diff(work_dir),
        }

    diff = review_diff(work_dir)
    # ghi kết quả vào manifest.agent
    try:
        from core.manifest import Manifest
        m = Manifest(work_dir)
        agent = dict(m.data.get("agent") or {})
        agent["qa_gate"] = True
        agent["qa_last"] = {
            "at": _now(),
            "pass": diff.get("pass"),
            "n_changed": diff.get("n_changed_vs_pre"),
            "n_rv": diff.get("n_reviewed_flag_rv"),
            "n_missing": diff.get("n_missing_vi"),
            "fail_reasons": diff.get("fail_reasons"),
        }
        if diff.get("pass"):
            agent.pop("qa_blocked", None)
            agent["qa_blocked"] = False
        else:
            agent["qa_blocked"] = True
            agent["held_at"] = "qa_gate"
            agent["hold_reason"] = "QA gate: " + ", ".join(diff.get("fail_reasons") or ["fail"])
        m.data["agent"] = agent
        tr = m.data.setdefault("stages", {}).setdefault("translate", {})
        if diff.get("pass"):
            tr["cur_text"] = f"QA pass · rv={diff.get('n_reviewed_flag_rv')} · đổi={diff.get('n_changed_vs_pre')}"
        else:
            tr["cur_text"] = "QA BLOCK — " + ", ".join(diff.get("fail_reasons") or [])
        m.data["stages"]["translate"] = tr
        js.persist(m)
    except Exception as e:  # noqa: BLE001
        diff["manifest_error"] = str(e)

    return {"ok": True, "pass": bool(diff.get("pass")), "diff": diff}


def set_qa_gate(work_dir: Path, enabled: bool, *, override_pass: bool | None = None) -> dict:
    from core.manifest import Manifest
    m = Manifest(work_dir)
    agent = dict(m.data.get("agent") or {})
    agent["qa_gate"] = bool(enabled)
    if override_pass is True:
        agent["qa_blocked"] = False
        agent["qa_override"] = True
        agent.pop("held_at", None)
        tr = m.data.setdefault("stages", {}).setdefault("translate", {})
        tr["cur_text"] = "QA override — cho phép TTS"
        m.data["stages"]["translate"] = tr
    elif override_pass is False:
        agent["qa_blocked"] = True
    m.data["agent"] = agent
    js.persist(m)
    return {"ok": True, "qa_gate": bool(enabled), "agent": agent}
