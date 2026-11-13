"""Deterministic pronoun post-pass (không cần LLM)."""
from __future__ import annotations

from engines.translate import apply_pronoun_postpass, _is_modernish_genre


def test_modernish_mixed():
    plan = {"genre": "đô thị dị năng", "style_lock": {"mode": "mixed"}}
    assert _is_modernish_genre(plan)


def test_detox_nguoi_ta():
    plan = {
        "genre": "đô thị dị năng võ thuật",
        "style_lock": {"mode": "mixed"},
        "pronouns": [
            {"who": "Tô Lâm", "to": "Quá Sơn", "self": "tôi", "call": "cậu"},
        ],
        "characters": [
            {"zh": "苏林", "vi": "Tô Lâm"},
            {"zh": "过山", "vi": "Quá Sơn"},
        ],
    }
    rows = [
        {"i": 0, "zh": "苏林看着过山：你完了。", "vi": "Ta bảo ngươi: ngươi xong rồi."},
        {"i": 1, "zh": "过山冷笑。", "vi": "Quá Sơn cười lạnh."},
    ]
    n = apply_pronoun_postpass(rows, plan)
    assert n >= 1
    assert "ngươi" not in rows[0]["vi"].lower()
    assert "ta " not in rows[0]["vi"].lower() or rows[0]["vi"].lower().startswith("tôi")
    # câu không có đại từ lệch — giữ nguyên
    assert rows[1]["vi"] == "Quá Sơn cười lạnh."


def test_skip_manual():
    plan = {"style_lock": {"mode": "modern"}, "genre": "đô thị"}
    rows = [{"i": 0, "zh": "你过来", "vi": "Ngươi lại đây.", "manual": 1}]
    assert apply_pronoun_postpass(rows, plan) == 0
    assert rows[0]["vi"] == "Ngươi lại đây."
