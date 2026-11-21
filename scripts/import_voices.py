"""Chép giọng clone từ cây khác (mặc định V2) sang V6 — gộp danh sách, KHÔNG ghi đè giọng đã có.

Giọng clone = 1 dòng trong voices/f5_voices.json ({tên: {ref: đường dẫn wav, text: lời của file mẫu}}) + file wav
(voices/f5_refs, voices/clone_refs, voices/vieneu_bac …). Git chỉ mang file json, không mang wav (*.wav bị bỏ qua) →
máy mới có danh sách nhưng thiếu file, giọng không hiện.

Chạy (mặc định chỉ XEM sẽ làm gì; thêm --apply để chép thật):
    python scripts/import_voices.py --src Z:\\PinelineV2
    python scripts/import_voices.py --src Z:\\PinelineV2 --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _rel(ref: str) -> Path:
    return Path(str(ref).replace("\\", "/"))


def _abs(tree: Path, ref: str) -> Path:
    p = _rel(ref)
    return p if p.is_absolute() else tree / p


def _load(f: Path) -> dict:
    if not f.is_file():
        return {}
    return json.loads(f.read_text(encoding="utf-8-sig"))


def plan(src: Path, dst: Path) -> list[dict]:
    """Mỗi giọng của cây nguồn: action = add (thêm mới) | fix_file (đã có tên, thiếu wav) | skip (đã đủ) |
    missing_src (nguồn cũng không có wav)."""
    s_reg, d_reg = _load(src / "voices" / "f5_voices.json"), _load(dst / "voices" / "f5_voices.json")
    out = []
    for name, v in s_reg.items():
        ref = str((v or {}).get("ref") or "")
        s_file = _abs(src, ref)
        if not ref or not s_file.is_file():
            out.append({"name": name, "action": "missing_src", "src": str(s_file)})
            continue
        rel = _rel(ref)
        if rel.is_absolute():                     # wav nằm ngoài cây nguồn → chép vào voices/clone_refs
            rel = Path("voices") / "clone_refs" / s_file.name
        if name in d_reg:
            d_file = _abs(dst, str(d_reg[name].get("ref") or ""))
            act = "skip" if d_file.is_file() else "fix_file"
            out.append({"name": name, "action": act, "src": str(s_file), "dst": str(d_file), "entry": d_reg[name]})
            continue
        out.append({"name": name, "action": "add", "src": str(s_file), "dst": str(dst / rel),
                    "entry": {**v, "ref": rel.as_posix()}})
    return out


def apply(src: Path, dst: Path) -> list[dict]:
    steps = plan(src, dst)
    reg_file = dst / "voices" / "f5_voices.json"
    reg = _load(reg_file)
    for st in steps:
        if st["action"] not in ("add", "fix_file"):
            continue
        d = Path(st["dst"])
        d.parent.mkdir(parents=True, exist_ok=True)
        if not d.exists():
            shutil.copy2(st["src"], d)
        if st["action"] == "add":
            reg[st["name"]] = st["entry"]
    reg_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = reg_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(reg_file)
    return steps


VI = {"add": "THÊM", "fix_file": "BỔ SUNG FILE", "skip": "đã có", "missing_src": "NGUỒN THIẾU WAV"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=r"Z:\PinelineV2", help="cây nguồn (có thư mục voices\\)")
    ap.add_argument("--dst", default=str(ROOT), help="cây đích (mặc định cây V6 này)")
    ap.add_argument("--apply", action="store_true", help="chép thật (không có cờ này = chỉ xem)")
    a = ap.parse_args(argv)
    src, dst = Path(a.src), Path(a.dst)
    if not (src / "voices" / "f5_voices.json").is_file():
        print(f"Không thấy {src / 'voices' / 'f5_voices.json'} — kiểm --src", file=sys.stderr)
        return 2
    steps = apply(src, dst) if a.apply else plan(src, dst)
    for st in steps:
        print(f"  {VI[st['action']]:<16} {st['name']:<24} {st.get('dst') or st['src']}")
    n = sum(st["action"] in ("add", "fix_file") for st in steps)
    print(f"\n{'Đã chép' if a.apply else 'Sẽ chép'} {n} giọng."
          + ("" if a.apply else " Thêm --apply để chép thật.") + (" Khởi động lại :8806 không cần — danh sách đọc lúc mở." if a.apply else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
