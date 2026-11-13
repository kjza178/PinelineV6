"""
download.py — STAGE 1: Tải video (Douyin / Bilibili / TikTok / ...).

Engine: yt-dlp. Tự resume — đã tải xong thì bỏ qua.
Đã gộp kỹ thuật production từ Galaxy Video Downloader (Jonathan Nguyen):
    - Ép H.264 (avc1): pipeline xử lý frame/render nhanh & tương thích hơn AV1/VP9.
    - aria2c đa kết nối + args đã tune (né lỗi TLS 0x80090301 của Bilibili).
    - Tự tìm file cookie theo nền tảng.
    - normalize_url cho link Bilibili dạng /lists?sid=.
    - Proxy.

Kết quả một job nằm gọn trong 1 thư mục:  work/<job>/
    source.mp4        video gốc đã merge (H.264)
    meta.json         metadata rút gọn (title, duration, uploader, kích thước...)
    source.info.json  metadata đầy đủ do yt-dlp dump
    manifest.json     trạng thái pipeline (stage nào đã xong)

Chạy tay:
    python download.py "https://v.douyin.com/xxxxx/"
    python download.py "https://www.bilibili.com/video/BVxxxx" --cookies-from-browser chrome
    python download.py "<url>" --cookies-dir ./cookies --proxy http://ip:port
    python download.py "<url>" --job phim01 --force

Yêu cầu:
    pip install -U yt-dlp        (nhớ -U, Douyin hay đổi chống-bot)
    ffmpeg trong PATH            (merge video + audio)
    aria2c (tùy chọn)            để trong ./ hoặc ./bin/ hoặc PATH -> tự bật tăng tốc

Cookie: để file cookies Netscape (.txt) tên chứa tên nền tảng trong thư mục
--cookies-dir (mặc định ./cookies), ví dụ: bilibili_cookies.txt, douyin_cookies.txt.
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

import yt_dlp

from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state

STAGE = "download"

# --- Cơ chế DỪNG bản tải đang chạy (để 'tải lại từ đầu' xoá được file đang khoá) ---
import threading as _threading
import time as _time_mod
_STOP: set[str] = set()
_STOP_LOCK = _threading.Lock()


def _request_stop(work_dir) -> None:
    with _STOP_LOCK:
        _STOP.add(Path(work_dir).name)


def _is_stopped(work_dir) -> bool:
    with _STOP_LOCK:
        return Path(work_dir).name in _STOP


def _clear_stop(work_dir) -> None:
    with _STOP_LOCK:
        _STOP.discard(Path(work_dir).name)


def _kill_job_aria2c(work_dir) -> None:
    """Kill tiến trình aria2c đang tải cho ĐÚNG job này (nhận diện qua job_id/đường
    dẫn job trong dòng lệnh) -> nhả khoá file để xoá + tải lại sạch."""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return
    jobid = Path(work_dir).name.lower()
    wd = str(Path(work_dir).resolve()).lower()
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            nm = (p.info.get("name") or "").lower()
            if "aria2c" not in nm:
                continue
            cl = " ".join(p.info.get("cmdline") or []).lower()
            if jobid in cl or wd in cl:
                p.kill()
                print(f"[stop] kill aria2c của job {jobid}")
        except Exception:  # noqa: BLE001
            continue


def _aria2_downloaded(ctrl_path) -> int:
    """Byte ĐÃ TẢI THẬT theo file điều khiển .aria2. Khi tải đa kết nối, file .part
    là SPARSE (ghi ở nhiều offset) -> st_size phồng gần đủ dù chưa tải xong -> % sai.
    Đọc bitfield trong .aria2 (số piece đã xong × pieceLen) mới ra byte thật."""
    import struct
    try:
        data = Path(ctrl_path).read_bytes()
        off = 2 + 4                                   # version(2) + extension(4)
        ihl = struct.unpack(">I", data[off:off + 4])[0]; off += 4 + ihl   # infoHash
        piece_len = struct.unpack(">I", data[off:off + 4])[0]; off += 4
        total_len = struct.unpack(">Q", data[off:off + 8])[0]; off += 8 + 8  # +uploadLen
        bf_len = struct.unpack(">I", data[off:off + 4])[0]; off += 4
        bitfield = data[off:off + bf_len]
        done_pieces = sum(bin(b).count("1") for b in bitfield)
        return min(done_pieces * piece_len, total_len)
    except Exception:  # noqa: BLE001 — đang ghi dở -> tạm dùng st_size file dữ liệu
        try:
            return Path(str(ctrl_path)[:-7]).stat().st_size   # bỏ đuôi ".aria2"
        except OSError:
            return 0


def _clean_incomplete_fragments(work_dir) -> list:
    """Xoá RIÊNG các mảnh tải DỞ (file .part + control .aria2) để tải lại sạch bằng
    native. GIỮ NGUYÊN mảnh đã HOÀN CHỈNH (vd video source.f30064.mp4 xong -> không
    có .part/.aria2 -> còn nguyên). Đây là 'chỉ reset mảnh lỗi' (thường là audio):
    tránh aria2c kẹt 80090301 ở byte cuối + tránh native 416 do offset file sparse."""
    removed = []
    for p in list(Path(work_dir).glob("*.part")) + list(Path(work_dir).glob("*.part.aria2")):
        try:
            p.unlink(); removed.append(p.name)
        except OSError:
            pass
    return removed


def _delete_sources(work_dir) -> bool:
    """Xoá mọi source.* ; trả True nếu SẠCH (không còn file nào kẹt/khoá)."""
    left = 0
    for f in Path(work_dir).glob("source.*"):
        try:
            f.unlink()
        except OSError:
            left += 1
    return left == 0

# Ưu tiên H.264 (avc1) + audio VỪA ĐỦ (≤100kbps). LỒNG TIẾNG -> audio gốc chỉ bị
# ducking dưới giọng Việt nên KHÔNG cần track Hi-Res (vd Bilibili 30280 ~158k/676MB)
# — track Hi-Res đó hay bị CDN Bilibili ngắt kết nối giữa chừng (tải mãi không xong).
# Lấy 30232(93k)/30216(66k) nhẹ + ổn hơn nhiều; hết mới fallback bestaudio.
def _format_sel(max_height: int | None = None, *, hevc: bool = False) -> str:
    """Selector yt-dlp. max_height giới hạn chiều cao; hevc=True ưu tiên H.265 cùng độ phân giải.

    Lưu ý: KHÔNG nhét dấu / vào nhánh audio của `video+audio` — yt-dlp sẽ tách thành
    alternative audio-only (đã từng tải nhầm đúng 30280).
    """
    vh = f"[height<={int(max_height)}]" if max_height else ""
    # Một selector audio duy nhất (nhẹ trước nhờ format_sort br)
    audio = "bestaudio[ext=m4a]/bestaudio"
    if hevc:
        # Bilibili: hvc1.* ; không fallback `best` (dễ ra audio-only)
        return (
            f"bestvideo[vcodec^=hvc1]{vh}+bestaudio[ext=m4a]/"
            f"bestvideo[vcodec^=hev1]{vh}+bestaudio[ext=m4a]/"
            f"bestvideo[vcodec^=hvc1]{vh}+bestaudio/"
            f"bestvideo[vcodec^=hev1]{vh}+bestaudio/"
            f"bestvideo[vcodec^=av01]{vh}+bestaudio[ext=m4a]/"
            f"bestvideo{vh}+bestaudio[ext=m4a]"
        )
    return (
        f"bestvideo[vcodec^=avc1]{vh}+bestaudio[ext=m4a]/"
        f"bestvideo[vcodec^=avc1]{vh}+bestaudio/"
        f"best[vcodec^=avc1]{vh}/"
        f"bestvideo{vh}+bestaudio[ext=m4a]"
    )


FORMAT = _format_sel(None)
FORMAT_SORT = ["vcodec:h264"]


def _cfg_max_height() -> int | None:
    """DOWNLOAD_MAX_HEIGHT từ env/config (vd 720/480). 0/off = không giới hạn."""
    import os
    try:
        from core.config import get as cfg
    except Exception:  # noqa: BLE001
        def cfg(k, d=""):  # type: ignore
            return os.getenv(k, d)
    raw = (os.getenv("DOWNLOAD_MAX_HEIGHT") or cfg("DOWNLOAD_MAX_HEIGHT", "") or "").strip().lower()
    if raw in ("", "0", "off", "none", "full", "best"):
        return None
    try:
        h = int(raw)
        return h if h > 0 else None
    except ValueError:
        return None


def _cfg_prefer_hevc() -> bool:
    """DOWNLOAD_PREFER_HEVC=1 → ưu tiên H.265 cùng độ phân giải (file nhẹ, CDN đỡ đứt)."""
    import os
    try:
        from core.config import get as cfg
    except Exception:  # noqa: BLE001
        def cfg(k, d=""):  # type: ignore
            return os.getenv(k, d)
    raw = (os.getenv("DOWNLOAD_PREFER_HEVC") or cfg("DOWNLOAD_PREFER_HEVC", "") or "").strip().lower()
    return raw in ("1", "true", "yes", "on", "hevc", "h265")


def _auto_height_for_duration(duration_sec: float | None, cap: int | None) -> int | None:
    """Phim rất dài (Bilibili multi-season) → 720p AVC dễ ~3GB+, CDN hay đứt.
    Tự hạ trần chiều cao trừ khi user đã set DOWNLOAD_MAX_HEIGHT."""
    if cap is not None:
        return cap
    if not duration_sec or duration_sec <= 0:
        return None
    if duration_sec >= 4 * 3600:   # ≥4h → 480p
        return 480
    if duration_sec >= 2 * 3600:   # ≥2h → 720p (vẫn cho phép)
        return 720
    return None


def _probe_duration(url: str, base_opts: dict) -> float | None:
    """Lấy duration nhanh (không tải) để chọn format."""
    try:
        opts = {k: v for k, v in base_opts.items()
                if k in ("cookiefile", "cookiesfrombrowser", "proxy", "quiet", "no_warnings")}
        opts.update({"skip_download": True, "quiet": True, "no_warnings": True})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return float(info.get("duration") or 0) or None
    except Exception as e:  # noqa: BLE001
        print(f"[probe] không lấy được duration: {e}")
        return None

_IMG_EXT = {".json", ".webp", ".jpg", ".jpeg", ".png"}


def detect_platform(url: str) -> str:
    u = url.lower()
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if "douyin.com" in u or "iesdouyin" in u or "v.douyin" in u:
        return "douyin"
    if "tiktok.com" in u:
        return "tiktok"
    return "generic"


def normalize_url(url: str) -> str:
    """Chuyển link Bilibili kiểu mới về format yt-dlp hiểu (từ code Galaxy)."""
    m = re.match(r"https?://space\.bilibili\.com/(\d+)/lists\?sid=(\d+)", url)
    if m:
        uid, sid = m.group(1), m.group(2)
        return f"https://space.bilibili.com/{uid}/channel/collectiondetail?sid={sid}"
    return url


def job_id_from_url(url: str) -> str:
    """Job id mặc định = hash URL. Chạy lại cùng URL -> cùng thư mục -> resume."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def find_cookie_file(platform: str, cookies_dir: str | Path | None) -> str | None:
    """Tìm file cookie theo nền tảng trong cookies_dir (vd bilibili_cookies.txt)."""
    if not cookies_dir:
        return None
    d = Path(cookies_dir)
    if not d.is_dir():
        return None
    for f in sorted(d.iterdir()):
        name = f.name.lower()
        if name.endswith(".txt") and "cookie" in name and platform in name:
            return str(f)
    return None


