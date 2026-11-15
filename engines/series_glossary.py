"""
Bảng nhân vật / thuật ngữ / xưng hô dùng chung cho CẢ BỘ (mọi mùa).

- apply(work_dir, plan): khi dịch 1 mùa → đè các mục đã chốt của bộ lên plan (tên, tên phụ,
  xưng hô, thuật ngữ); nhân vật mới chỉ có ở mùa này giữ nguyên.
- save_from(work_dir): khi bản dịch 1 mùa được duyệt (qua rà soát, sắp lồng tiếng) → gộp bảng
  của mùa đó vào kho bộ, để mùa sau kế thừa.

Kho: data/series_glossary/<tên bộ>.json

2026-11-15 — KHÔNG ÂM THẦM GHI ĐÈ quyết định đã chốt (ý tưởng từ llm-subs):
  Trước đây tập mới luôn thắng: plan thô của tập 5 dịch 林凡 = «Lâm Phiền» là cả bộ đổi theo, mọi tập sau sai.
  Nay các giá trị RỜI RẠC đã có trong kho — tên VI / giới tính nhân vật, tự xưng / cách gọi của cặp xưng hô, chữ VI
  của thuật ngữ khoá — tập mới khác thì GIỮ bản kho và ghi xung đột vào <tên bộ>.conflicts.json; người chọn trên
  desktop (giữ / dùng bản mới / bỏ qua) qua resolve_conflict. Mục MỚI vẫn thêm bình thường; ô kho đang trống thì
  điền. Mô tả tự do (vai trò, ghi chú, thể loại…) không phải quyết định rời rạc → không tạo xung đột.
  Bảng của tập do NGƯỜI duyệt (Context Studio: context_pack.json skip_plan) là quyết định của người → được ghi đè.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "series_glossary"
_LOCK = threading.RLock()          # save_from / resolve_conflict trong cùng tiến trình không giẫm nhau

# Trường RỜI RẠC được bảo vệ (khác nhau = xung đột) theo loại mục.
GUARDED = {"character": ("vi", "gender"), "pronoun": ("self", "call"), "term": ("vi",)}
KIND_VI = {"character": "nhân vật", "pronoun": "xưng hô", "term": "thuật ngữ"}
FIELD_VI = {"vi": "tên / chữ VI", "gender": "giới tính", "self": "tự xưng", "call": "cách gọi"}


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


def _conflicts_path(core: str) -> Path:
    p = _path(core)
    return p.with_name(p.stem + ".conflicts.json")


def load(core: str | None) -> dict | None:
    if not core:
        return None
    p = _path(core)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[series_glossary] đọc {p.name} lỗi ({e}) — thử films.db", flush=True)
    # Thử nạp từ SQLite films.db (nếu file JSON chưa có)
    try:
        import core.film_db as film_db
        with film_db.connect() as con:
            cur = con.execute("SELECT glossary_json FROM series_glossary WHERE core = ?", (core,))
            row = cur.fetchone()
            if row and row[0]:
                data = json.loads(row[0])
                from core.fileio import atomic_write_json
                STORE.mkdir(parents=True, exist_ok=True)
                atomic_write_json(p, data, fsync=False)
                return data
    except Exception as e:  # noqa: BLE001
        print(f"[series_glossary] nạp «{core}» từ films.db lỗi: {e}", flush=True)
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


def _norm(v) -> str:
    if isinstance(v, (list, tuple)):
        v = " / ".join(str(x) for x in v)
    v = unicodedata.normalize("NFC", str(v or "")).strip().lower()
    return re.sub(r"\s+", " ", v)


def _empty(v) -> bool:
    return not _norm(v)


def _human_reviewed(work_dir: Path) -> bool:
    """Bảng của tập do người duyệt (Context Studio ghi context_pack.json skip_plan) → quyết định của người."""
    try:
        cp = json.loads((work_dir / "context_pack.json").read_text(encoding="utf-8"))
        return bool(isinstance(cp, dict) and cp.get("skip_plan"))
    except FileNotFoundError:
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[series_glossary] context_pack.json lỗi: {e}", flush=True)
        return False


def _cid(kind: str, key: str, field: str, suggested) -> str:
    return hashlib.sha1(f"{kind}|{_norm(key)}|{field}|{_norm(suggested)}".encode("utf-8")).hexdigest()[:12]


def load_conflicts(core: str) -> dict:
    p = _conflicts_path(core)
    if not p.is_file():
        return {"core": core, "items": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return d
        print(f"[series_glossary] {p.name} sai cấu trúc — coi như rỗng", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[series_glossary] đọc {p.name} lỗi: {e}", flush=True)
    return {"core": core, "items": []}


def _merge_guarded(store: list, incoming: list, key, kind: str, *, trusted: bool, episode: str,
                   found: list[dict]) -> list:
    """Gộp `incoming` (tập mới) vào `store` (kho bộ). Mục mới → thêm. Mục đã có: trường rời rạc khác nhau →
    giữ kho + ghi vào `found` (trusted → ghi đè). Trường trống ở kho → điền. Bí danh → hợp. Thứ tự kho giữ nguyên."""
    out = [dict(x) for x in (store or []) if isinstance(x, dict)]
    by_key = {key(x): x for x in out if key(x)}
    for x in incoming or []:
        k = key(x)
        if not k:
            continue
        cur = by_key.get(k)
        if cur is None:
            nx = dict(x)
            out.append(nx)
            by_key[k] = nx
            continue
        for f in GUARDED[kind]:
            nv, ov = x.get(f), cur.get(f)
            if _empty(nv) or _norm(nv) == _norm(ov):
                continue
            if _empty(ov) or trusted:
                cur[f] = nv
                if trusted and not _empty(ov):
                    found.append({"kind": kind, "key": k, "field": f, "stored": ov, "suggested": nv,
                                  "episode": episode, "auto": "người duyệt (Context Studio) → dùng bản mới"})
                continue
            found.append({"kind": kind, "key": k, "field": f, "stored": ov, "suggested": nv, "episode": episode})
        if kind == "character":
            al = list(dict.fromkeys([*(cur.get("aliases") or []), *(x.get("aliases") or [])]))
            if al:
                cur["aliases"] = al
        for f in ("role", "note"):
            if _empty(cur.get(f)) and not _empty(x.get(f)):
                cur[f] = x[f]
    return out


def _record_conflicts(core: str, found: list[dict]) -> int:
    """Ghi xung đột mới vào <bộ>.conflicts.json (gộp trùng: cùng mục / trường / bản đề xuất). Trả số mục MỚI."""
    if not found:
        return 0
    from core.fileio import atomic_write_json
    d = load_conflicts(core)
    by_id = {it.get("id"): it for it in d["items"]}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    n_new = 0
    for c in found:
        cid = _cid(c["kind"], c["key"], c["field"], c["suggested"])
        it = by_id.get(cid)
        if it is None:
            it = {"id": cid, "kind": c["kind"], "key": c["key"], "field": c["field"], "stored": c["stored"],
                  "suggested": c["suggested"], "episodes": [], "count": 0, "first_at": now,
                  "status": "auto" if c.get("auto") else "open"}
            if c.get("auto"):
                it["resolved_at"], it["note"] = now, c["auto"]
            d["items"].append(it)
            by_id[cid] = it
            n_new += 0 if c.get("auto") else 1
        it["stored"] = c["stored"]
        it["count"] = int(it.get("count") or 0) + 1
        it["last_at"] = now
        if c["episode"] and c["episode"] not in it["episodes"]:
            it["episodes"] = (it["episodes"] + [c["episode"]])[-20:]
    d["core"] = core
    atomic_write_json(_conflicts_path(core), d, fsync=False)
    if n_new:
        try:
            from events.bus import bus
            bus.publish("series.conflict", {"core": core, "n_new": n_new,
                                            "n_open": sum(1 for it in d["items"] if it.get("status") == "open")})
        except Exception as e:  # noqa: BLE001
            print(f"[series_glossary] báo sự kiện xung đột lỗi: {e}", flush=True)
    return n_new


def _write_store(core: str, sg: dict) -> None:
    from core.fileio import atomic_write_json
    STORE.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_path(core), sg, fsync=False)
    # Đồng bộ films.db (best-effort, có log)
    try:
        import core.film_db as film_db
        with film_db.connect() as con:
            con.execute(
                "INSERT INTO series_glossary (core, glossary_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(core) DO UPDATE SET glossary_json=excluded.glossary_json, updated_at=excluded.updated_at",
                (core, json.dumps(sg, ensure_ascii=False), time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            con.commit()
    except Exception as e_db:  # noqa: BLE001
        print(f"[series_glossary] đồng bộ films.db «{core}» lỗi: {e_db}", flush=True)


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
    trusted = _human_reviewed(work_dir)
    season = work_dir.name
    try:
        req = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8")).get("request") or {}
        season = req.get("title") or season
    except Exception as e:  # noqa: BLE001
        print(f"[series_glossary] đọc manifest {work_dir.name}: {e}", flush=True)
    with _LOCK:
        sg = load(core) or {"core": core, "characters": [], "terms": [], "pronouns": [], "seasons": []}
        # hygiene trước khi ghi kho bộ (không lan âm HV sai)
        new_chars = [c for c in g.get("characters") or [] if _valid_char(c)]
        new_terms = list(g.get("terms") or [])
        new_chars, new_terms = _hygiene_chars_terms(new_chars, new_terms)
        found: list[dict] = []
        sg["characters"] = _merge_guarded(sg.get("characters") or [], new_chars, _zh, "character",
                                          trusted=trusted, episode=season, found=found)
        # Characters + pronouns = cứng series; terms chỉ giữ type=lock (glossary gầy)
        lock_terms = [t for t in new_terms if isinstance(t, dict) and str(t.get("type") or "").lower() == "lock"]
        old_lock = [t for t in (sg.get("terms") or [])
                    if isinstance(t, dict) and str(t.get("type") or "").lower() == "lock"]
        sg["terms"] = _merge_guarded(old_lock, lock_terms, _zh, "term", trusted=trusted, episode=season, found=found)
        sg["characters"], sg["terms"] = _hygiene_chars_terms(
            list(sg.get("characters") or []), list(sg.get("terms") or [])
        )
        sg["pronouns"] = _merge_guarded(sg.get("pronouns") or [], g.get("pronouns") or [], _pron, "pronoun",
                                        trusted=trusted, episode=season, found=found)
        # mô tả tự do: tập mới cập nhật (không phải quyết định rời rạc)
        for k in ("genre", "tone", "content_type", "pronoun_notes"):
            if g.get(k):
                sg[k] = g[k]
        seasons = [s for s in sg.get("seasons") or [] if s != season] + [season]
        sg.update({"core": core, "seasons": seasons[-30:], "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "updated_from": work_dir.name, "reason": reason})
        _write_store(core, sg)
        n_new = _record_conflicts(core, found)
    if n_new:
        print(f"[series_glossary] «{core}»: {n_new} xung đột mới từ {season} — giữ bản đã chốt, chờ người chọn",
              flush=True)
    return {"ok": True, "core": core, "n_characters": len(sg["characters"]), "n_terms": len(sg["terms"]),
            "n_conflicts_new": n_new, "trusted": trusted}


def list_conflicts(status: str | None = "open") -> list[dict]:
    """Mọi xung đột (mọi bộ) — status=None: tất cả. Mỗi mục kèm core, nhãn tiếng Việt."""
    out = []
    if not STORE.is_dir():
        return out
    for p in sorted(STORE.glob("*.conflicts.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[series_glossary] đọc {p.name} lỗi: {e}", flush=True)
            continue
        core = d.get("core") or p.name[: -len(".conflicts.json")]
        for it in d.get("items") or []:
            if status and it.get("status") != status:
                continue
            out.append({**it, "core": core, "kind_vi": KIND_VI.get(it.get("kind"), it.get("kind")),
                        "field_vi": FIELD_VI.get(it.get("field"), it.get("field"))})
    out.sort(key=lambda x: x.get("last_at") or "", reverse=True)
    return out


def resolve_conflict(core: str, cid: str, action: str) -> dict:
    """action: keep (giữ bản kho) | use_new (ghi bản đề xuất vào kho) | skip (bỏ qua, không đổi gì)."""
    if action not in ("keep", "use_new", "skip"):
        raise ValueError("action phải là keep, use_new hoặc skip")
    with _LOCK:
        d = load_conflicts(core)
        it = next((x for x in d["items"] if x.get("id") == cid), None)
        if it is None:
            raise KeyError(f"không có xung đột {cid} của «{core}»")
        if it.get("status") != "open":
            raise ValueError(f"xung đột này đã xử lý ({it.get('status')})")
        if action == "use_new":
            sg = load(core)
            if not sg:
                raise KeyError(f"không có bảng bộ «{core}»")
            lst = {"character": "characters", "pronoun": "pronouns", "term": "terms"}[it["kind"]]
            keyf = _pron if it["kind"] == "pronoun" else _zh
            hit = next((x for x in sg.get(lst) or [] if isinstance(x, dict) and keyf(x) == it["key"]), None)
            if hit is None:
                raise KeyError(f"mục «{it['key']}» không còn trong bảng bộ")
            hit[it["field"]] = it["suggested"]
            sg["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            sg["reason"] = f"người chọn bản mới: {it['key']} {it['field']}"
            _write_store(core, sg)
            # đề xuất khác cho cùng mục / trường giờ so với giá trị mới → đóng lại (giữ)
            for x in d["items"]:
                if x is not it and x.get("status") == "open" and x.get("kind") == it["kind"] \
                        and x.get("key") == it["key"] and x.get("field") == it["field"]:
                    x["status"], x["resolved_at"] = "kept", time.strftime("%Y-%m-%d %H:%M:%S")
                    x["note"] = "đóng theo lựa chọn khác cho cùng mục"
        it["status"] = {"keep": "kept", "use_new": "used_new", "skip": "skipped"}[action]
        it["resolved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        from core.fileio import atomic_write_json
        atomic_write_json(_conflicts_path(core), d, fsync=False)
    return {"ok": True, "id": cid, "status": it["status"]}
