"""scripts/import_voices.py — gộp giọng clone từ cây khác, không ghi đè giọng đã có, chỉ xem khi không --apply."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_voices as iv  # noqa: E402


def _tree(root: Path, reg: dict, files: list[str]) -> Path:
    (root / "voices").mkdir(parents=True)
    (root / "voices" / "f5_voices.json").write_text(json.dumps(reg), encoding="utf-8")
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFF" + f.encode())
    return root


def test_merge_without_overwrite(tmp_path):
    src = _tree(tmp_path / "v2", {"A": {"ref": "voices\\f5_refs\\A.wav", "text": "a"},
                                 "B": {"ref": "voices/clone_refs/B.wav", "text": "b-v2"},
                                 "C": {"ref": "voices/f5_refs/C.wav", "text": "c"},
                                 "X": {"ref": "voices/f5_refs/X.wav", "text": "không có file"}},
                ["voices/f5_refs/A.wav", "voices/clone_refs/B.wav", "voices/f5_refs/C.wav"])
    dst = _tree(tmp_path / "v6", {"B": {"ref": "voices/clone_refs/B.wav", "text": "b-v6"},
                                 "C": {"ref": "voices/f5_refs/C.wav", "text": "c"},
                                 "v57": {"ref": "voices/clone_refs/v57.wav", "text": "riêng v6"}},
                ["voices/clone_refs/B.wav", "voices/clone_refs/v57.wav"])
    before = (dst / "voices" / "f5_voices.json").read_text(encoding="utf-8")
    acts = {s["name"]: s["action"] for s in iv.plan(src, dst)}
    assert acts == {"A": "add", "B": "skip", "C": "fix_file", "X": "missing_src"}
    assert iv.main(["--src", str(src), "--dst", str(dst)]) == 0          # không --apply → không ghi gì
    assert (dst / "voices" / "f5_voices.json").read_text(encoding="utf-8") == before
    iv.main(["--src", str(src), "--dst", str(dst), "--apply"])
    reg = json.loads((dst / "voices" / "f5_voices.json").read_text(encoding="utf-8"))
    assert reg["A"]["ref"] == "voices/f5_refs/A.wav" and (dst / "voices/f5_refs/A.wav").is_file()
    assert reg["B"]["text"] == "b-v6" and reg["v57"]["text"] == "riêng v6"   # không ghi đè giọng đã có
    assert (dst / "voices/f5_refs/C.wav").is_file() and "X" not in reg
