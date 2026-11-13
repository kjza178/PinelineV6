"""Ghi file an toàn giữa nhiều tiến trình (server :8801, sidecar :8802, script).

atomic_write_text: ghi temp riêng (pid+thread) rồi os.replace — người đọc không bao giờ thấy file dở.
file_lock: khoá liên tiến trình bằng file .lock tạo O_EXCL; khoá bỏ quên (tiến trình chết) quá
stale_sec giây thì gỡ."""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8", fsync: bool = True) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding=encoding, newline="") as f:
        f.write(text)
        f.flush()
        if fsync:
            os.fsync(f.fileno())
    # Windows: đích đang bị mở đọc → WinError 5/32 trong chốc lát
    for i in range(20):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    try:
        os.replace(tmp, p)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def atomic_write_json(path: str | Path, obj, indent: int | None = 2, fsync: bool = True) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent), fsync=fsync)


def read_json_checkpoint(path: str | Path, default=None):
    """Đọc checkpoint JSON. Hỏng → đổi tên thành .corrupt-<ts> (giữ để cứu) và trả default,
    thay vì để caller ghi đè im lặng."""
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        bad = p.with_name(f"{p.name}.corrupt-{int(time.time())}")
        with contextlib.suppress(OSError):
            os.replace(p, bad)
        print(f"[fileio] {p.name} hỏng ({e}) → giữ bản hỏng {bad.name}, làm lại phần này", flush=True)
        return default


if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(path: str | Path, timeout: float = 15.0, stale_sec: float = 60.0):  # noqa: ARG001
    """Khoá liên tiến trình + liên luồng bằng khoá của HỆ ĐIỀU HÀNH trên file `<path>.lock`.

    Fix 2026-11-11 (V6): bản cũ dùng "file tồn tại = đang khoá" (O_EXCL) + đọc PID để gỡ khoá chết.
    Trên Windows, người giữ khoá xoá file thất bại (bị nuốt lỗi) khi tiến trình khác đang mở file đọc PID
    → khoá rò tới khi stale 60 s → TimeoutError / hai bên cùng ghi. Bản mới: khoá byte bằng
    msvcrt.locking (Windows) / flock (POSIX); tiến trình chết → OS tự nhả, không cần đoán stale.
    `stale_sec` giữ cho tương thích chữ ký. File .lock để lại vô hại (POSIX: KHÔNG xoá, tránh 2 inode).
    """
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    fd = None
    while fd is None:
        try:
            cand = os.open(lock, os.O_CREAT | os.O_RDWR)
        except PermissionError:   # Windows: file đang chờ xoá
            cand = None
        if cand is not None:
            if _try_lock(cand):
                fd = cand
                break
            os.close(cand)
        if time.time() - t0 > timeout:
            raise TimeoutError(f"không lấy được khoá {lock}")
        time.sleep(0.01)
    try:
        yield
    finally:
        _unlock(fd)
        os.close(fd)
        if os.name == "nt":
            # Chỉ xoá được khi không ai còn mở → an toàn; ai đang chờ thì file còn, họ khoá tiếp.
            with contextlib.suppress(OSError):
                lock.unlink()
