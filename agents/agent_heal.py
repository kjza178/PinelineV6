"""
agent_heal.py — Chẩn đoán + heal job lỗi (an toàn theo cấp).

safe: heal stale running, vá dịch trống, TTS thiếu clip, mux lại nếu có audio.
aggressive: thêm asr/tts force (chỉ khi user/agent allow + level=aggressive).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable


WIRED = ("download", "asr", "translate", "tts", "mux")


# ---------------------------------------------------------------------------
# fix 2026-11-12 agentH (C): race heal lúc khởi động
# ---------------------------------------------------------------------------
def start_delay_sec() -> int:
    """Hoãn lượt heal đầu tiên của watcher/brain sau khi start (AGENT_HEAL_START_DELAY_SEC, mặc định 180)."""
    raw = ""
    try:
        from core.config import get as _get
        v = _get("AGENT_HEAL_START_DELAY_SEC")
        raw = "" if v is None else str(v).strip()
    except Exception:  # noqa: BLE001
        raw = ""
    if not raw:
        import os
        raw = (os.getenv("AGENT_HEAL_START_DELAY_SEC") or "180").strip()
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return 180


def job_active(job_id: str, hooks: dict | None = None) -> str | None:
    """Lý do job đang chạy thật (không được heal): queue.current / claim stage / engine sống.
    None = không thấy hoạt động. Không import server (tránh vòng) — dùng module đã nạp."""
    import sys
    jid = str(job_id or "")
    if not jid:
        return None
    fn = (hooks or {}).get("job_active")
    if callable(fn):
        try:
            r = fn(jid)
            if r:
                return str(r)
        except Exception:  # noqa: BLE001
            pass
    jq = sys.modules.get("core.job_queue") or sys.modules.get("job_queue")   # V6: core.job_queue
    if jq is not None:
        try:
            cur = getattr(jq, "_current", None)
            if cur and (cur == jid or jid.startswith(str(cur) + "_p")):
                return "queue.current"
        except Exception:  # noqa: BLE001
            pass
    srv = sys.modules.get("server")
    if srv is not None:
        for name, why in (("_job_proc_alive", "engine"), ("_job_lineage_busy", "stage")):
            f = getattr(srv, name, None)
            if callable(f):
                try:
                    r = f(jid)
                    if r:
                        return why if r is True else f"{why}:{r}"
                except Exception:  # noqa: BLE001
                    pass
    return None


def _mf(work: Path) -> dict:
    p = work / "manifest.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _stage(data: dict, name: str) -> dict:
    return dict((data.get("stages") or {}).get(name) or {})


def _count_missing_vi(work: Path) -> int:
    sp = work / "script.json"
    if not sp.is_file():
        return -1
    try:
        rows = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return -1
    if not isinstance(rows, list):
        return -1
    return sum(1 for r in rows if isinstance(r, dict) and not (r.get("vi") or "").strip())


def _tts_missing(work: Path, st: dict) -> int:
    if st.get("n_missing") is not None:
        try:
            return int(st["n_missing"])
        except Exception:  # noqa: BLE001
            pass
    tp = work / "tts.json"
    if not tp.is_file():
        return -1 if st.get("status") == "done" else 0
    try:
        rows = json.loads(tp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return -1
    if not isinstance(rows, list):
        return -1
    return sum(1 for r in rows if isinstance(r, dict) and not (r.get("audio") or r.get("path")))


def _finished(work: Path, data: dict) -> bool:
    try:
        from agents.agent_hold import job_finished
        return bool(job_finished(work, data))
    except Exception:  # noqa: BLE001
        return False


def diagnose_job(work_root: Path, job_id: str) -> dict:
    work = work_root / job_id
    if not (work / "manifest.json").is_file():
        return {"ok": False, "error": f"job {job_id} không tồn tại"}

    data = _mf(work)
    # fix 2026-11-13 agentN: job ĐÃ XONG (mux+output / đã đăng / parent đã ghép Full / part của parent đó)
    # → không chẩn đoán lỗi. Trước: job xong đã dọn script/tts/asr.json bị bắt «asr_bad» + «tts_not_started»
    # → heal tts_run → «chưa dịch xong (thiếu script.json)» → Telegram «heal thất bại» (2493d3/ae9956/ec10e4).
    if _finished(work, data):
        return {
            "ok": True,
            "job_id": job_id,
            "finished": True,
            "failed_stages": [],
            "running_stages": [],
            "issues": [{"code": "finished", "stage": "job",
                        "detail": "job đã xong (có output / đã đăng / đã ghép Full) — không heal"}],
            "classifications": [],
            "class_notes": [],
            "actions_safe": [],
            "actions_aggressive": [],
            "note": "job đã xong — heal/watch/brain bỏ qua.",
        }
    stages = {s: _stage(data, s) for s in WIRED}
    issues: list[dict] = []
    actions_safe: list[dict] = []
    actions_aggressive: list[dict] = []

    # Stale running
    for s, st in stages.items():
        if st.get("status") == "running":
            issues.append({
                "code": "stale_or_running",
                "stage": s,
                "detail": st.get("cur_text") or st.get("error") or "status=running",
            })
            actions_safe.append({
                "action": "heal_stale_running",
                "why": f"stage {s} đang running — heal nếu mồ côi / hoặc stop rồi chạy lại",
            })

    # Failed stages & .stop flag
    if (work / ".stop").is_file():
        issues.append({
            "code": "stop_flag_set",
            "stage": "job",
            "detail": "cờ .stop đang bật — job bị chặn không thể chạy tiếp",
        })
        actions_safe.append({
            "action": "reset_state",
            "why": "gỡ cờ .stop và reset trạng thái dở dang để sẵn sàng chạy lại",
        })

    for s, st in stages.items():
        if st.get("status") == "failed":
            issues.append({
                "code": "stage_failed",
                "stage": s,
                "detail": (st.get("error") or st.get("cur_text") or "")[:300],
            })
            if s in ("translate", "tts", "mux"):
                actions_safe.append({
                    "action": "reset_state",
                    "why": f"dọn dẹp artifact dở của stage {s} và reset về pending để chạy lại",
                })

    # Download / source
    has_source = any((work / f"source{ext}").is_file() for ext in (".mp4", ".mkv", ".webm", ".mov"))
    vp = stages["download"].get("video_path")
    if vp and Path(vp).is_file():
        has_source = True
        want = stages["download"].get("video_size")
        try:
            now = Path(vp).stat().st_size
        except OSError:
            now = want
        if want and now != want:
            issues.append({"code": "source_changed", "stage": "download",
                           "detail": f"video nguồn đổi sau khi import ({want} → {now} byte): {vp}"})
    if stages["download"].get("status") == "failed" or (
        stages["asr"].get("status") not in (None, "pending", "") and not has_source
        and stages["download"].get("status") != "done"
    ):
        if not has_source:
            issues.append({"code": "no_source", "stage": "download", "detail": "thiếu source video"})
            actions_aggressive.append({"action": "download_or_import", "why": "cần tải/import lại — không auto"})

    # ASR
    asr_ok = (work / "asr.json").is_file()
    # fix 2026-11-13 agentN: asr done + đã dịch xong mà asr.json đã dọn ≠ ASR hỏng
    tr_s = stages["translate"].get("status")
    if stages["asr"].get("status") == "failed" or (not asr_ok and (
        tr_s in ("failed", "running") or (tr_s == "done" and stages["asr"].get("status") != "done")
    )):
        issues.append({"code": "asr_bad", "stage": "asr", "detail": "asr.json thiếu hoặc ASR failed"})
        actions_aggressive.append({"action": "asr_run_force", "why": "nhận diện lại — nặng GPU"})

    # Translate gaps
    n_miss = _count_missing_vi(work)
    if (work / "script.json").is_file() and n_miss > 0:
        issues.append({"code": "translate_missing", "stage": "translate", "detail": f"{n_miss} câu thiếu vi"})
        actions_safe.append({"action": "translate_fill", "why": f"vá {n_miss} câu trống"})
    if stages["translate"].get("status") == "failed":
        actions_aggressive.append({"action": "translate_rerun", "why": "dịch lại toàn bộ"})
        actions_safe.append({"action": "script_review", "why": "thử rà soát nếu đã có nháp"})

    # Quality hint (not hard fail)
    if (work / "script.json").is_file() and n_miss == 0 and stages["translate"].get("status") == "done":
        actions_safe.append({
            "action": "script_review_optional",
            "why": "không lỗi cứng — nên rà văn phong trước TTS nếu chưa rv",
        })

    # TTS
    tts_st = stages["tts"]
    tts_miss = _tts_missing(work, tts_st)
    has_tts = (work / "tts.json").is_file()
    has_dub = has_tts or bool(data.get("dub"))
    if tts_st.get("status") == "failed":
        actions_safe.append({"action": "tts_run", "why": "TTS failed — chạy lại (không force full trừ khi cần)"})
    elif tts_miss and tts_miss > 0 and stages["translate"].get("status") == "done":
        # agentN: n_missing cũ của TTS khi dịch CHƯA xong (job đang chạy lại ASR/dịch) ≠ thiếu clip
        issues.append({"code": "tts_missing_clips", "stage": "tts", "detail": f"{tts_miss} câu thiếu audio"})
        actions_safe.append({"action": "tts_run", "why": "đọc bù câu thiếu"})
    elif (stages["translate"].get("status") == "done" and not has_tts
          and tts_st.get("status") not in ("running", "done")):   # agentN: tts done (tts.json dọn) ≠ chưa TTS
        issues.append({"code": "tts_not_started", "stage": "tts", "detail": "đã dịch nhưng chưa TTS"})
        actions_safe.append({"action": "tts_run", "why": "chạy TTS"})

    # Mux
    out = work / "output.mp4"
    partial = work / "output.partial.mp4"
    mux_st = stages["mux"]
    if mux_st.get("status") == "failed" or (partial.is_file() and not (out.is_file() and out.stat().st_size > 0)):
        issues.append({
            "code": "mux_failed_or_partial",
            "stage": "mux",
            "detail": mux_st.get("error") or mux_st.get("cur_text") or "partial/no output",
        })
        if has_dub:
            actions_safe.append({"action": "mux_retry", "why": "xóa partial (nếu có) + mux lại"})
        else:
            actions_safe.append({"action": "tts_then_mux", "why": "chưa có dub — TTS trước"})

    if mux_st.get("status") == "done" and out.is_file() and out.stat().st_size > 0 and not issues:
        issues.append({"code": "ok_output", "stage": "mux", "detail": "đã có output.mp4 — không cần heal lỗi kỹ thuật"})

    # Dedup actions by action name
    def _uniq(items: list[dict]) -> list[dict]:
        seen = set()
        out_l = []
        for a in items:
            k = a["action"]
            if k in seen:
                continue
            seen.add(k)
            out_l.append(a)
        return out_l

    failed = [s for s, st in stages.items() if st.get("status") == "failed"]
    running = [s for s, st in stages.items() if st.get("status") == "running"]

    # C: phân loại lỗi theo log / cur_text
    classifications: list[dict] = []
    class_notes: list[str] = []
    try:
        from agents.agent_error_class import classify_stage_status, filter_actions_by_class
        for s in failed:
            c = classify_stage_status(s, stages[s])
            if c:
                classifications.append(c)
                issues.append({
                    "code": f"class:{c.get('class')}",
                    "stage": s,
                    "detail": c.get("hint") or "",
                })
        actions_safe, class_notes = filter_actions_by_class(actions_safe, classifications)
    except Exception as e:  # noqa: BLE001
        class_notes = [f"classifier lỗi: {e}"]

    return {
        "ok": True,
        "job_id": job_id,
        "failed_stages": failed,
        "running_stages": running,
        "issues": issues,
        "classifications": classifications,
        "class_notes": class_notes,
        "actions_safe": _uniq(actions_safe),
        "actions_aggressive": _uniq(actions_aggressive),
        "note": (
            "safe = có thể auto khi allow_mutate. "
            "aggressive = force ASR/dịch lại — cần level=aggressive. "
            "class notify_only/none → không spam heal."
        ),
    }


def heal_job(
    work_root: Path,
    job_id: str,
    *,
    apply: bool = False,
    level: str = "safe",
    hooks: dict[str, Callable[..., Any]] | None = None,
) -> dict:
    """apply=False: chỉ trả plan. apply=True: chạy actions theo level."""
    diag = diagnose_job(work_root, job_id)
    if not diag.get("ok"):
        return diag
    if diag.get("finished"):   # fix 2026-11-13 agentN: job đã xong → không plan, không heal
        return {"ok": True, "applied": False, "job_id": job_id, "finished": True, "skipped": "job đã xong",
                "plan": [], "results": [],
                "note": "KHÔNG heal: job đã xong (có output / đã đăng / đã ghép Full) — không có việc gì."}

    plan = list(diag.get("actions_safe") or [])
    if (level or "safe").lower() == "aggressive":
        plan = plan + list(diag.get("actions_aggressive") or [])

    # bỏ optional review khỏi auto trừ khi là action duy nhất chất lượng
    auto_plan = [a for a in plan if a["action"] != "script_review_optional"]
    # C: class bảo notify_only → không apply tts/mux (chỉ stale)
    blocked_classes = {
        c.get("class")
        for c in (diag.get("classifications") or [])
        if c.get("safe_action") in ("notify_only", "none")
    }
    if blocked_classes and apply:
        auto_plan = [a for a in auto_plan if a["action"] == "heal_stale_running"]
    # fix 2026-11-12 agentG: plan rỗng → nói rõ (trước LLM đọc results=[] thành «chưa thực thi»)
    empty_note = ""
    if not auto_plan:
        aggr = [a.get("action") for a in (diag.get("actions_aggressive") or [])]
        empty_note = (
            "KHÔNG có action safe cho job này — heal safe không làm gì (đây không phải lỗi tool). "
            + (f"Chỉ có action aggressive: {', '.join(map(str, aggr))} — cần level=aggressive "
               "hoặc user quyết (ASR/dịch lại nặng)." if aggr else "Không có action nào — báo user.")
        )
    if not apply:
        return {
            "ok": True,
            "applied": False,
            "job_id": job_id,
            "note": empty_note or "plan dưới đây sẽ chạy khi apply=true",
            "plan": auto_plan,
            "skipped_optional": [a for a in plan if a["action"] == "script_review_optional"],
            "blocked_classes": sorted(blocked_classes),
            "class_notes": diag.get("class_notes") or [],
            "diagnose": diag,
        }

    hooks = hooks or {}
    # fix 2026-11-12 agentH (C): job đang chạy thật (queue.current / engine sống) → không heal
    why_active = job_active(job_id, hooks)
    if why_active:
        return {
            "ok": True,
            "applied": False,
            "job_id": job_id,
            "skipped": f"job đang chạy ({why_active})",
            "results": [],
            "note": f"KHÔNG heal: job đang chạy ({why_active}) — tránh giết engine đang chạy",
        }
    results = []
    work = work_root / job_id
    # Action nền (GPU queue) — chỉ 1 cái/lần, tránh 409 chồng
    _ASYNC_STOP = {
        "translate_fill", "script_review", "tts_run", "tts_then_mux",
        "mux_retry", "asr_run_force", "translate_rerun", "reset_state",
    }

    for step in auto_plan:
        act = step["action"]
        try:
            if act == "heal_stale_running":
                fn = hooks.get("heal_stale")
                results.append({"action": act, "result": fn(job_id) if fn else {"skipped": "no hook"}})
            elif act == "translate_fill":
                fn = hooks.get("translate_fill")
                results.append({"action": act, "result": fn(job_id) if fn else {"skipped": "no hook"}})
            elif act == "script_review":
                fn = hooks.get("script_review")
                results.append({"action": act, "result": fn(job_id, None) if fn else {"skipped": "no hook"}})
            elif act in ("tts_run", "tts_then_mux"):
                fn = hooks.get("tts_run")
                r = fn(job_id) if fn else {"skipped": "no hook"}
                results.append({"action": "tts_run", "result": r})
                # không xếp mux ngay sau — đợi vòng watch sau
            elif act == "mux_retry":
                partial = work / "output.partial.mp4"
                if partial.is_file():
                    try:
                        partial.unlink()
                    except OSError as e:
                        results.append({"action": "unlink_partial", "result": {"error": str(e)}})
                fn = hooks.get("mux_retry")
                results.append({"action": act, "result": fn(job_id) if fn else {"skipped": "no hook"}})
            elif act == "asr_run_force":
                fn = hooks.get("asr_force")
                results.append({"action": act, "result": fn(job_id) if fn else {"skipped": "no hook"}})
            elif act == "translate_rerun":
                fn = hooks.get("translate_rerun")
                results.append({"action": act, "result": fn(job_id) if fn else {"skipped": "no hook"}})
            elif act == "download_or_import":
                results.append({"action": act, "result": {"error": "cần user import/tải tay — không auto"}})
            elif act == "reset_state":
                fn = hooks.get("reset_state")
                results.append({"action": act, "result": fn(job_id) if fn else reset_job_state(work_root, job_id, hooks=hooks)})
            else:
                results.append({"action": act, "result": {"skipped": "unknown"}})
        except Exception as e:  # noqa: BLE001
            results.append({"action": act, "result": {"error": str(e)}})
        if act in _ASYNC_STOP:
            break

    return {
        "ok": True,
        "applied": True,
        "job_id": job_id,
        "level": level,
        "results": results,
        "blocked_classes": sorted(blocked_classes),
        "class_notes": diag.get("class_notes") or [],
        "diagnose": diag,
        "next": (empty_note or "poll stage_peek_all sau vài giây — TTS/mux chạy nền"),
        "note": empty_note or f"đã chạy {len(results)} action",
    }


def _job_needs_attention(diag: dict) -> bool:
    if not diag.get("ok") or diag.get("finished"):
        return False
    if diag.get("failed_stages") or diag.get("running_stages"):
        return True
    codes = {i.get("code") for i in (diag.get("issues") or [])}
    bad = {
        "stage_failed", "no_source", "asr_bad", "translate_missing",
        "tts_missing_clips", "tts_not_started", "mux_failed_or_partial",
        "stale_or_running",
    }
    return bool(codes & bad)


def scan_problem_jobs(
    work_root: Path,
    *,
    list_jobs: Callable[[], list[dict]] | None = None,
    include_done_ok: bool = False,
) -> dict:
    """Quét mọi job có manifest — tìm fail/kẹt/thiếu bước."""
    jobs_meta: list[dict] = []
    if list_jobs:
        try:
            jobs_meta = list_jobs() or []
        except Exception:  # noqa: BLE001
            jobs_meta = []

    ids: list[str] = []
    if jobs_meta:
        ids = [str(j.get("job_id")) for j in jobs_meta if j.get("job_id")]
    else:
        if work_root.is_dir():
            for d in sorted(work_root.iterdir()):
                if d.is_dir() and (d / "manifest.json").is_file():
                    ids.append(d.name)

    problems = []
    healthy = []
    for jid in ids:
        if not jid or jid.startswith("_"):
            continue
        diag = diagnose_job(work_root, jid)
        if not diag.get("ok"):
            problems.append({"job_id": jid, "diagnose": diag})
            continue
        if _job_needs_attention(diag):
            problems.append({
                "job_id": jid,
                "failed_stages": diag.get("failed_stages"),
                "running_stages": diag.get("running_stages"),
                "issue_codes": [i.get("code") for i in (diag.get("issues") or [])],
                "actions_safe": diag.get("actions_safe"),
                "actions_aggressive": diag.get("actions_aggressive"),
            })
        elif include_done_ok:
            healthy.append(jid)

    return {
        "ok": True,
        "n_scanned": len(ids),
        "n_problems": len(problems),
        "problems": problems,
        "healthy_sample": healthy[:10] if include_done_ok else [],
    }


def heal_failed_batch(
    work_root: Path,
    *,
    apply: bool = False,
    level: str = "safe",
    max_jobs: int = 20,
    list_jobs: Callable[[], list[dict]] | None = None,
    hooks: dict[str, Callable[..., Any]] | None = None,
) -> dict:
    """Bắt mọi job lỗi/kẹt rồi heal (plan hoặc apply)."""
    scan = scan_problem_jobs(work_root, list_jobs=list_jobs)
    problems = scan.get("problems") or []
    max_jobs = max(1, min(int(max_jobs or 20), 50))
    selected = problems[:max_jobs]
    skipped = problems[max_jobs:]

    if not apply:
        return {
            "ok": True,
            "applied": False,
            "n_problems": scan.get("n_problems"),
            "n_will_touch": len(selected),
            "n_skipped_over_max": len(skipped),
            "batch_plan": [
                {
                    "job_id": p["job_id"],
                    "actions_safe": p.get("actions_safe"),
                    "issue_codes": p.get("issue_codes"),
                }
                for p in selected
            ],
            "hint": "Gọi lại apply=true (+ allow_mutate) để heal safe từng job tuần tự.",
        }

    results = []
    for p in selected:
        jid = p["job_id"]
        results.append(
            heal_job(
                work_root,
                jid,
                apply=True,
                level=level,
                hooks=hooks,
            )
        )

    return {
        "ok": True,
        "applied": True,
        "level": level,
        "n_healed": len(results),
        "n_skipped_over_max": len(skipped),
        "results": results,
        "next": "Đợi GPU — poll status / stage_peek. Không chạy 2 heal batch chồng nhau.",
    }


def reset_job_state(
    work_root: Path,
    job_id: str,
    *,
    stages: list[str] | str | None = None,
    keep_asr: bool = True,
    restart: bool = False,
    hooks: dict[str, Callable[..., Any]] | None = None,
) -> dict:
    """Reset sạch các stage bị kẹt/lỗi của 1 job (xóa artifact dở, gỡ .stop, reset manifest về pending).

    stages: None | "failed" | "stuck" | "all" | list[str] (vd: ["translate", "tts", "mux"]).
            Mặc định nếu None / "failed" / "stuck": tự phát hiện các stage failed/stuck và downstream liên quan.
    keep_asr: mặc định True (giữ download + asr để không phải nhận diện lại).
    restart: nếu True, sau khi reset sẽ kích hoạt chạy lại stage đầu tiên đã reset.
    """
    work = work_root / job_id
    if not (work / "manifest.json").is_file():
        return {"ok": False, "error": f"job {job_id} không tồn tại"}

    hooks = hooks or {}

    # 1. Dừng job nếu đang chạy
    stop_fn = hooks.get("stop_job")
    if stop_fn:
        try:
            stop_fn(job_id)
        except Exception:  # noqa: BLE001
            pass

    # 2. Gỡ cờ .stop
    for stop_p in (work / ".stop", Path(os.getenv("WORK_DIR") or "work") / job_id / ".stop"):
        try:
            if stop_p.exists():
                stop_p.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    data = _mf(work)
    manifest_stages = data.setdefault("stages", {})

    # Xác định các stage cần reset
    target_stages: set[str] = set()
    if isinstance(stages, str):
        s_lower = stages.strip().lower()
        if s_lower in ("all", "*"):
            target_stages = {"translate", "tts", "mux"}
            if not keep_asr:
                target_stages.update({"asr", "download"})
        elif s_lower in ("failed", "stuck", "auto", ""):
            stages = None
        else:
            target_stages = {s.strip().lower() for s in s_lower.split(",") if s.strip()}
    elif isinstance(stages, (list, tuple, set)):
        target_stages = {str(s).strip().lower() for s in stages if str(s).strip()}

    if not target_stages:
        for s in ("mux", "tts", "translate", "asr", "download"):
            st = manifest_stages.get(s) or {}
            if st.get("status") in ("failed", "running"):
                target_stages.add(s)

        # Kiểm tra thêm artifact hỏng/thiếu
        if not (work / "script.json").is_file() and manifest_stages.get("translate", {}).get("status") == "done":
            target_stages.add("translate")

        # Quy tắc phụ thuộc hạ nguồn:
        # Nếu translate bị reset -> tts và mux cũng phải reset
        if "translate" in target_stages:
            target_stages.update({"tts", "mux"})
        # Nếu tts bị reset -> mux cũng phải reset
        elif "tts" in target_stages:
            target_stages.add("mux")

        if not keep_asr and "asr" in target_stages:
            target_stages.update({"translate", "tts", "mux"})
        else:
            target_stages.discard("asr")
            target_stages.discard("download")

        # Nếu không phát hiện stage nào cụ thể nhưng gọi reset -> mặc định translate, tts, mux
        if not target_stages:
            target_stages = {"translate", "tts", "mux"}

    # 3. Dọn dẹp artifact theo stage
    deleted: list[str] = []

    # Real dirs (hỗ trợ cả work_root lẫn WORK_DIR của instance)
    target_dirs = {work}
    d_alt = Path(os.getenv("WORK_DIR") or "work") / job_id
    if d_alt.is_dir():
        target_dirs.add(d_alt)

    for d in target_dirs:
        if "translate" in target_stages:
            for pat in (
                "script.json", "script.display.json", "script.pre_review.json",
                "script.pre_agent_review.json", "sub.vi.srt", "sub.vi.burn.srt",
                "sub.vi.burn.follow.ass", ".display_split_vi", ".fit_mode",
            ):
                f = d / pat
                if f.is_file():
                    try:
                        f.unlink(missing_ok=True)
                        deleted.append(pat)
                    except Exception:  # noqa: BLE001
                        pass
            manifest_stages["translate"] = {"status": "pending"}

        if "tts" in target_stages:
            for pat in ("tts.json", "tts_config.json", "tts_fit.json", "dub_fit.wav"):
                f = d / pat
                if f.is_file():
                    try:
                        f.unlink(missing_ok=True)
                        deleted.append(pat)
                    except Exception:  # noqa: BLE001
                        pass
            tts_dir = d / "tts"
            if tts_dir.is_dir():
                try:
                    import shutil
                    shutil.rmtree(tts_dir, ignore_errors=True)
                    deleted.append("tts/")
                except Exception:  # noqa: BLE001
                    pass
            manifest_stages["tts"] = {"status": "pending"}

        if "mux" in target_stages:
            for pat in ("output.partial.mp4", "_filter_complex.txt"):
                f = d / pat
                if f.is_file():
                    try:
                        f.unlink(missing_ok=True)
                        deleted.append(pat)
                    except Exception:  # noqa: BLE001
                        pass
            manifest_stages["mux"] = {"status": "pending"}

        if "asr" in target_stages and not keep_asr:
            for pat in ("asr.json", "audio.wav", "audio.wav.json", "sub.zh.srt"):
                f = d / pat
                if f.is_file():
                    try:
                        f.unlink(missing_ok=True)
                        deleted.append(pat)
                    except Exception:  # noqa: BLE001
                        pass
            manifest_stages["asr"] = {"status": "pending"}

    # 4. Reset cờ agent
    agent_meta = data.setdefault("agent", {})
    agent_meta["qa_blocked"] = False
    agent_meta.pop("qa_last", None)
    agent_meta.pop("review_request", None)

    # Lưu manifest
    try:
        (work / "manifest.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Lỗi lưu manifest.json: {e}"}

    # 5. Kích hoạt chạy lại (nếu có yêu cầu restart)
    restarted_stage = None
    if restart:
        order = ["download", "asr", "translate", "tts", "mux"]
        earliest = None
        for s in order:
            if s in target_stages:
                earliest = s
                break
        if earliest:
            run_fn = hooks.get("run_stage")
            if run_fn:
                try:
                    run_fn(job_id, earliest)
                    restarted_stage = earliest
                except Exception as e:  # noqa: BLE001
                    restarted_stage = f"error: {e}"

    return {
        "ok": True,
        "job_id": job_id,
        "reset_stages": sorted(target_stages),
        "deleted_files": sorted(set(deleted)),
        "restarted_stage": restarted_stage,
        "hint": f"Đã reset {', '.join(sorted(target_stages))}. " + (
            f"Đang chạy lại {restarted_stage}" if restarted_stage else "Sẵn sàng chạy lại."
        ),
    }

