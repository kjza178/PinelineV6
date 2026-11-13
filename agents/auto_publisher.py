"""
auto_publisher.py — Tự động hoá toàn trình: Sau khi render xong → Tự tạo Metadata SEO
& Thumbnail 16:9 bằng Aurora AI → Tự Upload trực tiếp ngầm lên YouTube qua API.
"""
from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
import json
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import core.film_db as film_db
import agents.meta_studio as meta_studio

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_FILE = DATA_DIR / "auto_publish_config.json"
YT_DIR = _machine.yt_uploader_dir()
DEFAULT_TOKEN_FILE = YT_DIR / "accounts" / "default" / "token.json"
SHARED_CLIENT_SECRET = YT_DIR / "client_secret.json"
UPLOAD_HISTORY_FILE = YT_DIR / "upload_history.jsonl"
STATE_FILE = DATA_DIR / "auto_publish_state.json"
SCAN_LOCK_FILE = DATA_DIR / ".auto_publish_scan"
GLOBAL_PUBLISH_LOCK_FILE = DATA_DIR / ".auto_publish_global.lock"
PUBLISH_QUEUE_FILE = DATA_DIR / "auto_publish_queue.json"
QUEUE_LOCK_FILE = DATA_DIR / ".auto_publish_queue"

# YouTube thường từ chối video >~12h; 11.5h + 64GiB = soft refuse (auto) / confirm (manual).
YT_MAX_DURATION_SEC = 41400
YT_HUGE_SIZE_BYTES = 64 * 1024 * 1024 * 1024

_lock = threading.Lock()
_scan_lock = threading.Lock()
_publish_serial_lock = threading.Lock()
_active_jobs: set[str] = set()
_worker_thread: threading.Thread | None = None
_drain_thread: threading.Thread | None = None
_stop_event = threading.Event()
_active_lock_paths: set[Path] = set()

# Trạng thái tiến trình tự động (hỗ trợ liên tiến trình qua file auto_publish_state.json)
AUTO_STATE: dict[str, Any] = {
    "current_job_id": None,
    "current_title": None,
    "file_size_mb": 0,
    "current_stage": "idle",  # "metadata", "thumbnail", "uploading", "idle"
    "progress_percent": 0,
    "last_published": None,
    "last_error": None,
    "queue_count": 0,
    "queued_jobs": [],
}


def get_auto_state() -> dict[str, Any]:
    """Lấy trạng thái tiến trình tự động xuất bản (đồng bộ liên tiến trình qua file)."""
    with _lock:
        if STATE_FILE.is_file():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    AUTO_STATE.update(data)
            except Exception:
                pass
        cur = dict(AUTO_STATE)
    # Bổ sung danh sách hàng chờ từ file queue liên tiến trình
    try:
        q_items = _read_publish_queue()
        q_ids = [it["job_id"] for it in q_items if it.get("job_id") and it.get("job_id") != cur.get("current_job_id")]
        cur["queued_jobs"] = q_ids
        if q_ids:
            cur["queue_count"] = max(int(cur.get("queue_count") or 0), len(q_ids))
    except Exception:
        pass
    return cur


def update_auto_state(**kwargs) -> dict[str, Any]:
    """Cập nhật trạng thái tiến trình và ghi đồng bộ ra file cho các tiến trình khác đọc.
    Luôn đọc lại STATE_FILE trước khi merge để tiến trình phụ (sidecar) không ghi đè
    trạng thái đang upload của tiến trình chính (server).
    """
    with _lock:
        if STATE_FILE.is_file():
            try:
                disk_data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                if isinstance(disk_data, dict):
                    AUTO_STATE.update(disk_data)
            except Exception:
                pass
        AUTO_STATE.update(kwargs)
        cur = dict(AUTO_STATE)
    try:
        from core.fileio import atomic_write_json
        atomic_write_json(STATE_FILE, cur)
    except Exception:
        pass
    return cur


