"""Deterministic VI quality scan + safe heals (Second Brain L2).

Port từ D:\\PinelineV2_tmp\\_qa_watch_05670.py — không phụ thuộc Gemma.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CALQUE_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "没大没小",
        re.compile(r"(?i)(chưa\s+)?lớn\s+không\s+nhỏ|không\s+lớn\s+không\s+nhỏ"),
        "calque 没大没小 → nên «không biết trên dưới»",
    ),
    (
        "打天下",
        re.compile(r"(?i)chinh phục thiên hạ"),
        "calque 打天下",
    ),
    (
        "穷酸",
        re.compile(r"(?i)nghèo nàn"),
        "calque 穷酸 → thảm hại",
    ),
    (
        "好大力气",
        re.compile(r"(?i)tốn rất nhiều công sức"),
        "calque 好大力气",
    ),
]

VI_ONLY_BAD = [
    (re.compile(r"(?i)\b\d+\s*vạn(\s*tỷ)?\b"), "calque số «Nvạn»"),
    (re.compile(r"(?i)ba vạn|hai ba vạn|mười vạn năm"), "calque vạn HV"),
    (re.compile(r"(?i)người thức tỉnh"), "觉醒者 nên Giác Tỉnh Giả?"),
    (re.compile(r"(?i)luồng sức mạnh kỳ lạ"), "calque sức mạnh kỳ lạ"),
    (re.compile(r"[\u4e00-\u9fff]"), "CJK còn trong VI"),
]

HANG = re.compile(
    r"(?i)(trước đó|sau đó|nhưng|và|thì|là|của|với|cho|ở|tới|đến|nên|vì|nếu)\s*[.。…]*$"
)
STUTTER_VI = re.compile(
    r"(?i)\b([A-Za-zÀ-ỹĐđ]{2,}(?:\s+[A-Za-zÀ-ỹĐđ]{2,}){0,2})\s+\1\b"
)
ONOMAT = re.compile(r"(?i)^(đinh|tinh|ha+|haha|khà|ầm|ầm ầm|leng keng)+$")
OK_REDUPE = {
    "đường đường", "đàng hoàng", "được được", "thôi thôi", "đâu đâu",
    "sao sao", "gì gì", "này này", "kia kia", "mau mau", "nhanh nhanh",
    "chậm chậm", "từ từ", "hừ hừ", "ha ha", "haha", "khà khà",
    "đi đi", "về về", "oa oa", "oà oà", "xì xì", "tút tút", "ừ ừ", "ờ ờ",
    "cậu cậu",
}
NAME_ECHO_STOP = {
    "thứ", "người", "cậu", "mày", "anh", "em", "ta", "tôi", "hắn", "cô",
    "chị", "ông", "bà", "nó", "bọn", "các", "một", "hai", "ba",
}

_MEIDAMEIXIAO_WANT = "Cậu còn không biết trên dưới à."


def scan_script(rows: list[dict]) -> list[dict]:
    """Trả flags {i,sev,kind,note,zh,vi,rv,mt}."""
    flags: list[dict] = []
    for r in rows:
        if not isinstance(r, dict) or "i" not in r:
            continue
        try:
            i = int(r["i"])
        except (TypeError, ValueError):
            continue
        zh = str(r.get("zh") or "").strip()
        vi = str(r.get("vi") or "").strip()
        rv = r.get("rv")
        mt = r.get("mt")
        if not vi:
            flags.append({
                "i": i, "sev": "high", "kind": "empty_vi", "note": "VI trống",
                "zh": zh, "vi": vi, "rv": rv, "mt": mt,
            })
            continue
        for needle, pat, note in CALQUE_RULES:
            if needle in zh and pat.search(vi):
                flags.append({
                    "i": i, "sev": "high", "kind": "calque", "note": note,
                    "zh": zh, "vi": vi, "rv": rv, "mt": mt,
                })
        for pat, note in VI_ONLY_BAD:
            if pat.search(vi):
                flags.append({
                    "i": i,
                    "sev": "high" if "CJK" in note else "med",
                    "kind": "vi_pattern",
                    "note": note,
                    "zh": zh, "vi": vi, "rv": rv, "mt": mt,
                })
        words = re.findall(r"[A-Za-zÀ-ỹĐđ0-9]+", vi)
        # hang_ack=1: Brain/LLM đã trace và xác nhận thoại cố ý nửa câu / false positive
        if HANG.search(vi) and len(words) <= 8 and not r.get("hang_ack"):
            sev = "high" if (
                re.search(r"(?i)(cho nên|vì vậy|tuy nhiên|nhưng|và|là)\s*[.。…]*$", vi)
                or re.search(r"(?i)(đây là|đó là)\s*[.。…]*$", vi)
            ) else "med"
            flags.append({
                "i": i, "sev": sev, "kind": "hang", "note": "đuôi treo / nửa câu",
                "zh": zh, "vi": vi, "rv": rv, "mt": mt,
            })
        for m_ne in re.finditer(
            r"(?i)\b([A-ZÀ-Ỹ][\wÀ-ỹ]{3,}(?:\s+[A-ZÀ-Ỹ][\wÀ-ỹ]{2,})?)\b"
            r".{0,30}\bcũng là\b.{0,16}\b\1\b",
            vi,
        ):
            name = m_ne.group(1).strip()
            first = name.split()[0].lower()
            if name.lower() in NAME_ECHO_STOP or first in NAME_ECHO_STOP:
                continue
            flags.append({
                "i": i, "sev": "high", "kind": "name_echo",
                "note": f"lặp danh xưng «{name} cũng là {name}»",
                "zh": zh, "vi": vi, "rv": rv, "mt": mt,
            })
            break
        m = STUTTER_VI.search(vi)
        if m:
            frag = m.group(1).strip()
            frag_l = frag.lower()
            redupe = f"{frag_l} {frag_l}"
            if (
                len(frag) >= 2
                and frag_l not in OK_REDUPE
                and redupe not in OK_REDUPE
                and not ONOMAT.search(frag_l.replace(" ", ""))
                and not re.fullmatch(
                    r"(?i)được|thôi|ờ|ừ|ừm|đường|đi|cậu|oa|oà|xì|tút", frag_l
                )
            ):
                flags.append({
                    "i": i, "sev": "med", "kind": "stutter",
                    "note": f"lặp «{frag}»",
                    "zh": zh, "vi": vi, "rv": rv, "mt": mt,
                })
        if re.search(r"(?i)\b(không|sai|đúng|phải|chờ|đợi)\s+\1\s+\1\b", vi):
            flags.append({
                "i": i, "sev": "med", "kind": "stutter3",
                "note": "lặp 3 lần cùng từ (ASR?)",
                "zh": zh, "vi": vi, "rv": rv, "mt": mt,
            })
        if zh and vi and len(words) <= 4 and len(zh) >= 6:
            if re.search(r"(?i)^(cậu|mày|em|anh|ta|ngươi)\s+(còn|đã|vẫn)\s+", vi):
                if re.search(r"(?i)không\s+\S+\s+không\s+", vi) or re.search(
                    r"(?i)chưa\s+\S+\s+không\s+", vi
                ):
                    flags.append({
                        "i": i, "sev": "high", "kind": "nonsense_shape",
                        "note": "cấu trúc «chưa X không Y» khả nghi",
                        "zh": zh, "vi": vi, "rv": rv, "mt": mt,
                    })
    seen: set[tuple] = set()
    out: list[dict] = []
    for f in flags:
        k = (f["i"], f["kind"], f["note"])
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


def apply_safe_heals(rows: list[dict]) -> int:
    """Heal deterministic an toàn tại chỗ. Trả số câu đổi."""
    n = 0
    for r in rows:
        if not isinstance(r, dict) or r.get("manual"):
            continue
        zh = str(r.get("zh") or "")
        vi = str(r.get("vi") or "").strip()
        if not vi:
            continue
        new = vi
        if "没大没小" in zh:
            if re.search(r"(?i)(chưa\s+)?lớn\s+không\s+nhỏ|không\s+lớn\s+không\s+nhỏ", new):
                new2 = re.sub(
                    r"(?i)(còn\s+)?(chưa\s+)?lớn\s+không\s+nhỏ",
                    "còn không biết trên dưới",
                    new,
                    count=1,
                )
                new2 = re.sub(
                    r"(?i)không\s+lớn\s+không\s+nhỏ|lớn\s+chưa\s+nhỏ|chưa\s+lớn\s+chưa\s+nhỏ",
                    "không biết trên dưới",
                    new2,
                    count=1,
                )
                new2 = re.sub(r"(?i)\bcòn\s+còn\s+", "còn ", new2)
                new2 = re.sub(r"\s{2,}", " ", new2).strip()
                if new2 and new2[-1] not in ".!?…":
                    new2 += "."
                if re.fullmatch(
                    r"(?i)(cậu|mày|em|anh|ngươi|người)\s+còn\s+không biết trên dưới\.",
                    new2,
                ):
                    new2 = new2[:-1] + " à."
                new = new2 or _MEIDAMEIXIAO_WANT
        if "穷酸" in zh and "nghèo nàn" in new:
            new = new.replace("nghèo nàn", "thảm hại")
        if "打天下" in zh:
            new = new.replace("chinh phục thiên hạ", "đánh thiên hạ")
        if "好大力气" in zh and "rất nhiều công sức" in new:
            new = new.replace("tốn rất nhiều công sức", "khó khăn lắm")
        if "觉醒者" in zh:
            new = new.replace("Người thức tỉnh", "Giác Tỉnh Giả").replace(
                "người thức tỉnh", "Giác Tỉnh Giả"
            )
        if "一股奇怪的力量" in zh and "luồng sức mạnh kỳ lạ" in new:
            new = new.replace("luồng sức mạnh kỳ lạ", "luồng lực kỳ lạ")
        if new != vi:
            r["vi"] = new
            n += 1
    return n


def summarize_flags(flags: list[dict]) -> dict[str, Any]:
    by_sev = Counter(str(f.get("sev") or "") for f in flags)
    by_kind = Counter(str(f.get("kind") or "") for f in flags)
    high = [f for f in flags if f.get("sev") == "high"]
    return {
        "n_flags": len(flags),
        "n_high": len(high),
        "high_open": len(high),
        "by_sev": dict(by_sev),
        "by_kind": dict(by_kind),
        "high_i": [int(f["i"]) for f in high[:80] if "i" in f],
        "high_sample": high[:15],
    }


def write_qa_brain(work_dir: Path, flags: list[dict], *, healed: int = 0) -> dict:
    """Ghi work/<job>/qa_brain.json cho gate + UI."""
    work_dir = Path(work_dir)
    summary = summarize_flags(flags)
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "healed": healed,
        **summary,
        "flags": flags[:200],
    }
    path = work_dir / "qa_brain.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_qa_brain(work_dir: Path) -> dict:
    p = Path(work_dir) / "qa_brain.json"
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def scan_and_heal_job(work_dir: Path, *, write: bool = True) -> dict:
    """Scan script.json → heal → (high hang) LLM trace/fix → qa_brain.json."""
    work_dir = Path(work_dir)
    sp = work_dir / "script.json"
    if not sp.is_file():
        return {"ok": False, "error": "no script.json"}
    try:
        rows = json.loads(sp.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    if not isinstance(rows, list):
        return {"ok": False, "error": "script not list"}

    healed = apply_safe_heals(rows)
    flags = scan_script(rows)
    hang_high = [
        f for f in flags
        if isinstance(f, dict) and f.get("kind") == "hang" and f.get("sev") == "high"
    ]
    hang_r: dict = {}
    if hang_high:
        try:
            from agents.agent_hang_fix import fix_hang_job, enabled as hang_enabled
            if hang_enabled():
                # ghi safe-heal trước để fix_hang_job reload không mất
                if write and healed:
                    try:
                        from core.fileio import atomic_write_json
                        atomic_write_json(sp, rows, fsync=False)
                    except Exception:  # noqa: BLE001
                        sp.write_text(
                            json.dumps(rows, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                # fix_hang_job tự reload/ghi script + qa_brain
                hang_r = fix_hang_job(work_dir, write=write)
                if hang_r.get("skipped"):
                    # đang review/dịch — giữ flags hiện tại, không HOLD-clear sớm
                    pass
                else:
                    healed += int(hang_r.get("n_fixed") or 0) + int(
                        hang_r.get("n_safe") or 0
                    )
                    brain = hang_r.get("brain") or (
                        load_qa_brain(work_dir) if write else summarize_flags(flags)
                    )
                    return {
                        "ok": True,
                        "job_id": work_dir.name,
                        "healed": healed,
                        "n_flags": brain.get("n_flags", 0),
                        "n_high": brain.get("n_high", 0),
                        "high_open": int(
                            hang_r.get("high_open")
                            if hang_r.get("high_open") is not None
                            else brain.get("high_open")
                            or 0
                        ),
                        "hang_fix": {
                            "n_cases": hang_r.get("n_cases"),
                            "n_fixed": hang_r.get("n_fixed"),
                            "high_hang_left": hang_r.get("high_hang_left"),
                            "trace_path": hang_r.get("trace_path"),
                        },
                        "brain": brain,
                    }
        except Exception as e:  # noqa: BLE001
            hang_r = {"ok": False, "error": str(e)[:200]}

    if write and healed:
        try:
            from core.fileio import atomic_write_json
            atomic_write_json(sp, rows, fsync=False)
        except Exception:  # noqa: BLE001
            sp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    brain = write_qa_brain(work_dir, flags, healed=healed) if write else summarize_flags(flags)
    out = {
        "ok": True,
        "job_id": work_dir.name,
        "healed": healed,
        "n_flags": brain.get("n_flags", len(flags)),
        "n_high": brain.get("n_high", 0),
        "high_open": int(brain.get("high_open") or 0),
        "brain": brain,
    }
    if hang_r:
        out["hang_fix"] = hang_r
    return out
