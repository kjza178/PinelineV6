"""Lock / ledger / QA nhảy ngôi — không cần LLM."""
from __future__ import annotations

import engines.pronoun_consistency as pc


def test_mark_and_banner():
    plan = {
        "genre": "đô thị dị năng",
        "pronouns": [{"who": "A", "to": "B", "self": "anh", "call": "em"}],
    }
    plan = pc.mark_locked(plan)
    assert pc.is_pronouns_locked(plan)
    b = pc.lock_banner(plan)
    assert "KHÓA" in b
    assert "A→B" in b


def test_merge_pronouns_incoming_wins():
    base = [{"who": "A", "to": "B", "self": "tôi", "call": "cậu"}]
    inc = [{"who": "A", "to": "B", "self": "anh", "call": "em"}]
    m = pc._merge_pronouns(base, inc)
    assert len(m) == 1
    assert m[0]["self"] == "anh"
    assert m[0]["call"] == "em"


def test_find_jumps_anh_em_vs_ong():
    rows = [
        {"i": 0, "zh": "a", "vi": "Anh đi đây."},
        {"i": 1, "zh": "b", "vi": "Em đợi anh."},
        {"i": 2, "zh": "c", "vi": "Ông đừng tới nữa."},
    ]
    jumps = pc.find_pronoun_jumps(rows, window=3)
    assert any(j["i"] == 2 for j in jumps)


def test_ledger_from_prev():
    prev = [{"zh": "我走了", "vi": "Anh đi đây."}, {"zh": "好", "vi": "Em ừ."}]
    t = pc.ledger_from_prev(prev)
    assert "LEDGER" in t
    assert "anh" in t.lower() or "em" in t.lower()


def test_write_zh_srt(tmp_path):
    lines = [{"i": 0, "zh": "你好"}, {"i": 1, "zh": "你过来"}]
    segs = [
        {"i": 0, "start": 0.0, "end": 1.0, "text": "你好"},
        {"i": 1, "start": 1.0, "end": 2.5, "text": "你过来"},
    ]
    p = pc.write_zh_srt(tmp_path, lines, segments=segs)
    assert p.is_file()
    body = p.read_text(encoding="utf-8")
    assert "你好" in body
    assert "-->" in body


def test_select_analyze_lines_fast_shrinks():
    lines = [{"i": i, "zh": ("你过来" if i % 7 == 0 else "你好") + str(i)} for i in range(3000)]
    # force fast path
    out = pc.select_analyze_lines(lines, mode="fast")
    assert len(out) < len(lines)
    assert out[0]["i"] == 0
    assert out[-1]["i"] == 2999


def test_filter_review_fixes_when_locked():
    plan = pc.mark_locked({
        "genre": "đô thị",
        "pronouns": [{"who": "A", "to": "B", "self": "anh", "call": "em"}],
    })
    rows = [{"i": 1, "zh": "我走了", "vi": "Anh đi đây."}]
    fixes = {1: "Ông đi đây."}
    out = pc.filter_review_fixes_for_lock(fixes, rows, plan)
    assert 1 not in out  # đổi anh→ông bị bỏ
    assert pc.fix_preserves_locked_register("Anh đi đây.", "Anh đi ngay.")
