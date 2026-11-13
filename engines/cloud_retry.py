"""
cloud_retry.py — fix 2026-11-11 translate-b2 (Việc A): THỬ LẠI model cloud (Aurora/GPT…)
khi bị limit/quota (429) hoặc lỗi tạm (5xx/timeout) TRƯỚC khi đổi sang local / park.

Aurora xoay vòng tài khoản → 429/limit thường chỉ tạm thời. Trước đây REVIEW gặp 429 là
đổi preset llama ngay (tốn VRAM, chậm, chất lượng thấp hơn).

- `classify_cloud_retry(e)`  → "limit" | "transient" | None (None = không thử lại).
- `retry_after_seconds(e)`   → giây chờ từ header/field Retry-After (nếu có).
- `call_with_cloud_retry(fn, ...)` → gọi fn(); lỗi limit/transient thì ngủ backoff tăng dần
  (mặc định 30→60→120→240 s, jitter ±10 %), kiểm `.stop` mỗi ≤ 2 s, KHÔNG giữ khoá chung
  khi ngủ. Hết lượt → ném lại lỗi GỐC (gắn `_cloud_retry_exhausted=True`) để caller đi
  đường cũ (fallback local C9 / lô lỗi → park B2).
- «Cửa chờ» chung (`CloudGate`, circuit breaker mềm) theo nhà cung cấp: một luồng thấy limit
  → MỌI luồng (kể cả luồng chưa lỗi) chờ tới mốc thử lại chung; hết mốc chỉ MỘT luồng đi dò
  (probe); dò OK → mở cửa cho tất cả; dò vẫn limit → đẩy mốc tiếp. Không bão retry.
- review 2026-11-12: NGÂN SÁCH CHỜ CHUNG cho một đợt limit (`outage_budget_sec`, mặc định
  ≈ 577 s) — quá ngân sách thì mọi luồng thôi chờ, gặp limit nữa là bỏ cloud ngay (trước đó
  tổng chờ ∝ số luồng: 24 luồng ≈ 2,4 giờ). Retry-After lớn hơn ngân sách còn lại → bỏ ngay.
  Request bay từ TRƯỚC đợt limit trả OK không mở cửa. Một lời gọi đã kéo dài
  ≥ max(600 s, 2 × ngân sách) → không thử lại nữa (llm.py đã tự thử 12 request × timeout).

Config (config.get; caller truyền `cfg_get`):
  REVIEW_CLOUD_RETRY    số lần thử lại (mặc định 4; 0 = tắt retry, hành vi cũ)
  REVIEW_CLOUD_BACKOFF  chuỗi giây "30,60,120,240" (lần thứ k dùng phần tử k, hết thì phần tử cuối)
  REVIEW_LOCAL_FALLBACK 1/0 — do translate.py đọc (0 = không bao giờ đổi sang local)
"""
from __future__ import annotations

import random
import re
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable

DEFAULT_RETRIES = 4
DEFAULT_BACKOFF = (30.0, 60.0, 120.0, 240.0)
STOP_SLICE_SEC = 2.0          # kiểm .stop tối đa mỗi 2 s khi chờ
PROBE_GRACE_SEC = 15.0        # probe bay > 15 s (không bị 429 ngay) → coi như đã mở, cho luồng khác đi
RETRY_AFTER_MAX_SEC = 900.0
MAX_OUTAGE_BUDGET_SEC = 3600.0  # review 2026-11-12: trần ngân sách chờ chung / đợt limit
# review 2026-11-12: không BẮT ĐẦU lần thử lại mới khi 1 lời gọi đã kéo dài quá
# max(MIN_CALL_ELAPSED_SEC, 2 × ngân sách) — llm.py tự thử tới 12 request/lần (timeout 900 s
# mỗi request) nên retry sau timeout dài chỉ nhân thời gian treo.
MIN_CALL_ELAPSED_SEC = 600.0

# Hook thời gian — test thay bằng đồng hồ giả.
_sleep: Callable[[float], None] = time.sleep
_now: Callable[[], float] = time.time
_rand: Callable[[], float] = random.random


class CloudRetryStopped(RuntimeError):
    """User bấm Dừng (.stop) trong lúc chờ thử lại cloud. Thông điệp chứa «stopped by user»
    để mọi chỗ đang bắt chuỗi này (run_translate / server) xử lý như Dừng thường."""

    def __init__(self, msg: str = "stopped by user (đang chờ thử lại cloud)"):
        super().__init__(msg)


# ───────────────────────── phân loại lỗi ─────────────────────────

