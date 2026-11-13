"""
Rà XƯNG HÔ trong bản dịch theo glossary.pronouns + genre.

1. Quét deterministic (cùng apply_pronoun_postpass) rồi
2. Các câu còn nghi (Ta/Ngươi lệch genre, hoặc cặp who+to có mặt mà self/call lệch)
   → LLM chỉ sửa đại từ / danh xưng, giữ nghĩa còn lại.
"""
from __future__ import annotations

from state import job_state as js  # V6 F1: mọi ghi manifest qua state
import json
import re
import time
import unicodedata
from pathlib import Path

from engines.llm import chat, parse_json, is_local, unload_local
from core.manifest import Manifest

BATCH = 25


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower().replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def find_pronoun_issues(rows: list[dict], plan: dict) -> list[dict]:
    from engines.translate import (
        _is_modernish_genre,
        _pronoun_pairs,
        _char_aliases,
    )

    modern = _is_modernish_genre(plan)
    pairs = _pronoun_pairs(plan)
    aliases = _char_aliases(plan)
    issues = []

    def mentions(blob: str, name: str) -> bool:
        if not name:
            return False
        if name in blob:
            return True
        keys = aliases.get(name) or {name}
        for ks in aliases.values():
            if name in ks:
                keys |= ks
        return any(k and k in blob for k in keys)

    for r in rows:
        if not isinstance(r, dict) or r.get("manual"):
            continue
        zh = (r.get("zh") or "").strip()
        vi = (r.get("vi") or "").strip()
        if not zh or not vi:
            continue
        reasons = []
        if modern:
            if re.search(r"\b[Nn]gươi\b", vi):
                reasons.append("còn «ngươi» (đô thị/mixed → cậu/mày/anh…)")
            # Ta đầu câu (không phải ông ta)
            if re.search(r"(?:^|[.!?…]\s*)Ta\b", vi) or re.search(
                r"(?:^|[.!?…]\s*)\bta\b", vi
            ):
                reasons.append("còn «Ta/ta» ngôi 1 (đô thị → tôi/tao)")
        blob = zh + "\n" + vi
        for pr in pairs:
            who, to = pr["who"], pr["to"]
            if not mentions(zh, who):
                continue
            if to and to not in ("*", "chung", "narrator") and not mentions(blob, to):
                continue
            self_f, call = (pr.get("self") or "").strip(), (pr.get("call") or "").strip()
            fv = _fold(vi)
            if call and _fold(call) and _fold(call) not in fv:
                if re.search(r"\b[Nn]gươi\b|\b[Tt]a\b", vi) or (
                    call.lower() in ("cậu", "mày", "anh", "em") and "ngươi" in vi.lower()
                ):
                    reasons.append(
                        f"{who}→{to or '?'}: kỳ vọng gọi «{call}», tự xưng «{self_f or '?'}»"
                    )
        if reasons:
            issues.append({
                "i": r.get("i"),
                "zh": zh,
                "vi": vi,
                "reasons": reasons[:4],
            })
    return issues


def _fix_batch(
    chunk: list[dict],
    plan_block: str,
    model: str | None,
    *,
    system: str | None = None,
) -> dict[int, str]:
    sys = system or (
        "Bạn là biên tập viên lồng tiếng Việt. Sửa ĐẠI TỪ XƯNG HÔ / danh xưng cho khớp "
        "ma trận who→to và thể loại (đô thị: tôi/cậu/mày — CẤM Ta/Ngươi). "
        "CHỈ đổi đại từ/danh xưng liên quan; giữ nguyên nghĩa, độ dài, tên riêng đã đúng. "
        'CHỈ trả JSON {"fixes":[{"i":<int>,"vi":"..."}]}.'
    )
    user = (

        plan_block
        + "\n\nCÁC CÂU NGHI LỆCH XƯNG HÔ:\n"
        + json.dumps(
            [{"i": c["i"], "zh": c["zh"], "vi": c["vi"], "why": c["reasons"]} for c in chunk],
            ensure_ascii=False,
        )
    )
    out = chat(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        model=model, temperature=0.1, json_mode=True, max_tokens=4096,
    )
    try:
        data = parse_json(out)
        fixes = data.get("fixes", data if isinstance(data, list) else [])
        return {
            int(f["i"]): (f.get("vi") or "").strip()
            for f in fixes
            if isinstance(f, dict) and "i" in f and (f.get("vi") or "").strip()
        }
    except Exception:  # noqa: BLE001
        return {}


