"""
manifest.py — Trạng thái/checkpoint cho pipeline.

Mỗi job (mỗi video) có một thư mục làm việc riêng chứa manifest.json.
Mỗi stage ghi trạng thái vào đây; crash/mất điện thì resume từ stage cuối
đã "done". File này dùng CHUNG cho tất cả stage sau (download, separate,
asr, translate, tts, mux...), nên viết một lần xài mãi.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from core.fileio import file_lock as _file_lock
except Exception:  # noqa: BLE001  — chạy từ venv/cwd khác không thấy fileio
    _file_lock = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Khoá RIÊNG cho mỗi manifest (theo đường dẫn). Reader (UI poll) và writer
# (luồng tiến độ) trong CÙNG tiến trình sẽ không bao giờ chồng lên nhau -> os.replace
# không còn dính WinError 5/32 -> KHÔNG còn retry-ngủ tới 6s làm "treo treo".
# (Các stage chạy subprocess như asr/tts thì retry-ngủ nằm ở tiến trình con, không
#  đóng băng UI; reader phía server vẫn có retry ngắn làm lưới an toàn.)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(str(path)))
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _locks[key] = lk
        return lk


_MISSING = object()

# V6: hook sau mỗi lần ghi thành công (cùng tiến trình) → state.index cập nhật + phát event UI.
# Ghi từ tiến trình con (asr/tts) không chạy hook ở server → state.watcher bắt theo mtime.
_write_hooks: list = []


def register_write_hook(fn) -> None:
    """fn(work_dir: Path, data: dict). Lỗi trong hook không làm hỏng lần ghi."""
    if fn not in _write_hooks:
        _write_hooks.append(fn)


def unregister_write_hook(fn) -> None:
    if fn in _write_hooks:
        _write_hooks.remove(fn)


def _run_hooks(work_dir: Path, data: dict) -> None:
    for fn in list(_write_hooks):
        try:
            fn(work_dir, data)
        except Exception as e:  # noqa: BLE001
            print(f"[manifest] write hook lỗi {work_dir.name}: {e}")


def _merge3(base, mine, theirs):
    """Gộp 3 chiều dict JSON. Key mình không đổi so với lúc load → lấy bản đĩa (người khác
    vừa ghi); mình đổi → lấy của mình; cả hai cùng đổi dict → gộp sâu, còn lại mình thắng."""
    if not (isinstance(mine, dict) and isinstance(theirs, dict)):
        return mine
    if not isinstance(base, dict):
        base = {}
    keys = list(mine) + [k for k in theirs if k not in mine] + [k for k in base if k not in mine and k not in theirs]
    out = {}
    for k in keys:
        b, m, t = base.get(k, _MISSING), mine.get(k, _MISSING), theirs.get(k, _MISSING)
        if m == b:
            v = t
        elif t == b:
            v = m
        elif isinstance(m, dict) and isinstance(t, dict):
            v = _merge3(b if isinstance(b, dict) else {}, m, t)
        else:
            v = m
        if v is not _MISSING:
            out[k] = v
    return out


@contextlib.contextmanager
def _xproc_lock(path: Path):
    """Khoá liên tiến trình (server + subprocess asr/tts cùng ghi). Không lấy được thì vẫn ghi
    (như trước) — không để 1 khoá kẹt làm chết luồng tiến độ."""
    if _file_lock is None:
        yield
        return
    try:
        cm = _file_lock(path, timeout=5.0, stale_sec=30.0)
        cm.__enter__()
    except Exception as e:  # noqa: BLE001
        print(f"[manifest] không lấy được khoá {path.name}: {e} — ghi không khoá")
        yield
        return
    try:
        yield
    finally:
        cm.__exit__(None, None, None)


class Manifest:
    def __init__(self, work_dir: str | Path):
        self.work_dir = Path(work_dir)
        self.path = self.work_dir / "manifest.json"
        self.data: dict = {"created_at": _now(), "stages": {}}
        self._base: dict = copy.deepcopy(self.data)
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        # Có thể đọc TRÚNG lúc TIẾN TRÌNH KHÁC (asr/tts subprocess) đang ghi -> nội
        # dung dở/rỗng. Thử lại vài lần (ngắn) để /api/jobs không 500. Writer cùng
        # tiến trình đã bị khoá _lock_for chặn nên gần như không bao giờ dính ở đây.
        self._load_failed = False
        with _lock_for(self.path):
            for i in range(6):
                try:
                    self.data = json.loads(self.path.read_text(encoding="utf-8"))
                    self.data.setdefault("stages", {})
                    self._base = copy.deepcopy(self.data)
                    return
                except (json.JSONDecodeError, OSError):
                    time.sleep(0.02 * (i + 1))
        # File tồn tại nhưng đọc hỏng: GIỮ data cũ nếu có; đánh dấu để save() không xoá job.
        self._load_failed = True
        print(f"[manifest] load fail {self.path} — từ chối ghi đè bằng data rỗng")

    def _read_disk(self) -> dict | None:
        for i in range(6):
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                return d if isinstance(d, dict) else None
            except FileNotFoundError:
                return None
            except (json.JSONDecodeError, OSError):
                time.sleep(0.02 * (i + 1))
        return None

    def update(self, fn) -> bool:
        """Đọc lại từ đĩa → fn(data) → ghi, trong khoá (thread + liên tiến trình).
        Manifest hỏng / thiếu stages → không ghi, trả False."""
        with _lock_for(self.path), _xproc_lock(self.path):
            disk = self._read_disk()
            if disk is None or not isinstance(disk.get("stages"), dict):
                return False
            self.data, self._base, self._load_failed = disk, copy.deepcopy(disk), False
            if fn(self.data) is False:
                return False
            self._write_locked(self.data)
        _run_hooks(self.work_dir, self.data)
        return True

    def _commit(self) -> bool:
        """Ghi snapshot của object này: gộp 3 chiều với bản đĩa (chỉ khoá mình đổi thắng), trong khoá
        thread + liên tiến trình. Code mới KHÔNG gọi trực tiếp — dùng state.job_state (persist / hàm ngữ nghĩa)."""
        # Chặn bug wipe: load JSON hỏng rồi save() sẽ XOÁ download/asr/translate/voice.
        if getattr(self, "_load_failed", False):
            print(f"[manifest] BỎ QUA save (load failed) -> {self.path.name}")
            return False
        with _lock_for(self.path), _xproc_lock(self.path):
            disk = self._read_disk() if self.path.exists() else None
            # Object sống lâu (m_early render, TTS orchestrator…) giữ snapshot cũ: chỉ ghi phần
            # mình đổi, phần người khác vừa ghi (cờ HOLD/QA, title_vi, tiến độ) giữ nguyên.
            data = _merge3(self._base, self.data, disk) if disk is not None else self.data
            # Chặn wipe nếu stages rỗng nhưng file trên đĩa còn stages (race).
            if not data.get("stages") and disk and disk.get("stages"):
                print(f"[manifest] BỎ QUA save rỗng (đĩa còn stages) -> {self.path.name}")
                return False
            self._write_locked(data)
        _run_hooks(self.work_dir, self.data)
        return True

    def save(self) -> None:  # pragma: no cover - chỉ còn cho script ngoài V6
        """DEPRECATED (V6 F1): dùng state.job_state.persist(m) hoặc hàm ngữ nghĩa. Giữ để script cũ không vỡ."""
        self._commit()

    def _write_locked(self, data: dict) -> None:
        """Ghi atomic; gọi khi đang giữ _lock_for(self.path)."""
        self.data = data
        self._base = copy.deepcopy(data)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        # Ghi atomic: viết ra file tạm rồi replace. Nếu mất điện GIỮA lúc ghi,
        # manifest.json cũ vẫn nguyên vẹn -> resume được, không hỏng job.
        #
        # Tên tạm RIÊNG theo tiến trình+thread: tránh 2 thread (vd monitor tải +
        # tiến độ ASR) cùng ghi đè 1 file .tmp của nhau.
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp = self.path.with_name(f".manifest.{os.getpid()}.{threading.get_ident()}.tmp")
        # Windows: os.replace bị WinError 5/32 nếu file đích đang được MỞ ĐỌC đúng
        # khoảnh khắc replace. Khoá _lock_for chặn mọi READER CÙNG TIẾN TRÌNH nên
        # replace gần như luôn thành công ngay lần đầu -> hết retry-ngủ = hết "treo".
        # Retry (ngắn hơn: ~2.5s) chỉ còn là lưới cho reader ở tiến trình khác.
        last: Exception | None = None
        tmp.write_text(text, encoding="utf-8")
        for i in range(12):
            try:
                os.replace(tmp, self.path)
                return
            except (PermissionError, OSError) as e:  # noqa: PERF203
                last = e
                time.sleep(0.02 * (i + 1))
        # Bó tay sau ~6s: ghi thẳng (không atomic) để KHÔNG mất cập nhật / KHÔNG
        # làm văng exception giết thread. Lần ghi sau sẽ tự lành lại.
        try:
            self.path.write_text(text, encoding="utf-8")
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        if last:
            print(f"[manifest] replace kẹt, đã ghi trực tiếp {self.path.name}: {last}")

    def stage(self, name: str) -> dict | None:
        return self.data.get("stages", {}).get(name)

    def is_done(self, name: str) -> bool:
        st = self.stage(name)
        return bool(st and st.get("status") == "done")

    def mark_running(self, name: str) -> None:
        """Giữ config cũ (voice/speed/engine/n_done...) — chỉ đổi status/started_at.
        Xoá cur_text/error cũ để UI không còn hiện thông báo lỗi lần trước."""
        prev = dict(self.data.get("stages", {}).get(name) or {})
        keep = {k: v for k, v in prev.items()
                if k not in ("status", "error", "finished_at", "cur_text")}
        self.data.setdefault("stages", {})[name] = {
            **keep, "status": "running", "started_at": _now(),
            "cur_text": "đang chạy…",
        }
        self._commit()

    def set_stage(self, name: str, **payload) -> dict:
        """Cập nhật/merge stage (vd. status=running + asr_engine=…).

        Dùng bởi luồng auto-split ASR video dài — không thay mark_running/mark_done.
        """
        prev = dict(self.data.get("stages", {}).get(name) or {})
        st = {**prev, **payload}
        if payload.get("status") == "running":
            st.setdefault("started_at", _now())
            st["cur_text"] = payload.get("cur_text") or st.get("cur_text") or "đang chạy…"
            st.pop("error", None)
            st.pop("finished_at", None)
        elif payload.get("status") == "done":
            st.setdefault("finished_at", _now())
        elif payload.get("status") == "failed":
            st.setdefault("finished_at", _now())
        self.data.setdefault("stages", {})[name] = st
        self._commit()
        return st

    def mark_done(self, name: str, **payload) -> dict:
        prev = dict(self.data.get("stages", {}).get(name) or {})
        # Giữ voice/speed/engine nếu payload không truyền lại
        for k in ("voice", "speed", "tts_engine", "engine"):
            if k not in payload and k in prev:
                payload[k] = prev[k]
        self.data.setdefault("stages", {})[name] = {
            "status": "done", "finished_at": _now(), **payload,
        }
        self._commit()
        return self.data["stages"][name]

    def mark_failed(self, name: str, error) -> None:
        prev = dict(self.data.get("stages", {}).get(name) or {})
        keep = {k: v for k, v in prev.items()
                if k not in ("status", "error", "finished_at")}
        self.data.setdefault("stages", {})[name] = {
            **keep,
            "status": "failed",
            "finished_at": _now(),
            "error": str(error),
        }
        self._commit()
