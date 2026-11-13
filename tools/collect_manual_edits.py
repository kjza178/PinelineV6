#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gom câu NGƯỜI SỬA TAY (manual) trong work dir thành ỨNG VIÊN golden (định dạng golden V6) — CHỈ ĐỌC work dir.

2026-11-12 next (Bước 8b). Không đụng pipeline, không ghi gì vào work dir. Ghi vào thư mục --out:
  <out>/<job>/lines.gold.jsonl   mỗi dòng {i, scene, zh, vi_system, spk, to, pron_expected{self,call,ref3},
                                  vi_gold, error_tags, note, evidence}
  <out>/<job>/pairs.json         {job, status:"candidate", pairs:[cặp plan dùng trong các câu, kèm evidence]}
  <out>/<job>/characters.json    {job, status:"candidate", characters:[từ glossary.json]}
  <out>/<job>/meta.json          {job, status:"candidate", title, genre, range, n_lines, contiguous:false, …}
  <out>/summary.json             tổng hợp các job

vi_system = bản máy TRƯỚC khi người sửa: lấy dòng trace.jsonl (luồng simple) cuối cùng có «new» của câu đó;
không có trace → null (evidence="none") — người duyệt tự điền. vi_gold = vi hiện tại (manual).
Câu manual mà vi_gold == vi_system (chỉ dọn zh) → bỏ. Ứng viên CHƯA phải golden: status=candidate, mẫu KHÔNG liên
tục; người duyệt gắn error_tags + chuyển sang data/golden_* bằng tay. KHÔNG trộn vào golden_heldout.

  python tools/collect_manual_edits.py --work D:\\PinelineV2_work --out Z:\\PinelineV6\\scratch\\manual_golden_cand
  python tools/collect_manual_edits.py --work D:\\PinelineV2_work --jobs 2d3316593ea9,365baf19df54 --out <dir>
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
import time
from pathlib import Path


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def _trace_prev(job_dir: Path) -> dict[int, str]:
    """{i: vi máy cuối cùng} từ trace.1.jsonl rồi trace.jsonl (bỏ bước «manual»)."""
    out: dict[int, str] = {}
    for name in ("trace.1.jsonl", "trace.jsonl"):
        p = job_dir / name
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                rec = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(rec, dict) or "new" not in rec or rec.get("i") is None:
                continue
            if str(rec.get("step") or "").startswith("manual"):
                continue
            try:
                out[int(rec["i"])] = rec.get("new") or ""
            except (TypeError, ValueError):
                continue
    return out


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().split())


def _pair_of(plan: dict, spk: str, to: str) -> dict | None:
    for p in plan.get("pronouns") or []:
        if isinstance(p, dict) and _norm(p.get("who")) == _norm(spk) and _norm(p.get("to")) == _norm(to):
            return p
    return None


def _split(v) -> list[str] | None:
    xs = [x.strip() for x in str(v or "").replace(" hoặc ", "/").split("/") if x.strip()]
    return xs or None


