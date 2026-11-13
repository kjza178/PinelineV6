"""
playlist.py — Bóc 1 link thành DANH SÁCH video rồi tải hàng loạt.

Vì sao tách riêng: Pineline theo kiến trúc "1 job = 1 video" (mỗi video 1 thư mục
work/<hash>/ có manifest + resume riêng). Nên KHÔNG nhét vòng lặp tải nhiều video
vào download.py. Thay vào đó, lớp này:
    1) expand(url)        -> đọc link, trả về [url1, url2, ...]
                            (video đơn -> danh sách 1 phần tử; playlist/channel -> N)
    2) batch_download()   -> tải song song CÓ KIỂM SOÁT, mỗi video 1 job độc lập,
                            tự resume, 1 video hỏng không làm chết cả loạt.

Khác biệt quan trọng so với bản Galaxy (đa luồng 6 × aria2c 4 conn = 24 kết nối,
đúng nguồn gốc lỗi 412/EOF của Bilibili): ở đây số job song song NHỎ (mặc định 2)
và mỗi job hạ số kết nối (conn=3) -> tổng ~6, an toàn với giới hạn ~4-5 conn/server.

Chạy tay:
    python playlist.py "https://space.bilibili.com/UID/lists?sid=SID"
    python playlist.py "<link playlist>" --workers 3 --conn 2
    python playlist.py "<link>" --list-only        # chỉ liệt kê, không tải

Yêu cầu: yt-dlp, ffmpeg (như download.py).
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yt_dlp

from engines.download import (
    normalize_url, detect_platform, find_cookie_file,
    download_video, job_id_from_url,
)


def _build_url_from_entry(entry: dict, platform: str) -> str | None:
    """Lấy URL đầy đủ từ một entry playlist. extract_flat có khi chỉ trả id."""
    u = entry.get("webpage_url") or entry.get("url") or entry.get("id")
    if not u:
        return None
    u = str(u)
    if u.startswith("http"):
        return u
    # chỉ có id -> dựng URL theo nền tảng (giống cách Galaxy làm)
    if platform == "douyin":
        return f"https://www.douyin.com/video/{u}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@_/video/{u}"
    return f"https://www.bilibili.com/video/{u}"


def expand_meta(
    url: str,
    cookies: str | None = None,
    cookies_dir: str | Path | None = "cookies",
    cookies_from_browser: str | None = None,
    proxy: str | None = None,
) -> dict:
    """Như expand nhưng trả THÊM metadata để gom nhóm:
        {"title": <tiêu đề playlist/video>, "parts": [{"url":.., "title":..}, ...]}
    Video đơn -> parts có 1 phần tử. Playlist/multi-P -> nhiều phần.
    """
    url = normalize_url(url)
    platform = detect_platform(url)

    opts: dict = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": False,
        "ignoreerrors": True,
    }
    cookie_file = cookies or find_cookie_file(platform, cookies_dir)
    if cookie_file:
        opts["cookiefile"] = cookie_file
    elif cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if proxy:
        opts["proxy"] = proxy

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info is None:
        return {"title": None, "parts": []}

    gtitle = info.get("title")
    entries = info.get("entries")
    if not entries:                                  # video đơn
        return {"title": gtitle,
                "parts": [{"url": info.get("webpage_url") or url, "title": gtitle}]}

    parts: list[dict] = []
    seen: set[str] = set()
    for e in entries:
        if not e:
            continue
        u = _build_url_from_entry(e, platform)
        if u and u not in seen:
            seen.add(u)
            parts.append({"url": u, "title": e.get("title")})
    return {"title": gtitle, "parts": parts}


def expand(
    url: str,
    cookies: str | None = None,
    cookies_dir: str | Path | None = "cookies",
    cookies_from_browser: str | None = None,
    proxy: str | None = None,
) -> list[str]:
    """Đọc link, trả danh sách URL video. Video đơn -> [url]; playlist -> [nhiều]."""
    meta = expand_meta(url, cookies=cookies, cookies_dir=cookies_dir,
                       cookies_from_browser=cookies_from_browser, proxy=proxy)
    return [p["url"] for p in meta["parts"]]


def batch_download(
    url: str,
    work_root: str | Path = "work",
    workers: int = 2,
    conn: int = 3,
    cookies: str | None = None,
    cookies_dir: str | Path | None = "cookies",
    cookies_from_browser: str | None = None,
    proxy: str | None = None,
    force: bool = False,
    on_done=None,
) -> list[dict]:
    """Tải tất cả video của 1 link. Trả list kết quả {url, job_id, ok, error}.

    on_done(result) — callback tùy chọn, gọi mỗi khi 1 video xong (để UI/log).
    """
    urls = expand(
        url, cookies=cookies, cookies_dir=cookies_dir,
        cookies_from_browser=cookies_from_browser, proxy=proxy,
    )
    if not urls:
        print("[playlist] Không bóc được video nào từ link.")
        return []

    print(f"[playlist] {len(urls)} video | {workers} luồng × conn {conn}")
    work_root = Path(work_root)

    def _one(vurl: str) -> dict:
        job_id = job_id_from_url(vurl)
        work_dir = work_root / job_id
        try:
            download_video(
                vurl, work_dir,
                cookies=cookies, cookies_dir=cookies_dir,
                cookies_from_browser=cookies_from_browser,
                proxy=proxy, force=force, conn=conn,
            )
            return {"url": vurl, "job_id": job_id, "ok": True, "error": None}
        except Exception as e:  # noqa: BLE001
            return {"url": vurl, "job_id": job_id, "ok": False, "error": str(e)}

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, u): u for u in urls}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            flag = "OK" if r["ok"] else f"LỖI ({r['error'][:60]})"
            print(f"[playlist] {i}/{len(urls)} {flag}  {r['job_id']}")
            if on_done:
                on_done(r)

    ok = sum(1 for r in results if r["ok"])
    print(f"[playlist] Xong: {ok}/{len(results)} tải được.")
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Bóc playlist/channel thành nhiều video và tải hàng loạt."
    )
    ap.add_argument("url", help="Link video / playlist / channel")
    ap.add_argument("--work-root", default="work")
    ap.add_argument("--workers", type=int, default=2, help="Số video tải song song.")
    ap.add_argument("--conn", type=int, default=3,
                    help="Số kết nối aria2c mỗi video (hạ khi workers cao).")
    ap.add_argument("--cookies", default=None)
    ap.add_argument("--cookies-dir", default="cookies")
    ap.add_argument("--cookies-from-browser", default=None)
    ap.add_argument("--proxy", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list-only", action="store_true",
                    help="Chỉ liệt kê video, không tải.")
    args = ap.parse_args(argv)

    if args.list_only:
        urls = expand(
            args.url, cookies=args.cookies, cookies_dir=args.cookies_dir,
            cookies_from_browser=args.cookies_from_browser, proxy=args.proxy,
        )
        print(f"{len(urls)} video:")
        for i, u in enumerate(urls, 1):
            print(f"  {i:>3}. {u}")
        return

    batch_download(
        args.url, work_root=args.work_root,
        workers=args.workers, conn=args.conn,
        cookies=args.cookies, cookies_dir=args.cookies_dir,
        cookies_from_browser=args.cookies_from_browser,
        proxy=args.proxy, force=args.force,
    )


if __name__ == "__main__":
    main()
