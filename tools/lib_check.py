"""
lib_check.py — Kiểm tra bản quyền / Block YouTube cho Phim trong Thư viện gốc (D:\\Lib) trước khi làm job.

Bao gồm:
- Tìm video nguồn (merged hoặc tập đầu).
- Cắt mẫu nhanh (sample 90s qua ffmpeg) để kiểm tra thử.
- Đưa sample vào hàng chờ yt_uploader để upload test lên kênh phụ.
- Quét và đối chiếu trạng thái từ queue_state.json của yt_uploader.
- Kiểm tra YouTube Content ID (Sạch, Bị chặn, Claim nhạc) và lưu vào films.db.
"""
from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import engines.check_block as check_block
import core.film_db as film_db

ROOT = Path(__file__).resolve().parents[1]
LIB_ROOT = Path(os.getenv("LIB_ROOT") or _machine.library_dir())
SAMPLE_DIR = Path(_machine.setting("LIB_SAMPLE_DIR", "") or r"Z:\PinelineV2\data\samples")
YT_DIR = _machine.yt_uploader_dir()
YT_QUEUE = YT_DIR / "queue_state.json"


def resolve_title_vi(name: str, title_vi: str = "") -> str:
    """Tìm hoặc dịch tên tiếng Việt cho tên mùa/phim để dễ nhận biết."""
    t_vi = (title_vi or "").strip()
    if t_vi and t_vi != name:
        return t_vi
    # 1. Tra trong bảng library_blocks
    try:
        b = film_db.get_library_block(name)
        if b and b.get("title_vi"):
            return str(b["title_vi"]).strip()
    except Exception:
        pass
    # 2. Tra trong bảng films
    try:
        with film_db._lock, film_db.connect() as con:
            row = con.execute(
                "SELECT title_vi FROM films WHERE (hg_name=? OR series=? OR title=? OR source_path LIKE ?) AND title_vi IS NOT NULL AND title_vi != '' ORDER BY created_at DESC LIMIT 1",
                (name, name, name, f"%{name}%"),
            ).fetchone()
            if row and row["title_vi"]:
                return str(row["title_vi"]).strip()
    except Exception:
        pass
    # 3. Tra trong cache dịch feeder_state.json
    try:
        feeder_state = ROOT / "work" / "logs" / "feeder_state.json"
        if feeder_state.is_file():
            sdata = json.loads(feeder_state.read_text(encoding="utf-8"))
            tr = sdata.get("tr") or {}
            if name in tr and tr[name]:
                return str(tr[name]).strip()
    except Exception:
        pass
    # 4. Tra trong title_mt (cache Google / dịch thô)
    try:
        import engines.title_mt as title_mt
        t = title_mt.translate_one(name)
        if t and t != name:
            return t.strip()
    except Exception:
        pass
    return t_vi or ""


def find_source_video(name: str) -> Path | None:
    """Tìm file video nguồn cho thư mục season trong D:\\Lib."""
    folder = LIB_ROOT / name
    if not folder.is_dir():
        return None
    # 1. Tìm file merged trước
    merged_candidates = list(folder.glob("*merged*.mp4")) + list(folder.glob("*gộp*.mp4"))
    if merged_candidates:
        return merged_candidates[0]
    # 2. Tìm bất kỳ file .mp4, .mkv, .ts nào
    files = sorted([f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in (".mp4", ".mkv", ".ts", ".mov")])
    if files:
        return files[0]
    return None


def cut_sample(name: str, start_sec: int = 60, dur_sec: int = 90, title_vi: str = "") -> dict[str, Any]:
    """Cắt một đoạn clip mẫu siêu nhanh (90s) từ video gốc để up test, kèm tên tiếng Việt dễ nhận biết."""
    src = find_source_video(name)
    if not src or not src.is_file():
        return {"ok": False, "error": f"Không tìm thấy video nguồn trong {LIB_ROOT / name}"}

    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    # Giải quyết tên tiếng Việt
    t_vi = resolve_title_vi(name, title_vi)

    # Làm sạch tên file an toàn cho hệ thống tệp Windows / Linux
    safe_name = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    safe_name = re.sub(r'\s+', "_", safe_name)

    safe_vi = re.sub(r'[\\/*?:"<>|]', "", t_vi).strip() if t_vi else ""
    safe_vi = re.sub(r'\s+', "_", safe_vi)

    # Đặt tên kèm tên tiếng Việt nếu có
    if safe_vi and safe_vi.lower() != safe_name.lower() and safe_vi.lower() not in safe_name.lower():
        sample_filename = f"[{safe_vi}]_{safe_name}_sample_{dur_sec}s.mp4"
    else:
        sample_filename = f"{safe_name}_sample_{dur_sec}s.mp4"

    out_path = SAMPLE_DIR / sample_filename

    # Cách 1: Copy stream (siêu nhanh, mất <1s)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", str(src),
        "-t", str(dur_sec),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_path)
    ]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if p.returncode != 0 or not out_path.is_file() or out_path.stat().st_size < 10000:
            # Fallback nếu copy bị lỗi keyframe: transcode ultrafast
            cmd_fb = [
                "ffmpeg", "-y",
                "-ss", str(start_sec),
                "-i", str(src),
                "-t", str(dur_sec),
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart",
                str(out_path)
            ]
            p_fb = subprocess.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            if p_fb.returncode != 0:
                return {"ok": False, "error": f"Lỗi ffmpeg: {p_fb.stderr.decode('utf-8', errors='ignore')[:300]}"}
    except Exception as e:
        return {"ok": False, "error": f"Lỗi khi cắt sample: {e}"}

    if not out_path.is_file() or out_path.stat().st_size == 0:
        return {"ok": False, "error": "File sample tạo ra rỗng"}

    size_mb = round(out_path.stat().st_size / (1024 * 1024), 2)
    film_db.upsert_library_block(
        name=name,
        title_vi=t_vi,
        video_path=str(src),
        test_sample_path=str(out_path)
    )
    return {
        "ok": True,
        "name": name,
        "title_vi": t_vi,
        "source_path": str(src),
        "sample_path": str(out_path),
        "sample_filename": sample_filename,
        "size_mb": size_mb,
        "duration_sec": dur_sec
    }