_STATUS_RES = (
    re.compile(r"Aurora/ChatGPT (\d{3}) \(no-retry\)"),
    re.compile(r"từ chối payload \((\d{3})\)"),
    re.compile(r"thất bại[^:\n]{0,120}:\s*(\d{3})\b"),   # «… thất bại: 429 …» / «… sau 4 lần: 503 …»
    re.compile(r"^\s*(\d{3})\b"),
)
_LIMIT_RE = re.compile(
    r"rate[ _-]?limit|too many requests|quota|usage[ _-]?(?:limit|cap)|limit[ _-]?(?:reached|exceeded)|"
    r"(?:reached|exceeded|hit)\s+(?:the|your|a)?\s*(?:usage\s+|rate\s+|message\s+)?(?:limit|cap)|"
    r"insufficient_quota|requests? per (?:min|minute|hour|day)",
    re.I,
)
_EXHAUSTED_RE = re.compile(r"thất bại|that bai|timed? ?out|timeout|connection (?:reset|aborted|refused)", re.I)
_NO_RETRY_STATUS = {400, 401, 403, 404, 413, 422}
_TRANSIENT_STATUS = {500, 502, 503, 504, 520, 522, 524}


def _chain(e: BaseException | None):
    seen: set[int] = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        yield e
        e = e.__cause__ or e.__context__


def error_status(e: BaseException) -> int | None:
    """Mã HTTP: response.status_code (dọc chuỗi __cause__/__context__) hoặc mẫu thông điệp
    cố định do llm.py tự dựng."""
    for x in _chain(e):
        sc = getattr(getattr(x, "response", None), "status_code", None)
        if isinstance(sc, int):
            return sc
        msg = str(x)
        for rx in _STATUS_RES:
            mm = rx.search(msg)
            if mm:
                try:
                    return int(mm.group(1))
                except ValueError:
                    pass
    return None


def classify_cloud_retry(e: BaseException) -> str | None:
    """"limit" (429 / thông điệp limit-quota-rate) · "transient" (5xx, timeout, mất kết nối,
    LLMUnavailable «… thất bại» sau khi llm.py đã thử hết) · None (không thử lại: 4xx khác,
    payload quá lớn, parse/bad output, thiếu key, Dừng…)."""
    if isinstance(e, CloudRetryStopped) or "stopped by user" in str(e).lower():
        return None
    if isinstance(e, ValueError):            # ReviewBadOutput / parse JSON → không phải lỗi mạng
        return None
    st = error_status(e)
    if st == 429:
        return "limit"
    if st in _NO_RETRY_STATUS:
        return None
    text = " | ".join(str(x) for x in _chain(e))
    if _LIMIT_RE.search(text):
        return "limit"
    if st in _TRANSIENT_STATUS:
        return "transient"
    try:
        import requests as _rq
        if any(isinstance(x, (_rq.Timeout, _rq.ConnectionError)) for x in _chain(e)):
            return "transient"
    except Exception:  # noqa: BLE001
        pass
    if any(isinstance(x, (TimeoutError, ConnectionError)) for x in _chain(e)):
        return "transient"
    try:
        from engines.llm import LLMUnavailable
    except Exception:  # noqa: BLE001
        LLMUnavailable = None  # type: ignore[assignment]
    if LLMUnavailable is not None and isinstance(e, LLMUnavailable) and st is None:
        # llm.py đã thử hết lượt (mất mạng / 5xx / 429 kéo dài) — KHÔNG phải lỗi cấu hình
        if _EXHAUSTED_RE.search(str(e)):
            return "transient"
    return None


