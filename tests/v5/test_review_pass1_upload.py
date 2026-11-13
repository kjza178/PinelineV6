"""Pass1: GPT đọc FULL file qua upload; chunk merge; chat fallback tắt mặc định."""
from __future__ import annotations

from pathlib import Path

import engines.translate as T


def test_merge_chunk_results():
    merged = T._pass1_merge_chunk_results([
        {"style_brief": "giọng A", "register": "toi-cau", "nonsense_i": [1, 2], "half_cue_pairs": [[1, 2]]},
        {"style_brief": "giọng B", "nonsense_i": [2, 9], "half_cue_pairs": [[8, 9]], "notes": ["x"]},
    ])
    assert "giọng A" in merged["style_brief"] and "giọng B" in merged["style_brief"]
    assert merged["nonsense_i"] == [1, 2, 9]
    assert merged["half_cue_pairs"] == [[1, 2], [8, 9]]
    assert merged["n_chunks"] == 2
    assert merged["register"] == "toi-cau"


def test_chunk_cues_default(monkeypatch):
    monkeypatch.setattr(T, "_cfg_get", lambda k, d=None: {}.get(k, d))
    assert T._pass1_chunk_cues() == 1000


def test_write_chunk_srt_name(tmp_path: Path):
    rows = [{"i": i, "zh": f"中{i}", "vi": f"vi{i}", "start": i, "end": i + 1} for i in range(5)]
    p = T.write_review_pair_srt(tmp_path, rows, name="sub.review_pair.p01.srt")
    assert p.name == "sub.review_pair.p01.srt"
    assert p.is_file() and p.stat().st_size > 0


def test_max_upload_bytes_default(monkeypatch):
    monkeypatch.setattr(T, "_cfg_get", lambda k, d=None: {}.get(k, d))
    assert T._pass1_max_upload_bytes() == 5_242_880