def collect_job(job_dir: Path) -> dict | None:
    rows = _load(job_dir / "script.json", [])
    if not isinstance(rows, list):
        return None
    plan = _load(job_dir / "glossary.json", {}) or {}
    prev = _trace_prev(job_dir)
    scenes = [s for s in plan.get("scenes") or [] if isinstance(s, dict)]

    def scene_of(i: int):
        for sc in scenes:
            rng = sc.get("range") or [sc.get("start_i"), sc.get("end_i")]
            try:
                if int(rng[0]) <= i <= int(rng[1]):
                    return sc.get("scene_id")
            except (TypeError, ValueError, IndexError):
                continue
        return None

    lines, used_pairs = [], {}
    for r in rows:
        if not isinstance(r, dict) or not r.get("manual"):
            continue
        vi = (r.get("vi") or "").strip()
        if not vi:
            continue
        try:
            i = int(r.get("i"))
        except (TypeError, ValueError):
            continue
        vs = prev.get(i)
        if vs is not None and vs.strip() == vi:
            continue                    # manual chỉ do dọn zh — vi không đổi
        spk, to = str(r.get("s_spk") or "?"), str(r.get("s_to") or "-")
        pr = _pair_of(plan, spk, to)
        if pr:
            used_pairs.setdefault((pr.get("who"), pr.get("to")), {"pair": pr, "i": []})["i"].append(i)
        lines.append({
            "i": i, "scene": scene_of(i), "zh": r.get("zh") or "", "vi_system": vs, "spk": spk, "to": to,
            "pron_expected": {"self": _split(pr.get("self")) if pr else None,
                              "call": _split(pr.get("call")) if pr else None, "ref3": None},
            "vi_gold": vi, "error_tags": [],
            "note": "ứng viên từ sửa tay" + (f" (alt_pick={r.get('alt_pick')})" if r.get("alt_pick") is not None else ""),
            "evidence": "trace" if vs is not None else "none",
        })
    if not lines:
        return None
    lines.sort(key=lambda x: x["i"])
    man = _load(job_dir / "manifest.json", {}) or {}
    req = man.get("request") or {}
    pairs = [{"who": k[0], "to": k[1], "self": _split(v["pair"].get("self")), "call": _split(v["pair"].get("call")),
              "evidence": [{"i": i} for i in v["i"][:20]], "range": None, "source": "plan(candidate)"}
             for k, v in used_pairs.items()]
    meta = {"job": job_dir.name, "status": "candidate", "title": req.get("title") or "", "title_vi": "",
            "genre": plan.get("genre") or "", "genre_note": "", "range": [lines[0]["i"], lines[-1]["i"]],
            "n_lines": len(lines), "contiguous": False, "script_lines_total": len(rows),
            "n_vi_system_known": sum(1 for x in lines if x["vi_system"] is not None),
            "built": time.strftime("%Y-%m-%d"), "reviewer": "chưa duyệt — tools/collect_manual_edits.py"}
    chars = [c for c in plan.get("characters") or [] if isinstance(c, dict)]
    return {"lines": lines, "pairs": pairs, "characters": chars, "meta": meta}


def _jobs(work: Path, only: list[str] | None) -> list[Path]:
    if only:
        return [work / j for j in only if (work / j).is_dir()]
    return sorted(p for p in work.iterdir() if p.is_dir() and (p / "script.json").is_file())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gom câu sửa tay → ứng viên golden (chỉ đọc work dir)")
    ap.add_argument("--work", required=True, help="thư mục work (vd D:\\PinelineV2_work) hoặc 1 job")
    ap.add_argument("--out", required=True, help="thư mục ghi ứng viên (KHÔNG được nằm trong --work)")
    ap.add_argument("--jobs", default="", help="danh sách job cách dấu phẩy (mặc định: mọi job có script.json)")
    a = ap.parse_args(argv)
    work, out = Path(a.work).resolve(), Path(a.out).resolve()
    # review agent D: từ chối cả khi --out là thư mục CHA của --work (vd --work <work>/job1 --out <work> ⇒ ghi vào job1)
    # và khi --out là golden_heldout (đề thi — không trộn ứng viên vào)
    for a_, b_ in ((out, work), (work, out)):
        try:
            a_.relative_to(b_)
            print("[collect] từ chối: --out và --work lồng nhau (công cụ chỉ đọc work)", file=sys.stderr)
            return 2
        except ValueError:
            pass
    if "golden_heldout" in {x.lower() for x in out.parts}:
        print("[collect] từ chối: --out nằm trong golden_heldout", file=sys.stderr)
        return 2
    jobs = [work] if (work / "script.json").is_file() else _jobs(work, [j for j in a.jobs.split(",") if j.strip()])
    summary = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "work": str(work), "jobs": {}}
    for jd in jobs:
        res = collect_job(jd)
        if not res:
            continue
        od = out / jd.name
        od.mkdir(parents=True, exist_ok=True)
        with open(od / "lines.gold.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for rec in res["lines"]:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        for name, obj in (("pairs.json", {"job": jd.name, "status": "candidate", "pairs": res["pairs"]}),
                          ("characters.json", {"job": jd.name, "status": "candidate", "characters": res["characters"]}),
                          ("meta.json", res["meta"])):
            (od / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        summary["jobs"][jd.name] = {"n_lines": res["meta"]["n_lines"],
                                    "n_vi_system_known": res["meta"]["n_vi_system_known"]}
        print(f"[collect] {jd.name}: {res['meta']['n_lines']} câu sửa tay "
              f"({res['meta']['n_vi_system_known']} có bản máy trước)")
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[collect] {len(summary['jobs'])} job có ứng viên → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
