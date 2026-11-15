"""Hongguo 2 — 2026-11-15: Browse không còn mất phim giữa các trang; Ranking không còn cứng top 100."""
from __future__ import annotations

import agents.hongguo_rpc as rpc


def _cards(a, b):
    return [{"id": str(i), "title": f"P{i}"} for i in range(a, b)]


def test_feed_returns_every_gathered_card_so_next_page_loses_nothing(monkeypatch):
    # engine trả 20 thẻ / lần, tổng 100 thẻ
    def call(method, params, timeout=None):
        off = int(params.get("offset") or 0)
        return {"cards": _cards(off, min(off + 20, 100)), "nextOffset": off + 20, "hasMore": off + 20 < 100,
                "sessionId": "s1"}
    monkeypatch.setattr(rpc, "call", call)
    monkeypatch.setattr(rpc, "_card_to_result", lambda c: {"series_id": c["id"]})
    got, off, sess = [], 0, ""
    for _ in range(10):
        d = rpc.feed(offset=off, session_id=sess, limit=24)
        got += [r["series_id"] for r in d["results"]]
        off, sess = d["nextOffset"], d["sessionId"]
        if not d["hasMore"]:
            break
    assert got == [str(i) for i in range(100)]          # trước: mỗi trang cắt 24/40 → mất 16 phim / trang


def test_rank_pages_beyond_100(monkeypatch):
    total = 230
    state = {"n": 0}

    def call(method, params, timeout=None):
        if method == "rank.page":
            state["n"] = 10
            return {"cards": _cards(0, 10), "hasMore": True, "chart": params.get("chart"), "charts": []}
        a = state["n"]
        state["n"] = min(a + 10, total)
        return {"cards": _cards(a, state["n"]), "hasMore": state["n"] < total}
    monkeypatch.setattr(rpc, "call", call)
    monkeypatch.setattr(rpc, "_card_to_result", lambda c: {"series_id": c["id"]})
    rpc._RANK_CACHE.clear()
    d1 = rpc.rank(chart="hot", limit=100)
    assert len(d1["results"]) == 100 and d1["hasMore"] and d1["nextOffset"] == 100
    d2 = rpc.rank(chart="hot", offset=100, limit=100)
    d3 = rpc.rank(chart="hot", offset=200, limit=100)
    ids = [r["series_id"] for d in (d1, d2, d3) for r in d["results"]]
    assert ids == [str(i) for i in range(total)] and not d3["hasMore"]
