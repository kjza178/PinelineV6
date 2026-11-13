"""
render.py — STAGE 7 (Ghép + xuất): ráp video lồng tiếng "sạch như bản localize".

Video (1 lượt ffmpeg):
  1. Zoom+crop / Ken Burns+Drift (uniqueness).
  2. Blur dải hardsub (đáy giữa) -> xoá phụ đề Trung cháy sẵn.
  3. Blur ô watermark còn sót (góc).
  4. OCR "static": phủ ô gốc + chèn chữ Việt đúng vị trí.
  5. Burn phụ đề Việt (sub.vi.srt) + ghi chú OCR "moving".
  6. LUT nhẹ + film grain.
Audio (ưu tiên an toàn bản quyền — KHÔNG phải tư vấn fair use pháp lý):
  - voice_only / voice_bgm: bỏ nhạc gốc; voice_bgm = giọng Việt + BGM thư viện assets/bgm/
  - demucs_* / duck: giữ nền gốc (có rủi ro bản quyền BGM) — chỉ khi user chủ động chọn.

Đầu ra: work/<job>/output.mp4

Chạy tay:
    python render.py work/<job>
    python render.py work/<job> --zoom 1.12 --motion --grain weak
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import json
import os
import subprocess
import time
from collections import deque
from pathlib import Path

from core.config import get as cfg
from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
import importlib as _importlib
import engines.assemble as _assemble
import engines.media_ts as _media_ts
import engines.sfx_lib as _sfx_lib
import engines.render_fx as _render_fx
# Server chỉ reload render mỗi lần mux; reload kèm để server (import assemble trong hàm) dùng code mới.
_importlib.reload(_assemble)
_importlib.reload(_media_ts)
_importlib.reload(_sfx_lib)
_importlib.reload(_render_fx)
from engines.media_ts import AUDIO_TS_FIX, ensure_job_audio_ts  # noqa: E402
from engines.render_fx import (
    append_inline_noise_grain,
    append_lut_filters,
    apply_brand_overlay,
    bgm_volume,
    build_motion_zoompan,
    build_static_zoom,
    clamp_zoom,
    default_audio_mode,
    dub_volume,
    list_brand_logo_files,
    list_frame_files,
    normalize_grain,
    pick_bgm,
    pick_frame,
    prepare_frame_file,
)
from engines.vram import free_gpu_vram


def _ass_colour(val: str, default: str = "&H00FFFFFF") -> str:
    """#RRGGBB hoặc &HAABBGGRR -> ASS."""
    s = (val or "").strip()
    if s.upper().startswith("&H"):
        return s
    s = s.lstrip("#")
    if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
        r, g, b = s[0:2], s[2:4], s[4:6]
        return f"&H00{b}{g}{r}".upper()
    return default


_EXPR_FLAT_MAX = 64


def _sum_expr(terms: list[str]) -> str:
    """a+b+…; ffmpeg chỉ nhận ≤100 hạng tử cộng phẳng → dài thì gom ngoặc cân bằng."""
    if len(terms) <= _EXPR_FLAT_MAX:
        return "+".join(terms)

    def tree(ts: list[str]) -> str:
        if len(ts) == 1:
            return ts[0]
        m = len(ts) // 2
        return f"({tree(ts[:m])}+{tree(ts[m:])})"
    return tree(terms)


def _pw_expr(ends: list[float], vals: list) -> str:
    """Theo t: vals[i] với i nhỏ nhất có t < ends[i] (ends tăng dần, len = len(vals) - 1), hết → vals[-1].
    ffmpeg giới hạn ~100 tầng if() lồng → dài thì dựng cây nhị phân (độ sâu log2)."""
    if len(vals) <= _EXPR_FLAT_MAX:
        expr = str(vals[-1])
        for end, v in zip(reversed(ends[:len(vals) - 1]), reversed(vals[:-1])):
            expr = f"if(lt(t\\,{end:.2f})\\,{v}\\,{expr})"
        return expr

    def tree(lo: int, hi: int) -> str:
        if lo == hi:
            return str(vals[lo])
        mid = (lo + hi) // 2
        return f"if(lt(t\\,{ends[mid]:.2f})\\,{tree(lo, mid)}\\,{tree(mid + 1, hi)})"
    return tree(0, len(vals) - 1)


def _censor_expr(work_dir: Path | None = None) -> str | None:
    """enable=... cho blur cảnh nhạy cảm. Config CENSOR_RANGES='12-18,90-102' (giây)
    hoặc work/<job>/censor.json → {"ranges":[[12,18],[90,102]]}."""
    chunks: list[str] = []
    raw = (cfg("CENSOR_RANGES", "") or "").strip()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if "-" not in part:
            continue
        a, b = part.split("-", 1)
        try:
            chunks.append(f"between(t,{float(a):.3f},{float(b):.3f})")
        except ValueError:
            continue
    if work_dir:
        p = Path(work_dir) / "censor.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                for pair in data.get("ranges") or []:
                    if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                        chunks.append(f"between(t,{float(pair[0]):.3f},{float(pair[1]):.3f})")
            except Exception:  # noqa: BLE001
                pass
    return _sum_expr(chunks) if chunks else None


def _probe_dur(path: Path) -> float:
    """Độ dài video (giây) để tính % render."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True).stdout.strip()
        return float(out)
    except Exception:  # noqa: BLE001
        return 0.0


_PROG_KEYS = {"frame", "fps", "stream_0_0_q", "bitrate", "total_size", "out_time_us",
              "out_time_ms", "out_time", "dup_frames", "drop_frames", "speed", "progress"}


def _ffmpeg_progress(cmd: list, total: float, m: Manifest,
                     cwd: str | Path | None = None,
                     lo: float = 0.0, hi: float = 100.0) -> tuple[int, str]:
    """Chạy ffmpeg + đọc -progress -> cập nhật % vào manifest (mux). Trả (mã, lỗi).

    cwd: thư mục job — bắt buộc khi dùng lut3d=file=_render_lut.cube (basename).
    lo/hi: dải % của stage mà lượt ffmpeg này chiếm (two-pass: audio 0–15, encode 15–100).
    """
    # -progress/-nostats là GLOBAL option -> phải đặt NGAY SAU 'ffmpeg' (đặt sau file
    # output sẽ làm ffmpeg lỗi 'no output').
    cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]
    work = Path(cwd) if cwd else None
    if work is not None:
        try:
            (work / ".stop").unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1,
                            cwd=str(cwd) if cwd else None)
    _no_power_throttle(proc.pid)
    if work is not None:
        try:
            (work / ".ffmpeg.pid").write_text(str(proc.pid), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    tail: deque = deque(maxlen=15)
    last = 0.0
    # out_time có thể reset khi ffmpeg reinit filter (đổi res mid-stream).
    # Cộng dồn offset khi phát hiện nhảy lùi; đồng thời ước % từ total_size khi
    # out_time đứng yên nhưng file vẫn tăng (tránh UI "stuck" ~42% trong khi encode gần xong).
    peak_pct = 0.0
    try:
        peak_pct = float((m.data.get("stages") or {}).get(STAGE, {}).get("progress") or 0)
    except (TypeError, ValueError):
        peak_pct = 0.0
    prev_out_us = -1
    out_offset_us = 0
    last_size = 0
    last_bitrate_bps = 0.0  # từ dòng bitrate=...kbits/s
    stop_hits = 0

    def _commit_pct(pct: float) -> None:
        nonlocal peak_pct, last
        now = time.time()
        if now - last < 1.2:
            return
        last = now
        pct = lo + (hi - lo) * max(0.0, min(pct, 100.0)) / 100.0
        pct = max(peak_pct, min(pct, 99.9))
        if round(pct, 1) == round(peak_pct, 1) and peak_pct > 0:
            return  # không spam save khi % không đổi
        peak_pct = pct
        st = m.data["stages"].get(STAGE, {})
        st.update({"status": "running", "progress": round(pct, 1),
                   "updated_at": time.time()})
        m.data["stages"][STAGE] = st
        js.persist(m)

    def _user_stop() -> bool:
        nonlocal stop_hits
        if work is None:
            return False
        if (work / ".stop").exists():
            stop_hits += 1
            return True
        return False

    try:
        for line in proc.stdout:
            if _user_stop():
                print(f"[render] nhận .stop → kill ffmpeg pid={proc.pid}", flush=True)
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
                break
            line = line.strip()
            if not line:
                continue
            key, _, val = line.partition("=")
            if key not in _PROG_KEYS:
                tail.append(line)          # dòng log/lỗi (không phải progress)
                continue
            if key == "bitrate" and val:
                # "4321.0kbits/s" | "N/A"
                try:
                    num = val.lower().replace("kbits/s", "").replace("bits/s", "").strip()
                    if num and num != "n/a":
                        b = float(num)
                        # nếu đơn vị kbits/s (ffmpeg mặc định)
                        if "kbit" in val.lower():
                            b *= 1000.0
                        if b > 0:
                            last_bitrate_bps = b
                except ValueError:
                    pass
            elif key == "total_size" and total > 0:
                try:
                    sz = int(val)
                except ValueError:
                    sz = 0
                if sz > last_size:
                    last_size = sz
                    # Ước % từ dung lượng khi biết bitrate; fallback 4M+192k nếu chưa có.
                    bps = last_bitrate_bps if last_bitrate_bps > 0 else 4.192e6
                    exp = max(bps / 8.0 * total, 1.0)
                    size_pct = sz / exp * 100.0
                    if lo + (hi - lo) * min(size_pct, 100.0) / 100.0 > peak_pct + 0.05:
                        _commit_pct(size_pct)
            elif key == "out_time_us" and total > 0:
                try:
                    out_us = int(val)
                except ValueError:
                    continue
                # Timeline reset (filter reinit): cộng dồn thay vì kẹt peak mãi.
                if prev_out_us >= 0 and out_us + 2_000_000 < prev_out_us:
                    out_offset_us += prev_out_us
                prev_out_us = out_us
                eff_us = out_offset_us + out_us
                _commit_pct(eff_us / 1e6 / total * 100.0)
        proc.wait()
    finally:
        if work is not None:
            try:
                (work / ".ffmpeg.pid").unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
    if stop_hits:
        return 1, "stopped by user"
    return proc.returncode, " | ".join(tail)[-400:]


def _no_power_throttle(pid: int) -> None:
    """Tắt EcoQoS của Windows cho ffmpeg con (tránh bị dồn sang E-core khi console thu nhỏ)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class _PPTS(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                        ("StateMask", wintypes.ULONG)]

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x0200, False, int(pid))  # PROCESS_SET_INFORMATION
        if not h:
            return
        st = _PPTS(1, 1, 0)  # EXECUTION_SPEED: opt-out
        k32.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st))
        k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass


def _mp4_video_stts(video: Path) -> tuple[int, list[int]] | None:
    """(timescale, [count, delta, ...]) từ box stts của track video mp4/mov."""
    import struct

    def boxes(f, start: int, end: int):
        pos = start
        while pos + 8 <= end:
            f.seek(pos)
            size, typ = struct.unpack(">I4s", f.read(8))
            hdr = 8
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
                hdr = 16
            elif size == 0:
                size = end - pos
            if size < hdr:
                return
            yield typ, pos + hdr, pos + size
            pos += size

    if Path(video).suffix.lower() not in (".mp4", ".mov", ".m4v"):
        return None
    try:
        with open(video, "rb") as f:
            f.seek(0, 2)
            moov = next(((s, e) for t, s, e in boxes(f, 0, f.tell()) if t == b"moov"), None)
            if not moov:
                return None
            for t, s, e in boxes(f, *moov):
                if t != b"trak":
                    continue
                mdia = next(((s2, e2) for t2, s2, e2 in boxes(f, s, e) if t2 == b"mdia"), None)
                if not mdia:
                    continue
                sub = {t2: (s2, e2) for t2, s2, e2 in boxes(f, *mdia)}
                if b"hdlr" not in sub or b"mdhd" not in sub or b"minf" not in sub:
                    continue
                f.seek(sub[b"hdlr"][0] + 8)
                if f.read(4) != b"vide":
                    continue
                f.seek(sub[b"mdhd"][0])
                ver = f.read(1)[0]
                f.seek(sub[b"mdhd"][0] + (20 if ver == 1 else 12))
                timescale = struct.unpack(">I", f.read(4))[0]
                stbl = next(((s2, e2) for t2, s2, e2 in boxes(f, *sub[b"minf"]) if t2 == b"stbl"), None)
                stts = stbl and next(((s2, e2) for t2, s2, e2 in boxes(f, *stbl) if t2 == b"stts"), None)
                if not stts:
                    return None
                f.seek(stts[0] + 4)
                n = struct.unpack(">I", f.read(4))[0]
                return timescale, list(struct.unpack(f">{2 * n}I", f.read(8 * n)))
    except (OSError, struct.error, StopIteration):
        return None
    return None


def _probe_pts(video: Path, a: float, b: float) -> list[float]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", f"{max(0.0, a)}%{b}",
         "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, timeout=300)
    out = []
    for x in r.stdout.split():
        try:
            out.append(float(x.strip(",")))
        except ValueError:
            pass
    return sorted(out)


