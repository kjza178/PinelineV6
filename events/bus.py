"""events/bus.py — Bus sự kiện trong tiến trình + luồng SSE cho UI (thay polling 2 s).

    from events.bus import bus
    bus.publish("job.updated", {"job_id": jid, ...})
    sub = bus.subscribe(); sub.get(timeout=15) / for chunk in bus.sse_lines(sub): ...

- Không phụ thuộc FastAPI: route SSE bọc `sse_stream()` (async) trong StreamingResponse.
  `sse_lines()` (đồng bộ) còn cho script/test — KHÔNG dùng cho route: mỗi client giữ 1 thread của
  threadpool (40) suốt kết nối → ~40 client là mọi route `def` đứng (fix 2026-11-13).
- `stage.progress` được gộp (throttle) theo job → UI tối đa ~4 cập nhật/giây/job.
- Subscriber chậm không làm nghẽn publisher: hàng đợi có trần, đầy thì bỏ sự kiện cũ nhất.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

THROTTLED = {"stage.progress", "job.touched"}   # job.touched: V6 F3 (state.index)


@dataclass
class Event:
    type: str
    data: dict
    seq: int
    ts: float = field(default_factory=time.time)

    def sse(self) -> str:
        payload = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        return f"id: {self.seq}\nevent: {self.type}\ndata: {payload}\n\n"


class Subscription:
    def __init__(self, maxsize: int) -> None:
        self.q: queue.Queue[Event] = queue.Queue(maxsize=maxsize)
        self.dropped = 0

    def put(self, ev: Event) -> None:
        try:
            self.q.put_nowait(ev)
        except queue.Full:
            try:
                self.q.get_nowait()
                self.dropped += 1
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(ev)
            except queue.Full:
                self.dropped += 1

    def get(self, timeout: float | None = None) -> Event | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None


class AsyncSubscription:
    """Subscriber cho route SSE async: nhận event từ BẤT KỲ thread nào, đọc trong event loop.

    Không giữ thread nào trong lúc chờ (khác Subscription + sse_lines). `put` chỉ hẹn việc vào loop
    (call_soon_threadsafe) nên publisher không bao giờ bị chặn; hàng đầy thì bỏ event cũ nhất."""

    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int) -> None:
        self.loop = loop
        self.maxsize = maxsize
        self.q: asyncio.Queue[Event] = asyncio.Queue()
        self.dropped = 0
        self.closed = False

    def put(self, ev: Event) -> None:
        if self.closed:
            return
        try:
            self.loop.call_soon_threadsafe(self._put_now, ev)
        except RuntimeError:        # loop đã đóng (server tắt) → bus tự gỡ ở lần emit sau
            self.closed = True

    def _put_now(self, ev: Event) -> None:
        if self.q.qsize() >= self.maxsize:
            try:
                self.q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.dropped += 1
        self.q.put_nowait(ev)

    async def get(self, timeout: float | None = None) -> Event | None:
        try:
            return await asyncio.wait_for(self.q.get(), timeout)
        except asyncio.TimeoutError:
            return None


class EventBus:
    def __init__(self, *, throttle_sec: float = 0.25, sub_maxsize: int = 500, history: int = 200) -> None:
        self._lock = threading.Lock()
        self._subs: list[Subscription | AsyncSubscription] = []
        self._seq = 0
        self._throttle = throttle_sec
        self._last_emit: dict[tuple[str, str], float] = {}
        self._pending: dict[tuple[str, str], dict] = {}
        self._sub_maxsize = sub_maxsize
        self._history: list[Event] = []
        self._history_max = history

    # ---- publish ----
    def publish(self, type_: str, data: dict[str, Any]) -> Event | None:
        now = time.monotonic()
        with self._lock:
            if type_ in THROTTLED:
                key = (type_, str(data.get("job_id", "")))
                last = self._last_emit.get(key)
                if last is not None and now - last < self._throttle:
                    self._pending[key] = dict(data)   # giữ bản mới nhất, flush() sẽ đẩy
                    return None
                self._last_emit[key] = now
                self._pending.pop(key, None)
            return self._emit_locked(type_, data)

    def flush(self) -> int:
        """Đẩy các stage.progress đang bị giữ (gọi định kỳ từ runtime, vd mỗi 0.5 s)."""
        n = 0
        now = time.monotonic()
        with self._lock:
            for key, data in list(self._pending.items()):
                last = self._last_emit.get(key)
                if last is None or now - last >= self._throttle:
                    self._last_emit[key] = now
                    self._pending.pop(key, None)
                    self._emit_locked(key[0], data)
                    n += 1
        return n

    def _emit_locked(self, type_: str, data: dict) -> Event:
        self._seq += 1
        ev = Event(type_, dict(data), self._seq)
        self._history.append(ev)
        del self._history[:-self._history_max]
        for s in list(self._subs):
            if getattr(s, "closed", False):     # AsyncSubscription mà loop đã đóng
                self._subs.remove(s)
                continue
            s.put(ev)
        return ev

    # ---- subscribe ----
    def subscribe(self, *, since: int | None = None) -> Subscription:
        s = Subscription(self._sub_maxsize)
        with self._lock:
            if since is not None:   # Last-Event-ID: gửi bù sự kiện đã lỡ
                for ev in self._history:
                    if ev.seq > since:
                        s.put(ev)
            self._subs.append(s)
        return s

    def subscribe_async(self, *, since: int | None = None,
                        loop: asyncio.AbstractEventLoop | None = None) -> AsyncSubscription:
        """Như subscribe nhưng cho route async — gọi TRONG event loop (hoặc truyền `loop`)."""
        s = AsyncSubscription(loop or asyncio.get_running_loop(), self._sub_maxsize)
        with self._lock:
            if since is not None:
                for ev in self._history:
                    if ev.seq > since:
                        s.put(ev)
            self._subs.append(s)
        return s

    async def sse_stream(self, s: AsyncSubscription, *, keepalive_sec: float = 15.0) -> AsyncIterator[str]:
        """Bản async của sse_lines: chờ event bằng await → không tốn thread; client ngắt thì Starlette
        huỷ generator → `finally` gỡ subscriber ngay (không đợi hết keepalive)."""
        try:
            yield "retry: 2000\n\n"
            while True:
                ev = await s.get(timeout=keepalive_sec)
                yield ev.sse() if ev else ": keepalive\n\n"
        finally:
            s.closed = True
            self.unsubscribe(s)

    def unsubscribe(self, s: Subscription | AsyncSubscription) -> None:
        with self._lock:
            if s in self._subs:
                self._subs.remove(s)

    def sse_lines(self, s: Subscription, *, keepalive_sec: float = 15.0,
                  stop: threading.Event | None = None) -> Iterator[str]:
        """Sinh chuỗi SSE; comment keepalive để proxy/WebView không cắt kết nối."""
        try:
            yield "retry: 2000\n\n"
            while not (stop and stop.is_set()):
                ev = s.get(timeout=keepalive_sec)
                yield ev.sse() if ev else ": keepalive\n\n"
        finally:
            self.unsubscribe(s)

    @property
    def n_subscribers(self) -> int:
        with self._lock:
            return len(self._subs)


bus = EventBus()
