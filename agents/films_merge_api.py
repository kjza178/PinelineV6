"""Gộp parts (split jobs) + scan job đã làm sẵn sàng merge — dùng từ sidecar :8802."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORK = Path(os.getenv("WORK_DIR") or (ROOT / "work")).resolve()


def _ok(data: dict | None = None, **extra: Any) -> tuple[int, dict]:
    return 200, {"ok": True, **(data or {}), **extra}


def _err(msg: str, code: int = 400, **extra: Any) -> tuple[int, dict]:
    return code, {"ok": False, "error": msg, **extra}


def _parent_id(job_id: str) -> str | None:
    jid = (job_id or "").strip()
    if len(jid) > 4 and jid[-4] == "_" and jid[-3] == "p" and jid[-2:].isdigit():
        return jid[:-4]
    return None


def _read_split_status(parent_dir: Path) -> str:
    mf = parent_dir / "manifest.json"
    if not mf.is_file():
        return ""
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        return str((data.get("split_info") or {}).get("status") or "")
    except Exception:
        return ""


def _part_ready(part_dir: Path) -> bool:
    out = part_dir / "output.mp4"
    if not (out.is_file() and out.stat().st_size > 100_000):
        return False
    mf = part_dir / "manifest.json"
    if not mf.is_file():
        return True  # có output đủ lớn → coi ready
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        mux = (data.get("stages") or {}).get("mux") or {}
        st = str(mux.get("status") or "").lower()
        if st and st not in ("done", "completed"):
            return False
    except Exception:
        pass
    return True


def scan_mergeable(limit: int = 200) -> list[dict]:
    """Parent có đủ part output, chưa merged → sẵn sàng gộp."""
    if not WORK.is_dir():
        return []
    # Tìm parent từ thư mục *_pNN
    parents: dict[str, list[str]] = {}
    for p in WORK.iterdir():
        if not p.is_dir():
            continue
        parent = _parent_id(p.name)
        if parent:
            parents.setdefault(parent, []).append(p.name)
    out: list[dict] = []
    for parent, parts in sorted(parents.items()):
        parts = sorted(parts)
        pdir = WORK / parent
        split_st = _read_split_status(pdir) if pdir.is_dir() else ""
        if split_st == "merged":
            continue
        ready = [sid for sid in parts if _part_ready(WORK / sid)]
        missing = [sid for sid in parts if sid not in ready]
        has_full = (pdir / "output_full.mp4").is_file() or (
            (pdir / "output.mp4").is_file() and (pdir / "output.mp4").stat().st_size > 50_000_000
            and split_st == "merged"
        )
        if has_full and split_st == "merged":
            continue
        if not ready:
            continue
        all_ready = len(missing) == 0 and len(ready) >= 2
        # cũng cho merge nếu chỉ còn 1 part? không — cần ≥2
        if len(parts) < 2:
            continue
        out.append({
            "job_id": parent,
            "n_parts": len(parts),
            "n_ready": len(ready),
            "parts": parts,
            "ready_parts": ready,
            "missing_parts": missing,
            "merge_ready": all_ready,
            "split_status": split_st or "pending",
            "work_exists": pdir.is_dir(),
        })
        if len(out) >= limit:
            break
    return out


def list_mergeable() -> tuple[int, dict]:
    items = scan_mergeable()
    return _ok({
        "items": items,
        "n": len(items),
        "n_ready": sum(1 for x in items if x.get("merge_ready")),
        "work_root": str(WORK),
    })


def merge_one(job_id: str) -> tuple[int, dict]:
    jid = (job_id or "").strip()
    if not jid:
        return _err("Thiếu job_id")
    # Nếu truyền part id → merge parent
    parent = _parent_id(jid) or jid
    try:
        from engines.split_parts import merge_job_parts
        res = merge_job_parts(parent, work_root=WORK)
        if isinstance(res, dict) and res.get("ok") is False:
            return 400, {"ok": False, **res}
        # film_db sync parent
        try:
            import core.film_db as film_db
            film_db.sync()
        except Exception:
            pass
        return _ok({"job_id": parent, **(res if isinstance(res, dict) else {"result": res})})
    except FileNotFoundError as e:
        return _err(str(e), 404)
    except ValueError as e:
        return _err(str(e), 400)
    except Exception as e:
        return _err(str(e), 500)


def merge_many(payload: dict) -> tuple[int, dict]:
    ids = payload.get("job_ids") or payload.get("ids") or []
    if isinstance(ids, str):
        ids = [ids]
    ids = [str(x).strip() for x in ids if str(x).strip()]
    if not ids:
        # merge tất cả sẵn sàng
        ids = [x["job_id"] for x in scan_mergeable() if x.get("merge_ready")]
    if not ids:
        return _err("Không có job nào để gộp")
    results = []
    ok_n = 0
    for jid in ids:
        code, body = merge_one(jid)
        results.append(body)
        if body.get("ok"):
            ok_n += 1
    return _ok({"merged": ok_n, "total": len(ids), "results": results})


def dispatch(method: str, path: str, body: dict | None = None) -> tuple[int, dict] | None:
    m = (method or "GET").upper()
    p = (path or "").rstrip("/") or "/"
    body = body or {}

    if m == "GET" and p == "/films/mergeable":
        return list_mergeable()
    if m == "POST" and p == "/films/merge-parts":
        return merge_many(body)
    if m == "POST" and p.startswith("/api/jobs/") and p.endswith("/merge-parts"):
        jid = p[len("/api/jobs/"):-len("/merge-parts")]
        return merge_one(jid)
    return None