def squeeze_fixes(video: Path) -> list[dict]:
    """Đoạn mốc thời gian bị nén khi gộp tập khác fps: N khung dài ~0 rồi 1 khung dài hàng phút.

    Trải đều cả cụm (N khung + khung dài) trên khoảng tới khung kế tiếp → hình khớp lại audio.
    """
    got = _mp4_video_stts(video)
    if not got:
        return []
    ts, ent = got
    counts: dict[int, int] = {}
    for i in range(0, len(ent), 2):
        if ts / 120 <= ent[i + 1] <= ts / 10:
            counts[ent[i + 1]] = counts.get(ent[i + 1], 0) + ent[i]
    if not counts:
        return []
    normal = max(counts, key=counts.get)
    fixes = []
    t = 0
    i = 0
    while i < len(ent):
        cnt, delta = ent[i], ent[i + 1]
        nxt = ent[i + 3] if i + 3 < len(ent) else 0
        if (cnt >= 25 and 0 < delta * 8 < normal and i + 2 < len(ent)
                and ent[i + 2] == 1 and nxt > normal * 25):
            a_dts = t / ts
            b_dts = (t + cnt * delta) / ts
            span = (cnt * delta + nxt) / ts
            # pts lệch dts (B-frame) + vài khung cuối tập trước trùng mốc → tìm dải pts
            # liên tục cách đúng `delta` tick dài nhất.
            all_ticks = [round(p * ts) for p in _probe_pts(video, a_dts - 60, b_dts + 10)]
            ticks = sorted(set(all_ticks))
            best, j0 = (0, -1), 0
            for j in range(1, len(ticks) + 1):
                if j == len(ticks) or ticks[j] - ticks[j - 1] != delta:
                    if j - 1 - j0 > best[1] - best[0]:
                        best = (j0, j - 1)
                    j0 = j
            frames = best[1] - best[0] + 1
            if abs(frames - (cnt + 1)) <= 3:
                a, b = ticks[best[0]], ticks[best[1]]
                # Khung cuối tập trước (lưới chuẩn) lẫn trong dải: khung đầu dải + các mốc trùng.
                from collections import Counter
                n_old = 1 + sum(1 for x, c in Counter(all_ticks).items() if a <= x <= b and c > 1)
                s0 = (a + n_old * normal) / ts
                end = a / ts + span
                n_new = max(1, (b - a) // delta)
                fixes.append({"a": a / ts, "b": b / ts, "tiny": delta / ts, "normal": normal / ts,
                              "s0": s0, "step": (end - s0) / n_new, "frames": n_new,
                              "old": n_old, "span": span})
            else:
                print(f"[render] WARN mốc nén tại {a_dts:.1f}s: dải pts {frames} khung ≠ {cnt + 1} "
                      f"→ bỏ qua sửa", flush=True)
        t += cnt * delta
        i += 2
    return fixes


def squeeze_setpts(fixes: list[dict]) -> str:
    """setpts trải lại các đoạn bị nén (rỗng nếu không có)."""
    if not fixes:
        return ""
    expr = "PTS"
    for fx in fixes:
        a, b, tiny = fx["a"], fx["b"], fx["tiny"]
        half = tiny / 2
        # Khung tập trước đến cách khung trước ~1 khung chuẩn → giữ; khung bị nén (cách ~0) → trải lại.
        expr = (f"if(between(T\\,{a - half:.6f}\\,{b + half:.6f})*lt(T-PREV_INT\\,{fx['normal'] / 2:.6f})\\,"
                f"({fx['s0']:.6f}+((T-{a:.6f})/{tiny:.12f}-1)*{fx['step']:.9f})/TB\\,{expr})")
    return f"setpts='{expr}',"


def _cfg_num(key: str, default: str) -> float:
    v = (os.getenv(key) or str(cfg(key, default) or default)).strip()
    try:
        return float(v)
    except ValueError:
        return float(default)


def chunk_plan(total: float) -> tuple[float, int]:
    """(giây mỗi khúc, số khúc chạy cùng lúc). RENDER_CHUNK_SEC=0 hoặc RENDER_CHUNK_PAR<2 → 1 lượt."""
    sec = _cfg_num("RENDER_CHUNK_SEC", "300")
    par = int(max(1, min(6, _cfg_num("RENDER_CHUNK_PAR", "3"))))
    if sec <= 0 or par < 2:
        return 0.0, 1
    sec = max(60.0, sec)
    return (sec, par) if total >= 2 * sec else (0.0, 1)


def chunk_cuts(video: Path, total: float, sec: float, avoid: list[dict]) -> list[float]:
    """Điểm cắt mỗi ~sec giây, nằm GIỮA 2 khung liền nhau (không trùng mốc khung → không lặp/mất khung).
    Khúc cuối ngắn hơn sec/2 thì gộp vào khúc trước."""
    cuts: list[float] = []
    k = 1
    while k * sec <= total - sec / 2:
        t = k * sec
        k += 1
        for fx in avoid:
            if fx["a"] - 10 <= t <= fx["a"] + fx["span"] + 10:
                t = fx["a"] + fx["span"] + 15
        if (cuts and t <= cuts[-1] + 30) or t >= total - 30:
            continue
        pts = [p for p in _probe_pts(video, t, t + 3) if p >= t]
        for j in range(len(pts) - 1):
            if 0 < pts[j + 1] - pts[j] < 0.1:
                cuts.append((pts[j] + pts[j + 1]) / 2)
                break
    return cuts


def _run_chunked(video: Path, work_dir: Path, graph: str, venc: list[str], pre_in: list[str],
                 cuts: list[float], total: float, mixed: Path, abit: str, out_tmp: Path,
                 m: Manifest, lo: float, par: int = 3) -> tuple[int, str]:
    """Render video theo khúc (giữ mốc gốc), tối đa `par` khúc chạy cùng lúc, ghép copy, gắn audio 1 lần."""
    import threading

    bounds = [0.0] + cuts + [None]
    n = len(bounds) - 1
    par = max(1, min(par, n))
    parts = [work_dir / f"_chunk{i}.mp4" for i in range(n)]
    lst = work_dir / "_chunks.txt"
    (work_dir / ".stop").unlink(missing_ok=True)
    procs: list[subprocess.Popen | None] = [None] * n
    threads: list[threading.Thread | None] = [None] * n
    lens = [(bounds[i + 1] or total) - bounds[i] for i in range(n)]
    done_s = [0.0] * n
    tails: list[deque] = [deque(maxlen=8) for _ in range(n)]
    stop = {"hit": False}
    # Ghi graph 1 lần: các khúc khởi động lệch nhau, ghi lại file lúc khúc khác đang đọc sẽ hỏng.
    script = _attach_filter_complex([], graph, work_dir)

    def _reader(i: int) -> None:
        s = bounds[i]
        for line in procs[i].stdout:
            key, _, val = line.strip().partition("=")
            if key == "out_time_us":
                try:
                    out = int(val) / 1e6
                except ValueError:
                    continue
                rel = out - s if out >= s - 5 else out
                done_s[i] = max(done_s[i], min(max(rel, 0.0), lens[i]))
            elif key and key not in _PROG_KEYS:
                tails[i].append(line.strip())

    def _spawn(i: int) -> None:
        s, e = bounds[i], bounds[i + 1]
        cmd = ["ffmpeg", "-progress", "pipe:1", "-nostats", "-y", "-copyts", *pre_in]
        if s > 0:
            cmd += ["-ss", f"{s:.6f}"]
        cmd += ["-i", _ff_input(video, work_dir), "-filter_complex_script", script.name,
                "-map", "[vout]", "-an", *venc]
        if e is not None:
            cmd += ["-to", f"{e:.6f}"]
        cmd += ["-loglevel", "error", parts[i].name]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", bufsize=1, cwd=str(work_dir))
        _no_power_throttle(p.pid)
        procs[i] = p
        threads[i] = threading.Thread(target=_reader, args=(i,), daemon=True)
        threads[i].start()

    def _running() -> list[subprocess.Popen]:
        return [p for p in procs if p is not None and p.poll() is None]

    def _write_pids() -> None:
        try:
            (work_dir / ".ffmpeg.pid").write_text(",".join(str(p.pid) for p in _running() + [aproc]),
                                                  encoding="utf-8")
        except OSError:
            pass

    # AAC chỉ chạy 1 luồng (~14x realtime) -> encode song song với video, lúc ghép chỉ copy.
    aud = work_dir / "_audio.m4a"
    acmd = ["ffmpeg", "-y", "-i", _ff_input(mixed, work_dir), "-vn", "-c:a", "aac", "-b:a", abit, "-ar", "48000"]
    if total and total > 0:
        acmd += ["-t", f"{total:.3f}"]
    acmd += ["-loglevel", "error", aud.name]
    aproc = subprocess.Popen(acmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                             encoding="utf-8", errors="replace", cwd=str(work_dir))
    _no_power_throttle(aproc.pid)
    print(f"[render] chia {n} khúc ~{max(lens):.0f}s · {par} khúc song song · "
          f"cắt tại {', '.join(f'{c:.3f}s' for c in cuts)}", flush=True)
    nxt = 0
    while nxt < par:
        _spawn(nxt)
        nxt += 1
    _write_pids()
    failed = False
    peak = lo
    while True:
        time.sleep(1.5)
        if (work_dir / ".stop").exists():
            stop["hit"] = True
            break
        spawned = False
        for i in range(nxt):
            p = procs[i]
            if p.poll() is None or done_s[i] >= lens[i] + 1:
                continue
            if p.returncode != 0:
                failed = True
                break
            done_s[i] = lens[i] + 1
            if nxt < n:
                _spawn(nxt)
                nxt += 1
                spawned = True
        if failed:
            break
        if spawned:
            _write_pids()
        if nxt >= n and not _running():
            break
        pct = lo + (99.0 - lo) * min(sum(min(d, ln) for d, ln in zip(done_s, lens)) / max(total, 1.0), 1.0)
        if pct >= peak + 0.1:
            peak = pct
            st = m.data["stages"].get(STAGE, {})
            st.update({"status": "running", "progress": round(pct, 1),
                       "phase": f"encode×{par} ({sum(1 for d, ln in zip(done_s, lens) if d > ln)}/{n})",
                       "updated_at": time.time()})
            m.data["stages"][STAGE] = st
            js.persist(m)
    if stop["hit"] or failed:
        for p in _running():
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
    for i in range(nxt):
        procs[i].wait()
        threads[i].join(timeout=5)

    def _cleanup() -> None:
        for f in parts + [lst, aud]:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass

    bad = [i for i in range(nxt) if procs[i].returncode != 0]
    if stop["hit"] or bad:
        try:
            aproc.kill()
        except Exception:  # noqa: BLE001
            pass
        aproc.wait()
        (work_dir / ".ffmpeg.pid").unlink(missing_ok=True)
        _cleanup()
        if stop["hit"]:
            return 1, "stopped by user"
        err = " | ".join(" ".join(tails[i]) for i in bad)[-400:]
        return 1, f"đoạn {bad} lỗi: {err}"
    if aproc.poll() is None:
        st = m.data["stages"].get(STAGE, {})
        st.update({"status": "running", "progress": 99.0, "phase": "audio", "cur_text": "chờ encode audio…",
                   "updated_at": time.time()})
        m.data["stages"][STAGE] = st
        js.persist(m)
    while aproc.poll() is None:
        time.sleep(1.5)
        if (work_dir / ".stop").exists():
            aproc.kill()
            aproc.wait()
            (work_dir / ".ffmpeg.pid").unlink(missing_ok=True)
            _cleanup()
            return 1, "stopped by user"
    (work_dir / ".ffmpeg.pid").unlink(missing_ok=True)
    aud_ok = aproc.returncode == 0 and aud.exists() and aud.stat().st_size > 0
    if not aud_ok:
        print(f"[render] encode audio song song lỗi, encode lại lúc ghép: "
              f"{(aproc.stderr.read() if aproc.stderr else '')[-200:]}", flush=True)

    lines = []
    for i, f in enumerate(parts):
        lines.append(f"file '{f.name}'")
        lines.append(f"inpoint {bounds[i]:.6f}")
        if bounds[i + 1] is not None:
            lines.append(f"outpoint {bounds[i + 1]:.6f}")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    st = m.data["stages"].get(STAGE, {})
    st.update({"status": "running", "progress": 99.0, "phase": "concat", "cur_text": "ghép đoạn + audio…",
               "updated_at": time.time()})
    m.data["stages"][STAGE] = st
    js.persist(m)
    if aud_ok:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-i", aud.name,
               "-map", "0:v", "-map", "1:a", "-c", "copy"]
    else:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-i", _ff_input(mixed, work_dir),
               "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", abit]
    if total and total > 0:
        cmd += ["-t", f"{total:.3f}"]
    cmd += ["-loglevel", "error", out_tmp.name]
    p = subprocess.run(cmd, cwd=str(work_dir), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    _cleanup()
    if p.returncode != 0:
        return p.returncode, f"ghép đoạn lỗi: {(p.stderr or '')[-400:]}"
    return 0, ""


# Encoder: env/config RENDER_ENCODER = auto | nvenc | x264
# auto: thử GPU; fail 2 lần liên tiếp -> lần sau bỏ nvenc (file .nvenc_state.json).
_NVENC_STATE = Path(__file__).resolve().parents[1] / ".nvenc_state.json"
_NVENC_FAIL_SKIP = 2


def _encoder_pref() -> str:
    try:
        import core.config as _cfg   # RENDER_ENCODER file-first: sửa pineline.config.json ăn ngay
        v = (_cfg.get("RENDER_ENCODER", "auto") or "auto").strip().lower()
    except Exception:  # noqa: BLE001
        v = (os.getenv("RENDER_ENCODER") or "auto").strip().lower()
    if v in ("nvenc", "gpu", "h264_nvenc"):
        return "nvenc"
    if v in ("x264", "cpu", "libx264"):
        return "x264"
    return "auto"


def _nvenc_state() -> dict:
    try:
        if _NVENC_STATE.exists():
            d = json.loads(_NVENC_STATE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception:  # noqa: BLE001
        pass
    return {"fails": 0, "skip": False}


def _nvenc_save(d: dict) -> None:
    try:
        _NVENC_STATE.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _want_nvenc() -> bool:
    pref = _encoder_pref()
    if pref == "x264":
        print("[render] RENDER_ENCODER=x264 → CPU libx264", flush=True)
        return False
    if pref == "nvenc":
        print("[render] RENDER_ENCODER=nvenc → ép GPU h264_nvenc", flush=True)
        return True
    st = _nvenc_state()
    if st.get("skip"):
        print(f"[render] bỏ nvenc (đã lỗi {st.get('fails', 0)} lần). "
              "Thử lại: set RENDER_ENCODER=nvenc hoặc xoá .nvenc_state.json", flush=True)
        return False
    return True


def _nvenc_mark_ok() -> None:
    _nvenc_save({"fails": 0, "skip": False})


def _nvenc_mark_fail(err: str) -> None:
    # Lỗi parse filter/cmdline Windows ≠ lỗi NVENC — không đếm skip.
    el = (err or "").lower()
    if any(x in el for x in (
        "filter_complex", "error parsing", "no such file or directory",
        "invalid argument", "option '",
    )) and "nvenc" not in el and "cuda" not in el:
        print("[render] lỗi filter/cmdline (không tính fail NVENC)", flush=True)
        return
    st = _nvenc_state()
    n = int(st.get("fails") or 0) + 1
    skip = n >= _NVENC_FAIL_SKIP
    _nvenc_save({"fails": n, "skip": skip, "last_error": (err or "")[:300]})
    if skip:
        print(f"[render] nvenc lỗi {n} lần → lần sau dùng CPU luôn "
              "(RENDER_ENCODER=nvenc để ép thử GPU lại)", flush=True)


def _attach_filter_complex(cmd: list[str], graph: str, work_dir: Path) -> Path:
    """Ghi filtergraph ra file — tránh cắt cmdline Windows (~8191 ký tự)."""
    script = Path(work_dir) / "_filter_complex.txt"
    script.write_text(graph, encoding="utf-8")
    cmd += ["-filter_complex_script", script.name]
    return script


def _ff_input(path: Path | str, work_dir: Path) -> str:
    """Đường dẫn -i khi ffmpeg cwd=work_dir: file trong job → tên/relative; ngoài → absolute."""
    p = Path(path).resolve()
    wd = Path(work_dir).resolve()
    try:
        return str(p.relative_to(wd)).replace("\\", "/")
    except ValueError:
        return str(p)


def _x264_cmd(cmd: list[str]) -> list[str]:
    """Đổi lệnh nvenc → libx264 (bỏ flag chỉ GPU)."""
    cmd = [c if c != "h264_nvenc" else "libx264" for c in cmd]
    cmd = [c if c != "p4" else "veryfast" for c in cmd]
    out: list[str] = []
    skip_next = False
    for c in cmd:
        if skip_next:
            skip_next = False
            continue
        if c in ("-rc", "-gpu"):
            skip_next = True
            continue
        out.append(c)
    return out


def normalize_vbitrate(v: str | None, W: int = 1920, H: int = 1080) -> tuple[str, str]:
    """Chuẩn hoá bitrate H264: trả (vbit, buf). Cho phép 2M | 4M | 8M."""
    raw = (v or "8M").strip().lower()
    aliases = {
        "2": "2M", "2m": "2M", "low": "2M", "light": "2M", "strong": "2M",
        "4": "4M", "4m": "4M", "medium": "4M", "mid": "4M", "balance": "4M",
        "8": "8M", "8m": "8M", "high": "8M", "normal": "8M", "default": "8M",
        "auto": "auto",
    }
    key = aliases.get(raw, raw if raw.endswith("m") else "8M")
    if key == "auto":
        key = "8M" if H >= 1000 or W >= 1800 else "4M"
    if key not in ("2M", "4M", "8M"):
        key = "8M"
    n = int(key[:-1])
    return key, f"{n * 2}M"


STAGE = "mux"                 # giữ tên stage 'mux' để khớp WIRED_STAGES cũ
FONT = "C\\:/Windows/Fonts/arial.ttf"     # Arial hỗ trợ tiếng Việt; ':' phải escape


def _find_video(work_dir: Path) -> Path | None:
    # agentK 2026-11-12: 1 hàm resolve nguồn (preflight chờ → chạy; nguồn chuẩn hoá > gốc)
    from engines.source_preflight import stage_source_video
    return stage_source_video(work_dir)


def _size(video: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video)],
        capture_output=True, text=True).stdout.strip()
    w, h = out.split("x")[:2]
    return int(w), int(h)


def _fps(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "default=nk=1:nw=1", str(video)],
        capture_output=True, text=True).stdout.strip()
    try:
        if "/" in out:
            a, b = out.split("/", 1)
            return max(1.0, float(a) / float(b))
        return max(1.0, float(out))
    except Exception:  # noqa: BLE001
        return 25.0


def _esc(t: str) -> str:
    """Escape text cho drawtext."""
    return (t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")
             .replace("%", "\\%"))


def _map_pt(fx: float, fy: float, zoom: float) -> tuple[float, float]:
    """Toạ độ fraction (0-1) sau khi zoom+crop giữa."""
    off = (zoom - 1) / 2
    return fx * zoom - off, fy * zoom - off


def _map_box(x0: float, y0: float, x1: float, y1: float, zoom: float,
             pad: float = 0.0) -> tuple[float, float, float, float] | None:
    """Map hộp nguồn → sau zoom/crop; None nếu gần như bị crop hết."""
    if zoom <= 1.01:
        nx0, ny0 = x0 - pad, y0 - pad
        nx1, ny1 = x1 + pad, y1 + pad
    else:
        pts = [
            _map_pt(x0, y0, zoom), _map_pt(x1, y0, zoom),
            _map_pt(x0, y1, zoom), _map_pt(x1, y1, zoom),
        ]
        nx0 = min(p[0] for p in pts) - pad
        ny0 = min(p[1] for p in pts) - pad
        nx1 = max(p[0] for p in pts) + pad
        ny1 = max(p[1] for p in pts) + pad
    nx0, ny0 = max(0.0, nx0), max(0.0, ny0)
    nx1, ny1 = min(1.0, nx1), min(1.0, ny1)
    if (nx1 - nx0) < 0.012 or (ny1 - ny0) < 0.012:
        return None
    return nx0, ny0, nx1, ny1


def _flip_box(x0: float, y0: float, x1: float, y1: float,
              hflip: bool, vflip: bool) -> tuple[float, float, float, float]:
    if hflip:
        x0, x1 = 1.0 - x1, 1.0 - x0
    if vflip:
        y0, y1 = 1.0 - y1, 1.0 - y0
    return x0, y0, x1, y1


def _default_wm_corners() -> list[dict]:
    """Fallback góc kênh khi OCR không bắt được logo hình (ô nhỏ, chỉ mép)."""
    raw = (os.getenv("WM_FORCE_CORNERS") or cfg("WM_FORCE_CORNERS", "tr") or "tr").strip().lower()
    if raw in ("0", "off", "none", "false", "no"):
        return []
    want = {p.strip() for p in raw.replace(";", ",").split(",") if p.strip()}
    if "all" in want:
        want = {"tl", "tr", "bl", "br"}
    out: list[dict] = []
    # ~14%×9% — đủ logo góc, không nuốt góc hình
    presets = {
        "tr": (0.86, 0.0, 0.14, 0.09),
        "tl": (0.0, 0.0, 0.14, 0.09),
        "br": (0.86, 0.91, 0.14, 0.09),
        "bl": (0.0, 0.91, 0.14, 0.09),
    }
    for q, (x, y, w, h) in presets.items():
        if q in want:
            out.append({"t0": 0.0, "t1": None, "x": x, "y": y, "w": w, "h": h, "q": q})
    return out


_OVERLAY_CACHE_V = 12
_WM_MAX_W = 0.16
_WM_MAX_H = 0.10
_WM_HINTS = (
    "bilibili", "bili", "抖音", "douyin", "超高清", "4k修复", "4K修复",
    "高清修复", "ai生成", "ai 生成", "西瓜", "腾讯", "优酷",
)