def add_to_yt_queue(name: str, sample_path: str = "", title: str = "", privacy: str = "unlisted", account: str = "default", title_vi: str = "") -> dict[str, Any]:
    """Đưa video sample (hoặc video gốc) vào hàng chờ upload của yt_uploader."""
    t_vi = resolve_title_vi(name, title_vi)
    if not sample_path:
        # Nếu chưa truyền, tìm sample đã có hoặc cắt mới
        cand_found = None
        if SAMPLE_DIR.is_dir():
            safe_name = re.sub(r'[\\/*?:"<>|]', "", name).strip().replace(" ", "_")
            matches = list(SAMPLE_DIR.glob(f"*{safe_name}*sample*.mp4"))
            if matches:
                matches.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                cand_found = matches[0]

        if cand_found and cand_found.is_file():
            sample_path = str(cand_found)
        else:
            cut_res = cut_sample(name, title_vi=t_vi)
            if not cut_res.get("ok"):
                return cut_res
            sample_path = cut_res["sample_path"]

    if not Path(sample_path).is_file():
        return {"ok": False, "error": f"Không tìm thấy file {sample_path}"}

    if not YT_QUEUE.is_file():
        return {"ok": False, "error": f"Không tìm thấy hàng chờ {YT_QUEUE}"}

    try:
        items = json.loads(YT_QUEUE.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"Không đọc được queue_state.json: {e}"}

    # Kiểm tra xem đường dẫn này đã có trong queue chưa
    norm_p = os.path.normcase(os.path.normpath(sample_path))
    for it in items:
        if os.path.normcase(os.path.normpath(it.get("path") or "")) == norm_p:
            return {"ok": True, "already_queued": True, "queue_len": len(items), "path": sample_path}

    display_title = f"[{t_vi}] {name}" if (t_vi and t_vi.strip().lower() != name.strip().lower()) else name
    item = {
        "path": str(sample_path),
        "title": title or f"[TEST_BLOCK] {display_title}",
        "description": f"Test block Content ID for {display_title}",
        "tags": "test",
        "privacy": privacy,
        "category_id": "22",
        "thumb": "",
        "account": account or "default",
        "ip": "",
        "status": "chờ",
        "progress": 0.0,
        "url": "",
        "publish_at": "",
        "playlist_id": ""
    }
    items.insert(0, item)  # Đưa lên đầu hàng chờ
    YT_QUEUE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    film_db.upsert_library_block(
        name=name,
        title_vi=t_vi,
        test_sample_path=sample_path,
        status="testing",
        reason="Đã đưa vào hàng chờ upload kênh test"
    )
    return {"ok": True, "queued": True, "queue_len": len(items), "path": sample_path}


def test_library_by_url(name: str, url_or_id: str) -> dict[str, Any]:
    """Kiểm tra URL hoặc Video ID YouTube test cho 1 bộ phim trong Thư viện."""
    vid = check_block.extract_video_id(url_or_id)
    if not vid:
        return {"ok": False, "error": f"Link YouTube không hợp lệ: {url_or_id}"}

    res = check_block.check_youtube_block(vid)
    status = res.get("status") or "unknown"
    reason = res.get("reason") or ""
    yt_url = f"https://youtu.be/{vid}"

    record = film_db.upsert_library_block(
        name=name,
        status=status,
        reason=reason,
        test_yt_url=yt_url,
        test_yt_id=vid
    )
    return {
        "ok": True,
        "name": name,
        "video_id": vid,
        "yt_url": yt_url,
        "status": status,
        "reason": reason,
        "record": record
    }


