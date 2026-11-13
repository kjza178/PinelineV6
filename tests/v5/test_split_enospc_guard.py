"""Unit tests: split_parts ENOSPC guards (no ffmpeg)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.split_parts import (  # noqa: E402
    DiskFullError,
    _is_disk_full_text,
    estimate_split_need_bytes,
)


def test_is_disk_full_text_variants():
    assert _is_disk_full_text("ffmpeg: No space left on device")
    assert _is_disk_full_text("[Errno 28] No space left on device")
    assert _is_disk_full_text("OS error 28")
    assert _is_disk_full_text("There is not enough space on the disk")
    assert not _is_disk_full_text("Invalid data found when processing input")
    assert not _is_disk_full_text("")


def test_estimate_split_need_gt_source(tmp_path: Path):
    f = tmp_path / "v.bin"
    f.write_bytes(b"x" * (10 * 1024 * 1024))  # 10MB
    need = estimate_split_need_bytes(f)
    assert need > 10 * 1024 * 1024
    assert need >= int(10 * 1024 * 1024 * 1.15)


def test_disk_full_error_is_oserror():
    assert issubclass(DiskFullError, OSError)
    e = DiskFullError("no space")
    assert _is_disk_full_text(str(e))