def _read_publish_queue() -> list[dict[str, Any]]:
    if not PUBLISH_QUEUE_FILE.is_file():
        return []
    try:
        data = json.loads(PUBLISH_QUEUE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_publish_queue(items: list[dict[str, Any]]) -> None:
    try:
        from core.fileio import atomic_write_json
        atomic_write_json(PUBLISH_QUEUE_FILE, items)
    except Exception:
        pass


_SPLIT_PART_ID_RE = re.compile(r"^.+_p\d{2,}$", re.I)
_SPLIT_PART_TITLE_RE = re.compile(r"Phần\s*\d+\s*/\s*\d+", re.I)


def is_auto_split_part(job_id: str) -> bool:
    """True nếu job là mảnh auto-split (…_p01) — chỉ parent Full được upload."""
    jid = str(job_id or "").strip()
    if not jid:
        return False
    if _SPLIT_PART_ID_RE.match(jid):
        return True
    mf = ROOT / "work" / jid / "manifest.json"
    if not mf.is_file():
        return False
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return False
    req = data.get("request") or {}
    if req.get("parent_job_id"):
        return True
    title = str(
        req.get("title")
        or (data.get("meta") or {}).get("title")
        or req.get("part_title")
        or ""
    )
    return bool(_SPLIT_PART_TITLE_RE.search(title))


def _enqueue_job_record(
    job_id: str,
    privacy: str | None = None,
    schedule_hours: float | None = None,
    force: bool = False,
    allow_oversized: bool = False,
) -> list[dict[str, Any]]:
    """Thêm job vào hàng chờ upload liên tiến trình (data/auto_publish_queue.json)."""
    job_id = str(job_id or "").strip()
    if not job_id:
        return _read_publish_queue()
    if is_auto_split_part(job_id):
        print(
            f"[auto-publish] Bỏ qua enqueue «{job_id}» — part auto-split (chỉ upload bản Full sau merge).",
            flush=True,
        )
        return _read_publish_queue()
    try:
        from core.fileio import file_lock
        with file_lock(QUEUE_LOCK_FILE, timeout=5.0, stale_sec=30.0):
            items = _read_publish_queue()
            existing = next((it for it in items if it.get("job_id") == job_id), None)
            if existing:
                if privacy is not None:
                    existing["privacy"] = privacy
                if schedule_hours is not None:
                    existing["schedule_hours"] = schedule_hours
                if force:
                    existing["force"] = True
                if allow_oversized:
                    existing["allow_oversized"] = True
            else:
                items.append({
                    "job_id": job_id,
                    "privacy": privacy,
                    "schedule_hours": schedule_hours,
                    "force": bool(force),
                    "allow_oversized": bool(allow_oversized),
                    "enqueued_at": time.time(),
                })
            # Sắp xếp hàng chờ theo thứ tự bộ & phần (P1 -> Pn)
            try:
                from core.job_queue import job_sort_key
                items.sort(key=lambda x: job_sort_key(x.get("job_id") or ""))
            except Exception:
                pass
            _write_publish_queue(items)
            return items
    except Exception:
        items = _read_publish_queue()
        if not any(it.get("job_id") == job_id for it in items):
            items.append({
                "job_id": job_id,
                "privacy": privacy,
                "schedule_hours": schedule_hours,
                "force": bool(force),
                "allow_oversized": bool(allow_oversized),
                "enqueued_at": time.time(),
            })
            _write_publish_queue(items)
        return items


def _dequeue_job_record(job_id: str) -> list[dict[str, Any]]:
    """Xoá job khỏi hàng chờ upload sau khi đã xử lý xong."""
    job_id = str(job_id or "").strip()
    try:
        from core.fileio import file_lock
        with file_lock(QUEUE_LOCK_FILE, timeout=5.0, stale_sec=30.0):
            items = [it for it in _read_publish_queue() if it.get("job_id") != job_id]
            _write_publish_queue(items)
            return items
    except Exception:
        items = [it for it in _read_publish_queue() if it.get("job_id") != job_id]
        _write_publish_queue(items)
        return items


def _sync_queue_state_badge() -> None:
    """Đồng bộ số lượng và danh sách job đang chờ upload vào auto_publish_state.json."""
    try:
        items = _read_publish_queue()
        st = get_auto_state()
        cur_jid = st.get("current_job_id") if st.get("current_stage") != "idle" else None
        waiting_ids = [it["job_id"] for it in items if it.get("job_id") and it.get("job_id") != cur_jid]
        update_auto_state(queue_count=len(waiting_ids), queued_jobs=waiting_ids)
    except Exception:
        pass


def touch_active_locks() -> None:
    """Làm mới mtime của các file lock đang giữ khi upload chunk lớn (tránh bị coi là stale)."""
    now = time.time()
    for lp in list(_active_lock_paths):
        try:
            if lp.exists():
                os.utime(lp, (now, now))
        except OSError:
            pass


def is_pid_alive(pid: int) -> bool:
    """Kiểm tra xem PID có đang thực sự chạy trên Windows không."""
    if pid <= 0:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if h:
            kernel32.CloseHandle(h)
            return True
        return False
    except Exception:
        return False


def get_job_lock_path(job_id: str) -> Path:
    """Xác định đường dẫn file lock của job (ưu tiên trong thư mục work/<job_id>)."""
    w_dir = ROOT / "work" / job_id
    if w_dir.is_dir():
        return w_dir / ".upload.lock"
    return DATA_DIR / f".upload_{job_id}.lock"


def acquire_job_lock(job_id: str, stale_sec: float = 14400.0) -> tuple[int | None, Path, int | None]:
    """
    Chiếm lock liên tiến trình cho job_id.
    Trả về (fd, lock_path, owner_pid).
    Nếu fd is not None: chiếm lock thành công.
    Nếu fd is None: job đang bị tiến trình owner_pid giữ lock.
    """
    lock_path = get_job_lock_path(job_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            content = lock_path.read_text(encoding="utf-8").strip().split()
            owner_pid = int(content[0]) if content else -1
            mtime = lock_path.stat().st_mtime
            if is_pid_alive(owner_pid) and (time.time() - mtime < stale_sec):
                return None, lock_path, owner_pid
            # PID đã chết hoặc lock quá hạn (stale) -> xoá lock cũ
            try:
                lock_path.unlink()
            except OSError:
                pass
        except Exception:
            pass

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, f"{os.getpid()} {time.time():.0f} {job_id}\n".encode("utf-8"))
        _active_lock_paths.add(lock_path)
        return fd, lock_path, os.getpid()
    except (FileExistsError, PermissionError):
        try:
            content = lock_path.read_text(encoding="utf-8").strip().split()
            owner_pid = int(content[0]) if content else -1
            return None, lock_path, owner_pid
        except Exception:
            return None, lock_path, -1


def release_job_lock(fd: int | None, lock_path: Path | None) -> None:
    """Giải phóng lock liên tiến trình cho job."""
    if lock_path:
        _active_lock_paths.discard(lock_path)
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    if lock_path and lock_path.exists():
        try:
            lock_path.unlink()
        except OSError:
            pass


def acquire_global_publish_lock(
    job_id: str,
    wait_timeout: float = 14400.0,
    stale_sec: float = 14400.0,
) -> tuple[int | None, Path]:
    """
    Chiếm khoá upload toàn cục liên tiến trình để đảm bảo tại mọi thời điểm
    CHỈ CÓ DUY NHẤT 1 JOB được tạo metadata/thumb/upload.
    Nếu có job trước đang upload dở, hàm này sẽ CHỜ đến khi job trước hoàn tất,
    tuyệt đối không ghi đè hoặc bỏ qua job trước hay job sau.
    """
    lock_path = GLOBAL_PUBLISH_LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    logged_wait = False

    while not _stop_event.is_set():
        if lock_path.exists():
            try:
                content = lock_path.read_text(encoding="utf-8").strip().split()
                owner_pid = int(content[0]) if content else -1
                owner_job = content[2] if len(content) > 2 else "?"
                mtime = lock_path.stat().st_mtime
                if not is_pid_alive(owner_pid) or (time.time() - mtime > stale_sec):
                    try:
                        lock_path.unlink()
                    except OSError:
                        pass
                else:
                    if not logged_wait:
                        print(
                            f"[auto-publish] Job {job_id} đã xếp vào hàng đợi upload — đang chờ job {owner_job} (PID {owner_pid}) upload xong...",
                            flush=True,
                        )
                        logged_wait = True
                        _sync_queue_state_badge()
                    if time.time() - t0 > wait_timeout:
                        return None, lock_path
                    time.sleep(2.0)
                    continue
            except Exception:
                pass

        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, f"{os.getpid()} {time.time():.0f} {job_id}\n".encode("utf-8"))
            _active_lock_paths.add(lock_path)
            return fd, lock_path
        except (FileExistsError, PermissionError):
            if time.time() - t0 > wait_timeout:
                return None, lock_path
            time.sleep(1.0)

    return None, lock_path


def release_global_publish_lock(fd: int | None, lock_path: Path | None) -> None:
    """Giải phóng khoá upload toàn cục liên tiến trình."""
    if lock_path:
        _active_lock_paths.discard(lock_path)
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    if lock_path and lock_path.exists():
        try:
            lock_path.unlink()
        except OSError:
            pass


def is_any_job_uploading(stale_sec: float = 14400.0) -> tuple[bool, str | None]:
    """Kiểm tra xem có bất kỳ job nào đang trong quá trình tự động xuất bản không.
    Trả về (is_busy, active_job_id).
    """
    with _lock:
        if _active_jobs:
            return True, next(iter(_active_jobs))
    if GLOBAL_PUBLISH_LOCK_FILE.exists():
        try:
            content = GLOBAL_PUBLISH_LOCK_FILE.read_text(encoding="utf-8").strip().split()
            owner_pid = int(content[0]) if content else -1
            owner_job = content[2] if len(content) > 2 else None
            mtime = GLOBAL_PUBLISH_LOCK_FILE.stat().st_mtime
            if is_pid_alive(owner_pid) and (time.time() - mtime < stale_sec):
                return True, owner_job
        except Exception:
            pass
    st = get_auto_state()
    cur_jid = st.get("current_job_id")
    if cur_jid and st.get("current_stage") not in (None, "idle"):
        if is_job_uploading(cur_jid):
            return True, cur_jid
    return False, None


def is_job_uploading(job_id: str) -> bool:
    """Kiểm tra job có đang được xử lý upload ở bất kỳ tiến trình nào không."""
    with _lock:
        if job_id in _active_jobs:
            return True
    lock_path = get_job_lock_path(job_id)
    if lock_path.exists():
        try:
            content = lock_path.read_text(encoding="utf-8").strip().split()
            owner_pid = int(content[0]) if content else -1
            mtime = lock_path.stat().st_mtime
            if is_pid_alive(owner_pid) and (time.time() - mtime < 14400.0):
                return True
        except Exception:
            pass
    return False


