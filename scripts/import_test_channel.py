"""Chép cấu hình kênh test YouTube (data/test_channel: projects.json, token đăng nhập…) từ cây khác (mặc định V2)
sang V6 — KHÔNG ghi đè file V6 đã có. Mặc định chỉ XEM; thêm --apply để chép thật.

Kênh test dùng cho «Test bản quyền» (Phim chờ làm). Thư mục này chứa client_secret + token Google → chỉ nằm trên máy,
không lên git (.gitignore).

    python scripts/import_test_channel.py --src Z:\\PinelineV2
    python scripts/import_test_channel.py --src Z:\\PinelineV2 --apply
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = ("code_verifier", "current_oauth_project")      # trạng thái đăng nhập dở — không chép


def plan(src: Path, dst: Path) -> list[tuple[Path, Path, str]]:
    s, d = src / "data" / "test_channel", dst / "data" / "test_channel"
    out = []
    for f in sorted(s.rglob("*")):
        if not f.is_file() or f.name.startswith(SKIP):
            continue
        t = d / f.relative_to(s)
        out.append((f, t, "skip" if t.exists() else "copy"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=r"Z:\PinelineV2")
    ap.add_argument("--dst", default=str(ROOT))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    src, dst = Path(a.src), Path(a.dst)
    if not (src / "data" / "test_channel" / "projects.json").is_file():
        print(f"Không thấy {src / 'data' / 'test_channel' / 'projects.json'} — kiểm --src", file=sys.stderr)
        return 2
    steps = plan(src, dst)
    for f, t, act in steps:
        if act == "copy" and a.apply:
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, t)
        print(f"  {'CHÉP' if act == 'copy' else 'đã có':<6} {t.relative_to(dst)}")
    n = sum(act == "copy" for _, _, act in steps)
    print(f"\n{'Đã chép' if a.apply else 'Sẽ chép'} {n} file." + ("" if a.apply else " Thêm --apply để chép thật."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
