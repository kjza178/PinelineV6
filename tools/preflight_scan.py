"""Quét CHỈ-ĐỌC các job trong work dir: nguồn video nào có vấn đề (preflight).

Không ghi manifest, không chuẩn hoá, không đụng file nguồn. Job cũ (đã qua download trước khi
có preflight) KHÔNG được chạy lại tự động — dùng script này để biết job nào cần xử lý, rồi tự
chạy cho từng job:  python source_preflight.py work/<job> --run

  python tools/preflight_scan.py                     # mọi job trong ./work (giải mã audio đầy đủ)
  python tools/preflight_scan.py --quick             # chỉ ffprobe + drift + vài khung hình (rất nhanh)
  python tools/preflight_scan.py --jobs a,b --json out.json
  python tools/preflight_scan.py --only-unchecked    # bỏ job đã có download.preflight
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import engines.source_preflight as sp  # noqa: E402


def _default_work_root() -> str:
    """V6 (port Q6): work root của instance (PINELINE_WORK_ROOT do apply_env đặt → instances/<tên>.json)
    — không hardcode thư mục work của V2."""
    import os
    v = (os.getenv("PINELINE_WORK_ROOT") or "").strip()
    if v:
        return v
    try:
        from core.instance import load
        return str(load().work_root)
    except Exception:  # noqa: BLE001
        return str(ROOT / "work")


def scan(work_root: Path, *, jobs: list[str] | None = None, quick: bool = False,
         only_unchecked: bool = False, limit: int = 0) -> list[dict]:
    rows: list[dict] = []
    dirs = sorted(p for p in work_root.iterdir() if (p / "manifest.json").is_file())
    if jobs:
        want = set(jobs)
        dirs = [d for d in dirs if d.name in want]
    for d in dirs:
        if limit and len(rows) >= limit:
            break
        dl = sp._read_download_stage(d)
        if dl.get("status") != "done":
            continue
        if only_unchecked and isinstance(dl.get("preflight"), dict):
            continue
        src = sp.original_source_video(d, dl)
        row: dict = {"job": d.name, "video": str(src) if src else None,
                     "has_preflight": isinstance(dl.get("preflight"), dict),
                     "pending": bool(dl.get("preflight_pending")),
                     "norm": dl.get("video_path_norm")}
        if src is None:
            row.update(status="no_source")
            rows.append(row)
            continue
        t0 = time.time()
        try:
            c = sp.check_source(src, quick=quick)
        except Exception as e:  # noqa: BLE001
            c = {"ok": False, "fatal": f"lỗi khi kiểm: {e}", "issues": [], "warnings": []}
        row.update(
            status="fatal" if c.get("fatal") else ("ok" if c.get("ok") else "audio_bad"),
            fatal=c.get("fatal"), issues=c.get("issues"), warnings=c.get("warnings"),
            drift=c.get("drift"), duration=(c.get("probe") or {}).get("duration"),
            sec=round(time.time() - t0, 2),
        )
        rows.append(row)
        print(f"{row['status']:>9}  {d.name}  {row['sec']:>6}s  "
              f"{'; '.join((c.get('issues') or []) + ([c['fatal']] if c.get('fatal') else []))}", flush=True)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-root", default=_default_work_root())
    ap.add_argument("--jobs", default="", help="danh sách job id, phẩy ngăn cách")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only-unchecked", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", default="", help="ghi kết quả ra file JSON (ngoài work dir)")
    a = ap.parse_args(argv)
    rows = scan(Path(a.work_root), jobs=[j for j in a.jobs.split(",") if j.strip()] or None,
                quick=a.quick, only_unchecked=a.only_unchecked, limit=a.limit)
    bad = [r for r in rows if r.get("status") in ("audio_bad", "fatal", "no_source")]
    print(f"\n{len(rows)} job · {len(bad)} có vấn đề")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
