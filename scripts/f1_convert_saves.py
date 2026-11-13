"""F1 (2026-11-11): chuyển mọi Manifest.save() sang state.job_state. Chạy 1 lần; idempotent."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORT = "from state import job_state as js  # V6 F1: mọi ghi manifest qua state\n"


def block(src: str, start_pat: str, end_pat: str, after: str | None = None) -> tuple[int, int, str]:
    """Tìm khối từ dòng chứa start_pat tới hết dòng chứa end_pat (sau vị trí `after`)."""
    base = src.index(after) if after else 0
    i = src.index(start_pat, base)
    ls = src.rindex("\n", 0, i) + 1
    j = src.index(end_pat, i)
    le = src.index("\n", j) + 1
    indent = src[ls:i]
    return ls, le, indent


def replace_block(src, start_pat, end_pat, new_line, after=None):
    ls, le, ind = block(src, start_pat, end_pat, after)
    new = "".join(ind + ln + "\n" for ln in new_line.splitlines())
    return src[:ls] + new + src[le:]


def server_semantic(src: str) -> str:
    if "js.set_review_retry(" not in src:
        src = replace_block(src, "m_pend = Manifest(wd)", "m_pend.save()",
                            "js.set_review_retry(wd, n_unrev=n_unrev, retry_sec=_REVIEW_RETRY_SEC,\n"
                            "                    max_tries=_REVIEW_RETRY_MAX)",
                            after="PARK ghế")
    if 'reason="qa_brain"' not in src:
        src = replace_block(src, "m_br = Manifest(wd)", "m_br.save()",
                            'js.set_hold(wd, reason="qa_brain",\n'
                            '            detail=f"Second Brain: {qb.get(\'high_open\')} high QA — chặn TTS/mux",\n'
                            '            qa_blocked=True,\n'
                            '            stage_note=f"HOLD Brain — {qb.get(\'high_open\')} high QA flags")')
    if 'reason="qa_gate"' not in src:
        src = replace_block(src, "m_qa = Manifest(wd)", "m_qa.save()",
                            'js.set_hold(wd, reason="qa_gate", detail=f"QA gate block — {reasons}",\n'
                            '            qa_blocked=True, stage_note=f"HOLD QA gate — {reasons}")')
    while "m_opts = Manifest(work_dir)" in src:
        ls, le, ind = block(src, "m_opts = Manifest(work_dir)", "m_opts.save()")
        seg = src[ls:le]
        m = re.search(r'qo\["hardsub_blur"\] = (.+)\n', seg)
        assert m, seg
        src = src[:ls] + f'{ind}js.set_queue_opts(work_dir, {{"hardsub_blur": {m.group(1).strip()}}}, merge=True)\n' + src[le:]
    if "m_stamp = Manifest(work_dir)" in src:
        ls, le, ind = block(src, "m_stamp = Manifest(work_dir)", "m_stamp.save()")
        seg = src[ls:le]
        body = seg.split('m_stamp.data["queue_opts"] = ', 1)[1]
        body = body[: body.rindex("m_stamp.save()")].rstrip()
        assert body.startswith("{") and body.endswith("}"), body[-40:]
        src = src[:ls] + f"{ind}js.set_queue_opts(work_dir, {body})\n" + src[le:]
    return src


SAVE = re.compile(r"\b([A-Za-z_][A-Za-z_0-9]*)\.save\(\)")


def add_import(src: str) -> str:
    if IMPORT.strip() in src:
        return src
    lines = src.splitlines(keepends=True)
    # sau khối import đầu file (sau docstring/__future__)
    idx = 0
    for k, ln in enumerate(lines[:200]):
        if ln.startswith(("import ", "from ")) and "__future__" not in ln:
            idx = k
            break
    return "".join(lines[:idx]) + IMPORT + "".join(lines[idx:])


def convert(path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    orig = src
    if path.name == "server.py":
        src = server_semantic(src)
    src, n = SAVE.subn(r"js.persist(\1)", src)
    if src != orig:
        src = add_import(src)
        path.write_text(src, encoding="utf-8")
    return n


if __name__ == "__main__":
    targets = [ROOT / p for p in sys.argv[1:]]
    total = 0
    for p in targets:
        n = convert(p)
        total += n
        print(f"{p.relative_to(ROOT)}: {n} persist")
    print("tổng persist:", total)
