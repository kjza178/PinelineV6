"""
agent_hold.py — gỡ HOLD khi người dùng CHẠY TAY 1 bước + chặn brain/auto đặt lại HOLD (fix 2026-11-12 agentH).

Bối cảnh: HOLD (agent.hold_after_translate / qa_blocked / held_at) chặn TTS/mux trong luồng TỰ ĐỘNG.
Khi người dùng bấm chạy tay (/api/jobs/{id}/run/{stage}, /tts, /mux, /translate, /translate-fill)
thì người bấm = override → gỡ cờ ngay (trước đây cờ ở lại → brain/digest báo HOLD mãi, job xong
vẫn «HOLD»), ghi agent.hold_override = {by:"manual", stage, at, was, high, high_i}.

Brain/auto (server._auto_gpu HOLD qa_brain, tool pineline_hold_after_translate của LLM) KHÔNG được
đặt lại HOLD cho job đã override, trừ khi qa_brain.json có high MỚI sau lần chạy tay đó.

Mọi ghi manifest qua state/job_state (Manifest.update — khoá thread + liên tiến trình).
V6 (port P6 2026-11-12): package agents/, ghi qua js.try_mutate; ghi xong state.index phát `job.updated`
(SSE /api/events) → UI thấy cờ HOLD đổi, không cần polling.

fix 2026-11-12 agentI2: lệnh NGƯỜI DÙNG đi qua AI Agent (Telegram agent_tele_cmd gửi header
X-Pineline-Caller: user + X-Pineline-Source: telegram tới /api/agent/chat, /api/agent/confirm; UI chat
Agent) chạy tool in-process (request=None) trong `user_context(...)` → cũng tính là chạy tay.
Brain (`auto_context("brain")`, header `brain`) và heal/av-sync hook (`auto_context("heal")`) luôn thắng
→ KHÔNG tính tay, kể cả khi lồng trong user_context.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOLD_KEYS = ("qa_blocked", "hold_after_translate", "held_at")

_ctx = threading.local()


def is_held(agent: dict | None) -> bool:
    agent = agent or {}
    return any(agent.get(k) for k in HOLD_KEYS)


# ---------------------------------------------------------------------------
# fix 2026-11-13 agentN: MỘT định nghĩa «job đã xong» cho heal/watch/brain/LLM + phân loại tạm dừng
# ---------------------------------------------------------------------------
_DONE_ST = ("done", "ok", "completed")
WIRED_STAGES = ("download", "asr", "translate", "tts", "mux")
UPLOADED_STATUSES = frozenset({"uploaded", "published", "public", "success", "done"})
_PART_RE = re.compile(r"^(.+)_p(\d{1,3})$", re.I)
# Khoá «park» (tự thử lại) — gỡ cùng HOLD khi người chạy tay / Chạy tiếp / job xong.
PARK_KEYS = ("review_retries", "review_retry_at", "simple_park", "split_wait_parts")


def _norm_st(v: Any) -> str:
    s = str(v or "").strip().lower()
    return "done" if s in _DONE_ST else s


def _read_mf(work_dir: Path) -> dict | None:
    try:
        d = json.loads((Path(work_dir) / "manifest.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _output_ok(work_dir: Path) -> bool:
    for name in ("output.mp4", "output_full.mp4"):
        try:
            p = Path(work_dir) / name
            if p.is_file() and p.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


def merged_full(data: dict | None) -> bool:
    """Parent split đã ghép bản Full (split_info.status=merged hoặc mux.is_full_merged) + mux done."""
    data = data or {}
    mux = ((data.get("stages") or {}).get("mux") or {}) if isinstance(data.get("stages"), dict) else {}
    if _norm_st(mux.get("status")) != "done":
        return False
    si = data.get("split_info") if isinstance(data.get("split_info"), dict) else {}
    return bool(mux.get("is_full_merged") or str(si.get("status") or "").lower() == "merged")


def job_finished(work_dir: Path, data: dict | None = None, *, check_parent: bool = True) -> bool:
    """Job ĐÃ XONG — heal/watch/brain/LLM bỏ qua, không DECISION, không báo «heal thất bại».

    True khi KHÔNG stage nào running VÀ một trong:
      * upload_status ∈ UPLOADED_STATUSES (đã đăng — kể cả script/tts đã dọn);
      * mux done + output.mp4 / output_full.mp4 (không bị đánh stale translate/tts/mux);
      * parent split đã ghép Full (merged_full);
      * mọi stage wired done (ngữ nghĩa cũ agent_brain.job_finished);
      * part «<parent>_pNN» mà parent đã ghép Full / đã đăng.
    """
    work_dir = Path(work_dir)
    if data is None:
        data = _read_mf(work_dir) or {}
    stages = data.get("stages") if isinstance(data.get("stages"), dict) else {}
    sts = {k: _norm_st((v or {}).get("status")) for k, v in stages.items() if isinstance(v, dict)}
    if any(v == "running" for v in sts.values()):
        return False
    if str(data.get("upload_status") or "").strip().lower() in UPLOADED_STATUSES:
        return True
    stale = any(isinstance(stages.get(s), dict) and stages[s].get("stale") for s in ("translate", "tts", "mux"))
    if not stale:
        if sts.get("mux") == "done" and (_output_ok(work_dir) or merged_full(data)):
            return True
        if sts and all(sts.get(s) == "done" for s in WIRED_STAGES):
            return True
    if check_parent:
        m = _PART_RE.match(work_dir.name)
        if m and not stale:
            pwd = work_dir.parent / m.group(1)
            pdata = _read_mf(pwd)
            if pdata and (merged_full(pdata) or str(pdata.get("upload_status") or "").strip().lower()
                          in UPLOADED_STATUSES) and job_finished(pwd, pdata, check_parent=False):
                return True
    return False


# Loại tạm dừng (bảng đầy đủ: docs/fixes/2026-11-13-brain-finished-hold.md)
HOLD_PARK = "park"                 # (a) park tự động simple/B2/fill_pending: held_at=waiting_review
HOLD_SPLIT_WAIT = "split_wait"     # (d) parent chờ part park: held_at=split_parts
HOLD_REVIEW_FAILED = "review_failed"   # (b) hết lượt thử lại → cần người
HOLD_QA = "qa"                     # (c) qa_brain / qa_gate / simple_gate / qa_blocked
HOLD_AFTER_TRANSLATE = "after_translate"   # (c)/(e) hold_after_translate bật tay/LLM/UI
HOLD_OTHER = "other"
AUTO_PARK = frozenset({HOLD_PARK, HOLD_SPLIT_WAIT})

NEED_BY_HOLD = {
    HOLD_REVIEW_FAILED: "xem các câu chưa soát/dịch rồi bấm Chạy tiếp.",
    HOLD_QA: "duyệt bản dịch rồi bấm Chạy tiếp (hoặc chạy tay Lồng tiếng).",
    HOLD_AFTER_TRANSLATE: "duyệt bản dịch rồi bấm Chạy tiếp (hoặc chạy tay Lồng tiếng).",
    HOLD_OTHER: "xem job rồi bấm Chạy tiếp.",
}


def hold_class(agent: dict | None) -> str | None:
    """None = không tạm dừng. park/split_wait = TỰ chạy lại (không cần người). Còn lại = HOLD cần người.
    Thứ tự khớp server._queue_park_ready: cờ cứng (hold_after_translate/qa_blocked) thắng held_at."""
    a = agent or {}
    if not is_held(a):
        return None
    held = str(a.get("held_at") or "").strip()
    if held == "review_failed":
        return HOLD_REVIEW_FAILED
    if held in ("qa_brain", "qa_gate", "simple_gate") or a.get("qa_blocked"):
        return HOLD_QA
    if a.get("hold_after_translate") or held == "after_translate":
        return HOLD_AFTER_TRANSLATE
    if held == "waiting_review":
        return HOLD_PARK
    if held == "split_parts":
        return HOLD_SPLIT_WAIT
    return HOLD_OTHER


def review_retry_max() -> int:
    try:
        return int(os.getenv("QUEUE_REVIEW_RETRY_MAX", "3") or 3)
    except ValueError:
        return 3


_N_LINES_RE = re.compile(r"(?:còn|thiếu)\s+(\d+)\s+câu", re.I)


def hold_info(agent: dict | None) -> dict:
    """Mô tả tạm dừng cho tin Telegram / snapshot LLM."""
    a = agent or {}
    cls = hold_class(a)
    reason = str(a.get("hold_reason") or a.get("held_at") or "")
    m = _N_LINES_RE.search(reason)
    try:
        k = int(a.get("review_retries") or 0)
    except (TypeError, ValueError):
        k = 0
    return {
        "cls": cls,
        "auto": cls in AUTO_PARK,
        "need_human": bool(cls) and cls not in AUTO_PARK,
        "reason": reason[:300],
        "n_lines": int(m.group(1)) if m else None,
        "retry_at": a.get("review_retry_at"),
        "try": k,
        "max": review_retry_max(),
        "parts": [str(x) for x in (a.get("split_wait_parts") or [])][:8],
    }


def _qa_high(work_dir: Path) -> tuple[int, list[int]]:
    try:
        d = json.loads((Path(work_dir) / "qa_brain.json").read_text(encoding="utf-8"))
        hi = [int(x) for x in (d.get("high_i") or []) if str(x).lstrip("-").isdigit()]
        return int(d.get("high_open") or len(hi) or 0), hi
    except Exception:  # noqa: BLE001
        return 0, []


# ---------------------------------------------------------------------------
# Ngữ cảnh «tự động» (brain/agent) — để phân biệt với người bấm tay
# ---------------------------------------------------------------------------
class auto_context:
    """with auto_context("brain"): ... — trong khối này set_hold coi là của brain/auto."""

    def __init__(self, who: str = "brain") -> None:
        self.who = who
        self._prev = None

    def __enter__(self):
        self._prev = getattr(_ctx, "who", None)
        _ctx.who = self.who
        return self

    def __exit__(self, *exc):
        _ctx.who = self._prev
        return False


def auto_caller() -> str | None:
    return getattr(_ctx, "who", None)


class user_context:
    """with user_context("agent_chat:telegram"): ... — tool chạy in-process do NGƯỜI ra lệnh (route
    /api/agent/chat|confirm nhận request tay). auto_context (brain/heal) bên trong vẫn thắng."""

    def __init__(self, source: str = "user") -> None:
        self.source = str(source or "user")
        self._prev = None

    def __enter__(self):
        self._prev = getattr(_ctx, "user", None)
        _ctx.user = self.source
        return self

    def __exit__(self, *exc):
        _ctx.user = self._prev
        return False


def user_caller() -> str | None:
    return getattr(_ctx, "user", None)


def request_source(request: Any) -> str:
    """Nhãn nguồn của request tay: header X-Pineline-Source (vd telegram) hoặc «ui»."""
    try:
        s = str(request.headers.get("x-pineline-source") or "").strip().lower()
    except Exception:  # noqa: BLE001
        s = ""
    s = "".join(ch for ch in s if ch.isalnum() or ch in "_-")[:24]
    return s or "ui"


def manual_source(request: Any = None) -> str | None:
    """Nguồn «chạy tay» của lệnh hiện tại, None = tự động.
    - Trong auto_context (brain/heal) → None (luôn thắng).
    - Có request HTTP → is_manual_request (UI/WebView, hoặc header X-Pineline-Caller: user|ui|manual).
    - request=None (gọi Python trực tiếp) → chỉ tay khi đang trong user_context (route Agent nhận lệnh người)."""
    if auto_caller():
        return None
    if request is not None:
        return ("http:" + request_source(request)) if is_manual_request(request) else None
    return user_caller()


def is_manual_request(request: Any) -> bool:
    """Lệnh HTTP từ người dùng (UI/desktop). Gọi Python trực tiếp (request=None: agent/queue/heal hook)
    hoặc HTTP có header X-Pineline-Caller (brain/agent fallback HTTP) → KHÔNG phải chạy tay."""
    if request is None:
        return False
    try:
        h = request.headers
        caller = str(h.get("x-pineline-caller") or "").strip().lower()
        if caller:
            return caller in ("ui", "manual", "user")
        ua = str(h.get("user-agent") or "").lower()
        if ua.startswith(("python-urllib", "python-requests", "httpx", "aiohttp")):
            return False
    except Exception:  # noqa: BLE001
        return False
    return True


# ---------------------------------------------------------------------------
# B: gỡ HOLD khi chạy tay
# ---------------------------------------------------------------------------
def manual_override(work_dir: Path, stage: str, *, by: str = "manual", source: str = "",
                    notify: bool = True) -> dict | None:
    """Job đang HOLD → gỡ cờ + ghi agent.hold_override. Trả dict override (None nếu không HOLD/không ghi)."""
    work_dir = Path(work_dir)
    if not (work_dir / "manifest.json").is_file():
        return None
    high, high_i = _qa_high(work_dir)
    box: dict = {}

    def _fn(dd: dict) -> bool | None:
        a = dict(dd.get("agent") or {})
        if not is_held(a):
            return False
        was = {k: a.get(k) for k in HOLD_KEYS + ("hold_reason",) if a.get(k)}
        a["qa_blocked"] = False
        a["hold_after_translate"] = False
        a.pop("held_at", None)
        a.pop("hold_reason", None)
        for k in PARK_KEYS:   # fix 2026-11-13 agentN: người chạy tay → bộ đếm park/review_failed về 0
            a.pop(k, None)
        ov = {
            "by": by,
            "stage": stage,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ts": time.time(),
            "source": source,
            "was": was,
            "high": high,
            "high_i": high_i[:200],
        }
        a["hold_override"] = ov
        dd["agent"] = a
        tr = (dd.get("stages") or {}).get("translate")
        if isinstance(tr, dict) and str(tr.get("cur_text") or "").startswith("HOLD"):
            tr["cur_text"] = f"HOLD đã gỡ (bạn chạy tay {stage})"
        box["ov"] = ov
        box["data"] = dd
        return None

    try:
        from state import job_state as js
        ok = js.try_mutate(work_dir, _fn)
    except Exception as e:  # noqa: BLE001
        print(f"[hold] override {work_dir.name} lỗi: {e}", flush=True)
        return None
    if not ok or "ov" not in box:
        return None
    ov = box["ov"]
    _log(work_dir, f"HOLD override {work_dir.name}: gỡ HOLD vì chạy tay {stage} "
                   f"(source={source or '-'} was={json.dumps(ov.get('was'), ensure_ascii=False)[:160]})")
    if notify:
        _notify_override(work_dir, stage, box.get("data"))
    return ov


def maybe_manual_override(request: Any, work_dir: Path, stage: str, source: str = "") -> dict | None:
    """Gọi từ route: chỉ gỡ khi là lệnh tay của người dùng (HTTP tay, hoặc tool Agent trong user_context)."""
    src = manual_source(request)
    if not src:
        return None
    if request is None:   # agentI2: lệnh người qua Agent (Telegram/UI chat) → ghi rõ nguồn
        source = f"{source}+{src}" if source else src
    return manual_override(work_dir, stage, source=source)


def hold_suppressed(work_dir: Path, agent: dict | None = None, *, high_now: int | None = None,
                    high_i_now: list[int] | None = None) -> bool:
    """True = KHÔNG được đặt lại HOLD (job đã override tay, chưa có high mới sau lần chạy đó)."""
    work_dir = Path(work_dir)
    if agent is None:
        try:
            agent = (json.loads((work_dir / "manifest.json").read_text(encoding="utf-8")) or {}).get("agent") or {}
        except Exception:  # noqa: BLE001
            agent = {}
    ov = (agent or {}).get("hold_override")
    if not isinstance(ov, dict) or ov.get("by") != "manual":
        return False
    if high_now is None or high_i_now is None:
        h, hi = _qa_high(work_dir)
        high_now = h if high_now is None else high_now
        high_i_now = hi if high_i_now is None else high_i_now
    try:
        high_then = int(ov.get("high") or 0)
    except (TypeError, ValueError):
        high_then = 0
    then_i = set(int(x) for x in (ov.get("high_i") or []) if str(x).lstrip("-").isdigit())
    new_i = [i for i in (high_i_now or []) if i not in then_i]
    if int(high_now or 0) > high_then or (new_i and then_i):
        return False   # có high mới sau lần chạy tay → cho HOLD lại
    return True


# ---------------------------------------------------------------------------
def _log(work_dir: Path, msg: str) -> None:
    try:
        logs = Path(work_dir).parent / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with (logs / "agent_brain.log").open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass
    print(f"[hold] {msg}", flush=True)


def render_override_msg(work_dir: Path, stage: str, data: dict | None = None) -> str:
    from agents.agent_brain_notify import render_job, split_part
    work_dir = Path(work_dir)
    jid = work_dir.name
    base, part = split_part(jid)
    parent = None
    if part is not None:
        try:
            parent = json.loads((work_dir.parent / base / "manifest.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            parent = None
    try:
        from agents.agent_brain import _tz
        tz = _tz()
    except Exception:  # noqa: BLE001
        tz = None
    return render_job(jid, data, [{"kind": "hold_cleared", "why": "manual", "stage": stage}],
                      parent=parent, tz=tz)


def _notify_override(work_dir: Path, stage: str, data: dict | None) -> None:
    try:
        from agents.agent_brain import _tg_allowed, _tg_mode
        if not _tg_allowed(_tg_mode(), "action"):
            return
        msg = render_override_msg(work_dir, stage, data)
    except Exception as e:  # noqa: BLE001
        print(f"[hold] render tin lỗi: {e}", flush=True)
        return

    def _send() -> None:
        try:
            from agents.agent_watch import telegram_send
            telegram_send(msg, force=True, dedupe_key=f"hold_override:{Path(work_dir).name}:{stage}:{int(time.time())}")
        except Exception as e:  # noqa: BLE001
            print(f"[hold] tg lỗi: {e}", flush=True)

    threading.Thread(target=_send, name="hold_override_tg", daemon=True).start()
