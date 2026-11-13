"""CLI: python -m tools.context_studio analyze <srt|asr.json> -o work/<job>

Chỉ PRE-SCAN — không dịch.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# cho phép chạy: python -m tools.context_studio ...
from .analyzer import analyze, summarize
from .character_mapper import enrich_characters, validate_pronouns
from .exporter import write_pack
from .schema import scan_number_flags
from .srt_io import load_segments


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="context_studio",
        description="PRE-SCAN SRT Trung → glossary + style_lock + qa_flags (cho Pineline dịch)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("analyze", help="Phân tích SRT/asr.json → context pack")
    p.add_argument("input", help="File .srt hoặc asr.json / script.json")
    p.add_argument("-o", "--out", required=True, help="Thư mục output (vd work/<job_id>)")
    p.add_argument("--llm", action="store_true", help="Gọi LLM (cần Pineline llm.py + key)")
    p.add_argument("--model", default=None, help="Override TRANSLATE_MODEL")
    p.add_argument("--print-summary", action="store_true")

    p2 = sub.add_parser("summary", help="In tóm tắt glossary.json đã có")
    p2.add_argument("glossary", help="Đường dẫn glossary.json")

    args = ap.parse_args(argv)

    if args.cmd == "summary":
        g = json.loads(Path(args.glossary).read_text(encoding="utf-8"))
        print(json.dumps(summarize(g), ensure_ascii=False, indent=2))
        return 0

    segs = load_segments(args.input)
    print(f"[context_studio] loaded {len(segs)} lines from {args.input}")
    glossary = analyze(segs, use_llm=bool(args.llm), model=args.model)
    glossary = enrich_characters(glossary, segments=segs)
    warns = validate_pronouns(glossary)
    for w in warns[:20]:
        print(f"[warn] {w}")
    flags = scan_number_flags(segs)
    paths = write_pack(args.out, glossary, flags)
    print("[context_studio] wrote:")
    for k, v in paths.items():
        print(f"  {k}: {v}")
    if args.print_summary or True:
        print("[summary]", json.dumps(summarize(glossary), ensure_ascii=False))
        n_sus = sum(1 for f in flags if f.get("suspicious"))
        print(f"[flags] total={len(flags)} suspicious_numbers={n_sus}")
        n_pr = len(glossary.get("pronouns") or [])
        print(f"[relations] pronouns={n_pr} matrix={len(glossary.get('relation_matrix') or [])}")
    print("[next] Copy pack vào work/<job> — Pineline skip build_plan nếu có context_pack.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
