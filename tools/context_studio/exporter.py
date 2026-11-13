"""05 — Export context pack vào thư mục job Pineline (hoặc out/)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_pack(
    out_dir: str | Path,
    glossary: dict[str, Any],
    flags: list[dict] | None = None,
    *,
    also_style_lock_file: bool = True,
) -> dict[str, str]:
    """
    Ghi:
      glossary.json     — Pineline translate đọc được (+ style_lock bên trong)
      style_lock.json   — bản tách (tiện UI)
      qa_flags.json     — số/tên nghi vấn (không sửa SRT)
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    gpath = out / "glossary.json"
    gpath.write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["glossary"] = str(gpath)

    lock = glossary.get("style_lock") or {}
    if also_style_lock_file:
        sp = out / "style_lock.json"
        sp.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["style_lock"] = str(sp)

    fp = out / "qa_flags.json"
    payload = {
        "flags": flags or [],
        "n_suspicious": sum(1 for f in (flags or []) if f.get("suspicious")),
        "n_total": len(flags or []),
        "policy": "AI may FLAG conflicts but MUST NOT rewrite Chinese transcript without evidence.",
    }
    fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["qa_flags"] = str(fp)

    # marker: Pineline skip build_plan
    marker = out / "context_pack.json"
    marker.write_text(
        json.dumps({
            "ready_for_translate": True,
            "skip_plan": True,
            "files": paths,
            "summary": {
                "genre": glossary.get("genre"),
                "style": (lock or {}).get("mode"),
                "characters": len(glossary.get("characters") or []),
                "pronouns": len(glossary.get("pronouns") or []),
                "flags": payload["n_total"],
                "suspicious": payload["n_suspicious"],
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["context_pack"] = str(marker)

    # relation matrix tách file (tiện UI / diff)
    rel = glossary.get("relation_matrix") or glossary.get("pronouns") or []
    if rel:
        rp = out / "relation_matrix.json"
        rp.write_text(json.dumps(rel, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["relation_matrix"] = str(rp)

    return paths
