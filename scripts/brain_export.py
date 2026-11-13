"""brain_export.py — Xuất dữ liệu Second Brain ra JSON gọn cho trang workflow (docs/brain/workflow.html).

  python scripts/brain_export.py                 # → docs/brain/generated/brain.json
  python scripts/brain_export.py --out x.json

Dùng lại bộ phân tích AST của brain_build.py (không chạy code dự án).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brain_build as B  # noqa: E402


def export(tree_name: str) -> dict:
    mods = B.load_all()
    out = []
    for m in sorted(mods.values(), key=lambda m: m.name):
        if m.layer == "test":
            continue
        longs = sorted(((q, f) for q, f in m.all_fns if f.length >= B.LONG_FN), key=lambda t: -t[1].length)
        out.append({
            "n": m.name, "r": m.rel, "l": m.layer, "loc": m.loc, "d": m.doc[:240],
            "i": sorted(x for x in m.imports_int if x in mods and mods[x].layer != "test"),
            "b": sorted(x for x in m.imported_by if mods[x].layer != "test"),
            "sp": sorted(m.launched_by),
            "f": [[f.name, f.line, f.length, f.doc[:110]] for f in m.functions][:120],
            "c": [[c, line, nm] for c, line, nm, _d in m.classes][:30],
            "rt": [[me, p, h, line] for me, p, h, line in sorted(m.routes, key=lambda r: r[1])][:150],
            "k": sorted(m.cfg_keys)[:120],
            "a": sorted(m.artifacts)[:60],
            "p": sorted(m.ports),
            "lf": [[q, f.line, f.length] for q, f in longs[:10]],
            "fx": [[p, t] for p, t in sorted(m.fix_docs, reverse=True)[:30]],
            "t": sorted(mods[t].rel for t in m.tests),
            "s": {k: v for k, v in m.smells.items() if v},
            "h": B.hotspot_score(m),
            "ch": m.changelog_hits,
        })
    layers = Counter(o["l"] for o in out)
    return {
        "tree": tree_name, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_modules": len(out), "n_loc": sum(o["loc"] for o in out),
        "n_routes": sum(len(o["rt"]) for o in out), "layers": dict(layers), "modules": out,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(B.GEN_DIR / "brain.json"))
    a = ap.parse_args()
    data = export(B.ROOT.name)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"brain.json: {data['n_modules']} module, {data['n_loc']:,} dòng, {data['n_routes']} route → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
