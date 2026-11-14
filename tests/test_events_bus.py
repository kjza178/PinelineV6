import threading
import time

from events.bus import EventBus


def test_publish_subscribe_and_sse():
    b = EventBus()
    s = b.subscribe()
    b.publish("job.updated", {"job_id": "a", "status": "running"})
    ev = s.get(timeout=1)
    assert ev.type == "job.updated" and ev.data["job_id"] == "a"
    txt = ev.sse()
    assert txt.startswith(f"id: {ev.seq}\nevent: job.updated\ndata: ") and txt.endswith("\n\n")


def test_progress_throttled_then_flushed():
    b = EventBus(throttle_sec=0.1)
    s = b.subscribe()
    for i in range(50):
        b.publish("stage.progress", {"job_id": "a", "i": i})
    first = s.get(timeout=0.1)
    assert first.data["i"] == 0 and s.get(timeout=0.05) is None   # 49 cái bị gộp
    time.sleep(0.4)
    assert b.flush() == 1
    assert s.get(timeout=0.1).data["i"] == 49                     # chỉ giữ bản mới nhất


def test_throttle_is_per_job():
    b = EventBus(throttle_sec=10)
    s = b.subscribe()
    b.publish("stage.progress", {"job_id": "a"})
    b.publish("stage.progress", {"job_id": "b"})
    assert {s.get(0.1).data["job_id"], s.get(0.1).data["job_id"]} == {"a", "b"}


def test_slow_subscriber_drops_oldest_not_blocks():
    b = EventBus(sub_maxsize=3)
    s = b.subscribe()
    t0 = time.monotonic()
    for i in range(100):
        b.publish("job.updated", {"i": i})
    assert time.monotonic() - t0 < 1
    got = [s.get(0.05).data["i"] for _ in range(3)]
    assert got == [97, 98, 99] and s.dropped == 97


def test_replay_since_last_event_id():
    b = EventBus()
    e1 = b.publish("x", {"n": 1})
    b.publish("x", {"n": 2})
    s = b.subscribe(since=e1.seq)
    assert s.get(0.1).data["n"] == 2


def test_sse_lines_keepalive_and_unsubscribe():
    b = EventBus()
    s = b.subscribe()
    stop = threading.Event()
    gen = b.sse_lines(s, keepalive_sec=0.05, stop=stop)
    assert next(gen).startswith("retry:")
    assert next(gen) == ": keepalive\n\n"
    b.publish("y", {})
    assert "event: y" in next(gen)
    gen.close()
    assert b.n_subscribers == 0


# ───── SSE async (fix 2026-11-13: route /api/events không giữ thread của threadpool) ─────

def test_async_subscription_receives_from_other_thread_and_unsubscribes():
    import asyncio

    b = EventBus()

    async def run():
        sub = b.subscribe_async()
        assert b.n_subscribers == 1
        gen = b.sse_stream(sub, keepalive_sec=0.05)
        assert (await gen.__anext__()).startswith("retry:")
        t = threading.Thread(target=lambda: b.publish("job.updated", {"job_id": "a"}))
        t.start()
        t.join()
        chunk = await gen.__anext__()
        assert "event: job.updated" in chunk and '"job_id":"a"' in chunk
        assert (await gen.__anext__()).startswith(": keepalive")     # hết event → keepalive, không treo
        await gen.aclose()
        assert b.n_subscribers == 0

    asyncio.run(run())


def test_async_subscription_replays_history_and_drops_oldest_when_full():
    import asyncio

    b = EventBus(sub_maxsize=3)
    e1 = b.publish("x", {"n": 1})
    b.publish("x", {"n": 2})

    async def run():
        sub = b.subscribe_async(since=e1.seq)
        assert (await sub.get(0.2)).data["n"] == 2
        for n in range(10, 15):
            b.publish("x", {"n": n})
        await asyncio.sleep(0)
        got = []
        while (ev := await sub.get(0.05)) is not None:
            got.append(ev.data["n"])
        assert got == [12, 13, 14] and sub.dropped == 2
        b.unsubscribe(sub)

    asyncio.run(run())


def test_closed_loop_subscriber_is_pruned_not_raising():
    import asyncio

    b = EventBus()
    loop = asyncio.new_event_loop()
    b.subscribe_async(loop=loop)
    loop.close()
    b.publish("x", {"n": 1})      # loop đã đóng → không văng lỗi cho publisher
    b.publish("x", {"n": 2})      # lần sau: đã bị gỡ
    assert b.n_subscribers == 0