def run_pronoun_pass(work_dir: str | Path, model: str | None = None) -> dict:
    from engines.translate import (
        _plan_block_for_qa,
        ensure_hard_terms,
        apply_pronoun_postpass,
    )
    from engines.subs import to_srt
    try:
        from core.config import pass_llm_model
    except Exception:  # noqa: BLE001
        def pass_llm_model(kind: str = "pronoun") -> str | None:  # type: ignore
            return None

    work_dir = Path(work_dir)
    sp, gp = work_dir / "script.json", work_dir / "glossary.json"
    if not sp.is_file():
        return {"ok": False, "error": "chưa có script.json"}
    rows = json.loads(sp.read_text(encoding="utf-8"))
    plan = json.loads(gp.read_text(encoding="utf-8")) if gp.is_file() else {}
    plan = ensure_hard_terms(plan)

    # 1) Deterministic trước
    n_det = apply_pronoun_postpass(rows, plan)

    issues = find_pronoun_issues(rows, plan)
    # #3 policy: title bậc + «cậu» → formal 2nd (ngươi/ông theo register)
    reg = None
    preferred = "ngươi"
    sys_override = None
    n_title = 0
    n_mech = 0
    try:
        from engines.pronoun_register_policy import (
            build_register_plan,
            scan_title_intimate_issues,
            normalize_ong_nguoi_in_title_window,
            enforce_remaining_cau,
        )
        from core.config import get as _cfg_get
        pol_on = (_cfg_get("PRONOUN_TITLE_POLICY", "1") or "1").strip().lower() in (
            "1", "true", "yes", "on",
        )
        if pol_on:
            reg = build_register_plan(plan)
            preferred = reg.preferred_formal_2nd
            seen = {x.get("i") for x in issues}
            extra = scan_title_intimate_issues(rows, window=2, existing_ids=seen)
            n_title = len(extra)
            issues.extend(extra)
            sys_override = reg.prompt_rules()
            print(
                f"[pronoun] title-policy mode={reg.mode} prefer={preferred} "
                f"+{n_title} title_intimate",
                flush=True,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[pronoun] title-policy skip: {e}", flush=True)

    rmodel = model or pass_llm_model("pronoun")
    pos = {r["i"]: k for k, r in enumerate(rows) if isinstance(r, dict) and "i" in r}
    plan_block = _plan_block_for_qa(plan)
    m = Manifest(work_dir)
    fixed, sample = 0, []
    touched: set[int] = set()
    for k in range(0, len(issues), BATCH):
        chunk = issues[k : k + BATCH]
        try:
            fixes = _fix_batch(chunk, plan_block, rmodel, system=sys_override)
        except Exception as e:  # noqa: BLE001
            print(f"[pronoun] lô {k // BATCH} lỗi, bỏ qua: {e}", flush=True)
            fixes = {}
        for i, vi in fixes.items():
            if i in pos and vi and vi != rows[pos[i]].get("vi"):
                if len(sample) < 15:
                    sample.append({"i": i, "before": rows[pos[i]].get("vi"), "after": vi, "why": chunk[0].get("reasons") if chunk else []})
                rows[pos[i]]["vi"] = vi
                rows[pos[i]]["pr"] = 1
                fixed += 1
                touched.add(int(i))
        # post-pass lại sau LLM
        apply_pronoun_postpass(rows, plan)
        if reg is not None and touched:
            try:
                normalize_ong_nguoi_in_title_window(rows, touched, preferred, window=2)
            except Exception:  # noqa: BLE001
                pass
        sp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            st = m.data.setdefault("stages", {}).setdefault("translate", {})
            st["cur_text"] = (
                f"Agent: rà xưng hô {min(k + BATCH, len(issues))}/{len(issues)} câu nghi"
            )
            st["updated_at"] = time.time()
            js.persist(m)
        except Exception:  # noqa: BLE001
            pass
    if reg is not None:
        try:
            mech = enforce_remaining_cau(rows, preferred, window=2)
            n_mech = len(mech)
            if n_mech:
                apply_pronoun_postpass(rows, plan)
                sp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"[pronoun] mech enforce skip: {e}", flush=True)

    try:
        (work_dir / "sub.vi.srt").write_text(to_srt(rows, key="vi"), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if is_local(rmodel):
        unload_local(rmodel)
    print(
        f"[pronoun] det={n_det} · nghi={len(issues)} · title={n_title} · "
        f"LLM sửa={fixed} · mech={n_mech}",
        flush=True,
    )
    return {
        "ok": True,
        "n_det": n_det,
        "n_issues": len(issues),
        "n_title": n_title,
        "n_fixed": fixed,
        "n_mech": n_mech,
        "register": {"mode": getattr(reg, "mode", None), "prefer": preferred} if reg else None,
        "sample": sample,
    }
