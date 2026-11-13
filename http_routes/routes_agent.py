"""AI Agent nhúng (agent_core): hook cho Runtime + /api/agent/*.

Tách từ server.py (đợt 7). Chỉ được nạp từ cuối server.py (sau khi mọi hàm/model lõi đã
định nghĩa): truy cập server qua `S.<tên>` để patch `server.<tên>` trong test vẫn có tác dụng."""
from __future__ import annotations

from state import job_state as js  # V6 F1: mọi ghi manifest qua state
import os
import json
import time
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from core.manifest import Manifest
import core.batch_config as batch_config  # noqa: F401 — giữ tên cho code/test cũ (V6 F2: dùng job_opts)
import core.job_opts as job_opts

from fastapi import APIRouter, Request

import server as S

router = APIRouter()

__all__ = [
    "AgentAvSyncHealReq",
    "AgentAvSyncScanReq",
    "AgentHealBatchReq",
    "AgentQaGateReq",
    "_AV_SYNC_HOOKS",
    "_HEAL_HOOKS",
    "_agent_asr_force",
    "_agent_asr_set_engine",
    "_agent_assemble_fit",
    "_agent_core",
    "_agent_edit_line",
    "_agent_get_hold",
    "_agent_get_job",
    "_agent_heal_stale",
    "_agent_mux_force_av",
    "_agent_mux_retry",
    "_agent_mux_run",
    "_agent_notify",
    "_agent_queue_add",
    "_agent_reset_state",
    "_agent_resume",
    "_agent_run_stage",
    "_agent_script_review",
    "_agent_script_review_legacy",
    "_agent_set_hold",
    "_agent_stop_job",
    "_agent_translate_fill",
    "_agent_translate_rerun",
    "_agent_tts_run_simple",
    "_agent_tts_set_voice",
    "_job_in_pipeline",
    "api_agent_av_sync",
    "api_agent_av_sync_heal",
    "api_agent_av_sync_heal_batch",
    "api_agent_av_sync_scan",
    "api_agent_chat",
    "api_agent_confirm",
    "api_agent_heal_failed",
    "api_agent_pending",
    "api_agent_qa_gate",
    "api_agent_reset_state",
    "api_agent_resume",
    "api_agent_review",
    "api_agent_review_diff",
    "api_agent_scan_failed",
    "api_agent_status",
    "api_agent_watch_once",
    "router",
]


# ===== Pineline V2 — AI Agent nhúng (agent_core) =====
import agents.agent_core as _agent_core


def _agent_get_job(job_id: str) -> dict:
    work_dir = S.WORK_ROOT / job_id
    if not (work_dir / "manifest.json").exists():
        raise FileNotFoundError(f"job {job_id} không tồn tại")
    return S.read_job(work_dir)


def _agent_queue_add(job_ids: list[str], opts: dict) -> dict:
    opts = dict(opts or {})
    skip_done = bool(opts.pop("skip_done", True))
    return S.job_queue.queue_add(job_ids, opts=opts, skip_done=skip_done)