def load_config() -> dict[str, Any]:
    """Tải cấu hình tự động upload."""
    default_cfg = {
        "enabled": True,                 # Bật/tắt tự động upload sau render
        "privacy": "unlisted",           # "unlisted", "public", "private"
        "schedule_hours": 0.0,           # 0 = đăng ngay theo privacy; >0 = hẹn giờ sau N tiếng (tự chuyển private + publishAt)
        "generate_ai_thumb": True,       # Dùng Aurora sinh ảnh 16:9 điện ảnh
        "include_poster_in_thumb": True, # Đặt thêm khung poster gốc ở góc phải
        "account_id": "default",         # Tài khoản trong Z:\yt_uploader\accounts
        "category_id": "24",             # 24 = Entertainment, 1 = Film & Animation
    }
    if not CONFIG_FILE.is_file():
        return default_cfg
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        default_cfg.update(cfg)
    except Exception:
        pass
    return default_cfg


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Lưu cấu hình tự động upload."""
    current = load_config()
    current.update(cfg)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def get_youtube_service(account_id: str = "default"):
    r"""Lấy YouTube API service từ tài khoản trong Z:\yt_uploader\accounts."""
    tok_path = YT_DIR / "accounts" / account_id / "token.json"
    if not tok_path.is_file():
        # Fallback về token cũ ở thư mục gốc nếu có
        tok_path = YT_DIR / "token.json"
    if not tok_path.is_file():
        raise FileNotFoundError(f"Chưa có Google OAuth token cho tài khoản '{account_id}' tại {tok_path}")

    data = json.loads(tok_path.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(data)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        tok_path.write_text(creds.to_json(), encoding="utf-8")

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def format_youtube_title(ai_title: str, clean_name: str = "", season: int | None = None) -> str:
    """Chuẩn hoá tiêu đề YouTube theo format chuẩn: Phần X | [Tên phim] - [Hook].
    Tránh cắt ngang từ khi dài quá 95 ký tự.
    """
    title = str(ai_title or "").strip().lstrip("⭐").strip()
    s_num = season
    if not s_num:
        m_s = re.search(r'(?:phần|phan|mùa|mua|s|p|tập|tap)\s*([0-9]+)', (clean_name + " " + title), re.IGNORECASE)
        if m_s:
            s_num = int(m_s.group(1))

    # Loại bỏ hoàn toàn chữ Hán khỏi title
    if re.search(r'[\u4e00-\u9fff]', title):
        title = re.sub(r'[\u4e00-\u9fff]+', '', title)
        title = re.sub(r'\s{2,}', ' ', title).strip()

    title = re.sub(r'\*\*', '', title).strip()

    def _truncate_word_safe(text: str, max_len: int = 95) -> str:
        t = text.strip()
        if len(t) <= max_len:
            return t
        clipped = t[:max_len].rsplit(" ", 1)[0].rstrip(" ,.-:|–—")
        return clipped or t[:max_len]

    if s_num == -1:
        prefix = "Phần Cuối | "
    elif s_num:
        prefix = f"Phần {s_num} | "
    else:
        prefix = "Trọn Bộ | "
    # Nếu đã có định dạng Phần X | hoặc Trọn Bộ | thì chuẩn hoá và đổi : thành -
    if re.match(r'^(?:Phần|Tập|Mùa|Trọn\s*Bộ)\s*(?:\d+|Cuối)?\s*[|:\-–—]\s*', title, re.IGNORECASE):
        # Đổi dấu hai chấm thành gạch ngang
        title = re.sub(r'^((?:Phần|Tập|Mùa|Trọn\s*Bộ)\s*(?:\d+|Cuối)?\s*[|:\-–—]\s*[^:\-–—]+):\s*', r'\1 - ', title)
        if s_num == -1:
            title = re.sub(
                r'^(?:Phần|Tập|Mùa|Trọn\s*Bộ)\s*(?:\d+|Cuối)?\s*[|:\-–—]\s*',
                'Phần Cuối | ',
                title,
                flags=re.I,
            )
        elif s_num:
            title = re.sub(r'^(?:Phần|Tập|Mùa|Trọn\s*Bộ)\s*(?:\d+|Cuối)?\s*[|:\-–—]\s*', f'Phần {s_num} | ', title, flags=re.I)
        title = re.sub(r'\s{2,}', ' ', title).strip()
        return _truncate_word_safe(title, 95)

    # Nếu title đã có số phần trong câu (chuẩn cấu trúc Hán Việt, vd "... Phần 2"):
    if s_num and re.search(rf'\b(?:phần|mùa|tập)\s*{s_num}\b', title, re.IGNORECASE):
        return _truncate_word_safe(title, 95)

    # Bỏ các tiền tố / hậu tố P2, S2, (Phần 2) bị lặp lại trong thân câu
    clean_title = re.sub(r'\(?\s*(?:phần|phan|mùa|mua|s|p|tập|tap)\s*[0-9]+\s*\)?', '', title, flags=re.IGNORECASE)
    clean_title = re.sub(r'\s{2,}', ' ', clean_title).strip()
    clean_title = clean_title.lstrip(":-|–— ").strip()

    if ":" in clean_title and " - " not in clean_title:
        clean_title = clean_title.replace(":", " -", 1)

    if clean_name and clean_name.lower() not in clean_title.lower():
        clean_title = f"{clean_name} - {clean_title}"

    final = f"{prefix}{clean_title}"
    return _truncate_word_safe(final, 95)


def upload_to_youtube(
    video_path: str | Path,
    title: str,
    description: str,
    tags: list[str] | str,
    thumb_path: str | Path | None = None,
    privacy: str = "unlisted",
    publish_at: str | None = None,
    account_id: str = "default",
    category_id: str = "24",
) -> dict[str, Any]:
    """Upload trực tiếp video + thumbnail lên YouTube qua API v3."""
    v_path = Path(video_path)
    if not v_path.is_file():
        return {"ok": False, "error": f"Không tìm thấy file video: {video_path}"}

    try:
        yt = get_youtube_service(account_id)
    except Exception as e:
        return {"ok": False, "error": f"Lỗi khởi tạo YouTube Service: {e}"}

    # Chuẩn hoá và làm sạch tags (YouTube cấm '#', '<', '>', quotes, và giới hạn độ dài)
    raw_tags = tags if isinstance(tags, list) else [t.strip() for t in str(tags).split(",") if t.strip()]
    clean_tags = []
    total_tag_len = 0
    for t in raw_tags:
        t_str = re.sub(r'^[#＃]+', '', str(t).strip())
        t_str = re.sub(r'[<>"\'\n\r\t]', '', t_str).strip()
        if not t_str or len(t_str) < 2 or len(t_str) > 70:
            continue
        if total_tag_len + len(t_str) + 1 > 380:
            break
        if t_str not in clean_tags:
            clean_tags.append(t_str)
            total_tag_len += len(t_str) + 1

    # Chuẩn bị body metadata
    is_scheduled = bool(publish_at)
    status_dict: dict[str, Any] = {
        "privacyStatus": "private" if is_scheduled else privacy,
        "selfDeclaredMadeForKids": False,
    }
    if is_scheduled:
        status_dict["publishAt"] = publish_at

    body = {
        "snippet": {
            "title": str(title)[:100].strip(),
            "description": str(description)[:5000].strip(),
            "tags": clean_tags[:30],
            "categoryId": str(category_id or "24"),
            "defaultLanguage": "vi",
        },
        "status": status_dict,
    }

    # Upload video bằng Resumable chunks 256MB
    print(f"[auto-upload] Bắt đầu upload {v_path.name} ({v_path.stat().st_size // (1024*1024)} MB) lên YouTube...", flush=True)
    media = MediaFileUpload(str(v_path), chunksize=256 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    try:
        while response is None:
            touch_active_locks()
            status, response = req.next_chunk()
            touch_active_locks()
            if status:
                prog = int(status.progress() * 100)
                update_auto_state(progress_percent=prog, current_stage="uploading")
                print(f"[auto-upload] Đang tải lên: {prog}%", flush=True)
    except Exception as e_up:
        err_str = str(e_up)
        if "invalidTags" in err_str or "keywords" in err_str:
            print("[auto-upload] YouTube báo invalidTags -> Tự động bỏ tags và thử lại...", flush=True)
            body["snippet"]["tags"] = []
            media = MediaFileUpload(str(v_path), chunksize=256 * 1024 * 1024, resumable=True)
            req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                touch_active_locks()
                status, response = req.next_chunk()
                touch_active_locks()
                if status:
                    prog = int(status.progress() * 100)
                    update_auto_state(progress_percent=prog, current_stage="uploading")
                    print(f"[auto-upload] Đang tải lên: {prog}%", flush=True)
        else:
            raise

    video_id = response.get("id")
    if not video_id:
        return {"ok": False, "error": "Upload thành công nhưng không lấy được Video ID từ YouTube"}

    yt_url = f"https://youtu.be/{video_id}"
    print(f"[auto-upload] Upload hoàn tất! URL: {yt_url}", flush=True)

    # Đặt Thumbnail cho video nếu có
    if thumb_path and Path(thumb_path).is_file():
        try:
            print(f"[auto-upload] Đang upload thumbnail: {thumb_path}...", flush=True)
            yt.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumb_path), mimetype="image/jpeg", resumable=True)
            ).execute()
            print("[auto-upload] Đã đặt thumbnail thành công!", flush=True)
        except Exception as e_th:
            print(f"[auto-upload] Cảnh báo lỗi upload thumbnail (video vẫn up thành công): {e_th}", flush=True)

    return {
        "ok": True,
        "video_id": video_id,
        "yt_url": yt_url,
        "title": title,
        "privacy": body["status"]["privacyStatus"],
        "publish_at": publish_at,
    }


def _revert_job_to_done(job_id: str, error_msg: str | None = None) -> None:
    """Hoàn trả trạng thái job về 'done' khi quá trình upload bị lỗi hoặc gián đoạn để không bị kẹt ở 'uploading'."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        from core.manifest import Manifest
        w_dir = ROOT / "work" / job_id
        if (w_dir / "manifest.json").is_file():
            m = Manifest(w_dir)
            if not m.data.get("yt_url"):
                m.data["upload_status"] = "failed"
                if error_msg:
                    m.data["upload_error"] = str(error_msg)
                js.persist(m)
    except Exception:
        pass

    try:
        with film_db._lock, film_db.connect() as con:
            con.execute(
                "UPDATE films SET status = 'done', updated_at = ? WHERE job_id = ? AND (yt_url IS NULL OR yt_url = '')",
                (now_utc, job_id),
            )
    except Exception:
        pass


