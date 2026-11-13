#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A/B cờ dịch translate-b3 trên golden silver (M0) — chạy CODE V2 trên job TẠM, chấm bằng eval_translation.

!!! CẢNH BÁO — đọc trước khi chạy thật (không --dry-run):
  * Dùng GPU (NMT HachimiMT, CTranslate2) + GPT/Aurora (REVIEW, vá câu, pass1, pronoun-QA) → TỐN QUOTA.
    Số lượt THẬT = số job × số biến thể theo --jobs/--variants — in ra «[golden_ab] kế hoạch: …» trước khi chạy;
    mỗi lượt ≈ cửa sổ golden + ngữ cảnh (~150–170 câu) REVIEW + pass1.
  * CHỈ chạy khi V2 RẢNH (hàng đợi :8801 trống, không job đang dịch/TTS) — tranh GPU/quota với production.
  * KHÔNG đổi preset llama: script đặt PINELINE_FLAG_PRESET_SWITCH=0 (guard của V2 preset_switcher)
    + AUTO_SWITCH_PRESETS=0 + REVIEW_LOCAL_FALLBACK=0 + TRANSLATE_FILL_MODEL=auto (vá bằng model REVIEW cloud).
    Biến thể nmt_hint* cần LLM dịch cả cửa sổ → dùng --hint-model (mặc định REVIEW_MODEL/AURORA_REVIEW_MODEL
    trong config V2, tức cloud) thay vì gemma local.
  * KHÔNG ghi vào work V2: chỉ ĐỌC D:\\PinelineV2_work\\<job>\\{asr,glossary,style_lock,pronoun_lock,
    context_pack,manifest}.json; job tạm ở D:\\PinelineV6_work\\golden_ab\\<job>_<variant>\\ (từ chối nếu
    --out-root nằm trong work V2 hoặc cây V2). Không gọi HTTP :8801/:8802; không git.

Biến thể (env áp cho tiến trình con chạy run_translate + run_review của V2):
  base                     như prod (không đặt cờ b3)
  regex_off                PRONOUN_REGEX_MIXED=0
  mask_off                 REVIEW_PRONOUN_MASK=0 (REVIEW_PRONOUN_PLAN_GUIDE mặc định 1 → khối (G))
  regex_mask_off           cả hai
  nmt_hint                 MT_ZH_VI_ROLE=hint (NMT chỉ làm gợi ý, LLM --hint-model dịch có plan)
  nmt_hint_regex_mask_off  hint + PRONOUN_REGEX_MIXED=0 + REVIEW_PRONOUN_MASK=0
  halfcue                  HALFCUE_MERGE=1
  simple                   TRANSLATE_PIPELINE=simple → translate_simple.pipeline.run (luồng dịch mới 2026-11-12:
                           plan ∥ NMT → review cloud 1 lần/lô → vá câu bị cờ ≤2 lượt → cổng)
  simple_noleak            simple + SIMPLE_LEXICON_EXCLUDE_GOLDEN=1 (bỏ mục lexicon golden_derived — đo trung thực)
  simple_rules             simple + SIMPLE_PAIR_RULES=1 (điền cặp xưng hô trống theo bảng quan hệ)
  simple_rules_fill        simple_rules + SIMPLE_PAIR_FILL=1 (1 lần gọi model bổ sung cặp thiếu ≥3 câu)
  simple_rules_fill_zh     simple_rules_fill + SIMPLE_ZH_SOURCE=1 (job tạm golden KHÔNG có video → giữ ASR;
                           chỉ khác khi có hardsub_cues.json/hardsub_track.json; sửa tên theo glossary)

Ví dụ (Windows, cmd):
  python scripts\\golden_ab.py --dry-run
  python scripts\\golden_ab.py --variants base,regex_off --jobs 9dedde16e35b --yes
  python scripts\\golden_ab.py --score-only            # chấm lại các job tạm đã chạy
  python scripts\\golden_ab.py --score-source          # không LLM: chấm script.json hiện có của V2 (kiểm pipeline)
  python scripts\\golden_ab.py --prepare-only --out-root <thư mục nháp>   # không LLM: chỉ dựng job tạm
