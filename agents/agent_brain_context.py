"""System snapshot cho Second Brain LLM — toàn hệ, không chỉ 1 job."""
from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
import json
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os


def _sidecar_url() -> str:
    from core.instance import sidecar_url  # V6 F4: sidecar của instance (v6: 8816), không 8802
    return sidecar_url()


def _self_base() -> str:
    """URL server Pineline đang chạy brain (fix 2026-11-11: trước cứng :8801 → V4/V5 điều khiển nhầm V2)."""
    from core.instance import server_base  # V6 F4: thiếu env → cổng instance, không 8801
    return server_base()


def _ping(url: str, timeout: float = 2.0) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 200) or 200)
            body = resp.read()[:200]
            return {"ok": 200 <= code < 300, "code": code, "hint": body[:80].decode("utf-8", "ignore")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "code": e.code, "hint": str(e.reason)[:80]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "code": 0, "hint": str(e)[:100]}


def _disk(path: Path) -> dict[str, Any]:
    try:
        u = shutil.disk_usage(str(path))
        return {
            "path": str(path),
            "free_gb": round(u.free / (1024**3), 1),
            "total_gb": round(u.total / (1024**3), 1),
            "used_pct": round(100 * (1 - u.free / max(u.total, 1)), 1),
        }
    except Exception as e:  # noqa: BLE001
        return {"path": str(path), "error": str(e)[:80]}


def _job_brief(work_root: Path, d: Path) -> dict[str, Any] | None:
    mf = d / "manifest.json"
    if not mf.is_file():
        return None
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    stages = data.get("stages") or {}
    agent = data.get("agent") or {}
    brief_st = {}
    for name in ("download", "asr", "translate", "tts", "mux"):
        st = stages.get(name) or {}
        if not isinstance(st, dict):
            continue
        status = st.get("status")
        if status in (None, "pending") and name not in ("translate",):
            # still include if others running
            pass
        brief_st[name] = {
            "status": status,
            "cur": (st.get("cur_text") or "")[:80],
            "progress": st.get("progress") or st.get("review_progress"),
            "rd": st.get("review_done"),
            "rt": st.get("review_total"),
        }
    qa = {}
    qbp = d / "qa_brain.json"
    if qbp.is_file():
        try:
            qb = json.loads(qbp.read_text(encoding="utf-8"))
            qa = {
                "high_open": qb.get("high_open") or qb.get("n_high"),
                "by_kind": qb.get("by_kind"),
                "high_i": (qb.get("high_i") or [])[:12],
            }
        except Exception:  # noqa: BLE001
            pass
    title = (
        (data.get("request") or {}).get("title")
        or (stages.get("download") or {}).get("title")
        or d.name
    )
    phase_name, phase_busy, can_hold = "idle", None, False
    try:
        from agents.agent_hang_fix import _review_or_translate_busy
        phase_busy = _review_or_translate_busy(d)
        tr0 = stages.get("translate") or {}
        tts0 = stages.get("tts") or {}
        mux0 = stages.get("mux") or {}
        tts_st = str(tts0.get("status") or "").lower()
        mux_st = str(mux0.get("status") or "").lower()
        tr_st = str(tr0.get("status") or "").lower()
        try:
            rp = float(tr0["review_progress"]) if tr0.get("review_progress") is not None else None
        except (TypeError, ValueError):
            rp = None
        if tts_st in ("done", "ok", "completed", "running") or mux_st in (
            "done", "ok", "completed", "running",
        ):
            phase_name = "post_tts"
        elif phase_busy:
            phase_name = "mid_review"
        elif tr_st in ("done", "ok", "completed") or (
            bool(tr0.get("reviewed") or tr0.get("review")) and rp is not None and rp >= 99.5
        ):
            phase_name = "pre_tts"
            can_hold = True
    except Exception:  # noqa: BLE001
        pass
    return {
        "job_id": d.name,
        "title": str(title)[:80],
        "stages": brief_st,
        "phase": {
            "name": phase_name,
            "busy": phase_busy,
            "can_hold": can_hold,
            "review_progress": (brief_st.get("translate") or {}).get("progress"),
        },
        "agent": {
            "hold": agent.get("hold_after_translate"),
            "qa_blocked": agent.get("qa_blocked"),
            "held_at": agent.get("held_at"),
            "hold_reason": (agent.get("hold_reason") or "")[:120],
            # fix 2026-11-13 agentN: park/split_wait = TỰ thử lại (KHÔNG phải HOLD cần người)
            "hold_class": _hold_cls(agent),
        },
        "finished": _finished(d),
        "qa_brain": qa,
        "stop": (d / ".stop").exists(),
        "has_script": (d / "script.json").is_file(),
        "has_output": (d / "output.mp4").is_file(),
    }


def _hold_cls(agent: dict) -> str | None:
    try:
        from agents.agent_hold import hold_class
        return hold_class(agent)
    except Exception:  # noqa: BLE001
        return None


def _finished(d: Path) -> bool:
    try:
        from agents.agent_hold import job_finished
        return bool(job_finished(d))
    except Exception:  # noqa: BLE001
        return False


