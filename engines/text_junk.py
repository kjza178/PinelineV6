"""Nhận diện câu ASR «rác» (fix 2026-11-13 agentM, sự cố 60892cc63cb7_p01 i=1039 zh="None.").

Rác = KHÔNG có nội dung để dịch / lồng tiếng:
  - rỗng / chỉ khoảng trắng / chỉ dấu câu, ký hiệu (không có ký tự CJK, chữ cái hay chữ số nào)
  - chữ «None» / «null» / «nan» (± dấu câu, hoa thường) — FireRedASR2 AED đôi khi giải mã đoạn
    ồn ngắn thành token tiếng Anh «none»; FireRedPunc (RuleBaedTxtFix) viết hoa + thêm dấu → «None.»

Dùng chung: firered_asr._parse_firered_result (bỏ ngay từ ASR) và translate_simple (câu rác
đã nằm sẵn trong asr.json/script.json cũ → tự soát, không dịch, không lồng tiếng).
"""
from __future__ import annotations

import re

NULL_WORDS = frozenset({"none", "null", "nan"})
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def is_junk_text(s) -> bool:
    """True khi s không có gì để dịch/đọc (xem docstring module). Chữ Hán/chữ/số thật → False."""
    if s is None:
        return True
    core = _NON_WORD.sub("", str(s))
    if not core:
        return True
    return core.lower() in NULL_WORDS
