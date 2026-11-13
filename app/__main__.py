"""python -m app --instance v6"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.instance import load  # noqa: E402


_LOCK = None
_LOCK_WORK = None


def _work_lock_path(root: Path, work_root: Path) -> Path:
    """Khoá theo work root (chuẩn hoá: tuyệt đối, chữ thường, '/') — cùng work = cùng file khoá."""
    import hashlib
    key = os.path.abspath(str(work_root)).replace("\\", "/").rstrip("/").lower()
    return root / "scratch" / f".backend_work_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}.lock"


def _single_instance_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")  # noqa: SIM115 — giữ mở suốt đời tiến trình
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default=None)
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args()
    inst = load(a.instance)
    if a.port:
        from dataclasses import replace
        inst = replace(inst, port=a.port)
    inst.temp.mkdir(parents=True, exist_ok=True)
    inst.apply_env()
    # V6 F3: server.py (legacy) còn vài đường dẫn tương đối cwd → luôn chạy từ gốc cây.
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    # 1 backend / cổng: uvicorn chạy startup TRƯỚC khi bind → bản thứ 2 sẽ kill engine "orphan"
    # (thực ra của bản đang chạy) rồi mới lỗi cổng. Khoá file chặn từ đầu.
    global _LOCK, _LOCK_WORK
    _LOCK = _single_instance_lock(root / "scratch" / f".backend_{inst.port}.lock")
    if _LOCK is None:
        print(f"Đã có backend Pineline giữ cổng {inst.port} — thoát.", flush=True)
        raise SystemExit(1)
    # review1: khoá thêm theo WORK ROOT — 2 backend khác cổng (vd --port) cùng work vẫn kill engine /
    # reset stage «treo» của nhau lúc startup.
    _LOCK_WORK = _single_instance_lock(_work_lock_path(root, inst.work_root))
    if _LOCK_WORK is None:
        print(f"Đã có backend Pineline dùng work {inst.work_root} — thoát.", flush=True)
        raise SystemExit(1)
    import logging
    import uvicorn

    log_dir = Path(__file__).resolve().parents[1] / "scratch"
    log_dir.mkdir(exist_ok=True)
    fh = logging.FileHandler(log_dir / f"backend_{inst.name}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addHandler(fh)
    logging.getLogger().setLevel(logging.INFO)

    from app.main import create_app
    uvicorn.run(create_app(inst), host=inst.host, port=inst.port, log_level="info")


if __name__ == "__main__":
    main()
