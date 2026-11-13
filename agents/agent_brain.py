"""
Second Brain — daemon nền full-silent.

Sensors → incidents → policy → actuators.
Bật: AGENT_BRAIN=1 (hoặc pineline.config.json).
Telegram: AGENT_BRAIN_TELEGRAM=events|minimal|report|all|off
  Nội dung tin do CODE dựng theo mẫu (agent_brain_notify.py, fix 2026-11-12 agentH): 1 khối/job =
  tiêu đề (emoji + tên phim VI + P<n> + id rút gọn) + ≤ 3 dòng (chuyện gì / brain làm gì + kết quả /
  cần bạn làm gì). Văn LLM (DECISION) chỉ vào log; tool mutate của LLM → tin mẫu kèm kết quả.
  Cùng lỗi của cùng job: 1 lần + nhắc lại ≤ mỗi AGENT_BRAIN_REMIND_SEC (7200), ≤ AGENT_BRAIN_REMIND_MAX (3).
  AGENT_HEAL_START_DELAY_SEC (180): hoãn heal + LLM tick sau khi brain khởi động.
  events  (mặc định, fix 2026-11-12 agentG) = chỉ báo khi stage đổi trạng thái (bắt đầu/xong/lỗi),
          job xong toàn bộ, hoặc brain hành động (HOLD/CLEAR/heal) + DECISION có ý nghĩa;
          gộp mọi sự kiện của 1 tick thành 1 tin.
  minimal = chỉ HOLD/L3/hành động.
  report/all = events + digest «Brain báo cáo» (≥ AGENT_BRAIN_TG_SEC, mặc định 600 s,
          chỉ gửi khi nội dung khác lần trước).
"""
from __future__ import annotations

import hashlib
try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _sidecar_url() -> str:
    from core.instance import sidecar_url  # V6 F4: sidecar của instance (v6: 8816), không 8802
    return sidecar_url()


def _self_base() -> str:
    """URL server Pineline đang chạy brain (fix 2026-11-11: trước cứng :8801 → V4/V5 điều khiển nhầm V2)."""
    from core.instance import server_base  # V6 F4: thiếu env → cổng instance, không 8801
    return server_base()

# urllib dùng cho LLM HTTP fallback + infra ping


_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()


def _cfg(key: str, default: str = "") -> str:
    try:
        from core.config import get as _get
        v = _get(key)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    except Exception:  # noqa: BLE001
        pass
    return (os.getenv(key) or default).strip()


def _truthy(v: str) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def brain_enabled() -> bool:
    # Mặc định BẬT theo plan deploy; tắt tường minh AGENT_BRAIN=0
    raw = _cfg("AGENT_BRAIN", "1")
    if raw == "":
        return True
    return _truthy(raw)


def _interval_sec() -> int:
    try:
        return max(30, int(_cfg("AGENT_BRAIN_SEC", "60") or "60"))
    except ValueError:
        return 60


def _tg_mode() -> str:
    return (_cfg("AGENT_BRAIN_TELEGRAM", "events") or "events").lower()


# Level Telegram (fix 2026-11-12 agentG):
#   event    = stage đổi trạng thái / job xong toàn bộ
#   action   = brain HOLD/CLEAR/heal thật
#   decision = DECISION LLM có ý nghĩa (đã lọc + khử trùng lặp)
#   info     = ACT/NEED-LLM (chỉ report/all)   progress = digest (chỉ report/all)
_TG_CORE_LEVELS = ("hold", "l3", "critical", "event", "action", "decision")
_TG_REPORT_MODES = ("report", "digest", "progress", "all", "on")
_tg_local = threading.local()


def _tg_allowed(mode: str, level: str) -> bool:
    mode = (mode or "").lower()
    if mode in ("0", "off", "none", "false"):
        return False
    if mode == "minimal":
        return level in ("hold", "l3", "critical", "action")
    if mode == "events":
        return level in _TG_CORE_LEVELS
    if mode in _TG_REPORT_MODES:
        return level in _TG_CORE_LEVELS + ("info", "progress")
    return level in ("hold", "l3", "critical")


def _tg_begin_tick(work_root: Path | None = None) -> None:
    """Bắt đầu gom tin của 1 tick (thread-local) → _tg_flush_tick gửi 1 tin duy nhất.
    fix 2026-11-12 agentH: sự kiện theo job (_emit) gom thành khối/job, render bằng agent_brain_notify."""
    _tg_local.buf = []
    _tg_local.jobs = {}
    if work_root is not None:
        _tg_local.root = Path(work_root)


def _tz():
    from agents.agent_brain_notify import tz_from_cfg
    return tz_from_cfg(_cfg("AGENT_BRAIN_TZ", ""))


def _render_job_block(jid: str, events: list[dict], root: Path | None) -> str:
    from agents.agent_brain_notify import render_job, split_part
    data = parent = None
    if root is not None:
        data = _read_mf(Path(root) / jid)
        base, part = split_part(jid)
        if part is not None:
            parent = _read_mf(Path(root) / base)
    if data is None:
        data = next((e.get("_data") for e in events if isinstance(e.get("_data"), dict)), None)
    return render_job(jid, data, events, parent=parent, tz=_tz())


def _tg_flush_tick() -> str | None:
    from agents.agent_brain_notify import render_tick
    buf = getattr(_tg_local, "buf", None)
    jobs = getattr(_tg_local, "jobs", None) or {}
    root = getattr(_tg_local, "root", None)
    _tg_local.buf = None
    _tg_local.jobs = None
    if not buf and not jobs:
        return None
    blocks = []
    for jid, evs in jobs.items():
        try:
            blocks.append(_render_job_block(jid, evs, root))
        except Exception as e:  # noqa: BLE001
            print(f"[agent_brain] render {jid} lỗi: {e}", flush=True)
    msg = render_tick(blocks, list(buf or []))
    if not msg:
        return None
    _tg_send(msg, key="tick:" + hashlib.sha1(msg.encode("utf-8")).hexdigest()[:16])
    return msg


def _emit(jid: str, kind: str, *, level: str = "event", data: dict | None = None, **kw: Any) -> None:
    """Sự kiện của 1 job → khối tin dựng bằng mẫu (agent_brain_notify). Ngoài tick: gửi ngay."""
    if not jid or not _tg_allowed(_tg_mode(), level):
        return
    ev = {"kind": kind, **kw}
    if data is not None:
        ev["_data"] = data
    jobs = getattr(_tg_local, "jobs", None)
    if jobs is not None:
        lst = jobs.setdefault(jid, [])
        if not any(_ev_eq(ev, x) for x in lst):
            lst.append(ev)
        return
    msg = _render_job_block(jid, [ev], getattr(_tg_local, "root", None))
    if msg:
        _tg_send(msg, key=f"{level}:{msg[:120]}")


def _ev_eq(a: dict, b: dict) -> bool:
    ka = {k: v for k, v in a.items() if k != "_data"}
    kb = {k: v for k, v in b.items() if k != "_data"}
    return ka == kb


def _tg_send(msg: str, *, key: str) -> None:
    try:
        from agents.agent_watch import telegram_send
        telegram_send(msg, force=True, dedupe_key=f"brain:{key}")
    except Exception as e:  # noqa: BLE001
        print(f"[agent_brain] tg skip: {e}", flush=True)


def _remind_sec() -> int:
    """Cùng lỗi / cùng kết quả hành động của 1 job: nhắc lại tối đa mỗi N giây (mặc định 2 giờ)."""
    try:
        return max(600, int(_cfg("AGENT_BRAIN_REMIND_SEC", "7200") or "7200"))
    except ValueError:
        return 7200


def _once(state: dict | None, key: str, sec: float | None = None) -> bool:
    """True nếu key chưa báo trong `sec` giây (mặc định _remind_sec) — và đánh dấu đã báo."""
    if state is None:
        return True
    sec = _remind_sec() if sec is None else sec
    if not _cooldown_ok(state, "tg:" + key, sec):
        return False
    _mark_fp(state, "tg:" + key, "")
    return True


def _log_dir(root: Path) -> Path:
    d = root / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_line(root: Path, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with (_log_dir(root) / "agent_brain.log").open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001
        pass
    print(f"[agent_brain] {msg}", flush=True)


def _state_path(root: Path) -> Path:
    return _log_dir(root) / "agent_brain_state.json"


def _load_state(root: Path) -> dict:
    p = _state_path(root)
    if not p.is_file():
        return {"fps": {}, "last_tick": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"fps": {}, "last_tick": None}


def _save_state(root: Path, state: dict) -> None:
    try:
        _state_path(root).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


def _cooldown_ok(state: dict, fp: str, sec: float = 1800.0) -> bool:
    fps = state.setdefault("fps", {})
    prev = float((fps.get(fp) or {}).get("at") or 0)
    return (time.time() - prev) >= sec


def _mark_fp(state: dict, fp: str, note: str = "") -> None:
    fps = state.setdefault("fps", {})
    fps[fp] = {"at": time.time(), "note": note}


def _telegram(msg: str, *, level: str = "hold") -> None:
    if not _tg_allowed(_tg_mode(), level):
        return
    buf = getattr(_tg_local, "buf", None)
    if buf is not None and level != "progress":
        # Trong tick: gộp — 1 tin/tick (digest gửi riêng)
        if msg not in buf:
            buf.append(msg)
        return
    _tg_send(msg, key=f"{level}:{msg[:120]}")


def _ping(url: str, timeout: float = 2.5) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200) or 200
            if 200 <= int(code) < 300:
                return True, f"ok:{code}"
            return False, f"http:{code}"
    except urllib.error.HTTPError as e:
        return False, f"http:{e.code}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:80]


