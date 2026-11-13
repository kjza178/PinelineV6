"""
check_cookies.py — Soi file cookie: còn hạn không, đã ĐĂNG NHẬP chưa, và có lấy
được HD không. Trả lời trực tiếp câu "vì sao video HD tải không được".

Port & tổng quát hóa từ test_cookies.py (Galaxy). Khác bản gốc: không hard-code
1 file, mà tự tìm cookie theo nền tảng (dùng find_cookie_file của download.py),
và test max-resolution CÓ/KHÔNG cookie để thấy chênh lệch.

Nguyên tắc: Bilibili trả 1080p+/HD chỉ khi cookie có SESSDATA (token đăng nhập).
Cookie "khách" (chỉ có buvid...) -> kẹt 480p/720p.

Chạy tay:
    python check_cookies.py --platform bilibili
    python check_cookies.py --cookies cookies/bilibili_cookies.txt
    python check_cookies.py --platform bilibili --url https://www.bilibili.com/video/BVxxxx

Yêu cầu: yt-dlp.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from engines.download import detect_platform, find_cookie_file

# Cookie đánh dấu ĐÃ đăng nhập, theo nền tảng.
LOGIN_MARKERS = {
    "bilibili": ["SESSDATA", "DedeUserID", "bili_jct"],
    "douyin": ["sessionid", "sessionid_ss", "sid_tt"],
    "tiktok": ["sessionid", "sid_tt"],
}

# URL mẫu để test độ phân giải (public, ngắn). Đổi nếu video bị gỡ.
DEFAULT_TEST_URL = {
    "bilibili": "https://www.bilibili.com/video/BV1hfGP6aE66",
}


def inspect_expiry(cookie_file: str) -> tuple[int, int, int]:
    """In từng cookie kèm hạn dùng. Trả (tổng, hết hạn, session)."""
    now = int(time.time())
    total = expired = session = 0
    with open(cookie_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            total += 1
            name = parts[5]
            raw = parts[4]
            expiry = int(raw) if raw.isdigit() and raw != "0" else 0
            if expiry == 0:
                session += 1
                status = "Session (mất khi đóng trình duyệt)"
            elif expiry < now:
                expired += 1
                status = "!! HẾT HẠN !!"
            else:
                days = (expiry - now) / 86400
                status = f"còn {days:.1f} ngày"
            print(f"  {name:22s} | {status}")
    return total, expired, session


def check_login(cookie_file: str, platform: str) -> bool:
    """Có token đăng nhập không?"""
    markers = LOGIN_MARKERS.get(platform, [])
    content = Path(cookie_file).read_text(encoding="utf-8", errors="ignore")
    found = [m for m in markers if m in content]
    if not markers:
        print(f"  (chưa biết marker đăng nhập cho '{platform}')")
        return False
    key = markers[0]  # marker chính (vd SESSDATA)
    if key in content:
        print(f"  ✅ Có {key} — ĐÃ đăng nhập ({', '.join(found)})")
        return True
    print(f"  ❌ KHÔNG có {key} — cookie KHÁCH, chưa đăng nhập")
    print("     -> Video HD (1080p+) / nội dung giới hạn sẽ KHÔNG tải được.")
    return False


def max_resolution(url: str, cookie_file: str | None) -> tuple[int, str]:
    """Trả (max_height, title) mà yt-dlp thấy được với/không cookie."""
    import yt_dlp

    opts = {"quiet": True, "skip_download": True, "socket_timeout": 20}
    if cookie_file:
        opts["cookiefile"] = cookie_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    fmts = info.get("formats", []) or []
    heights = [f.get("height") or 0 for f in fmts]
    return (max(heights) if heights else 0), info.get("title", "N/A")


def run(platform: str | None, cookie_file: str | None,
        cookies_dir: str, url: str | None) -> None:
    # --- Xác định file cookie ---
    if not cookie_file:
        if not platform:
            print("Cần --cookies <file> hoặc --platform <bilibili|douyin|tiktok>.")
            return
        cookie_file = find_cookie_file(platform, cookies_dir)
        if not cookie_file:
            print(f"Không tìm thấy cookie cho '{platform}' trong '{cookies_dir}/'.")
            print("  (đặt file tên chứa nền tảng + 'cookie', vd bilibili_cookies.txt)")
            return
    if not platform:
        platform = detect_platform(url or cookie_file)

    print("=" * 56)
    print(f"COOKIE: {cookie_file}")
    print(f"Nền tảng: {platform} | kích thước {Path(cookie_file).stat().st_size} bytes")
    print("=" * 56)

    print("\n[1] Hạn dùng từng cookie:")
    total, expired, session = inspect_expiry(cookie_file)
    print(f"  → {total} cookie ({expired} hết hạn, {session} session)")

    print("\n[2] Trạng thái đăng nhập:")
    logged_in = check_login(cookie_file, platform)

    h0 = h1 = None
    test_url = url or DEFAULT_TEST_URL.get(platform)
    if test_url:
        print(f"\n[3] Test độ phân giải trên: {test_url}")
        try:
            h0, title = max_resolution(test_url, None)
            print(f"  KHÔNG cookie: {h0}p   ({title})")
        except Exception as e:  # noqa: BLE001
            print(f"  KHÔNG cookie: lỗi {e}")
        try:
            h1, _ = max_resolution(test_url, cookie_file)
            print(f"  CÓ  cookie : {h1}p")
        except Exception as e:  # noqa: BLE001
            print(f"  CÓ  cookie : lỗi {e}")
    else:
        print(f"\n[3] Bỏ qua test độ phân giải (chưa có URL mẫu cho '{platform}').")

    print("\n" + "=" * 56)
    if expired and expired == total:
        print("KẾT LUẬN: Cookie HẾT HẠN toàn bộ — export lại từ trình duyệt.")
    elif not logged_in:
        print("KẾT LUẬN: Cookie KHÁCH — đăng nhập rồi export lại để tải HD.")
    elif h0 is not None and h1 is not None and h1 < h0:
        print(f"KẾT LUẬN: Cookie GHIM chất lượng thấp ({h1}p < {h0}p không cookie).")
        print("  Thủ phạm: cookie CURRENT_QUALITY / CURRENT_FNVAL. Cách xử lý:")
        print("  xóa 2 dòng đó khỏi file cookie, HOẶC tải không cookie nếu là video")
        print("  công khai, HOẶC ép chất lượng trong yt-dlp (fnval).")
    else:
        hi = f" (thấy tối đa {h1}p)" if h1 else ""
        print(f"KẾT LUẬN: Cookie ổn (đã đăng nhập, còn hạn){hi}.")
    print("=" * 56)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chẩn đoán cookie cho Pineline.")
    ap.add_argument("--platform", default=None, help="bilibili | douyin | tiktok")
    ap.add_argument("--cookies", default=None, help="Đường dẫn file cookies.txt cụ thể.")
    ap.add_argument("--cookies-dir", default="cookies", help="Thư mục chứa cookie.")
    ap.add_argument("--url", default=None, help="URL mẫu để test độ phân giải.")
    args = ap.parse_args(argv)

    run(args.platform, args.cookies, args.cookies_dir, args.url)


if __name__ == "__main__":
    main()