def _oversized_youtube_reasons(row: dict | None, out_path: str | Path | None) -> list[str]:
    """Lý do file quá dài/nặng cho YouTube (dùng từ chối auto-publish)."""
    reasons: list[str] = []
    try:
        dur = float((row or {}).get("duration_sec") or 0)
    except Exception:
        dur = 0.0
    if dur > YT_MAX_DURATION_SEC:
        reasons.append(f"duration ~{dur/3600:.1f}h > 11.5h")
    try:
        p = Path(out_path) if out_path else None
        size = p.stat().st_size if p and p.is_file() else 0
    except Exception:
        size = 0
    if size >= YT_HUGE_SIZE_BYTES:
        reasons.append(f"size ~{size/(1024**3):.1f} GiB ≥ 64 GiB")
    return reasons


def enqueue_publish(
    job_id: str,
    privacy: str | None = None,
    schedule_hours: float | None = None,
    force: bool = False,
    allow_oversized: bool = False,
) -> dict[str, Any]:
    """
    Đưa job vừa hoàn thành vào hàng đợi xuất bản tuần tự (không làm đứt quãng job đang upload).
    """
    job_id = str(job_id or "").strip()
    if not job_id:
        return {"ok": False, "error": "missing job_id"}
    if is_auto_split_part(job_id):
        return {
            "ok": False,
            "error": "skip_split_part",
            "skipped_split_part": True,
            "job_id": job_id,
            "message": "Part auto-split (Phần N/N) không đưa vào upload — chờ merge Full.",
        }

    try:
        film_db.sync_one(job_id)
    except Exception as e_sync:
        print(f"[auto-publish] Cảnh báo sync_one({job_id}): {e_sync}", flush=True)

    # Soft-refuse file quá dài trước khi xếp hàng (auto); manual UI truyền allow_oversized=True sau confirm.
    if not allow_oversized:
        row = film_db.get(job_id) or {}
        out_path = row.get("output_path")
        if not out_path or not Path(str(out_path)).is_file():
            cand = ROOT / "work" / job_id / "output.mp4"
            out_path = str(cand) if cand.is_file() else out_path
        reasons = _oversized_youtube_reasons(row, out_path)
        if reasons:
            msg = "Từ chối auto-publish: file quá dài/nặng cho YouTube (" + "; ".join(reasons) + ")"
            print(f"[auto-publish] {job_id}: {msg}", flush=True)
            return {
                "ok": False,
                "error": msg,
                "refused_oversized": True,
                "need_confirm": True,
                "warnings": reasons,
                "job_id": job_id,
            }

    items = _enqueue_job_record(
        job_id,
        privacy=privacy,
        schedule_hours=schedule_hours,
        force=force,
        allow_oversized=allow_oversized,
    )
    _sync_queue_state_badge()
    print(f"[auto-publish] Đã xếp job {job_id} vào hàng đợi xuất bản (tổng hàng chờ: {len(items)}).", flush=True)
    _drain_publish_queue_async()
    return {"ok": True, "queued": True, "job_id": job_id, "queue_size": len(items)}


def _drain_publish_queue_async() -> None:
    """Khởi chạy luồng nền xử lý tuần tự từng job trong hàng đợi nếu chưa có luồng drain nào đang chạy."""
    global _drain_thread
    with _lock:
        if _drain_thread and _drain_thread.is_alive():
            return
        _drain_thread = threading.Thread(
            target=_drain_publish_queue_loop,
            name="auto-publish-drain",
            daemon=True,
        )
        _drain_thread.start()


def _is_yt_upload_limit_error(err: Any) -> bool:
    """True nếu YouTube báo hết hạn mức upload ngày (uploadLimitExceeded)."""
    s = str(err or "").lower()
    return (
        "uploadlimitexceeded" in s
        or "exceeded the number of videos they may upload" in s
        or ("upload" in s and "limit" in s and "exceed" in s)
    )


def _drain_publish_queue_loop() -> None:
    """Vòng lặp lấy từng job từ hàng đợi ra xuất bản tuần tự cho đến khi hết."""
    while not _stop_event.is_set():
        items = _read_publish_queue()
        if not items:
            _sync_queue_state_badge()
            break
        item = items[0]
        jid = str(item.get("job_id") or "").strip()
        if not jid:
            _dequeue_job_record("")
            continue
        try:
            res = publish_job(
                jid,
                privacy=item.get("privacy"),
                schedule_hours=item.get("schedule_hours"),
                force=bool(item.get("force", False)),
                allow_oversized=bool(item.get("allow_oversized", False)),
            )
            if isinstance(res, dict) and res.get("stop_queue"):
                print(
                    f"[auto-publish] DỪNG hàng đợi upload — YouTube uploadLimitExceeded "
                    f"(job {jid}). Các job còn lại giữ trong queue, không upload tiếp.",
                    flush=True,
                )
                break
        except Exception as e:
            print(f"[auto-publish] Lỗi khi xử lý hàng đợi cho job {jid}: {e}", flush=True)
            if _is_yt_upload_limit_error(e):
                update_auto_state(
                    last_error=f"Dừng upload: YouTube uploadLimitExceeded ({jid}): {e}",
                    current_stage="idle",
                    current_job_id=None,
                    progress_percent=0,
                )
                print(
                    f"[auto-publish] DỪNG hàng đợi — uploadLimitExceeded. Giữ job trong queue.",
                    flush=True,
                )
                break
            _dequeue_job_record(jid)
            _sync_queue_state_badge()
        time.sleep(1.0)


