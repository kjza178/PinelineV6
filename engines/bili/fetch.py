"""Tải 1 video Bilibili về thư mục job: playurl DASH → chọn luồng → chọn node CDN → tải đa luồng → ghép ffmpeg.

Khác bản gốc (bilibili_downloader/core/downloader.py):
  - tải TIẾP được: đoạn đã xong ghi vào <file>.parts.json → dừng / lỗi rồi chạy lại không tải lại từ đầu;
  - mỗi đoạn thử lần lượt các mirror (đã xếp theo tốc độ), nghỉ tăng dần; link hết hạn (403) → xin playurl mới;
  - dừng giữa chừng (stop()) — dùng cơ chế dừng sẵn có của engines/download;
  - tiến độ gom cả hình + tiếng (on_progress(done, total, speed)).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engines.bili import api, cdn

CHUNK = 8 * 1024 * 1024


class Stopped(RuntimeError):
    pass


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


class StreamFile:
    """Một luồng (hình hoặc tiếng) tải theo Range vào file .part, nhớ đoạn đã xong để tải tiếp."""

    def __init__(self, dest: Path, urls: list[str], session_factory, hdr: dict, workers: int, log=print):
        self.dest, self.urls, self.sf, self.hdr, self.workers, self.log = Path(dest), list(urls), session_factory, hdr, workers, log
        self.part = self.dest.with_name(self.dest.name + ".part")
        self.state = self.dest.with_name(self.dest.name + ".parts.json")
        self.total = 0
        self.done_bytes = 0
        self._lock = threading.Lock()

    def size(self) -> int:
        s = self.sf()
        for u in self.urls:
            try:
                r = s.get(u, headers={**self.hdr, "Range": "bytes=0-0"}, timeout=12)
                cr = r.headers.get("Content-Range", "")
                if r.status_code in (200, 206) and "/" in cr:
                    return int(cr.rsplit("/", 1)[1])
            except Exception as e:  # noqa: BLE001
                self.log(f"[bili] đo kích thước lỗi ({type(e).__name__}) — thử node khác")
        raise api.BiliError("Không đo được kích thước luồng (mọi node CDN đều lỗi / link hết hạn)")

    def run(self, stop, on_bytes) -> None:
        if self.dest.is_file() and self.dest.stat().st_size > 0:
            self.total = self.done_bytes = self.dest.stat().st_size
            return
        self.total = self.total or self.size()
        ranges = [(i, a, min(a + CHUNK, self.total) - 1) for i, a in enumerate(range(0, self.total, CHUNK))]
        done: set[int] = set()
        if self.part.is_file() and self.state.is_file() and self.part.stat().st_size == self.total:
            try:
                st = json.loads(self.state.read_text(encoding="utf-8"))
                if st.get("total") == self.total:
                    done = {int(x) for x in st.get("done") or []}
            except (OSError, ValueError) as e:
                self.log(f"[bili] đọc tiến độ cũ lỗi ({e}) — tải lại luồng này")
        if not done:
            with open(self.part, "wb") as f:
                f.truncate(self.total)
        self.done_bytes = sum(b - a + 1 for i, a, b in ranges if i in done)
        if done:
            self.log(f"[bili] tải tiếp {self.dest.name}: đã có {self.done_bytes * 100 // max(self.total, 1)}%")
        on_bytes(0)
        local = threading.local()

        def seg(i: int, a: int, b: int) -> None:
            if not hasattr(local, "s"):
                local.s = self.sf()
            need = b - a + 1
            err = None
            for attempt in range(6):
                for u in self.urls:
                    if stop():
                        raise Stopped()
                    try:
                        r = local.s.get(u, headers={**self.hdr, "Range": f"bytes={a}-{b}"}, timeout=30)
                        if r.status_code in (200, 206) and len(r.content) == need:
                            with self._lock:
                                with open(self.part, "r+b") as f:
                                    f.seek(a)
                                    f.write(r.content)
                                done.add(i)
                                self.done_bytes += need
                                self.state.write_text(json.dumps({"total": self.total, "done": sorted(done)}), encoding="utf-8")
                            on_bytes(need)
                            return
                        err = f"HTTP {r.status_code}, {len(r.content)}/{need} byte"
                    except Stopped:
                        raise
                    except Exception as e:  # noqa: BLE001
                        err = f"{type(e).__name__}: {e}"
                time.sleep(min(2 ** attempt, 20))
            raise api.BiliError(f"Đoạn {a}-{b} của {self.dest.name} lỗi sau nhiều lần thử: {err}")

        todo = [r for r in ranges if r[0] not in done]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = [ex.submit(seg, *r) for r in todo]
            first_err = None
            for f in futs:
                try:
                    f.result()
                except BaseException as e:  # noqa: BLE001 — gom lỗi đầu tiên, các đoạn khác vẫn chờ xong
                    first_err = first_err or e
            if first_err:
                raise first_err
        self.part.replace(self.dest)
        self.state.unlink(missing_ok=True)


def download(url: str, work_dir: Path, *, cookie: str = "", max_height: int | None = None, prefer_hevc: bool = False,
             stop=lambda: False, on_progress=None, workers: int = 8, log=print, session_factory=None) -> dict:
    """Tải về work_dir/source.mp4 (+ source.jpg, source.info.json). Trả meta (title, duration, width, height, vcodec…)."""
    import requests
    sf = session_factory or requests.Session
    s = sf()
    hdr = api.headers(cookie)
    work_dir = Path(work_dir)
    url = api.resolve_short(url, s)
    link = api.parse_link(url)
    if link["type"] != "video":
        raise api.BiliError("Link này là danh sách (hợp tập / series) — thêm job sẽ tách thành từng video")
    info = api.video_info(link["bvid"], s, hdr)
    pages = info["pages"] or [{"page": 1, "cid": None, "part": "", "duration": info["duration"]}]
    page = next((p for p in pages if p["page"] == (link.get("p") or 1)), pages[0])
    if not page.get("cid"):
        raise api.BiliError("Không lấy được cid của video")
    title = info["title"] if len(pages) <= 1 else f"{info['title']} - {page['part'] or 'P' + str(page['page'])}"
    dur = page.get("duration") or info["duration"]
    if login := cookie and "SESSDATA" in cookie:
        log("[bili] có cookie đăng nhập → mở chất lượng cao")
    else:
        log("[bili] không có cookie đăng nhập — Bilibili thường giới hạn ≤480p/720p")

    mh = max_height(dur) if callable(max_height) else max_height     # callable: trần theo thời lượng (phim dài → thấp hơn)

    def get_streams():
        d = api.playurl(info["bvid"], page["cid"], s, hdr)
        return d, *api.pick_streams(d["dash"], mh, prefer_hevc)

    d, v, a = get_streams()
    log(f"[bili] {title} · {v.get('width')}x{v.get('height')} {api.QUALITY.get(v.get('id'), v.get('id'))} "
        f"codec {v.get('codecs')} · tiếng {a.get('id')}")
    best_v, v_order = cdn.probe_best(api.stream_urls(v), s, hdr, log=log)
    _, a_order = cdn.probe_best(api.stream_urls(a), s, hdr, log=log)

    vf = StreamFile(work_dir / "source.video.m4s", v_order, sf, hdr, workers, log)
    af = StreamFile(work_dir / "source.audio.m4s", a_order, sf, hdr, max(2, workers // 2), log)
    t0, last = time.time(), [0.0]
    lock = threading.Lock()
    got = [0]

    def on_bytes(n: int) -> None:
        with lock:
            got[0] += n
            now = time.time()
            if on_progress and (now - last[0] >= 1.0 or n == 0):
                last[0] = now
                total = (vf.total or 0) + (af.total or 0)
                done = vf.done_bytes + af.done_bytes
                on_progress(done, total, got[0] / max(now - t0, 1e-3))

    for sfile in (vf, af):                    # đo trước cả hai → % tính trên tổng hình + tiếng ngay từ đầu
        try:
            if not (sfile.dest.is_file() and sfile.dest.stat().st_size > 0):
                sfile.total = sfile.size()
        except api.BiliError as e:
            log(f"[bili] {e} — sẽ thử lại khi tải")
    for sfile in (vf, af):
        for attempt in range(2):
            try:
                sfile.run(stop, on_bytes)
                break
            except Stopped:
                raise
            except api.BiliError as e:
                if attempt:
                    raise
                log(f"[bili] {e} — xin link mới (link CDN có hạn) rồi tải tiếp")
                d, v2, a2 = get_streams()
                fresh = v2 if sfile is vf else a2
                _, sfile.urls = cdn.probe_best(api.stream_urls(fresh), s, hdr, log=log)
                sfile.total = 0
    if on_progress:
        tot = vf.total + af.total
        on_progress(tot, tot, got[0] / max(time.time() - t0, 1e-3))

    out = work_dir / "source.mp4"
    tmp = work_dir / "source.muxing.mp4"
    r = subprocess.run([_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(vf.dest), "-i", str(af.dest),
                        "-c", "copy", "-movflags", "+faststart", str(tmp)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not tmp.is_file():
        raise api.BiliError(f"ffmpeg ghép hình + tiếng lỗi: {(r.stderr or '')[-300:]}")
    tmp.replace(out)
    for f in (vf.dest, af.dest):
        f.unlink(missing_ok=True)
    if info.get("pic"):
        try:
            pr = s.get(info["pic"], headers=hdr, timeout=15)
            if pr.status_code == 200 and pr.content:
                (work_dir / "source.jpg").write_bytes(pr.content)
        except Exception as e:  # noqa: BLE001
            log(f"[bili] tải ảnh bìa lỗi (bỏ qua): {e}")
    meta = {"platform": "bilibili", "source_url": api.video_url(info["bvid"], page["page"] if len(pages) > 1 else None),
            "title": title, "id": info["bvid"] + (f"_p{page['page']}" if len(pages) > 1 else ""),
            "duration_sec": dur, "uploader": info["owner"], "width": v.get("width"), "height": v.get("height"),
            "vcodec": v.get("codecs"), "webpage_url": api.video_url(info["bvid"], page["page"] if len(pages) > 1 else None),
            "downloader": "bili_native", "quality": api.QUALITY.get(v.get("id"), v.get("id")), "logged_in": bool(login)}
    (work_dir / "source.info.json").write_text(json.dumps({**meta, "description": info.get("desc", ""),
                                                           "thumbnail": info.get("pic", "")}, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    return meta