def scan_all_lib_from_yt_queue(check_live: bool = True) -> dict[str, Any]:
    """Quét toàn bộ các video thuộc D:\\Lib trong queue_state.json và cập nhật trạng thái."""
    if not YT_QUEUE.is_file():
        return {"ok": False, "error": "Không tìm thấy queue_state.json"}
    try:
        items = json.loads(YT_QUEUE.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"Lỗi đọc queue: {e}"}

    results = []
    clean_cnt = 0
    blocked_cnt = 0
    partially_blocked_cnt = 0
    claim_cnt = 0

    for item in items:
        p = item.get("path") or ""
        u = item.get("url") or ""
        if not p or not _machine.in_library(p) or not u:
            continue
        name = Path(p).parent.name
        vid = check_block.extract_video_id(u)
        
        status = "unknown"
        reason = ""
        if check_live:
            r = check_block.check_youtube_block(u)
            status = r.get("status") or "unknown"
            reason = r.get("reason") or ""
        else:
            ex = film_db.get_library_block(name)
            if ex and ex.get("status"):
                status = ex["status"]
                reason = ex.get("reason") or ""

        if status == "clean":
            clean_cnt += 1
        elif status == "blocked":
            blocked_cnt += 1
        elif status == "partially_blocked":
            partially_blocked_cnt += 1
        elif status == "claim":
            claim_cnt += 1

        rec = film_db.upsert_library_block(
            name=name,
            video_path=p,
            status=status,
            reason=reason,
            test_yt_url=u,
            test_yt_id=vid
        )
        results.append(rec)

    return {
        "ok": True,
        "total": len(results),
        "clean": clean_cnt,
        "blocked": blocked_cnt,
        "partially_blocked": partially_blocked_cnt,
        "claim": claim_cnt,
        "results": results
    }


def clear_samples(older_than_hours: float = 0) -> dict[str, Any]:
    """Dọn dẹp các file video mẫu 90s trong data/samples để giải phóng dung lượng ổ cứng."""
    import time
    if not SAMPLE_DIR.is_dir():
        return {"ok": True, "deleted": 0, "freed_mb": 0.0}

    now = time.time()
    deleted = 0
    freed_bytes = 0
    cutoff = now - (older_than_hours * 3600)

    for f in list(SAMPLE_DIR.iterdir()):
        if f.is_file() and f.suffix.lower() == ".mp4":
            try:
                mtime = f.stat().st_mtime
                if older_than_hours <= 0 or mtime < cutoff:
                    sz = f.stat().st_size
                    f.unlink(missing_ok=True)
                    deleted += 1
                    freed_bytes += sz
            except OSError:
                pass

    freed_mb = round(freed_bytes / (1024 * 1024), 2)
    return {"ok": True, "deleted": deleted, "freed_mb": freed_mb}


def list_yt_accounts() -> list[dict[str, Any]]:
    """Lấy danh sách tài khoản đã cấu hình trong Z:\\yt_uploader."""
    import importlib
    import sys
    sys.path.insert(0, str(YT_DIR))
    try:
        yt_accounts = importlib.import_module("yt_accounts")
        accs = yt_accounts.list_accounts() if hasattr(yt_accounts, "list_accounts") else []
        out = []
        for a in accs:
            out.append({
                "id": getattr(a, "id", "default"),
                "name": getattr(a, "name", "default"),
                "channel_title": getattr(a, "channel_title", ""),
                "channel_id": getattr(a, "channel_id", ""),
                "email": getattr(a, "email", "")
            })
        return out or [{"id": "default", "name": "default", "channel_title": "Sage Review", "email": "noemimarton1988@gmail.com"}]
    except Exception:
        return [{"id": "default", "name": "default", "channel_title": "Sage Review", "email": "noemimarton1988@gmail.com"}]


def batch_enqueue_untested_lib(names: list[str] | None = None, limit: int = 50, privacy: str = "unlisted", account: str = "default") -> dict[str, Any]:
    """Cắt mẫu 90s và đưa hàng loạt các mùa chưa test trong D:\\Lib vào hàng chờ yt_uploader."""
    if not names:
        tested = film_db.list_library_blocks()
        candidates = []
        if LIB_ROOT.is_dir():
            for f in sorted(LIB_ROOT.iterdir(), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
                if f.is_dir():
                    st = (tested.get(f.name) or {}).get("status")
                    if not st or st == "unknown":
                        candidates.append(f.name)
        names = candidates[:limit]

    if not names:
        return {"ok": True, "enqueued": 0, "names": [], "message": "Không còn phim nào chưa test trong thư viện."}

    enqueued = []
    errors = []
    for nm in names:
        r = add_to_yt_queue(name=nm, privacy=privacy, account=account)
        if r.get("ok"):
            enqueued.append(nm)
        else:
            errors.append({"name": nm, "error": r.get("error")})

    return {
        "ok": True,
        "enqueued": len(enqueued),
        "names": enqueued,
        "errors": errors,
        "total_requested": len(names)
    }