def publish_job(
    job_id: str,
    privacy: str | None = None,
    schedule_hours: float | None = None,
    force: bool = False,
    allow_oversized: bool = False,
) -> dict[str, Any]:
    """
    Quy trình toàn diện: Sinh Metadata qua Aurora → Dựng Thumbnail 16:9 → Upload lên YouTube.
    Được bảo vệ bởi khoá tuần tự toàn cục (_publish_serial_lock + GLOBAL_PUBLISH_LOCK_FILE)
    để khi 2 job xong sát nhau, job sau sẽ xếp hàng đợi job trước upload xong 100% mới chạy tiếp.
    """
    job_id = str(job_id).strip()
    if not job_id:
        return {"ok": False, "error": "missing job_id"}
    if is_auto_split_part(job_id):
        _dequeue_job_record(job_id)
        _sync_queue_state_badge()
        return {
            "ok": False,
            "error": "skip_split_part",
            "skipped_split_part": True,
            "job_id": job_id,
            "message": "Part auto-split không upload — chờ merge Full.",
        }

    # Đảm bảo thông tin mới nhất của job đã có trong films.db trước khi kiểm tra
    try:
        film_db.sync_one(job_id)
    except Exception:
        pass

    # Soft-refuse oversized trước khi chiếm lock (manual UI set allow_oversized sau confirm).
    if not allow_oversized:
        row0 = film_db.get(job_id) or {}
        out0 = row0.get("output_path")
        if not out0 or not Path(str(out0)).is_file():
            cand0 = ROOT / "work" / job_id / "output.mp4"
            out0 = str(cand0) if cand0.is_file() else out0
        reasons0 = _oversized_youtube_reasons(row0, out0)
        if reasons0:
            msg = "Từ chối publish: file quá dài/nặng cho YouTube (" + "; ".join(reasons0) + ")"
            return {
                "ok": False,
                "error": msg,
                "refused_oversized": True,
                "need_confirm": True,
                "warnings": reasons0,
                "job_id": job_id,
            }

    # Ghi nhận vào hàng chờ liên tiến trình để UI hiển thị số lượng phim đang chờ
    _enqueue_job_record(
        job_id,
        privacy=privacy,
        schedule_hours=schedule_hours,
        force=force,
        allow_oversized=allow_oversized,
    )
    _sync_queue_state_badge()

    with _lock:
        if job_id in _active_jobs:
            print(f"[auto-publish] Job {job_id} đã nằm trong luồng chờ/xử lý của tiến trình hiện tại.", flush=True)
            return {"ok": True, "queued": True, "skipped": True, "reason": "job already queued/active in current process"}
        _active_jobs.add(job_id)

    global_fd: int | None = None
    global_lp: Path | None = None
    lock_fd: int | None = None
    lock_path: Path | None = None
    result: dict[str, Any] = {"ok": False}

    try:
        # 1. Xếp hàng tuần tự trong cùng tiến trình & liên tiến trình (chờ job trước upload xong)
        with _publish_serial_lock:
            global_fd, global_lp = acquire_global_publish_lock(job_id)
            if global_fd is None:
                result = {"ok": False, "error": f"Quá thời gian chờ khoá upload toàn cục cho job {job_id}"}
                return result

            # 2. Chiếm lock riêng của job_id
            lock_fd, lock_path, owner_pid = acquire_job_lock(job_id)
            if lock_fd is None:
                print(f"[auto-publish] Job {job_id} đang được upload bởi tiến trình PID {owner_pid}, bỏ qua.", flush=True)
                result = {"ok": False, "skipped": True, "reason": f"job currently being published by PID {owner_pid}"}
                return result

            result = _publish_job_internal(job_id, privacy=privacy, schedule_hours=schedule_hours, force=force)
            return result
    finally:
        release_job_lock(lock_fd, lock_path)
        release_global_publish_lock(global_fd, global_lp)
        stop_q = bool(result.get("stop_queue") or result.get("upload_limit_exceeded"))
        if stop_q:
            # Giữ job (và phần còn lại) trong queue — hết hạn mức thì upload tiếp sẽ fail hàng loạt
            print(
                f"[auto-publish] Không dequeue / không drain tiếp — uploadLimitExceeded (job {job_id}).",
                flush=True,
            )
            _sync_queue_state_badge()
        else:
            _dequeue_job_record(job_id)
            with _lock:
                _active_jobs.discard(job_id)
            _sync_queue_state_badge()
            # Nếu vẫn còn job trong hàng đợi thì kích hoạt xử lý tiếp
            if _read_publish_queue() and not _stop_event.is_set():
                _drain_publish_queue_async()
        if stop_q:
            with _lock:
                _active_jobs.discard(job_id)