Kết quả: docs/reviews/2026-11-12-golden-ab.md (+ .json) — đổi bằng --out-md/--out-json.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
V6 = HERE.parent
sys.path.insert(0, str(HERE))
import eval_translation as EV  # noqa: E402

VARIANTS: dict[str, dict[str, str]] = {
    "base": {},
    "regex_off": {"PRONOUN_REGEX_MIXED": "0"},
    "mask_off": {"REVIEW_PRONOUN_MASK": "0"},
    "regex_mask_off": {"PRONOUN_REGEX_MIXED": "0", "REVIEW_PRONOUN_MASK": "0"},
    "nmt_hint": {"MT_ZH_VI_ROLE": "hint"},
    "nmt_hint_regex_mask_off": {"MT_ZH_VI_ROLE": "hint", "PRONOUN_REGEX_MIXED": "0", "REVIEW_PRONOUN_MASK": "0"},
    "halfcue": {"HALFCUE_MERGE": "1"},
    "simple": {"TRANSLATE_PIPELINE": "simple"},
    # 2026-11-12 next (agent B): cờ thử nghiệm của translate_simple — mặc định tắt trong V2
    "simple_noleak": {"TRANSLATE_PIPELINE": "simple", "SIMPLE_LEXICON_EXCLUDE_GOLDEN": "1"},
    "simple_rules": {"TRANSLATE_PIPELINE": "simple", "SIMPLE_PAIR_RULES": "1"},
    "simple_rules_fill": {"TRANSLATE_PIPELINE": "simple", "SIMPLE_PAIR_RULES": "1", "SIMPLE_PAIR_FILL": "1"},
    "simple_rules_fill_zh": {"TRANSLATE_PIPELINE": "simple", "SIMPLE_PAIR_RULES": "1", "SIMPLE_PAIR_FILL": "1",
                             "SIMPLE_ZH_SOURCE": "1"},
}
# Cờ do script quản lý — xoá khỏi env cha để biến thể không bị rò cờ.
_MANAGED = {"PRONOUN_REGEX_MIXED", "REVIEW_PRONOUN_MASK", "REVIEW_PRONOUN_PLAN_GUIDE", "MT_ZH_VI_ROLE",
            "HALFCUE_MERGE", "HALFCUE_GAP", "HALFCUE_MAX_CHARS", "HALFCUE_MAX_CUES", "FILL_PENDING_PARK",
            "PINELINE_CFG_KEYS", "MT_ZH_VI", "MT_ZH_VI_ONLY", "TRANSLATE_PIPELINE",
            "SIMPLE_LEXICON_EXCLUDE_GOLDEN", "SIMPLE_PAIR_RULES", "SIMPLE_PAIR_FILL", "SIMPLE_PAIR_FILL_MIN",
            "SIMPLE_PAIR_FILL_MODEL", "SIMPLE_PAIR_FILL_CONF", "SIMPLE_LEARN_INFERRED", "SIMPLE_ZH_SOURCE",
            "SIMPLE_ZH_NAMEFIX", "SIMPLE_ZH_NAMEFIX_MIN", "SIMPLE_ZH_HS_SAMPLES", "SIMPLE_ZH_HS_MIN_RATIO"}
# An toàn chung mọi biến thể.
SAFE_ENV = {
    "PINELINE_FLAG_PRESET_SWITCH": "0",
    "AUTO_SWITCH_PRESETS": "0",
    "REVIEW_LOCAL_FALLBACK": "0",
    "TRANSLATE_FILL_MODEL": "auto",
    "AGENT_BRAIN": "0",
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
}
COPY_FILES = ("glossary.json", "style_lock.json", "pronoun_lock.json", "context_pack.json")


def _default(env_key: str, win: str) -> str:
    return os.getenv(env_key) or win