def _agent_edit_line(job_id: str, i: int, vi: str | None, zh: str | None):
    res = S.edit_script_line(job_id, S.ScriptLineEdit(i=i, vi=vi, zh=zh))
    if isinstance(res, JSONResponse):
        try:
            body = json.loads(res.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {"error": "edit failed", "status_code": res.status_code}
        return body
    return res


def _agent_set_hold(job_id: str, enabled: bool) -> dict:
    work_dir = S.WORK_ROOT / job_id
    if not (work_dir / "manifest.json").exists():
        return {"ok": False, "error": "job không tồn tại"}
    if enabled:
        # port P6 11-12 (V2 agentH B): brain/auto không đặt lại HOLD cho job người dùng đã gỡ tay
        # (agent.hold_override) — trừ khi QA có high mới sau lần chạy tay đó.
        try:
            from agents import agent_hold
            if agent_hold.auto_caller() and agent_hold.hold_suppressed(work_dir):
                return {"ok": False, "job_id": job_id, "hold_after_translate": False,
                        "error": "job đã được người dùng gỡ HOLD (chạy tay) và không có high QA mới "
                                 "— không đặt lại HOLD"}
        except Exception:  # noqa: BLE001
            pass
    m = Manifest(work_dir)
    agent = dict(m.data.get("agent") or {})
    agent["hold_after_translate"] = bool(enabled)
    if enabled:
        agent.pop("held_at", None)
        agent["hold_reason"] = "sẽ dừng sau translate để duyệt script"
    else:
        if agent.get("held_at") == "after_translate":
            agent.pop("held_at", None)
        agent.pop("hold_reason", None)
    m.data["agent"] = agent
    tr = (m.data.get("stages") or {}).get("translate") or {}
    if tr.get("status") == "done" and (tr.get("cur_text") or "").startswith("HOLD"):
        tr = dict(tr)
        tr["cur_text"] = "đủ bản dịch" if not enabled else tr.get("cur_text")
        if not enabled:
            tr["cur_text"] = "đủ bản dịch"
        m.data.setdefault("stages", {})["translate"] = tr
    js.persist(m)
    S._job_cache.pop(str(work_dir), None)
    return {"ok": True, "job_id": job_id, "hold_after_translate": bool(enabled), "agent": agent}


def _agent_get_hold(job_id: str) -> dict:
    work_dir = S.WORK_ROOT / job_id
    if not (work_dir / "manifest.json").exists():
        return {}
    m = Manifest(work_dir)
    return dict(m.data.get("agent") or {})


def _agent_run_stage(job_id: str, stage: str, opts: dict) -> dict:
    """Chạy 1 stage qua API nội bộ (vd tts sau duyệt)."""
    opts = dict(opts or {})
    voice = opts.get("voice")
    force = bool(opts.get("force"))
    engine = opts.get("engine")
    speed = opts.get("speed")
    # port P6 (V2 agentI2): lệnh NGƯỜI (Telegram/UI chat Agent, user_context) → KHÔNG gỡ HOLD trước ở đây;
    # S.run_stage gỡ sau khi claim thành công qua _manual_hold_override (hold_override + 1 tin «🔓»).
    try:
        from agents import agent_hold
        manual = bool(agent_hold.manual_source(None))
    except Exception:  # noqa: BLE001
        manual = False
    h: dict = {}
    # Nếu đang hold after translate và sắp chạy tts → clear hold trước
    if stage == "tts":
        h = _agent_get_hold(job_id)
        if not manual:
            if h.get("hold_after_translate") or h.get("held_at") == "after_translate":
                _agent_set_hold(job_id, False)
            # QA override khi user/agent chủ động chạy TTS
            if h.get("qa_blocked") or h.get("held_at") == "qa_gate":
                from agents.agent_qa_gate import set_qa_gate
                set_qa_gate(S.WORK_ROOT / job_id, True, override_pass=True)
    res = S.run_stage(job_id, stage, force=force, voice=voice, speed=speed, engine=engine)
    if manual and stage == "tts" and not isinstance(res, JSONResponse) \
            and (h.get("qa_blocked") or h.get("held_at") == "qa_gate"):
        _agent_mark_qa_override(job_id)
    if isinstance(res, JSONResponse):
        try:
            return json.loads(res.body.decode("utf-8"))
        except Exception:
            return {"error": f"run_stage {stage} failed", "status_code": res.status_code}
    return res if isinstance(res, dict) else {"ok": True, "res": res}


def _agent_mark_qa_override(job_id: str) -> None:
    """agentI2: người chạy TTS qua Agent trên job QA-block → giữ ngữ nghĩa cũ agent.qa_override=True
    (qua Manifest.update, sau khi run_stage đã claim + gỡ HOLD)."""
    def _fn(dd: dict):
        a = dict(dd.get("agent") or {})
        if a.get("qa_override") and a.get("qa_gate"):
            return False
        a["qa_override"] = True
        a["qa_gate"] = True
        dd["agent"] = a
        return None
    try:
        js.try_mutate(S.WORK_ROOT / job_id, _fn)   # V6: state/job_state
        S._job_cache.pop(str(S.WORK_ROOT / job_id), None)
    except Exception as e:  # noqa: BLE001
        print(f"[hold] qa_override {job_id} lỗi: {e}", flush=True)


def _agent_stop_job(job_id: str):
    res = S.stop_job(job_id)
    if isinstance(res, JSONResponse):
        try:
            return json.loads(res.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"error": "stop failed", "status_code": res.status_code}
    return res if res is not None else {"ok": True, "job_id": job_id}


def _agent_tts_set_voice(job_id: str, opts: dict):
    res = S.set_tts_voice(job_id, S.TTSVoiceReq(
        voice=opts.get("voice"),
        speed=opts.get("speed"),
        engine=opts.get("engine"),
    ))
    if isinstance(res, JSONResponse):
        try:
            return json.loads(res.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"error": "tts_set_voice failed"}
    return res


def _agent_mux_run(job_id: str, opts: dict):
    """Render lại theo đúng opts job đã chạy (queue_opts) ⊕ config batch, chỉ đè key agent truyền."""
    opts = dict(opts or {})
    # V6 F2: agent > queue_opts ghim > config batch — core.job_opts (một nguồn với UI / hàng chờ)
    o = job_opts.for_job(S.WORK_ROOT / job_id, opts)
    if opts.get("brand") is False:
        o["brand"], o["brand_mode"] = False, "off"
    elif (opts.get("brand") or opts.get("brand_text")) and str(o.get("brand_mode") or "off") == "off":
        o["brand"], o["brand_mode"] = True, "drift"
    req = S.MuxReq(**job_opts.render_kwargs(o))
    res = S.mux_job(job_id, req)
    if isinstance(res, JSONResponse):
        try:
            return json.loads(res.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"error": "mux failed"}
    return res


def _agent_asr_set_engine(job_id: str, engine: str):
    res = S.set_asr_engine(job_id, S.AsrEngineReq(engine=engine))
    if isinstance(res, JSONResponse):
        try:
            return json.loads(res.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"error": "asr_set_engine failed"}
    return res


def _job_in_pipeline(job_id: str) -> bool:
    with S._STAGE_BUSY_LOCK:
        if any(j == job_id for j, _ in S._STAGE_BUSY):
            return True
    return S.job_queue.queue_state().get("current") == job_id


def _agent_script_review(job_id: str, review_model: str | None):
    """Rà soát tên nhân vật + văn phong trước lồng tiếng.

    - Job đang chạy pipeline và chưa lồng tiếng → ghi yêu cầu; pipeline tự rà ngay khi dịch xong
      100% (trước QA gate / TTS). Không xếp vào hàng GPU (sẽ bị đợi tới sau render).
    - Job đang đứng (HOLD / QA block / dịch xong chưa TTS) → rà ngay.
    - Đã lồng tiếng → rà ngay nhưng báo phải lồng tiếng + render lại."""
    work_dir = S.WORK_ROOT / job_id
    if not (work_dir / "manifest.json").exists():
        return {"ok": False, "error": "job không tồn tại"}
    m = Manifest(work_dir)
    tr = m.stage("translate") or {}
    tts = m.stage("tts") or {}
    tts_started = tts.get("status") in ("running", "done") or (work_dir / "tts.json").exists()
    agent = dict(m.data.get("agent") or {})
    rq = {"requested_at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": review_model or None, "names": True}
    if _job_in_pipeline(job_id) and not tts_started:
        agent["review_request"] = rq
        m.data["agent"] = agent
        js.persist(m)
        S._job_cache.pop(str(work_dir), None)
        when = ("ngay khi dịch xong 100%" if tr.get("status") != "done"
                else "ngay sau lượt QA đang chạy")
        return {"ok": True, "deferred": True, "job_id": job_id,
                "message": f"Đã ghi yêu cầu. Job đang chạy (dịch {tr.get('progress') or '?'}%) — {when} sẽ "
                           "rà tên nhân vật theo bảng + rà văn phong toàn bộ, rồi mới lồng tiếng. "
                           "Xong sẽ báo Telegram."}
    if not (work_dir / "script.json").exists():
        return {"ok": False, "error": "chưa có bản dịch để soát"}
    agent["review_request"] = rq
    m.data["agent"] = agent
    js.persist(m)
    target = (m.data.get("request") or {}).get("target", "Tiếng Việt")

    def _flow():
        S._run_requested_review(work_dir, target)
        S._save_series_glossary(work_dir)

    S._gpu.submit(_flow)
    msg = "Đang rà tên nhân vật + văn phong (job đang đứng, chạy ngay)."
    if tts_started:
        msg += " ⚠ Job ĐÃ lồng tiếng — rà xong phải lồng tiếng lại (tts_run force) rồi render (mux_run)."
    elif agent.get("held_at"):
        msg += " Job đang HOLD — rà xong gọi resume_job để lồng tiếng + render."
    return {"ok": True, "deferred": False, "job_id": job_id, "message": msg}


def _agent_resume(job_id: str) -> dict:
    """Duyệt xong → bỏ HOLD/QA block và xếp lại job lên ĐẦU hàng chờ: pipeline bỏ qua bước đã xong,
    chạy tiếp lồng tiếng + render."""
    work_dir = S.WORK_ROOT / job_id
    if not (work_dir / "manifest.json").exists():
        return {"ok": False, "error": "job không tồn tại"}
    if _job_in_pipeline(job_id):
        return {"ok": False, "error": "job đang chạy rồi — không cần chạy tiếp"}
    m = Manifest(work_dir)
    agent = dict(m.data.get("agent") or {})
    agent["hold_after_translate"] = False
    # fix 2026-11-13 agentN: «Chạy tiếp» = người đã duyệt → gỡ cả dấu park + bộ đếm lượt thử lại
    # (trước: review_retries còn > max → lần park kế tiếp nhảy thẳng review_failed, không tự thử lại).
    for k in ("held_at", "hold_reason", "review_retries", "review_retry_at", "simple_park", "split_wait_parts"):
        agent.pop(k, None)
    if agent.get("qa_blocked"):
        agent["qa_override"] = True
        agent["qa_blocked"] = False
    m.data["agent"] = agent
    js.persist(m)
    S._job_cache.pop(str(work_dir), None)
    try:
        qo = m.data.get("queue_opts") or {}
        opts = job_opts.resolve(None, qo, fill_voice=not qo)   # V6 F2: ghim > batch (core.job_opts)
    except Exception:  # noqa: BLE001
        opts = dict(m.data.get("queue_opts") or S.job_queue.queue_state().get("opts") or {})
    opts.pop("pin_opts", None)
    r = S.job_queue.queue_add([job_id], opts=opts, skip_done=False)
    S.job_queue.queue_front(job_id)
    return {"ok": True, "job_id": job_id, "queued": r.get("queued"), "queue": S.job_queue.queue_state()["queue"],
            "message": "Đã bỏ chặn và xếp lên đầu hàng chờ — bỏ qua bước đã xong, chạy tiếp lồng tiếng + render."}


@router.post("/api/agent/review/{job_id}")
def api_agent_review(job_id: str, body: dict | None = None):
    return _agent_script_review(job_id, (body or {}).get("review_model"))


@router.post("/api/agent/resume/{job_id}")
def api_agent_resume(job_id: str):
    return _agent_resume(job_id)


def _agent_script_review_legacy(job_id: str, review_model: str | None):
    """Rà soát văn phong = translate API review_only (run_review)."""
    res = S.retranslate_job(
        job_id,
        S.TranslateReq(review_only=True, review_model=review_model or None),
    )
    if isinstance(res, JSONResponse):
        try:
            return json.loads(res.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"error": "script_review failed"}
    return res


def _agent_heal_stale(job_id: str):
    work_dir = S.WORK_ROOT / job_id
    if not (work_dir / "manifest.json").exists():
        return {"error": "not found"}
    healed = S._heal_stale_running(work_dir)
    S._job_cache.pop(str(work_dir), None)
    return {"ok": True, "healed_stages": healed}


def _agent_translate_fill(job_id: str):
    res = S.translate_fill(job_id)
    if isinstance(res, JSONResponse):
        try:
            return json.loads(res.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"error": "translate_fill failed"}
    return res


def _agent_translate_rerun(job_id: str):
    res = S.retranslate_job(job_id, S.TranslateReq(review_only=False, mode="batch"))
    if isinstance(res, JSONResponse):
        try:
            return json.loads(res.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"error": "translate_rerun failed"}
    return res


def _agent_tts_run_simple(job_id: str):
    return _agent_run_stage(job_id, "tts", {"force": False})


def _agent_mux_retry(job_id: str):
    return _agent_mux_run(job_id, {"audio_mode": "duck_bgm", "vbitrate": "4M"})


def _agent_asr_force(job_id: str):
    return _agent_run_stage(job_id, "asr", {"force": True})


def _agent_assemble_fit(job_id: str, max_speed: float = 1.3):
    from engines.assemble import assemble_fit
    work_dir = S.WORK_ROOT / job_id
    if not (work_dir / "tts.json").exists():
        return {"ok": False, "error": "chưa có tts.json"}
    return assemble_fit(work_dir, max_speed=float(max_speed or 1.3))


def _agent_mux_force_av(job_id: str, audio_mode: str | None = None):
    mode = audio_mode or "duck_bgm"
    return _agent_mux_run(job_id, {
        "audio_mode": mode,
        "orig_vol": 0.28,
        "vbitrate": "4M",
        "fit": True,
    })


def _agent_reset_state(job_id: str, stages: list[str] | str | None = None, keep_asr: bool = True, restart: bool = False):
    from agents.agent_heal import reset_job_state
    return reset_job_state(S.WORK_ROOT, job_id, stages=stages, keep_asr=keep_asr, restart=restart, hooks=_HEAL_HOOKS)


_HEAL_HOOKS = {
    "heal_stale": _agent_heal_stale,
    "translate_fill": _agent_translate_fill,
    "script_review": _agent_script_review,
    "tts_run": _agent_tts_run_simple,
    "mux_retry": _agent_mux_retry,
    "asr_force": _agent_asr_force,
    "translate_rerun": _agent_translate_rerun,
    "stop_job": _agent_stop_job,
    "run_stage": lambda jid, st: _agent_run_stage(jid, st, {}),
    "reset_state": _agent_reset_state,
    # port P6 11-12 (V2 agentH C): heal bỏ qua job đang chạy thật (engine sống / claim stage)
    "job_active": lambda jid: ("engine" if S._job_proc_alive(jid) else None) or S._job_lineage_busy(jid),
}

_AV_SYNC_HOOKS = {
    "assemble_fit": _agent_assemble_fit,
    "mux_force": _agent_mux_force_av,
}


def _as_auto(fn, who: str = "heal"):
    """port P6 (V2 agentI2): hook heal/av-sync là logic TỰ ĐỘNG → chạy trong agent_hold.auto_context,
    không bị tính «chạy tay» (không gỡ HOLD) kể cả khi người gọi heal qua chat Agent/Telegram."""
    def _w(*a, **k):
        from agents import agent_hold
        with agent_hold.auto_context(who):
            return fn(*a, **k)
    _w.__name__ = getattr(fn, "__name__", "hook")
    _w.__wrapped__ = fn
    return _w


_HEAL_HOOKS = {k: _as_auto(v, "heal") for k, v in _HEAL_HOOKS.items()}
_AV_SYNC_HOOKS = {k: _as_auto(v, "av_sync") for k, v in _AV_SYNC_HOOKS.items()}


def _agent_user_ctx(request, route: str):
    """agentI2: route Agent nhận lệnh NGƯỜI (UI/WebView, hoặc header X-Pineline-Caller: user từ Telegram
    agent_tele_cmd) → tool in-process chạy trong agent_hold.user_context → run_stage/tts/mux/translate
    tính là chạy tay (gỡ HOLD + 1 tin). Brain gửi header `brain` / gọi run_agent in-process → không."""
    import contextlib
    try:
        from agents import agent_hold
        if request is not None and agent_hold.is_manual_request(request):
            return agent_hold.user_context(f"{route}:{agent_hold.request_source(request)}")
    except Exception:  # noqa: BLE001
        pass
    return contextlib.nullcontext()


def _agent_notify(text: str) -> None:
    from agents.agent_watch import telegram_send
    if not telegram_send(text, force=True):
        print(f"[agent] {text}", flush=True)


_agent_core.bind(_agent_core.Runtime(
    work_root=S.WORK_ROOT,
    list_jobs=S.list_jobs,
    get_job=_agent_get_job,
    queue_state=S.job_queue.queue_state,
    queue_add=_agent_queue_add,
    queue_stop=S.job_queue.queue_stop,
    list_voices=S.list_voices,
    draft_yt=lambda jid: _agent_core.default_yt_draft(S.WORK_ROOT, jid),
    edit_script_line=_agent_edit_line,
    set_hold_after_translate=_agent_set_hold,
    get_hold=_agent_get_hold,
    run_stage=_agent_run_stage,
    stop_job=_agent_stop_job,
    tts_set_voice=_agent_tts_set_voice,
    mux_run=_agent_mux_run,
    asr_set_engine=_agent_asr_set_engine,
    script_review=_agent_script_review,
    resume_job=_agent_resume,
    heal_hooks=_HEAL_HOOKS,
    av_sync_hooks=_AV_SYNC_HOOKS,
    notify=_agent_notify,
))


@router.get("/api/agent/status")
def api_agent_status():
    """Snapshot jobs cho panel Agent (không cần LLM)."""
    try:
        return {"ok": True, "snapshot": _agent_core.status_snapshot()}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/api/agent/chat")
def api_agent_chat(req: S.AgentChatReq, request: Request = None):
    """Chat với AI Agent — tools gọi hàm nội bộ (queue/manifest), không HTTP self."""
    msg = (req.message or "").strip()
    if not msg:
        return JSONResponse({"error": "message rỗng"}, status_code=400)
    try:
        with _agent_user_ctx(request, "agent_chat"):
            result = _agent_core.run_agent(
                msg,
                history=req.history,
                allow_mutate=bool(req.allow_mutate),
                max_steps=max(1, min(int(req.max_steps or 8), 16)),
                require_confirm=bool(req.require_confirm),
                force_first_tool=(getattr(req, "force_first_tool", None) or None),
            )
        return {
            "ok": True,
            "reply": result.reply,
            "steps": result.steps,
            "model": result.model,
            "allow_mutate": bool(req.allow_mutate),
            "confirms": result.confirms,
        }
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/api/agent/pending")
def api_agent_pending():
    return {"ok": True, "pending": _agent_core.pending_actions()}


@router.post("/api/agent/confirm")
def api_agent_confirm(req: S.AgentConfirmReq, request: Request = None):
    """Chạy (approve) hoặc huỷ thao tác agent đã đỗ lại chờ xác nhận."""
    try:
        with _agent_user_ctx(request, "agent_confirm"):
            res = _agent_core.confirm_action(req.id, bool(req.approve))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return res if res.get("ok") else JSONResponse(res, status_code=404)


class AgentHealBatchReq(BaseModel):
    apply: bool = False
    level: str = "safe"  # safe | aggressive
    max_jobs: int = 20


class AgentQaGateReq(BaseModel):
    enabled: bool = True
    override_pass: bool | None = None


@router.get("/api/agent/scan-failed")
def api_agent_scan_failed():
    """Quét mọi job lỗi/kẹt (không sửa)."""
    from agents.agent_heal import scan_problem_jobs
    try:
        return {"ok": True, **scan_problem_jobs(S.WORK_ROOT, list_jobs=S.list_jobs)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/api/agent/heal-failed")
def api_agent_heal_failed(req: AgentHealBatchReq):
    """Heal hàng loạt job lỗi. apply=false chỉ plan."""
    from agents.agent_heal import heal_failed_batch
    try:
        if bool(req.apply) and S.job_queue.queue_busy():
            return {
                "ok": True,
                "applied": False,
                "skipped": "queue_busy",
                "hint": "Hàng chờ job đang chạy — đợi queue xong rồi heal lại",
                "queue": S.job_queue.queue_state(),
            }
        return heal_failed_batch(
            S.WORK_ROOT,
            apply=bool(req.apply),
            level=str(req.level or "safe"),
            max_jobs=int(req.max_jobs or 20),
            list_jobs=S.list_jobs,
            hooks=_HEAL_HOOKS,
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/api/agent/jobs/{job_id}/review-diff")
def api_agent_review_diff(job_id: str):
    from agents.agent_qa_gate import review_diff
    try:
        return review_diff(S.WORK_ROOT / job_id, job_id)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/api/agent/jobs/{job_id}/qa-gate")
def api_agent_qa_gate(job_id: str, req: AgentQaGateReq):
    from agents.agent_qa_gate import set_qa_gate
    try:
        return set_qa_gate(
            S.WORK_ROOT / job_id,
            bool(req.enabled),
            override_pass=req.override_pass,
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


class AgentResetStateReq(BaseModel):
    stages: list[str] | str | None = None
    keep_asr: bool = True
    restart: bool = False


@router.post("/api/agent/jobs/{job_id}/reset-state")
def api_agent_reset_state(job_id: str, req: AgentResetStateReq | None = None):
    """Reset sạch trạng thái các stage bị kẹt/lỗi của job (xóa artifact dở, gỡ .stop, đưa manifest về pending)."""
    try:
        r = req or AgentResetStateReq()
        return _agent_reset_state(job_id, stages=r.stages, keep_asr=r.keep_asr, restart=r.restart)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/api/agent/watch-once")
def api_agent_watch_once():
    """Chạy 1 vòng watcher ngay (scan→heal safe nếu GPU rảnh)."""
    from agents.agent_watch import watch_once
    try:
        return watch_once(
            S.WORK_ROOT,
            list_jobs=S.list_jobs,
            hooks=_HEAL_HOOKS,
            max_jobs=int(os.getenv("AGENT_WATCH_MAX_JOBS") or "10"),
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


class AgentAvSyncHealReq(BaseModel):
    apply: bool = False


@router.get("/api/agent/jobs/{job_id}/av-sync")
def api_agent_av_sync(job_id: str):
    from agents.agent_av_sync import diagnose_av_sync
    try:
        return diagnose_av_sync(S.WORK_ROOT, job_id)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/api/agent/jobs/{job_id}/av-sync-heal")
def api_agent_av_sync_heal(job_id: str, req: AgentAvSyncHealReq):
    from agents.agent_av_sync import heal_av_sync
    try:
        return heal_av_sync(
            S.WORK_ROOT, job_id, apply=bool(req.apply), hooks=_AV_SYNC_HOOKS,
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


class AgentAvSyncScanReq(BaseModel):
    job_ids: list[str] | None = None
    group_id: str | None = None
    deep: bool = False
    apply: bool = False


@router.post("/api/agent/av-sync/scan")
def api_agent_av_sync_scan(req: AgentAvSyncScanReq):
    from agents.agent_av_sync import scan_av_sync
    try:
        return scan_av_sync(
            S.WORK_ROOT, req.job_ids, group_id=req.group_id, deep=bool(req.deep),
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/api/agent/av-sync/heal-batch")
def api_agent_av_sync_heal_batch(req: AgentAvSyncScanReq):
    from agents.agent_av_sync import heal_av_sync_batch
    try:
        return heal_av_sync_batch(
            S.WORK_ROOT,
            req.job_ids,
            group_id=req.group_id,
            apply=bool(req.apply),
            hooks=_AV_SYNC_HOOKS,
            deep=bool(req.deep),
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
