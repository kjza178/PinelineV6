#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thống kê luồng dịch simple THEO JOB — CHỈ ĐỌC work dir (2026-11-12 next, Bước 8c).

Mỗi job: số câu · tỉ lệ câu còn cờ (theo loại: pronoun/name/empty/cjk) · cổng (done/park/hold + lý do) ·
số lô review lỗi · cặp thiếu (plan_suggestions.json missing_pairs) · cặp suy luận (plan_inferred.json: rule/llm_fill) ·
số câu sửa tay (manual) · nguồn ZH (zh_src: ocr/asr/namefix nếu có).

  python tools/simple_stats.py --work D:\\PinelineV2_work                    (bảng markdown ra màn hình)
  python tools/simple_stats.py --work D:\\PinelineV2_work --json out.json   (ghi thêm JSON — ngoài work dir)
  python tools/simple_stats.py --work D:\\PinelineV2_work --all             (cả job không chạy simple)
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import json
import sys
from pathlib import Path


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def job_stats(jd: Path) -> dict | None:
    rows = _load(jd / "script.json", None)
    if not isinstance(rows, list):
        return None
    rows = [r for r in rows if isinstance(r, dict)]
    man = _load(jd / "manifest.json", {}) or {}
    st = ((man.get("stages") or {}).get("translate") or {})
    simple = st.get("simple") or {}
    n = len(rows)
    by_kind: dict[str, int] = {}
    n_flag = 0
    for r in rows:
        fl = r.get("s_flags") or []
        if fl:
            n_flag += 1
        for k in fl:
            by_kind[k] = by_kind.get(k, 0) + 1
    zh_src: dict[str, int] = {}
    for r in rows:
        if r.get("zh_src"):
            zh_src[r["zh_src"]] = zh_src.get(r["zh_src"], 0) + 1
    sug = _load(jd / "plan_suggestions.json", {}) or {}
    inf = _load(jd / "plan_inferred.json", {}) or {}
    inf_pairs = [p for p in inf.get("pairs") or [] if isinstance(p, dict)]
    rv = simple.get("review") or {}
    gate = simple.get("status") or ("done" if st.get("mode") == "simple" and st.get("status") == "done" else "")
    return {
        "job": jd.name, "mode": st.get("mode") or "", "status": st.get("status") or "", "gate": gate,
        "reasons": "; ".join(simple.get("reasons") or [])[:120],
        "n": n, "n_rv": sum(1 for r in rows if r.get("rv")),
        "n_missing_vi": sum(1 for r in rows if not (r.get("vi") or "").strip()),
        "n_flag": n_flag, "flag_ratio": round(n_flag / n, 4) if n else 0.0, "flags_by_kind": by_kind,
        "n_manual": sum(1 for r in rows if r.get("manual")),
        "fail_lots": rv.get("n_fail_lots"), "lots": rv.get("lots"),
        "missing_pairs": len(sug.get("missing_pairs") or []), "model_pairs": len(sug.get("model_pairs") or []),
        "pairs_rule": sum(1 for p in inf_pairs if p.get("src") == "rule"),
        "pairs_llm_fill": sum(1 for p in inf_pairs if p.get("src") == "llm_fill"),
        "zh_src": zh_src,
    }


def to_md(stats: list[dict]) -> str:
    head = ("| job | cổng | câu | cờ % | cờ theo loại | sửa tay | lô lỗi | cặp thiếu | rule | llm_fill | zh_src | lý do |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|---|")
    out = [head]
    for s in stats:
        kinds = ", ".join(f"{k}={v}" for k, v in sorted(s["flags_by_kind"].items())) or "—"
        zs = ", ".join(f"{k}={v}" for k, v in sorted(s["zh_src"].items())) or "—"
        lots = f"{s['fail_lots']}/{s['lots']}" if s["lots"] is not None else "—"
        out.append(f"| {s['job']} | {s['gate'] or s['status'] or '—'} | {s['n']} | {s['flag_ratio'] * 100:.1f} | "
                   f"{kinds} | {s['n_manual']} | {lots} | {s['missing_pairs']} | {s['pairs_rule']} | "
                   f"{s['pairs_llm_fill']} | {zs} | {s['reasons'] or ''} |")
    tot = {g: sum(1 for s in stats if s["gate"] == g) for g in ("done", "park", "hold")}
    out.append("")
    out.append(f"Tổng {len(stats)} job · done={tot['done']} · park={tot['park']} · hold={tot['hold']} · "
               f"câu sửa tay={sum(s['n_manual'] for s in stats)} · cặp thiếu={sum(s['missing_pairs'] for s in stats)}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Thống kê luồng simple theo job (chỉ đọc)")
    ap.add_argument("--work", required=True)
    ap.add_argument("--jobs", default="")
    ap.add_argument("--all", action="store_true", help="kể cả job không chạy simple")
    ap.add_argument("--json", default="", help="ghi JSON (đường dẫn NGOÀI work dir)")
    a = ap.parse_args(argv)
    work = Path(a.work).resolve()
    if (work / "script.json").is_file():
        dirs = [work]
    elif a.jobs.strip():
        dirs = [work / j.strip() for j in a.jobs.split(",") if j.strip()]
    else:
        dirs = sorted(p for p in work.iterdir() if p.is_dir())
    stats = [s for s in (job_stats(d) for d in dirs) if s and (a.all or s["mode"] == "simple")]
    print(to_md(stats))
    if a.json:
        jp = Path(a.json).resolve()
        try:
            jp.relative_to(work)
            print("[stats] từ chối ghi JSON trong work dir", file=sys.stderr)
            return 2
        except ValueError:
            pass
        jp.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