def _uploader_hint(work_dir: Path) -> str:
    try:
        meta = json.loads((work_dir / "meta.json").read_text(encoding="utf-8"))
        return (meta.get("uploader") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _is_wm_text(txt: str, uploader: str = "") -> bool:
    t = (txt or "").strip()
    if not t:
        return False
    low = t.lower()
    if any(h in low for h in _WM_HINTS):
        return True
    if uploader and (uploader in t or t in uploader):
        return True
    import re
    if re.search(r"[一-鿿].*\d|\d.*[一-鿿]", t) and len(t) <= 24:
        return True
    return False


def _wm_quadrant(cx: float, cy: float) -> str | None:
    # Nới nhẹ so với mép — logo kênh hay nằm hơi lệch vào trong
    if cy < 0.22 and cx < 0.42:
        return "tl"
    if cy < 0.22 and cx > 0.58:
        return "tr"
    if cy > 0.74 and cx < 0.42:
        return "bl"
    if cy > 0.74 and cx > 0.55:
        return "br"
    return None


def _expand_logo_pad(q: str, x0, y0, x1, y1) -> tuple[float, float, float, float]:
    """OCR chữ → snap nhẹ về mép góc; giữ kích thước gần bbox, cap nhỏ."""
    pad = 0.01
    bw = max(0.04, min(_WM_MAX_W, (x1 - x0) + 2 * pad))
    bh = max(0.035, min(_WM_MAX_H, (y1 - y0) + 2 * pad))
    # Nếu OCR quá hẹp (chỉ bắt chữ), nới tối thiểu để phủ icon cạnh chữ
    bw = max(bw, min(_WM_MAX_W, (x1 - x0) * 1.35 + 0.02))
    bh = max(bh, min(_WM_MAX_H, (y1 - y0) * 1.35 + 0.015))
    if q == "br":
        x1, y1 = 0.998, 0.998
        x0, y0 = max(0.0, x1 - bw), max(0.0, y1 - bh)
    elif q == "tr":
        x1, y0 = 0.998, 0.002
        x0, y1 = max(0.0, x1 - bw), min(1.0, y0 + bh)
    elif q == "tl":
        x0, y0 = 0.002, 0.002
        x1, y1 = min(1.0, x0 + bw), min(1.0, y0 + bh)
    elif q == "bl":
        x0, y1 = 0.002, 0.998
        x1, y0 = min(1.0, x0 + bw), max(0.0, y1 - bh)
    else:
        x0, y0 = max(0.0, x0 - pad), max(0.0, y0 - pad)
        x1, y1 = min(1.0, x1 + pad), min(1.0, y1 + pad)
    return (
        max(0.0, x0), max(0.0, y0),
        min(x1 - x0, 1.0 - max(0.0, x0)), min(y1 - y0, 1.0 - max(0.0, y0)),
    )


def _cap_wm_frac(x0: float, y0: float, x1: float, y1: float, q: str = "") -> tuple[float, float, float, float]:
    """Giới hạn hộp che sau map — không cho phình quá mép góc."""
    w, h = x1 - x0, y1 - y0
    if w <= _WM_MAX_W and h <= _WM_MAX_H:
        return x0, y0, x1, y1
    cw, ch = min(w, _WM_MAX_W), min(h, _WM_MAX_H)
    q = (q or "").lower()
    if q == "br" or (not q and x0 > 0.5 and y0 > 0.5):
        return max(0.0, 1.0 - cw), max(0.0, 1.0 - ch), 1.0, 1.0
    if q == "tr" or (not q and x0 > 0.5 and y1 < 0.5):
        return max(0.0, 1.0 - cw), 0.0, 1.0, min(1.0, ch)
    if q == "tl" or (not q and x1 < 0.5 and y1 < 0.5):
        return 0.0, 0.0, min(1.0, cw), min(1.0, ch)
    if q == "bl" or (not q and x1 < 0.5 and y0 > 0.5):
        return 0.0, max(0.0, 1.0 - ch), min(1.0, cw), 1.0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return max(0.0, cx - cw / 2), max(0.0, cy - ch / 2), min(1.0, cx + cw / 2), min(1.0, cy + ch / 2)


def _wm_sample_times(full_dur: float) -> tuple[list[float], float]:
    """Mốc giây lấy mẫu watermark trên TOÀN bộ video (không chỉ 3 phút đầu)."""
    try:
        stride = float(os.getenv("WM_SAMPLE_SEC") or "45")
    except ValueError:
        stride = 45.0
    stride = max(15.0, stride)
    try:
        cap = int(os.getenv("WM_MAX_SAMPLES") or "90")
    except ValueError:
        cap = 90
    cap = max(12, min(cap, 200))
    if full_dur <= 0:
        return [0.0], stride
    if full_dur / stride > cap:
        stride = full_dur / cap
    times = []
    t = 0.0
    while t < full_dur - 0.25 and len(times) < cap:
        times.append(round(t, 2))
        t += stride
    end = max(0.0, full_dur - 1.0)
    if not times or times[-1] < end - stride * 0.4:
        times.append(round(end, 2))
    return times, stride


def _extract_wm_frames(video: Path, out_dir: Path, times: list[float]) -> list[tuple[Path, float]]:
    """Seek từng mốc → 1 frame JPEG. Nhanh hơn decode cả file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("wm_*.jpg"):
        try:
            f.unlink()
        except OSError:
            pass
    pairs = []
    for i, t in enumerate(times):
        out = out_dir / f"wm_{i:04d}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", str(video),
                 "-frames:v", "1", "-q:v", "4", str(out), "-loglevel", "error"],
                check=True, timeout=60)
        except Exception:  # noqa: BLE001
            continue
        if out.exists() and out.stat().st_size > 500:
            pairs.append((out, float(t)))
    return pairs


def _merge_wm_timeline(
    hits: list[tuple[float, str, float, float, float, float, str]],
    stride: float, full_dur: float, uploader: str,
) -> list[dict]:
    """Gộp hit (t,q,x0,y0,x1,y1,txt) → [{t0,t1,x,y,w,h}, …] theo góc + thời gian."""
    from collections import defaultdict
    by_q: dict[str, list] = defaultdict(list)
    for h in hits:
        by_q[h[1]].append(h)
    gap = max(stride * 1.6, 25.0)
    pad_t = max(stride * 0.55, 8.0)
    segs: list[dict] = []
    for q, items in by_q.items():
        items = sorted(items, key=lambda x: x[0])
        if not items:
            continue
        # Cụm thời gian liền kề cùng góc
        clusters: list[list] = [[items[0]]]
        for h in items[1:]:
            if h[0] - clusters[-1][-1][0] <= gap:
                clusters[-1].append(h)
            else:
                clusters.append([h])
        for cl in clusters:
            # Cần ≥2 hit, hoặc 1 hit khớp tên kênh/hint rõ
            strong = any(_is_wm_text(h[6], uploader) for h in cl)
            if len(cl) < 2 and not strong:
                continue
            # Median bbox — KHÔNG dùng min/max union (131 frame → ô khổng lồ)
            import statistics as _st
            ws = [h[4] - h[2] for h in cl]
            hs = [h[5] - h[3] for h in cl]
            xs0 = [h[2] for h in cl]
            ys0 = [h[3] for h in cl]
            med_w = float(_st.median(ws))
            med_h = float(_st.median(hs))
            # Neo theo góc: median mép gần corner
            if q == "br":
                x1 = float(_st.median([h[4] for h in cl]))
                y1 = float(_st.median([h[5] for h in cl]))
                x0, y0 = x1 - med_w, y1 - med_h
            elif q == "tr":
                x1 = float(_st.median([h[4] for h in cl]))
                y0 = float(_st.median(ys0))
                x0, y1 = x1 - med_w, y0 + med_h
            elif q == "tl":
                x0 = float(_st.median(xs0))
                y0 = float(_st.median(ys0))
                x1, y1 = x0 + med_w, y0 + med_h
            elif q == "bl":
                x0 = float(_st.median(xs0))
                y1 = float(_st.median([h[5] for h in cl]))
                x1, y0 = x0 + med_w, y1 - med_h
            else:
                x0 = float(_st.median(xs0))
                y0 = float(_st.median(ys0))
                x1, y1 = x0 + med_w, y0 + med_h
            fx, fy, fw, fh = _expand_logo_pad(q, x0, y0, x1, y1)
            t0 = max(0.0, cl[0][0] - pad_t)
            t1 = min(full_dur, cl[-1][0] + pad_t)
            # Xuất hiện gần như suốt phim → phủ full (tránh nhấp nháy delogo)
            span = cl[-1][0] - cl[0][0]
            if span >= full_dur * 0.55 or (len(cl) >= 6 and span >= full_dur * 0.35):
                t0, t1 = 0.0, full_dur
            segs.append({
                "t0": round(t0, 2), "t1": round(t1, 2),
                "x": round(fx, 4), "y": round(fy, 4),
                "w": round(fw, 4), "h": round(fh, 4),
                "q": q, "n": len(cl),
            })
    segs.sort(key=lambda s: (s["t0"], s["q"]))
    return segs


def _parse_wm_cache(raw: dict) -> list:
    """Đọc cache: list dict timed, hoặc list [x,y,w,h] cũ → coi full video."""
    wms = []
    for it in raw.get("wm") or []:
        if isinstance(it, dict) and all(k in it for k in ("x", "y", "w", "h")):
            wms.append(it)
        elif isinstance(it, (list, tuple)) and len(it) >= 6:
            wms.append({
                "t0": float(it[0]), "t1": float(it[1]),
                "x": float(it[2]), "y": float(it[3]),
                "w": float(it[4]), "h": float(it[5]),
            })
        elif isinstance(it, (list, tuple)) and len(it) >= 4:
            wms.append({
                "t0": 0.0, "t1": None,
                "x": float(it[0]), "y": float(it[1]),
                "w": float(it[2]), "h": float(it[3]),
            })
    return wms


def detect_overlays(work_dir, n_frames: int = 40, force: bool = False):
    """Dò hardsub (đầu phim) + watermark THEO THỜI GIAN trên cả video.

    Trả (band|None, wm_segs) — wm_segs = [{t0,t1,x,y,w,h}, …].
    """
    work_dir = Path(work_dir)
    cache = work_dir / "hardsub_band.json"
    if cache.exists() and not force:
        try:
            raw = json.loads(cache.read_text(encoding="utf-8"))
            if int(raw.get("v") or 0) >= _OVERLAY_CACHE_V:
                band = None
                b = raw.get("band")
                if b:
                    def _f(x):
                        if isinstance(x, (int, float)):
                            return float(x)
                        return float(str(x).strip().strip(",").split(",")[0])
                    t = tuple(_f(x) for x in b)
                    band = t[:4] if len(t) >= 4 else t
                return band, _parse_wm_cache(raw)
        except Exception:  # noqa: BLE001
            pass
    video = _find_video(work_dir)
    if not video:
        return None, []
    W, H = _size(video)
    full_dur = _probe_dur(video) or 0.0
    try:
        import re
        import shutil
        import statistics
        from engines.ocr import extract_keyframes
        from rapidocr_onnxruntime import RapidOCR
    except Exception:  # noqa: BLE001
        return None, []
    uploader = _uploader_hint(work_dir)
    cjk = re.compile(r"[一-鿿]")
    ocr = RapidOCR()

    # --- Hardsub: vài phút đầu (ổn định dải thoại) ---
    fdir = work_dir / "_hsframes"
    hs_dur = min(full_dur or 180.0, 180.0)
    frames = extract_keyframes(video, fdir, dur=hs_dur, stride=1)
    if len(frames) > n_frames:
        frames = frames[:: max(1, len(frames) // n_frames)][:n_frames]
    rows = []
    for img, _t in frames:
        try:
            res, _ = ocr(str(img))
        except Exception:  # noqa: BLE001
            continue
        for box, txt, conf in (res or []):
            if conf < 0.45 or not cjk.search(txt or ""):
                continue
            xs = [p[0] for p in box]; ys = [p[1] for p in box]
            x0, x1 = min(xs) / W, max(xs) / W
            y0, y1 = min(ys) / H, max(ys) / H
            w = x1 - x0
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if w < 0.12 or not (0.12 < cx < 0.88):
                continue
            if x0 > 0.62 or x1 > 0.95:
                continue
            # bỏ vùng giữa màn (logo/title), giữ hardsub đáy/trên
            if 0.38 < cy < 0.52:
                continue
            rows.append((cy, y0, y1, x0, x1))
    shutil.rmtree(fdir, ignore_errors=True)
    band = None
    if len(rows) >= max(2, len(frames) * 0.12):
        bottomish = [r for r in rows if r[0] >= 0.55]
        use = bottomish if len(bottomish) >= max(2, len(rows) * 0.30) else rows
        med = statistics.median(r[0] for r in use)
        near = [r for r in use if abs(r[0] - med) < 0.12]
        if len(near) >= max(2, len(use) * 0.35):
            y0 = min(r[1] for r in near)
            y1 = max(r[2] for r in near)
            x0 = min(r[3] for r in near)
            x1 = max(r[4] for r in near)
            if med >= 0.55:
                mid = (x0 + x1) / 2.0
                half = max((x1 - x0) / 2.0, 0.38)
                x0 = max(0.06, mid - half)
                x1 = min(0.94, mid + half)
            # Pad mỏng — khung gọn
            y0 = max(0.0, y0 - 0.012)
            y1 = min(1.0, y1 + 0.008)
            x0 = max(0.0, x0 - 0.04)
            x1 = min(0.98, x1 + 0.04)
            band = (y0, y1, x0, x1)

    # --- Watermark timeline: lấy mẫu rải cả phim ---
    times, stride = _wm_sample_times(full_dur if full_dur > 1 else hs_dur)
    print(f"[watermark] mẫu {len(times)} frame / {full_dur:.0f}s (stride~{stride:.0f}s)", flush=True)
    wdir = work_dir / "_wmframes"
    wm_frames = _extract_wm_frames(video, wdir, times)
    hits: list[tuple[float, str, float, float, float, float, str]] = []
    for img, t in wm_frames:
        try:
            res, _ = ocr(str(img))
        except Exception:  # noqa: BLE001
            continue
        for box, txt, conf in (res or []):
            if conf < 0.40:
                continue
            xs = [p[0] for p in box]; ys = [p[1] for p in box]
            x0, x1 = min(xs) / W, max(xs) / W
            y0, y1 = min(ys) / H, max(ys) / H
            w = x1 - x0
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            tx = (txt or "").strip()
            q = _wm_quadrant(cx, cy)
            if not q:
                continue
            if _is_wm_text(tx, uploader) or (cjk.search(tx) and w < 0.42):
                hits.append((t, q, x0, y0, x1, y1, tx))
    shutil.rmtree(wdir, ignore_errors=True)
    wm = _merge_wm_timeline(hits, stride, full_dur or hs_dur, uploader)

    payload = {
        "v": _OVERLAY_CACHE_V,
        "band": list(band) if band else None,
        "wm": wm,
        "wm_sample": {"n": len(times), "stride": round(stride, 2), "hits": len(hits)},
    }
    if band and len(band) >= 4:
        payload["x0"], payload["x1"] = band[2], band[3]
    cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pos = "TRÊN" if (band and band[0] < 0.4) else ("DƯỚI" if band else "—")
    print(f"[hardsub] dò dải: {('%.3f-%.3f (%s)' % (band[0], band[1], pos)) if band else 'không phát hiện hardsub'}")
    brief = ", ".join(f"{s['q']}@{s['t0']:.0f}-{s['t1']:.0f}s" for s in wm[:8])
    print(f"[watermark] timeline {len(wm)} đoạn" + (f" ({brief})" if brief else ""), flush=True)
    return band, wm


def detect_hardsub_band(work_dir, n_frames: int = 40, force: bool = False):
    """Tương thích cũ: chỉ trả band."""
    band, _wm = detect_overlays(work_dir, n_frames=n_frames, force=force)
    return band


_HS_TRACK_V = 1


def _hs_cluster_band(rows: list[tuple]) -> tuple[float, float]:
    """(y0,y1) của 1 cụm vị trí — bỏ 5% ngoại lai mỗi phía khi đủ mẫu."""
    y0s = sorted(r[1] for r in rows)
    y1s = sorted(r[2] for r in rows)
    k = len(rows) // 20 if len(rows) >= 20 else 0
    return y0s[k], y1s[len(y1s) - 1 - k]


def detect_hardsub_track(work_dir, force: bool = False) -> dict | None:
    """Dò toạ độ hardsub THEO THỜI GIAN trên toàn video (file merge đổi vị trí giữa các tập).

    Trả {"x0","x1","bands":[{"y0","y1","ranges":[[t0,t1],…]}],…} theo tỉ lệ khung nguồn
    (bands rỗng = đã dò mà không thấy hardsub), None nếu không dò được.
    """
    work_dir = Path(work_dir)
    cache = work_dir / "hardsub_track.json"
    if cache.exists() and not force:
        try:
            raw = json.loads(cache.read_text(encoding="utf-8"))
            if int(raw.get("v") or 0) >= _HS_TRACK_V:
                return raw
        except Exception:  # noqa: BLE001
            pass
    video = _find_video(work_dir)
    if not video:
        return None
    import re
    import shutil
    from concurrent.futures import ThreadPoolExecutor
    from rapidocr_onnxruntime import RapidOCR

    dur = _probe_dur(video) or 0.0
    if dur <= 0:
        return None
    try:
        cap = int(cfg("HARDSUB_TRACK_MAX_SAMPLES", "300") or "300")
    except ValueError:
        cap = 300
    stride = max(20.0, dur / max(30, cap))
    times = [round((i + 0.5) * stride, 2) for i in range(int(dur / stride))] or [dur / 2]
    fdir = work_dir / "_hstrack"
    shutil.rmtree(fdir, ignore_errors=True)
    fdir.mkdir(parents=True, exist_ok=True)

    def _grab(it: tuple[int, float]) -> tuple[Path, float] | None:
        i, t = it
        out = fdir / f"hs_{i:05d}.jpg"
        try:
            subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=960:-2", "-q:v", "3", str(out), "-loglevel", "error"],
                           check=True, timeout=60)
        except Exception:  # noqa: BLE001
            return None
        return (out, t) if out.exists() and out.stat().st_size > 500 else None

    t_start = time.time()
    with ThreadPoolExecutor(4) as ex:
        frames = [f for f in ex.map(_grab, enumerate(times)) if f]
    import numpy as np
    from PIL import Image
    ocr = RapidOCR()
    cjk = re.compile(r"[一-鿿]")

    def _scan(img: Path, t: float, y_from: float, y_to: float) -> list[tuple]:
        """OCR dải [y_from,y_to] của khung → ứng viên hardsub (t,y0,y1,x0,x1) theo tỉ lệ cả khung."""
        try:
            with Image.open(img) as im:
                iw, ih = im.size
                oy = int(ih * y_from)
                arr = np.array(im.convert("RGB").crop((0, oy, iw, int(ih * y_to))))[:, :, ::-1].copy()
            res, _ = ocr(arr)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for box, txt, conf in res or []:
            if conf < 0.5 or not cjk.search(txt or ""):
                continue
            xs = [p[0] for p in box]; ys = [p[1] + oy for p in box]
            x0, x1 = min(xs) / iw, max(xs) / iw
            y0, y1 = min(ys) / ih, max(ys) / ih
            # Hardsub: 1 dòng căn giữa, cao ~4–12% khung
            if abs((x0 + x1) / 2 - 0.5) <= 0.02 and 0.04 <= (y1 - y0) <= 0.12 and (x1 - x0) >= 0.04:
                out.append((t, y0, y1, x0, x1))
        return out

    # Vùng (đáy/đỉnh) từ ~40 khung quét cả khung, rồi chỉ OCR nửa khung đó (nhanh ~2×).
    probe = frames[:: max(1, len(frames) // 40)]
    pc = [c for img, t in probe for c in _scan(img, t, 0.0, 1.0)]
    n_bot = len({c[0] for c in pc if (c[1] + c[2]) / 2 >= 0.6})
    n_top = len({c[0] for c in pc if (c[1] + c[2]) / 2 <= 0.4})
    zone = "bottom" if n_bot >= n_top else "top"
    y_from, y_to = (0.45, 1.0) if zone == "bottom" else (0.0, 0.55)
    cand: list[tuple[float, float, float, float, float]] = []   # t, y0, y1, x0, x1
    for img, t in frames:
        cand.extend(_scan(img, t, y_from, y_to))
    shutil.rmtree(fdir, ignore_errors=True)

    if zone == "bottom":
        rows = [c for c in cand if (c[1] + c[2]) / 2 >= 0.6]
    else:
        rows = [c for c in cand if (c[1] + c[2]) / 2 <= 0.4]
    n_hit = len({c[0] for c in rows})
    result: dict = {"v": _HS_TRACK_V, "zone": zone, "n": len(frames), "hits": n_hit,
                    "stride": round(stride, 2), "dur": round(dur, 3), "bands": []}
    # Tăng ngưỡng: Phải có ít nhất 25% số mẫu xuất hiện chữ thì mới kết luận là có dải hardsub
    # Tránh nhận nhầm chữ UI, hiệu ứng chiêu thức rải rác trong game/anime rồi bật che full video
    if n_hit < max(3, int(len(frames) * 0.25)):
        cache.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[hardsub] track: không thấy hardsub liên tục ({n_hit}/{len(frames)} mẫu, dưới ngưỡng 25%) -> KHÔNG che", flush=True)
        return result

    # 1 mẫu / mốc: chọn dòng gần vị trí mẫu trước nhất
    per_t: dict[float, list] = {}
    for c in rows:
        per_t.setdefault(c[0], []).append(c)
    picked, prev_cy = [], None
    for t in sorted(per_t):
        cs = per_t[t]
        if prev_cy is None:
            c = max(cs, key=lambda r: r[2] - r[1])
        else:
            c = min(cs, key=lambda r: abs((r[1] + r[2]) / 2 - prev_cy))
        prev_cy = (c[1] + c[2]) / 2
        picked.append(c)

    # Chia đoạn theo thời gian (tâm dọc lệch > 0.8% khung = vị trí mới), rồi gộp đoạn cùng vị trí.
    # Không gom toàn cục: các vị trí sát nhau (0.904 → 0.916 → 0.925) sẽ bị nối thành 1 dải quá cao.
    import statistics

    def _cy(i: int) -> float:
        return (picked[i][1] + picked[i][2]) / 2

    def _group() -> list[list[int]]:
        runs: list[list[int]] = []
        for k in range(len(picked)):
            if runs and abs(_cy(k) - statistics.median(_cy(i) for i in runs[-1][-15:])) <= 0.008:
                runs[-1].append(k)
            else:
                runs.append([k])
        gs: list[list[int]] = []
        for run in runs:
            med = statistics.median(_cy(i) for i in run)
            for g in gs:
                if abs(statistics.median(_cy(i) for i in g) - med) <= 0.008:
                    g.extend(run)
                    break
            else:
                gs.append(list(run))
        return gs

    groups = _group()
    # Cụm lẻ (<3 mẫu) nằm xa các dải chính = chữ UI căn giữa, không phải hardsub
    big = [g for g in groups if len(g) >= max(3, int(len(picked) * 0.02))] or [max(groups, key=len)]
    meds = [statistics.median(_cy(i) for i in g) for g in big]
    lo, hi = min(meds) - 0.03, max(meds) + 0.03
    drop = {i for g in groups if len(g) < 3 and not lo <= statistics.median(_cy(i) for i in g) <= hi
            for i in g}
    if drop:
        picked = [c for i, c in enumerate(picked) if i not in drop]
        groups = _group()
    cluster_of = [0] * len(picked)
    for gi, g in enumerate(groups):
        for i in g:
            cluster_of[i] = gi

    # Khoảng thời gian: hardsub anime gần như liên tục nhưng OCR miss → hở lớn nếu pad/merge hẹp.
    ranges: dict[int, list[list[float]]] = {}
    try:
        full_ratio = float(cfg("HARDSUB_FULL_COVER_RATIO", "0.40") or "0.40")
    except ValueError:
        full_ratio = 0.40
    full_ratio = max(0.25, min(0.95, full_ratio))
    try:
        gap_fill = float(cfg("HARDSUB_GAP_FILL_SEC", "0") or "0")
    except ValueError:
        gap_fill = 0.0
    if gap_fill <= 0:
        gap_fill = min(max(stride * 3.0, 45.0), 120.0)
    for cl in sorted(set(cluster_of)):
        cl_indices = [i for i, c in enumerate(cluster_of) if c == cl]
        if not cl_indices:
            continue
        cl_picked = [picked[i] for i in cl_indices]
        # Đủ dày (≥ full_ratio mẫu) → hardsub bền → phủ cả video (tránh hở giữa các miss OCR)
        if len(cl_picked) >= int(len(frames) * full_ratio):
            ranges[cl] = [[0.0, dur]]
            continue

        rs: list[list[float]] = []
        # Pad/merge đủ nối mẫu kế cả khi miss 1–2 khung (stride phim dài ~25–30s)
        half_pad = min(max(stride * 0.55, 8.0), 40.0)
        merge_gap = min(max(stride * 1.25, 8.0), 60.0)
        for c in cl_picked:
            t = c[0]
            cur_a = max(0.0, t - half_pad)
            cur_b = min(dur, t + half_pad)
            if rs and cur_a <= rs[-1][1] + merge_gap:
                rs[-1][1] = max(rs[-1][1], cur_b)
            else:
                rs.append([cur_a, cur_b])
        if len(rs) >= 2:
            filled = [rs[0]]
            for a, b in rs[1:]:
                if a <= filled[-1][1] + gap_fill:
                    filled[-1][1] = max(filled[-1][1], b)
                else:
                    filled.append([a, b])
            rs = filled
        ranges[cl] = rs

    half = max(max(abs(c[3] - 0.5), abs(c[4] - 0.5)) for c in picked)
    half = min(0.47, max(0.25, half + 0.03))
    result["x0"], result["x1"] = round(0.5 - half, 4), round(0.5 + half, 4)
    for cl in sorted(ranges):
        y0, y1 = _hs_cluster_band([picked[i] for i in range(len(picked)) if cluster_of[i] == cl])
        result["bands"].append({
            "y0": round(y0, 4), "y1": round(y1, 4),
            "n": sum(1 for x in cluster_of if x == cl),
            "ranges": [[round(a, 2), round(b, 2)] for a, b in ranges[cl]],
        })
    cache.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    brief = " · ".join(f"y={b['y0']:.3f}-{b['y1']:.3f}×{len(b['ranges'])}đoạn" for b in result["bands"])
    print(f"[hardsub] track {len(result['bands'])} dải ({n_hit}/{len(frames)} mẫu, "
          f"{time.time() - t_start:.0f}s) x={result['x0']:.3f}-{result['x1']:.3f}: {brief}", flush=True)
    return result


def _even(n: int | float) -> int:
    return max(2, int(n) // 2 * 2)


def _rounded_bar_alpha(w: int, h: int, r: int, alpha: float) -> str:
    """Biểu thức geq alpha cho hình chữ nhật bo góc (0–255)."""
    r = max(2, min(int(r), w // 5, h // 2))
    a_in = max(1, min(255, int(round(255 * max(0.05, min(1.0, float(alpha)))))))
    # Escape dấu phẩy trong expression (filtergraph)
    return (
        f"if(lt(X\\,{r})*lt(Y\\,{r})\\,"
        f"if(lte(hypot({r}-X\\,{r}-Y)\\,{r})\\,{a_in}\\,0)\\,"
        f"if(gt(X\\,{w - r})*lt(Y\\,{r})\\,"
        f"if(lte(hypot(X-({w - r})\\,{r}-Y)\\,{r})\\,{a_in}\\,0)\\,"
        f"if(lt(X\\,{r})*gt(Y\\,{h - r})\\,"
        f"if(lte(hypot({r}-X\\,Y-({h - r}))\\,{r})\\,{a_in}\\,0)\\,"
        f"if(gt(X\\,{w - r})*gt(Y\\,{h - r})\\,"
        f"if(lte(hypot(X-({w - r})\\,Y-({h - r}))\\,{r})\\,{a_in}\\,0)\\,"
        f"{a_in}))))"
    )


def _boxblur_r(desired: int, w: int, h: int, sub: int = 0) -> int:
    """Radius boxblur hợp lệ: ffmpeg yêu cầu 2*r < min(w,h) của plane.
    crop trên yuv420p làm tròn w/h xuống số chẵn; sub=1 cho plane màu (nửa kích thước)."""
    m = min(int(w) // 2 * 2, int(h) // 2 * 2) >> sub
    max_r = max(0, (m - 1) // 2)
    if max_r < 1:
        return 0
    return max(1, min(int(desired), max_r))


def _safe_crop(w: int, h: int, x: int, y: int) -> str:
    """crop ffmpeg an toàn khi iw/ih đổi giữa chừng (biểu thức min)."""
    w, h, x, y = int(w), int(h), int(x), int(y)
    return (
        f"crop=w='min({w}\\,iw)':h='min({h}\\,ih)':"
        f"x='min({x}\\,max(0\\,iw-ow))':y='min({y}\\,max(0\\,ih-oh))'"
    )


def _hardsub_bar_filter(bw: int, bh: int, bx: int, by: int) -> str:
    """Che hardsub: 1× boxblur + lớp đen geq bo góc (không desat).

    - drawbox = góc vuông (user báo thiếu bo tròn).
    - geq chỉ r=g=b=0 + alpha bo góc — không tính luma ×3 (tránh chậm).
    - Vẫn không dùng -hwaccel khi hardsub (tránh xanh CUDA).
    """
    try:
        bar_a = float(cfg("HARDSUB_BAR_ALPHA", "0.95") or "0.95")
    except ValueError:
        bar_a = 0.95
    bar_a = max(0.92, min(1.0, bar_a))
    try:
        rad = int(float(cfg("HARDSUB_BAR_RADIUS", "16") or "16"))
    except ValueError:
        rad = 16
    br = _boxblur_r(max(12, int(cfg("HARDSUB_BLUR_R", "24") or "24")), bw, bh)
    chroma = _boxblur_r(max(6, br // 2), bw, bh, sub=1)
    rad = max(8, min(rad, bh // 2 - 1, bw // 8))
    a_blk = _rounded_bar_alpha(bw, bh, rad, bar_a)
    crop = _safe_crop(bw, bh, bx, by)
    # Mặt nạ bo góc tĩnh: geq chỉ tính 1 khung, overlay (eof_action=repeat) giữ nó suốt video.
    # geq trên luồng video tính lại từng pixel mỗi khung → chậm ~1/3 cả lượt render.
    return (
        f"split=2[base][s];[s]{crop}[c1];"
        f"[c1]boxblur={br}:2:{chroma}:2[blurred];"
        f"[base][blurred]overlay={bx}:{by}[mid];"
        f"color=c=black:s={int(bw)}x{int(bh)}:r=1:d=1,format=rgba,"
        f"geq=r='0':g='0':b='0':a='{a_blk}'[blk];"
        f"[mid][blk]overlay={bx}:{by}"
    )


def _hardsub_cover_fullw() -> bool:
    """Luôn phủ hết ngang khi che hardsub — OCR x0/x1 thường chỉ ~50% giữa → chữ Trung lòi 2 mép."""
    v = (cfg("HARDSUB_COVER_FULLW") or cfg("HARDSUB_STRETCH_FULLW") or "1")
    return str(v).strip().lower() not in ("0", "false", "no", "off")


def _hardsub_track_boxes(track: dict, W: int, H: int, hflip: bool, vflip: bool
                         ) -> list[tuple[int, int, int, int, list, float]]:
    """Dải hardsub (tỉ lệ nguồn) → ô pixel trên canvas TRƯỚC zoom: (bx,by,bw,bh,ranges,tổng giây).

    Ngang: mặc định FULL width (không tin OCR x0/x1 — hay hẹp → chữ Trung lộ 2 bên).
    Dọc: nới pad trên/dưới để phủ hết glyph.
    HARDSUB_FIXED_BOTTOM=1: dải chính (nhiều mẫu nhất) phủ cả video — OCR miss không còn hở đoạn.
    """
    try:
        pad_t = float(cfg("HARDSUB_PAD_TOP", "0.022") or "0.022")
        pad_b = float(cfg("HARDSUB_PAD_BOTTOM", "0.018") or "0.018")
    except ValueError:
        pad_t, pad_b = 0.022, 0.018
    dur = float(track.get("dur") or 0)
    full_w = _hardsub_cover_fullw()
    fixed_bottom = (cfg("HARDSUB_FIXED_BOTTOM", "0") or "0").strip().lower() in (
        "1", "true", "yes", "on")
    force_blur = (cfg("HARDSUB_FORCE_BLUR", "0") or "0").strip().lower() in (
        "1", "true", "yes")
    bands = list(track.get("bands") or [])
    primary_i = None
    if fixed_bottom and bands:
        def _band_weight(bb: dict) -> float:
            n = float(bb.get("n") or 0)
            if n > 0:
                return n
            return sum(float(e) - float(a) for a, e in (bb.get("ranges") or []))

        primary_i = max(range(len(bands)), key=lambda i: _band_weight(bands[i]))
    out = []
    for bi, b in enumerate(bands):
        if full_w:
            x0, x1 = 0.0, 1.0
        else:
            x0 = float(track.get("x0") or b.get("x0") or 0.08)
            x1 = float(track.get("x1") or b.get("x1") or 0.92)
        y0 = max(0.0, float(b["y0"]) - pad_t)
        y1 = min(1.0, float(b["y1"]) + pad_b)
        x0, y0, x1, y1 = _flip_box(x0, y0, x1, y1, hflip, vflip)
        bx = max(0, int(W * x0) // 2 * 2)
        by = max(0, int(H * y0) // 2 * 2)
        bw = min(W - bx, max(4, (int(W * x1 + 1) - bx) // 2 * 2))
        bh = min(H - by, max(4, (int(H * y1 + 1) - by) // 2 * 2))
        rs = [(float(a), float(e)) for a, e in b.get("ranges") or []]
        total = sum(e - a for a, e in rs)
        # Phủ full video: FIXED_BOTTOM dải chính / FORCE / đã phủ ≥90% thời lượng
        if (fixed_bottom and bi == primary_i) or force_blur or (
                dur and rs and rs[0][0] <= 0.5 and rs[-1][1] >= dur - 0.5
                and total >= dur * 0.90):
            rs = []
            total = dur if dur > 0 else total
        out.append((bx, by, bw, bh, rs, total))
    return out


_STRETCH_ROWS, _STRETCH_GAP = 6, 4


def _stretch_cfg() -> tuple[float, float, bool]:
    """(sigma ngang, sigma dọc, phủ hết bề ngang) cho kiểu che stretch."""
    try:
        sx = float(cfg("HARDSUB_STRETCH_SIGMA", "0") or "0")
        sy = float(cfg("HARDSUB_STRETCH_SIGMA_V", "0") or "0")
    except ValueError:
        sx, sy = 0.0, 0.0
    full = (cfg("HARDSUB_STRETCH_FULLW", "1") or "1").strip() not in ("0", "false", "no")
    return max(0.0, sx), max(0.0, sy), full


def _stretch_chain(tag: str, bx: int, bw: int, bh: int, ty: str, oy: str, en: str) -> str:
    """Che hardsub kiểu kéo giãn: trung bình vài hàng ngay trên ô, kéo dọc phủ ô rồi làm mềm
    — 1 lớp vệt dọc giữ màu cảnh, mép trên liền hình."""
    S = _STRETCH_ROWS
    sx, sy, _full = _stretch_cfg()
    soft = f",gblur=sigma={sx:g}:sigmaV={sy:g}" if sx > 0 or sy > 0 else ""
    return (
        f"split=2[hb{tag}][ht{tag}];"
        f"[ht{tag}]crop=w={bw}:h={S}:x={bx}:y='{ty}',format=yuv444p,scale={bw}:1:flags=area,"
        f"scale={bw}:{bh}:flags=neighbor{soft}[hbl{tag}];"
        f"[hb{tag}][hbl{tag}]overlay=x={bx}:y='{oy}'{en}"
    )


def _stretch_track_filter(boxes: list[tuple[int, int, int, int, list, float]], W: int, H: int) -> str:
    """Kiểu stretch cho mọi dải: khoảng nào nhiều dải cùng hiện thì phủ 1 ô gộp (không chồng 2 lớp).
    Mỗi chiều cao ô gộp → 1 chuỗi, các chuỗi bật ở khoảng thời gian không giao nhau."""
    S, G = _STRETCH_ROWS, _STRETCH_GAP
    full = [not b[4] for b in boxes]
    cuts = sorted({0.0} | {x for b in boxes for r in b[4] for x in r})
    cuts.append(float("inf"))
    bx = min(b[0] for b in boxes) // 2 * 2
    bw = _even(max(b[0] + b[2] for b in boxes) - bx)
    groups: dict[int, list[tuple[float, float, int]]] = {}
    for a, e in zip(cuts, cuts[1:]):
        mid = a + 1e-3
        act = [b for i, b in enumerate(boxes) if full[i] or any(r0 <= mid < r1 for r0, r1 in b[4])]
        if not act:
            continue
        top = min(b[1] for b in act) // 2 * 2
        h = min(_even(max(b[1] + b[3] for b in act) - top + 1), H)
        top = max(0, min(top, H - h))
        segs = groups.setdefault(h, [])
        if segs and segs[-1][2] == top and abs(segs[-1][1] - a) < 1e-6:
            segs[-1] = (segs[-1][0], e, top)
        else:
            segs.append((a, e, top))

    def _pw(segs: list[tuple[float, float, int]], f) -> str:
        return _pw_expr([end for _a, end, _top in segs[:-1]], [f(top) for _a, _e, top in segs])

    chains = []
    for k, (h, segs) in enumerate(sorted(groups.items())):
        en = ""
        if not (len(segs) == 1 and segs[0][0] <= 0 and segs[0][1] == float("inf")):
            en = ":enable='" + _sum_expr([f"between(t,{a:.2f},{min(e, 1e9):.2f})" for a, e, _ in segs]) + "'"
        chains.append(_stretch_chain(f"s{k}", bx, bw, h, _pw(segs, lambda y: max(0, y - G - S) // 2 * 2),
                                     _pw(segs, lambda y: y), en))
    return ",".join(chains)


def _hardsub_track_filter(boxes: list[tuple[int, int, int, int, list, float]], W: int, H: int) -> str:
    """Che đúng ô hardsub theo thời gian (không lớp đen).

    HARDSUB_STYLE=stretch (mặc định): kéo giãn hàng ảnh trên/dưới ô (xem _stretch_chain).
    HARDSUB_STYLE=blur: crop rộng hơn ô một lề → boxblur → cắt lại đúng ô → overlay.
    """
    try:
        margin = int(float(cfg("HARDSUB_BLUR_MARGIN", "24") or "24"))
    except ValueError:
        margin = 24
    # FULL ngang cho mọi style (stretch/blur) — tránh OCR hẹp để chữ Trung lộ 2 mép
    if _hardsub_cover_fullw():
        boxes = [(0, by, W, bh, rs, tot) for _bx, by, _bw, bh, rs, tot in boxes]
    if (cfg("HARDSUB_STYLE", "stretch") or "stretch").strip().lower() != "blur":
        return _stretch_track_filter(boxes, W, H)
    if len(boxes) > 1:
        return _hardsub_track_filter_dyn(boxes, W, H, margin)
    chains = []
    for i, (bx, by, bw, bh, rs, _tot) in enumerate(boxes):
        ex = max(0, bx - margin) // 2 * 2
        ey = max(0, by - margin) // 2 * 2
        ew = min(W, bx + bw + margin) // 2 * 2 - ex
        eh = min(H, by + bh + margin) // 2 * 2 - ey
        br = _boxblur_r(max(10, int(bh * 0.45)), ew, eh)
        chroma = _boxblur_r(max(4, br // 2), ew, eh, sub=1)
        en = ""
        if rs:
            expr = _sum_expr([f"between(t,{a:.2f},{e:.2f})" for a, e in rs])
            en = f":enable='{expr}'"
        chains.append(
            f"split=2[hb{i}][hs{i}];[hs{i}]{_safe_crop(ew, eh, ex, ey)},"
            f"boxblur={br}:2:{chroma}:2{en},crop={bw}:{bh}:{bx - ex}:{by - ey}[hbl{i}];"
            f"[hb{i}][hbl{i}]overlay={bx}:{by}{en}"
        )
    return ",".join(chains)


def _srt_to_ass_margins(srt: Path, out: Path, W: int, H: int, mv_at) -> int:
    """SRT → ASS với MarginV riêng từng câu (mv_at(t_giữa_câu) → px). Trả số câu.

    libass bỏ qua {\\pos} trong SRT nên phải qua ASS; font/cỡ/viền vẫn lấy từ force_style.
    """
    import re
    txt = srt.read_text(encoding="utf-8-sig")
    tm = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")

    def _sec(h, m, s, ms):
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000.0

    def _ass_t(x: float) -> str:
        cs = int(round(x * 100))
        return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"

    events = []
    for block in re.split(r"\r?\n\s*\r?\n", txt.strip()):
        lines = block.splitlines()
        for k, ln in enumerate(lines):
            mt = tm.search(ln)
            if mt:
                a, b = _sec(*mt.groups()[:4]), _sec(*mt.groups()[4:])
                body = "\\N".join(x.strip() for x in lines[k + 1:] if x.strip())
                body = body.replace("{", "(").replace("}", ")")
                if body:
                    events.append(f"Dialogue: 0,{_ass_t(a)},{_ass_t(b)},Default,,0,0,"
                                  f"{int(mv_at((a + b) / 2))},,{body}")
                break
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,"
        "1,2,1,2,40,40,28,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    out.write_text(head + "\n".join(events) + "\n", encoding="utf-8")
    return len(events)


def _hardsub_track_filter_dyn(boxes: list[tuple[int, int, int, int, list, float]], W: int, H: int,
                              margin: int) -> str:
    """Nhiều dải → 1 chuỗi blur chính + 1 chuỗi phụ (chỉ bật lúc 2 dải chồng nhau).

    crop x/y và overlay y tính lại mỗi khung theo t; mỗi chuỗi blur dù tắt vẫn tốn
    ~1/3 chi phí nên không làm 1 chuỗi/dải.
    """
    bx = min(b[0] for b in boxes)
    bw = max(b[0] + b[2] for b in boxes) - bx
    bh = max(b[3] for b in boxes)
    ex = max(0, bx - margin) // 2 * 2
    ew = min(W, bx + bw + margin) // 2 * 2 - ex
    eh = min(H, bh + 2 * margin) // 2 * 2
    bys, eys = [], []
    for b in boxes:
        by = max(0, min(b[1], H - bh)) // 2 * 2
        bys.append(by)
        eys.append(max(0, min(by - margin, H - eh)) // 2 * 2)

    full = [not b[4] for b in boxes]
    cuts = sorted({0.0} | {x for b in boxes for r in b[4] for x in r})
    cuts.append(float("inf"))
    prim: list[tuple[float, int]] = []          # (hết lúc, dải)
    sec: list[tuple[float, float, int]] = []    # (từ, đến, dải)
    last = 0
    for a, e in zip(cuts, cuts[1:]):
        mid = a + 1e-3
        act = [i for i, b in enumerate(boxes) if full[i] or any(r0 <= mid < r1 for r0, r1 in b[4])]
        p = act[0] if act else last
        last = p
        if prim and prim[-1][1] == p:
            prim[-1] = (e, p)
        else:
            prim.append((e, p))
        if len(act) >= 2:
            if sec and sec[-1][2] == act[1] and abs(sec[-1][1] - a) < 1e-6:
                sec[-1] = (sec[-1][0], e, act[1])
            else:
                sec.append((a, e, act[1]))

    def _pw(seg: list[tuple[float, int]], vals: list[int]) -> str:
        return _pw_expr([end for end, _i in seg[:-1]], [vals[i] for _e, i in seg])

    br = _boxblur_r(max(10, int(bh * 0.45)), ew, eh)
    chroma = _boxblur_r(max(4, br // 2), ew, eh, sub=1)
    offs = [by - ey for by, ey in zip(bys, eys)]

    def _chain(tag: str, seg: list[tuple[float, int]], en: str) -> str:
        return (
            f"split=2[hb{tag}][hs{tag}];"
            f"[hs{tag}]crop=w={ew}:h={eh}:x={ex}:y='{_pw(seg, eys)}',"
            f"boxblur={br}:2:{chroma}:2{en},"
            f"crop=w={bw}:h={bh}:x={bx - ex}:y='{_pw(seg, offs)}'[hbl{tag}];"
            f"[hb{tag}][hbl{tag}]overlay=x={bx}:y='{_pw(seg, bys)}'{en}"
        )

    out = _chain("p", prim, "")
    if sec:
        seg = [(e, i) for _a, e, i in sec]
        expr = _sum_expr([f"between(t,{a:.2f},{min(e, 1e9):.2f})" for a, e, _i in sec])
        out += "," + _chain("s", seg, f":enable='{expr}'")
    return out


def resolve_canvas(src_w: int, src_h: int, resolution: str = "source") -> tuple[int, int, str]:
    """Chọn khung xuất: source = giữ gốc; 1080/1k = Full HD (giữ tỉ lệ, tối đa 1920×1080)."""
    key = (resolution or "source").strip().lower()
    if key in ("1080", "1080p", "1k", "fhd", "fullhd"):
        th = 1080
        tw = _even(src_w * th / float(src_h))
        if tw > 1920:
            tw = 1920
            th = _even(src_h * tw / float(src_w))
        return tw, th, "1080p"
    return _even(src_w), _even(src_h), "source"


def _parse_flip(flip: str | None) -> tuple[bool, bool]:
    f = (flip or "").strip().lower()
    if f in ("h", "hflip", "horizontal", "ngang"):
        return True, False
    if f in ("v", "vflip", "vertical", "doc", "dọc"):
        return False, True
    if f in ("hv", "vh", "both", "both", "ca", "cả"):
        return True, True
    return False, False


def build_video_filter(W: int, H: int, zoom: float, ocr: list[dict],
                       srt: Path | None, hardsub_blur: bool,
                       band: tuple | None = None,
                       src_w: int | None = None, src_h: int | None = None,
                       flip: str | None = None, censor_expr: str | None = None,
                       wm_fracs: list | None = None,
                       motion: bool = True, lut: bool = True,
                       grain: str = "weak", fps: float = 25.0,
                       work_dir: Path | None = None,
                       brand: bool | None = None,
                       brand_text: str | None = None,
                       brand_mode: str | None = None,
                       brand_logo: str | None = None,
                       frame: str | None = None,
                       hs_track: dict | None = None,
                       ) -> tuple[str, dict]:
    """Trả (filter_comma_chain_without_final_vout, meta).

    Thứ tự: scale → flip → hardsub blur (toạ độ nguồn) → zoom/crop
    → watermark/delogo (map DETECT→sau zoom) → OCR/sub Việt.
    Hardsub che TRƯỚC zoom để không lệch khi crop giữa / Ken Burns.
    """
    hflip, vflip = _parse_flip(flip)
    zoom = clamp_zoom(zoom)
    grain = normalize_grain(grain)
    parts = []
    meta: dict = {"zoom": zoom, "motion": bool(motion), "lut": "off", "grain": "off",
                  "brand": "off", "brand_mode": "off", "brand_logo": "off", "frame": "off"}
    hs_box: tuple[int, int] | None = None
    z_eff = float(zoom) * (1.02 if motion and zoom > 1.01 else 1.0)
    drift_pad = (0.018 if motion and zoom > 1.01 else 0.008)

    # Luôn scale về canvas cố định TRƯỚC flip/hardsub.
    # Source merge HEVC đôi khi đổi width/height giữa chừng → crop hardsub
    # (vd 1482×92) vỡ: "Invalid too big ... Error reinitializing filters".
    parts.append(f"scale={W}:{H}:flags=lanczos,setsar=1")

    if hflip:
        parts.append("hflip")
    if vflip:
        parts.append("vflip")

    # Hardsub blur TRƯỚC zoom.
    # CODE CỨNG đáy: luôn khung rộng căn giữa — KHÔNG tin x0/x1 OCR
    # (detect lệch → bar hẹp → chữ Trung + nền xanh lòi 2 mép, vd a948f052b724).
    hs_mode = (cfg("HARDSUB_MODE", "track") or "track").strip().lower()
    track_boxes = (_hardsub_track_boxes(hs_track, W, H, hflip, vflip)
                   if hardsub_blur and hs_track and hs_mode != "bar" else [])
    force_blur = (cfg("HARDSUB_FORCE_BLUR", "0") or "0").strip().lower() in ("1", "true", "yes")

    if track_boxes:
        parts.append(_hardsub_track_filter(track_boxes, W, H))
        # Phụ đề Việt bám dải xuất hiện lâu nhất (toạ độ sau zoom)
        bx, by, bw, bh, _rs, _tot = max(track_boxes, key=lambda b: b[5])
        mapped = _map_box(bx / W, by / H, (bx + bw) / W, (by + bh) / H, z_eff, pad=0.0)
        if mapped:
            oby = max(0, int(H * mapped[1]) // 2 * 2)
            hs_box = (oby, min(max(2, int(H * (mapped[3] - mapped[1])) // 2 * 2), H - oby))
        else:
            hs_box = (by, bh)
        meta["hardsub"] = "track:" + "|".join(
            f"{b[2]}x{b[3]}@({b[0]},{b[1]})" + (f"×{len(b[4])}" if b[4] else "") for b in track_boxes)
        print(f"[hardsub] blur theo toạ độ {len(track_boxes)} dải: {meta['hardsub'][6:]}", flush=True)
    elif hardsub_blur and (band or force_blur):
        # Dải cố định: mặc định phủ FULL ngang (OCR/band hẹp → chữ Trung lòi mép)
        try:
            w_frac = float(cfg("HARDSUB_BLUR_W", "1.0") or "1.0")
        except ValueError:
            w_frac = 1.0
        if _hardsub_cover_fullw():
            w_frac = 1.0
        w_frac = max(0.5, min(1.0, w_frac))
        h_frac = 0.13

        if band and len(band) >= 2:
            y0 = max(0.0, min(float(band[0]), float(band[1])))
            y1 = min(1.0, max(float(band[0]), float(band[1])))
            y0 = max(0.0, y0 - 0.022)
            y1 = min(1.0, y1 + 0.012)
            max_h = float(cfg("HARDSUB_BLUR_MAX_H", "0.16") or "0.16")
            if (y1 - y0) > max_h:
                y0 = max(0.0, y1 - max_h)
            x0, x1 = 0.5 - w_frac * 0.5, 0.5 + w_frac * 0.5
        else:
            # force_blur khi không dò thấy band
            y1 = 0.985
            y0 = y1 - h_frac
            x0, x1 = 0.5 - w_frac * 0.5, 0.5 + w_frac * 0.5

        x0, y0, x1, y1 = _flip_box(x0, y0, x1, y1, hflip, vflip)
        if _hardsub_cover_fullw():
            x0, x1 = 0.0, 1.0
        bw = max(2, int(W * (x1 - x0)) // 2 * 2)
        bx = max(0, (W - bw) // 2) // 2 * 2
        bw = min(bw, W - bx)
        right = W - (bx + bw)
        if abs(bx - right) >= 2:
            bx = max(0, (W - bw) // 2) // 2 * 2
            bw = min(bw, W - bx)
        by = max(0, int(H * y0) // 2 * 2)
        bh = max(2, int(H * (y1 - y0)) // 2 * 2)
        bh = min(bh, H - by)
        mapped = _map_box(x0, y0, x1, y1, z_eff, pad=0.0)
        if mapped:
            _, my0, _, my1 = mapped
            oby = max(0, int(H * my0) // 2 * 2)
            obh = max(2, int(H * (my1 - my0)) // 2 * 2)
            hs_box = (oby, min(obh, H - oby))
        else:
            hs_box = (by, bh)
        bar = hs_mode == "bar"
        parts.append(_hardsub_bar_filter(bw, bh, bx, by) if bar else
                     _hardsub_track_filter([(bx, by, bw, bh, [], 0.0)], W, H))
        meta["hardsub"] = f"{'bar' if bar else 'blur'}:{bw}x{bh}@({bx},{by})"
        print(
            f"[hardsub] {'bar đen' if bar else 'blur'} dải phát hiện {bw}x{bh}@({bx},{by}) "
            f"y={y0:.3f}-{y1:.3f} w={w_frac:.2f}",
            flush=True,
        )
    else:
        if not hardsub_blur:
            meta["hardsub"] = "off"
            print("[hardsub] tắt theo hardsub_blur=false — không blur/stretch", flush=True)
        else:
            meta["hardsub"] = "none"
            print("[hardsub] Không phát hiện hardsub trong video gốc → KHÔNG blur (giữ nguyên khung hình gốc)", flush=True)

    if zoom > 1.01:
        if motion:
            mp = build_motion_zoompan(W, H, zoom, fps)
            if mp:
                parts.append(mp)
        else:
            sz = build_static_zoom(W, H, zoom)
            if sz:
                parts.append(sz)
    else:
        print("[render] Zoom: OFF (giữ nguyên khung hình 100%, không zoom/crop)", flush=True)

    segs = list(wm_fracs or [])
    if not segs:
        segs = _default_wm_corners()
        if segs:
            print(f"[watermark] OCR trống → force corners {[s['q'] for s in segs]}", flush=True)
    # Có OCR rồi → KHÔNG ép thêm góc mặc định (tránh che góc trống)

    wm_boxes = []
    for seg in segs:
        t0 = t1 = None
        q = ""
        if isinstance(seg, dict):
            fx = float(seg.get("x", 0)); fy = float(seg.get("y", 0))
            fw = float(seg.get("w", 0)); fh = float(seg.get("h", 0))
            q = str(seg.get("q") or "")
            if seg.get("t0") is not None:
                t0 = float(seg["t0"])
            if seg.get("t1") is not None:
                t1 = float(seg["t1"])
        elif isinstance(seg, (list, tuple)) and len(seg) >= 6:
            t0, t1 = float(seg[0]), float(seg[1])
            fx, fy, fw, fh = (float(seg[2]), float(seg[3]), float(seg[4]), float(seg[5]))
        elif isinstance(seg, (list, tuple)) and len(seg) >= 4:
            fx, fy, fw, fh = (float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3]))
        else:
            continue
        if fw < 0.02 or fh < 0.02:
            continue
        x0, y0, x1, y1 = fx, fy, fx + fw, fy + fh
        x0, x1 = max(0.0, min(x0, x1)), min(1.0, max(x0, x1))
        y0, y1 = max(0.0, min(y0, y1)), min(1.0, max(y0, y1))
        # Cache đã expand lúc detect — chỉ cap, không expand lần 2
        x0, y0, x1, y1 = _cap_wm_frac(x0, y0, x1, y1, q)
        x0, y0, x1, y1 = _flip_box(x0, y0, x1, y1, hflip, vflip)
        mapped = _map_box(x0, y0, x1, y1, z_eff, pad=drift_pad + 0.006)
        if not mapped:
            continue
        x0, y0, x1, y1 = mapped
        # Cap lại sau zoom (pad/map có thể phình)
        mq = q
        if hflip:
            mq = {"tl": "tr", "tr": "tl", "bl": "br", "br": "bl"}.get(q, q)
        if vflip:
            mq = {"tl": "bl", "bl": "tl", "tr": "br", "br": "tr"}.get(mq, mq)
        x0, y0, x1, y1 = _cap_wm_frac(x0, y0, x1, y1, mq)
        rx = max(1, int(W * x0))
        ry = max(1, int(H * y0))
        rw = min(W - rx - 1, max(2, int(W * (x1 - x0))))
        rh = min(H - ry - 1, max(2, int(H * (y1 - y0))))
        # Pixel cap ~16%×10% + neo lại góc sau khi cắt
        rw = min(rw, max(8, int(W * _WM_MAX_W)))
        rh = min(rh, max(8, int(H * _WM_MAX_H)))
        if mq == "br":
            rx, ry = max(1, W - rw - 1), max(1, H - rh - 1)
        elif mq == "tr":
            rx, ry = max(1, W - rw - 1), 1
        elif mq == "bl":
            rx, ry = 1, max(1, H - rh - 1)
        elif mq == "tl":
            rx, ry = 1, 1
        if rw < 8 or rh < 8:
            continue
        en = ""
        if t0 is not None and t1 is not None and t1 > t0:
            en = f":enable=between(t\\,{t0:.3f}\\,{t1:.3f})"
        key = (rx, ry, rw, rh, en)
        if key not in wm_boxes:
            wm_boxes.append(key)

    for i, (rx, ry, rw, rh, en) in enumerate(wm_boxes):
        parts.append(f"delogo=x={rx}:y={ry}:w={rw}:h={rh}{en}")
        # Blur nhẹ chỉ khi ô nhỏ — tránh đốm đen lớn như trước
        if max(rw, rh) > int(min(W, H) * 0.22):
            continue
        br_w = _boxblur_r(max(4, min(12, max(rh, rw) // 6)), rw, rh)
        if br_w < 2:
            continue
        chroma_w = _boxblur_r(max(2, br_w // 2), rw, rh, sub=1)
        if en:
            en_body = en.split("enable=", 1)[-1].replace("\\,", ",")
            ov_en = f":enable='{en_body}'"
        else:
            ov_en = ""
        parts.append(
            f"split=2[wb{i}][ws{i}];[ws{i}]{_safe_crop(rw, rh, rx, ry)},"
            f"boxblur={br_w}:1:{chroma_w}:1[wbl{i}];"
            f"[wb{i}][wbl{i}]overlay={rx}:{ry}{ov_en}"
        )

    fscale = max(1.0, H / 720.0)
    ocr_fs = max(18, int(26 * fscale))
    ocr_bw, ocr_bh = max(160, int(220 * fscale)), max(32, int(42 * fscale))
    for e in ocr:
        if e.get("kind") != "static" or not e.get("vi"):
            continue
        fx, fy = e["box_pct"][0] / 100, e["box_pct"][1] / 100
        if z_eff > 1.01:
            fx, fy = _map_pt(fx, fy, z_eff)
        if hflip:
            fx = 1.0 - fx
        if vflip:
            fy = 1.0 - fy
        px, py = int(fx * W), int(fy * H)
        s, en = e["start"], e["end"]
        bw, bh = ocr_bw, ocr_bh
        bx, by = max(0, px - bw // 2), max(0, py - bh // 2)
        parts.append(f"drawbox=x={bx}:y={by}:w={bw}:h={bh}:color=black@0.72:t=fill:"
                     f"enable='between(t,{s},{en})'")
        parts.append(f"drawtext=fontfile='{FONT}':text='{_esc(e['vi'])}':fontcolor=white:"
                     f"fontsize={ocr_fs}:x={bx}+({bw}-text_w)/2:y={by}+({bh}-text_h)/2:"
                     f"enable='between(t,{s},{en})'")
    if srt and srt.exists():
        # cwd=work_dir khi encode → dùng basename (tránh work/job/work/job/...)
        srt_p = Path(srt)
        if work_dir and srt_p.resolve().parent == Path(work_dir).resolve():
            sp = srt_p.name
        else:
            sp = str(srt_p.resolve()).replace("\\", "/").replace(":", "\\:")
        base_fs = int(float(cfg("SUB_FONT_SIZE", "36") or "36"))
        sub_fs = max(12, int(base_fs * fscale))
        font = (cfg("SUB_FONT", "Arial") or "Arial").replace("'", "")
        bold = 0 if (cfg("SUB_BOLD", "0") or "0") in ("0", "false", "no") else 1
        outline = int(float(cfg("SUB_OUTLINE", "2") or "2"))
        shadow = int(float(cfg("SUB_SHADOW", "1") or "1"))
        align = int(float(cfg("SUB_ALIGN", "2") or "2"))
        ml = int(float(cfg("SUB_MARGIN_L", "40") or "40"))
        mr = int(float(cfg("SUB_MARGIN_R", "40") or "40"))
        pri = _ass_colour(cfg("SUB_COLOR", "#FFFFFF") or "#FFFFFF")
        outc = _ass_colour(cfg("SUB_OUTLINE_COLOR", "#000000") or "#000000", "&H00000000")
        follow = (cfg("SUB_FOLLOW_HARDSUB", "1") or "1") not in ("0", "false", "no")
        if follow and hs_box:
            by, bh = hs_box
            mv = max(4, H - (by + bh // 2) - sub_fs // 2)
        else:
            mv = int(float(cfg("SUB_MARGIN_V", str(max(20, int(28 * fscale)))) or "28"))
        if follow and len(track_boxes) > 1 and work_dir:
            # Hardsub đổi vị trí theo tập → mỗi câu Việt bám dải đang hiện (toạ độ sau zoom)
            def _mv_box(b: tuple) -> int:
                mb = _map_box(b[0] / W, b[1] / H, (b[0] + b[2]) / W, (b[1] + b[3]) / H, z_eff, pad=0.0)
                if not mb:
                    return mv
                oby = int(H * mb[1])
                obh = int(H * (mb[3] - mb[1]))
                return max(4, H - (oby + obh // 2) - sub_fs // 2)

            mvs = [_mv_box(b) for b in track_boxes]

            def _mv_at(t: float) -> int:
                for i, b in enumerate(track_boxes):
                    if not b[4] or any(a <= t < e for a, e in b[4]):
                        return mvs[i]
                return mv

            ass = Path(work_dir) / (srt_p.stem + ".follow.ass")
            n_ev = _srt_to_ass_margins(srt_p, ass, W, H, _mv_at)
            if n_ev:
                sp = ass.name
                print(f"[render] sub Việt bám {len(mvs)} dải hardsub: {n_ev} câu, MarginV {sorted(set(mvs))}",
                      flush=True)
        parts.append(
            # WrapStyle=2: KHÔNG soft-wrap — chỉ xuống dòng khi có \N.
            # (WrapStyle=1 vẫn bẻ dòng khi text rộng hơn khung — gây 2 dòng dù CUT.)
            f"subtitles='{sp}':force_style='FontName={font},FontSize={sub_fs},"
            f"PlayResX={W},PlayResY={H},WrapStyle=2,"
            f"PrimaryColour={pri},OutlineColour={outc},BackColour=&H00000000,"
            f"BorderStyle=1,Outline={outline},Shadow={shadow},Alignment={align},"
            f"MarginV={mv},MarginL={ml},MarginR={mr},Bold={bold}'"
        )
    if censor_expr:
        sig = int(float(cfg("CENSOR_BLUR", "24") or "24"))
        parts.append(
            f"split=2[ok][cv];[cv]boxblur={sig}:2:{max(2, sig // 2)}:2[cb];"
            f"[ok][cb]overlay=enable='{censor_expr}'"
        )
        print(f"[render] censor blur enable={censor_expr}", flush=True)
    meta["lut"] = append_lut_filters(parts, lut, work_dir=work_dir)
    meta["grain"] = append_inline_noise_grain(parts, grain)

    body = ",".join(parts)
    job_key = work_dir.name if work_dir else None
    body, bmeta = apply_brand_overlay(
        body, W=W, H=H, fontfile=FONT, work_dir=work_dir,
        mode=brand_mode, enabled=brand, text=brand_text, logo=brand_logo,
        job_key=job_key,
    )
    meta.update(bmeta)

    # Khung PNG full-frame (sau brand)
    fr = pick_frame(frame, job_key=job_key)
    if fr and work_dir is not None:
        fr_name = prepare_frame_file(fr, work_dir)
        body = (
            f"{body}[vbase];movie={fr_name},format=rgba,scale={W}:{H}[fr];"
            f"[vbase][fr]overlay=0:0"
        )
        meta["frame"] = fr.name
        print(f"[render] frame overlay={fr.name}", flush=True)
    body = f"{body},format=yuv420p"
    return body, meta



def _pick_dub(work_dir: Path) -> Path | None:
    m = Manifest(work_dir)
    dub = m.data.get("dub")
    if dub and (work_dir / dub["file"]).exists():
        return work_dir / dub["file"]
    for name in ("dub_fit.wav", "dub_tts.wav"):
        if (work_dir / name).exists():
            return work_dir / name
    return None


def _normalize_audio_mode(audio_mode: str | None, have_nv: bool = True) -> str:
    mode = (audio_mode or "voice_only").strip().lower()
    if mode in ("library_bgm", "bgm", "safe_bgm"):
        mode = "voice_bgm"
    if mode in ("duck+bgm", "ducking_bgm", "sfx_bgm", "voice_bgm_duck"):
        mode = "duck_bgm"
    if mode in ("sfx_library", "sfx_yt", "yt_lib"):
        mode = "sfx_lib"
    if mode in ("demucs_keep", "demucs_clean") and not have_nv:
        mode = "duck"
    return mode


def _uses_sidechain(mode: str) -> bool:
    """duck / duck_bgm / sfx_lib dùng sidechaincompress — KHÔNG chạy cùng encode video
    (ffmpeg 7.1.1: audio EOF ~1853s trong khi video đủ dài)."""
    return mode in ("duck", "duck_bgm", "sfx_lib")


def _stage_note(m: Manifest):
    """Callback ghi dòng log tiến trình con vào cur_text của stage render (thưa ≥2 s)."""
    last = [0.0]

    def note(line: str) -> None:
        now = time.time()
        if not line.startswith("[sfx]") or now - last[0] < 2.0:
            return
        last[0] = now
        try:
            st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
            st.update({"phase": "sfx_lib", "cur_text": line[6:].strip(), "updated_at": now})
            js.persist(m)
        except Exception:  # noqa: BLE001
            pass
    return note


def _ensure_mixed_audio(
    work_dir: Path, video: Path, dub: Path | None, no_vocals: Path, vocals: Path,
    audio_mode: str, voice_vol: float, orig_vol: float, total: float,
    m: Manifest, force: bool = False,
) -> tuple[Path, str]:
    """Pass 1: mixed_audio.wav (audio-only + sidechain). Trả (path, used_mode)."""
    mixed = work_dir / "mixed_audio.wav"
    sig_path = work_dir / "mixed_audio.json"
    probe: list[str] = ["ffmpeg", "-y", "-i", _ff_input(video, work_dir)]
    af_probe, used = _build_audio_filter(
        probe, work_dir, dub, no_vocals, vocals, audio_mode, voice_vol, orig_vol)
    sig = _mix_signature(probe, af_probe, total, work_dir)
    need = (not mixed.exists()) or mixed.stat().st_size < 100_000
    old_sig = None
    if not need:
        try:
            old_sig = json.loads(sig_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old_sig = None
        if old_sig is not None:
            if old_sig != sig:
                print(f"[render] thiết lập trộn / input đổi → trộn lại {mixed.name}", flush=True)
                need = True
        elif dub and dub.exists():
            # bản trộn cũ chưa có mixed_audio.json: luật mtime như trước
            try:
                if mixed.stat().st_mtime < dub.stat().st_mtime:
                    need = True
            except OSError:
                need = True
    if force and need is False:
        print(f"[render] force mux nhưng reuse {mixed.name} (cùng thiết lập trộn)", flush=True)
    if not need:
        if old_sig is None:
            _write_mix_signature(sig_path, sig)
        print(f"[render] reuse {mixed.name} ({mixed.stat().st_size/1e6:.0f} MB)", flush=True)
        return mixed, f"{used}+twopass"

    print(f"[render] pass1 audio-mix → {mixed.name} (sidechain tách khỏi video)", flush=True)
    try:
        st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
        st.update({"phase": "audio_mix", "progress": 0.5, "cur_text": "trộn audio…",
                   "updated_at": time.time()})
        js.persist(m)
    except Exception:  # noqa: BLE001
        pass

    cmd: list[str] = ["ffmpeg", "-y", "-i", _ff_input(video, work_dir)]
    af, used = _build_audio_filter(
        cmd, work_dir, dub, no_vocals, vocals, audio_mode, voice_vol, orig_vol)
    if not af:
        raise RuntimeError("two-pass: không dựng được audio filter")
    _attach_filter_complex(cmd, af, work_dir)
    cmd += ["-map", "[aout]", "-vn"]
    if total and total > 0:
        cmd += ["-t", f"{total:.3f}"]
    tmp = work_dir / "mixed_audio.partial.wav"
    cmd += ["-c:a", "pcm_s24le", "-ar", "48000", "-ac", "2",
            "-loglevel", "error", tmp.name]
    rc, err = _ffmpeg_progress(cmd, total, m, cwd=work_dir, hi=15.0)
    if rc != 0:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"pass1 audio-mix lỗi: {err}")
    import os as _os
    _os.replace(tmp, mixed)
    _write_mix_signature(sig_path, sig)
    print(f"[render] pass1 xong {mixed.name} ({mixed.stat().st_size/1e6:.0f} MB)", flush=True)
    return mixed, f"{used}+twopass"


def _mix_signature(cmd: list[str], af: str | None, total: float, work_dir: Path) -> dict:
    """Filter + (đường dẫn, size, mtime) mọi input -i + độ dài — đổi thì phải trộn lại."""
    inputs = []
    for i, c in enumerate(cmd[:-1]):
        if c != "-i":
            continue
        p = Path(cmd[i + 1])
        if not p.is_absolute():
            p = Path(work_dir) / p
        try:
            s = p.stat()
            inputs.append([cmd[i + 1], s.st_size, int(s.st_mtime)])
        except OSError:
            inputs.append([cmd[i + 1], None, None])
    return {"v": 1, "af": af or "", "inputs": inputs, "total": round(float(total or 0), 1)}


def _write_mix_signature(path: Path, sig: dict) -> None:
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(sig, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        print(f"[render] không ghi được {path.name}: {e}", flush=True)


def _build_audio_filter(cmd: list, work_dir: Path, dub: Path | None, no_vocals: Path,
                        vocals: Path, audio_mode: str, voice_vol: float,
                        orig_vol: float) -> tuple[str | None, str]:
    """Dựng filter audio + THÊM input vào cmd. Trả (filter, tên_chế_độ_thực).

    Chế độ (khi có giọng Việt = dub):
      • voice_only  : CHỈ giọng Việt — bỏ nhạc nền + tiếng gốc.
      • voice_bgm   : giọng Việt + BGM thư viện assets/bgm/ (không dùng BGM gốc).
      • duck_bgm    : BGM thư viện + duck tiếng gốc thấp (giữ SFX nhẹ, át BGM gốc).
      • demucs_keep : nhạc nền gốc + GIỌNG TRUNG NHỎ + giọng Việt (rủi ro BGM).
      • demucs_clean: nhạc nền gốc + giọng Việt.
      • duck        : hạ tiếng gốc + SIDECHAIN khi có giọng Việt (rủi ro BGM gốc).
      • sfx_lib     : tiếng động gốc (CDX23) + nhạc kho assets/bgm_yt theo mức + giọng Việt
                      (bỏ hẳn nhạc gốc; cần sfx_lib.prepare chạy trước — run_render tự gọi).
    """
    have_nv, have_voc = no_vocals.exists(), vocals.exists()
    mode = _normalize_audio_mode(audio_mode, have_nv)

    def _ai(p: Path | str) -> str:
        return _ff_input(p, work_dir)

    if not dub:
        if mode in ("voice_only", "voice_bgm", "duck_bgm", "sfx_lib"):
            raise RuntimeError(
                "Chế độ chỉ giọng / voice+BGM thư viện cần file dub — hãy chạy TTS trước.")
        if have_nv:
            cmd += ["-i", _ai(no_vocals)]
            return "[1:a]anull[aout]", "nền(Demucs)"
        return None, "gốc"

    dv = dub_volume()
    st = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    # Lọc rumble đáy (<75Hz) + dịu âm xuýt (s/x/ch ~6.5kHz) + nén nhẹ ổn định âm lượng trong câu + chặn đỉnh 0.92
    vo_eq = "highpass=f=75,treble=g=-1.8:f=6500,acompressor=threshold=0.14:ratio=2.2:attack=12:release=160:makeup=1.12"
    lim = "alimiter=limit=0.92:attack=5:release=60:level=0"

    if mode == "voice_only":
        cmd += ["-i", _ai(dub)]
        af = (f"[1:a]{vo_eq},volume={dv},{st},"
              f"apad=pad_dur=7200,{lim}[aout]")
        return af, f"voice_only(chỉ giọng Việt@{dv})"

    if mode == "voice_bgm":
        bgm = pick_bgm(job_key=work_dir.name)
        if not bgm:
            print("[render] voice_bgm nhưng chưa có assets/bgm → voice_only", flush=True)
            cmd += ["-i", _ai(dub)]
            af = (f"[1:a]{vo_eq},volume={dv},{st},"
                  f"apad=pad_dur=7200,{lim}[aout]")
            return af, f"voice_only(thiếu BGM thư viện@{dv})"
        vol = bgm_volume()
        # BGM loop bằng -stream_loop -1 (không aloop size khổng lồ).
        # Không -shortest; chốt bằng -t video. amix duration=first theo dub đã apad
        # (dub thường ngắn hơn video → không apad thì nhạc + audio dừng ở câu thoại cuối).
        cmd += ["-stream_loop", "-1", "-i", _ai(bgm), "-i", _ai(dub)]
        af = (
            f"[1:a]volume={vol},{st}[bg];"
            f"[2:a]{vo_eq},volume={dv},{st},apad=pad_dur=7200[vo];"
            f"[vo][bg]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,{lim}[aout]"
        )
        return af, f"voice_bgm({bgm.name}@bgm{vol}/dub{dv})"

    if mode == "duck_bgm":
        # BGM thư viện thay nhạc gốc; tiếng nền duck thấp (SFX/ambience).
        # Nếu đã có no_vocals.wav (Demucs) thì ưu tiên dùng thay vì [0:a] để không lọt tiếng Trung gây rè.
        bgm = pick_bgm(job_key=work_dir.name)
        if not bgm:
            print("[render] duck_bgm thiếu assets/bgm → duck thuần", flush=True)
            mode = "duck"
        else:
            vol = bgm_volume()
            ov = float(orig_vol)
            if ov > 0.28:
                ov = 0.18
            cmd += ["-stream_loop", "-1", "-i", _ai(bgm), "-i", _ai(dub)]
            if have_nv:
                cmd += ["-i", _ai(no_vocals)]
                og_chain = f"[3:a]volume={ov},{st}[og]"
                og_tag = f"nv{ov}"
            else:
                og_chain = f"[0:a]{AUDIO_TS_FIX},volume={ov},{st}[og]"
                og_tag = f"orig{ov}"
            # Phải chạy trong pass1 audio-only (xem _ensure_mixed_audio) — không
            # sidechaincompress cùng encode video.
            af = (
                f"[2:a]{vo_eq},volume={dv},asplit=2[sc0][vo];[sc0]apad[sc];"
                f"{og_chain};"
                "[og][sc]sidechaincompress=threshold=0.02:ratio=14:attack=5:release=220[dk];"
                f"[1:a]volume={vol},{st}[bg];"
                f"[vo]{st}[vx];"
                f"[dk][bg][vx]amix=inputs=3:duration=first:dropout_transition=2:normalize=0,{lim}[aout]"
            )
            return af, f"duck_bgm({bgm.name}@bgm{vol}/{og_tag}/dub{dv})"

    if mode == "sfx_lib":
        fx, bg = work_dir / _sfx_lib.EFFECT, work_dir / _sfx_lib.BGM
        if not fx.exists() or not bg.exists():
            raise RuntimeError(f"sfx_lib: thiếu {fx.name}/{bg.name} — chưa chạy sfx_lib.prepare.")
        fv, mv = _sfx_lib.effect_volume(), _sfx_lib.music_volume()
        sv = round(dv * _sfx_lib.voice_boost(), 3)
        import soundfile as _sf
        # sidechaincompress có lúc kết thúc sớm khi sidechain ngắn hơn main → dub phải được
        # đệm đúng bằng độ dài stem tiếng động, trộn duration=longest.
        whole = f"{_sf.info(str(fx)).duration:.3f}"
        cmd += ["-i", _ai(fx), "-i", _ai(bg), "-i", _ai(dub)]   # 1,2,3
        af = (
            f"[3:a]{vo_eq},volume={sv},{st},apad=whole_dur={whole},asplit=3[scA][scB][vo];"
            f"[1:a]volume={fv},{st}[fx];[fx][scA]sidechaincompress=threshold=0.03:ratio=4:attack=5:release=280[fxd];"
            f"[2:a]volume={mv},{st}[bg];[bg][scB]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[bgd];"
            "[fxd][bgd][vo]amix=inputs=3:duration=longest:dropout_transition=2:normalize=0,"
            "alimiter=limit=0.89:attack=5:release=80:level=0[aout]"
        )
        return af, f"sfx_lib(sfx{fv}/nhạc kho{mv}/dub{sv})"

    if mode == "demucs_keep" and have_voc:
        cmd += ["-i", _ai(no_vocals), "-i", _ai(vocals), "-i", _ai(dub)]   # 1,2,3
        af = (f"[1:a]volume=1.0,{st}[bg];[2:a]volume={voice_vol},{st}[cv];[3:a]{vo_eq},volume={dv},{st}[vo];"
              f"[bg][cv][vo]amix=inputs=3:duration=longest:dropout_transition=2:normalize=0,{lim}[aout]")
        return af, f"demucs_keep(giọng Trung {voice_vol}/dub{dv})"

    if mode in ("demucs_keep", "demucs_clean"):   # clean (hoặc keep mà thiếu vocals)
        cmd += ["-i", _ai(no_vocals), "-i", _ai(dub)]    # 1,2
        af = (f"[1:a]volume=1.0,{st}[bg];[2:a]{vo_eq},volume={dv},{st}[vo];"
              f"[bg][vo]amix=inputs=2:duration=longest:dropout_transition=2:normalize=0,{lim}[aout]")
        return af, f"demucs_clean(dub{dv})"

    # duck: sidechain — tiếng nền tự tụt khi giọng Việt nói.
    # Nếu có sẵn no_vocals.wav (Demucs) thì ưu tiên dùng thay vì [0:a] để sạch hẳn tiếng thoại Trung.
    cmd += ["-i", _ai(dub)]                               # 1
    if have_nv:
        cmd += ["-i", _ai(no_vocals)]                     # 2
        og_chain = f"[2:a]volume={orig_vol},{st}[og]"
        duck_tag = "no_vocals+sidechain"
    else:
        og_chain = f"[0:a]{AUDIO_TS_FIX},volume={orig_vol},{st}[og]"
        duck_tag = "sidechain"
    # sidechaincompress dừng ở input ngắn hơn -> đệm sidechain để tiếng gốc chạy hết video
    af = (f"[1:a]{vo_eq},volume={dv},{st},asplit=2[sc0][vo];[sc0]apad[sc];{og_chain};"
          "[og][sc]sidechaincompress=threshold=0.02:ratio=12:attack=5:release=280[dk];"
          f"[dk][vo]amix=inputs=2:duration=longest:dropout_transition=2:normalize=0,{lim}[aout]")
    return af, f"duck({duck_tag}/dub{dv})"


def run_render(work_dir: str | Path, zoom: float = 1.12, add_sub: bool = True,
               hardsub_blur: bool = True, out_name: str = "output.mp4",
               force: bool = False, audio_mode: str | None = None,
               voice_vol: float = 0.2, orig_vol: float = 0.6,
               resolution: str = "source", flip: str = "",
               motion: bool = True, lut: bool = True,
               grain: str = "weak",
               brand: bool | None = None, brand_text: str | None = None,
               brand_mode: str | None = None, brand_logo: str | None = None,
               frame: str | None = None, vbitrate: str | None = None,
               speed_factor: float | None = None) -> dict:
    work_dir = Path(work_dir).resolve()
    if audio_mode is None or str(audio_mode).strip() == "":
        audio_mode = default_audio_mode()
    zoom = clamp_zoom(zoom)
    grain = normalize_grain(grain)
    m = Manifest(work_dir)
    if speed_factor is None:
        try:
            cfg_spd = os.getenv("RENDER_SPEED") or os.getenv("BYPASS_SPEED") or str(cfg("RENDER_SPEED", "") or "")
            if cfg_spd.strip():
                speed_factor = float(cfg_spd.strip())
            else:
                speed_factor = float(m.data.get("opts", {}).get("render_speed") or m.data.get("opts", {}).get("speed_factor") or 1.0)
        except Exception:
            speed_factor = 1.0
    speed_factor = max(0.5, min(2.0, float(speed_factor or 1.0)))
    if m.is_done(STAGE) and not force:
        st = m.stage(STAGE)
        if st and Path(st.get("output_path", "")).exists():
            print(f"[skip] Render đã xong: {st['output_path']}")
            return st

    video = _find_video(work_dir)
    if not video:
        raise RuntimeError("Chưa có video.")
    ts_fix = ensure_job_audio_ts(work_dir, video)
    if ts_fix.get("status") == "skip":
        print(f"[audio-ts] WARN nguồn dư {ts_fix.get('drift', 0):+.2f}s tiếng nhưng không dời mốc được: "
              f"{ts_fix.get('why')}", flush=True)
    if ((work_dir / "tts.json").exists() and (work_dir / "dub_fit.wav").exists()
            and not (work_dir / _assemble.FIT_STAMP).exists()):
        print("[render] dub_fit ghép bằng code cũ (1.0× cứng) → ghép lại khớp time thích ứng", flush=True)
        _assemble.assemble_fit(work_dir)
        force = True
    src_w, src_h = _size(video)
    W, H, res_label = resolve_canvas(src_w, src_h, resolution)
    fps = _fps(video)

    ocr = []
    ocr_path = work_dir / "ocr.json"
    if ocr_path.exists():
        ocr = json.loads(ocr_path.read_text(encoding="utf-8"))
    srt = None
    if add_sub:
        srt = work_dir / "sub.vi.srt"
        # SRT BURN: khớp giọng trên dub_fit (tts_fit.json). Không dùng mốc script gốc
        # vì assemble_fit đàn hồi có thể trễ hàng chục giây.
        try:
            from engines.assemble import write_fit_timings
            from engines.subs import to_srt_placed, to_srt_fit
            fit_path = work_dir / "tts_fit.json"
            dub_fit = work_dir / "dub_fit.wav"
            # Có dub_fit mà chưa/cũ timing map -> tính lại (không cần ghép audio)
            if dub_fit.exists() and (
                    not fit_path.exists()
                    or fit_path.stat().st_mtime < dub_fit.stat().st_mtime):
                write_fit_timings(work_dir)
            if fit_path.exists():
                placed = json.loads(fit_path.read_text(encoding="utf-8"))
                # CUT display: luôn tạo lại nếu thiếu / script mới hơn / đổi max_vi
                disp_p = work_dir / "script.display.json"
                stamp_p = work_dir / ".display_split_vi"
                from engines.subs import split_bilingual_rows, sub_split_cfg
                mz, mv, md = sub_split_cfg()
                script_p = work_dir / "script.json"
                need_disp = not disp_p.exists()
                if not need_disp and script_p.exists():
                    try:
                        if script_p.stat().st_mtime > disp_p.stat().st_mtime:
                            need_disp = True
                    except OSError:
                        need_disp = True
                if not need_disp:
                    try:
                        if not stamp_p.exists() or stamp_p.read_text(encoding="utf-8").strip() != str(mv):
                            need_disp = True
                    except OSError:
                        need_disp = True
                if need_disp and script_p.exists():
                    try:
                        sc = json.loads(script_p.read_text(encoding="utf-8"))
                        disp = split_bilingual_rows(
                            [dict(r) for r in sc], max_zh=mz, max_vi=mv, max_dur=md,
                        )
                        disp_p.write_text(
                            json.dumps(disp, ensure_ascii=False, indent=2), encoding="utf-8",
                        )
                        stamp_p.write_text(str(mv), encoding="utf-8")
                        print(
                            f"[render] tạo script.display.json CUT ({len(disp)} cue, max_vi={mv})",
                            flush=True,
                        )
                    except Exception as e:  # noqa: BLE001
                        print(f"[render] tạo display lỗi ({e})", flush=True)
                if disp_p.exists():
                    try:
                        from engines.subs import expand_placed_with_display
                        disp = json.loads(disp_p.read_text(encoding="utf-8"))
                        n0 = len(placed)
                        placed = expand_placed_with_display(placed, disp, key="vi")
                        print(
                            f"[render] burn CUT display: {n0} TTS → {len(placed)} cue",
                            flush=True,
                        )
                    except Exception as e:  # noqa: BLE001
                        print(f"[render] expand display lỗi ({e}) — burn script 1:1", flush=True)
                # An toàn: mọi cue VI còn dài hơn max_vi → cắt cứng trước khi ghi SRT
                try:
                    from engines.subs import _split_hard, sub_split_cfg, glue_vi_names
                    _, max_vi, _ = sub_split_cfg()
                    safe: list[dict] = []
                    n_over = 0
                    for p in placed:
                        vi = glue_vi_names((p.get("vi") or "").replace("\n", " ").strip())
                        if vi and len(vi) > max_vi:
                            n_over += 1
                            parts = _split_hard(vi, max_vi)
                            if len(parts) > 1:
                                st = float(p.get("start", 0) or 0)
                                en = float(p.get("end") or 0) or (st + float(p.get("dur") or 0.4))
                                if en <= st:
                                    en = st + 0.4
                                span = en - st
                                t = st
                                for i, part in enumerate(parts):
                                    t1 = en if i == len(parts) - 1 else (
                                        t + span * (len(part) / max(len(vi), 1))
                                    )
                                    if t1 < t + 0.08:
                                        t1 = min(en, t + 0.08)
                                    np = dict(p)
                                    np["vi"] = part
                                    np["start"] = round(t, 3)
                                    np["end"] = round(t1, 3)
                                    np["dur"] = round(max(0.0, t1 - t), 3)
                                    safe.append(np)
                                    t = t1
                                continue
                        np = dict(p)
                        np["vi"] = vi
                        safe.append(np)
                    if len(safe) != len(placed) or n_over:
                        print(
                            f"[render] burn hard-split: {len(placed)} → {len(safe)} cue "
                            f"(max_vi={max_vi}, over={n_over})",
                            flush=True,
                        )
                    placed = safe
                except Exception as e:  # noqa: BLE001
                    print(f"[render] hard-split burn lỗi ({e})", flush=True)
                burn = work_dir / "sub.vi.burn.srt"
                burn_txt = to_srt_placed(placed, key="vi")
                # An toàn: không để soft-wrap 2 dòng lọt vào burn (đàn\nông…)
                parts = burn_txt.split("\n\n")
                flat = []
                for p in parts:
                    ls = p.split("\n")
                    if len(ls) > 3:
                        flat.append("\n".join(ls[:2] + [" ".join(ls[2:])]))
                    else:
                        flat.append(p)
                burn_txt = "\n\n".join(flat)
                burn.write_text(burn_txt, encoding="utf-8")
                srt = burn
                print(f"[render] burn sub theo tts_fit.json ({len(placed)} câu, WrapStyle=2)", flush=True)
            else:
                sp, tp = work_dir / "script.json", work_dir / "tts.json"
                if sp.exists() and tp.exists():
                    from engines.subs import _split_hard, sub_split_cfg, glue_vi_names
                    script = json.loads(sp.read_text(encoding="utf-8"))
                    durs = {c["i"]: c.get("dur", 0) for c in json.loads(tp.read_text(encoding="utf-8"))}
                    _, max_vi, _ = sub_split_cfg()
                    # Cắt cứng VI trước to_srt_fit — tránh burn 1:1 dài
                    cut_script = []
                    for row in script:
                        r = dict(row)
                        vi = glue_vi_names((r.get("vi") or "").replace("\n", " ").strip())
                        if vi and len(vi) > max_vi:
                            parts = _split_hard(vi, max_vi)
                            if len(parts) > 1:
                                # giữ 1 timing, ghép bằng space ngắn — vẫn 1 cue; hard-split timing cần fit
                                # → tách thành nhiều row ảo cùng start/end chia tỉ lệ
                                st = float(r.get("start", 0) or 0)
                                en = float(r.get("end") or 0) or (st + 0.4)
                                if en <= st:
                                    en = st + 0.4
                                span = en - st
                                t = st
                                for i, part in enumerate(parts):
                                    t1 = en if i == len(parts) - 1 else (
                                        t + span * (len(part) / max(len(vi), 1))
                                    )
                                    if t1 < t + 0.08:
                                        t1 = min(en, t + 0.08)
                                    nr = dict(r)
                                    nr["vi"] = part
                                    nr["start"] = round(t, 3)
                                    nr["end"] = round(t1, 3)
                                    cut_script.append(nr)
                                    t = t1
                                continue
                        r["vi"] = vi
                        cut_script.append(r)
                    burn = work_dir / "sub.vi.burn.srt"
                    burn.write_text(to_srt_fit(cut_script, durs, key="vi"), encoding="utf-8")
                    srt = burn
                    print("[render] WARN burn sub theo script (chưa có tts_fit) — có thể lệch giọng",
                          flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[render] không tạo được sub burn ({e}) -> dùng sub.vi.srt")
    no_vocals = work_dir / "no_vocals.wav"    # nhạc/SFX (Demucs)
    vocals = work_dir / "vocals.wav"          # giọng Trung tách riêng (Demucs)
    dub = _pick_dub(work_dir)
    # Có tts.json mà chưa ghép dub -> tự ghép (tránh output chỉ tiếng gốc im lặng).
    if dub is None and (work_dir / "tts.json").exists():
        print("[render] thiếu dub_*.wav nhưng có tts.json -> tự ghép khớp time", flush=True)
        from engines.assemble import assemble_fit
        assemble_fit(work_dir)
        dub = _pick_dub(work_dir)
    if dub is None and (work_dir / "tts.json").exists():
        raise RuntimeError(
            "Có tts.json nhưng không tạo được dub_fit.wav — từ chối render chỉ tiếng gốc.")

    # Kiểm tra cấu hình tắt hardsub_blur từ queue_opts hoặc config/env
    # Không dùng bool("false") — chuỗi "false"/"off" phải tắt blur.
    user_hs_blur = m.data.get("queue_opts", {}).get("hardsub_blur")
    if user_hs_blur is not None:
        if isinstance(user_hs_blur, str):
            v = user_hs_blur.strip().lower()
            if v in ("0", "false", "off", "no", "tắt", "tat", "không", "khong", ""):
                hardsub_blur = False
            elif v in ("1", "true", "on", "yes", "bật", "bat", "có", "co"):
                hardsub_blur = True
            # chuỗi lạ: giữ arg hardsub_blur từ caller
        else:
            hardsub_blur = bool(user_hs_blur)
    elif cfg("HARDSUB_BLUR", "1").strip().lower() in ("0", "false", "no", "off"):
        hardsub_blur = False

    band = None
    wm_fracs: list = []
    if hardsub_blur:
        try:
            redetect = os.getenv("HARDSUB_REDETECT", "").strip() in ("1", "true", "yes")
            band, wm_fracs = detect_overlays(work_dir, force=redetect)
        except Exception as e:  # noqa: BLE001
            print(f"[hardsub] dò lỗi ({e}) -> đáy mặc định, không ép 4 góc watermark")
    hs_track = None
    if hardsub_blur and (cfg("HARDSUB_MODE", "track") or "track").strip().lower() != "bar":
        try:
            hs_track = detect_hardsub_track(work_dir, force=redetect)
        except Exception as e:  # noqa: BLE001
            print(f"[hardsub] dò toạ độ lỗi ({e}) -> blur dải cố định", flush=True)

    out_final = work_dir / out_name
    # Ghi file tạm — tránh UI stream output.mp4 đang bị ffmpeg truncate
    # -> RuntimeError Content-Length (ASGI).
    # PHẢI kết thúc bằng .mp4 (ffmpeg đoán muxer theo đuôi; `.xxx.partial` = lỗi).
    stem = Path(out_name).stem  # "output"
    out_tmp = work_dir / f"{stem}.partial.mp4"

    m.mark_running(STAGE)
    free_gpu_vram("trước render NVENC")
    try:
        vf, fx_meta = build_video_filter(
            W, H, zoom, ocr, srt, hardsub_blur, band,
            src_w=src_w, src_h=src_h, flip=flip,
            censor_expr=_censor_expr(work_dir),
            wm_fracs=wm_fracs, motion=motion, lut=lut, grain=grain, fps=fps,
            work_dir=work_dir, brand=brand, brand_text=brand_text,
            brand_mode=brand_mode, brand_logo=brand_logo, frame=frame, hs_track=hs_track)

        total = _probe_dur(video)
        mode_n = _normalize_audio_mode(audio_mode, no_vocals.exists())
        # Two-pass: sidechaincompress + encode video cùng filter_complex → audio
        # cắt ~1853s (ffmpeg 7.1.1). Trộn audio riêng rồi gắn vào pass video.
        twopass_mixed: Path | None = None
        used_mode = mode_n
        if dub and mode_n == "sfx_lib":
            free_gpu_vram("trước tách tiếng động sfx_lib")
            _sfx_lib.run_subprocess(work_dir, video, total, on_line=_stage_note(m))
        if dub and _uses_sidechain(mode_n):
            twopass_mixed, used_mode = _ensure_mixed_audio(
                work_dir, video, dub, no_vocals, vocals,
                audio_mode, voice_vol, orig_vol, total, m, force=force)

        cmd = ["ffmpeg", "-y"]
        # Decode CUDA + overlay/blur hardsub (rgba/geq cũ) → artifact xanh thuần.
        # Có hardsub: decode CPU, encode vẫn NVENC — nhanh hơn geq desat, hết xanh.
        use_nvenc = _want_nvenc()
        has_actual_hardsub = fx_meta.get("hardsub") and fx_meta["hardsub"] != "none"
        use_hwaccel = use_nvenc and not has_actual_hardsub
        if use_hwaccel:
            cmd += ["-hwaccel", "cuda", "-hwaccel_device", "0"]
        elif use_nvenc and has_actual_hardsub:
            print("[render] Có hardsub thực tế → bỏ -hwaccel decode (tránh bar xanh CUDA)",
                  flush=True)
        cmd += ["-i", _ff_input(video, work_dir)]  # [0] = video
        fixes = squeeze_fixes(video)
        for fx in fixes:
            print(f"[render] sửa mốc nén {fx['a']:.1f}s: {fx['frames']} khung → "
                  f"{1 / fx['step']:.2f} fps trên {fx['span']:.1f}s", flush=True)
        vf = squeeze_setpts(fixes) + vf
        use_speed = abs(speed_factor - 1.0) >= 0.003
        if use_speed:
            pts_scale = 1.0 / speed_factor
            vf = f"{vf},setpts={pts_scale:.6f}*PTS"
            total_render = total / speed_factor
            print(f"[render] ⚡ Tốc độ lách bản quyền: x{speed_factor:.4f} (Video setpts={pts_scale:.6f}*PTS, Audio atempo={speed_factor:.4f}, thời lượng {total:.1f}s -> {total_render:.1f}s)", flush=True)
        else:
            total_render = total

        af: str | None = None
        if twopass_mixed is not None:
            cmd += ["-i", _ff_input(twopass_mixed, work_dir)]  # [1] = mixed_audio
            if use_speed:
                graph = f"[0:v]{vf}[vout];[1:a]atempo={speed_factor:.4f}[aout]"
            else:
                graph = f"[0:v]{vf}[vout]"
        else:
            af, used_mode = _build_audio_filter(
                cmd, work_dir, dub, no_vocals, vocals, audio_mode, voice_vol, orig_vol)
            if use_speed and af:
                if af.endswith("[aout]"):
                    af = af[:-6] + f"[aout_pre];[aout_pre]atempo={speed_factor:.4f}[aout]"
                else:
                    af = af + f";[aout]atempo={speed_factor:.4f}[aout_spd]"
            graph = f"[0:v]{vf}[vout]" + (";" + af if af else "")
        _attach_filter_complex(cmd, graph, work_dir)
        cmd += ["-map", "[vout]"]
        if twopass_mixed is not None:
            if use_speed:
                cmd += ["-map", "[aout]"]
            else:
                cmd += ["-map", "1:a"]
        else:
            if af:
                cmd += ["-map", "[aout]"]
            else:
                af_base = f"{AUDIO_TS_FIX},atempo={speed_factor:.4f}" if use_speed else AUDIO_TS_FIX
                cmd += ["-map", "0:a?", "-af", af_base]
        # bitrate: UI chọn 2M/4M/8M (mặc định 8M); "auto" = theo độ phân giải cũ
        vbit, buf = normalize_vbitrate(vbitrate, W, H)
        if use_nvenc:
            venc = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                    "-b:v", vbit, "-maxrate", vbit, "-bufsize", buf,
                    "-pix_fmt", "yuv420p", "-gpu", "0"]
        else:
            venc = ["-c:v", "libx264", "-preset", "veryfast", "-b:v", vbit,
                    "-maxrate", vbit, "-bufsize", buf]
        # Giữ đúng mốc thời gian gốc (không làm tròn về lưới 1/fps) — khớp audio cả với nguồn VFR.
        venc += ["-enc_time_base", "demux"]
        cmd += venc
        abit = "128k" if vbit == "2M" else "192k"
        # Không dùng -shortest. Chốt bằng -t theo độ dài video.
        if total_render and total_render > 0:
            cmd += ["-t", f"{total_render:.3f}"]
        cmd += ["-c:a", "aac", "-b:a", abit, "-ar", "48000",
                "-loglevel", "error", out_tmp.name]
        enc_label = (
            "nvenc+hwaccel" if use_hwaccel else
            ("nvenc" if use_nvenc else "x264")
        )
        enc_lo = 15.0 if twopass_mixed else 0.0
        try:
            st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
            st.update({"phase": "encode", "cur_text": "đang chạy…",
                       "progress": enc_lo,
                       "updated_at": time.time()})
            js.persist(m)
        except Exception:  # noqa: BLE001
            pass
        print(f"[render] {src_w}x{src_h}->{W}x{H} ({res_label}) zoom{zoom} · "
              f"motion={int(bool(motion))} lut={fx_meta.get('lut')} "
              f"{fx_meta.get('grain')} · flip={flip or 'off'} · "
              f"OCR {len(ocr)} · sub={bool(srt and srt.exists())} · "
              f"âm={used_mode} · giọng={'có' if dub else 'không'} · dài {total:.0f}s · "
              f"enc={enc_label}@{vbit} · fc_script={len(graph)}ch · "
              f"brand={fx_meta.get('brand')} · frame={fx_meta.get('frame')}"
              f"{' · twopass' if twopass_mixed else ''}"
              f"{f' · tốc độ x{speed_factor:.4f}' if use_speed else ''}",
              flush=True)
        if band:
            print(f"[render] hardsub blur band={band[0]:.3f}-{band[1]:.3f}", flush=True)
        if wm_fracs:
            print(f"[render] watermark timeline {len(wm_fracs)} đoạn", flush=True)
        rc, err = 1, ""
        chunk_sec, chunk_par = (0.0, 1) if use_speed else (chunk_plan(total) if twopass_mixed is not None else (0.0, 1))
        cuts = chunk_cuts(video, total, chunk_sec, fixes) if chunk_sec > 0 else []
        if cuts:
            pre_in = ["-hwaccel", "cuda", "-hwaccel_device", "0"] if use_hwaccel else []
            rc, err = _run_chunked(video, work_dir, graph, venc, pre_in, cuts, total,
                                   twopass_mixed, abit, out_tmp, m, enc_lo, chunk_par)
            if rc != 0 and err != "stopped by user":
                print(f"[render] chia đoạn lỗi ({err}) → render 1 lượt", flush=True)
        if rc != 0 and err != "stopped by user":
            rc, err = _ffmpeg_progress(cmd, total_render, m, cwd=work_dir, lo=enc_lo)
        if rc != 0 and use_nvenc and err != "stopped by user":
            print(f"[render] nvenc/hwaccel lỗi: {err or '(không có log)'}", flush=True)
            _nvenc_mark_fail(err)
            pref = _encoder_pref()
            el = (err or "").lower()
            # Ép nvenc: thử lại không hwaccel decode (vẫn NVENC encode), không chuyển CPU
            if pref == "nvenc":
                if "hwaccel" in el or "cuda" in el or "nvcuda" in el:
                    print("[render] thử lại NVENC không -hwaccel decode...", flush=True)
                    cmd2 = ["ffmpeg", "-y", "-i", _ff_input(video, work_dir)]
                    if twopass_mixed is not None:
                        cmd2 += ["-i", _ff_input(twopass_mixed, work_dir)]
                        if use_speed:
                            graph2 = f"[0:v]{vf}[vout];[1:a]atempo={speed_factor:.4f}[aout]"
                            _attach_filter_complex(cmd2, graph2, work_dir)
                            cmd2 += ["-map", "[vout]", "-map", "[aout]"]
                        else:
                            graph2 = f"[0:v]{vf}[vout]"
                            _attach_filter_complex(cmd2, graph2, work_dir)
                            cmd2 += ["-map", "[vout]", "-map", "1:a"]
                    else:
                        af2, _ = _build_audio_filter(
                            cmd2, work_dir, dub, no_vocals, vocals,
                            audio_mode, voice_vol, orig_vol)
                        if use_speed and af2:
                            if af2.endswith("[aout]"):
                                af2 = af2[:-6] + f"[aout_pre];[aout_pre]atempo={speed_factor:.4f}[aout]"
                            else:
                                af2 = af2 + f";[aout]atempo={speed_factor:.4f}[aout_spd]"
                        graph2 = f"[0:v]{vf}[vout]" + (";" + af2 if af2 else "")
                        _attach_filter_complex(cmd2, graph2, work_dir)
                        cmd2 += ["-map", "[vout]"]
                        if af2:
                            cmd2 += ["-map", "[aout]"]
                        else:
                            af_base2 = f"{AUDIO_TS_FIX},atempo={speed_factor:.4f}" if use_speed else AUDIO_TS_FIX
                            cmd2 += ["-map", "0:a?", "-af", af_base2]
                    cmd2 += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                             "-b:v", vbit, "-maxrate", vbit, "-bufsize", buf,
                             "-pix_fmt", "yuv420p", "-gpu", "0", "-enc_time_base", "demux"]
                    if total_render and total_render > 0:
                        cmd2 += ["-t", f"{total_render:.3f}"]
                    cmd2 += ["-c:a", "aac", "-b:a", abit, "-ar", "48000",
                             "-loglevel", "error", out_tmp.name]
                    rc, err = _ffmpeg_progress(cmd2, total_render, m, cwd=work_dir, lo=enc_lo)
                if rc != 0:
                    raise RuntimeError(
                        f"ffmpeg NVENC lỗi (RENDER_ENCODER=nvenc, không fallback CPU): {err}"
                    )
            elif any(x in el for x in ("nvenc", "cuda", "nvcuda", "driver", "encoder")):
                print("[render] thử CPU libx264...", flush=True)
                rc, err = _ffmpeg_progress(_x264_cmd(cmd), total_render, m, cwd=work_dir, lo=enc_lo)
            else:
                print("[render] không fallback CPU (lỗi không phải NVENC)", flush=True)
        elif rc == 0 and use_nvenc:
            _nvenc_mark_ok()
        if rc != 0:
            raise RuntimeError(f"ffmpeg lỗi: {err}")
        # atomic replace khi xong
        import os as _os
        _os.replace(out_tmp, out_final)
    except Exception as e:  # noqa: BLE001
        try:
            out_tmp.unlink(missing_ok=True)
        except OSError:
            pass
        m.mark_failed(STAGE, e)
        raise

    result = m.mark_done(STAGE, output_path=str(out_final),
                         zoom=zoom, flip=flip or "off", ocr_overlays=len(ocr),
                         audio_mode=used_mode, has_dub=bool(dub),
                         dub_file=str(dub) if dub else None,
                         resolution=res_label, width=W, height=H,
                         motion=bool(motion), lut=fx_meta.get("lut"),
                         grain=fx_meta.get("grain"),
                         brand=fx_meta.get("brand"), frame=fx_meta.get("frame"),
                         vbitrate=vbit,
                         speed_factor=round(speed_factor, 4) if use_speed else 1.0)
    print(f"[ok] Render xong -> {result['output_path']} · {W}x{H} ({res_label}) · "
          f"âm={used_mode} · giọng={'có' if dub else 'không'} · "
          f"vbit={vbit} · brand={fx_meta.get('brand')} · frame={fx_meta.get('frame')}"
          f"{f' · tốc độ x{speed_factor:.4f}' if use_speed else ''}")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stage render — ghép + xuất video localize.")
    ap.add_argument("work_dir")
    ap.add_argument("--zoom", type=float, default=1.12)
    ap.add_argument("--no-sub", action="store_true")
    ap.add_argument("--no-hardsub-blur", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--resolution", default="source",
                    help="source | 1080 (1K Full HD)")
    ap.add_argument("--flip", default="", choices=["", "h", "v", "hv"],
                    help="lật hình: h=ngang, v=dọc, hv=cả hai (phụ đề Việt không bị gương)")
    ap.add_argument("--audio-mode", default=None,
                    help="voice_bgm | duck_bgm | voice_only | duck | demucs_*")
    ap.add_argument("--motion", dest="motion", action="store_true", default=True)
    ap.add_argument("--no-motion", dest="motion", action="store_false")
    ap.add_argument("--lut", dest="lut", action="store_true", default=True)
    ap.add_argument("--no-lut", dest="lut", action="store_false")
    ap.add_argument("--grain", default="weak",
                    choices=["off", "weak", "medium", "strong"])
    ap.add_argument("--brand", action="store_true",
                    help="bật chữ kênh (hoặc dùng --brand-mode)")
    ap.add_argument("--brand-mode", default=None,
                    help="off | drift | corners | drift_seed")
    ap.add_argument("--brand-text", default=None, help="tên kênh / watermark chữ")
    ap.add_argument("--brand-logo", default=None,
                    help="off | auto | file logo PNG")
    ap.add_argument("--frame", default=None,
                    help="off | auto | tên file trong assets/overlays/frames")
    ap.add_argument("--vbitrate", default="8M",
                    help="H264 bitrate: 2M | 4M | 8M (mặc định)")
    ap.add_argument("--speed", "--speed-factor", dest="speed_factor", type=float, default=None,
                    help="tốc độ video+audio lách bản quyền: 1.022 (mặc định 1.0)")
    args = ap.parse_args(argv)
    run_render(args.work_dir, zoom=args.zoom, add_sub=not args.no_sub,
               hardsub_blur=not args.no_hardsub_blur, force=args.force,
               resolution=args.resolution, flip=args.flip,
               audio_mode=args.audio_mode, motion=args.motion,
               lut=args.lut, grain=args.grain,
               brand=True if args.brand else None,
               brand_text=args.brand_text, brand_mode=args.brand_mode,
               brand_logo=args.brand_logo, frame=args.frame,
               vbitrate=args.vbitrate, speed_factor=args.speed_factor)


if __name__ == "__main__":
    main()