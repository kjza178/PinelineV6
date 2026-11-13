"""Tests deterministic quality scan/heal (Second Brain L2)."""
from __future__ import annotations

from agents.agent_quality import apply_safe_heals, scan_script, summarize_flags


def test_meidameixiao_calque_flagged_and_healed():
    rows = [
        {
            "i": 8,
            "zh": "你还没大没小了。",
            "vi": "Cậu còn chưa lớn không nhỏ.",
            "mt": 1,
            "rv": 1,
        }
    ]
    flags = scan_script(rows)
    assert any(f.get("kind") == "calque" and f.get("sev") == "high" for f in flags)
    n = apply_safe_heals(rows)
    assert n == 1
    assert "không biết trên dưới" in rows[0]["vi"]
    flags2 = scan_script(rows)
    assert not any(
        f.get("kind") == "calque" and "没大没小" in (f.get("note") or "")
        for f in flags2
    )


def test_duong_duong_not_stutter():
    rows = [{
        "i": 11,
        "zh": "我堂堂轮回境",
        "vi": "Tôi đường đường là Luân Hồi Cảnh.",
        "rv": 1,
    }]
    flags = scan_script(rows)
    assert not any(f.get("kind") == "stutter" for f in flags)


def test_hang_cho_nen_high():
    rows = [{
        "i": 200,
        "zh": "谁也说不清。所以",
        "vi": "Không ai nói rõ được. Cho nên",
        "rv": 1,
    }]
    flags = scan_script(rows)
    hang = [f for f in flags if f.get("kind") == "hang"]
    assert hang and hang[0]["sev"] == "high"


def test_hang_ack_skips_flag():
    rows = [{
        "i": 201,
        "zh": "我是说。",
        "vi": "Ý tôi là.",
        "rv": 1,
        "hang_ack": 1,
    }]
    flags = scan_script(rows)
    assert not any(f.get("kind") == "hang" for f in flags)


def test_name_echo_thanh_gia():
    rows = [{
        "i": 878,
        "zh": "圣者你也是圣者废话。",
        "vi": "Thánh Giả cậu cũng là Thánh Giả nói nhảm.",
        "rv": 1,
    }]
    flags = scan_script(rows)
    assert any(f.get("kind") == "name_echo" for f in flags)
    s = summarize_flags(flags)
    assert s["n_high"] >= 1
