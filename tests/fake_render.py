"""Render GIẢ cho test F5 (chọn qua env PINELINE_RENDER_IMPL=tests.fake_render:<hàm>) — không ffmpeg/GPU."""
from __future__ import annotations

import os
import time
from pathlib import Path


def ok(work_dir, **kw):
    """Như render thật: ghi tiến độ vào manifest qua state (tiến trình con) + phát progress + mark done."""
    from state import job_state as js
    from workers.render_worker import emit
    wd = Path(work_dir)
    print("ffmpeg giả: frame=1 fps=30")            # dòng log thường (không phải JSON)
    js.mark_running(wd, "mux", cur_text="render giả")
    for pct in (25, 75):
        js.set_stage(wd, "mux", progress=pct)
        emit("progress", percent=pct, cur_text=f"giả {pct}%")
    out = wd / "output.mp4"
    out.write_bytes(b"x")
    (wd / "kw.json").write_text(__import__("json").dumps(kw, sort_keys=True), encoding="utf-8")
    js.mark_done(wd, "mux", output_path=str(out), progress=100)
    return str(out)


def boom(work_dir, **kw):
    raise RuntimeError("Chưa có track giọng Việt (dub_fit.wav). Ghép TTS thất bại")


def slow(work_dir, **kw):
    """Chạy lâu (tới khi bị terminate) — test Dừng."""
    (Path(work_dir) / "slow.pid").write_text(str(os.getpid()), encoding="utf-8")
    print("đang render chậm…", flush=True)
    for _ in range(600):
        time.sleep(0.1)
    return None


def crash(work_dir, **kw):
    """Chết đột ngột không kịp báo (như bị OOM/kill)."""
    os._exit(3)


def skip(work_dir, **kw):
    """Như do_render khi mux đã xong: trả None."""
    return None