def window_rows(asr: list[dict], lo: int, hi: int, ctx_before: int, ctx_after: int) -> list[dict]:
    """Cửa sổ ASR [lo-ctx_before, hi+ctx_after] theo i (giữ i gốc để eval ghép theo i)."""
    a, b = lo - ctx_before, hi + ctx_after
    return [dict(r) for r in asr if isinstance(r, dict) and a <= int(r.get("i", -1)) <= b]


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def prepare_job(job: str, gold: dict, src_work: Path, dest: Path, ctx_before: int, ctx_after: int,
                write: bool = True) -> dict:
    """Dựng job tạm; trả kế hoạch (để in ở --dry-run)."""
    lo, hi = gold["meta"]["range"]
    sd = src_work / job
    info = {"job": job, "range": [lo, hi], "src": str(sd), "dest": str(dest), "files": {}}
    asr_p = sd / "asr.json"
    info["files"]["asr.json"] = asr_p.is_file()
    for f in COPY_FILES + ("manifest.json",):
        info["files"][f] = (sd / f).is_file()
    if not asr_p.is_file():
        info["error"] = "thiếu asr.json"
        return info
    asr = json.loads(asr_p.read_text(encoding="utf-8"))
    win = window_rows(asr, lo, hi, ctx_before, ctx_after)
    info["n_window"] = len(win)
    info["n_gold"] = len(gold["lines"])
    # kiểm zh khớp golden (theo i)
    by_i = {int(r["i"]): r for r in win}
    miss = sum(1 for g in gold["lines"]
               if EV.norm_zh((by_i.get(g["i"]) or {}).get("text") or "") != EV.norm_zh(g["zh"]))
    info["zh_mismatch_vs_gold"] = miss
    if not write:
        return info
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "asr.json").write_text(json.dumps(win, ensure_ascii=False, indent=1), encoding="utf-8")
    for f in COPY_FILES:
        if (sd / f).is_file():
            shutil.copyfile(sd / f, dest / f)
    qo = {}
    try:
        qo = (json.loads((sd / "manifest.json").read_text(encoding="utf-8")).get("queue_opts") or {})
    except Exception:  # noqa: BLE001
        pass
    # manifest tối giản: KHÔNG có request.title → series_glossary không đọc/ghi bảng bộ chung
    (dest / "manifest.json").write_text(json.dumps({"stages": {}, "queue_opts": qo, "golden_ab": {
        "job": job, "range": [lo, hi], "prepared_at": time.strftime("%Y-%m-%d %H:%M:%S")}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def variant_env(name: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _MANAGED}
    env.update(SAFE_ENV)
    env.update(VARIANTS[name])
    return env


_CHILD = r'''
import json, os, sys, time, traceback
from pathlib import Path
v2, wd, hint_model = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, v2)
os.chdir(str(wd))
import translate as T
import series_glossary
series_glossary.save_from = lambda *a, **k: {"skipped": "golden_ab"}   # không ghi bảng bộ chung
import mt_zh_vi
hint = not mt_zh_vi.primary_mode()
t0 = time.time()
res = {"variant_env": {k: os.environ.get(k) for k in sorted(%(keys)r)}, "hint": hint}
try:
    if (os.environ.get("TRANSLATE_PIPELINE") or "").strip().lower() == "simple":
        from translate_simple import pipeline as SP
        r_s = SP.run(wd, "Tiếng Việt")
        res["simple"] = {k: r_s.get(k) for k in ("status", "gate", "review", "repair", "t_total")}
        res["ok"] = True
    else:
        T.run_translate(wd, auto_review=False, model=(hint_model or None) if hint else None)
        res["t_translate"] = round(time.time() - t0, 1)
        T.run_review(wd)
        res["ok"] = True
except BaseException as e:
    res["ok"] = False
    res["error"] = f"{type(e).__name__}: {e}"[:500]
    traceback.print_exc()
res["t_total"] = round(time.time() - t0, 1)
(wd / "golden_ab_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print("@@AB@@" + json.dumps(res, ensure_ascii=False))
'''


def run_variant(v2: Path, wd: Path, variant: str, hint_model: str, python: str) -> dict:
    code = _CHILD % {"keys": sorted(set(SAFE_ENV) | set().union(*[set(v) for v in VARIANTS.values()]))}
    script = wd / "_golden_ab_child.py"
    script.write_text(code, encoding="utf-8")
    log = wd / "golden_ab.log"
    with open(log, "w", encoding="utf-8") as lf:
        p = subprocess.run([python, str(script), str(v2), str(wd), hint_model or ""], cwd=str(wd),
                           env=variant_env(variant), stdout=lf, stderr=subprocess.STDOUT)
    try:
        return json.loads((wd / "golden_ab_result.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": f"tiến trình con thoát {p.returncode} — xem {log}"}


def score(golden_dir: str, job: str, script: Path) -> dict | None:
    if not script.is_file():
        return None
    res = EV.run(golden_dir, jobs=[job], script=str(script))
    j = res["jobs"].get(job)
    return j["metrics"] if j else None


_KEYS = [("pron_acc", "Xưng hô đúng"), ("pron_wrong", "Câu sai xưng hô"), ("consistency", "Đổi cặp vượt gold"),
         ("ref3_acc", "Giới ngôi 3"), ("idiom_acc", "Thành ngữ/lóng"), ("halfcue_left", "Câu cắt đôi còn"),
         ("chrF", "chrF"), ("edit_rate", "Edit rate"), ("changed", "% khác gold"), ("unaligned", "Không ghép")]


def _fmt(m: dict | None, k: str) -> str:
    if not m:
        return "—"
    v = m.get(k)
    if v is None:
        return "—"
    if k in ("pron_acc", "ref3_acc", "idiom_acc", "changed"):
        return f"{v * 100:.1f}%" if isinstance(v, (int, float)) and v <= 1.0 else str(v)
    if k == "edit_rate":
        return f"{v:.3f}"
    if k == "chrF":
        return f"{v:.1f}"
    return str(v)


def to_md(result: dict) -> str:
    out = ["# A/B cờ dịch translate-b3 trên golden silver",
           "",
           f"_{result['generated']} · `scripts/golden_ab.py` · golden `{result['golden']}` · code V2 `{result['v2']}`._",
           f"_Mode: {result['mode']}. Golden là SILVER — xem `data/golden/README.md`._",
           "", "## Tổng (mọi job)", "",
           "| Biến thể | " + " | ".join(lbl for _k, lbl in _KEYS) + " | job chấm được |",
           "|---|" + "---|" * (len(_KEYS) + 1)]
    for v, d in result["variants"].items():
        tot = d.get("total") or {}
        out.append(f"| {v} | " + " | ".join(_fmt(tot, k) for k, _l in _KEYS) + f" | {d.get('n_scored', 0)} |")
    out += ["", "## Theo job", ""]
    for v, d in result["variants"].items():
        out.append(f"### {v}")
        out.append("")
        out.append("| Job | " + " | ".join(lbl for _k, lbl in _KEYS) + " | chạy |")
        out.append("|---|" + "---|" * (len(_KEYS) + 1))
        for job, jd in d["jobs"].items():
            run = jd.get("run") or {}
            st = "ok" if run.get("ok") else (run.get("error") or "chưa chạy")[:60]
            out.append(f"| {job} | " + " | ".join(_fmt(jd.get("metrics"), k) for k, _l in _KEYS) + f" | {st} |")
        out.append("")
    out.append("Ghi chú: «Xưng hô đúng» = pronoun pair accuracy; «Câu cắt đôi còn» = câu gắn halfcue_dup chưa sửa. "
               "Định nghĩa đầy đủ: docstring `scripts/eval_translation.py`.")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A/B cờ dịch translate-b3 trên golden (dùng GPU + GPT — xem docstring)")
    ap.add_argument("--golden", default=str(V6 / "data" / "golden"))
    ap.add_argument("--v2", default=_default("GOLDEN_AB_V2", r"Z:\PinelineV2"))
    ap.add_argument("--v2-work", default=_default("GOLDEN_AB_V2_WORK", r"D:\PinelineV2_work"))
    ap.add_argument("--out-root", default=_default("GOLDEN_AB_OUT", r"D:\PinelineV6_work\golden_ab"))
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--jobs", default="")
    ap.add_argument("--ctx-before", type=int, default=20, help="số câu ngữ cảnh trước cửa sổ golden (dịch, không chấm)")
    ap.add_argument("--ctx-after", type=int, default=3)
    ap.add_argument("--hint-model", default="", help="model LLM dịch cho biến thể nmt_hint* (mặc định REVIEW model cloud)")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--dry-run", action="store_true", help="in kế hoạch, KHÔNG ghi/chạy gì")
    ap.add_argument("--prepare-only", action="store_true", help="chỉ dựng job tạm (không LLM/GPU)")
    ap.add_argument("--score-only", action="store_true", help="chỉ chấm job tạm đã chạy")
    ap.add_argument("--score-source", action="store_true",
                    help="không LLM: chấm script.json HIỆN CÓ của V2 trên đúng cửa sổ (kiểm pipeline chấm)")
    ap.add_argument("--force", action="store_true", help="chạy lại cả job tạm đã có kết quả")
    ap.add_argument("--yes", action="store_true", help="bỏ câu hỏi xác nhận trước khi chạy thật")
    ap.add_argument("--out-md", default=str(V6 / "docs" / "reviews" / "2026-11-12-golden-ab.md"))
    ap.add_argument("--out-json", default=str(V6 / "docs" / "reviews" / "2026-11-12-golden-ab.json"))
    a = ap.parse_args(argv)

    variants = [v.strip() for v in a.variants.split(",") if v.strip()]
    bad = [v for v in variants if v not in VARIANTS]
    if bad:
        ap.error(f"biến thể lạ: {bad} — có: {list(VARIANTS)}")
    jobs = [j.strip() for j in a.jobs.split(",") if j.strip()] or None
    gd = EV.load_golden(a.golden, jobs)
    if not gd:
        ap.error(f"không thấy golden trong {a.golden}")
    v2, v2w, out_root = Path(a.v2), Path(a.v2_work), Path(a.out_root)
    if _inside(out_root, v2w) or _inside(out_root, v2):
        ap.error(f"--out-root {out_root} nằm trong work/cây V2 — cấm ghi vào V2")
    hint_model = a.hint_model
    if not hint_model and not (a.dry_run or a.prepare_only or a.score_only or a.score_source):
        try:
            sys.path.insert(0, str(v2))
            from config import get as _g  # type: ignore
            hint_model = _g("AURORA_REVIEW_MODEL") or _g("REVIEW_MODEL") or ""
        except Exception:  # noqa: BLE001
            hint_model = ""
        finally:
            if sys.path and sys.path[0] == str(v2):
                sys.path.pop(0)

    mode = ("dry-run" if a.dry_run else "prepare-only" if a.prepare_only else "score-only" if a.score_only
            else "score-source" if a.score_source else "run")
    print(__doc__.split("Biến thể")[0].strip() if mode == "run" else f"[golden_ab] mode={mode}")

    if a.score_source:
        result = {"generated": time.strftime("%Y-%m-%d %H:%M"), "golden": a.golden, "v2": str(v2),
                  "mode": "score-source (script.json hiện có của V2, không chạy LLM)", "variants": {}}
        jobs_out, ok = {}, {}
        for job, gold in gd.items():
            sp = v2w / job / "script.json"
            m = score(a.golden, job, sp)
            info = prepare_job(job, gold, v2w, out_root / f"{job}_check", a.ctx_before, a.ctx_after, write=False)
            jobs_out[job] = {"metrics": m, "run": {"ok": m is not None, "error": None if m else f"không có {sp}"},
                             "window": info}
            if m:
                ok[job] = m
        result["variants"]["v2_current"] = {"jobs": jobs_out, "total": EV.aggregate(ok) if ok else None,
                                            "n_scored": len(ok)}
        _write(result, a)
        return 0

    plan = []
    for job, gold in gd.items():
        for v in variants:
            dest = out_root / f"{job}_{v}"
            info = prepare_job(job, gold, v2w, dest, a.ctx_before, a.ctx_after, write=False)
            info["variant"] = v
            info["env"] = {**SAFE_ENV, **VARIANTS[v]}
            info["has_result"] = (dest / "script.json").is_file() and (dest / "golden_ab_result.json").is_file()
            plan.append(info)
    n_done = sum(1 for p in plan if p.get("has_result"))
    n_rows = sum(int(p.get("n_window") or 0) for p in plan if not p.get("has_result"))
    print(f"[golden_ab] kế hoạch: {len(gd)} job × {len(variants)} biến thể = {len(plan)} lượt "
          f"(đã có kết quả, bỏ qua: {n_done}{' — --force sẽ chạy lại' if a.force else ''}; "
          f"sẽ chạy: {len(plan) - (0 if a.force else n_done)} lượt ≈ {n_rows} câu chưa có kết quả) · golden {a.golden}")
    if a.dry_run:
        for p in plan:
            print(json.dumps({k: p.get(k) for k in ("job", "variant", "range", "n_window", "n_gold",
                                                     "zh_mismatch_vs_gold", "src", "dest", "files", "env",
                                                     "has_result", "error")}, ensure_ascii=False))
        print(f"[golden_ab] dry-run: {len(plan)} lượt ({len(gd)} job × {len(variants)} biến thể) — không ghi, không chạy.")
        print(f"[golden_ab] hint-model cho nmt_hint*: {a.hint_model or '(REVIEW model trong config V2 lúc chạy)'}")
        return 0

    if mode == "run" and not a.yes:
        ans = input(f"Chạy {len(plan)} lượt — dùng GPU + GPT/Aurora (tốn quota). V2 đang RẢNH? gõ YES để chạy: ")
        if ans.strip().upper() not in ("YES", "Y"):
            print("Huỷ.")
            return 1

    result = {"generated": time.strftime("%Y-%m-%d %H:%M"), "golden": a.golden, "v2": str(v2), "mode": mode,
              "hint_model": hint_model, "variants": {}}
    for v in variants:
        jobs_out, ok = {}, {}
        for job, gold in gd.items():
            dest = out_root / f"{job}_{v}"
            run = None
            if mode in ("run", "prepare-only"):
                done = (dest / "script.json").is_file() and (dest / "golden_ab_result.json").is_file()
                if done and not a.force:
                    run = json.loads((dest / "golden_ab_result.json").read_text(encoding="utf-8"))
                    print(f"[golden_ab] {job}/{v}: đã có kết quả — bỏ qua (--force để chạy lại)")
                else:
                    if done and a.force:
                        stamp = time.strftime("%Y%m%d_%H%M%S")
                        shutil.move(str(dest), str(out_root / "_old" / f"{job}_{v}_{stamp}"))
                    info = prepare_job(job, gold, v2w, dest, a.ctx_before, a.ctx_after, write=True)
                    if info.get("error"):
                        run = {"ok": False, "error": info["error"]}
                    elif mode == "run":
                        print(f"[golden_ab] {job}/{v}: chạy {'translate_simple.pipeline.run' if v.startswith('simple') else 'run_translate + run_review'} "
                              f"({info['n_window']} câu, env {VARIANTS[v] or 'prod'}) …", flush=True)
                        run = run_variant(v2, dest, v, hint_model, a.python)
                        print(f"[golden_ab] {job}/{v}: {'OK' if run.get('ok') else 'LỖI ' + str(run.get('error'))}"
                              f" · {run.get('t_total')} s", flush=True)
                    else:
                        run = {"ok": None, "prepared": info}
            elif (dest / "golden_ab_result.json").is_file():
                run = json.loads((dest / "golden_ab_result.json").read_text(encoding="utf-8"))
            m = score(a.golden, job, dest / "script.json")
            jobs_out[job] = {"metrics": m, "run": run, "dir": str(dest)}
            if m:
                ok[job] = m
        result["variants"][v] = {"jobs": jobs_out, "total": EV.aggregate(ok) if ok else None, "n_scored": len(ok)}
    if mode == "prepare-only":
        print(json.dumps({v: {j: (d["run"] or {}).get("prepared", {}).get("n_window") for j, d in vd["jobs"].items()}
                          for v, vd in result["variants"].items()}, ensure_ascii=False))
        print("[golden_ab] prepare-only: đã dựng job tạm, KHÔNG chạy LLM/GPU, không ghi báo cáo.")
        return 0
    _write(result, a)
    return 0


def _write(result: dict, a) -> None:
    md = to_md(result)
    Path(a.out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out_md).write_text(md, encoding="utf-8")
    Path(a.out_json).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(md)
    print(f"[golden_ab] ghi {a.out_md} + {a.out_json}")


if __name__ == "__main__":
    sys.exit(main())
