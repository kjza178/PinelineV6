"""Phương án dịch tối ưu — ngữ cảnh + văn phong (trọng tâm Pineline).

Quét câu dễ lệch / đa nghĩa → 2–3 bản VI xếp hạng (pick = tối ưu đề xuất).
Bám Context Studio pack (genre · style_lock · xưng hô · terms). Chunk + ngữ cảnh trước/sau.

  python translate_alts.py work/<job> [--force] [--limit 120] [--apply-pick]
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import json
import os
import re
import time
from pathlib import Path

from engines.llm import LLMUnavailable, chat, is_local, parse_json, unload_local
from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
from engines.translate import (
    CTX_LINES,
    _plan_block,
    _tmodel,
    _translate_rules,
    ensure_hard_terms,
)

STAGE = "translate_alts"

_AMB_RE = re.compile(
    r"(嘲讽|脑子进水|打天下|娘[，,]|先下手|万|亿|兆|奴隶|公主|"
    r"看来|不过如此|放手一搏|追踪|穷酸|眼馋|气息|印记|觉醒)"
)


def _heuristic_ambiguous(row: dict) -> str | None:
    """Trả lý do ngắn nếu câu nên đưa phương án; None = bỏ qua."""
    zh = (row.get("zh") or "").strip()
    vi = (row.get("vi") or "").strip()
    if not zh or not vi:
        return None
    if row.get("manual"):
        return None
    if isinstance(row.get("alts"), list) and len(row["alts"]) >= 2:
        return None
    dur = float(row.get("end") or 0) - float(row.get("start") or 0)
    if _AMB_RE.search(zh):
        return "mẫu dễ lệch nghĩa / số / xưng hô / thuật ngữ"
    if re.search(
        r"(?i)\bvạn\b|não vào nước|nhạo báng|đánh hạ thiên hạ|"
        r"\bMẹ,|làm nô lệ cho mẹ|phương thức truy|"
        r"một nghìn một|vạn tỷ",
        vi,
    ):
        return "VI nghi calque / lệch văn phong"
    if len(zh) <= 6 and dur < 0.5:
        return "câu rất ngắn (ASR?)"
    if "看" in zh and len(zh) <= 4:
        return "cụm ngắn có 看 — cần ngữ cảnh"
    if len(vi) > 8 and re.search(r"(của tôi|của cậu|một cách|tổng cộng tôi có)", vi):
        if re.search(r"[万亿血量技能]", zh):
            return "có thể tối ưu thoại/game"
    return None


def _alts_batch(
    rows: list[dict],
    plan_block: str,
    prev_ctx: list[dict],
    next_ctx: list[dict],
    model: str | None,
    plan: dict | None,
) -> dict[int, dict]:
    """Trả {i: {alts[], pick, reason}} — tối ưu theo ngữ cảnh + văn phong."""
    sys = (
        "Bạn là biên tập DỊCH TỐI ƯU phụ đề lồng tiếng Trung→Việt "
        "(short drama / đô thị dị năng / tinh cầu).\n"
        "MỤC TIÊU: với mỗi câu CẦN cải thiện, đưa 2–3 phương án VI khác nhau, "
        "xếp hạng — pick = bản TỐI ƯU nhất.\n"
        "Tiêu chí (theo thứ tự):\n"
        "1) Đúng nghĩa + đúng mạch với câu trước/sau (đáp lời, đá xoáy, setup→punch).\n"
        "2) Đúng VĂN PHONG pack: xưng hô đời thường; thuật ngữ cứng Hán Việt; "
        "số viết rõ (31.001 / nghìn tỷ — CẤM ba vạn…); "
        "成语 hay dùng có thể HV (vd Tiên hạ thủ vi cường).\n"
        "3) Thoại nghe như người Việt nói trong phim — gãy, ngầu, không dịch máy.\n"
        "4) Khớp độ dài thoại (sec).\n"
        "Câu đã tối ưu → BỎ QUA. Không chữ Hán trong vi. CHỈ JSON.\n"
        + _translate_rules(plan)
    )
    ctx = ""
    if prev_ctx:
        ctx += "NGỮ CẢNH TRƯỚC (đã chốt):\n" + "\n".join(
            f"  {c.get('zh','')} -> {c.get('vi','')}" for c in prev_ctx
        ) + "\n"
    if next_ctx:
        ctx += "NGỮ CẢNH SAU (chỉ để hiểu mạch, chưa sửa):\n" + "\n".join(
            f"  {c.get('zh','')} -> {c.get('vi','')}" for c in next_ctx
        ) + "\n"
    if ctx:
        ctx += "\n"
    payload = [
        {
            "i": r["i"],
            "sec": round(float(r.get("end") or 0) - float(r.get("start") or 0), 1),
            "zh": r.get("zh") or "",
            "vi": r.get("vi") or "",
            "hint": r.get("_amb_hint") or "",
        }
        for r in rows
    ]
    user = (
        f"{plan_block}\n\n{ctx}"
        "Tối ưu các câu sau. CHỈ trả câu CẦN phương án thay thế.\n"
        'JSON: {"items":[{"i":1,"reason":"vì sao cần đổi",'
        '"alts":["tối ưu nhất","phương án 2","phương án 3"],"pick":0}]}\n'
        "alts[0] nên là bản tốt nhất; pick=0 trừ khi cố ý chọn khác. "
        "Có thể giữ bản vi hiện tại trong alts nếu vẫn dùng được.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    out = chat(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        model=model or _tmodel(),
        temperature=0.2,
        json_mode=True,
        max_tokens=8192,
    )
    try:
        data = parse_json(out)
        items = data.get("items", data if isinstance(data, list) else [])
    except Exception:  # noqa: BLE001
        return {}
    res: dict[int, dict] = {}
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or "i" not in it:
            continue
        alts = [str(a).strip() for a in (it.get("alts") or []) if str(a).strip()]
        seen: set[str] = set()
        uniq: list[str] = []
        for a in alts:
            k = a.casefold()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(a)
        if len(uniq) < 2:
            continue
        try:
            ii = int(it["i"])
        except (TypeError, ValueError):
            continue
        pick = it.get("pick", 0)
        try:
            pick = int(pick)
        except (TypeError, ValueError):
            pick = 0
        pick = max(0, min(pick, len(uniq) - 1))
        res[ii] = {
            "alts": uniq[:4],
            "pick": pick,
            "reason": (it.get("reason") or "").strip()[:200],
        }
    return res


def run_ambiguous_alts(
    work_dir: str | Path,
    *,
    batch: int = 20,
    model: str | None = None,
    force: bool = False,
    limit: int | None = None,
    apply_pick: bool = False,
) -> dict:
    """Quét script → vi_alts.json (phương án tối ưu theo ngữ cảnh + văn phong)."""
    work_dir = Path(work_dir)
    m = Manifest(work_dir)
    script_path = work_dir / "script.json"
    if not script_path.exists():
        raise RuntimeError("Chưa có script.json")
    rows = json.loads(script_path.read_text(encoding="utf-8"))
    plan_path = work_dir / "glossary.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    plan = ensure_hard_terms(plan)
    plan_block = _plan_block(plan)
    rmodel = model or _tmodel()

    candidates: list[dict] = []
    for r in rows:
        if not force and isinstance(r.get("alts"), list) and len(r["alts"]) >= 2:
            continue
        hint = _heuristic_ambiguous(r)
        if not hint:
            continue
        rr = dict(r)
        rr["_amb_hint"] = hint
        rr["text"] = r.get("zh") or ""
        candidates.append(rr)

    if limit is not None:
        candidates = candidates[: max(0, int(limit))]

    print(f"[alts] tối ưu · ứng viên={len(candidates)}/{len(rows)} · model={rmodel}")
    m.mark_running(STAGE)
    if is_local(rmodel):
        from engines.llm import free_gpu_vram
        free_gpu_vram("trước alts")

    pos_by_i = {r["i"]: idx for idx, r in enumerate(rows)}
    ctx_n = max(CTX_LINES, 6)
    found: dict[int, dict] = {}
    try:
        for k in range(0, len(candidates), batch):
            chunk = candidates[k : k + batch]
            pos = pos_by_i.get(chunk[0]["i"], 0)
            prev_ctx = [
                rows[j]
                for j in range(max(0, pos - ctx_n), pos)
                if rows[j].get("vi")
            ]
            last_pos = pos_by_i.get(chunk[-1]["i"], pos)
            next_ctx = [
                rows[j]
                for j in range(last_pos + 1, min(len(rows), last_pos + 1 + ctx_n))
                if rows[j].get("vi") or rows[j].get("zh")
            ]
            try:
                got = _alts_batch(
                    chunk, plan_block, prev_ctx, next_ctx, rmodel, plan
                )
            except LLMUnavailable:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[alts] lô lỗi: {e}")
                got = {}
            found.update(got)
            print(
                f"  ...alts {min(k + batch, len(candidates))}/{len(candidates)} "
                f"(+{len(got)} cần chọn)",
                flush=True,
            )
            st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
            st.update({
                "status": "running",
                "progress": round((k + len(chunk)) / max(1, len(candidates)) * 100, 1),
                "n_flagged": len(found),
                "updated_at": time.time(),
            })
            m.data["stages"][STAGE] = st
            js.persist(m)
    except Exception as e:  # noqa: BLE001
        m.mark_failed(STAGE, e)
        raise

    out_list: list[dict] = []
    for r in rows:
        info = found.get(r["i"])
        if not info:
            continue
        alts = list(info["alts"])
        cur = (r.get("vi") or "").strip()
        if cur and cur not in alts:
            alts = alts + [cur]
        r["alts"] = alts[:4]
        r["alt_reason"] = info.get("reason") or ""
        pick = int(info.get("pick") or 0)
        pick = max(0, min(pick, len(r["alts"]) - 1))
        r["alt_pick"] = pick
        if apply_pick:
            r["vi"] = r["alts"][pick]
        out_list.append({
            "i": r["i"],
            "start": r.get("start"),
            "end": r.get("end"),
            "zh": r.get("zh"),
            "vi": r.get("vi"),
            "alts": r["alts"],
            "pick": pick,
            "reason": r.get("alt_reason"),
        })

    alts_path = work_dir / "vi_alts.json"
    alts_path.write_text(
        json.dumps({
            "n": len(out_list),
            "model": rmodel,
            "goal": "phương án dịch tối ưu — ngữ cảnh + văn phong",
            "items": out_list,
            "note": "alts[pick]=đề xuất tối ưu. POST apply-alt hoặc --apply-pick.",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    script_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    result = m.mark_done(
        STAGE,
        alts_path=str(alts_path),
        n_flagged=len(out_list),
        n_candidates=len(candidates),
        model=rmodel or "default",
    )
    unload_local(rmodel)
    print(f"[ok] {len(out_list)} câu có phương án tối ưu → {alts_path}")
    return result


def analyze_pack_report(work_dir: str | Path) -> dict:
    """Checklist pack Context Studio có đủ để dịch chunk không."""
    work_dir = Path(work_dir)
    gpath = work_dir / "glossary.json"
    cpath = work_dir / "context_pack.json"
    if not gpath.exists():
        return {"ok": False, "missing": ["glossary.json"], "score": 0}
    g = json.loads(gpath.read_text(encoding="utf-8"))
    lock = g.get("style_lock") if isinstance(g.get("style_lock"), dict) else {}
    checks = {
        "genre": bool((g.get("genre") or "").strip()),
        "tone": bool((g.get("tone") or "").strip()),
        "mode": (lock.get("mode") or "") in ("modern", "mixed", "xianxia"),
        "characters>=3": len(g.get("characters") or []) >= 3,
        "pronouns>=5": len(g.get("pronouns") or []) >= 5,
        "terms>=5": len(g.get("terms") or []) >= 5,
        "skip_plan": False,
        "mixed_has_co_phong": True,
    }
    if cpath.exists():
        try:
            checks["skip_plan"] = bool(
                json.loads(cpath.read_text(encoding="utf-8")).get("skip_plan")
            )
        except Exception:  # noqa: BLE001
            checks["skip_plan"] = False
    if checks["mode"] and lock.get("mode") == "mixed":
        blob = f"{g.get('tone','')} {lock.get('pronoun_policy','')}".lower()
        checks["mixed_has_co_phong"] = any(
            x in blob for x in ("cổ phong", "hán việt", "han viet", "成语")
        )
    score = round(100 * sum(1 for v in checks.values() if v) / max(1, len(checks)))
    gaps = [k for k, v in checks.items() if not v]
    return {
        "ok": score >= 70 and not any(
            g in gaps for g in ("genre", "mode", "characters>=3")
        ),
        "score": score,
        "checks": checks,
        "gaps": gaps,
        "genre": g.get("genre"),
        "mode": lock.get("mode"),
        "n_characters": len(g.get("characters") or []),
        "n_pronouns": len(g.get("pronouns") or []),
        "n_terms": len(g.get("terms") or []),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Phương án dịch tối ưu (ngữ cảnh + văn phong)"
    )
    ap.add_argument("work_dir")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--apply-pick", action="store_true", help="Ghi vi = alts[pick]")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args(argv)
    if args.report_only:
        print(json.dumps(analyze_pack_report(args.work_dir), ensure_ascii=False, indent=2))
        return
    run_ambiguous_alts(
        args.work_dir,
        batch=args.batch,
        force=args.force,
        limit=args.limit,
        apply_pick=args.apply_pick,
    )


if __name__ == "__main__":
    main()
