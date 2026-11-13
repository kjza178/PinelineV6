"""tools/asr_boundary_scan.py — CHỈ ĐỌC: tìm câu ASR nghi bị cắt ở biên slice cứng k×1200 s.

Lỗi cũ (trước fix 2026-11-13-firered-slice-silence): firered_asr.py cắt audio tại đúng
k×FIRERED_SLICE_SEC -> câu vắt biên bị cắt đôi («您已成为天。|命之子。»), lặp chữ
(«我想学这个。|个猫咪…») hoặc mất chữ. Dấu vết trong asr.json: một câu KẾT THÚC sát biên
và/hoặc câu sau BẮT ĐẦU sát biên (FireRed VAD bị chặn ở mép slice).

Dùng:
    python tools/asr_boundary_scan.py [work_root]   # mọi job firered (mặc định work root instance V6)
    python tools/asr_boundary_scan.py --all      # mọi engine
    python tools/asr_boundary_scan.py --json out.json
Không ghi gì vào thư mục job.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _default_work_root() -> str:
    """V6 (port Q6): work root của instance (PINELINE_WORK_ROOT → instances/<tên>.json), không hardcode V2."""
    import os
    v = (os.getenv("PINELINE_WORK_ROOT") or "").strip()
    if v:
        return v
    try:
        from core.instance import load
        return str(load().work_root)
    except Exception:  # noqa: BLE001
        return str(ROOT / "work")


_PUNCT = re.compile(r"[\s，。！？、,.!?;；:：…\"'“”‘’《》()（）\-—]+")


def _core(t: str) -> str:
    return _PUNCT.sub("", t or "")


def scan_rows(rows: list[dict], slice_sec: float = 1200.0, tol: float = 0.05,
              near: float = 1.0) -> list[dict]:
    """Trả danh sách biên nghi lỗi: {boundary, kind, prev, next}."""
    rows = sorted((r for r in rows if isinstance(r, dict)),
                  key=lambda r: (float(r.get("start", 0)), float(r.get("end", 0))))
    if not rows:
        return []
    dur = max(float(r.get("end", 0) or 0) for r in rows)
    out = []
    k = 1
    while k * slice_sec < dur:
        b = k * slice_sec
        k += 1
        prev = nxt = None
        for r in rows:
            if float(r["end"]) <= b + tol:
                prev = r
            elif float(r["start"]) >= b - tol:
                nxt = r
                break
        if prev is None and nxt is None:
            continue
        pe = float(prev["end"]) if prev else -1e9
        ns = float(nxt["start"]) if nxt else 1e9
        touch_prev = abs(pe - b) <= tol
        touch_next = abs(ns - b) <= tol
        if not (touch_prev or touch_next):
            continue
        kinds = []
        if touch_prev and touch_next:
            kinds.append("cắt đôi")
        elif touch_prev:
            kinds.append("đuôi bị chặn" + (" (câu sau mất/lệch?)" if ns - b > near else ""))
        else:
            kinds.append("đầu bị chặn" + (" (câu trước mất/lệch?)" if b - pe > near else ""))
        a, c = _core(prev["text"]) if prev else "", _core(nxt["text"]) if nxt else ""
        if a and c and (a[-1] == c[0] or (len(a) > 1 and a[-2:] == c[:2])):
            kinds.append("lặp chữ")
        out.append({
            "boundary": b, "kind": ", ".join(kinds),
            "prev": {"i": prev.get("i"), "start": prev["start"], "end": prev["end"],
                     "text": prev["text"]} if prev else None,
            "next": {"i": nxt.get("i"), "start": nxt["start"], "end": nxt["end"],
                     "text": nxt["text"]} if nxt else None,
        })
    return out


def scan_job(job: Path, slice_sec: float, all_engines: bool) -> dict | None:
    asr = job / "asr.json"
    if not asr.is_file():
        return None
    engine, finished = "?", None
    try:
        man = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
        st = (man.get("stages") or {}).get("asr") or {}
        engine = st.get("engine") or st.get("asr_engine") or "?"
        finished = st.get("finished_at")
    except Exception:  # noqa: BLE001
        pass
    if not all_engines and "firered" not in str(engine):
        return {"job": job.name, "engine": engine, "skipped": True}
    try:
        rows = json.loads(asr.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"job": job.name, "engine": engine, "error": str(e)}
    if not isinstance(rows, list):
        return {"job": job.name, "engine": engine, "error": "asr.json không phải list"}
    dur = max([float(r.get("end", 0) or 0) for r in rows if isinstance(r, dict)] or [0])
    hits = scan_rows(rows, slice_sec)
    n_rows = len({(h["prev"] or {}).get("i") for h in hits if h["prev"]} |
                 {("n", (h["next"] or {}).get("i")) for h in hits if h["next"]})
    return {"job": job.name, "engine": engine, "asr_finished_at": finished, "dur_sec": dur,
            "n_boundaries": int(dur // slice_sec) if dur else 0, "hits": hits,
            "n_suspect_rows": n_rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Quét asr.json tìm câu bị cắt ở biên slice cứng (chỉ đọc).")
    ap.add_argument("work_root", nargs="?", default=_default_work_root())
    ap.add_argument("--slice-sec", type=float, default=1200.0)
    ap.add_argument("--all", action="store_true", help="quét cả engine khác firered")
    ap.add_argument("--json", help="ghi kết quả JSON ra file (ngoài thư mục job)")
    a = ap.parse_args(argv)
    root = Path(a.work_root)
    jobs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    res = [r for r in (scan_job(j, a.slice_sec, a.all) for j in jobs) if r]
    scanned = [r for r in res if not r.get("skipped") and not r.get("error")]
    bad = [r for r in scanned if r["hits"]]
    for r in bad:
        print(f"\n== {r['job']}  ({r['engine']}, {r['dur_sec'] / 60:.0f} phút, "
              f"{len(r['hits'])}/{r['n_boundaries']} biên lỗi, {r['n_suspect_rows']} câu nghi)")
        for h in r["hits"]:
            p = h["prev"]["text"] if h["prev"] else "∅"
            n = h["next"]["text"] if h["next"] else "∅"
            print(f"  @{h['boundary']:.0f}s [{h['kind']}]  «{p}|{n}»")
    for r in res:
        if r.get("error"):
            print(f"[lỗi] {r['job']}: {r['error']}")
    ok = [r["job"] for r in scanned if not r["hits"] and r["n_boundaries"]]
    short = [r["job"] for r in scanned if not r["n_boundaries"]]
    print(f"\nTổng: {len(res)} job có asr.json · quét {len(scanned)} "
          f"(bỏ qua {sum(1 for r in res if r.get('skipped'))} engine khác) · "
          f"{len(bad)} job nghi lỗi · {sum(r['n_suspect_rows'] for r in bad)} câu nghi · "
          f"{sum(len(r['hits']) for r in bad)} biên")
    if ok:
        print("Sạch (có biên nhưng không dấu vết): " + ", ".join(ok))
    if short:
        print(f"Ngắn ≤ {a.slice_sec:.0f}s (không có biên): " + ", ".join(short))
    if bad:
        print("Nên chạy lại ASR (+ dịch/TTS) cho: " + ", ".join(r["job"] for r in bad))
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