def get_aria2c_path() -> str | None:
    """Tìm aria2c -> trả ĐƯỜNG DẪN TUYỆT ĐỐI (yt-dlp tìm external_downloader trong
    PATH, không phải CWD, nên đường dẫn tương đối sẽ bị bỏ qua -> phải absolute)."""
    for cand in ("aria2c.exe", "aria2c", "bin/aria2c.exe", "bin/aria2c"):
        p = Path(cand)
        if p.exists():
            return str(p.resolve())          # tuyệt đối
    return shutil.which("aria2c")


def apply_speed_opts(opts: dict, conn: int = 4) -> dict:
    """Tải mảnh song song + aria2c nếu có (args tune từ code Galaxy).

    conn = số kết nối aria2c mỗi server. Khi tải NHIỀU video song song (playlist),
    hạ conn xuống (vd 2-3) để tổng kết nối tới Bilibili không vượt ~4-5 -> né lỗi
    EOF/TLS(0x80090301)/412.
    """
    opts["concurrent_fragment_downloads"] = conn
    aria2c = get_aria2c_path()
    if aria2c:
        opts["external_downloader"] = aria2c
        # Bilibili giới hạn ~4-5 kết nối/server; nhiều quá bị ngắt (EOF) + lỗi
        # TLS Windows 0x80090301. Giữ vừa phải + tự retry.
        opts["external_downloader_args"] = [
            "-x", str(conn), "-s", str(conn), "-k", "1M",
            f"--max-connection-per-server={conn}", "--min-split-size=1M",
            "--file-allocation=none",
            "--max-tries=10", "--retry-wait=3",
            "--timeout=60", "--connect-timeout=30",
            "--max-file-not-found=5",
        ]
    return opts