def _parse_retry_after(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
        return dt.timestamp() - _now()
    except Exception:  # noqa: BLE001
        return None


_RA_MSG_RES = (
    re.compile(r"retry[-_ ]after[\"']?\s*[:=]\s*[\"']?(\d+(?:\.\d+)?)", re.I),
    re.compile(r"(?:try again|retry) in\s+(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?|m|min|mins|minutes?)\b", re.I),
)


def retry_after_seconds(e: BaseException) -> float | None:
    """Retry-After từ header response (dọc chuỗi lỗi) hoặc field/thông điệp
    («retry_after: 30», «try again in 20s»). None nếu không có."""
    for x in _chain(e):
        resp = getattr(x, "response", None)
        hdrs = getattr(resp, "headers", None)
        if hdrs:
            try:
                v = hdrs.get("Retry-After") or hdrs.get("retry-after")
            except Exception:  # noqa: BLE001
                v = None
            ra = _parse_retry_after(v)
            if ra is not None:
                return max(0.0, min(ra, RETRY_AFTER_MAX_SEC))
        ra_attr = getattr(x, "retry_after", None)
        if isinstance(ra_attr, (int, float)):
            return max(0.0, min(float(ra_attr), RETRY_AFTER_MAX_SEC))
    text = " | ".join(str(x) for x in _chain(e))
    for rx in _RA_MSG_RES:
        mm = rx.search(text)
        if mm:
            val = float(mm.group(1))
            unit = (mm.group(2) if mm.lastindex and mm.lastindex >= 2 else "s") or "s"
            unit = unit.lower()
            if unit == "ms":
                val /= 1000.0
            elif unit.startswith("m") and unit != "ms":
                val *= 60.0
            return max(0.0, min(val, RETRY_AFTER_MAX_SEC))
    return None


# ───────────────────────── cấu hình ─────────────────────────

def _cfg(cfg_get, key: str, default: str) -> str:
    try:
        if cfg_get is None:
            from core.config import get as cfg_get  # type: ignore[no-redef]
        v = cfg_get(key, default)
    except Exception:  # noqa: BLE001
        v = default
    return default if v is None or str(v).strip() == "" else str(v).strip()


def retry_count(cfg_get=None) -> int:
    try:
        n = int(float(_cfg(cfg_get, "REVIEW_CLOUD_RETRY", str(DEFAULT_RETRIES))))
    except (TypeError, ValueError):
        n = DEFAULT_RETRIES
    return max(0, min(n, 20))


def backoff_schedule(cfg_get=None) -> list[float]:
    raw = _cfg(cfg_get, "REVIEW_CLOUD_BACKOFF", ",".join(str(int(x)) for x in DEFAULT_BACKOFF))
    out: list[float] = []
    for part in re.split(r"[,; ]+", raw):
        if not part:
            continue
        try:
            out.append(max(0.0, min(float(part), 3600.0)))
        except ValueError:
            continue
    return out or list(DEFAULT_BACKOFF)


def outage_budget_sec(n_max: int, sched: list[float]) -> float:
    """review 2026-11-12: ngân sách chờ CHUNG cho một đợt limit = tổng backoff của n_max lần
    (mặc định 30+60+120+240 = 450 s) × 1,25 + PROBE_GRACE_SEC, trần MAX_OUTAGE_BUDGET_SEC.
    Không phụ thuộc số luồng."""
    if n_max <= 0:
        return 0.0
    s = sched or list(DEFAULT_BACKOFF)
    tot = sum(max(1.0, s[min(k, len(s) - 1)]) for k in range(n_max))
    return min(MAX_OUTAGE_BUDGET_SEC, tot * 1.25 + PROBE_GRACE_SEC)


def local_fallback_enabled(cfg_get=None) -> bool:
    return _cfg(cfg_get, "REVIEW_LOCAL_FALLBACK", "1").lower() not in ("0", "false", "no", "off")


# ───────────────────────── cửa chờ chung ─────────────────────────

class CloudGate:
    """Circuit breaker mềm DÙNG CHUNG cho mọi luồng gọi cùng nhà cung cấp cloud.

    tripped=False → cửa mở, đi luôn. tripped=True → chờ tới `until`; hết mốc thì 1 luồng
    được dò (probe), luồng khác chờ kết quả (tối đa PROBE_GRACE_SEC). Khoá nội bộ chỉ giữ
    trong vài phép gán — mọi giấc ngủ nằm NGOÀI khoá.
    """

    def __init__(self, name: str = "cloud"):
        self.name = name
        self._lock = threading.Lock()
        self.tripped = False
        self.until = 0.0
        self.probe_since: float | None = None
        self.n_trips = 0
        self.n_probes = 0
        # review 2026-11-12: ngân sách chờ CHUNG cho một đợt limit (outage) — trước đây mỗi
        # luồng lần lượt làm probe với 4 lần thử riêng → tổng chờ ∝ số luồng (24 luồng ≈ 2,7 giờ).
        self.outage_start: float | None = None
        self.last_fail_at = 0.0
        # 12-11: số thứ tự (không phụ thuộc đồng hồ) — đồng hồ thô trên Windows làm request cũ và
        # mốc 429 trùng tick. Mỗi đợt limit mới tăng seq; request nhớ seq lúc bắt đầu.
        self.seq = 0
        self.outage_seq = 0

    def _stale_locked(self, t: float, budget: float | None) -> bool:
        return bool(budget) and self.outage_start is not None and t - self.last_fail_at >= float(budget)

    def trip(self, until: float, budget: float | None = None) -> float:
        with self._lock:
            t = _now()
            if not self.tripped or self.outage_start is None or self._stale_locked(t, budget):
                self.outage_start = t
                self.seq += 1
                self.outage_seq = self.seq
            self.last_fail_at = t
            self.tripped = True
            self.until = max(self.until, until)
            self.probe_since = None
            self.n_trips += 1
            return self.until

    def note_fail(self) -> None:
        """Ghi nhận 1 lỗi limit trong đợt đang mở (không đẩy mốc, không trip lại)."""
        with self._lock:
            if self.tripped:
                self.last_fail_at = _now()

    def current_seq(self) -> int:
        with self._lock:
            return self.seq

    def success(self, started_at: float | None = None, started_seq: int | None = None) -> None:
        """Mở cửa. `started_at` = lúc request bắt đầu: request bay TỪ TRƯỚC đợt limit
        (started_at < outage_start) không được mở cửa — tránh 1 request cũ OK làm mọi luồng
        đang chờ bắn lại ngay (đốt lượt thử lại không chờ)."""
        with self._lock:
            if self.tripped and started_seq is not None and started_seq < self.outage_seq:
                return      # request bắt đầu TRƯỚC đợt limit hiện tại (so theo số thứ tự)
            if (
                started_seq is None and started_at is not None and self.tripped
                and self.outage_start is not None and started_at < self.outage_start
            ):
                return
            self.tripped = False
            self.probe_since = None
            self.outage_start = None

    def over_budget(self, budget: float | None) -> bool:
        """Đợt limit hiện tại đã chờ quá ngân sách chung (và chưa nguội) → không chờ thêm."""
        if not budget:
            return False
        with self._lock:
            t = _now()
            if not self.tripped or self.outage_start is None or self._stale_locked(t, budget):
                return False
            return t - self.outage_start >= float(budget)

    def budget_left(self, budget: float | None) -> float | None:
        if not budget:
            return None
        with self._lock:
            t = _now()
            if not self.tripped or self.outage_start is None or self._stale_locked(t, budget):
                return float(budget)
            return max(0.0, float(budget) - (t - self.outage_start))

    def release_probe(self) -> None:
        """Probe kết thúc bằng lỗi KHÔNG phải limit → nhả quyền dò (cửa vẫn trip)."""
        with self._lock:
            self.probe_since = None

    def wait_turn(self, stop_check: Callable[[], bool] | None = None, budget: float | None = None) -> bool:
        """Chờ tới lượt. Trả True nếu luồng này là probe.

        budget (giây): đợt limit đã kéo dài quá ngân sách chung → KHÔNG chờ nữa, đi ngay
        (caller gặp limit lần nữa sẽ bỏ cloud ngay). Đợt cũ đã nguội (không lỗi mới trong
        `budget` giây) → coi như đợt mới."""
        while True:
            if stop_check is not None and stop_check():
                raise CloudRetryStopped()
            with self._lock:
                t = _now()
                if not self.tripped:
                    return False
                if self._stale_locked(t, budget):
                    self.outage_start = None          # nguội → đợt sau tính lại từ đầu
                elif budget and self.outage_start is not None and t - self.outage_start >= float(budget):
                    return False                      # quá ngân sách chung → không chờ
                if t < self.until:
                    dt = self.until - t
                elif self.probe_since is None:
                    self.probe_since = t
                    self.n_probes += 1
                    return True
                elif t - self.probe_since >= PROBE_GRACE_SEC:
                    return False
                else:
                    dt = PROBE_GRACE_SEC - (t - self.probe_since)
            _sleep(max(0.01, min(STOP_SLICE_SEC, dt)))


_GATES: dict[str, CloudGate] = {}
_GATES_LOCK = threading.Lock()


def gate_key(model: str | None) -> str:
    m = (model or "").strip().lower()
    if m.startswith(("chatgpt/", "aurora/")) or m in ("chatgpt", "aurora"):
        return "aurora"
    return "cloud:" + (m.split("/", 1)[0] if "/" in m else m or "default")


def get_gate(model: str | None) -> CloudGate:
    k = gate_key(model)
    with _GATES_LOCK:
        g = _GATES.get(k)
        if g is None:
            g = _GATES[k] = CloudGate(k)
        return g


def reset_gates() -> None:
    with _GATES_LOCK:
        _GATES.clear()


def _sleep_with_stop(sec: float, stop_check: Callable[[], bool] | None) -> None:
    end = _now() + max(0.0, sec)
    while True:
        if stop_check is not None and stop_check():
            raise CloudRetryStopped()
        left = end - _now()
        if left <= 0:
            return
        _sleep(max(0.01, min(STOP_SLICE_SEC, left)))


def stop_checker(stop_path: str | Path | None) -> Callable[[], bool] | None:
    if stop_path is None:
        return None
    p = Path(stop_path)
    return p.exists


def call_with_cloud_retry(
    fn: Callable[[], object],
    *,
    model: str | None = None,
    label: str = "[review]",
    what: str = "",
    stop_check: Callable[[], bool] | None = None,
    retries: int | None = None,
    schedule: list[float] | None = None,
    cfg_get=None,
    gate: CloudGate | None = None,
    log: Callable[[str], None] | None = None,
):
    """Gọi fn() với retry cloud. Lỗi không thuộc limit/transient → ném ngay (hành vi cũ).
    Hết `retries` lần thử lại → ném lại lỗi gốc, gắn `_cloud_retry_exhausted=True` và
    `_cloud_retry_kind`."""
    n_max = retry_count(cfg_get) if retries is None else max(0, int(retries))
    sched = list(schedule) if schedule else backoff_schedule(cfg_get)
    g = gate if gate is not None else get_gate(model)
    who = "Aurora" if gate_key(model) == "aurora" else "cloud"
    say = log or (lambda s: print(s, flush=True))
    budget = outage_budget_sec(n_max, sched)
    max_elapsed = max(MIN_CALL_ELAPSED_SEC, 2.0 * budget)
    t_call0 = _now()
    attempt = 0
    while True:
        is_probe = g.wait_turn(stop_check, budget) if n_max > 0 else False
        t_start = _now()
        s_start = g.current_seq()
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            kind = classify_cloud_retry(e)
            if kind is None or n_max <= 0:
                if is_probe:
                    g.release_probe()
                raise
            ra = retry_after_seconds(e)
            # review 2026-11-12: lý do bỏ cloud sớm (trước khi đủ n_max lần)
            why_stop = ""
            if attempt < n_max:
                if kind == "limit" and g.over_budget(budget):
                    why_stop = f"đợt limit đã chờ quá ngân sách chung {budget:.0f}s"
                elif kind == "limit" and ra is not None and ra > (g.budget_left(budget) or 0.0):
                    why_stop = f"Retry-After {ra:.0f}s vượt ngân sách chờ còn lại"
                elif _now() - t_call0 >= max_elapsed:
                    why_stop = f"lời gọi đã kéo dài {_now() - t_call0:.0f}s ≥ {max_elapsed:.0f}s"
            if attempt >= n_max or why_stop:
                if is_probe:
                    g.release_probe()
                if kind == "limit":
                    g.note_fail()   # giữ đợt limit «nóng» (không đẩy mốc, không đóng lại cửa đã mở)
                try:
                    e._cloud_retry_exhausted = True   # type: ignore[attr-defined]
                    e._cloud_retry_kind = kind        # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
                say(f"{label} {who} {'limit' if kind == 'limit' else 'lỗi tạm'} — đã thử lại "
                    f"{attempt}/{n_max} lần{(' (' + what + ')') if what else ''}"
                    f"{(' · ' + why_stop) if why_stop else ''}, bỏ cloud: {str(e)[:160]}")
                raise
            attempt += 1
            base = sched[min(attempt - 1, len(sched) - 1)] if sched else 30.0
            if ra is not None and ra > 0:
                delay = ra + _rand() * min(5.0, 0.1 * ra)
                src = " (Retry-After)"
            else:
                delay = base * (0.9 + 0.2 * _rand())
                src = ""
            delay = max(1.0, delay)
            if kind == "limit":
                until = g.trip(_now() + delay, budget)
                wait_s = max(0.0, until - _now())
                say(f"{label} {who} limit — thử lại cloud lần {attempt}/{n_max} sau {wait_s:.0f}s{src}"
                    f"{(' · ' + what) if what else ''}: {str(e)[:120]}")
                # chờ ở vòng lặp đầu (wait_turn) — cùng mốc với các luồng khác
            else:
                if is_probe:
                    g.release_probe()
                say(f"{label} {who} lỗi tạm (5xx/timeout) — thử lại cloud lần {attempt}/{n_max} "
                    f"sau {delay:.0f}s{src}{(' · ' + what) if what else ''}: {str(e)[:120]}")
                _sleep_with_stop(delay, stop_check)
            continue
        except BaseException:
            # review 2026-11-12: probe chết vì BaseException (KeyboardInterrupt/SystemExit…) →
            # nhả quyền dò ngay (trước đây luồng khác phải chờ PROBE_GRACE_SEC)
            if is_probe:
                g.release_probe()
            raise
        g.success(t_start, s_start)
        if attempt:
            say(f"{label} {who} OK sau {attempt} lần thử lại{(' · ' + what) if what else ''}")
        return res