def build_system_snapshot(work_root: Path, *, max_jobs: int = 25) -> dict[str, Any]:
    work_root = Path(work_root)
    jobs: list[dict] = []
    if work_root.is_dir():
        dirs = sorted(
            [d for d in work_root.iterdir() if d.is_dir() and (d / "manifest.json").is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:max_jobs]
        for d in dirs:
            b = _job_brief(work_root, d)
            if b:
                jobs.append(b)

    # chỉ giữ job “đáng quan tâm” + vài done gần đây
    interesting = []
    for j in jobs:
        st = j.get("stages") or {}
        statuses = [str((st.get(s) or {}).get("status") or "") for s in st]
        agent = j.get("agent") or {}
        if (
            any(s in ("running", "failed") for s in statuses)
            or agent.get("hold")
            or agent.get("qa_blocked")
            or (j.get("qa_brain") or {}).get("high_open")
            or j.get("stop")
        ):
            interesting.append(j)
    if len(interesting) < 8:
        for j in jobs:
            if j not in interesting:
                interesting.append(j)
            if len(interesting) >= 12:
                break

    queue = {}
    try:
        import core.job_queue as _jq
        queue = {
            "busy": bool(_jq.queue_busy()),
            "state": _jq.queue_state() if hasattr(_jq, "queue_state") else None,
        }
        # truncate
        if isinstance(queue.get("state"), dict):
            qs = dict(queue["state"])
            for k in ("history", "queue_items", "opts_by_job"):
                if k in qs and isinstance(qs[k], (list, dict)):
                    qs[k] = f"<{type(qs[k]).__name__} n={len(qs[k])}>"
            queue["state"] = qs
    except Exception as e:  # noqa: BLE001
        queue = {"error": str(e)[:100]}

    infra = {
        "server_8801": _ping(_self_base() + "/api/agent/status"),
        "sidecar": _ping(_sidecar_url() + "/api/agent/status"),
        "llama_8083": _ping(_machine.llama_base_url() + "/models"),
        "chatgpt_3377": _ping(_machine.aurora_base_url() + "/models"),
        "hongguo_8000": _ping(_machine.hongguo_base_url() + "/"),
    }

    # active llama preset
    active = {}
    try:
        ap = _machine.presets_active_json()
        if ap.is_file():
            active = json.loads(ap.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass

    disks = [
        _disk(Path("D:/")),
        _disk(Path("Z:/") if Path("Z:/").exists() else work_root),
    ]

    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "work_root": str(work_root),
        "infra": infra,
        "llama_active": {
            "id": active.get("id") or active.get("alias"),
            "started": active.get("started"),
        },
        "queue": queue,
        "jobs_focus": interesting,
        "n_jobs_scanned": len(jobs),
        "disk": disks,
    }


def snapshot_to_prompt(snap: dict[str, Any], *, max_chars: int = 12000) -> str:
    """JSON gọn cho LLM user message."""
    text = json.dumps(snap, ensure_ascii=False, indent=2)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…[truncated]"
    return text


BRAIN_LLM_INSTRUCTION = """Bạn là Pineline SECOND BRAIN — AI agent quyết định thật (không chat user).

Mọi quyết định HOLD / heal / bỏ qua PHẢI dựa trên tool đọc job (pineline_job / diagnose_job),
KHÔNG được HOLD chỉ vì snapshot nói high QA.

Tools: pineline_status, pineline_job, pineline_diagnose_job, heal_job,
pineline_hold_after_translate, script_review, script_edit_*, queue_*, running_check, …
CẤM nói «không có tool». CẤM chỉ viết text mà không gọi tool khi còn việc.

Với MỖI job ưu tiên, bắt buộc làm đủ vòng:
A. Đang làm gì? stage nào? phase (mid_review/pre_tts/post_tts/idle)?
B. Chạy ổn hay không? (progress tăng? treo? failed?)
C. Nếu không ổn: lỗi ở đâu (stage/QA kind/i=…)?
D. Phương án: heal_job / script_edit / script_review / đợi mid_review / CLEAR hold /
   HOLD chỉ khi phase=pre_tts VÀ còn high SAU khi đã đọc pineline_job.
E. Gọi tool thực thi phương án (allow_mutate=ON).

Luật cứng:
- mid_review (đang soát/dịch) → CẤM hold_after_translate; CẤM stop; chỉ quan sát/đợi.
- post_tts (tts/mux done|running) → CẤM HOLD.
- pre_tts + high QA → được HOLD sau khi đã pineline_job và nêu phương án.
- .stop=true → bỏ qua. Không force ASR/download trừ evidence rõ.
- finished=true (có output / đã đăng / đã ghép Full) → BỎ QUA hẳn: không heal, không DECISION.
- agent.hold_class=park|split_wait = tạm dừng TỰ thử lại (không phải HOLD cần người) → không heal/HOLD, chỉ đợi.

Kết thúc ≤10 dòng, format:
DECISION <job_id>: stage=… | ok=yes/no | issue=… | plan=… | tools=…

SNAPSHOT (gợi ý ưu tiên — vẫn phải gọi tool xác minh):
"""
