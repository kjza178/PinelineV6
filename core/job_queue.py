from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

WORK_ROOT = Path("work")
WIRED = ("download", "asr", "translate", "tts", "mux")
_PERSIST_NAME = "_job_queue.json"

_CN_NUMS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_season_from_text(*texts: str) -> int | None:
    """Bóc số phần/mùa từ title (Phần N / P N / 第N季). Không dùng group_index."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return None
    m_head = re.match(
        r"^(?:phần|mùa|season|tập|p)\s*(\d+)\s*[|:\-–—]",
        blob.strip(),
        re.I,
    )
    if m_head:
        return int(m_head.group(1))
    m_vi = re.search(
        r"(?:^|[\s_\-\.\(:|])(?:p|phần|mùa|season|s|tập|t)\s*(\d+)(?:[\s_\-\.\):|]|$)",
        blob,
        re.I,
    )
    if m_vi:
        return int(m_vi.group(1))
    m_cn = re.search(r"第([一二三四五六七八九十\d]+)季", blob)
    if m_cn:
        val = m_cn.group(1)
        if val.isdigit():
            return int(val)
        if val in _CN_NUMS:
            return _CN_NUMS[val]
    # «Phần Cuối» / «mùa cuối» — không phải Phần 1 (tránh auto-pub gắn title premise S1).
    if re.search(r"(?:phần|mùa|tập|season)\s*cuối|\bfinal\b", blob, re.I):
        return -1
    return None


def _norm_series_key(name: str) -> str:
    """Chuẩn hoá tên bộ để sort: gom biến thể ngàn/nghìn, bỏ dấu câu thừa."""
    import unicodedata
    s = (name or "").strip().lower()
    if not s:
        return ""
    s = s.replace("nghìn", "ngan").replace("ngàn", "ngan")
    s = s.replace("đế vương", "de").replace("đế giả", "de").replace("đế", "de")
    nfkd = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def get_job_info(job_id: str) -> dict:
    """Lấy thông tin tiêu đề, bộ phim (series), mùa/phần (season) từ manifest để sort và hiển thị."""
    jid = str(job_id or "").strip()
    wd = WORK_ROOT / jid
    mf = wd / "manifest.json"
    fallback = {
        "job_id": jid,
        "title": jid,
        "series": jid,
        "season": 1,
        "group_id": None,
        "group_index": None,
        "group_title": None,
    }
    if not mf.is_file():
        return fallback
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return fallback

    req = data.get("request") or {}
    title_vi = (data.get("title_vi") or "").strip()
    req_title = (req.get("title") or "").strip()
    title_zh = (data.get("title_zh") or "").strip()
    group_id = (req.get("group_id") or "").strip() or None
    group_title = (req.get("group_title") or "").strip() or None
    group_index = req.get("group_index")

    title = title_vi or req_title or title_zh or jid

    # Season: ƯU TIÊN title (Phần N / 第N季). group_index chỉ fallback — hay lệch badge UI.
    season_no = _parse_season_from_text(title_vi, title, req_title, title_zh)
    if season_no is None and group_index is not None and str(group_index).strip().isdigit():
        season_no = int(group_index)

    # Series name từ title_vi
    target_text = title_vi or title
    extracted_series = ""

    m_head = re.match(r"^(?:phần|mùa|season|tập|p)\s*(\d+)\s*[|:\-–—]\s*(.*)$", target_text, re.I)
    if m_head:
        rest = m_head.group(2)
        parts = re.split(r"[:|–—]", rest)
        extracted_series = parts[0].strip(" -_()[]")
    else:
        cleaned = target_text
        cleaned = re.sub(r"(?:^|[\s_\-\.\(:])(?:p|phần|mùa|season|s|tập|t)\s*\d+.*$", "", cleaned, flags=re.I)
        parts = re.split(r"[:|–—]", cleaned)
        extracted_series = parts[0].strip(" -_()[]") if parts else cleaned.strip(" -_()[]")

    if season_no is None:
        season_no = 1

    if extracted_series and not re.search(r"[\u4e00-\u9fff]", extracted_series):
        series_name = extracted_series
    else:
        series_name = extracted_series or group_title or (group_id if group_id else "") or title_vi or title

    return {
        "job_id": jid,
        "title": title,
        "series": series_name.strip(),
        "season": season_no,
        "group_id": group_id,
        "group_index": group_index,
        "group_title": group_title,
    }


def job_sort_key(job_id: str) -> tuple:
    """Khóa sắp xếp: (group_id|series_norm, season P1..Pn, title)."""
    info = get_job_info(job_id)
    series_key = (info.get("group_id") or "").strip() or _norm_series_key(info.get("series") or "")
    return (series_key.lower(), int(info.get("season") or 1), (info.get("title") or "").lower())

_lock = threading.RLock()
_q: list[str] = []
_current: str | None = None
_opts: dict = {}  # fallback / last default (compat)
_opts_by_job: dict[str, dict] = {}
_hist: list[dict] = []
_stop = False
_thread: threading.Thread | None = None
_runner: Callable[[str, dict], tuple[str, str]] | None = None
_clear_stop_hook: Callable[[str], None] | None = None
# Studio invariant: HOLD / soát dở → park ghế, KHÔNG nhảy job kế đến khi sẵn sàng / stop / remove.
_PARK_STATUSES = frozenset({"held", "parked", "waiting_review"})
_park_ready_hook: Callable[[str], bool] | None = None
_PARK_POLL_SEC = float(os.getenv("QUEUE_PARK_POLL_SEC", "5") or "5")

# ───────────── chạy 24/7 (R7.3–R7.5, 2026-11-14) ─────────────
_HISTORY_NAME = "_job_queue_history.jsonl"
_HISTORY_MAX_BYTES = 2 * 1024 * 1024
_consec_fail = 0                       # số job `failed` liên tiếp (job `done` đặt lại 0; `stopped`/park không tính)
_recent_fail: list[dict] = []          # các job failed trong chuỗi hiện tại (cho thông báo)
_paused: dict | None = None            # {"reason", "ts", "fails": [...]} — hàng chờ đang TẠM DỪNG (R7.4)
_notify_hook: Callable[[str], None] | None = None
_needs_human_hook: Callable[[str], bool] | None = None


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default)) or default))
    except ValueError:
        return default


def max_consec_fail() -> int:
    """R7.4: số job failed LIÊN TIẾP làm hàng chờ tạm dừng (mặc định 3; 0 = tắt)."""
    return max(0, _env_int("QUEUE_MAX_CONSEC_FAIL", 3))


def hold_yield_sec() -> float:
    """R7.3: job HOLD CẦN NGƯỜI giữ ghế quá ngần này giây mà còn job khác chờ → nhường ghế (0 = tắt = luật
    Studio cũ: giữ ghế vô thời hạn). Mặc định 0 — chủ dự án bật khi muốn chạy 24/7 không người canh."""
    try:
        return max(0.0, float(os.getenv("QUEUE_HOLD_YIELD_SEC", "0") or 0))
    except ValueError:
        return 0.0


def set_notify_hook(fn: Callable[[str], None] | None) -> None:
    """Server gắn kênh báo (Telegram / event UI). Lỗi trong hook không ảnh hưởng hàng chờ."""
    global _notify_hook
    _notify_hook = fn


def set_needs_human_hook(fn: Callable[[str], bool] | None) -> None:
    """Server gắn: True = job đang HOLD cần NGƯỜI (khác park tự thử lại)."""
    global _needs_human_hook
    _needs_human_hook = fn


def _notify(text: str) -> None:
    print(f"[queue] {text}", flush=True)
    fn = _notify_hook
    if fn is None:
        return
    try:
        fn(text)
    except Exception as e:  # noqa: BLE001
        print(f"[queue] notify hook lỗi: {e}", flush=True)


def _needs_human(job_id: str) -> bool:
    fn = _needs_human_hook
    if fn is not None:
        try:
            return bool(fn(job_id))
        except Exception as e:  # noqa: BLE001
            print(f"[queue] needs_human hook lỗi: {e}", flush=True)
            return False
    try:
        data = json.loads((WORK_ROOT / str(job_id) / "manifest.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — không đọc được thì coi như không chắc → không nhường ghế
        return False
    ag = data.get("agent") or {}
    return bool(ag.get("hold_after_translate") or ag.get("qa_blocked")
                or str(ag.get("held_at") or "") not in ("", "waiting_review", "split_parts"))


def _reset_for_tests(load_disk: bool = False) -> None:
    """Chỉ dùng trong pytest."""
    global _q, _current, _opts, _opts_by_job, _hist, _stop, _thread, _runner, _clear_stop_hook
    global _park_ready_hook, _consec_fail, _paused, _notify_hook, _needs_human_hook, _save_failed
    with _lock:
        _stop = True
        old_thread = _thread
    if old_thread and old_thread is not threading.current_thread():
        old_thread.join(timeout=2)
    with _lock:
        _q.clear()
        _current = None
        _opts = {}
        _opts_by_job = {}
        _hist = []
        _stop = True
        _thread = None
        _runner = None
        _clear_stop_hook = None
        _park_ready_hook = None
        _consec_fail = 0
        _recent_fail.clear()
        _paused = None
        _notify_hook = None
        _needs_human_hook = None
        _save_failed = 0
        set_stop_hook(None)
    if load_disk:
        _load()


def set_runner(fn: Callable[[str, dict], tuple[str, str]]) -> None:
    global _runner
    _runner = fn


def set_park_ready_hook(fn: Callable[[str], bool] | None) -> None:
    """Server gắn hook: True = job HOLD/waiting có thể chạy tiếp (TTS/mux)."""
    global _park_ready_hook
    _park_ready_hook = fn


def is_park_status(status: str | None) -> bool:
    return str(status or "").strip().lower() in _PARK_STATUSES


def _default_park_ready(job_id: str) -> bool:
    """Fallback khi chưa wire hook: bỏ HOLD flags + (waiting_review → hết câu chưa rv)."""
    wd = WORK_ROOT / str(job_id)
    mf = wd / "manifest.json"
    if not mf.is_file():
        return False
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return False
    agent = data.get("agent") or {}
    if agent.get("hold_after_translate") or agent.get("qa_blocked"):
        return False
    held = str(agent.get("held_at") or "").strip()
    if held in ("", "waiting_review"):
        if held == "waiting_review":
            sp = wd / "script.json"
            if not sp.is_file():
                return False
            try:
                rows = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                return False
            if not isinstance(rows, list):
                return False
            unrev = sum(
                1
                for r in rows
                if isinstance(r, dict)
                and not r.get("rv")
                and (r.get("vi") or "").strip()
            )
            return unrev == 0
        return True
    # held_at khác (qa_brain / after_translate) → cần resume/clear hold
    return False


def _park_ready(job_id: str) -> bool:
    if _park_ready_hook is not None:
        try:
            return bool(_park_ready_hook(job_id))
        except Exception:
            return False
    return _default_park_ready(job_id)


def is_wired_done(work_dir: Path) -> bool:
    mf = work_dir / "manifest.json"
    out = work_dir / "output.mp4"
    if not mf.is_file() or not out.is_file() or out.stat().st_size <= 0:
        return False
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return False
    stages = data.get("stages") or {}
    return all((stages.get(s) or {}).get("status") == "done" for s in WIRED)


def _persist_path() -> Path:
    return WORK_ROOT / _PERSIST_NAME


_save_failed = 0      # số lần ghi _job_queue.json hỏng liên tiếp (0 = lần ghi gần nhất thành công)


def _save() -> bool:
    """Ghi hàng chờ ra đĩa (atomic). KHÔNG BAO GIỜ ném lỗi — trả False khi ghi hỏng.

    R7.1 (fix 2026-11-14): trước đây lỗi ghi (đĩa đầy, `os.replace` dính PermissionError vì antivirus / tiến trình
    khác đang mở file trên Windows) văng thẳng ra — trong luồng worker = thread chết im lặng: `current` kẹt, mọi job
    còn lại đứng tới khi có người xếp job mới. Hàng chờ trong bộ nhớ vẫn đúng → thử lại ngắn, hỏng thì ghi log và
    chạy tiếp; lần `_save` sau sẽ ghi bù. (Mất bản ghi đĩa chỉ ảnh hưởng nếu server chết đúng lúc đó.)"""
    global _save_failed
    payload = {
        "queue": list(_q),
        "opts": dict(_opts),
        "opts_by_job": {k: dict(v) for k, v in _opts_by_job.items()},
        "current": _current,
        "paused": dict(_paused) if _paused else None,
    }
    last: Exception | None = None
    for attempt in range(4):
        try:
            WORK_ROOT.mkdir(parents=True, exist_ok=True)
            target = _persist_path()
            temp = target.with_name(f".{target.name}.tmp")
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
            if _save_failed:
                print(f"[queue] ghi hàng chờ đã được lại (sau {_save_failed} lần hỏng)", flush=True)
            _save_failed = 0
            return True
        except Exception as e:  # noqa: BLE001 — OSError là chính; TypeError nếu opts không tuần tự hoá được
            last = e
            if attempt < 3:
                time.sleep(0.05 * (attempt + 1))
    _save_failed += 1
    if _save_failed == 1 or _save_failed % 20 == 0:      # không spam log khi đĩa đầy kéo dài
        print(f"[queue] KHÔNG ghi được {_persist_path()} ({type(last).__name__}: {last}) — hàng chờ vẫn chạy "
              f"trong bộ nhớ; khởi động lại lúc này có thể mất thứ tự hàng chờ (lần hỏng thứ {_save_failed})",
              flush=True)
    return False


def _load() -> None:
    global _q, _opts, _opts_by_job, _current, _paused
    p = _persist_path()
    if not p.is_file():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    with _lock:
        _q = [str(x) for x in (data.get("queue") or []) if str(x).strip()]
        _opts = dict(data.get("opts") or {})
        raw_ob = data.get("opts_by_job") or {}
        _opts_by_job = {
            str(k): dict(v) for k, v in raw_ob.items() if isinstance(v, dict)
        }
        # Migrate: old persist only had global opts → apply to pending jobs
        if _opts and not _opts_by_job:
            for jid in _q:
                _opts_by_job[jid] = dict(_opts)
        cur = data.get("current")
        if cur and cur not in _q:
            _q.insert(0, str(cur))
            if _opts and str(cur) not in _opts_by_job:
                _opts_by_job[str(cur)] = dict(_opts)
        _current = None
        pz = data.get("paused")
        _paused = dict(pz) if isinstance(pz, dict) and _q else None   # restart không tự gỡ tạm dừng


def _hist_push(job_id: str, status: str, detail: str = "") -> None:
    rec = {"job_id": job_id, "status": status, "detail": detail, "ts": time.time()}
    _hist.append(rec)
    del _hist[:-20]
    _history_append(rec)


def _history_path() -> Path:
    return WORK_ROOT / _HISTORY_NAME


def _history_append(rec: dict) -> None:
    """R7.5: nhật ký hàng chờ ra file JSONL (sống qua restart) — «đêm qua chuyện gì xảy ra». Best-effort:
    không ghi được thì chỉ báo 1 dòng, không ảnh hưởng hàng chờ. Quá 2 MB → xoay sang `.1` (giữ 2 file)."""
    try:
        p = _history_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            if p.stat().st_size > _HISTORY_MAX_BYTES:
                os.replace(p, p.with_name(p.name + ".1"))
        except OSError:
            pass
        line = dict(rec)
        line["at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec.get("ts") or time.time()))
        try:
            line["title"] = get_job_info(str(rec.get("job_id") or "")).get("title")
        except Exception:  # noqa: BLE001
            pass
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"[queue] không ghi được nhật ký hàng chờ: {e}", flush=True)


def queue_history(limit: int = 100) -> list[dict]:
    """Các mục mới nhất của nhật ký hàng chờ (mới → cũ), đọc từ file (cả file đã xoay nếu cần)."""
    limit = max(1, min(int(limit or 100), 2000))
    out: list[dict] = []
    for p in (_history_path(), _history_path().with_name(_HISTORY_NAME + ".1")):
        if len(out) >= limit:
            break
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in reversed(lines):
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
            if len(out) >= limit:
                break
    return out


def queue_busy() -> bool:
    """True nếu worker đang chạy hoặc còn job trong hàng — dùng GPU gate heal/watch."""
    with _lock:
        return bool(_current) or bool(_q) or bool(_thread and _thread.is_alive())


def queue_state() -> dict:
    with _lock:
        return {
            "queue": list(_q),
            "queue_items": [get_job_info(x) for x in _q],
            "current": _current,
            "current_info": get_job_info(_current) if _current else None,
            "history": list(_hist[-20:]),
            "worker": bool(_thread and _thread.is_alive()),
            "opts": dict(_opts),
            "opts_by_job": {k: dict(v) for k, v in _opts_by_job.items()},
            "busy": bool(_current) or bool(_q) or bool(_thread and _thread.is_alive()),
            "persist_ok": _save_failed == 0,     # False = đang không ghi được _job_queue.json (đĩa đầy / file bị khoá)
            "paused": dict(_paused) if _paused else None,   # R7.4: tạm dừng vì nhiều job lỗi liên tiếp
            "consec_fail": _consec_fail,
        }


def queue_add(
    job_ids: list[str],
    opts: dict | None = None,
    skip_done: bool = True,
    sort_first: bool = True,
    opts_for: Callable[[str], dict] | None = None,
) -> dict:
    """opts_for(job_id) (V6 F2): opts riêng từng job (vd ⊕ queue_opts đã ghim của job, core.job_opts);
    lỗi → dùng `opts` chung."""
    global _opts, _stop
    cleaned: list[str] = []
    skipped_done: list[str] = []
    skipped_missing: list[str] = []
    seen: set[str] = set()
    for raw in job_ids or []:
        jid = str(raw or "").strip()
        if not jid or jid in seen:
            continue
        seen.add(jid)
        wd = WORK_ROOT / jid
        if not (wd / "manifest.json").is_file():
            skipped_missing.append(jid)
            continue
        if skip_done and is_wired_done(wd):
            skipped_done.append(jid)
            continue
        cleaned.append(jid)
    if sort_first and len(cleaned) > 1:
        cleaned.sort(key=job_sort_key)
    per_job: dict[str, dict] = {}
    if opts_for is not None and opts is not None:
        for jid in cleaned:      # ngoài khoá: đọc manifest
            try:
                per_job[jid] = dict(opts_for(jid))
            except Exception as e:  # noqa: BLE001
                print(f"[queue] opts_for {jid}: {e}", flush=True)
    with _lock:
        appended: list[str] = []
        for jid in cleaned:
            if jid not in _q and jid != _current:
                _q.append(jid)
                appended.append(jid)
                if opts is not None:
                    _opts_by_job[jid] = per_job.get(jid) or dict(opts)
        if appended:
            if opts is not None:
                # fallback default cho job cũ chưa có snapshot
                _opts = dict(opts)
            _stop = False
        _save()
        if appended:
            _ensure_worker_unlocked()      # đang tạm dừng (R7.4) → không bật worker; job vẫn vào hàng
        return {
            "ok": True,
            "queued": appended,
            "skipped_done": skipped_done,
            "skipped_missing": skipped_missing,
            "queue": list(_q),
            "current": _current,
            "paused": dict(_paused) if _paused else None,
        }


def queue_sort(by: str = "series_season") -> dict:
    """Sắp xếp lại các job đang chờ trong hàng:
    - 'series_season': Nhóm theo tên bộ + Phần 1, 2, 3..
    - 'name': Theo tên tiêu đề A-Z
    - 'reverse': Đảo ngược thứ tự
    """
    with _lock:
        if by == "series_season":
            _q.sort(key=job_sort_key)
        elif by == "name":
            _q.sort(key=lambda jid: get_job_info(jid)["title"].lower())
        elif by == "reverse":
            _q.reverse()
        _save()
    return {"ok": True, "by": by, **queue_state()}


def queue_move(job_id: str, action: str) -> dict:
    """Di chuyển vị trí của 1 job trong hàng chờ:
    action: 'top' (lên đầu), 'up' (lên 1 nấc), 'down' (xuống 1 nấc), 'bottom' (xuống cuối)
    """
    jid = str(job_id or "").strip()
    with _lock:
        if jid not in _q:
            err = f"Job '{jid}' không có trong hàng chờ"
            return {"ok": False, "error": err, **queue_state()}
        idx = _q.index(jid)
        if action == "top":
            if idx > 0:
                _q.pop(idx)
                _q.insert(0, jid)
        elif action == "up":
            if idx > 0:
                _q[idx - 1], _q[idx] = _q[idx], _q[idx - 1]
        elif action == "down":
            if idx < len(_q) - 1:
                _q[idx], _q[idx + 1] = _q[idx + 1], _q[idx]
        elif action == "bottom":
            if idx < len(_q) - 1:
                _q.pop(idx)
                _q.append(jid)
        else:
            return {"ok": False, "error": f"Hành động không hợp lệ: {action}", **queue_state()}
        _save()
    return {"ok": True, "moved": jid, "action": action, **queue_state()}


def queue_reorder(new_order: list[str]) -> dict:
    """Gán lại toàn bộ thứ tự cho hàng chờ theo danh sách client gửi."""
    with _lock:
        clean_order = [str(x).strip() for x in (new_order or []) if str(x).strip()]
        order_set = set(clean_order)
        reordered = [j for j in clean_order if j in _q]
        leftover = [j for j in _q if j not in order_set]
        _q[:] = reordered + leftover
        _save()
    return {"ok": True, **queue_state()}


def queue_add_group(
    group_id: str,
    opts: dict | None = None,
    skip_done: bool = True,
    opts_for: Callable[[str], dict] | None = None,
) -> dict:
    """Enqueue mọi member của group theo group_index tăng dần."""
    gid = str(group_id or "").strip()
    if not gid:
        return {"ok": False, "error": "thiếu group_id", "queued": []}
    members: list[tuple[int, str]] = []
    if WORK_ROOT.is_dir():
        for d in WORK_ROOT.iterdir():
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                req = data.get("request") or {}
                if req.get("group_id") != gid:
                    continue
                idx = int(req.get("group_index") or 9999)
                members.append((idx, d.name))
            except Exception:  # noqa: BLE001
                continue
    members.sort(key=lambda x: (x[0], x[1]))
    ids = [jid for _, jid in members]
    if not ids:
        return {
            "ok": False,
            "error": f"không có job nào thuộc group {gid}",
            "queued": [],
            "group_id": gid,
        }
    r = queue_add(ids, opts=opts, skip_done=skip_done, opts_for=opts_for)
    r["group_id"] = gid
    r["group_order"] = ids
    return r


def queue_front(job_id: str) -> None:
    with _lock:
        if job_id in _q:
            _q.remove(job_id)
            _q.insert(0, job_id)
            _save()


def queue_remove(job_ids: list[str]) -> dict:
    """Bỏ job khỏi hàng chờ (không stop current)."""
    want = {str(x).strip() for x in (job_ids or []) if str(x).strip()}
    with _lock:
        kept = [j for j in _q if j not in want]
        removed = [j for j in _q if j in want]
        _q[:] = kept
        for jid in removed:
            _opts_by_job.pop(jid, None)
        _save()
        return {"ok": True, "removed": removed, "queue": list(_q), "current": _current}


def queue_update_opts(updates: dict[str, dict]) -> dict:
    """Đổi opts của job ĐANG CHỜ tại chỗ (giữ thứ tự, không đụng current) — thay cho remove+add."""
    with _lock:
        updated = [j for j in _q if j in updates]
        for jid in updated:
            _opts_by_job[jid] = dict(updates[jid])
        skipped = [j for j in updates if j not in updated]
        if updated:
            _save()
        return {"ok": True, "updated": updated, "skipped": skipped, "queue": list(_q), "current": _current}


def queue_clear() -> dict:
    global _paused, _consec_fail
    with _lock:
        _q.clear()
        _opts_by_job.clear()
        _paused = None            # hàng chờ trống → không còn gì để tạm dừng
        _consec_fail = 0
        _recent_fail.clear()
        _save()
    return {"ok": True, **queue_state()}


def queue_resume() -> dict:
    """R7.4: người dùng đã xem lỗi → gỡ tạm dừng, đặt lại bộ đếm, chạy tiếp hàng chờ."""
    global _paused, _consec_fail, _stop
    with _lock:
        was = _paused
        _paused = None
        _consec_fail = 0
        _recent_fail.clear()
        if _q:
            _stop = False
            _ensure_worker_unlocked()
        _save()
    if was:
        print("[queue] gỡ tạm dừng — chạy tiếp hàng chờ", flush=True)
    return {"ok": True, "resumed": bool(was), **queue_state()}


def queue_stop() -> dict:
    """Clear queue + báo worker dừng; gọi _stop_job(current) nếu server đã wire."""
    global _stop, _current, _paused, _consec_fail
    with _lock:
        _stop = True
        _q.clear()
        _paused = None
        _consec_fail = 0
        _recent_fail.clear()
        # giữ opts_by_job của current để runner còn đọc; dọn pending
        cur = _current
        pending_opts = {k: v for k, v in _opts_by_job.items() if k == cur}
        _opts_by_job.clear()
        _opts_by_job.update(pending_opts)
        _save()
    if cur and _runner is not None:
        stop_hook = getattr(queue_stop, "_hook", None)
        if callable(stop_hook):
            try:
                stop_hook(cur)
            except Exception:
                pass
    return {"ok": True, "stopped_job": cur}


def set_stop_hook(fn: Callable[[str], None] | None) -> None:
    queue_stop._hook = fn  # type: ignore[attr-defined]


def set_clear_stop_hook(fn: Callable[[str], None] | None) -> None:
    global _clear_stop_hook
    _clear_stop_hook = fn


def _ensure_worker_unlocked() -> None:
    global _thread, _stop
    if _thread and _thread.is_alive():
        return
    if _paused:       # R7.4: đang tạm dừng vì lỗi liên tiếp → chỉ queue_resume() mới chạy lại
        return
    _stop = False
    _thread = threading.Thread(
        target=_worker_loop, daemon=True, name="pineline-job-queue"
    )
    _thread.start()


def _ensure_worker() -> None:
    with _lock:
        _ensure_worker_unlocked()


def _worker_loop() -> None:
    """Vòng worker có lưới an toàn (R7.1): lỗi KHÔNG lường trước trong chính vòng lặp (không phải lỗi của job —
    runner đã tự bắt) được ghi log kèm traceback rồi vòng lặp chạy tiếp, thay vì thread chết để hàng chờ đứng."""
    global _current, _thread
    crashes = 0
    while True:
        try:
            _worker_loop_inner()
            return
        except Exception:  # noqa: BLE001
            import traceback
            crashes += 1
            print(f"[queue] LỖI trong vòng worker (lần {crashes}) — chạy tiếp sau 2 s:\n{traceback.format_exc()}",
                  flush=True)
            with _lock:
                _current = None
                if crashes >= 5:      # lỗi lặp lại ngay → dừng hẳn, không quay vòng đốt CPU; xếp job mới sẽ bật lại
                    _thread = None
                    print("[queue] vòng worker lỗi 5 lần liên tiếp → DỪNG worker (hàng chờ giữ nguyên)", flush=True)
                    return
            time.sleep(2.0)


def _worker_loop_inner() -> None:
    global _current, _stop, _thread, _consec_fail, _paused
    while True:
        with _lock:
            if _stop:
                _current = None
                _thread = None
                _save()
                return
            if not _q:
                _current = None
                _thread = None
                _save()
                return
            job_id = _q.pop(0)
            if _clear_stop_hook is not None:
                try:
                    _clear_stop_hook(job_id)
                except Exception:
                    pass
            _current = job_id
            # get (không pop): server chết giữa job → _load đưa current lại đầu hàng với đúng opts
            opts = dict(_opts_by_job.get(job_id) or _opts)
            _save()
        status = "failed"
        detail = ""
        try:
            if _runner is None:
                status, detail = "failed", "no runner"
            else:
                result = _runner(job_id, opts)
                if isinstance(result, tuple):
                    status, detail = result
                else:
                    status = result or "failed"
        except Exception as e:  # noqa: BLE001
            status, detail = "failed", str(e)[:200]

        # Studio: HOLD / soát dở → giữ ghế, chờ sẵn sàng rồi chạy lại CÙNG job.
        # Không lấy job kế (tránh nhảy linh tinh khi Brain/review dừng sớm).
        if is_park_status(status):
            with _lock:
                _hist_push(job_id, status, detail)
                if job_id in _q:
                    _q.remove(job_id)
                _q.insert(0, job_id)
                _opts_by_job[job_id] = dict(opts)
                _current = job_id
                _save()
            poll = max(0.5, float(_PARK_POLL_SEC) or 5.0)
            parked_at = time.time()
            while True:
                with _lock:
                    if _stop:
                        _current = None
                        _thread = None
                        _save()
                        return
                    if job_id not in _q:
                        # user remove / clear — nhường ghế
                        _current = None
                        _opts_by_job.pop(job_id, None)
                        _save()
                        break
                    others = len(_q) > 1
                # R7.3 (tuỳ chọn, mặc định tắt): HOLD cần NGƯỜI quá lâu mà còn job khác chờ → nhường ghế.
                # Job giữ nguyên trạng thái HOLD trong manifest; «Chạy tiếp» sẽ xếp nó lại lên ĐẦU hàng.
                limit = hold_yield_sec()
                if limit > 0 and others and time.time() - parked_at >= limit and _needs_human(job_id):
                    with _lock:
                        if job_id in _q:
                            _q.remove(job_id)
                        _current = None
                        _hist_push(job_id, "sidelined", f"HOLD cần người quá {int(limit)} s — nhường ghế cho job kế")
                        _save()
                    _notify(f"Job «{get_job_info(job_id).get('title') or job_id}» đang chờ người duyệt quá "
                            f"{int(limit // 60) or 1} phút → nhường ghế cho job kế. Bấm «Chạy tiếp» ở job đó khi duyệt xong.")
                    break
                if _park_ready(job_id):
                    with _lock:
                        # R7.2 (fix 2026-11-14): job giữ ghế phải chạy lại TRƯỚC. Trong lúc park, hàng chờ có thể
                        # bị sắp lại / job khác bị đẩy lên đầu (queue_move, queue_sort, queue_front của «Chạy tiếp»
                        # job khác) → trước đây vòng kế lấy _q[0] là job KHÁC, phá luật «không nhảy job».
                        if job_id in _q and _q[0] != job_id:
                            _q.remove(job_id)
                            _q.insert(0, job_id)
                        _current = None
                        _save()
                    break
                time.sleep(poll)
            continue

        trip: dict | None = None
        with _lock:
            _hist_push(job_id, status, detail)
            _current = None
            if job_id not in _q:
                _opts_by_job.pop(job_id, None)
            # R7.4: nhiều job failed LIÊN TIẾP = nhiều khả năng lỗi chung (GPU, đĩa, cloud, cấu hình) → tạm dừng
            # thay vì đánh failed cả hàng chờ trong vài phút. `done` đặt lại; `stopped`/khác không tính.
            st_l = str(status or "").strip().lower()
            if st_l == "failed":
                _consec_fail += 1
                _recent_fail.append({"job_id": job_id, "detail": str(detail or "")[:200]})
                del _recent_fail[:-10]
            elif st_l == "done":
                _consec_fail = 0
                _recent_fail.clear()
            lim = max_consec_fail()
            if lim > 0 and _consec_fail >= lim and _q and not _stop:
                trip = {"reason": f"{_consec_fail} job lỗi liên tiếp", "ts": time.time(),
                        "fails": [dict(x) for x in _recent_fail[-lim:]], "left": len(_q)}
                _paused = trip
                _thread = None
            _save()
            if trip is None and _stop:
                _thread = None
                return
        if trip is not None:
            last = trip["fails"][-1] if trip["fails"] else {}
            _notify(f"TẠM DỪNG hàng chờ: {trip['reason']} (còn {trip['left']} job chưa chạy). "
                    f"Lỗi gần nhất: {last.get('detail') or '?'} — xem lỗi rồi bấm «Chạy tiếp hàng chờ».")
            return


# Load persist khi import (server có thể set WORK_ROOT trước — xem Task 2).
# Dưới pytest không nạp hàng chờ thật: worker test sẽ chạy job thật song song server.
if "pytest" not in sys.modules:
    _load()