def sense_infra() -> list[dict]:
    out: list[dict] = []
    checks = [
        ("llama_8083", _machine.llama_base_url() + "/models"),
        ("chatgpt_3377", _machine.aurora_base_url() + "/models"),
        ("sidecar", _sidecar_url() + "/api/agent/status"),
    ]
    for name, url in checks:
        ok, detail = _ping(url)
        if not ok:
            sev = "l0"
            if name == "llama_8083" and "503" in detail:
                sev = "l0"
            out.append({
                "layer": "l0",
                "kind": "infra_down",
                "target": name,
                "detail": detail,
                "sev": sev,
            })
    return out


def sense_jobs(work_root: Path, *, max_jobs: int = 40) -> list[dict]:
    """Quét job có script / failed / stale cho quality + ops hints."""
    work_root = Path(work_root)
    incidents: list[dict] = []
    if not work_root.is_dir():
        return incidents
    dirs = sorted(
        [d for d in work_root.iterdir() if d.is_dir() and (d / "manifest.json").is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:max_jobs]
    for d in dirs:
        if (d / ".stop").exists():
            continue
        try:
            mf = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if job_finished(d, mf):   # fix 2026-11-13 agentN: job đã xong → không incident
            continue
        stages = mf.get("stages") or {}
        agent = mf.get("agent") or {}
        # Ops: failed stages
        for name, st in stages.items():
            if not isinstance(st, dict):
                continue
            if st.get("status") == "failed":
                incidents.append({
                    "layer": "l1",
                    "kind": "stage_failed",
                    "job_id": d.name,
                    "stage": name,
                    "detail": (st.get("error") or st.get("cur_text") or "")[:200],
                })
        # Quality chỉ khi được đụng (không mid_review / post_tts)
        tr = stages.get("translate") or {}
        if (d / "script.json").is_file() and tr.get("status") in (
            "running", "done", "ok", "completed", "pending",
        ):
            phase = _qa_pipeline_phase(d)
            if phase["phase"] in ("mid_review", "post_tts"):
                continue
            incidents.append({
                "layer": "l2",
                "kind": "quality_scan",
                "job_id": d.name,
                "review": bool(tr.get("review")),
                "held": bool(agent.get("hold_after_translate") or agent.get("qa_blocked")),
                "phase": phase["phase"],
            })
    return incidents


def _qa_pipeline_phase(work_dir: Path) -> dict:
    """Perceive: job đang ở đâu — mid_review | pre_tts | post_tts | idle.

    Brain chỉ HOLD ở pre_tts (dịch/soát xong, TTS chưa chạy, còn high).
    """
    work_dir = Path(work_dir)
    out = {
        "phase": "idle",
        "busy": None,
        "tr_status": "",
        "tts_status": "",
        "mux_status": "",
        "review_progress": None,
        "can_touch_script": False,
        "can_hold": False,
    }
    try:
        from core.manifest import Manifest
        from agents.agent_hang_fix import _review_or_translate_busy
        m = Manifest(work_dir)
        stg = m.data.get("stages") or {}
        tr = stg.get("translate") or {}
        tts = stg.get("tts") or {}
        mux = stg.get("mux") or {}
        out["tr_status"] = str(tr.get("status") or "")
        out["tts_status"] = str(tts.get("status") or "")
        out["mux_status"] = str(mux.get("status") or "")
        try:
            out["review_progress"] = float(tr.get("review_progress")) if tr.get("review_progress") is not None else None
        except (TypeError, ValueError):
            out["review_progress"] = None
        busy = _review_or_translate_busy(work_dir)
        out["busy"] = busy
        tts_st = out["tts_status"].lower()
        mux_st = out["mux_status"].lower()
        if tts_st in ("done", "ok", "completed", "running") or mux_st in (
            "done", "ok", "completed", "running",
        ):
            out["phase"] = "post_tts"
            return out
        if busy:
            out["phase"] = "mid_review"
            return out
        tr_st = out["tr_status"].lower()
        rp = out["review_progress"]
        review_done = tr_st in ("done", "ok", "completed") or (
            bool(tr.get("reviewed") or tr.get("review"))
            and rp is not None
            and rp >= 99.5
        )
        if review_done or (tr_st in ("done", "ok", "completed")):
            out["phase"] = "pre_tts"
            out["can_touch_script"] = True
            out["can_hold"] = True
            return out
        # pending/idle có script nhưng chưa cửa TTS
        out["phase"] = "idle"
        out["can_touch_script"] = tr_st not in ("running",)
        return out
    except Exception as e:  # noqa: BLE001
        out["phase"] = "idle"
        out["busy"] = f"err:{e}"
        return out


# ---------------------------------------------------------------------------
# Sự kiện stage + gỡ HOLD cũ (fix 2026-11-12 agentG)
# ---------------------------------------------------------------------------
_WIRED_STAGES = ("download", "asr", "translate", "tts", "mux")
_DONE_ST = ("done", "ok", "completed")
_HOLD_KEYS = ("qa_blocked", "hold_after_translate", "held_at")


def _norm_status(v: Any) -> str:
    s = str(v or "").strip().lower()
    return "done" if s in _DONE_ST else s


def _job_dirs(work_root: Path, max_jobs: int = 60) -> list[Path]:
    work_root = Path(work_root)
    if not work_root.is_dir():
        return []
    try:
        dirs = [d for d in work_root.iterdir() if d.is_dir() and (d / "manifest.json").is_file()]
    except OSError:
        return []

    def _mt(p: Path) -> float:
        try:
            return (p / "manifest.json").stat().st_mtime
        except OSError:
            return 0.0

    return sorted(dirs, key=_mt, reverse=True)[:max_jobs]


def _read_mf(d: Path) -> dict | None:
    try:
        data = json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _has_output(d: Path) -> bool:
    try:
        p = Path(d) / "output.mp4"
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _job_title(data: dict, jid: str) -> str:
    req = data.get("request") or {}
    t = data.get("title_vi") or data.get("title") or req.get("title") or (
        (data.get("stages") or {}).get("download") or {}
    ).get("title") or ""
    return str(t)[:40]


def job_finished(d: Path, data: dict | None = None) -> bool:
    """Job đã xong — MỘT định nghĩa dùng chung: agent_hold.job_finished (fix 2026-11-13 agentN:
    + đã đăng, + parent đã ghép Full, + part của parent đó, + output_full.mp4)."""
    from agents.agent_hold import job_finished as _jf
    return bool(_jf(Path(d), data))


def _is_held(ag: dict | None) -> bool:
    ag = ag or {}
    return any(ag.get(k) for k in _HOLD_KEYS)


def _scan_job_states(work_root: Path, max_jobs: int = 60) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in _job_dirs(work_root, max_jobs):
        data = _read_mf(d)
        if data is None:
            continue
        st: dict[str, str] = {}
        err: dict[str, str] = {}
        at: dict[str, Any] = {}
        for name, v in (data.get("stages") or {}).items():
            if not isinstance(v, dict):
                continue
            st[name] = _norm_status(v.get("status"))
            if st[name] == "failed":
                err[name] = str(v.get("error") or v.get("cur_text") or "")[:120]
                at[name] = v.get("finished_at") or v.get("updated_at") or v.get("started_at")
        ag = data.get("agent") or {}
        try:
            from agents.agent_hold import hold_info
            hi = hold_info(ag)
        except Exception:  # noqa: BLE001
            hi = {}
        out[d.name] = {
            "st": st,
            "err": err,
            "at": at,
            "complete": bool(job_finished(d, data)),
            "title": _job_title(data, d.name),
            "held": _is_held(ag),
            "hold_reason": str(ag.get("hold_reason") or ag.get("held_at") or "")[:120],
            # fix 2026-11-13 agentN: loại tạm dừng (park tự thử lại ≠ HOLD cần người)
            "hold_cls": hi.get("cls"),
            "park_try": hi.get("try"),
            "retry_at": hi.get("retry_at"),
            "n_lines": hi.get("n_lines"),
            "parts": hi.get("parts") or [],
        }
    return out


def _remind_max() -> int:
    try:
        return max(0, int(_cfg("AGENT_BRAIN_REMIND_MAX", "3") or "3"))
    except ValueError:
        return 3


def detect_stage_events(work_root: Path, state: dict, *, max_jobs: int = 60) -> list[dict]:
    """So trạng thái stage hiện tại với lần trước (state['job_stages']) → list sự kiện (dict).

    Sự kiện: stage_start / stage_done / stage_failed / job_done / hold_set / remind_failed
    (mỗi dict có "jid" + "kind" — render bằng agent_brain_notify). Lần đầu (chưa có baseline)
    → chỉ ghi baseline, không báo (tránh lũ tin sau deploy).
    Cùng 1 lỗi của cùng job: báo 1 lần lúc chuyển sang failed, nhắc lại tối đa mỗi
    AGENT_BRAIN_REMIND_SEC (7200 s), tối đa AGENT_BRAIN_REMIND_MAX (3) lần.
    """
    cur = _scan_job_states(work_root, max_jobs=max_jobs)
    prev = state.get("job_stages")
    evs: list[dict] = []
    notes = state.setdefault("fail_notes", {})
    now = time.time()
    if isinstance(prev, dict):
        for jid, info in cur.items():
            p = prev.get(jid)
            pst = (p or {}).get("st") or {}
            names = [s for s in _WIRED_STAGES if s in info["st"]] + sorted(
                s for s in info["st"] if s not in _WIRED_STAGES
            )
            for stg in names:
                s_now = info["st"].get(stg)
                if s_now == pst.get(stg) or s_now not in ("running", "done", "failed"):
                    continue
                if p is None and s_now == "done":
                    continue  # job mới xuất hiện: không kể lại các bước đã xong
                if s_now == "running":
                    evs.append({"jid": jid, "kind": "stage_start", "stage": stg})
                elif s_now == "done":
                    evs.append({"jid": jid, "kind": "stage_done", "stage": stg})
                else:
                    err = (info.get("err") or {}).get(stg, "")
                    evs.append({"jid": jid, "kind": "stage_failed", "stage": stg, "error": err,
                                "at": (info.get("at") or {}).get(stg)})
                    notes[f"{jid}:{stg}"] = {"sig": err[:60], "at": now, "n": 0}
            if p is not None and info.get("complete") and not p.get("complete"):
                evs.append({"jid": jid, "kind": "job_done"})
            if p is not None and not info.get("complete"):
                ev = _hold_event(jid, info, p)
                if ev:
                    evs.append(ev)
    # nhắc lại lỗi chưa xử lý
    rmax = _remind_max()
    live: set[str] = set()
    for jid, info in cur.items():
        if info.get("complete"):   # agentN: job đã xong → không nhắc lỗi cũ
            continue
        for stg, err in (info.get("err") or {}).items():
            k = f"{jid}:{stg}"
            live.add(k)
            n = notes.get(k)
            if not isinstance(n, dict) or n.get("sig") != err[:60]:
                notes[k] = {"sig": err[:60], "at": now, "n": 0}   # baseline / lỗi khác → không nhắc ngay
                continue
            if rmax and int(n.get("n") or 0) < rmax and now - float(n.get("at") or 0) >= _remind_sec():
                evs.append({"jid": jid, "kind": "remind_failed", "stage": stg, "error": err,
                            "at": (info.get("at") or {}).get(stg)})
                n["at"] = now
                n["n"] = int(n.get("n") or 0) + 1
    for k in list(notes):
        if k not in live and (Path(work_root) / k.split(":", 1)[0] / "manifest.json").is_file():
            notes.pop(k, None)
    if len(notes) > 400:
        state["fail_notes"] = dict(list(notes.items())[-400:])
    keep: dict[str, dict] = {}
    if isinstance(prev, dict):
        for k, v in prev.items():
            if k not in cur and (Path(work_root) / k / "manifest.json").is_file():
                keep[k] = v
    keep.update(cur)
    if len(keep) > 300:
        keep = dict(list(keep.items())[-300:])
    state["job_stages"] = keep
    return evs


def _hold_event(jid: str, info: dict, p: dict) -> dict | None:
    """fix 2026-11-13 agentN — tin tạm dừng ĐÚNG loại, mỗi lần chuyển 1 tin:
    * park tự động (waiting_review simple/B2) / split_parts → «park_set» (⏸ … tự thử lại lúc HH:MM (lần k/max)),
      chỉ khi MỚI park hoặc lượt park mới (review_retries đổi) — không nhắc lại;
    * HOLD cần người (review_failed / qa_* / simple_gate / hold_after_translate) → «hold_set» (⛔ … Cần bạn),
      khi mới HOLD hoặc chuyển từ park/loại khác sang.
    State cũ (chưa có hold_cls) → coi như không đổi (không lũ tin sau restart)."""
    from agents.agent_hold import AUTO_PARK, NEED_BY_HOLD, HOLD_PARK
    cls = info.get("hold_cls")
    if not cls:
        return None
    p_held = bool(p.get("held"))
    pcls = p.get("hold_cls") if "hold_cls" in p else (cls if p_held else None)
    if cls in AUTO_PARK:
        new_try = cls == HOLD_PARK and pcls == cls and "park_try" in p and p.get("park_try") != info.get("park_try")
        if pcls != cls or new_try:
            return {"jid": jid, "kind": "park_set", "cls": cls, "n_lines": info.get("n_lines"),
                    "retry_at": info.get("retry_at"), "try": info.get("park_try"),
                    "max": _review_retry_max(), "parts": info.get("parts") or [],
                    "reason": info.get("hold_reason") or ""}
        return None
    if pcls != cls:
        return {"jid": jid, "kind": "hold_set", "cls": cls, "reason": info.get("hold_reason") or "",
                "need": NEED_BY_HOLD.get(cls)}
    return None


def _review_retry_max() -> int:
    from agents.agent_hold import review_retry_max
    return review_retry_max()


def emit_events(evs: list[dict]) -> None:
    """Đẩy sự kiện vào buffer tick (level theo loại)."""
    for ev in evs or []:
        ev = dict(ev)
        jid = ev.pop("jid", "")
        kind = ev.pop("kind", "")
        level = "hold" if kind == "hold_set" else "event"
        _emit(jid, kind, level=level, **ev)


def clear_stale_holds(work_root: Path, state: dict | None = None, *, max_jobs: int = 80) -> list[str]:
    """Gỡ HOLD cũ TẤT ĐỊNH (không cần LLM): job đã xong (mux done + output, hoặc mọi stage done)
    mà manifest.agent còn qa_blocked / hold_after_translate / held_at → clear qua Manifest.update."""
    work_root = Path(work_root)
    cleared: list[str] = []
    for d in _job_dirs(work_root, max_jobs):
        data = _read_mf(d)
        if not data:
            continue
        ag = data.get("agent") or {}
        if not any(ag.get(k) for k in _HOLD_KEYS):
            continue
        if not job_finished(d, data):
            continue
        old = {k: ag.get(k) for k in _HOLD_KEYS + ("hold_reason",) if ag.get(k)}

        def _fn(dd: dict, _d: Path = d) -> bool | None:
            if not job_finished(_d, dd):
                return False
            a = dict(dd.get("agent") or {})
            if not any(a.get(k) for k in _HOLD_KEYS):
                return False
            a["qa_blocked"] = False
            a["hold_after_translate"] = False
            a.pop("held_at", None)
            a.pop("hold_reason", None)
            from agents.agent_hold import PARK_KEYS
            for k in PARK_KEYS:   # agentN: job xong → bỏ cả dấu park (review_retry_at / split_wait_parts…)
                a.pop(k, None)
            a["hold_cleared"] = {
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "by": "brain:job_done",
                "was": {k: v for k, v in old.items() if k != "hold_reason"},
            }
            dd["agent"] = a
            tr = (dd.get("stages") or {}).get("translate")
            if isinstance(tr, dict) and "HOLD" in str(tr.get("cur_text") or ""):
                tr["cur_text"] = "HOLD cũ đã gỡ (job đã xong)"
            return None

        try:
            ok = js.try_mutate(d, _fn)   # V6: state/job_state (Manifest.update trong khoá)
        except Exception as e:  # noqa: BLE001
            log_line(work_root, f"clear stale hold {d.name} lỗi: {e}")
            continue
        if not ok:
            continue
        cleared.append(d.name)
        _drop_pending(work_root, d.name)
        log_line(
            work_root,
            f"CLEAR HOLD {d.name} (job đã xong — mux done/output có) was={json.dumps(old, ensure_ascii=False)[:200]}",
        )
        # agentN: part «_pNN» của parent đã ghép Full → gỡ im lặng (không lũ tin cho từng part cũ)
        try:
            from agents.agent_brain_notify import split_part
            base, part = split_part(d.name)
            quiet = part is not None and job_finished(work_root / base)
        except Exception:  # noqa: BLE001
            quiet = False
        if not quiet:
            _emit(d.name, "job_done", level="action")
            _emit(d.name, "hold_cleared", level="action", why="job_done")
        if state is not None:
            _mark_fp(state, f"clearhold:{d.name}", "job_done")
    return cleared


def _drop_pending(work_root: Path, jid: str) -> None:
    try:
        pend = _log_dir(Path(work_root)) / "brain_pending_decisions.json"
        if not pend.is_file():
            return
        items = json.loads(pend.read_text(encoding="utf-8")) or []
        if not isinstance(items, list):
            return
        new = [x for x in items if not (isinstance(x, dict) and x.get("job_id") == jid)]
        if len(new) != len(items):
            pend.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _gpu_or_queue_busy(work_root: Path) -> bool:
    try:
        import core.job_queue as _jq
        if _jq.queue_busy():
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from agents.agent_watch import _gpu_busy
        return bool(_gpu_busy(work_root))
    except Exception:  # noqa: BLE001
        return False


def decide(incidents: list[dict], state: dict) -> list[dict]:
    """Policy full-silent → list actions."""
    actions: list[dict] = []
    for inc in incidents:
        layer = inc.get("layer")
        kind = inc.get("kind")
        if layer == "l0" and kind == "infra_down":
            fp = f"l0:{inc.get('target')}:{inc.get('detail')}"
            if not _cooldown_ok(state, fp, 600):
                continue
            actions.append({
                "type": "infra_recover",
                "target": inc.get("target"),
                "detail": inc.get("detail"),
                "fp": fp,
            })
        elif layer == "l1" and kind == "stage_failed":
            jid = inc.get("job_id")
            fp = f"l1:{jid}:{inc.get('stage')}:{inc.get('detail')[:40]}"
            if not _cooldown_ok(state, fp, 1800):
                continue
            actions.append({
                "type": "heal_job",
                "job_id": jid,
                "level": "safe",
                "fp": fp,
                "detail": inc.get("detail"),
            })
        elif layer == "l2" and kind == "quality_scan":
            jid = inc.get("job_id")
            fp = f"l2qual:{jid}"
            # quality scan mỗi tick OK; heal/HOLD có cooldown riêng trong apply
            actions.append({
                "type": "quality_job",
                "job_id": jid,
                "fp": fp,
                "held": inc.get("held"),
            })
    return actions


def apply_actions(
    work_root: Path,
    actions: list[dict],
    state: dict,
    *,
    hooks: dict[str, Callable[..., Any]] | None = None,
) -> list[dict]:
    hooks = hooks or {}
    results: list[dict] = []
    work_root = Path(work_root)

    for act in actions:
        typ = act.get("type")
        try:
            if typ == "infra_recover":
                target = act.get("target")
                detail = str(act.get("detail") or "")
                note = f"infra {target} {detail}"
                if target == "llama_8083":
                    # Đợi load / thử switch REVIEW_PRESET nếu không phải đang loading ngắn
                    preset = _cfg("REVIEW_PRESET") or _cfg("TRANSLATE_PRESET") or ""
                    if "503" in detail or "Loading" in detail:
                        log_line(work_root, f"infra: {target} loading/503 — chờ (không spam switch)")
                    elif preset:
                        try:
                            import tools.preset_switcher as preset_switcher
                            ok = preset_switcher.switch_preset(preset, max_wait_sec=120)
                            note = f"switch {preset} ok={ok}"
                            log_line(work_root, note)
                            if ok:
                                _telegram(f"🔧 Brain: nạp lại llama {preset} → thành công.", level="l3")
                        except Exception as e:  # noqa: BLE001
                            note = f"switch fail: {e}"
                            log_line(work_root, note)
                    _mark_fp(state, act["fp"], note)
                    results.append({"ok": True, "action": act, "note": note})
                else:
                    log_line(work_root, f"infra down: {target} {detail}")
                    _mark_fp(state, act["fp"], detail)
                    results.append({"ok": True, "action": act, "note": "logged"})

            elif typ == "heal_job":
                jid = str(act.get("job_id") or "")
                if not jid or (work_root / jid / ".stop").exists():
                    continue
                from agents.agent_heal import heal_job, job_active
                if job_finished(work_root / jid):   # fix 2026-11-13 agentN (port R6)
                    results.append({"ok": True, "action": act, "skipped": "finished"})
                    continue
                # fix 2026-11-12 agentH (C): không heal job đang là queue.current / có engine sống
                why_active = job_active(jid, hooks)
                if why_active:
                    log_line(work_root, f"heal {jid} bỏ qua — job đang chạy ({why_active})")
                    results.append({"ok": True, "action": act, "skipped": f"active:{why_active}"})
                    continue
                level = act.get("level") or "safe"
                r = heal_job(work_root, jid, apply=True, level=level, hooks=hooks)
                # escalate một lần nếu safe không đủ và không stopped
                if (
                    isinstance(r, dict)
                    and not r.get("healed")
                    and level == "safe"
                    and _cooldown_ok(state, f"l1agg:{jid}", 3600)
                ):
                    r2 = heal_job(
                        work_root, jid, apply=True, level="aggressive", hooks=hooks,
                    )
                    r = {"safe": r, "aggressive": r2}
                    _mark_fp(state, f"l1agg:{jid}", "aggressive")
                    log_line(work_root, f"heal aggressive {jid}")
                _mark_fp(state, act["fp"], "heal")
                results.append({"ok": True, "action": act, "result": r})
                try:
                    from agents.agent_brain_notify import heal_summary
                    hs = heal_summary(r)
                    log_line(work_root, f"heal {jid} → {hs['outcome']} acts={hs['actions']} {hs['reason']}")
                    if _once(state, f"heal:{jid}:{hs['outcome']}:{','.join(hs['actions'])}"):
                        _emit(jid, "heal", level="action", by="brain", **hs)
                except Exception:  # noqa: BLE001
                    pass

            elif typ == "quality_job":
                jid = str(act.get("job_id") or "")
                if not jid or (work_root / jid / ".stop").exists():
                    continue
                wd = work_root / jid
                phase = _qa_pipeline_phase(wd)
                # 1) Perceive — đang mid_review / post_tts → không đụng, không HOLD
                if phase["phase"] == "mid_review":
                    log_line(
                        work_root,
                        f"qual {jid} skip — đang {phase.get('busy')} (phase=mid_review)",
                    )
                    results.append({"ok": True, "action": act, "skipped": "mid_review", "phase": phase})
                    continue
                if phase["phase"] == "post_tts":
                    log_line(
                        work_root,
                        f"qual {jid} skip — TTS/mux đã chạy (phase=post_tts)",
                    )
                    results.append({"ok": True, "action": act, "skipped": "post_tts", "phase": phase})
                    continue

                # 2) Act — sửa/ack khi được đụng script
                from agents.agent_quality import scan_and_heal_job
                r = scan_and_heal_job(wd, write=bool(phase.get("can_touch_script", True)))
                high = int(r.get("high_open") or 0)
                healed = int(r.get("healed") or 0)
                hang = r.get("hang_fix") or {}
                n_fixed = int(hang.get("n_fixed") or hang.get("n_fixed_cues") or 0)
                n_cases = int(hang.get("n_cases") or 0)
                skipped_hang = bool(hang.get("skipped"))
                note = (
                    f"qual {jid} phase={phase['phase']} healed={healed} "
                    f"hang_cases={n_cases} fixed={n_fixed} high={high}"
                    + (f" hang_skip={hang.get('reason')}" if skipped_hang else "")
                )
                log_line(work_root, note)
                results.append({"ok": bool(r.get("ok")), "action": act, "result": r, "phase": phase})

                # Báo kết quả xử lý (không chỉ HOLD)
                if healed or n_fixed or n_cases:
                    _telegram(
                        f"Brain ACT {jid}: healed={healed} hang_fix={n_fixed}/{n_cases} "
                        f"high_left={high} phase={phase['phase']}",
                        level="info",
                    )

                set_hold = hooks.get("set_hold") or hooks.get("set_hold_after_translate")

                # 3) Rule path: chỉ CLEAR khi sạch. HOLD/phương án lỗi → LLM agent quyết.
                if high == 0:
                    try:
                        from core.manifest import Manifest
                        m = Manifest(wd)
                        agent0 = dict(m.data.get("agent") or {})
                        was_held = bool(
                            agent0.get("held_at") == "qa_brain" or agent0.get("qa_blocked")
                        )
                        if was_held or act.get("held"):
                            def _clr(dd: dict) -> None:
                                a = dict(dd.get("agent") or {})
                                a["qa_blocked"] = False
                                a.pop("held_at", None)
                                a.pop("hold_reason", None)
                                a["hold_after_translate"] = False
                                dd["agent"] = a
                            js.mutate(wd, _clr)  # V6: qua state/job_state
                            if set_hold:
                                set_hold(jid, False)
                            log_line(work_root, f"CLEAR HOLD {jid} (qa clean → pipeline)")
                            _emit(jid, "hold_cleared", level="action", why="qa_clean")
                    except Exception as e:  # noqa: BLE001
                        log_line(work_root, f"clear hold fail: {e}")
                elif high > 0:
                    # Ghi phiếu cho LLM — không tự HOLD (mọi quyết định HOLD phải qua agent)
                    try:
                        pend = _log_dir(work_root) / "brain_pending_decisions.json"
                        items = []
                        if pend.is_file():
                            try:
                                items = json.loads(pend.read_text(encoding="utf-8")) or []
                            except Exception:  # noqa: BLE001
                                items = []
                        if not isinstance(items, list):
                            items = []
                        items = [x for x in items if not (
                            isinstance(x, dict) and x.get("job_id") == jid
                        )]
                        items.append({
                            "at": time.time(),
                            "job_id": jid,
                            "phase": phase.get("phase"),
                            "high": high,
                            "healed": healed,
                            "hang_fixed": n_fixed,
                            "need": "llm_decide",
                            "hint": (
                                "pre_tts+high → cân nhắc HOLD sau pineline_job"
                                if phase.get("phase") == "pre_tts"
                                else f"phase={phase.get('phase')} high={high} — đừng HOLD nếu mid_review"
                            ),
                        })
                        pend.write_text(
                            json.dumps(items[-30:], ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    except Exception as e:  # noqa: BLE001
                        log_line(work_root, f"pending decision write: {e}")
                    log_line(
                        work_root,
                        f"qual {jid} high={high} phase={phase['phase']} — chờ LLM agent quyết "
                        f"(không auto-HOLD)",
                    )
                    if _cooldown_ok(state, f"needllm:{jid}:{high}", 600):
                        _mark_fp(state, f"needllm:{jid}:{high}", "pending")
                        _telegram(
                            f"Brain NEED-LLM {jid}: high={high} phase={phase['phase']} "
                            f"healed={healed} — agent phải đọc job rồi chọn plan",
                            level="info",
                        )
            else:
                results.append({"ok": False, "action": act, "error": "unknown type"})
        except Exception as e:  # noqa: BLE001
            log_line(work_root, f"action lỗi {typ}: {e}")
            results.append({"ok": False, "action": act, "error": str(e)})
    return results


def _llm_enabled() -> bool:
    mode = (_cfg("AGENT_BRAIN_MODE", "hybrid") or "hybrid").lower()
    if mode in ("llm", "hybrid", "agent"):
        return True
    if mode in ("rules", "rule", "off"):
        return False
    return _truthy(_cfg("AGENT_BRAIN_LLM", "1"))


def _llm_interval_sec() -> int:
    try:
        return max(60, int(_cfg("AGENT_BRAIN_LLM_SEC", "180") or "180"))
    except ValueError:
        return 180


def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 60) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=({"Content-Type": "application/json"} if body is not None else {})
        | {"X-Pineline-Caller": "brain"},   # fix agentH: không bị coi là «chạy tay» (gỡ HOLD)
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _ensure_brain_runtime(work_root: Path) -> str:
    """Bind agent_core Runtime nếu chưa có — FS + HTTP proxy (không cần restart :8801)."""
    import agents.agent_core as agent_core

    if getattr(agent_core, "_rt", None) is not None:
        return "already_bound"

    work_root = Path(work_root)

    def list_jobs() -> list[dict]:
        jobs: list[dict] = []
        if not work_root.is_dir():
            return jobs
        for d in work_root.iterdir():
            if not d.is_dir():
                continue
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            req = data.get("request") or {}
            jobs.append({
                "job_id": d.name,
                "title": data.get("title") or req.get("title") or d.name,
                "status": data.get("status") or "unknown",
                "overall": data.get("overall") or 0,
                "stages": data.get("stages") or {},
                "group_id": data.get("group_id"),
                "group_title": data.get("group_title"),
                "group_index": data.get("group_index"),
                "agent": data.get("agent") or {},
            })
        jobs.sort(key=lambda j: str(j.get("job_id")))
        return jobs

    def get_job(job_id: str) -> dict:
        mf = work_root / job_id / "manifest.json"
        if not mf.is_file():
            raise FileNotFoundError(f"job {job_id} không tồn tại")
        data = json.loads(mf.read_text(encoding="utf-8"))
        req = data.get("request") or {}
        return {
            "job_id": job_id,
            "title": data.get("title") or req.get("title") or job_id,
            "status": data.get("status") or "unknown",
            "overall": data.get("overall") or 0,
            "stages": data.get("stages") or {},
            "group_id": data.get("group_id"),
            "group_title": data.get("group_title"),
            "group_index": data.get("group_index"),
            "agent": data.get("agent") or {},
            "request": req,
        }

    def queue_state() -> dict:
        try:
            return _http_json("GET", _self_base() + "/api/queue/jobs", timeout=8)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e), "queue": [], "current": None}

    def queue_add(job_ids: list[str], opts: dict) -> dict:
        body = {"job_ids": job_ids, **(opts or {})}
        try:
            return _http_json("POST", _self_base() + "/api/queue/jobs", body, timeout=30)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def queue_stop() -> dict:
        try:
            return _http_json("POST", _self_base() + "/api/queue/jobs/stop", {}, timeout=15)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def get_hold(job_id: str) -> dict:
        mf = work_root / job_id / "manifest.json"
        if not mf.is_file():
            return {}
        try:
            return dict(json.loads(mf.read_text(encoding="utf-8")).get("agent") or {})
        except Exception:  # noqa: BLE001
            return {}

    def set_hold(job_id: str, enabled: bool) -> dict:
        mf = work_root / job_id / "manifest.json"
        if not mf.is_file():
            return {"ok": False, "error": "job không tồn tại"}
        if enabled:
            try:   # fix 2026-11-12 agentH (B): job đã gỡ HOLD tay → không HOLD lại khi không có high mới
                from agents import agent_hold
                if agent_hold.hold_suppressed(work_root / job_id):
                    return {"ok": False, "job_id": job_id, "error": "người dùng đã gỡ HOLD (chạy tay), "
                            "không có high QA mới — không đặt lại HOLD"}
            except Exception:  # noqa: BLE001
                pass

        def _fn(data: dict) -> None:   # fix agentH: Manifest.update thay write_text thô (không khoá)
            agent = dict(data.get("agent") or {})
            agent["hold_after_translate"] = bool(enabled)
            if enabled:
                agent.pop("held_at", None)
                agent["hold_reason"] = "brain: dừng sau translate để duyệt"
            else:
                agent.pop("held_at", None)
                agent.pop("hold_reason", None)
            data["agent"] = agent
        ok = js.try_mutate(work_root / job_id, _fn)   # V6: state/job_state
        return {"ok": bool(ok), "job_id": job_id, "hold_after_translate": bool(enabled)}

    def edit_line(job_id: str, i: int, vi: str | None, zh: str | None) -> dict:
        body = {"i": i}
        if vi is not None:
            body["vi"] = vi
        if zh is not None:
            body["zh"] = zh
        try:
            return _http_json(
                "POST",
                f"{_self_base()}/api/jobs/{job_id}/script-line",
                body,
                timeout=30,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def run_stage(job_id: str, stage: str, opts: dict) -> dict:
        try:
            return _http_json(
                "POST",
                f"{_self_base()}/api/jobs/{job_id}/run/{stage}",
                opts or {},
                timeout=30,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def stop_job(job_id: str) -> dict:
        try:
            return _http_json(
                "POST", f"{_self_base()}/api/jobs/{job_id}/stop", {}, timeout=20,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def notify(text: str) -> None:
        try:
            from agents.agent_watch import telegram_send
            if not telegram_send(text, force=True):
                print(f"[agent_brain] {text}", flush=True)
        except Exception:  # noqa: BLE001
            print(f"[agent_brain] {text}", flush=True)

    def heal_stale(job_id: str) -> dict:
        try:
            return _http_json(
                "POST",
                f"{_self_base()}/api/agent/jobs/{job_id}/reset-state",
                {"stages": ["*"], "keep_asr": True, "restart": False},
                timeout=60,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    agent_core.bind(agent_core.Runtime(
        work_root=work_root,
        list_jobs=list_jobs,
        get_job=get_job,
        queue_state=queue_state,
        queue_add=queue_add,
        queue_stop=queue_stop,
        list_voices=lambda: [],
        edit_script_line=edit_line,
        set_hold_after_translate=set_hold,
        get_hold=get_hold,
        run_stage=run_stage,
        stop_job=stop_job,
        notify=notify,
        heal_hooks={
            "heal_stale": heal_stale,
            "run_stage": lambda jid, st: run_stage(jid, st, {}),
            "stop_job": stop_job,
        },
    ))
    return "bound_fs_http"


def _run_agent_llm(
    message: str,
    *,
    work_root: Path,
    max_steps: int = 10,
    force_first_tool: str = "pineline_status",
    force_first_args: dict | None = None,
) -> dict:
    """Gọi agent_core tool-loop in-process (bind FS/HTTP nếu cần) hoặc fallback HTTP chat."""
    try:
        from agents import agent_hold
        ctx = agent_hold.auto_context("brain")
    except Exception:  # noqa: BLE001
        import contextlib
        ctx = contextlib.nullcontext()
    with ctx:
        return _run_agent_llm_inner(message, work_root=work_root, max_steps=max_steps,
                                    force_first_tool=force_first_tool, force_first_args=force_first_args)


def _run_agent_llm_inner(
    message: str,
    *,
    work_root: Path,
    max_steps: int = 10,
    force_first_tool: str = "pineline_status",
    force_first_args: dict | None = None,
) -> dict:
    inproc_err = "skip"
    try:
        import agents.agent_core as agent_core
        via_bind = _ensure_brain_runtime(Path(work_root))
        kwargs: dict[str, Any] = {
            "history": None,
            "allow_mutate": True,
            "max_steps": max_steps,
            "require_confirm": False,
        }
        try:
            res = agent_core.run_agent(
                message,
                force_first_tool=force_first_tool or None,
                force_first_args=force_first_args,
                **kwargs,
            )
        except TypeError:
            try:
                res = agent_core.run_agent(
                    message,
                    force_first_tool=force_first_tool or None,
                    **kwargs,
                )
            except TypeError:
                res = agent_core.run_agent(message, **kwargs)
        return {
            "ok": True,
            "via": f"inprocess:{via_bind}",
            "reply": res.reply,
            "steps": res.steps,
            "model": res.model,
            "confirms": res.confirms,
        }
    except Exception as e:  # noqa: BLE001
        inproc_err = str(e)

    try:
        payload = json.dumps({
            "message": message,
            "allow_mutate": True,
            "require_confirm": False,
            "max_steps": max_steps,
            "history": [],
            "force_first_tool": force_first_tool or None,
        }).encode("utf-8")
        req = urllib.request.Request(
            _self_base() + "/api/agent/chat",
            data=payload,
            headers={"Content-Type": "application/json", "X-Pineline-Caller": "brain"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        data["via"] = "http"
        data.setdefault("ok", True)
        return data
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"llm_tick fail inproc={inproc_err}; http={e}",
        }


_READ_TOOLS = (
    "pineline_status", "pineline_job", "queue_status", "running_check",
    "config_get", "films", "library", "diagnose_job", "scan_failed",
    "review_diff", "stage_peek", "stage_peek_all", "style_pack_peek",
    "script_get", "av_sync_check", "av_sync_scan", "hg_status",
    "hg_library", "feed", "pineline_diagnose_job", "pineline_running_check",
    "pineline_queue_status", "pineline_config_get",
)


def _mutating_steps(steps: list) -> list[dict]:
    """Step thật sự đổi trạng thái (theo agent_core.tool_tier; fallback danh sách read)."""
    out = []
    try:
        from agents.agent_core import tool_tier as _tier
    except Exception:  # noqa: BLE001
        _tier = None
    for st in steps or []:
        if not isinstance(st, dict) or not st.get("tool"):
            continue
        name = str(st["tool"])
        if _tier is not None:
            try:
                if _tier(name, st.get("args") or {}) == "read":
                    continue
            except Exception:  # noqa: BLE001
                if name in _READ_TOOLS:
                    continue
        elif name in _READ_TOOLS:
            continue
        prev = str(st.get("result_preview") or "")
        if '"mutate_blocked"' in prev or '"awaiting_user_confirm"' in prev:
            continue
        out.append(st)
    return out


def _deep_focus(snap: dict, work_root: Path) -> tuple[list[str], dict[str, dict]]:
    """Job cần LLM deep tick: failed / held / high_open (chưa qua TTS) / pending / pre_tts có high.
    BỎ QUA job running khoẻ và job đã xong (fix 2026-11-12 agentG)."""
    focus: list[str] = []
    meta_all: dict[str, dict] = {}
    for j in snap.get("jobs_focus") or []:
        if not isinstance(j, dict) or j.get("stop"):
            continue
        jid = str(j.get("job_id") or "")
        if not jid:
            continue
        ag = j.get("agent") or {}
        qb = j.get("qa_brain") or {}
        st = j.get("stages") or {}
        phase = (j.get("phase") or {}).get("name") or ""
        failed = [s for s in st if str((st.get(s) or {}).get("status") or "") == "failed"]
        running = any(str((st.get(s) or {}).get("status") or "") == "running" for s in st)
        # fix 2026-11-13 agentN: chỉ HOLD CẦN NGƯỜI mới đáng deep tick — park tự động (waiting_review /
        # split_parts) tự thử lại, không DECISION; job đã xong → bỏ hẳn (kể cả stage failed cũ).
        try:
            from agents.agent_hold import AUTO_PARK, hold_class
            hcls = hold_class({"hold_after_translate": ag.get("hold"), "qa_blocked": ag.get("qa_blocked"),
                               "held_at": ag.get("held_at")})
        except Exception:  # noqa: BLE001
            hcls, AUTO_PARK = ("other" if (ag.get("qa_blocked") or ag.get("hold") or ag.get("held_at")) else None), ()
        held = bool(hcls) and hcls not in AUTO_PARK
        try:
            high = int(qb.get("high_open") or 0)
        except (TypeError, ValueError):
            high = 0
        finished = job_finished(Path(work_root) / jid)
        if finished:
            continue
        want = bool(failed) or held or (high > 0 and phase not in ("post_tts",))
        if want:
            focus.append(jid)
            meta_all[jid] = {
                "phase": phase, "high": high, "failed": failed,
                "running": running, "held": held,
                "err": {s: str((st.get(s) or {}).get("error") or "")[:80] for s in failed},
            }
    return focus, meta_all


def _deep_sig(meta: dict) -> str:
    core = {
        "failed": sorted(meta.get("failed") or []),
        "err": meta.get("err") or {},
        "high": meta.get("high"),
        "held": meta.get("held"),
        "phase": meta.get("phase"),
        "pending": bool(meta.get("pending")),
    }
    return hashlib.sha1(json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _deep_cooldown_sec() -> int:
    try:
        return max(300, int(_cfg("AGENT_BRAIN_DEEP_SEC", "1800") or "1800"))
    except ValueError:
        return 1800


def _decision_dedupe_sec() -> int:
    try:
        return max(60, int(_cfg("AGENT_BRAIN_DECISION_DEDUP_SEC", "1800") or "1800"))
    except ValueError:
        return 1800


def _job_evidence(jid: str, work_root: Path | None = None) -> str:
    """Chạy sẵn tool đọc (pineline_job + diagnose + heal plan) → nhét vào prompt.
    Lý do (fix agentG): model qua proxy :3377 nhiều lần trả lời «tool chưa thực thi / chưa có kết quả»
    dù step có chạy — nên đưa bằng chứng thẳng vào message, không phụ thuộc role=tool."""
    parts: list[str] = []
    try:
        import agents.agent_core as agent_core
        if work_root is not None:
            _ensure_brain_runtime(Path(work_root))
        for name, args, lim in (
            ("pineline_job", {"job_id": jid}, 2200),
            ("pineline_diagnose_job", {"job_id": jid}, 1600),
            ("pineline_heal_job", {"job_id": jid, "apply": False, "level": "safe"}, 900),
        ):
            try:
                r = agent_core._dispatch(name, args, allow_mutate=False)
            except Exception as e:  # noqa: BLE001
                r = json.dumps({"error": str(e)[:200]}, ensure_ascii=False)
            if name == "pineline_heal_job":
                try:
                    o = json.loads(r)
                    r = json.dumps({k: o.get(k) for k in ("plan", "blocked_classes", "class_notes", "note")},
                                   ensure_ascii=False)
                except Exception:  # noqa: BLE001
                    pass
            parts.append(f"[{name}({json.dumps(args, ensure_ascii=False)}) → ĐÃ CHẠY]\n{str(r)[:lim]}")
    except Exception as e:  # noqa: BLE001
        parts.append(f"(không lấy được bằng chứng: {e})")
    return "\n".join(parts)


def _log_tool_issues(work_root: Path, jid: str, steps: list) -> None:
    for st in steps or []:
        if not isinstance(st, dict):
            continue
        prev = str(st.get("result_preview") or "")
        if '"error"' in prev or "mutate_blocked" in prev or '"plan": []' in prev or '"results": []' in prev:
            log_line(work_root, f"LLM tool {st.get('tool')} job={jid} result: {prev[:240].replace(chr(10), ' ')}")


def _report_llm_actions(state: dict | None, jid: str, steps: list) -> int:
    """Tool MUTATE thật của LLM → tin mẫu «Brain: <việc> → <kết quả>» (fix 2026-11-12 agentH).
    Văn LLM KHÔNG lên Telegram. Cùng (job, tool, kết quả) chỉ báo lại sau AGENT_BRAIN_REMIND_SEC."""
    from agents.agent_brain_notify import tool_outcome
    n = 0
    for st in _mutating_steps(steps):
        tool = str(st.get("tool") or "")
        args = st.get("args") or {}
        j = str((args.get("job_id") if isinstance(args, dict) else "") or jid or "")
        if not j:
            continue
        out, det = tool_outcome(tool, st.get("result_preview"))
        if tool == "pineline_hold_after_translate" and isinstance(args, dict):
            det = det or ("bật HOLD" if args.get("enabled") else "gỡ HOLD")
        if tool in ("pineline_run_stage", "pineline_run_stage_wait") and isinstance(args, dict):
            from agents.agent_brain_notify import stage_vi
            det = det or stage_vi(str(args.get("stage") or ""))
        if not _once(state, f"llm:{j}:{tool}:{out}:{det}"):
            continue
        _emit(j, "llm_action", level="action", tool=tool, outcome=out, detail=det)
        n += 1
    return n


def _maybe_decision_tg(state: dict, jid: str, meta: dict, steps: list, reply: str) -> bool:
    """DECISION của LLM: CHỈ log (không gửi văn LLM lên Telegram). Có tool mutate → tin mẫu kết quả.
    (fix 2026-11-12 agentH — trước: «Brain DECISION …» + 350 ký tự văn LLM, markdown/bảng/<badge>.)"""
    return _report_llm_actions(state, jid, steps) > 0


def llm_tick(work_root: Path, state: dict | None = None) -> dict:
    """LLM Second Brain: đọc snapshot toàn hệ → tool-loop quyết định."""
    from agents.agent_brain_context import (
        BRAIN_LLM_INSTRUCTION,
        build_system_snapshot,
        snapshot_to_prompt,
    )

    work_root = Path(work_root)
    snap = build_system_snapshot(work_root, max_jobs=30)
    # ghi snapshot để audit
    try:
        sp = _log_dir(work_root) / "agent_brain_last_snapshot.json"
        sp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    # Snapshot ngắn: ưu tiên — LLM phải tool-verify rồi mới HOLD/heal
    msg = BRAIN_LLM_INSTRUCTION + snapshot_to_prompt(snap, max_chars=4500)
    if state is None:
        state = {}
    focus, focus_meta = _deep_focus(snap, work_root)
    # phiếu rule path để lại (bỏ job đã xong)
    try:
        pend = _log_dir(work_root) / "brain_pending_decisions.json"
        if pend.is_file():
            for it in json.loads(pend.read_text(encoding="utf-8")) or []:
                if isinstance(it, dict) and it.get("job_id"):
                    jid = str(it["job_id"])
                    if job_finished(work_root / jid):
                        continue
                    if jid not in focus:
                        focus.append(jid)
                    focus_meta.setdefault(jid, {})["pending"] = it
    except Exception:  # noqa: BLE001
        pass
    if focus:
        lines = []
        for jid in focus[:8]:
            meta = focus_meta.get(jid) or {}
            lines.append(
                f"- {jid}: phase={meta.get('phase')} high={meta.get('high')} "
                f"failed={meta.get('failed')} running={meta.get('running')}"
            )
        msg += (
            "\n\nJOB CẦN QUYẾT ĐỊNH (bắt buộc pineline_job từng cái → DECISION):\n"
            + "\n".join(lines)
        )
    log_line(work_root, "LLM tick start (agent_core tool-loop)")
    max_steps = int(_cfg("AGENT_BRAIN_LLM_STEPS", "12") or "12")
    out = _run_agent_llm(
        msg,
        work_root=work_root,
        max_steps=max_steps,
        force_first_tool="pineline_status",
    )
    # Ép deep tick: mỗi job ưu tiên phải được agent đọc + quyết
    steps0 = list(out.get("steps") or []) if out.get("ok") else []
    first_steps = list(steps0)
    tools0 = [s.get("tool") for s in steps0 if isinstance(s, dict)]
    deep_sigs = state.setdefault("deep_sig", {})
    deep_jobs = []
    for jid0 in focus:
        meta = focus_meta.get(str(jid0)) or {}
        sig = _deep_sig(meta)
        prev = deep_sigs.get(jid0) or {}
        if prev.get("sig") == sig and time.time() - float(prev.get("at") or 0) < _deep_cooldown_sec():
            continue  # cùng tình trạng đã quyết gần đây → không đốt LLM lại
        deep_jobs.append(jid0)
        if len(deep_jobs) >= 3:
            break
    for jid0 in deep_jobs:
        meta = focus_meta.get(str(jid0)) or {}
        deep_sigs[jid0] = {"sig": _deep_sig(meta), "at": time.time()}
        evidence = _job_evidence(str(jid0), work_root)
        follow = (
            f"SECOND BRAIN — quyết định THẬT cho job {jid0}. allow_mutate=ON.\n"
            f"Meta: {json.dumps(meta, ensure_ascii=False)}\n"
            f"BẰNG CHỨNG — các tool đọc dưới đây ĐÃ CHẠY THẬT, kết quả nằm ngay đây "
            f"(không được nói «chưa thực thi / chưa có kết quả»):\n{evidence}\n\n"
            f"Tên tool CHÍNH XÁC: pineline_job, pineline_diagnose_job, pineline_heal_job, "
            f"pineline_hold_after_translate.\n"
            f"1) Nếu failed/treo: pineline_heal_job apply=true level=safe CHỈ khi plan safe ở trên KHÔNG rỗng. "
            f"Plan safe rỗng (vd ASR failed chỉ có asr_run_force = aggressive) → KHÔNG heal, ghi plan=báo user.\n"
            f"2) Chỉ pineline_hold_after_translate enabled=true nếu phase=pre_tts và còn high. mid_review → CẤM HOLD.\n"
            f"3) Nếu high=0 mà đang HOLD qa_brain → enabled=false (CLEAR).\n"
            f"Kết thúc 1 khối: DECISION {jid0}: stage=… | ok=… | issue=… | plan=… | tools=…"
        )
        log_line(work_root, f"LLM deep tick job={jid0} phase={meta.get('phase')}")
        out2 = _run_agent_llm(
            follow,
            work_root=work_root,
            max_steps=max(8, max_steps // 2),
            force_first_tool="pineline_job",
            force_first_args={"job_id": str(jid0)},
        )
        if out2.get("ok"):
            steps0 = steps0 + list(out2.get("steps") or [])
            out = {
                **out2,
                "steps": steps0,
                "via": f"{out.get('via')}+deep:{jid0}",
                "reply": ((out.get("reply") or "") + "\n" + (out2.get("reply") or "")).strip(),
            }
            tools0 = [s.get("tool") for s in steps0 if isinstance(s, dict)]
            reply2 = (out2.get("reply") or "").strip()
            steps2 = list(out2.get("steps") or [])
            _log_tool_issues(work_root, str(jid0), steps2)
            if reply2:
                log_line(work_root, "LLM DECISION " + str(jid0) + ": " + " ".join(reply2.split())[:400])
            _maybe_decision_tg(state, str(jid0), meta, steps2, reply2)
            # xóa pending đã xử lý
            try:
                pend = _log_dir(work_root) / "brain_pending_decisions.json"
                if pend.is_file():
                    items = [
                        x for x in (json.loads(pend.read_text(encoding="utf-8")) or [])
                        if not (isinstance(x, dict) and x.get("job_id") == jid0)
                    ]
                    pend.write_text(
                        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            except Exception:  # noqa: BLE001
                pass
        else:
            log_line(work_root, f"LLM deep tick fail {jid0}: {out2.get('error')}")

    if out.get("ok"):
        steps = out.get("steps") or []
        tools = [s.get("tool") for s in steps if isinstance(s, dict)]
        log_line(
            work_root,
            f"LLM tick ok via={out.get('via')} model={out.get('model')} "
            f"tools={tools[:12]}",
        )
        reply = (out.get("reply") or "")[:500]
        if reply:
            log_line(work_root, f"LLM reply: {reply.replace(chr(10), ' | ')}")
        # tool mutate của lượt chính (deep tick đã báo riêng ở trên)
        _report_llm_actions(state, "", first_steps)
    else:
        log_line(work_root, f"LLM tick fail: {out.get('error')}")
    return out



def _tg_report_sec() -> int:
    """Chu kỳ digest — tối thiểu 600 s (fix 2026-11-12 agentG: trước 120 s = spam)."""
    try:
        return max(600, int(_cfg("AGENT_BRAIN_TG_SEC", "600") or "600"))
    except ValueError:
        return 600


def _digest_sig(msg: str) -> str:
    """Chữ ký nội dung digest bỏ % / số / cur_text đổi liên tục → chỉ đổi khi tập job/stage đổi."""
    lines = []
    for ln in (msg or "").splitlines():
        m = re.match(r"\s*•\s*(\S+)\s*(\[[^\]]*\]|fail:\S+|HOLD)?", ln)
        if m:
            lines.append(f"{m.group(1)} {m.group(2) or ''}")
        elif not ln.startswith("queue"):
            lines.append(re.sub(r"\d+", "#", ln))
    return hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def _progress_digest(work_root: Path) -> str:
    """Tóm tắt ngắn running/queue cho Telegram."""
    lines: list[str] = []
    running: list[str] = []
    failed: list[str] = []
    held: list[str] = []
    try:
        import urllib.request
        with urllib.request.urlopen(_self_base() + "/api/jobs", timeout=8) as resp:
            jobs = json.loads(resp.read().decode("utf-8", "replace"))
        if not isinstance(jobs, list):
            jobs = []
    except Exception as e:  # noqa: BLE001
        return f"Brain: không đọc được /api/jobs ({e})"

    for j in jobs:
        if not isinstance(j, dict):
            continue
        jid = str(j.get("job_id") or "")[:12]
        title = (j.get("title") or "")[:28]
        stages = j.get("stages") or {}
        run_st = [
            k for k, st in stages.items()
            if isinstance(st, dict) and st.get("status") == "running"
        ]
        fail_st = [
            k for k, st in stages.items()
            if isinstance(st, dict) and st.get("status") == "failed"
        ]
        agent = j.get("agent") or {}
        if run_st:
            cur = ""
            st0 = stages.get(run_st[0]) or {}
            if isinstance(st0, dict):
                cur = (st0.get("cur_text") or "")[:40]
                pct = st0.get("progress")
                if pct is not None:
                    cur = f"{pct}% {cur}".strip()
            running.append(f"• {jid} [{'/'.join(run_st)}] {title} {cur}".strip())
        if fail_st:
            failed.append(f"• {jid} fail:{'/'.join(fail_st[:3])} {title}".strip())
        if agent.get("hold_after_translate") or agent.get("qa_blocked"):
            held.append(f"• {jid} HOLD {title}".strip())

    qnote = ""
    try:
        import urllib.request
        with urllib.request.urlopen(_self_base() + "/api/queue/jobs", timeout=5) as resp:
            q = json.loads(resp.read().decode("utf-8", "replace"))
        if isinstance(q, dict):
            n = len(q.get("jobs") or q.get("queue") or [])
            qnote = f"queue≈{n}"
        elif isinstance(q, list):
            qnote = f"queue≈{len(q)}"
    except Exception:  # noqa: BLE001
        pass

    lines.append("🧠 Brain báo cáo")
    if qnote:
        lines.append(qnote)
    if running:
        lines.append("RUNNING:")
        lines.extend(running[:8])
    else:
        lines.append("RUNNING: (không)")
    if failed:
        lines.append("FAILED:")
        lines.extend(failed[:5])
    if held:
        lines.append("HOLD:")
        lines.extend(held[:5])
    return "\n".join(lines)


def _maybe_tg_progress(work_root: Path, state: dict) -> None:
    mode = _tg_mode()
    if mode not in ("all", "report", "digest", "progress", "on"):
        return
    last = float(state.get("last_tg_report") or 0)
    if time.time() - last < _tg_report_sec():
        return
    msg = _progress_digest(work_root)
    state["last_tg_report"] = time.time()
    sig = _digest_sig(msg)
    if sig == state.get("last_tg_report_sig"):
        log_line(work_root, "tg progress report skip (không đổi)")
        return
    state["last_tg_report_sig"] = sig
    _telegram(msg, level="progress")
    log_line(work_root, "tg progress report sent")


def tick(
    work_root: Path,
    *,
    hooks: dict[str, Callable[..., Any]] | None = None,
    list_jobs: Callable[[], list] | None = None,  # noqa: ARG001 — reserved
) -> dict:
    work_root = Path(work_root)
    state = _load_state(work_root)
    _tg_begin_tick(work_root)
    try:
        return _tick_inner(work_root, state, hooks=hooks, list_jobs=list_jobs)
    finally:
        try:
            _tg_flush_tick()
        except Exception as e:  # noqa: BLE001
            log_line(work_root, f"tg flush lỗi: {e}")


def _tick_inner(
    work_root: Path,
    state: dict,
    *,
    hooks: dict[str, Callable[..., Any]] | None = None,
    list_jobs: Callable[[], list] | None = None,
) -> dict:
    # 0) Gỡ HOLD cũ của job đã xong + sự kiện stage (bắt đầu/xong/lỗi) — tất định, không LLM
    try:
        clear_stale_holds(work_root, state)
    except Exception as e:  # noqa: BLE001
        log_line(work_root, f"clear stale holds lỗi: {e}")
    try:
        emit_events(detect_stage_events(work_root, state))
    except Exception as e:  # noqa: BLE001
        log_line(work_root, f"detect events lỗi: {e}")
    # fix 2026-11-12 agentH (C): hoãn heal + LLM ≥ AGENT_HEAL_START_DELAY_SEC sau khi brain khởi động
    grace = _start_grace_left()
    incidents: list[dict] = []
    incidents.extend(sense_infra())
    # Nhường GPU khi queue bận — vẫn chạy quality nhẹ? Plan: giống watch skip heal nặng
    busy = _gpu_or_queue_busy(work_root)
    mode = (_cfg("AGENT_BRAIN_MODE", "hybrid") or "hybrid").lower()
    run_rules = mode in ("hybrid", "rules", "rule", "agent", "llm")
    # llm-only: vẫn giữ quality scan nhẹ làm safety net
    if busy:
        # Vẫn quality_scan jobs gần đây (deterministic, không LLM)
        incidents.extend(sense_jobs(work_root, max_jobs=15))
        # Lọc bỏ heal_job khi busy
        actions = [a for a in decide(incidents, state) if a.get("type") != "heal_job"]
        log_line(work_root, f"tick busy — skip L1 heal; actions={len(actions)}")
    else:
        # L1 từ agent_heal.scan nếu có
        try:
            from agents.agent_heal import scan_problem_jobs
            scan = scan_problem_jobs(work_root, list_jobs=list_jobs)
            for p in (scan.get("problems") or [])[:10]:
                incidents.append({
                    "layer": "l1",
                    "kind": "stage_failed",
                    "job_id": p.get("job_id"),
                    "stage": ",".join(p.get("failed_stages") or []) or "unknown",
                    "detail": ",".join(p.get("issue_codes") or [])[:120],
                })
        except Exception as e:  # noqa: BLE001
            log_line(work_root, f"scan_problem_jobs: {e}")
        incidents.extend(sense_jobs(work_root, max_jobs=40))
        actions = decide(incidents, state)

    if grace > 0:
        n0 = len(actions)
        actions = [a for a in actions if a.get("type") != "heal_job"]
        if n0 != len(actions):
            log_line(work_root, f"hoãn heal — vừa khởi động (còn {int(grace)} s)")
    results = []
    if run_rules:
        results = apply_actions(work_root, actions, state, hooks=hooks)
    # Gửi ngay sự kiện + hành động rule path (LLM tick có thể mất vài phút)
    _tg_flush_tick()
    _tg_begin_tick(work_root)

    llm_out = None
    if _llm_enabled() and grace <= 0:
        last = float(state.get("last_llm_tick") or 0)
        if time.time() - last >= _llm_interval_sec():
            try:
                llm_out = llm_tick(work_root, state)
                state["last_llm_tick"] = time.time()
            except Exception as e:  # noqa: BLE001
                log_line(work_root, f"llm_tick exception: {e}")
                llm_out = {"ok": False, "error": str(e)}

    try:
        _maybe_tg_progress(work_root, state)
    except Exception as e:  # noqa: BLE001
        log_line(work_root, f"tg progress skip: {e}")
    state["last_tick"] = time.time()
    state["last_n_incidents"] = len(incidents)
    state["last_n_actions"] = len(actions)
    state["mode"] = mode
    _save_state(work_root, state)
    return {
        "ok": True,
        "mode": mode,
        "n_incidents": len(incidents),
        "n_actions": len(actions),
        "n_results": len(results),
        "busy": busy,
        "results": results,
        "llm": llm_out,
    }


_started_at: float | None = None


def _start_grace_left(now: float | None = None) -> float:
    """Số giây còn phải hoãn heal/LLM sau khi brain loop khởi động (0 = hết hoãn / brain_once)."""
    if _started_at is None:
        return 0.0
    try:
        from agents.agent_heal import start_delay_sec
        delay = start_delay_sec()
    except Exception:  # noqa: BLE001
        delay = 180
    return max(0.0, _started_at + delay - (now if now is not None else time.time()))


def _loop(work_root: Path, hooks, list_jobs, interval: int) -> None:
    global _started_at
    _started_at = time.time()
    log_line(work_root, f"brain start interval={interval}s policy=full_silent tg={_tg_mode()} "
                        f"heal_delay={int(_start_grace_left())}s")
    while not _stop.is_set():
        pause = Path(work_root) / "logs" / "brain_pause"
        if pause.exists() or not brain_enabled():
            log_line(work_root, "brain paused (brain_pause hoặc AGENT_BRAIN=0) — skip tick")
            _stop.wait(interval)
            continue
        try:
            tick(work_root, hooks=hooks, list_jobs=list_jobs)
        except Exception as e:  # noqa: BLE001
            log_line(work_root, f"tick lỗi: {e}")
        _stop.wait(interval)
    log_line(work_root, "brain stop")


def start_brain(
    work_root: Path,
    *,
    hooks: dict[str, Callable[..., Any]] | None = None,
    list_jobs: Callable[[], list] | None = None,
) -> bool:
    global _thread
    if not brain_enabled():
        return False
    interval = _interval_sec()
    with _lock:
        if _thread and _thread.is_alive():
            return True
        _stop.clear()
        _thread = threading.Thread(
            target=_loop,
            args=(Path(work_root), hooks or {}, list_jobs, interval),
            name="agent_brain",
            daemon=True,
        )
        _thread.start()
    return True


def stop_brain() -> None:
    _stop.set()


def brain_once(
    work_root: Path,
    *,
    hooks: dict[str, Callable[..., Any]] | None = None,
    list_jobs: Callable[[], list] | None = None,
) -> dict:
    """API/ops: chạy 1 tick thủ công."""
    return tick(Path(work_root), hooks=hooks, list_jobs=list_jobs)