def _publish_job_internal(
    job_id: str,
    privacy: str | None = None,
    schedule_hours: float | None = None,
    force: bool = False,
) -> dict[str, Any]:
    cfg = load_config()
    if not force and not cfg.get("enabled", True):
        print(f"[auto-publish] Bỏ qua job {job_id} vì tính năng tự động upload đang tắt trong cấu hình.", flush=True)
        return {"ok": False, "skipped": True, "reason": "auto_publish disabled in config"}

    privacy = privacy or cfg.get("privacy", "unlisted")
    if schedule_hours is None:
        schedule_hours = float(cfg.get("schedule_hours") or 0.0)

    # 1. Đồng bộ và kiểm tra thông tin job trong films.db và manifest.json
    try:
        film_db.sync_one(job_id)
    except Exception:
        pass

    row = film_db.get(job_id)
    if not row:
        rows = film_db.query(q=job_id, limit=5)
        row = next((r for r in rows if r.get("job_id") == job_id), None)
    if not row:
        return {"ok": False, "error": f"Không tìm thấy job {job_id} trong database"}

    mf_path = ROOT / "work" / job_id / "manifest.json"
    m_data = {}
    if mf_path.is_file():
        try:
            m_data = json.loads(mf_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    existing_yt = row.get("yt_url") or m_data.get("yt_url")
    if not force and existing_yt:
        return {"ok": True, "already_uploaded": True, "yt_url": existing_yt, "video_id": existing_yt.split("/")[-1]}

    # Tìm file video hoàn chỉnh
    out_path = row.get("output_path")
    if not out_path or not Path(out_path).is_file():
        cand = ROOT / "work" / job_id / "output.mp4"
        if not cand.is_file():
            cand = ROOT / "work" / job_id / "final.mp4"
        if cand.is_file():
            out_path = str(cand)
        else:
            return {"ok": False, "error": f"Chưa có file video hoàn chỉnh cho job {job_id}"}

    title_vi = (row.get("title_vi") or row.get("title") or m_data.get("title_vi") or "").strip()
    title_zh = (row.get("title") or "").strip()
    try:
        from core.film_db import derive_hg_name, is_junk_hg_name
        poster_name = (row.get("hg_name") or "").strip()
        if is_junk_hg_name(poster_name, job_id):
            poster_name = derive_hg_name(
                job_id,
                {"hg_name": row.get("hg_name"), "parent_job_id": (job_id.rsplit("_p", 1)[0] if "_p" in job_id else "")},
                row.get("title") or "",
            )
        if is_junk_hg_name(poster_name, job_id):
            poster_name = ""
    except Exception:
        poster_name = row.get("hg_name") or row.get("title") or row.get("series") or ""
    f_size_mb = Path(out_path).stat().st_size // (1024 * 1024) if Path(out_path).is_file() else 0

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Đánh dấu upload_status = 'uploading' vào manifest và films.db ngay từ đầu để scanner không quét trúng
    try:
        from core.manifest import Manifest
        m = Manifest(ROOT / "work" / job_id)
        m.data["upload_status"] = "uploading"
        m.data["upload_started_at"] = now_utc
        js.persist(m)
    except Exception:
        pass

    with film_db._lock, film_db.connect() as con:
        con.execute(
            "UPDATE films SET status = 'uploading', updated_at = ? WHERE job_id = ? AND (yt_url IS NULL OR yt_url = '')",
            (now_utc, job_id)
        )

    update_auto_state(
        current_job_id=job_id,
        current_title=title_vi,
        file_size_mb=f_size_mb,
        current_stage="metadata",
        progress_percent=10,
        last_error=None,
    )
    _sync_queue_state_badge()

    try:
        # 2. Kiểm tra nhận dạng Series / Group để dùng chung Master Thumbnail & Tags SEO
        series_key, series_name = meta_studio.get_series_key(job_id)
        shared_meta = meta_studio.get_series_meta(series_key, series_name=series_name)
        shared_thumb = meta_studio.get_series_thumbnail(series_key, series_name=series_name)

        thumb_filename = f"thumb_{job_id}.jpg"
        thumb_path = meta_studio.THUMB_DIR / thumb_filename

        season_no = row.get("season")
        if season_no is None:
            from core.job_queue import get_job_info
            season_no = get_job_info(job_id).get("season")
            if season_no is None:
                season_no = 1

        # Phần Cuối: giữ title_vi, không gọi generate_part_metadata(season=1) → tránh «Phần 1 | Xuyên Không…»
        is_final = (season_no == -1) or bool(re.search(r"phần\s*cuối|mùa\s*cuối", str(title_vi or ""), re.I))

        if shared_meta and shared_thumb and shared_thumb.is_file() and not is_final:
            # === CÙNG SERIES ĐÃ CÓ MẪU: DÙNG CHUNG 1 THUMBNAIL 16:9 & TAGS SEO, SINH METADATA RIÊNG CHO PHẦN {X} ===
            print(f"[auto-publish] {job_id}: 🎯 Phát hiện cùng Series «{series_name}» (Group: {series_key}) -> DÙNG CHUNG 1 Master Thumbnail 16:9 & Bộ Tags SEO của Series!", flush=True)
            import shutil
            shutil.copyfile(str(shared_thumb), str(thumb_path))
            print(f"[auto-publish] {job_id}: Đã sao chép Master Thumbnail của Series ({thumb_path.stat().st_size // 1024} KB)", flush=True)

            # Sinh Title & Description chuyên biệt cho Phần {season_no} với đầy đủ 4 khối chuẩn YouTube SEO
            print(f"[auto-publish] {job_id}: Sinh Title & Description chuẩn YouTube SEO cho Phần {season_no} của «{series_name}»...", flush=True)
            part_meta = meta_studio.generate_part_metadata(
                series_name=series_name,
                season=season_no,
                episode_info=title_vi,
                shared_meta=shared_meta,
                job_id=job_id,
                title_zh=title_zh,
            )
            final_title = format_youtube_title(part_meta.get("title", ""), clean_name=series_name, season=season_no)
            final_desc = str(part_meta.get("description") or "").replace("\\n", "\n").strip()
            final_tags = part_meta.get("tags") or shared_meta.get("tags") or [series_name, "Phim Thuyết Minh", "Manhua Tiếng Việt"]

            update_auto_state(current_stage="uploading", progress_percent=60)
        elif shared_meta and shared_thumb and shared_thumb.is_file() and is_final:
            print(f"[auto-publish] {job_id}: Phần Cuối — giữ title_vi, dùng master thumb/tags series «{series_name}»", flush=True)
            import shutil
            shutil.copyfile(str(shared_thumb), str(thumb_path))
            final_title = format_youtube_title(title_vi or series_name, clean_name=series_name, season=-1)
            final_desc = str(shared_meta.get("description") or f"{title_vi} - Phần cuối thuyết minh.").replace("\\n", "\n").strip()
            final_tags = shared_meta.get("tags") or [series_name, "Phim Thuyết Minh", "Manhua Tiếng Việt"]
            update_auto_state(current_stage="uploading", progress_percent=60)
        else:
            # === TẬP ĐẦU TIÊN CỦA SERIES: SINH MỚI BẰNG AURORA VÀ LƯU LẠI LÀM MẪU DÙNG CHUNG ===
            print(f"[auto-publish] {job_id}: Sinh bộ Metadata SEO & Master Thumbnail 16:9 mẫu cho Series «{series_name}»...", flush=True)
            meta_res = meta_studio.generate_metadata(
                title_vi=title_vi,
                title_zh=title_zh,
                info_text=f"Phim thuyết minh: {title_vi}",
                tone="giật gân, kịch tính",
                job_id=job_id,
            )

            final_title = title_vi
            if meta_res.get("best_title"):
                final_title = meta_res["best_title"]
            elif meta_res.get("titles"):
                final_title = meta_res["titles"][0]

            final_title = format_youtube_title(final_title, clean_name=title_vi, season=season_no)
            final_desc = meta_res.get("description") or f"{title_vi} - Trọn bộ thuyết minh chất lượng cao."
            final_tags = meta_res.get("tags") or [title_vi, "Phim Thuyết Minh", "Manhua Tiếng Việt"]
            # finalize đã sanitize hook + badge NEW/HOT + prompt AI có chữ 3D
            hooks = meta_res.get("hook_texts") or meta_studio.hooks_from_series_title(
                title_vi or series_name, title_zh or ""
            )
            badge_txt = meta_studio.pick_badge_text(
                meta_res.get("badge_text"), job_id=job_id, title_vi=title_vi or series_name,
            )
            sub_txt = (meta_res.get("sub_text") or series_name or title_vi or "").strip()
            ai_prompt = meta_res.get("master_image_prompt") or meta_res.get("ai_image_prompt") or ""
            if "INTEGRATED 3D VIETNAMESE TYPOGRAPHY" not in (ai_prompt or ""):
                ai_prompt = meta_studio.build_master_image_prompt(
                    title_vi=title_vi or series_name,
                    title_zh=title_zh or "",
                    story_moment=str((meta_res.get("thumbnail_concept") or {}).get("story_moment") or ""),
                    visual_dna=str((meta_res.get("thumbnail_concept") or {}).get("visual_dna") or ""),
                    palette=str((meta_res.get("thumbnail_concept") or {}).get("selected_concept") or ""),
                    layout=str((meta_res.get("thumbnail_concept") or {}).get("layout") or ""),
                    hook_texts=hooks,
                    badge_text=badge_txt,
                    sub_text=sub_txt,
                )

            update_auto_state(current_stage="thumbnail", progress_percent=35)

            # AI vẽ luôn chữ 3D (NEW/HOT + hook neo tên) — không dùng Pillow overlay
            print(f"[auto-publish] {job_id}: Đang tạo Thumbnail 16:9 (AI typography)...", flush=True)
            from agents.agent_tele_cmd import _fetch_poster_bytes
            poster_bytes = _fetch_poster_bytes(poster_name)

            ai_img_bytes = None
            if cfg.get("generate_ai_thumb") and ai_prompt:
                try:
                    print(
                        f"[auto-publish] {job_id}: Aurora edits 16:9 badge={badge_txt} "
                        f"hooks={hooks[0]}/{hooks[1] if len(hooks)>1 else ''}...",
                        flush=True,
                    )
                    touch_active_locks()
                    ai_img_bytes = meta_studio.generate_ai_image(
                        ai_prompt,
                        timeout=150,
                        job_id=job_id,
                        poster_name=poster_name,
                        ref_images=poster_bytes if poster_bytes else None,
                    )
                except Exception as e_ai:
                    print(f"[auto-publish] {job_id}: Lỗi sinh ảnh AI Aurora ({e_ai}) — chuyển sang fallback", flush=True)

            if ai_img_bytes:
                try:
                    from PIL import Image
                    import io
                    im = Image.open(io.BytesIO(ai_img_bytes)).convert("RGB")
                    tw, th = 1280, 720
                    ratio = im.width / max(im.height, 1)
                    target_ratio = tw / th
                    if ratio > target_ratio:
                        new_w = int(im.height * target_ratio)
                        im = im.crop(((im.width - new_w) // 2, 0, (im.width + new_w) // 2, im.height))
                    elif ratio < target_ratio:
                        new_h = int(im.width / target_ratio)
                        im = im.crop((0, (im.height - new_h) // 2, im.width, (im.height + new_h) // 2))
                    im = im.resize((tw, th), Image.Resampling.LANCZOS)
                    im.save(thumb_path, format="JPEG", quality=95, optimize=True)
                    print(
                        f"[auto-publish] {job_id}: Đã lưu Master Thumbnail AI 16:9 "
                        f"({thumb_path.stat().st_size // 1024} KB)",
                        flush=True,
                    )
                except Exception as e_save_ai:
                    print(f"[auto-publish] {job_id}: Lỗi lưu ảnh AI ({e_save_ai})", flush=True)
                    ai_img_bytes = None

            if not ai_img_bytes:
                print(f"[auto-publish] {job_id}: Không tạo được ảnh AI Aurora -> KHÔNG dùng thumbnail fallback (để mặc định YouTube, người dùng sẽ tự cập nhật sau).", flush=True)
                thumb_path = None

            # Tự động lưu lại làm mẫu Master Thumbnail & Tags cho toàn bộ Series này nếu có ảnh AI thành công
            if ai_img_bytes and thumb_path and thumb_path.is_file():
                meta_studio.save_series_thumbnail(series_key, thumb_path, series_name=series_name)
            meta_studio.save_series_meta(series_key, {
                "series_key": series_key,
                "series_name": series_name,
                "thumb_filename": f"series_{series_key}.jpg" if (ai_img_bytes and thumb_path) else "",
                "tags": final_tags,
                "description": final_desc,
                "best_hook": meta_res.get("hook_texts", [""])[0] if meta_res.get("hook_texts") else "",
                "created_by_job": job_id,
            }, series_name=series_name)
            if ai_img_bytes and thumb_path:
                print(f"[auto-publish] {job_id}: Đã lưu mẫu Master Thumbnail & Tags cho toàn bộ Series «{series_name}»!", flush=True)

            update_auto_state(current_stage="uploading", progress_percent=60)

        # 4. Tính toán PublishAt nếu có hẹn giờ
        publish_at_iso = None
        if schedule_hours and schedule_hours > 0:
            publish_dt = datetime.now(timezone.utc) + timedelta(hours=schedule_hours)
            publish_at_iso = publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            print(f"[auto-publish] {job_id}: Hẹn giờ xuất bản lúc {publish_at_iso} (sau {schedule_hours}h)", flush=True)

        # 5. Upload lên YouTube qua API
        touch_active_locks()
        up_res = upload_to_youtube(
            video_path=out_path,
            title=final_title,
            description=final_desc,
            tags=final_tags,
            thumb_path=thumb_path,
            privacy=privacy,
            publish_at=publish_at_iso,
            account_id=cfg.get("account_id", "default"),
            category_id=cfg.get("category_id", "24"),
        )
    except Exception as e_fatal:
        err_msg = f"Lỗi ngoại lệ khi xuất bản {job_id}: {e_fatal}"
        print(f"[auto-publish] {err_msg}", flush=True)
        _revert_job_to_done(job_id, err_msg)
        limit_hit = _is_yt_upload_limit_error(e_fatal)
        if limit_hit:
            err_msg = (
                f"DỪNG upload — YouTube uploadLimitExceeded khi xuất bản {job_id}: {e_fatal}"
            )
            print(f"[auto-publish] {err_msg}", flush=True)
        update_auto_state(
            last_error=err_msg,
            current_stage="idle",
            current_job_id=None,
            progress_percent=0,
        )
        return {
            "ok": False,
            "error": err_msg,
            "upload_limit_exceeded": limit_hit,
            "stop_queue": limit_hit,
        }

    if not up_res.get("ok"):
        err_msg = str(up_res.get("error") or "Upload thất bại")
        _revert_job_to_done(job_id, err_msg)
        limit_hit = _is_yt_upload_limit_error(err_msg)
        if limit_hit:
            err_msg = f"DỪNG upload — YouTube uploadLimitExceeded ({job_id}): {err_msg}"
            print(f"[auto-publish] {err_msg}", flush=True)
        update_auto_state(
            last_error=err_msg,
            current_stage="idle",
            current_job_id=None,
            progress_percent=0,
        )
        out = dict(up_res)
        out["error"] = err_msg
        out["upload_limit_exceeded"] = limit_hit
        out["stop_queue"] = limit_hit
        return out

    yt_url = up_res["yt_url"]
    video_id = up_res["video_id"]
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 6. Cập nhật Database films.db
    with film_db._lock, film_db.connect() as con:
        con.execute(
            "UPDATE films SET yt_url = ?, status = 'uploaded', uploaded_at = ?, yt_channel = ?, updated_at = ? WHERE job_id = ?",
            (yt_url, now_utc, "Sage Review", now_utc, job_id)
        )
        con.execute(
            "INSERT INTO events(ts, job_id, kind, detail) VALUES (?,?,?,?)",
            (now_utc, job_id, "upload", yt_url)
        )

    # 7. Cập nhật manifest.json của job
    try:
        from core.manifest import Manifest
        m = Manifest(ROOT / "work" / job_id)
        m.data["yt_url"] = yt_url
        m.data["uploaded_at"] = now_utc
        m.data["upload_status"] = "uploaded"
        js.persist(m)
    except Exception:
        pass

    # 8. Ghi nhận lịch sử upload vào upload_history.jsonl
    try:
        hist_entry = {
            "ts": now_utc,
            "job_id": job_id,
            "video_id": video_id,
            "url": yt_url,
            "title": final_title,
            "path": str(out_path),
            "thumb": str(thumb_path) if thumb_path else "",
            "privacy": privacy,
            "publish_at": publish_at_iso,
            "account": cfg.get("account_id", "default"),
        }
        with UPLOAD_HISTORY_FILE.open("a", encoding="utf-8") as hf:
            hf.write(json.dumps(hist_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

    update_auto_state(
        last_published={
            "job_id": job_id,
            "title": final_title,
            "yt_url": yt_url,
            "uploaded_at": now_utc,
        },
        current_stage="idle",
        current_job_id=None,
        progress_percent=100,
    )

    print(f"🎉 [auto-publish] ĐÃ TỰ ĐỘNG XUẤT BẢN THÀNH CÔNG: {final_title} -> {yt_url}", flush=True)
    return {
        "ok": True,
        "job_id": job_id,
        "title": final_title,
        "yt_url": yt_url,
        "video_id": video_id,
        "thumb_url": f"/meta/thumbnail?file={thumb_filename}" if thumb_path else None,
    }


def _collect_and_enqueue_done_jobs() -> list[dict[str, Any]]:
    """
    Đồng bộ films.db, tự động giải cứu các job bị kẹt trạng thái 'uploading' mồ côi (không còn tiến trình giữ lock),
    và đưa toàn bộ các job 'done' chưa upload vào hàng chờ tuần tự.
    """
    try:
        film_db.sync()
    except Exception:
        pass

    # Giải cứu các job trong DB có status='uploading' nhưng thực tế không có tiến trình nào đang giữ lock
    try:
        uploading_rows = film_db.query(status="uploading", limit=50)
        for u_row in uploading_rows:
            u_jid = u_row.get("job_id")
            if u_jid and not is_job_uploading(u_jid):
                print(f"[auto-publish] Phát hiện job {u_jid} bị kẹt trạng thái 'uploading' (không còn lock hoạt động) -> Phục hồi về 'done' để xếp hàng upload lại.", flush=True)
                _revert_job_to_done(u_jid, "Interrupted upload recovered by scanner")
    except Exception:
        pass

    done_films = film_db.query(status="done", limit=50)
    pending_upload: list[dict[str, Any]] = []
    for f in done_films:
        jid = f.get("job_id")
        if not jid:
            continue
        if is_auto_split_part(jid):
            continue
        if f.get("yt_url"):
            continue
        if not f.get("work_exists"):
            continue
        if is_job_uploading(jid):
            continue
        # Title kiểu «… - Phần 2/2» (kể cả khi id chưa có _pNN)
        title = str(f.get("title_vi") or f.get("title") or f.get("title_zh") or "")
        if _SPLIT_PART_TITLE_RE.search(title):
            continue
        mf = ROOT / "work" / jid / "manifest.json"
        if mf.is_file():
            try:
                m_chk = json.loads(mf.read_text(encoding="utf-8"))
                if m_chk.get("yt_url") or m_chk.get("upload_status") == "uploaded":
                    continue
                if m_chk.get("upload_status") == "uploading" and is_job_uploading(jid):
                    continue
            except Exception:
                pass
        pending_upload.append(f)

    # Sắp xếp tuần tự theo Tên Bộ & Phần (P1 -> P2 -> P3 -> P4)
    try:
        from core.job_queue import job_sort_key
        pending_upload.sort(key=lambda f: job_sort_key(f.get("job_id") or ""))
    except Exception:
        pass

    for f in pending_upload:
        jid = f.get("job_id")
        if jid:
            _enqueue_job_record(jid)

    # Gỡ part auto-split đã lọt queue cũ (…_pNN / «Phần N/N»)
    try:
        for it in list(_read_publish_queue()):
            jid = str(it.get("job_id") or "")
            if jid and is_auto_split_part(jid):
                print(f"[auto-publish] Gỡ part khỏi queue upload: {jid}", flush=True)
                _dequeue_job_record(jid)
    except Exception:
        pass

    _sync_queue_state_badge()
    return pending_upload


def scan_and_publish_completed_jobs() -> dict[str, Any]:
    """Quét và tự động xuất bản tất cả các job đã xong (done) nhưng chưa upload."""
    # Nếu đang có bất kỳ tiến trình nào đang upload, chỉ thu thập thêm job mới vào hàng đợi
    # chứ tuyệt đối không ghi đè trạng thái hay làm gián đoạn job đang upload.
    busy_now, active_owner_job = is_any_job_uploading()
    if busy_now:
        cfg = load_config()
        if cfg.get("enabled", True):
            _collect_and_enqueue_done_jobs()
        st = get_auto_state()
        return {
            "ok": True,
            "busy": True,
            "current_job_id": st.get("current_job_id") or active_owner_job,
            "current_title": st.get("current_title"),
            "stage": st.get("current_stage") if st.get("current_stage") != "idle" else "uploading",
            "progress_percent": st.get("progress_percent", 0),
            "file_size_mb": st.get("file_size_mb", 0),
            "queue_count": st.get("queue_count", 0),
            "queued_jobs": st.get("queued_jobs", []),
            "message": f"Hệ thống đang xuất bản '{st.get('current_title') or active_owner_job}' ({st.get('progress_percent', 0)}%) — các phim xong sau đã được xếp vào hàng đợi ({st.get('queue_count', 0)} phim chờ)."
        }

    if not _scan_lock.acquire(blocking=False):
        st = get_auto_state()
        return {
            "ok": True,
            "busy": True,
            "current_job_id": st.get("current_job_id"),
            "current_title": st.get("current_title"),
            "stage": st.get("current_stage"),
            "progress_percent": st.get("progress_percent", 0),
            "file_size_mb": st.get("file_size_mb", 0),
            "queue_count": st.get("queue_count", 0),
            "message": f"Hệ thống đang xuất bản '{st.get('current_title') or st.get('current_job_id')}' ({st.get('progress_percent')}%)"
        }

    try:
        from core.fileio import file_lock
        # Scan chỉ giữ lock ngắn — stale 3 phút (trước 4h khiến worker kẹt sau restart sidecar)
        with file_lock(SCAN_LOCK_FILE, timeout=0.5, stale_sec=180.0):
            count = _scan_and_publish_impl()
            return {"ok": True, "busy": False, "scanned": count}
    except TimeoutError:
        st = get_auto_state()
        return {
            "ok": True,
            "busy": True,
            "current_job_id": st.get("current_job_id"),
            "current_title": st.get("current_title"),
            "stage": st.get("current_stage"),
            "progress_percent": st.get("progress_percent", 0),
            "file_size_mb": st.get("file_size_mb", 0),
            "queue_count": st.get("queue_count", 0),
            "message": f"Tiến trình quét đang được xử lý ở tiến trình khác ({st.get('current_title') or st.get('current_job_id')})."
        }
    finally:
        _scan_lock.release()


def _scan_and_publish_impl() -> int:
    cfg = load_config()
    if not cfg.get("enabled"):
        return 0

    pending_upload = _collect_and_enqueue_done_jobs()
    queue_items = _read_publish_queue()
    if not pending_upload and not queue_items:
        _sync_queue_state_badge()
        return 0

    # Danh sách job cần xử lý theo thứ tự hàng đợi
    job_ids_to_run: list[str] = []
    for it in queue_items:
        jid = it.get("job_id")
        if jid and jid not in job_ids_to_run:
            job_ids_to_run.append(jid)
    for f in pending_upload:
        jid = f.get("job_id")
        if jid and jid not in job_ids_to_run:
            job_ids_to_run.append(jid)

    if not job_ids_to_run:
        _sync_queue_state_badge()
        return 0

    print(f"[auto-publish] Tìm thấy {len(job_ids_to_run)} phim trong hàng đợi xuất bản. Bắt đầu tự động xử lý tuần tự P1→Pn...", flush=True)
    count = 0
    for jid in job_ids_to_run:
        if _stop_event.is_set():
            break
        try:
            r = publish_job(jid)
            if r.get("ok") and not r.get("already_uploaded") and not r.get("skipped"):
                count += 1
        except Exception as e:
            print(f"[auto-publish] Lỗi xử lý job {jid}: {e}", flush=True)

    _sync_queue_state_badge()
    return count


def _auto_publish_worker_loop():
    """Vòng lặp chạy ngầm tự động quét sau mỗi 60 giây."""
    print("[auto-publish] Worker chạy ngầm đã khởi động (chu kỳ 60s)", flush=True)
    while not _stop_event.is_set():
        try:
            scan_and_publish_completed_jobs()
        except Exception as e:
            print(f"[auto-publish] Lỗi vòng lặp worker: {e}", flush=True)
        _stop_event.wait(60)


def start_worker():
    """Khởi động worker ngầm nếu chưa chạy."""
    global _worker_thread
    with _lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _stop_event.clear()
        _worker_thread = threading.Thread(target=_auto_publish_worker_loop, name="auto-publish-worker", daemon=True)
        _worker_thread.start()


def stop_worker():
    """Dừng worker ngầm."""
    _stop_event.set()
