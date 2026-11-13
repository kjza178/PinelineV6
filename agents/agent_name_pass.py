"""
Rà TÊN NHÂN VẬT trong bản dịch theo bảng nhân vật (glossary.json).

1. Quét: câu nào zh có tên nhân vật (苏林…) mà vi không có tên Việt đã chốt (Tô Lâm) → nghi sai tên.
2. Gửi các câu nghi ngờ cho LLM sửa, CHỈ đổi tên riêng / danh xưng, giữ nguyên phần còn lại.
"""
from __future__ import annotations

from state import job_state as js  # V6 F1: mọi ghi manifest qua state
import json
import time
import unicodedata
from pathlib import Path

from engines.llm import chat, parse_json, is_local, unload_local
from core.manifest import Manifest

BATCH = 30


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower().replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _names(plan: dict) -> list[dict]:
    """Chỉ characters (+ terms type=lock). Terms thường = mềm, không ép name-pass."""
    out = []
    for c in plan.get("characters") or []:
        zh, vi = (c.get("zh") or "").strip(), (c.get("vi") or "").strip()
        # bỏ chú thích ngoặc khi khớp ZH trong câu
        zh_main = zh.split("（")[0].split("(")[0].strip()
        if len(zh_main) >= 2 and vi:
            out.append({
                "zh": zh_main,
                "vi": vi.split("/")[0].strip(),
                "ok": [_fold(vi)] + [_fold(a) for a in c.get("aliases") or [] if a],
            })
    for t in plan.get("terms") or []:
        if str((t or {}).get("type") or "").lower() != "lock":
            continue
        zh, vi = (t.get("zh") or "").strip(), (t.get("vi") or "").strip()
        zh_main = zh.split("（")[0].split("(")[0].strip()
        if len(zh_main) >= 2 and vi:
            out.append({"zh": zh_main, "vi": vi, "ok": [_fold(vi)]})
    return out


def find_issues(rows: list[dict], plan: dict) -> list[dict]:
    names = _names(plan)
    issues = []
    for r in rows:
        zh, vi = r.get("zh") or "", r.get("vi") or ""
        if not zh or not vi:
            continue
        fv = _fold(vi)
        miss = [n for n in names if n["zh"] in zh and not any(o and o in fv for o in n["ok"])]
        if miss:
            issues.append({"i": r["i"], "zh": zh, "vi": vi,
                           "names": [f"{n['zh']} = {n['vi']}" for n in miss]})
    return issues


def _fix_batch(chunk: list[dict], plan_block: str, model: str | None) -> dict[int, str]:
    sys = (
        "Bạn là biên tập viên lồng tiếng. Mỗi câu dưới đây có tên nhân vật / thuật ngữ trong bản gốc tiếng Trung "
        "nhưng bản dịch tiếng Việt viết sai / thiếu / dùng cách dịch khác. Sửa bản dịch để dùng ĐÚNG tên / thuật ngữ "
        "Việt theo cột 'names' và bảng thuật ngữ, thay cả bản dịch nghĩa đen của thuật ngữ đó. "
        "CHỈ đổi tên riêng / thuật ngữ / danh xưng liên quan, giữ nguyên nghĩa, "
        "độ dài và văn phong phần còn lại. Nếu câu đã gọi đúng người bằng đại từ hợp lý theo bảng xưng hô "
        "và thêm tên làm câu gượng thì bỏ qua câu đó. CHỈ trả JSON {\"fixes\":[{\"i\":<int>,\"vi\":\"...\"}]}."
    )
    user = (plan_block + "\n\nCÁC CÂU CẦN KIỂM TRA:\n"
            + json.dumps([{"i": c["i"], "zh": c["zh"], "vi": c["vi"], "names": c["names"]} for c in chunk],
                         ensure_ascii=False))
    out = chat([{"role": "system", "content": sys}, {"role": "user", "content": user}],
               model=model, temperature=0.1, json_mode=True, max_tokens=4096)
    try:
        data = parse_json(out)
        fixes = data.get("fixes", data if isinstance(data, list) else [])
        return {int(f["i"]): (f.get("vi") or "").strip() for f in fixes
                if isinstance(f, dict) and "i" in f and (f.get("vi") or "").strip()}
    except Exception:  # noqa: BLE001
        return {}


def run_name_pass(work_dir: str | Path, model: str | None = None) -> dict:
    from engines.translate import _plan_block, ensure_hard_terms
    try:
        from core.config import pass_llm_model
    except Exception:  # noqa: BLE001
        def pass_llm_model(kind: str = "name") -> str | None:  # type: ignore
            return None

    work_dir = Path(work_dir)
    sp, gp = work_dir / "script.json", work_dir / "glossary.json"
    if not sp.is_file():
        return {"ok": False, "error": "chưa có script.json"}
    rows = json.loads(sp.read_text(encoding="utf-8"))
    plan = json.loads(gp.read_text(encoding="utf-8")) if gp.is_file() else {}
    if not _names(plan):
        return {"ok": True, "n_names": 0, "n_issues": 0, "n_fixed": 0, "note": "bảng nhân vật trống"}
    issues = find_issues(rows, plan)
    rmodel = model or pass_llm_model("name")
    pos = {r["i"]: k for k, r in enumerate(rows)}
    plan_block = _plan_block(ensure_hard_terms(plan))
    m = Manifest(work_dir)
    fixed, sample = 0, []
    for k in range(0, len(issues), BATCH):
        chunk = issues[k: k + BATCH]
        try:
            fixes = _fix_batch(chunk, plan_block, rmodel)
        except Exception as e:  # noqa: BLE001
            print(f"[names] lô {k // BATCH} lỗi, bỏ qua: {e}", flush=True)
            fixes = {}
        for i, vi in fixes.items():
            if i in pos and vi and vi != rows[pos[i]].get("vi"):
                if len(sample) < 15:
                    sample.append({"i": i, "before": rows[pos[i]].get("vi"), "after": vi})
                rows[pos[i]]["vi"] = vi
                rows[pos[i]]["nm"] = 1
                fixed += 1
        sp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            st = m.data.setdefault("stages", {}).setdefault("translate", {})
            st["cur_text"] = f"Agent: rà tên nhân vật {min(k + BATCH, len(issues))}/{len(issues)} câu nghi sai"
            st["updated_at"] = time.time()
            js.persist(m)
        except Exception:  # noqa: BLE001
            pass
    if is_local(rmodel):
        unload_local(rmodel)
    print(f"[names] {len(issues)} câu nghi sai tên · sửa {fixed}", flush=True)
    return {"ok": True, "n_names": len(_names(plan)), "n_issues": len(issues), "n_fixed": fixed,
            "sample": sample}
