"""Thêm job từ link — xem trước rồi mới tạo (hộp «Thêm job» của /v6/).

2026-11-16: trước đây dán link là tạo job ngay (yt-dlp bóc playlist, không xem được là mấy tập, tập nào). Giờ:
  inspect(url)  → loại link, tên, danh sách tập (Bilibili: API gốc — multi-P / hợp tập / series), trạng thái cookie,
                  tập nào đã có job;
  create(...)   → tạo job cho các tập ĐÃ CHỌN, gom nhóm khi > 1 (như POST /api/jobs), bỏ qua tập đã có job.
"""
from __future__ import annotations

import hashlib


def _server():
    import server
    return server


def inspect(url: str) -> dict:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")) and not url.upper().startswith("BV"):
        raise ValueError("Dán link video (http…) — file trên máy dùng ô đường dẫn")
    from engines.download import detect_platform, job_id_from_url
    from engines.playlist import expand_meta
    if url.upper().startswith("BV"):
        url = f"https://www.bilibili.com/video/{url}"
    platform = detect_platform(url)
    meta = expand_meta(url)
    parts = meta.get("parts") or [{"url": url, "title": meta.get("title")}]
    srv = _server()
    for p in parts:
        jid = job_id_from_url(p["url"])
        p["job_id"] = jid
        p["exists"] = (srv.WORK_ROOT / jid / "manifest.json").is_file()
    out = {"url": url, "platform": platform, "kind": meta.get("kind") or ("playlist" if len(parts) > 1 else "video"),
           "title": meta.get("title") or (parts[0].get("title") if parts else ""), "pic": meta.get("pic", ""),
           "parts": parts, "total_sec": sum(int(p.get("duration") or 0) for p in parts)}
    if platform == "bilibili":
        from engines.bili import cookies as bck
        out["cookie"] = bck.status(check=True)
        from engines.download import _bili_native_on
        out["downloader"] = "Bilibili gốc (API DASH, đa luồng)" if _bili_native_on() else "yt-dlp"
    else:
        out["downloader"] = "yt-dlp"
    return out


def create(url: str, parts: list[dict], group_title: str = "", target: str = "Tiếng Việt") -> dict:
    """parts: [{url, title}] đã chọn. Tập đã có job → bỏ qua (không ghi đè job đang làm)."""
    from engines.download import job_id_from_url
    srv = _server()
    parts = [p for p in parts or [] if str(p.get("url") or "").startswith("http")]
    if not parts:
        raise ValueError("chưa chọn tập nào")
    group = None
    if len(parts) > 1:
        gid = hashlib.sha1((url or parts[0]["url"]).encode("utf-8")).hexdigest()[:10]
        group = {"group_id": gid, "group_title": group_title or url, "group_total": len(parts)}
    made, skipped = [], []
    for i, p in enumerate(parts, 1):
        jid = job_id_from_url(p["url"])
        if (srv.WORK_ROOT / jid / "manifest.json").is_file():
            skipped.append(jid)
            continue
        g = {**group, "group_index": i, "part_title": p.get("title")} if group else None
        made.append(srv._create(p["url"], target, None, g))
    return {"ok": bool(made), "job_ids": made, "skipped": skipped, "count": len(made)}


VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".flv", ".ts", ".avi", ".m4v")


def pick_files(initial: str = "") -> list[str]:
    """Chạy tools/pick_files_dialog.py (tiến trình riêng — tkinter không chạy trong luồng server). Chỉ trả file video có thật."""
    import subprocess
    import sys
    from pathlib import Path
    from core import machine
    script = Path(__file__).resolve().parents[1] / "tools" / "pick_files_dialog.py"
    init = initial.strip().strip('"') or str(machine.library_dir())
    try:
        r = subprocess.run([sys.executable, str(script), init], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("hết thời gian chờ chọn file") from e
    except OSError as e:
        raise RuntimeError(f"không mở được hộp chọn file: {e}") from e
    if r.returncode != 0 and not (r.stdout or "").strip():
        raise RuntimeError((r.stderr or "hộp chọn file lỗi").strip()[-300:])
    out = []
    for ln in (r.stdout or "").splitlines():
        p = Path(ln.strip().strip('"'))
        if ln.strip() and p.is_file() and p.suffix.lower() in VIDEO_EXT and str(p) not in out:
            out.append(str(p))
    return out
