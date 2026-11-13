"""
Watcher A: định kỳ scan → heal safe + log + Telegram (tuỳ chọn).

Env:
  AGENT_WATCH=1
  AGENT_WATCH_SEC=300
  AGENT_WATCH_MAX_JOBS=10
  AGENT_WATCH_COOLDOWN=1800   giây không heal lại cùng fingerprint job
  AGENT_WATCH_SKIP_PREFIX=agenttest   bỏ job test (csv)
  AGENT_TELEGRAM=1
  AGENT_TELEGRAM_STARTUP=0    không spam "watcher ON" mỗi lần restart
  AGENT_HEAL_START_DELAY_SEC=180  hoãn lượt heal đầu sau khi start (fix 2026-11-12 agentH)
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_last_tg: dict[str, float] = {}  # fingerprint -> unix


def _log_dir(root: Path) -> Path:
    d = root / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_line(root: Path, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with (_log_dir(root) / "agent_watch.log").open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001
        pass
    print(f"[agent_watch] {msg}", flush=True)


def _state_path(root: Path) -> Path:
    return _log_dir(root) / "agent_watch_state.json"


def _load_state(root: Path) -> dict:
    p = _state_path(root)
    if not p.is_file():
        return {"jobs": {}, "last_tg": ""}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"jobs": {}, "last_tg": ""}


def _save_state(root: Path, state: dict) -> None:
    try:
        _state_path(root).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


def _cooldown_sec() -> int:
    try:
        return max(60, int(os.getenv("AGENT_WATCH_COOLDOWN") or "1800"))
    except ValueError:
        return 1800


def _skip_prefixes() -> list[str]:
    raw = (os.getenv("AGENT_WATCH_SKIP_PREFIX") or "agenttest").strip()
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _should_skip_job(job_id: str) -> bool:
    j = (job_id or "").lower()
    return any(j.startswith(p) for p in _skip_prefixes())


def _fingerprint(problem: dict) -> str:
    codes = ",".join(sorted(str(c) for c in (problem.get("issue_codes") or []) if c))
    fails = ",".join(sorted(str(c) for c in (problem.get("failed_stages") or []) if c))
    return f"{problem.get('job_id')}|{codes}|{fails}"


def _load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return out


def telegram_config() -> tuple[str | None, list[str]]:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    users = (os.getenv("TELEGRAM_ALLOWED_USERS") or "").strip()
    if not token:
        env = _load_dotenv(Path.home() / ".hermes" / ".env")
        token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        users = users or (env.get("TELEGRAM_ALLOWED_USERS") or "").strip()
    if not token:
        env = _load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        users = users or (env.get("TELEGRAM_ALLOWED_USERS") or "").strip()
    uids = [x.strip() for x in users.split(",") if x.strip()]
    return (token or None), uids


def telegram_send(text: str, *, force: bool = False, dedupe_key: str | None = None) -> bool:
    if (os.getenv("AGENT_TELEGRAM") or "").strip() not in ("1", "true", "yes"):
        return False
    key = dedupe_key or text[:200]
    now = time.time()
    if not force and key in _last_tg and (now - _last_tg[key]) < 3600:
        return False  # trùng trong 1h
    token, uids = telegram_config()
    if not token or not uids:
        return False
    ok = False
    for uid in uids:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": uid,
                "text": text[:3500],
                "disable_web_page_preview": "1",
            }).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=20) as resp:
                if 200 <= resp.status < 300:
                    ok = True
        except Exception as e:  # noqa: BLE001
            print(f"[agent_watch] telegram lỗi: {e}", flush=True)
    if ok:
        _last_tg[key] = now
    return ok


def _gpu_busy(work_root: Path) -> bool:
    # Queue job đang chạy / còn chờ → nhường GPU (P0 gate)
    try:
        import core.job_queue as _jq
        if _jq.queue_busy():
            return True
    except Exception:  # noqa: BLE001
        pass
    if not work_root.is_dir():
        return False
    for d in work_root.iterdir():
        if not d.is_dir() or not (d / "manifest.json").is_file():
            continue
        try:
            data = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            for st in (data.get("stages") or {}).values():
                if isinstance(st, dict) and st.get("status") == "running":
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _filter_problems(
    work_root: Path,
    problems: list[dict],
) -> tuple[list[dict], list[str]]:
    """Bỏ job test + đang cooldown cùng fingerprint."""
    state = _load_state(work_root)
    jobs_st = state.setdefault("jobs", {})
    cool = _cooldown_sec()
    now = time.time()
    kept: list[dict] = []
    skipped: list[str] = []
    for p in problems:
        jid = str(p.get("job_id") or "")
        if _should_skip_job(jid):
            skipped.append(f"{jid}:skip_prefix")
            continue
        if p.get("running_stages"):
            skipped.append(f"{jid}:still_running")
            continue
        fp = _fingerprint(p)
        prev = jobs_st.get(jid) or {}
        if prev.get("fp") == fp and (now - float(prev.get("at") or 0)) < cool:
            skipped.append(f"{jid}:cooldown")
            continue
        kept.append(p)
    return kept, skipped


def _mark_healed(work_root: Path, problems: list[dict]) -> None:
    state = _load_state(work_root)
    jobs_st = state.setdefault("jobs", {})
    now = time.time()
    for p in problems:
        jid = str(p.get("job_id") or "")
        if not jid:
            continue
        jobs_st[jid] = {"fp": _fingerprint(p), "at": now}
    _save_state(work_root, state)


def _read_manifest(d: Path) -> dict | None:
    try:
        data = json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def watch_once(
    work_root: Path,
    *,
    list_jobs: Callable[[], list] | None,
    hooks: dict[str, Callable[..., Any]],
    max_jobs: int = 10,
) -> dict:
    from agents.agent_heal import heal_job, scan_problem_jobs

    work_root = Path(work_root)
    # Heal stale orphan trước (không đụng job đang chạy thật)
    heal_stale = hooks.get("heal_stale")
    if heal_stale and work_root.is_dir():
        for d in work_root.iterdir():
            if not d.is_dir() or not (d / "manifest.json").is_file():
                continue
            if _should_skip_job(d.name):
                continue
            try:
                # fix 2026-11-12 agentH (C): job đang là queue.current / có engine sống → không đụng
                from agents.agent_heal import job_active
                if job_active(d.name, hooks):
                    continue
            except Exception:  # noqa: BLE001
                pass
            try:
                data = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
                if any(
                    isinstance(st, dict) and st.get("status") == "running"
                    for st in (data.get("stages") or {}).values()
                ):
                    heal_stale(d.name)
            except Exception:  # noqa: BLE001
                continue

    if _gpu_busy(work_root):
        try:
            import core.job_queue as _jq
            qbusy = _jq.queue_busy()
        except Exception:  # noqa: BLE001
            qbusy = False
        reason = "queue_busy" if qbusy else "gpu_busy"
        log_line(work_root, f"skip — {reason} (nhường GPU cho hàng chờ / job running)")
        return {"ok": True, "skipped": reason}

    scan = scan_problem_jobs(work_root, list_jobs=list_jobs)
    raw = scan.get("problems") or []
    todo, skipped = _filter_problems(work_root, raw)
    if skipped:
        log_line(work_root, f"filter skip: {', '.join(skipped[:12])}")

    n_raw = len(raw)
    if not todo:
        log_line(
            work_root,
            f"scan ok — {n_raw} raw / 0 cần heal (cooldown hoặc skip)",
        )
        return {"ok": True, "n_problems": n_raw, "n_healed": 0, "skipped_filter": skipped}

    todo = todo[: max(1, min(max_jobs, 30))]
    log_line(work_root, f"heal {len(todo)}/{n_raw} job(s) safe")
    results = []
    for p in todo:
        jid = p["job_id"]
        try:
            results.append(
                heal_job(
                    work_root, jid, apply=True, level="safe", hooks=hooks,
                )
            )
        except Exception as e:  # noqa: BLE001
            results.append({"ok": False, "job_id": jid, "error": str(e)})

    _mark_healed(work_root, todo)

    # Chỉ Telegram nếu có action thật (không toàn skipped/409 rỗng).
    # fix 2026-11-12 agentH: tin dựng bằng mẫu agent_brain_notify (1 khối/job, kèm kết quả).
    meaningful = []
    blocks = []
    for r in results:
        try:
            from agents.agent_brain_notify import heal_summary
            hs = heal_summary(r)
        except Exception:  # noqa: BLE001
            hs = {"outcome": "noop", "actions": [], "reason": ""}
        jid = str((r or {}).get("job_id") or "")
        log_line(work_root, f"heal {jid} → {hs['outcome']} acts={hs['actions']} {hs.get('reason') or ''}".rstrip())
        if hs["outcome"] != "ok" or not jid:
            continue
        meaningful.append(f"{jid}:{','.join(hs['actions'])}")
        try:
            from agents.agent_brain_notify import render_job, split_part
            base, part = split_part(jid)
            data = _read_manifest(work_root / jid)
            parent = _read_manifest(work_root / base) if part is not None else None
            blocks.append(render_job(jid, data, [{"kind": "heal", "by": "watch", **hs}], parent=parent))
        except Exception as e:  # noqa: BLE001
            log_line(work_root, f"render tin heal {jid} lỗi: {e}")

    if meaningful:
        from agents.agent_brain_notify import render_tick
        msg = render_tick(blocks)
        log_line(work_root, "healed " + " ; ".join(meaningful[:8]))
        if msg:
            telegram_send(msg, dedupe_key="watch:" + "|".join(meaningful))
    else:
        log_line(work_root, f"heal xong nhưng không có action thành công ({len(results)} job)")

    return {
        "ok": True,
        "applied": True,
        "n_raw_problems": n_raw,
        "n_healed": len(meaningful),
        "results": results,
        "skipped_filter": skipped,
    }


def _loop(work_root: Path, list_jobs, hooks, interval: int, max_jobs: int) -> None:
    log_line(work_root, f"watcher start interval={interval}s max_jobs={max_jobs}")
    if (os.getenv("AGENT_TELEGRAM_STARTUP") or "").strip() in ("1", "true", "yes"):
        telegram_send(
            f"Pineline V2 watcher ON (mỗi {interval}s, heal safe)",
            force=True,
            dedupe_key=f"startup:{interval}",
        )
    # fix 2026-11-12 agentH (C): hoãn lượt heal đầu tiên sau khi start — server vừa lên, queue đang
    # nối lại job (2937ffd7989f fail ASR đúng giây watcher «heal 6/6 safe» lúc khởi động).
    try:
        from agents.agent_heal import start_delay_sec
        delay = start_delay_sec()
    except Exception:  # noqa: BLE001
        delay = 180
    if delay > 0:
        log_line(work_root, f"hoãn heal {delay}s sau khởi động")
        if _stop.wait(delay):
            log_line(work_root, "watcher stop")
            return
    while not _stop.is_set():
        try:
            watch_once(work_root, list_jobs=list_jobs, hooks=hooks, max_jobs=max_jobs)
        except Exception as e:  # noqa: BLE001
            log_line(work_root, f"watch lỗi: {e}")
        _stop.wait(interval)
    log_line(work_root, "watcher stop")


def start_watcher(
    work_root: Path,
    *,
    list_jobs: Callable[[], list] | None,
    hooks: dict[str, Callable[..., Any]],
) -> bool:
    global _thread
    if (os.getenv("AGENT_WATCH") or "").strip() not in ("1", "true", "yes"):
        return False
    try:
        interval = max(60, int(os.getenv("AGENT_WATCH_SEC") or "300"))
    except ValueError:
        interval = 300
    try:
        max_jobs = max(1, min(30, int(os.getenv("AGENT_WATCH_MAX_JOBS") or "10")))
    except ValueError:
        max_jobs = 10

    with _lock:
        if _thread and _thread.is_alive():
            return True
        _stop.clear()
        _thread = threading.Thread(
            target=_loop,
            args=(Path(work_root), list_jobs, hooks, interval, max_jobs),
            name="agent_watch",
            daemon=True,
        )
        _thread.start()
    return True


def stop_watcher() -> None:
    _stop.set()
