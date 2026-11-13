"""
check_block.py — Module kiểm tra trạng thái Bản quyền / Block YouTube (Cơ chế 1: Không cần đăng nhập).
Kiểm tra qua YouTube oEmbed và YouTube Watch Page (ytInitialPlayerResponse).
Phát hiện: Clean (Sạch), Blocked (Chặn toàn cầu/vùng), Claim (Bản quyền âm nhạc), Private (Riêng tư).
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_video_id(url_or_id: str) -> str:
    """Trích xuất Video ID (11 ký tự) từ link YouTube bất kỳ hoặc ID thô."""
    s = (url_or_id or "").strip()
    if not s:
        return ""
    # Nếu truyền sẵn ID 11 ký tự
    if re.fullmatch(r"[\w-]{11}", s):
        return s
    # Parse URL
    try:
        p = urllib.parse.urlparse(s if "//" in s else "//" + s)
        if "youtu.be" in p.netloc:
            vid = p.path.lstrip("/").split("/")[0]
            if re.fullmatch(r"[\w-]{11}", vid):
                return vid
        qs = urllib.parse.parse_qs(p.query)
        if "v" in qs and qs["v"]:
            vid = qs["v"][0]
            if re.fullmatch(r"[\w-]{11}", vid):
                return vid
    except Exception:
        pass
    # Regex fallback
    m = re.search(r"(?:v=|\/|youtu\.be\/)([\w-]{11})(?:[&?]|$)", s)
    return m.group(1) if m else ""


def check_youtube_block(url_or_id: str, timeout: int = 12) -> dict[str, Any]:
    """Kiểm tra video YouTube có bị Block hay dính bản quyền không (không cần cookie/đăng nhập).
    
    Returns dict:
        ok: bool
        video_id: str
        status: "clean" | "blocked" | "claim" | "private" | "deleted" | "unknown"
        reason: str (Lý do cụ thể từ YouTube)
        title: str
        author: str
        checked_at: str (ISO UTC)
    """
    vid = extract_video_id(url_or_id)
    if not vid:
        return {
            "ok": False,
            "error": f"Không nhận diện được Video ID từ: {url_or_id}",
            "status": "unknown",
            "reason": "URL không hợp lệ",
            "checked_at": _utc_now(),
        }

    watch_url = f"https://www.youtube.com/watch?v={vid}"
    now = _utc_now()
    title = ""
    author = ""

    # Bước 1: Thử gọi oEmbed để lấy metadata ban đầu và kiểm tra tính khả dụng công khai
    oembed_ok = False
    try:
        oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(watch_url)}&format=json"
        req_oembed = urllib.request.Request(oembed_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req_oembed, timeout=timeout) as resp:
            if resp.status == 200:
                oe_data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                title = oe_data.get("title") or ""
                author = oe_data.get("author_name") or ""
                oembed_ok = True
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            # Thường là Private hoặc Blocked
            pass
    except Exception:
        pass

    # Bước 2: Tải Watch Page HTML và bóc tách ytInitialPlayerResponse để đọc playabilityStatus chính xác
    try:
        req_watch = urllib.request.Request(
            watch_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "vi,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req_watch, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # Bóc tách ytInitialPlayerResponse JSON
        player_resp = None
        m = re.search(r"var ytInitialPlayerResponse\s*=\s*(\{.+?\});(?:var|<\/script>)", html)
        if not m:
            m = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.+?\});", html)
        if m:
            try:
                player_resp = json.loads(m.group(1))
            except Exception:
                pass

        if player_resp:
            ps = player_resp.get("playabilityStatus") or {}
            play_status = (ps.get("status") or "").upper()
            play_reason = ps.get("reason") or ""
            subreason = ""
            err_screen = (ps.get("errorScreen") or {}).get("playerErrorMessageRenderer") or {}
            if err_screen.get("subreason"):
                sub_txt = err_screen["subreason"]
                if isinstance(sub_txt, dict):
                    subreason = sub_txt.get("simpleText") or " ".join(
                        r.get("text", "") for r in sub_txt.get("runs", [])
                    )

            # Check playerInterstitialRenderer (xuất hiện trên YouTube khi video đang xử lý hoặc bị xoá)
            inter = (ps.get("errorScreen") or {}).get("playerInterstitialRenderer") or {}
            if inter.get("content"):
                vm = inter["content"].get("interstitialViewModel") or {}
                if vm.get("title", {}).get("content") and not play_reason:
                    play_reason = vm["title"]["content"]
                if vm.get("description", {}).get("content") and not subreason:
                    subreason = vm["description"]["content"]

            if not title:
                mf = (player_resp.get("microformat") or {}).get("playerMicroformatRenderer") or {}
                title = (mf.get("title") or {}).get("simpleText") or ""

            combined_reason = f"{play_reason}: {subreason}".strip(": ") if subreason else play_reason
            low_r = combined_reason.lower()

            # 0. Trường hợp ĐANG XỬ LÝ (Processing) - Video mới tải lên, YouTube đang xử lý và quét
            if any(k in low_r for k in ("xử lý", "processing", "chúng tôi đang xử lý", "being processed", "checks starting", "will begin shortly")):
                return {
                    "ok": True,
                    "video_id": vid,
                    "url": watch_url,
                    "status": "processing",
                    "reason": combined_reason or "YouTube đang xử lý video...",
                    "title": title,
                    "author": author,
                    "checked_at": now,
                }

            # 1. Trường hợp BỊ CHẶN (Blocked / Partially Blocked / Copyright Takedown / Content ID Block)
            # Ưu tiên kiểm tra BỊ CHẶN TRƯỚC HẾT vì YouTube thường báo "Video không có sẵn: Video này đã bị chặn do có nội dung được xác nhận quyền sở hữu của..."
            is_partial = any(k in low_r for k in (
                "không khả dụng tại quốc gia", "not available in your country",
                "ở một số quốc gia", "in some countries", "partially blocked", "chặn một phần"
            ))
            is_copyright = any(k in low_r for k in (
                "bản quyền", "copyright", "chặn", "block", "khiếu nại", "takedown",
                "quốc gia", "country", "claims", "không khả dụng tại", "blocked",
                "quyền sở hữu", "xác nhận quyền sở hữu", "sở hữu trí tuệ", "kuaikan"
            ))
            if is_partial:
                return {
                    "ok": True,
                    "video_id": vid,
                    "url": watch_url,
                    "status": "partially_blocked",
                    "reason": combined_reason or "Video bị chặn tại một số quốc gia (Partially blocked)",
                    "title": title,
                    "author": author,
                    "checked_at": now,
                }
            if is_copyright:
                return {
                    "ok": True,
                    "video_id": vid,
                    "url": watch_url,
                    "status": "blocked",
                    "reason": combined_reason or "Video bị chặn bản quyền YouTube (Content ID Block)",
                    "title": title,
                    "author": author,
                    "checked_at": now,
                }

            # 2. Trường hợp ĐÃ XOÁ / KHÔNG TỒN TẠI THẬT SỰ (Người dùng xoá, link chết)
            if any(k in low_r for k in ("đã xóa", "removed", "deleted", "người tải lên đã xóa")) or ("không có sẵn" in low_r and not is_copyright):
                return {
                    "ok": True,
                    "video_id": vid,
                    "url": watch_url,
                    "status": "deleted",
                    "reason": combined_reason or "Video không có sẵn hoặc đã bị xoá",
                    "title": title,
                    "author": author,
                    "checked_at": now,
                }

            # 3. Lỗi không thể phát khác (UNPLAYABLE, ERROR)
            if play_status in ("UNPLAYABLE", "ERROR"):
                return {
                    "ok": True,
                    "video_id": vid,
                    "url": watch_url,
                    "status": "unknown",
                    "reason": combined_reason or f"Không thể phát ({play_status})",
                    "title": title,
                    "author": author,
                    "checked_at": now,
                }

            # 2. Trường hợp RIÊNG TƯ (Private)
            if play_status == "LOGIN_REQUIRED":
                low_r = play_reason.lower()
                if "riêng tư" in low_r or "private" in low_r:
                    return {
                        "ok": True,
                        "video_id": vid,
                        "url": watch_url,
                        "status": "private",
                        "reason": play_reason or "Video đang ở chế độ Riêng tư (Private)",
                        "title": title,
                        "author": author,
                        "checked_at": now,
                    }
                return {
                    "ok": True,
                    "video_id": vid,
                    "url": watch_url,
                    "status": "unknown",
                    "reason": play_reason or "Yêu cầu đăng nhập hoặc giới hạn",
                    "title": title,
                    "author": author,
                    "checked_at": now,
                }

            # 3. Trường hợp PHÁT ĐƯỢC (OK)
            if play_status == "OK":
                # Kiểm tra hạn chế vùng qua playerMicroformatRenderer
                mf = (player_resp.get("microformat") or {}).get("playerMicroformatRenderer") or {}
                avail_countries = mf.get("availableCountries")
                if isinstance(avail_countries, list) and 0 < len(avail_countries) < 220:
                    return {
                        "ok": True,
                        "video_id": vid,
                        "url": watch_url,
                        "status": "partially_blocked",
                        "reason": f"Video bị chặn tại một số quốc gia (Partially blocked, chỉ xem được tại {len(avail_countries)} nước)",
                        "title": title,
                        "author": author,
                        "checked_at": now,
                    }

                # Kiểm tra thêm có credit bản quyền âm nhạc (Music Content ID Claim) không
                # Khi có Claim âm nhạc, YouTube watch page thường chứa cụm "Âm nhạc trong video này" hoặc "Music in this video"
                is_claim = False
                claim_info = ""
                if "Âm nhạc trong video này" in html or "Music in this video" in html:
                    is_claim = True
                    claim_info = "Phát hiện khiếu nại bản quyền âm thanh (Content ID Music Claim)"

                return {
                    "ok": True,
                    "video_id": vid,
                    "url": watch_url,
                    "status": "claim" if is_claim else "clean",
                    "reason": claim_info if is_claim else "Video sạch, xem bình thường (Không có hạn chế)",
                    "title": title,
                    "author": author,
                    "checked_at": now,
                }

    except Exception as e_watch:
        # Nếu watch page lỗi nhưng oembed thành công
        if oembed_ok:
            return {
                "ok": True,
                "video_id": vid,
                "url": watch_url,
                "status": "clean",
                "reason": "Video công khai / Unlisted truy cập bình thường",
                "title": title,
                "author": author,
                "checked_at": now,
            }
        return {
            "ok": False,
            "video_id": vid,
            "url": watch_url,
            "status": "unknown",
            "reason": f"Lỗi kết nối kiểm tra: {e_watch}",
            "title": title,
            "author": author,
            "checked_at": now,
        }

    return {
        "ok": True,
        "video_id": vid,
        "url": watch_url,
        "status": "clean" if oembed_ok else "unknown",
        "reason": "Hoàn tất kiểm tra",
        "title": title,
        "author": author,
        "checked_at": now,
    }


def check_film_by_job(job_id: str) -> dict[str, Any]:
    """Kiểm tra trạng thái bản quyền của một phim theo job_id và lưu kết quả vào film_db."""
    import core.film_db as film_db

    with film_db._lock, film_db.connect() as con:
        r = con.execute("SELECT job_id, title_vi, title, yt_url, yt_channel FROM films WHERE job_id=?", (job_id,)).fetchone()
        if not r:
            return {"ok": False, "error": f"Không tìm thấy job_id: {job_id}"}
        row = dict(r)

    yt_url = row.get("yt_url") or ""
    if not yt_url:
        return {
            "ok": False,
            "error": "Phim chưa được upload hoặc chưa có link YouTube (yt_url trống)",
            "job_id": job_id,
        }

    res = check_youtube_block(yt_url)
    status = res.get("status") or "unknown"
    reason = res.get("reason") or ""

    # Cập nhật kết quả vào database
    film_db.update_block_status(job_id=job_id, status=status, reason=reason)

    return {
        "ok": True,
        "job_id": job_id,
        "title": row.get("title_vi") or row.get("title"),
        "yt_url": yt_url,
        **res,
    }