def download_video(
    url: str,
    work_dir: str | Path,
    cookies: str | None = None,
    cookies_dir: str | Path | None = "cookies",
    cookies_from_browser: str | None = None,
    proxy: str | None = None,
    force: bool = False,
    conn: int = 4,
    fresh: bool = False,
) -> dict:
    """Tải 1 video về work_dir. Trả về dict trạng thái stage (video_path + metadata).

    conn  = số kết nối aria2c/mảnh song song (hạ xuống khi tải playlist song song).
    fresh = XOÁ sạch dữ liệu tải cũ (mảnh dở/hỏng) rồi tải LẠI TỪ ĐẦU. Dùng khi
            resume cứ fail (file .part hỏng, URL hết hạn...).
    """
    work_dir = Path(work_dir)
    m = Manifest(work_dir)

    if fresh:
        # File .part hay bị KHOÁ bởi bản tải đang chạy -> phải DỪNG bản cũ trước:
        #  1) báo vòng tải cũ dừng, 2) kill aria2c của job -> yt-dlp cũ nhả file,
        #  3) đợi xoá sạch source.* (tối đa ~12s), 4) mới tải lại từ đầu.
        _request_stop(work_dir)
        _kill_job_aria2c(work_dir)
        ok = False
        for _ in range(24):
            if _delete_sources(work_dir):
                ok = True
                break
            _kill_job_aria2c(work_dir)          # phòng aria2c respawn theo retry cũ
            _time_mod.sleep(0.5)
        _clear_stop(work_dir)
        print("[fresh] đã xoá sạch data cũ -> tải lại từ đầu" if ok
              else "[fresh] vài file còn kẹt, sẽ tải đè phần còn lại")
        force = True

    # --- CHECKPOINT: đã tải rồi thì bỏ qua ---
    if m.is_done(STAGE) and not force:
        st = m.stage(STAGE)
        if st and Path(st.get("video_path", "")).exists():
            print(f"[skip] Đã tải trước đó: {st['video_path']}")
            return st
        print("[warn] Manifest báo done nhưng file không còn — tải lại.")

    m.mark_running(STAGE)
    url = normalize_url(url)
    platform = detect_platform(url)

    user_cap = _cfg_max_height()
    # opts nền (cookie/proxy) trước để probe duration
    ydl_opts: dict = {
        "format": FORMAT,
        "format_sort": FORMAT_SORT,
        "merge_output_format": "mp4",
        "outtmpl": str(work_dir / "source.%(ext)s"),
        "noplaylist": True,          # 1 job = 1 video; playlist tách ở lớp queue
        "retries": 20,
        "fragment_retries": 20,
        "file_access_retries": 10,
        "retry_sleep_functions": {"http": lambda n: min(2 ** n, 45)},
        "socket_timeout": 90,
        # Không dùng http_chunk_size với Bilibili — hay lỗi "378 bytes read, N more expected"
        "writethumbnail": True,
        "writeinfojson": True,       # dump metadata gốc -> source.info.json
        "quiet": False,
        "no_warnings": False,
    }
    # Chỉ bật chunk cho nền tảng khác (YouTube…); Bilibili CDN hay fail range request
    if platform != "bilibili":
        ydl_opts["http_chunk_size"] = 10 * 1024 * 1024
    apply_speed_opts(ydl_opts, conn=conn)

    # --- Cookie: --cookies > auto theo nền tảng > --cookies-from-browser ---
    cookie_file = cookies or find_cookie_file(platform, cookies_dir)
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
        print(f"[cookie] {cookie_file}")
    elif cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)
        print(f"[cookie] từ browser: {cookies_from_browser}")
    else:
        print("[cookie] không có — tải chế độ khách (Bilibili HD/Douyin có thể bị chặn)")

    if proxy:
        ydl_opts["proxy"] = proxy
        print(f"[proxy] {proxy}")

    # Duration → tự hạ độ phân giải cho phim dài (né file 3GB+ CDN ngắt)
    dur = None
    info_cache = work_dir / "source.info.json"
    if info_cache.exists():
        try:
            dur = float(json.loads(info_cache.read_text(encoding="utf-8")).get("duration") or 0) or None
        except Exception:  # noqa: BLE001
            dur = None
    if dur is None:
        dur = _probe_duration(url, ydl_opts)
    max_h = _auto_height_for_duration(dur, user_cap)
    prefer_hevc = _cfg_prefer_hevc()
    ydl_opts["format"] = _format_sel(max_h, hevc=prefer_hevc)
    if prefer_hevc:
        ydl_opts["format_sort"] = ["res", "br"]  # không ép h264
        print(f"[format] ưu tiên HEVC/H.265 ≤{max_h or 'best'}p (nhẹ hơn AVC)")
    elif max_h:
        print(f"[format] giới hạn ≤{max_h}p AVC"
              + (f" (phim ~{int(dur/3600)}h, tránh file quá lớn)" if dur and not user_cap else ""))
    else:
        print("[format] best avc1 (không giới hạn chiều cao)")

    if "external_downloader" in ydl_opts:
        print("[speed] aria2c bật (đa kết nối)")

    # --- Progress: theo dõi KÍCH THƯỚC file đang tải trên đĩa (mỗi ~1s).
    # Cách này chạy cho CẢ aria2c (external, không bắn progress_hooks) lẫn native.
    import threading
    import time as _t
    _stop = threading.Event()

    def _total_bytes() -> int:
        """Tổng bytes dự kiến. Bilibili info.json không có requested_formats +
        filesize=None -> suy format_id từ tên file source.f<id>.* rồi lấy
        filesize (hoặc filesize_approx) từ mảng 'formats'."""
        try:
            import re
            data = json.loads((work_dir / "source.info.json").read_text(encoding="utf-8"))
            fmts = {f.get("format_id"): f for f in data.get("formats", [])}
            ids = set()
            for p in work_dir.glob("source.f*"):
                mo = re.match(r"source\.f([0-9A-Za-z]+)", p.name)   # source.f30064.mp4.part -> 30064
                if mo:
                    ids.add(mo.group(1))
            dur = data.get("duration") or 0
            tot = 0
            for fid in ids:
                f = fmts.get(fid) or {}
                sz = f.get("filesize") or f.get("filesize_approx")
                if not sz and dur and f.get("tbr"):
                    # Bilibili hay THIẾU filesize track audio -> ước từ bitrate:
                    # tbr(kbps) * 1000 / 8 * thời_lượng(giây) = bytes. Không có mục này
                    # thì tổng chỉ tính video -> % vọt >100% khi tải thêm audio.
                    sz = f["tbr"] * 1000 / 8 * dur
                tot += sz or 0
            return int(tot)
        except Exception:  # noqa: BLE001
            return 0

    def _monitor():
        total = 0
        prev, prev_t = 0, _t.time()
        while not _stop.wait(1.0):
            # 1 tick lỗi (file .part vừa bị đổi tên, đĩa bận...) KHÔNG được giết
            # thread -> nếu không tiến độ đứng im, trông như treo.
            try:
                files = {p for p in work_dir.glob("source.f*")} | set(work_dir.glob("*.part"))
                done = 0
                for p in files:
                    if p.suffix.lower() in (".json", ".jpg", ".aria2"):
                        continue
                    try:
                        ctrl = p.with_name(p.name + ".aria2")
                        if ctrl.exists():
                            done += _aria2_downloaded(ctrl)     # byte THẬT (file sparse)
                        else:
                            done += p.stat().st_size            # file xong / native (không sparse)
                    except OSError:
                        pass                    # file vừa bị rename/xoá -> bỏ qua
                # Tính lại total MỖI nhịp (không cache): file audio xuất hiện SAU video
                # -> nếu cache sớm thì total chỉ có video, % vọt >100%. Tính lại thì khi
                # audio xuất hiện total tự cập nhật (vd 6147 -> 6823 MB).
                t = _total_bytes()
                if t:
                    total = t
                # Vẫn có thể thấp hơn thực tế -> nâng tổng theo mức đã tải (không >100%, ETA không âm).
                if total and done > total:
                    total = done
                now = _t.time()
                speed = (done - prev) / (now - prev_t) if now > prev_t else 0
                prev, prev_t = done, now
                stg = m.data["stages"].get("download", {})
                stg.update({
                    "status": "running", "downloaded": done, "total": total or None,
                    "progress": round(min(done / total * 100, 100), 1) if total else None,
                    "speed": round(speed) if speed > 0 else None,
                    "eta": round((total - done) / speed) if total and speed > 0 and total > done else None,
                    "updated_at": now,           # nhịp sống cho UI (giống asr/dịch)
                })
                m.data["stages"]["download"] = stg
                js.persist(m)
            except Exception as e:  # noqa: BLE001
                print(f"[monitor] tick lỗi (bỏ qua): {e}")

    threading.Thread(target=_monitor, daemon=True).start()

    # --- Thang retry. Bilibili hay ngắt kết nối lúc GẦN XONG (EOF) + lỗi TLS
    # Windows (0x80090301) khi đa kết nối. CHÌA KHOÁ: aria2c RESUME được từ .part
    # (nó tự nói "will resume if restarted") -> cứ chạy lại aria2c là tải tiếp phần
    # còn thiếu, KHÔNG mất phần đã tải. Vậy nên thử aria2c NHIỀU LẦN (1 kết nối cho
    # ổn định), mỗi lần đi thêm được một khúc tới khi xong.
    #
    # TUYỆT ĐỐI KHÔNG xoá mảnh / KHÔNG 'tải mới' -> tránh vứt bỏ 6GB video đã tải.
    import copy
    aria2c_solo = copy.deepcopy(ydl_opts)
    apply_speed_opts(aria2c_solo, conn=1)

    native_opts = copy.deepcopy(ydl_opts)     # cứu cánh cuối: SSL Python (né SChannel),
    native_opts.pop("external_downloader", None)   # vẫn RESUME (giữ .part), không xoá
    native_opts.pop("external_downloader_args", None)

    # aria2c nhanh (video 6GB) -> vài lần resume -> NATIVE (SSL Python) cứu byte cuối.
    # Bilibili hay ngắt kết nối lúc audio gần xong: aria2c dính TLS Windows 80090301,
    # còn native resume thì 416 (offset file sparse sai). Nên tới native ta XOÁ mảnh dở
    # (thường là audio) rồi native TẢI FRESH -> né cả hai lỗi. Video xong vẫn được giữ.
    # Cuối thang: hạ độ phân giải (480→360) nếu CDN vẫn đứt file lớn.
    plans = [
        (f"aria2c x{conn}", ydl_opts),
        ("aria2c x1 (resume)", aria2c_solo),
        ("aria2c x1 (resume)", aria2c_solo),
        ("aria2c x1 (resume)", aria2c_solo),
        ("native (fresh mảnh dở)", native_opts),
    ]
    # Fallback chất lượng thấp hơn: CHỈ khi user không ép DOWNLOAD_MAX_HEIGHT.
    # (Trước đây ép 1080 vẫn tụt 480→360 — sai ý người dùng.)
    if user_cap is None:
        if not max_h or max_h > 480:
            low = copy.deepcopy(native_opts)
            low["format"] = _format_sel(480)
            plans.append(("native ≤480p", low))
        if not max_h or max_h > 360:
            low2 = copy.deepcopy(native_opts)
            low2["format"] = _format_sel(360)
            plans.append(("native ≤360p", low2))
    elif prefer_hevc:
        # Đã ưu tiên HEVC ở opts chính — fallback cùng độ phân giải sang AVC
        avc_opts = copy.deepcopy(native_opts)
        avc_opts["format"] = _format_sel(user_cap or max_h, hevc=False)
        avc_opts["format_sort"] = FORMAT_SORT
        avc_aria = copy.deepcopy(aria2c_solo)
        avc_aria["format"] = _format_sel(user_cap or max_h, hevc=False)
        avc_aria["format_sort"] = FORMAT_SORT
        plans.append(("native AVC cùng độ phân giải", avc_opts))
        plans.append(("aria2c x1 AVC", avc_aria))
        plans.append(("native AVC retry", avc_opts))
    else:
        # Không hạ chiều cao — đổi codec sang HEVC nếu AVC CDN đứt
        hevc_opts = copy.deepcopy(native_opts)
        hevc_opts["format"] = _format_sel(user_cap or max_h, hevc=True)
        hevc_opts["format_sort"] = ["res", "br"]
        plans.append(("native HEVC cùng độ phân giải", hevc_opts))
        plans.append(("native (retry cùng format)", native_opts))
        plans.append(("aria2c x1 (retry AVC)", aria2c_solo))
        plans.append(("native (retry cùng format)", native_opts))
    info = None
    last_err = None
    stopped = False
    try:
        for i, (kind, opts) in enumerate(plans, 1):
            if _is_stopped(work_dir):        # có yêu cầu 'tải lại từ đầu' -> nhường
                stopped = True
                break
            try:
                if "≤" in kind or "HEVC" in kind or "AVC" in kind:
                    # Đổi format/codec → mảnh cũ vô dụng
                    rm = _clean_incomplete_fragments(work_dir)
                    for f in list(work_dir.glob("source.f*")):
                        try:
                            f.unlink(); rm.append(f.name)
                        except OSError:
                            pass
                    if rm:
                        print(f"[clean] đổi format {kind}, xoá mảnh cũ: {rm}")
                elif kind.startswith("native"):
                    # Xoá mảnh DỞ (audio kẹt) -> native tải fresh (né 80090301 + 416).
                    rm = _clean_incomplete_fragments(work_dir)
                    if rm:
                        print(f"[clean] xoá mảnh dở, native tải lại: {rm}")
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                err = str(e)
                # CDN URL chết: chỉ đọc vài trăm byte của cả file → đừng resume .part cũ
                if "378 bytes read" in err or (
                        "bytes read" in err and "more expected" in err and "Giving up" in err):
                    rm = _clean_incomplete_fragments(work_dir)
                    for f in list(work_dir.glob("source.f*")):
                        try:
                            if f.suffix.lower() in (".part",) or str(f).endswith(".part"):
                                f.unlink(); rm.append(f.name)
                            elif f.name.endswith(".part") or ".part." in f.name:
                                f.unlink(); rm.append(f.name)
                        except OSError:
                            pass
                    # xoá mọi .part còn sót
                    for f in list(work_dir.glob("*.part")) + list(work_dir.glob("*.part.aria2")):
                        try:
                            f.unlink(); rm.append(f.name)
                        except OSError:
                            pass
                    if rm:
                        print(f"[clean] CDN chết (378/partial read) → xoá để lấy URL mới: {rm}")
                print(f"[retry] tải lỗi ({kind} {i}/{len(plans)}): {err[:160]} — thử lại")
        if stopped:
            print(f"[stop] tải cũ job {work_dir.name} dừng, nhường cho bản tải-lại")
            return {}                        # KHÔNG mark_failed để không đè bản mới
        if info is None:
            m.mark_failed(STAGE, last_err)
            raise RuntimeError(f"Tải thất bại sau {len(plans)} lần: {last_err}")
    finally:
        _stop.set()

    # --- Tìm đường dẫn file thật sau khi merge ---
    video_path = None
    reqs = info.get("requested_downloads")
    if reqs:
        video_path = reqs[0].get("filepath")
    if not video_path:
        video_path = str(work_dir / "source.mp4")
    if not Path(video_path).exists():
        cands = [
            c for c in sorted(work_dir.glob("source.*"))
            if c.suffix.lower() not in _IMG_EXT
        ]
        if cands:
            video_path = str(cands[0])
    video_path = str(Path(video_path))

    meta = {
        "platform": platform,
        "source_url": url,
        "title": info.get("title"),
        "id": info.get("id"),
        "duration_sec": info.get("duration"),
        "uploader": info.get("uploader") or info.get("uploader_id"),
        "width": info.get("width"),
        "height": info.get("height"),
        "vcodec": info.get("vcodec"),
        "webpage_url": info.get("webpage_url"),
    }
    (work_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = m.mark_done(STAGE, video_path=video_path, **meta)
    print(f"[ok] Tải xong: {video_path}")
    print(
        f"     {meta.get('title')} | {meta.get('height')}p "
        f"| {meta.get('duration_sec')}s | {meta.get('vcodec')} | {platform}"
    )
    # agentK 2026-11-12: preflight nguồn ngay sau tải (VIDEO_PREFLIGHT=1) — lỗi audio → chuẩn hoá;
    # không cứu được → stage download failed với lý do rõ (không để ASR fail mã 1).
    try:
        from engines.source_preflight import PreflightError, after_download
    except ImportError:
        return result
    try:
        if after_download(work_dir, video_path) is not None:
            result = Manifest(work_dir).stage(STAGE) or result
    except PreflightError as e:
        js.mark_failed(work_dir, STAGE, e)
        raise RuntimeError(str(e)) from e
    except Exception as e:  # noqa: BLE001
        print(f"[preflight] lỗi bất ngờ ({type(e).__name__}: {e}) — bỏ qua, dùng nguồn gốc")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Stage 1 — tải video cho pipeline dịch & lồng tiếng."
    )
    ap.add_argument("url", help="Link video (Douyin/Bilibili/TikTok/...)")
    ap.add_argument("--job", default=None,
                    help="Job id / tên thư mục. Mặc định = hash URL (để resume).")
    ap.add_argument("--work-root", default="work", help="Thư mục gốc chứa các job.")
    ap.add_argument("--cookies", default=None, help="Đường dẫn file cookies.txt cụ thể.")
    ap.add_argument("--cookies-dir", default="cookies",
                    help="Thư mục chứa cookie theo nền tảng (bilibili_cookies.txt...).")
    ap.add_argument("--cookies-from-browser", default=None,
                    help="Lấy cookie từ browser: chrome / edge / firefox ...")
    ap.add_argument("--proxy", default=None, help="http://user:pass@ip:port")
    ap.add_argument("--force", action="store_true", help="Tải lại kể cả khi đã có.")
    args = ap.parse_args(argv)

    job = args.job or job_id_from_url(args.url)
    work_dir = Path(args.work_root) / job
    print(f"[job] {job}  ->  {work_dir}")

    download_video(
        url=args.url,
        work_dir=work_dir,
        cookies=args.cookies,
        cookies_dir=args.cookies_dir,
        cookies_from_browser=args.cookies_from_browser,
        proxy=args.proxy,
        force=args.force,
    )


if __name__ == "__main__":
    main()
