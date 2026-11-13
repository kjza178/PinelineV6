"""B11 (F6) — test đặc tả cho module rủi ro chưa có test (review V2 2026-11-11: agent_tele_cmd, agent_feeder,
clone_tts). Chỉ hàm thuần: không mạng, không Telegram, không GPU. Ghi lại hành vi HIỆN TẠI để đổi sau không lệch ngầm.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_telegram(monkeypatch):
    monkeypatch.setenv("AGENT_TELEGRAM", "0")
    monkeypatch.setenv("PINELINE_FLAG_TELEGRAM", "0")


# ───────────── agents/agent_feeder ─────────────

def test_feeder_normalize_spec_clamps_and_defaults():
    from agents import agent_feeder as f
    s = f.normalize_spec({"n": 999, "mode": "x", "sources": ["bad"], "include": "a, b;c", "order": "z",
                          "hg_min_score": "99", "hg_max_per_day": "abc"})
    assert s["n"] == 50 and s["mode"] == "count" and s["sources"] == ["lib_merged"]
    assert s["include"] == ["a", "b", "c"] and s["order"] == "smart"
    assert s["hg_min_score"] == 10.0 and s["hg_max_per_day"] == f.DEFAULT_SPEC["hg_max_per_day"]
    assert f.normalize_spec({"n": 0})["n"] == 1
    assert f.normalize_spec(None)["sources"] == f.normalize_spec({})["sources"]


def test_feeder_describe_spec_mentions_sources():
    from agents import agent_feeder as f
    txt = f.describe_spec(f.normalize_spec({"sources": ["lib_merged", "hongguo"], "n": 2, "hg_query": "abc"}))
    assert "phim đã ghép" in txt and "2 phim/lần" in txt and "«abc»" in txt


def test_feeder_parse_request_rules():
    from agents import agent_feeder as f
    d = f.parse_request_rules('thêm 3 phim hongguo điểm >= 8.5 "Tổng tài"')
    assert d["mode"] == "count" and d["n"] == 3
    assert d["sources"] == ["lib_merged", "hongguo"] and d["hg_min_score"] == 8.5
    assert d["include"] == ["Tổng tài"]
    d = f.parse_request_rules("tất cả phim đã ghép")
    assert d["mode"] == "all" and "lib_merged" in d["sources"]
    assert f.parse_request_rules("2 bộ")["mode"] == "series"


def test_feeder_small_helpers():
    from agents import agent_feeder as f
    assert f._fmt_dur(3725) == "1h02m" and f._fmt_dur(65) == "1m05s" and f._fmt_dur(-3) == "0m00s"
    assert f._fold("Đường Tăng") == "duong tang"
    assert f.lib_verb("add") == "add" and f.lib_verb("xyz") is None
    assert f.lib_key("abc") == f.lib_key("abc") and len(f.lib_key("abc")) == 10


# ───────────── agents/agent_tele_cmd ─────────────

def test_tele_priority_cmd():
    from agents import agent_tele_cmd as t
    assert t.is_priority_cmd("/stop@bot x") is True
    assert t.is_priority_cmd("/status") is True
    assert t.is_priority_cmd("stop") is False
    assert t.is_priority_cmd("/feed 3") is False


def test_tele_slash_hint_requires_slash_for_mutating_text():
    from agents import agent_tele_cmd as t
    kind, msg = t._slash_hint("feed 3")
    assert kind == "hint" and "/feed 3" in msg
    assert t._slash_hint("lib add 3")[0] == "hint"
    assert t._slash_hint("/feed") == (None, "")
    assert t._slash_hint("xin chào bạn") == (None, "")


def test_tele_telegram_follows_instance_flag():
    from agents import agent_tele_cmd as t
    assert t._telegram_allowed() is False
    assert t.tg_send("x") is False          # cờ tắt → không gửi (không mạng)


# ───────────── engines/clone_tts ─────────────

def test_clone_tts_text_helpers():
    from engines import clone_tts as c
    assert c._is_trivial_vi("...") is True and c._is_trivial_vi("") is True
    assert c._is_trivial_vi("Chào") is False
    assert c.vi_for_tts("Giá 30 000 đồng và 31.000") == "Giá 30000 đồng và 31000"
    assert c._clip_key("a", "v", "e", 1) == c._clip_key("a", "v", "e", 1.0)
    assert c._clip_key("a", "v", "e", 1) != c._clip_key("a", "v", "e", 1.1)
