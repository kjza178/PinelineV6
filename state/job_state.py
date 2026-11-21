"""state/job_state.py — NGUỒN DUY NHẤT ghi trạng thái job (B9).

Mọi chỗ trước đây `m = Manifest(wd); m.data[...] = ...; m.save()` chuyển sang các hàm dưới.
Mỗi hàm = MỘT `Manifest.update(fn)`: đọc lại đĩa → sửa đúng khoá của mình → ghi atomic trong
khoá thread + liên tiến trình. Không còn ghi đè trạng thái của luồng khác (cờ HOLD, queue_opts, tiến độ).

Sau khi ghi: cập nhật JobIndex (bộ nhớ) → phát event cho UI.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterable

from core.manifest import Manifest, register_write_hook
from core.timeutil import now_iso as _iso
from state.index import index as _index


def _index_hook(work_dir: Path, data: dict) -> None:
    # fix 11-12 agentJ: chỉ job thuộc work root instance (index.load_all đã gắn) — ghi ở thư mục khác
    # (tmp test, script, cây khác) không lọt vào danh sách/SSE của instance.
    _index.apply_dir(work_dir, data)


# Mọi lần ghi manifest trong tiến trình này (kể cả qua persist / Manifest.mark_*) → index + event.
register_write_hook(_index_hook)

HOLD_KEYS = ("held_at", "hold_reason", "hold_after_translate", "qa_blocked")


class StateError(RuntimeError):
    """Manifest hỏng / thiếu stages → không ghi (Manifest.update trả False)."""


def _now() -> float:
    return time.time()


def _update(wd: Path | str, fn: Callable[[dict], Any]) -> dict:
    wd = Path(wd)
    m = Manifest(wd)
    declined = []

    def _wrapped(d):
        r = fn(d)
        if r is False:
            declined.append(True)
        return r

    ok = m.update(_wrapped)
    if declined:            # fn chủ động không đổi gì → không phải lỗi
        return m.data
    if not ok:
        raise StateError(f"không ghi được manifest {wd.name} (hỏng / thiếu stages)")
    return m.data


def _stage(data: dict, name: str) -> dict:
    stages = data.setdefault("stages", {})
    st = stages.get(name)
    if not isinstance(st, dict):
        st = stages[name] = {}
    return st


# ───────────── stage ─────────────

def mark_running(wd, stage: str, *, cur_text: str = "đang chạy…", **extra) -> dict:
    def fn(d):
        st = _stage(d, stage)
        fresh = st.get("status") != "running"     # lượt chạy MỚI (không phải gọi lặp khi đang chạy)
        st.update(extra)
        st["status"] = "running"
        st["cur_text"] = cur_text
        if fresh or not st.get("started_at"):
            st["started_at"] = _iso()             # ISO UTC — cùng dạng core.manifest (core/timeutil.py)
            st.pop("finished_at", None)           # mốc xong của lượt trước không còn đúng
        st.pop("error", None)
        st["updated_at"] = _now()
    return _update(wd, fn)


def mark_done(wd, stage: str, **payload) -> dict:
    def fn(d):
        st = _stage(d, stage)
        st.update(payload)
        st["status"] = "done"
        st["finished_at"] = _iso()
        st.pop("error", None)
        st["updated_at"] = _now()
    return _update(wd, fn)


def mark_failed(wd, stage: str, error: BaseException | str) -> dict:
    def fn(d):
        st = _stage(d, stage)
        st["status"] = "failed"
        st["error"] = str(error)[:500]
        st["finished_at"] = _iso()
        st["updated_at"] = _now()
    return _update(wd, fn)


def set_stage(wd, stage: str, **fields) -> dict:
    """Cập nhật vài khoá của 1 stage, KHÔNG đổi status trừ khi truyền status=…"""
    def fn(d):
        st = _stage(d, stage)
        st.update(fields)
        st["updated_at"] = _now()
    return _update(wd, fn)


def set_progress(wd, stage: str, *, publish: Callable[[str, dict], Any] | None = None, **fields) -> dict:
    """Tiến độ (review_done, percent, cur_text…). Ghi đĩa + event `stage.progress` (bus tự throttle)."""
    data = set_stage(wd, stage, **fields)
    if publish is None:
        from events.bus import bus
        publish = bus.publish
    publish("stage.progress", {"job_id": Path(wd).name, "stage": stage, **fields})
    return data


def reset_running(wd, *, note: str = "đứt giữa chừng — bấm Tiếp tục") -> list[str]:
    """Lúc start server: stage còn 'running' là tàn dư → 'pending'."""
    changed: list[str] = []

    def fn(d):
        for name, st in (d.get("stages") or {}).items():
            if isinstance(st, dict) and st.get("status") == "running":
                st["status"] = "pending"
                st["cur_text"] = note
                st.pop("updated_at", None)
                changed.append(name)
        return None if changed else False   # không đổi → không ghi
    try:
        _update(wd, fn)
    except StateError:
        changed.clear()
    return changed


def create_job(wd, *, request: dict | None = None, **top) -> dict:
    """Tạo manifest mới (job mới). Đã có thì không ghi đè."""
    wd = Path(wd)
    wd.mkdir(parents=True, exist_ok=True)
    m = Manifest(wd)
    if not m.path.exists():
        m.data.setdefault("stages", {})
        if request is not None:
            m.data["request"] = request
        m.data.update(top)
        m._commit()  # noqa: SLF001 — state là nơi duy nhất được ghi
    else:
        _index.apply_dir(wd, m.data)
    return m.data


# ───────────── cầu nối cho code V5 chưa tách (đếm bằng test ratchet, chỉ được giảm) ─────────────

def persist(m: Manifest) -> bool:
    """Ghi object Manifest mà code cũ đã sửa trực tiếp `m.data[...]`.

    Cùng ngữ nghĩa Manifest.save() cũ (gộp 3 chiều trong khoá, chỉ khoá mình đổi thắng) nhưng
    đi qua state → có index + event, và đếm được. Chỗ nào có hàm ngữ nghĩa (set_hold, set_queue_opts,
    set_review_retry, mark_*) thì dùng hàm đó thay vì persist.
    """
    return m._commit()  # noqa: SLF001


# ───────────── HOLD / park ─────────────

def set_hold(wd, *, reason: str, detail: str = "", block_tts: bool = True, qa_blocked: bool = False,
             stage_note: str | None = None) -> dict:
    def fn(d):
        ag = d.setdefault("agent", {})
        ag["held_at"] = reason
        ag["hold_reason"] = detail or reason
        if block_tts:
            ag["hold_after_translate"] = True
        if qa_blocked:
            ag["qa_blocked"] = True
        ag["held_ts"] = _now()
        if stage_note:
            _stage(d, "translate")["cur_text"] = stage_note
    return _update(wd, fn)


def clear_hold(wd, *, reasons: Iterable[str] | None = None, override_qa: bool = False) -> dict:
    """Gỡ HOLD. `reasons`: chỉ gỡ nếu held_at thuộc tập này (None = gỡ mọi loại)."""
    allowed = set(reasons) if reasons is not None else None

    def fn(d):
        ag = d.setdefault("agent", {})
        if allowed is not None and ag.get("held_at") not in allowed:
            return False
        for k in HOLD_KEYS + ("held_ts", "review_retries", "review_retry_at"):
            ag.pop(k, None)
        if override_qa:
            ag["qa_override"] = True
    return _update(wd, fn)


def set_review_retry(wd, *, n_unrev: int, retry_sec: float = 300.0, max_tries: int = 3) -> dict:
    """B2: REVIEW còn câu chưa rv → park và hẹn chạy lại; quá max_tries → HOLD review_failed (cần người).

    Khớp nguyên hành vi server.py V5 (đoạn "PARK ghế"): translate → pending, tts đang running → pending.
    """
    out: dict[str, Any] = {}

    def fn(d):
        now = _now()
        tr = _stage(d, "translate")
        tr.update({"status": "pending", "cur_text": f"soát dở — còn {n_unrev} câu chưa rv", "updated_at": now})
        tts = _stage(d, "tts")
        if tts.get("status") == "running":
            tts["status"] = "pending"
            tts["cur_text"] = "chờ soát xong"
        ag = d.setdefault("agent", {})
        ag["held_at"] = "waiting_review"
        ag["hold_reason"] = f"soát dở — còn {n_unrev} câu chưa rv"
        n_try = int(ag.get("review_retries") or 0) + 1
        ag["review_retries"] = n_try
        if n_try > max_tries:
            ag["held_at"] = "review_failed"
            ag["hold_after_translate"] = True
            ag["hold_reason"] = f"REVIEW thất bại {n_try - 1} lượt — còn {n_unrev} câu chưa rv, cần người xem"
            ag.pop("review_retry_at", None)
            out["result"] = "hold"
        else:
            ag["review_retry_at"] = now + retry_sec
            out["result"] = "park"
        out["tries"] = n_try
    _update(wd, fn)
    return out


def park_ready(data: dict, unrev: int, *, now: float | None = None) -> bool:
    """Thuần (không I/O) — job park có chạy lại được chưa.
    translate-simple (port F6 2026-11-12): `agent.simple_park` → chờ tới hạn review_retry_at kể cả unrev=0
    (park simple có thể do câu trống / chữ Hán — không lặp nóng)."""
    ag = data.get("agent") or {}
    if ag.get("hold_after_translate") or ag.get("qa_blocked"):
        return False
    held = str(ag.get("held_at") or "")
    if held == "waiting_review":
        if unrev == 0 and not ag.get("simple_park"):
            return True
        due = float(ag.get("review_retry_at") or 0)
        return bool(due) and (now or _now()) >= due
    return not held


# ───────────── translate-simple: cổng done / park / hold (port V2 2026-11-12 → V6 F6) ─────────────

SIMPLE_HOLD = "simple_gate"


def apply_simple_gate(wd, *, status: str, why: str = "", retry_sec: float = 300.0, max_tries: int = 3) -> dict:
    """Ghi kết quả cổng luồng dịch simple vào manifest.agent — MỘT Manifest.update.

    status (từ engines.translate_simple.gate.decide / pipeline.run):
      done                 → gỡ waiting_review/simple_gate + review_retries/review_retry_at/simple_park → "pass"
      hold + qa_override   → như done (người đã duyệt cho qua) → "pass"
      hold                 → held_at=simple_gate, hold_after_translate (cần người) → "hold"
      park (khác)          → held_at=waiting_review + simple_park + review_retries/review_retry_at (cơ chế B2)
                             → "park"; quá max_tries → held_at=review_failed + hold_after_translate → "hold"
    Trả {"result": "pass"|"park"|"hold", "tries": n}. Khớp nguyên V2 server._auto_translate_simple.
    """
    st = str(status or "park")
    out: dict[str, Any] = {"result": "park", "tries": 0}

    def fn(d):
        ag = dict(d.get("agent") or {})
        if st == "done" or (st == "hold" and ag.get("qa_override")):
            if ag.get("held_at") in ("waiting_review", SIMPLE_HOLD):
                ag.pop("held_at", None)
            for k in ("review_retries", "review_retry_at", "simple_park"):
                ag.pop(k, None)
            hr = str(ag.get("hold_reason") or "").lower()
            if "simple" in hr or "soát" in hr:
                ag.pop("hold_reason", None)
            out["result"] = "pass"
        elif st == "hold":
            ag["held_at"] = SIMPLE_HOLD
            ag["hold_after_translate"] = True
            ag["hold_reason"] = f"HOLD simple — {why}"[:300]
            ag["held_ts"] = _now()
            out["result"] = "hold"
        else:
            ag["held_at"] = "waiting_review"
            ag["simple_park"] = True
            ag["hold_reason"] = f"soát dở (simple) — {why}"[:300]
            n_try = int(ag.get("review_retries") or 0) + 1
            ag["review_retries"] = n_try
            out["tries"] = n_try
            if n_try > max_tries:
                ag["held_at"] = "review_failed"
                ag["hold_after_translate"] = True
                ag["hold_reason"] = f"dịch simple chưa xong sau {n_try - 1} lượt — {why}, cần người xem"[:300]
                ag.pop("review_retry_at", None)
                out["result"] = "hold"
            else:
                ag["review_retry_at"] = _now() + float(retry_sec)
                out["result"] = "park"
            tts = (d.get("stages") or {}).get("tts")
            if isinstance(tts, dict) and tts.get("status") == "running":
                tts["status"] = "pending"
                tts["cur_text"] = "chờ dịch simple xong"
        d["agent"] = ag
    _update(wd, fn)
    return out


def mutate(wd, fn: Callable[[dict], Any]) -> dict:
    """Đọc-sửa-ghi NGUYÊN TỬ cho logic riêng chưa có hàm ngữ nghĩa. fn(data) trả False = không ghi.
    Trong fn KHÔNG tạo Manifest(wd) khác (đang giữ khoá → kẹt)."""
    return _update(wd, fn)


def try_mutate(wd, fn: Callable[[dict], Any]) -> bool:
    """Như mutate nhưng trả bool, không raise: True = đã ghi; False = fn trả False (không đổi gì) hoặc
    manifest hỏng/thiếu. Dùng cho logic agent «ghi nếu cần» (gỡ HOLD cũ, override tay — port P6 11-12)."""
    declined: list[bool] = []

    def _wrapped(d):
        r = fn(d)
        if r is False:
            declined.append(True)
        return r
    try:
        _update(wd, _wrapped)
    except StateError:
        return False
    return not declined


# ───────────── opts ─────────────

def set_queue_opts(wd, opts: dict, *, merge: bool = False) -> dict:
    clean = {k: v for k, v in (opts or {}).items() if k != "pin_opts"}

    def fn(d):
        if merge:
            d.setdefault("queue_opts", {}).update(clean)
        else:
            d["queue_opts"] = clean
    return _update(wd, fn)


def set_agent(wd, **fields) -> dict:
    """Khoá khác trong manifest.agent (review_request, qa_gate, …)."""
    def fn(d):
        d.setdefault("agent", {}).update(fields)
    return _update(wd, fn)
