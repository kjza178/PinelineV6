"""Tạo cấu hình kênh test YouTube trên máy MỚI (không có cây V2 để chép data/test_channel).

2026-11-16: tools/test_channel đọc data/test_channel/projects.json ([{id, name, client_id, client_secret}]) nhưng không có
code nào tạo file đó (trước đây viết tay / chép theo cây). Ở đây: thêm tài khoản (project) từ file OAuth client JSON tải ở
Google Cloud Console (dạng {"web": {...}} hoặc {"installed": {...}}), hoặc từ client_secret*.json có sẵn trong thư mục
yt_uploader. KHÔNG trả client_secret ra ngoài.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _files():
    import tools.test_channel as tc
    return tc.DATA_DIR, tc.PROJECTS_FILE


def _load() -> list[dict]:
    _, pf = _files()
    if not pf.is_file():
        return []
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{pf} hỏng: {e}") from e
    return data if isinstance(data, list) else []


def parse_client(raw) -> tuple[str, str]:
    """Nội dung file OAuth client (str / dict) → (client_id, client_secret). Sai → ValueError."""
    try:
        d = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except ValueError as e:
        raise ValueError("không phải file JSON hợp lệ") from e
    inner = d.get("web") or d.get("installed") or d
    cid, sec = str(inner.get("client_id") or "").strip(), str(inner.get("client_secret") or "").strip()
    if not cid.endswith(".apps.googleusercontent.com") or not sec:
        raise ValueError("không thấy client_id / client_secret — tải đúng file «OAuth client» (JSON) ở Google Cloud Console")
    return cid, sec


def add_project(name: str, raw) -> dict:
    """Thêm (hoặc cập nhật nếu trùng client_id) một tài khoản test. Trả {id, name, client_id} (không có secret)."""
    cid, sec = parse_client(raw)
    data_dir, pf = _files()
    projs = _load()
    for p in projs:
        if p.get("client_id") == cid:
            p["client_secret"] = sec
            if name:
                p["name"] = name.strip()
            break
    else:
        n = 1
        ids = {p.get("id") for p in projs}
        while f"proj_{n}" in ids:
            n += 1
        p = {"id": f"proj_{n}", "name": (name or "").strip() or f"Project {n}", "client_id": cid, "client_secret": sec}
        projs.append(p)
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = pf.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(projs, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(pf)
    print(f"[test_channel_setup] đã lưu tài khoản test {p['id']} ({p['name']})", flush=True)
    return {"id": p["id"], "name": p["name"], "client_id": cid}


def remove_project(pid: str) -> bool:
    data_dir, pf = _files()
    projs = _load()
    keep = [p for p in projs if p.get("id") != pid]
    if len(keep) == len(projs):
        return False
    tmp = pf.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(pf)
    tok = data_dir / "tokens" / f"{pid}.json"
    if tok.is_file():
        tok.unlink()
    return True


def candidates() -> list[dict]:
    """File client_secret*.json trong thư mục yt_uploader (tối đa 3 cấp) — dùng lại OAuth client của tool đăng YouTube."""
    from core import machine
    root = machine.yt_uploader_dir()
    out = []
    if not root.is_dir():
        return out
    have = {p.get("client_id") for p in _load()}
    for f in sorted(root.glob("**/client_secret*.json")):
        if len(f.relative_to(root).parts) > 4:
            continue
        try:
            cid, _ = parse_client(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"path": str(f), "label": str(f.relative_to(root)), "client_id_tail": cid.split("-")[0][-6:],
                    "added": cid in have})
    return out


def add_from_candidate(path: str, name: str = "") -> dict:
    """Chỉ nhận đường dẫn nằm trong danh sách candidates() (không đọc file tuỳ ý)."""
    allowed = {c["path"] for c in candidates()}
    if path not in allowed:
        raise ValueError("file không nằm trong thư mục yt_uploader")
    p = Path(path)
    label = name or re.sub(r"^client_secret[_-]?", "", p.stem) or p.parent.name
    return add_project(label, p.read_text(encoding="utf-8"))
