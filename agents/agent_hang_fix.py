# -*- coding: utf-8 -*-
"""Second Brain — pass LLM trace + xử lý high `hang` (đuôi treo / nửa câu).

Không chỉ merge mù: mỗi flag được chẩn đoán (root), chọn phương án, rồi áp VI.
Ghi `qa_hang_trace.json` để audit. Chỉ mở HOLD khi re-scan hết high hang.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BATCH = 8


def _cfg(key: str, default: str | None = None) -> str | None:
    try:
        from core.config import get
        return get(key, default)
    except Exception:  # noqa: BLE001
        return os.getenv(key, default)


def enabled() -> bool:
    v = (_cfg("BRAIN_HANG_FIX", "1") or "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def _pos_map(rows: list[dict]) -> dict[int, int]:
    return {
        int(r["i"]): j
        for j, r in enumerate(rows)
        if isinstance(r, dict) and "i" in r
    }


def _window(rows: list[dict], idx: int, *, radius: int = 2) -> list[dict]:
    lo = max(0, idx - radius)
    hi = min(len(rows), idx + radius + 1)
    out = []
    for j in range(lo, hi):
        r = rows[j]
        if not isinstance(r, dict):
            continue
        out.append({
            "i": r.get("i"),
            "zh": (r.get("zh") or "").strip(),
            "vi": (r.get("vi") or "").strip(),
            "focus": j == idx,
        })
    return out


def collect_hang_cases(rows: list[dict], flags: list[dict] | None = None) -> list[dict]:
    """Lấy high hang (+ med hang kết thúc là/nhưng rõ) kèm cửa sổ ZH/VI."""
    if flags is None:
        from agents.agent_quality import scan_script
        flags = scan_script(rows)
    pos = _pos_map(rows)
    cases: list[dict] = []
    seen: set[int] = set()
    for f in flags:
        if not isinstance(f, dict):
            continue
        if str(f.get("kind") or "") != "hang":
            continue
        sev = str(f.get("sev") or "")
        if sev != "high":
            # med hang vẫn có thể xử lý nếu kết thúc bằng «là/nhưng»
            vi = str(f.get("vi") or "")
            if not re.search(r"(?i)(là|nhưng|vì|nên|của)\s*[.。…]*$", vi):
                continue
        try:
            i = int(f["i"])
        except (KeyError, TypeError, ValueError):
            continue
        if i in seen or i not in pos:
            continue
        seen.add(i)
        idx = pos[i]
        cases.append({
            "i": i,
            "sev": sev or "high",
            "kind": "hang",
            "note": f.get("note") or "đuôi treo / nửa câu",
            "zh": (f.get("zh") or rows[idx].get("zh") or "").strip(),
            "vi": (f.get("vi") or rows[idx].get("vi") or "").strip(),
            "window": _window(rows, idx, radius=2),
        })
    return cases


def _llm_trace_batch(cases: list[dict], model: str | None) -> list[dict]:
    """Trả list {i, root_cause, plan, rationale, fixes:[{i,vi}]}."""
    from engines.llm import chat, parse_json

    sys = (
        "Bạn là biên tập phụ đề VI + debug ASR/dịch. Mỗi CASE là cue bị gắn cờ "
        "hang (đuôi treo / nửa câu). ĐỌC cửa sổ ZH+VI quanh focus.\n"
        "Nhiệm vụ:\n"
        "1) root_cause: asr_cut | dialogue_trail | bad_mt | false_positive\n"
        "2) plan: một trong\n"
        "   - complete: ưu tiên — hoàn câu FOCUS bằng cách lấy nghĩa từ cue sau "
        "trong VI focus; GIỮ NGUYÊN VI các cue khác (không cắt mảnh cue sau)\n"
        "   - rewrite: viết lại VI focus đủ nghĩa, bám ZH focus\n"
        "   - keep_intentional: thoại cố ý nửa câu / dẫn ý sang cue sau — fixes=[]\n"
        "   - merge_next: CHỈ khi bắt buộc; vẫn phải để cue sau thành câu đủ nghĩa "
        "(không để sót mảnh «thân thể.» / «đó,»)\n"
        "3) rationale: 1 câu ngắn vì sao chọn plan\n"
        "4) fixes: chỉ các i cần đổi. Sau sửa, VI focus KHÔNG kết thúc bằng "
        "là/nhưng/và/thì/của/với/cho + dấu nếu đó là đuôi treo vô nghĩa. "
        "Không xóa cue; không đổi i. Với dialogue_trail ngắn (Ý tôi là / Nhưng. / "
        "Tôi chính là.) → keep_intentional, fixes=[].\n"
        "CHỈ JSON: {\"items\":[{\"i\":int,\"root_cause\":str,\"plan\":str,"
        "\"rationale\":str,\"fixes\":[{\"i\":int,\"vi\":str}]}]}"
    )
    payload = []
    for c in cases:
        payload.append({
            "i": c["i"],
            "sev": c["sev"],
            "note": c["note"],
            "focus_zh": c["zh"],
            "focus_vi": c["vi"],
            "window": c["window"],
        })
    user = "CASES:\n" + json.dumps(payload, ensure_ascii=False)
    use_model = model or _cfg("HANG_FIX_MODEL") or _cfg("PRONOUN_MODEL") or "chatgpt/gpt-5-6-mini"
    out = chat(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        model=use_model,
        temperature=0.1,
        json_mode=True,
        max_tokens=4096,
    )
    try:
        data = parse_json(out)
    except Exception:  # noqa: BLE001
        return []
    items = data.get("items", data if isinstance(data, list) else [])
    if not isinstance(items, list):
        return []
    clean: list[dict] = []
    for it in items:
        if not isinstance(it, dict) or "i" not in it:
            continue
        fixes_in = it.get("fixes") or []
        fixes = []
        if isinstance(fixes_in, list):
            for fx in fixes_in:
                if isinstance(fx, dict) and "i" in fx and (fx.get("vi") or "").strip():
                    fixes.append({"i": int(fx["i"]), "vi": str(fx["vi"]).strip()})
        clean.append({
            "i": int(it["i"]),
            "root_cause": str(it.get("root_cause") or "unknown"),
            "plan": str(it.get("plan") or "rewrite"),
            "rationale": str(it.get("rationale") or "")[:240],
            "fixes": fixes,
        })
    return clean


def apply_trace_items(rows: list[dict], items: list[dict]) -> int:
    pos = _pos_map(rows)
    n = 0
    for it in items:
        plan = str(it.get("plan") or "").strip().lower()
        root = str(it.get("root_cause") or "").strip().lower()
        focus_i = int(it["i"]) if "i" in it else None
        # Thoại cố ý nửa câu / false positive → ack để không HOLD lại
        if focus_i is not None and focus_i in pos and plan in (
            "keep_intentional", "keep",
        ):
            rows[pos[focus_i]]["hang_ack"] = 1
            rows[pos[focus_i]]["hang_plan"] = plan
            rows[pos[focus_i]]["hang_root"] = root or "dialogue_trail"
        if focus_i is not None and focus_i in pos and root == "false_positive":
            rows[pos[focus_i]]["hang_ack"] = 1
            rows[pos[focus_i]]["hang_plan"] = plan or "keep_intentional"
            rows[pos[focus_i]]["hang_root"] = "false_positive"
        for fx in it.get("fixes") or []:
            i = int(fx["i"])
            vi = (fx.get("vi") or "").strip()
            if i not in pos or not vi:
                continue
            j = pos[i]
            if rows[j].get("manual"):
                continue
            if vi != (rows[j].get("vi") or "").strip():
                rows[j]["vi"] = vi
                rows[j]["hang_fix"] = 1
                rows[j]["hang_plan"] = plan or rows[j].get("hang_plan")
                rows[j]["hang_root"] = root or rows[j].get("hang_root")
                # sau rewrite/merge/complete: nếu hết đuôi treo thì thôi; nếu còn
                # mà LLM chọn keep thì đã ack ở trên
                n += 1
            elif plan in ("rewrite", "complete", "merge_next") and focus_i == i:
                # LLM không đổi chữ nhưng đã xem — nếu vẫn hang, không ack
                pass
    return n


def _review_or_translate_busy(work_dir: Path) -> str | None:
    """Tránh race: review/dịch đang ghi script sẽ xoá hang_ack/hang_fix.

    Quan trọng: heal có thể hạ status running→pending giữa soát — vẫn coi là busy
    nếu review_done < review_total / review_progress < 100.
    """
    try:
        from core.manifest import Manifest
        m = Manifest(work_dir)
        tr = (m.data.get("stages") or {}).get("translate") or {}
        st = str(tr.get("status") or "").lower()
        # Mid-review kể cả khi heal đã gỡ running
        rd, rt = tr.get("review_done"), tr.get("review_total")
        try:
            if (
                tr.get("review")
                and rt is not None
                and rd is not None
                and int(rd) < int(rt)
            ):
                return f"review {rd}/{rt}"
        except (TypeError, ValueError):
            pass
        rp = tr.get("review_progress")
        try:
            if tr.get("review") and rp is not None and float(rp) < 99.5:
                return f"review_progress={rp}"
        except (TypeError, ValueError):
            pass
        if st not in ("running", "review", "reviewing"):
            return None
        # dịch NMT còn missing
        try:
            if int(tr.get("n_missing") or 0) > 0:
                return f"translate missing={tr.get('n_missing')}"
        except (TypeError, ValueError):
            pass
        return None
    except Exception:  # noqa: BLE001
        return None


def fix_hang_job(
    work_dir: str | Path,
    *,
    model: str | None = None,
    write: bool = True,
    max_cases: int = 40,
    force: bool = False,
) -> dict[str, Any]:
    """Trace + LLM fix hang → ghi script + qa_hang_trace.json → re-scan quality."""
    if not enabled():
        return {"ok": True, "skipped": True, "reason": "BRAIN_HANG_FIX off"}

    work_dir = Path(work_dir)
    if not force:
        busy = _review_or_translate_busy(work_dir)
        if busy:
            return {
                "ok": True,
                "skipped": True,
                "reason": f"busy:{busy}",
                "high_open": None,
            }
    sp = work_dir / "script.json"
    if not sp.is_file():
        return {"ok": False, "error": "no script.json"}
    rows = json.loads(sp.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        return {"ok": False, "error": "script not list"}

    from agents.agent_quality import scan_script, write_qa_brain, apply_safe_heals

    # deterministic trước
    n_safe = apply_safe_heals(rows)
    try:
        from engines.translate import apply_halfcue_dangling_heal
        n_safe += apply_halfcue_dangling_heal(rows)
    except Exception:  # noqa: BLE001
        pass

    flags0 = scan_script(rows)
    cases = collect_hang_cases(rows, flags0)[:max_cases]
    trace: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "job_id": work_dir.name,
        "n_cases": len(cases),
        "n_safe_healed": n_safe,
        "items": [],
        "n_fixed_cues": 0,
        "model": model or _cfg("HANG_FIX_MODEL") or _cfg("PRONOUN_MODEL") or "chatgpt/gpt-5-6-mini",
    }
    if not cases:
        brain = write_qa_brain(work_dir, flags0, healed=n_safe) if write else {}
        if write and n_safe:
            sp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "job_id": work_dir.name,
            "n_cases": 0,
            "n_fixed": 0,
            "n_safe": n_safe,
            "high_open": int(brain.get("high_open") or 0),
            "trace": trace,
        }

    all_items: list[dict] = []
    for k in range(0, len(cases), BATCH):
        chunk = cases[k : k + BATCH]
        try:
            items = _llm_trace_batch(chunk, model)
        except Exception as e:  # noqa: BLE001
            trace.setdefault("errors", []).append(str(e)[:200])
            items = []
        all_items.extend(items)

    n_fix = apply_trace_items(rows, all_items)
    # post deterministic lại
    n_safe2 = apply_safe_heals(rows)
    try:
        from engines.translate import apply_halfcue_dangling_heal
        n_safe2 += apply_halfcue_dangling_heal(rows)
    except Exception:  # noqa: BLE001
        pass

    flags1 = scan_script(rows)
    hang_left = [
        f for f in flags1
        if isinstance(f, dict) and f.get("kind") == "hang" and f.get("sev") == "high"
    ]
    trace["items"] = all_items
    trace["n_fixed_cues"] = n_fix
    trace["n_safe_after"] = n_safe2
    trace["high_hang_left"] = len(hang_left)
    trace["high_hang_i"] = [f.get("i") for f in hang_left[:40]]

    if write:
        sp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        (work_dir / "qa_hang_trace.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        try:
            from engines.subs import to_srt
            (work_dir / "sub.vi.srt").write_text(to_srt(rows, key="vi"), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        brain = write_qa_brain(work_dir, flags1, healed=n_safe + n_fix + n_safe2)
    else:
        from agents.agent_quality import summarize_flags
        brain = summarize_flags(flags1)

    return {
        "ok": True,
        "job_id": work_dir.name,
        "n_cases": len(cases),
        "n_fixed": n_fix,
        "n_safe": n_safe + n_safe2,
        "high_open": int(brain.get("high_open") or 0),
        "high_hang_left": len(hang_left),
        "trace_path": str(work_dir / "qa_hang_trace.json") if write else None,
        "brain": brain,
        "trace": trace,
    }
