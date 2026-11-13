"""
Soát lỗi máy móc bản dịch VI (script.json) — chạy SAU review/polish, trước TTS.

autofix_rows: sửa chắc chắn đúng (không cần LLM)
  · «ông tôi / cô tôi» khi zh là 他/她 (không phải họ hàng)
  · thiếu khoảng trắng sau dấu câu («Đù,thế»)
  · dấu nghìn → 1.000
  · biến thể thuật ngữ trong glossary («tám đại thiên cung» → «Bát Đại Thiên Cung»)
  · hoa/thường «X chi lực» theo đa số trong phim
lint_rows: báo cáo phần còn lại (tên/thuật ngữ sai hẳn → agent_name_pass sửa bằng LLM)
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import json
import re
from collections import Counter
from pathlib import Path

_KIN = r"ông|bà|cô|anh|chị|chú|bác|dì|lão|gã|mụ|thằng"
_KIN_TOI_RE = re.compile(rf"\b({_KIN})\s+tôi\b", re.IGNORECASE)
_ZH_3RD_RE = re.compile(r"他|她")
_ZH_KIN_RE = re.compile(r"爷|奶|姑|叔|伯|舅|姨|哥|姐|祖|父|母|爸|妈|爹|娘|师|婶|嫂")
_PUNCT_NOSPACE_RE = re.compile(r"(?<=[^\W\d_])([,;:!?]|\.(?!\.))(?=[^\W\d_])")
_NUM_SPACE_RE = re.compile(r"\b\d{1,3}(?:[ ,]\d{3})+\b")
_CHI_RE = re.compile(r"\b(\w+)\s+(chi)\s+(lực|thuật|thể|quang|ấn|hỏa|kiếm|khí)\b", re.IGNORECASE)
_TAIL_FRAG_RE = re.compile(r"[.!?…]\s+\S+(?:\s\S+)?,$")

_HV_NUM = {"một": "nhất", "hai": "nhị", "ba": "tam", "bốn": "tứ", "năm": "ngũ",
           "sáu": "lục", "bảy": "thất", "tám": "bát", "chín": "cửu", "mười": "thập"}
_HV_ALT = {**_HV_NUM, **{v: k for k, v in _HV_NUM.items()}}


def _fix_kin(zh: str, vi: str) -> str:
    if not _ZH_3RD_RE.search(zh) or _ZH_KIN_RE.search(zh):
        return vi
    return _KIN_TOI_RE.sub(lambda m: m.group(1) + " ta", vi)


def _fix_punct(vi: str) -> str:
    return _PUNCT_NOSPACE_RE.sub(r"\1 ", vi)


def _fix_num(vi: str) -> str:
    return _NUM_SPACE_RE.sub(lambda m: m.group(0).replace(" ", ".").replace(",", "."), vi)


def _glossary_terms(plan: dict | None) -> list[dict]:
    out = []
    for key in ("terms", "characters"):
        for e in (plan or {}).get(key) or []:
            zh, vi = (e.get("zh") or "").strip(), (e.get("vi") or "").strip()
            if len(zh) >= 2 and len(vi.split()) >= 2:
                out.append({"zh": zh, "vi": vi})
    return sorted(out, key=lambda t: -len(t["vi"]))


def _variant_re(vi: str) -> re.Pattern:
    parts = []
    for w in vi.split():
        alts = {w.lower(), _HV_ALT.get(w.lower(), w.lower())}
        parts.append("(?:" + "|".join(re.escape(a) for a in sorted(alts)) + ")")
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.IGNORECASE)


def _chi_majority(rows: list[dict]) -> bool | None:
    c = Counter()
    for r in rows:
        for m in _CHI_RE.finditer(r.get("vi") or ""):
            if m.group(1)[:1].isupper():
                c[m.group(2)[:1].isupper()] += 1
    if not c or c[True] == c[False]:
        return None
    return c[True] > c[False]


def autofix_rows(rows: list[dict], plan: dict | None = None) -> dict:
    terms = [(t, _variant_re(t["vi"])) for t in _glossary_terms(plan)]
    chi_title = _chi_majority(rows)
    n, sample = 0, []
    for r in rows:
        if not isinstance(r, dict) or r.get("manual"):
            continue
        zh, vi = r.get("zh") or "", r.get("vi") or ""
        if not vi:
            continue
        new = _fix_num(_fix_punct(_fix_kin(zh, vi)))
        for t, rx in terms:
            if t["zh"] in zh:
                new = rx.sub(t["vi"], new)
        if chi_title is not None:
            def _chi(m: re.Match) -> str:
                if not m.group(1)[:1].isupper():
                    return m.group(0)
                tail = f"{m.group(2)} {m.group(3)}"
                return f"{m.group(1)} {tail.title() if chi_title else tail.lower()}"
            new = _CHI_RE.sub(_chi, new)
        if new != vi:
            if len(sample) < 20:
                sample.append({"i": r.get("i"), "before": vi, "after": new})
            r["vi"] = new
            n += 1
    if n:
        print(f"[lint] tự sửa {n} câu (xưng hô/dấu câu/số/thuật ngữ)", flush=True)
    return {"n": n, "sample": sample}


def lint_rows(rows: list[dict], plan: dict | None = None) -> dict:
    """Đếm lỗi còn lại — không sửa. Dùng cho QA gate / agent / UI."""
    from agents.agent_name_pass import find_issues

    terms = [(t, _variant_re(t["vi"])) for t in _glossary_terms(plan)]
    cats: dict[str, list] = {k: [] for k in
                             ("kin_toi", "punct_space", "num_space", "term_variant", "term_missing", "tail_frag")}
    for r in rows:
        zh, vi = r.get("zh") or "", r.get("vi") or ""
        if not vi:
            continue
        item = {"i": r.get("i"), "zh": zh, "vi": vi}
        if _fix_kin(zh, vi) != vi:
            cats["kin_toi"].append(item)
        if _fix_punct(vi) != vi:
            cats["punct_space"].append(item)
        if _fix_num(vi) != vi:
            cats["num_space"].append(item)
        for t, rx in terms:
            if t["zh"] in zh and rx.search(vi) and t["vi"] not in vi:
                cats["term_variant"].append({**item, "term": t["vi"]})
                break
        if _TAIL_FRAG_RE.search(vi):
            cats["tail_frag"].append(item)
    cats["term_missing"] = find_issues(rows, plan or {})
    counts = {k: len(v) for k, v in cats.items()}
    fixable = sum(counts[k] for k in ("kin_toi", "punct_space", "num_space", "term_variant"))
    return {"counts": counts, "fixable": fixable,
            "samples": {k: v[:6] for k, v in cats.items() if v}}


def run_lint(work_dir: str | Path, fix: bool = False) -> dict:
    """Lint script.json của 1 job; fix=True thì autofix + ghi lại (+ SRT/display)."""
    work_dir = Path(work_dir)
    sp, gp = work_dir / "script.json", work_dir / "glossary.json"
    rows = json.loads(sp.read_text(encoding="utf-8"))
    plan = json.loads(gp.read_text(encoding="utf-8")) if gp.is_file() else {}
    before = lint_rows(rows, plan)
    if not fix:
        return {"ok": True, "before": before}
    res = autofix_rows(rows, plan)
    if res["n"]:
        sp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        write_display(work_dir, rows)
    return {"ok": True, "before": before, "fixed": res["n"], "sample": res["sample"],
            "after": lint_rows(rows, plan)}


def write_display(work_dir: Path, rows: list[dict]) -> int:
    from engines.subs import split_bilingual_rows, sub_split_cfg, to_srt

    mz, mv, md = sub_split_cfg()
    disp = split_bilingual_rows([dict(r) for r in rows], max_zh=mz, max_vi=mv, max_dur=md)
    (work_dir / "script.display.json").write_text(json.dumps(disp, ensure_ascii=False, indent=2), encoding="utf-8")
    (work_dir / ".display_split_vi").write_text(str(mv), encoding="utf-8")
    (work_dir / "sub.vi.srt").write_text(to_srt(disp, key="vi"), encoding="utf-8")
    return len(disp)


if __name__ == "__main__":
    import sys
    wd = sys.argv[1]
    out = run_lint(wd, fix="--fix" in sys.argv)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])
