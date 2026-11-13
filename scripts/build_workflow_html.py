"""build_workflow_html.py — Dựng docs/brain/workflow.html (trang workflow động) từ brain.json của các cây.

  python scripts/brain_export.py                      # ở MỖI cây trước (V2/V4/V5)
  python scripts/build_workflow_html.py               # tự tìm Z:\\PinelineV2, V4, V5 cạnh cây này
  python scripts/build_workflow_html.py --trees Z:\\PinelineV2 Z:\\PinelineV5 --out docs\\brain\\workflow.html

Mở file kết quả bằng trình duyệt (không cần server, không cần mạng ngoài font).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "docs" / "brain" / "workflow.template.html"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trees", nargs="*", help="thư mục các cây (mặc định: PinelineV2/V4/V5 cạnh cây này)")
    ap.add_argument("--template", default=str(TEMPLATE))
    ap.add_argument("--out", default=str(ROOT / "docs" / "brain" / "workflow.html"))
    a = ap.parse_args()
    trees = [Path(t) for t in a.trees] if a.trees else [ROOT.parent / n for n in ("PinelineV2", "PinelineV4", "PinelineV5")]
    data = {}
    for t in trees:
        p = t / "docs" / "brain" / "generated" / "brain.json"
        if p.is_file():
            data[t.name] = json.loads(p.read_text(encoding="utf-8"))
            print(f"  + {t.name}: {data[t.name]['n_modules']} module ({data[t.name]['generated_at']})")
        else:
            print(f"  - {t.name}: chưa có brain.json (chạy scripts/brain_export.py ở cây đó)")
    if not data:
        print("Không có dữ liệu.")
        return 1
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = Path(a.template).read_text(encoding="utf-8").replace("/*__DATA__*/null", blob, 1)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"→ {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
