"""
Bảng nhân vật / thuật ngữ / xưng hô dùng chung cho CẢ BỘ (mọi mùa).

- apply(work_dir, plan): khi dịch 1 mùa → đè các mục đã chốt của bộ lên plan (tên, tên phụ,
  xưng hô, thuật ngữ); nhân vật mới chỉ có ở mùa này giữ nguyên.
- save_from(work_dir): khi bản dịch 1 mùa được duyệt (qua rà soát, sắp lồng tiếng) → gộp bảng
  của mùa đó vào kho bộ, để mùa sau kế thừa.

Kho: data/series_glossary/<tên bộ>.json
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "series_glossary"


def _core_of(title: str) -> str:
    try:
        from agents.hongguo_bridge import franchise_core
        return franchise_core(title)
    except Exception:  # noqa: BLE001
        t = re.sub(r"\s*[（(][^）)]*[)）]\s*$", "", title or "")
        t = re.sub(r"(?:第)?[0-9一二三四五六七八九十百零两]+季\s*$", "", t)
        return t.strip(" ，,·-—:：")


def series_key(work_dir: Path | str) -> str | None:
    mp = Path(work_dir) / "manifest.json"
    try:
        req = (json.loads(mp.read_text(encoding="utf-8")).get("request") or {})
    except Exception:  # noqa: BLE001
        return None
    title = req.get("group_title") or req.get("title") or ""
    core = _core_of(title).strip()
    return core or None


def _path(core: str) -> Path:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", core).strip() or "_"
    return STORE / f"{safe}.json"


def load(core: str | None) -> dict | None:
    if not core:
        return None
    p = _path(core)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    # Thử nạp từ SQLite films.db (nếu file JSON chưa có)
    try:
        import core.film_db as film_db
        with film_db.connect() as con:
            cur = con.execute("SELECT glossary_json FROM series_glossary WHERE core = ?", (core,))
            row = cur.fetchone()
            if row and row[0]:
                data = json.loads(row[0])
                STORE.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return data
    except Exception:  # noqa: BLE001
        pass
    return None


def _valid_char(c: dict) -> bool:
    return isinstance(c, dict) and bool((c.get("zh") or "").strip()) and bool((c.get("vi") or "").strip())


def _merge_list(base: list, top: list, key) -> list:
    """top đè base theo key; giữ thứ tự: mục của top trước, mục chỉ có ở base sau."""
    out, seen = [], set()
    for x in top or []:
        k = key(x)
        if k and k not in seen:
            out.append(x)
            seen.add(k)
    for x in base or []:
        k = key(x)
        if k and k not in seen:
            out.append(x)
            seen.add(k)
    return out


def _zh(x) -> str:
    return (x.get("zh") or "").strip() if isinstance(x, dict) else ""


def _pron(x) -> str:
    return f"{x.get('who')}→{x.get('to')}" if isinstance(x, dict) and x.get("who") else ""


def _hygiene_chars_terms(chars: list, terms: list) -> tuple[list, list]:
    """Series hygiene (3): audit + bỏ mục hv_suspect không sửa được."""
    try:
        from engines.hv_auditor import filter_series_entries, audit_and_fix_plan
        # Fix in a temp plan then filter suspects
        tmp = {"characters": [dict(c) for c in (chars or []) if isinstance(c, dict)],
               "terms": [dict(t) for t in (terms or []) if isinstance(t, dict)]}
        audit_and_fix_plan(tmp, fix=True)
        chars = filter_series_entries(tmp.get("characters") or [], drop_suspect=True)
        terms = filter_series_entries(tmp.get("terms") or [], drop_suspect=True)
    except Exception as e:  # noqa: BLE001
        print(f"[series_glossary] hv hygiene skip: {e}", flush=True)
    return chars, terms


def apply(work_dir: Path | str, plan: dict) -> dict:
    core = series_key(work_dir)
    sg = load(core)
    if not sg:
        return plan
    plan = dict(plan or {})
    sg_chars = [c for c in sg.get("characters") or [] if _valid_char(c)]
    sg_terms = list(sg.get("terms") or [])
    sg_chars, sg_terms = _hygiene_chars_terms(sg_chars, sg_terms)
    plan["characters"] = _merge_list(plan.get("characters") or [], sg_chars, _zh)
    plan["terms"] = _merge_list(plan.get("terms") or [], sg_terms, _zh)
    plan["pronouns"] = _merge_list(plan.get("pronouns") or [], sg.get("pronouns") or [], _pron)
    for k in ("genre", "tone", "content_type", "pronoun_notes"):
        if sg.get(k) and not plan.get(k):
            plan[k] = sg[k]
    meta = dict(plan.get("_meta") or {})
    meta["series_glossary"] = {"core": core, "seasons": sg.get("seasons") or [],
                               "n_characters": len(sg_chars),
                               "applied_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    plan["_meta"] = meta
    print(f"[plan] kế thừa bảng bộ «{core}»: {len(sg_chars)} nhân vật, "
          f"{len(sg_terms)} thuật ngữ (từ {', '.join(sg.get('seasons') or []) or '?'})", flush=True)
    return plan


def save_from(work_dir: Path | str, reason: str = "duyệt trước TTS") -> dict:
    work_dir = Path(work_dir)
    core = series_key(work_dir)
    gp = work_dir / "glossary.json"
    if not core or not gp.is_file():
        return {"ok": False, "error": "không xác định được bộ hoặc chưa có glossary.json"}
    try:
        g = json.loads(gp.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"glossary.json lỗi: {e}"}
    sg = load(core) or {"core": core, "characters": [], "terms": [], "pronouns": [], "seasons": []}
    # hygiene trước khi ghi kho bộ (không lan âm HV sai)
    new_chars = [c for c in g.get("characters") or [] if _valid_char(c)]
    new_terms = list(g.get("terms") or [])
    new_chars, new_terms = _hygiene_chars_terms(new_chars, new_terms)
    # Characters + pronouns = cứng series; terms chỉ giữ type=lock (glossary gầy)
    sg["characters"] = _merge_list(sg.get("characters") or [], new_chars, _zh)
    lock_terms = [
        t for t in new_terms
        if isinstance(t, dict) and str(t.get("type") or "").lower() == "lock"
    ]
    old_lock = [
        t for t in (sg.get("terms") or [])
        if isinstance(t, dict) and str(t.get("type") or "").lower() == "lock"
    ]
    sg["terms"] = _merge_list(old_lock, lock_terms, _zh)
    # hygiene cả kho sau merge
    sg["characters"], sg["terms"] = _hygiene_chars_terms(
        list(sg.get("characters") or []), list(sg.get("terms") or [])
    )
    sg["pronouns"] = _merge_list(sg.get("pronouns") or [], g.get("pronouns") or [], _pron)
    for k in ("genre", "tone", "content_type", "pronoun_notes"):
        if g.get(k):
            sg[k] = g[k]
    season = work_dir.name
    try:
        req = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8")).get("request") or {}
        season = req.get("title") or season
    except Exception:  # noqa: BLE001
        pass
    seasons = [s for s in sg.get("seasons") or [] if s != season] + [season]
    sg.update({"core": core, "seasons": seasons[-30:], "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "updated_from": work_dir.name, "reason": reason})
    STORE.mkdir(parents=True, exist_ok=True)
    payload_str = json.dumps(sg, ensure_ascii=False, indent=2)
    _path(core).write_text(payload_str, encoding="utf-8")
    # Đồng bộ 2 chiều vào SQLite films.db
    try:
        import core.film_db as film_db
        with film_db.connect() as con:
            con.execute(
                "INSERT INTO series_glossary (core, glossary_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(core) DO UPDATE SET glossary_json=excluded.glossary_json, updated_at=excluded.updated_at",
                (core, payload_str, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            con.commit()
    except Exception as e_db:
        pass
    return {"ok": True, "core": core, "n_characters": len(sg["characters"]), "n_terms": len(sg["terms"])}
