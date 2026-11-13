"""Slim plan block for pronoun-QA — tránh Aurora 413."""
from __future__ import annotations

from engines.translate import _plan_block, _plan_block_for_qa


def _fat_plan(n_chars: int = 50, n_pronouns: int = 20, n_terms: int = 80) -> dict:
    chars = [
        {
            "zh": f"角色{i}",
            "vi": f"NhânVật{i}",
            "gender": "male" if i % 2 == 0 else "female",
            "role": "chính" if i < 3 else "phụ",
            "aliases": [f"A{i}", f"B{i}"],
            "gender_hint": "他" if i % 2 == 0 else "她",
            "self_ref": "ta" if i % 3 == 0 else "tôi",
            "relation": f"quan hệ với NV{i % 5}",
            "ref3": "hắn" if i % 2 == 0 else "nàng",
        }
        for i in range(n_chars)
    ]
    pronouns = [
        {
            "who": f"NhânVật{i}",
            "to": f"NhânVật{(i + 1) % n_chars}",
            "self": "anh" if i % 2 == 0 else "em",
            "call": "em" if i % 2 == 0 else "anh",
        }
        for i in range(n_pronouns)
    ]
    terms = [
        {"zh": f"术语{i}", "vi": f"Thuật ngữ {i}", "type": "lock" if i < 10 else "term"}
        for i in range(n_terms)
    ]
    return {
        "content_type": "phim",
        "genre": "đô thị dị năng",
        "tone": "hiện đại",
        "characters": chars,
        "terms": terms,
        "pronouns": pronouns,
        "pronoun_notes": "Giữ anh–em xuyên suốt cặp chính.",
        "style_lock": {
            "mode": "modern",
            "pronoun_policy": "anh_em",
            "emotional_style": "trầm ổn",
            "forbid": ["tại hạ", "bổn tọa", "trẫm"],
            "prefer": ["anh", "em", "cậu", "tớ"],
        },
        "pronouns_locked": True,
    }


def test_plan_block_for_qa_much_smaller():
    plan = _fat_plan()
    full = _plan_block(plan)
    slim = _plan_block_for_qa(plan)
    assert len(slim) < len(full) // 2, f"slim={len(slim)} full={len(full)}"
    assert "XƯNG HÔ" in slim
    assert "NhânVật0" in slim
    assert "Giữ anh–em" in slim
    assert "STYLE LOCK" in slim
    # characters dump + terms không có trong slim
    assert "NHÂN VẬT" not in slim
    assert "THUẬT NGỮ" not in slim
    assert "THÀNH NGỮ" not in slim


def test_plan_block_for_qa_keeps_pronouns_only():
    plan = _fat_plan(n_chars=50, n_pronouns=20)
    slim = _plan_block_for_qa(plan)
    for i in range(20):
        assert f"NhânVật{i}" in slim
