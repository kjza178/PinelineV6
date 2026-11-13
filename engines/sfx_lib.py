"""
sfx_lib.py — chế độ audio `sfx_lib`: giọng Việt + TIẾNG ĐỘNG gốc + NHẠC KHO SẠCH theo mức.

Bỏ toàn bộ nhạc nền gốc (nguồn claim Content ID) nhưng giữ tiếng động (nổ, va đập, xe...).
Chuẩn bị trong work/<job>/ (cache theo chữ ký, chạy lại chỉ làm phần đổi):
  1. sfx_effect.flac / sfx_music.flac — tách âm gốc bằng CDX23 (Demucs4: thoại/tiếng động/nhạc),
     đọc + ghi theo khúc (phim 10h không phải nạp cả file vào RAM/VRAM).
  2. sfx_levels.json — nhạc gốc từng đoạn ở mức none/calm/mid/intense (độ to + độ dồn của stem nhạc;
     chỉ để đo, không dùng lại nhạc gốc).
  3. sfx_bgm.flac — dải nhạc dài bằng video từ assets/bgm_yt/<mức>/ (YouTube Audio Library), mỗi đoạn
     lấy bài đúng mức, nối crossfade, chuẩn hoá độ to từng bài.
Render (render.py, mode sfx_lib) trộn: tiếng động + nhạc kho (cả hai tự hạ khi giọng Việt nói) + dub.

Chạy ở tiến trình con (render gọi) để nhả VRAM/RAM khi xong:
    python sfx_lib.py work/<job> --video work/<job>/source.mp4 [--force]
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from core.config import get as cfg

ROOT = Path(__file__).resolve().parents[1]
SR = 44100
EFFECT = "sfx_effect.flac"
MUSIC = "sfx_music.flac"
LEVELS = "sfx_levels.json"
BGM = "sfx_bgm.flac"
SIG = "sfx_lib.json"
LEVEL_NAMES = ("none", "calm", "mid", "intense")
CHUNK_S = 600.0
OVERLAP_S = 10.0
WIN_S = 2.0
XFADE_S = 3.0
VERSION = 1


def effect_volume() -> float:
    try:
        return float(cfg("RENDER_SFX_VOL", "0.62") or "0.62")
    except ValueError:
        return 0.62


def music_volume() -> float:
    try:
        return float(cfg("RENDER_LIB_BGM_VOL", "0.25") or "0.25")
    except ValueError:
        return 0.25


def voice_boost() -> float:
    """Hệ số nhân thêm lên RENDER_DUB_VOL, chỉ cho sfx_lib."""
    try:
        v = float(cfg("RENDER_SFX_VOICE_BOOST", "1.25") or "1.25")
    except ValueError:
        v = 1.25
    return max(0.5, min(2.0, v))


def model_dir() -> Path:
    return Path(cfg("SFX_CDX_MODEL_DIR", "") or ROOT / "models" / "cdx23")


def lib_dir() -> Path:
    return Path(cfg("SFX_LIB_DIR", "") or ROOT / "assets" / "bgm_yt")


def model_files() -> list[str]:
    hq = str(cfg("SFX_CDX_HQ", "0") or "0").strip().lower() in ("1", "true", "yes")
    if hq:
        return ["97d170e1-a778de4a.th", "97d170e1-dbb4db15.th", "97d170e1-e41a5468.th"]
    return ["97d170e1-dbb4db15.th"]


def _fstat(p: Path) -> list:
    try:
        s = p.stat()
        return [str(p), s.st_size, int(s.st_mtime)]
    except OSError:
        return [str(p), None, None]


# ---------------------------------------------------------------- 1. tách theo khúc

def separate_stream(read_block, write_block, fn, sr: int = SR, chunk_s: float = CHUNK_S,
                    overlap_s: float = OVERLAP_S) -> int:
    """Tách theo khúc, nối crossfade tuyến tính ở vùng chồng.

    read_block(n) → [2, ≤n] (rỗng khi hết); fn(x [2, L]) → [S, 2, L]; write_block(y [S, 2, k]) ghi nối tiếp.
    Trả số mẫu đã ghi (= số mẫu đọc)."""
    c, o = int(chunk_s * sr), int(overlap_s * sr)
    in_tail = out_tail = None
    written = 0
    while True:
        blk = read_block(c)
        if blk.shape[-1] == 0:
            break
        x = blk if in_tail is None else np.concatenate([in_tail, blk], axis=1)
        y = np.asarray(fn(x), dtype=np.float32)
        if out_tail is not None:
            n = out_tail.shape[-1]
            w = np.linspace(0.0, 1.0, n, dtype=np.float32)
            y[:, :, :n] = out_tail * (1.0 - w) + y[:, :, :n] * w
        keep = min(o, blk.shape[-1])
        cut = y.shape[-1] - keep
        if cut > 0:
            write_block(y[:, :, :cut])
            written += cut
        out_tail = y[:, :, cut:].copy()
        in_tail = x[:, x.shape[-1] - keep:]
    if out_tail is not None and out_tail.shape[-1]:
        write_block(out_tail)
        written += out_tail.shape[-1]
    return written


def _cdx_fn(device: str):
    import torch
    from demucs.apply import apply_model
    from demucs.states import load_model

    models = []
    for name in model_files():
        p = model_dir() / name
        if not p.is_file():
            raise RuntimeError(f"thiếu model CDX23: {p}")
        mdl = load_model(str(p))
        mdl.to(device)
        mdl.eval()
        models.append(mdl)

    def fn(x: np.ndarray) -> np.ndarray:
        t = torch.from_numpy(np.ascontiguousarray(x[None])).float().to(device)
        with torch.no_grad():
            outs = [apply_model(mdl, t, shifts=1, overlap=0.8)[0].cpu().numpy() for mdl in models]
        o = np.mean(outs, axis=0)          # CDX23: 0 nhạc, 1 tiếng động, 2 thoại
        return np.stack([o[1], o[0]])      # → [tiếng động, nhạc]

    return fn


def separate(src: Path, work_dir: Path, total: float, log=print) -> None:
    """Âm gốc (video/audio bất kỳ) → sfx_effect.flac + sfx_music.flac."""
    import soundfile as sf
    import torch

    from engines.media_ts import AUDIO_TS_FIX
    device = "cuda" if torch.cuda.is_available() else "cpu"
    fn = _cdx_fn(device)
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-af", AUDIO_TS_FIX, "-ac", "2", "-ar", str(SR),
         "-f", "f32le", "-"], stdout=subprocess.PIPE)
    tmp = [work_dir / (EFFECT + ".partial.flac"), work_dir / (MUSIC + ".partial.flac")]
    outs = [sf.SoundFile(str(p), "w", SR, 2, "PCM_16", format="FLAC") for p in tmp]
    done = [0]
    t0 = time.time()

    def read_block(n: int) -> np.ndarray:
        raw = proc.stdout.read(n * 8)
        return np.frombuffer(raw[: len(raw) // 8 * 8], dtype=np.float32).reshape(-1, 2).T

    def write_block(y: np.ndarray) -> None:
        for f, s in zip(outs, y):
            f.write(np.clip(s.T, -1.0, 1.0))
        done[0] += y.shape[-1]
        if total > 0:
            pct = min(99.9, done[0] / SR / total * 100)
            log(f"[sfx] tách tiếng động {pct:.0f}% ({time.time() - t0:.0f}s)")

    try:
        separate_stream(read_block, write_block, fn, chunk_s=CHUNK_S, overlap_s=OVERLAP_S)
    finally:
        for f in outs:
            f.close()
        proc.stdout.close()
        rc = proc.wait()
    if rc != 0 or done[0] == 0:
        raise RuntimeError(f"ffmpeg đọc âm gốc lỗi (mã {rc}, {done[0]} mẫu)")
    for p, name in zip(tmp, (EFFECT, MUSIC)):
        p.replace(work_dir / name)
    if device == "cuda":
        torch.cuda.empty_cache()


# ---------------------------------------------------------------- 2. mức nhạc gốc

def window_features(music_path: Path, win_s: float = WIN_S) -> tuple[np.ndarray, np.ndarray]:
    """(rms_db, flux) mỗi cửa sổ win_s của stem nhạc (mono; flux tính ở ~11 kHz)."""
    import soundfile as sf
    n = int(win_s * SR)
    rms, flux = [], []
    for blk in sf.blocks(str(music_path), blocksize=n, dtype="float32", always_2d=True):
        mono = blk.mean(axis=1)
        rms.append(20 * np.log10(np.sqrt(np.mean(mono ** 2)) + 1e-9))
        d = mono[: len(mono) // 4 * 4].reshape(-1, 4).mean(axis=1)
        if len(d) >= 2048:
            frames = np.lib.stride_tricks.sliding_window_view(d, 1024)[::512] * np.hanning(1024)
            mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)))
            flux.append(float(np.maximum(np.diff(mag, axis=0), 0).mean()))
        else:
            flux.append(0.0)
    return np.array(rms, dtype=np.float32), np.array(flux, dtype=np.float32)


def _median(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or len(x) == 0:
        return x
    pad = k // 2
    xp = np.pad(x, pad, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(xp, k), axis=1)[: len(x)]


def classify(rms_db: np.ndarray, flux: np.ndarray, win_s: float = WIN_S, none_db: float = -45.0,
             smooth_s: float = 30.0, min_seg_s: float = 40.0) -> list[dict]:
    """Cửa sổ → đoạn {start, end, level}. Mức calm/mid/intense chia theo tam phân vị của chính phim."""
    n = len(rms_db)
    if n == 0:
        return []
    k = max(1, int(round(smooth_s / win_s)) | 1)
    present = _median(rms_db, max(1, int(round(10 / win_s)) | 1)) >= none_db
    lv = np.zeros(n, dtype=int)
    if present.any():
        r, f = rms_db[present], flux[present]
        z = (rms_db - r.mean()) / (r.std() + 1e-6) + 0.7 * (flux - f.mean()) / (f.std() + 1e-6)
        z = _median(z, k)
        q1, q2 = np.quantile(z[present], [1 / 3, 2 / 3])
        lv = np.where(z < q1, 1, np.where(z < q2, 2, 3))
        lv[~present] = 0
    segs: list[list] = []
    for i, v in enumerate(lv):
        if segs and segs[-1][2] == v:
            segs[-1][1] = i + 1
        else:
            segs.append([i, i + 1, int(v)])
    min_w = max(1, int(round(min_seg_s / win_s)))
    while len(segs) > 1:
        j = min(range(len(segs)), key=lambda i: segs[i][1] - segs[i][0])
        if segs[j][1] - segs[j][0] >= min_w:
            break
        if j == 0:
            nb = 1
        elif j == len(segs) - 1:
            nb = j - 1
        else:
            nb = j - 1 if segs[j - 1][1] - segs[j - 1][0] >= segs[j + 1][1] - segs[j + 1][0] else j + 1
        a, b = sorted((j, nb))
        segs[a] = [segs[a][0], segs[b][1], segs[nb][2]]
        del segs[b]
        i = 0
        while i < len(segs) - 1:
            if segs[i][2] == segs[i + 1][2]:
                segs[i][1] = segs[i + 1][1]
                del segs[i + 1]
            else:
                i += 1
    return [{"start": round(a * win_s, 2), "end": round(b * win_s, 2), "level": LEVEL_NAMES[v]}
            for a, b, v in segs]


# ---------------------------------------------------------------- 3. dải nhạc kho

def library(lib: Path | None = None) -> dict[str, list[tuple[str, float]]]:
    """{mức: [(đường dẫn, giây)]} từ assets/bgm_yt/manifest.json (+ file lạ trong thư mục mức)."""
    lib = lib or lib_dir()
    out: dict[str, list[tuple[str, float]]] = {k: [] for k in LEVEL_NAMES[1:]}
    for lv in out:
        for f in sorted((lib / lv).glob("*")):
            if f.suffix.lower() in (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac"):
                out[lv].append((str(f), _probe(f)))
    return out


def _probe(f: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def plan_bgm(segments: list[dict], total: float, lib: dict[str, list[tuple[str, float]]], job_key: str,
             xfade: float = XFADE_S) -> list[dict]:
    """Xếp bài cho từng đoạn → [{file, at, off, dur, fin, fout}] (giây). Đoạn 'none' để trống."""
    rnd = random.Random(job_key)
    order = {}
    for lv, items in lib.items():
        items = [it for it in items if it[1] > 2 * xfade + 1]
        rnd.shuffle(items)
        order[lv] = [items, 0]
    plan = []
    for seg in segments:
        lv = seg["level"]
        if lv == "none" or not order.get(lv, [[]])[0]:
            continue
        a = max(0.0, seg["start"] - xfade / 2)
        b = min(total, seg["end"] + xfade / 2)
        cur = a
        while b - cur > 0.5:
            items, i = order[lv]
            f, d = items[i % len(items)]
            order[lv][1] = i + 1
            dur = min(d, b - cur)
            plan.append({"file": f, "at": round(cur, 3), "off": 0.0, "dur": round(dur, 3),
                         "fin": xfade if cur > 0 else 0.0, "fout": min(xfade, dur / 2)})
            if dur < d:
                break
            cur += dur - xfade
    return plan


def _decode(f: str) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", f, "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True, check=True)
    a = np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2).T
    voiced = a[:, np.abs(a).max(axis=0) > 1e-3]
    rms = float(np.sqrt(np.mean(voiced ** 2))) if voiced.size else 0.0
    gain = 10 ** (-20 / 20) / rms if rms > 1e-6 else 1.0
    return (a * min(gain, 8.0)).astype(np.float32)


def render_bgm(plan: list[dict], total: float, out: Path, block_s: float = 30.0) -> None:
    """Ghi dải nhạc dài `total` giây theo plan (từng khối, không giữ cả dải trong RAM)."""
    import soundfile as sf
    n_total = int(round(total * SR))
    cache: dict[str, np.ndarray] = {}
    tmp = out.with_name(out.name + ".partial.flac")
    with sf.SoundFile(str(tmp), "w", SR, 2, "PCM_16", format="FLAC") as fo:
        pos = 0
        bs = int(block_s * SR)
        while pos < n_total:
            end = min(n_total, pos + bs)
            buf = np.zeros((2, end - pos), dtype=np.float32)
            for p in plan:
                p0 = int(round(p["at"] * SR))
                p1 = p0 + int(round(p["dur"] * SR))
                lo, hi = max(pos, p0), min(end, p1)
                if lo >= hi:
                    continue
                if p["file"] not in cache:
                    if len(cache) > 6:
                        cache.pop(next(iter(cache)))
                    cache[p["file"]] = _decode(p["file"])
                a = cache[p["file"]]
                s0 = int(round(p["off"] * SR)) + (lo - p0)
                seg = a[:, s0:s0 + (hi - lo)]
                if seg.shape[1] < hi - lo:
                    seg = np.pad(seg, ((0, 0), (0, hi - lo - seg.shape[1])))
                t = (np.arange(lo, hi) - p0) / SR
                g = np.ones(hi - lo, dtype=np.float32)
                if p["fin"] > 0:
                    g = np.minimum(g, t / p["fin"])
                if p["fout"] > 0:
                    g = np.minimum(g, (p["dur"] - t) / p["fout"])
                buf[:, lo - pos:hi - pos] += seg * np.clip(g, 0.0, 1.0)
            fo.write(np.clip(buf.T, -1.0, 1.0))
            pos = end
    tmp.replace(out)


# ---------------------------------------------------------------- điều phối

def prepare(work_dir: Path, video: Path, total: float | None = None, force: bool = False, log=print) -> dict:
    work_dir, video = Path(work_dir), Path(video)
    total = float(total or _probe(video))
    if total <= 0:
        raise RuntimeError(f"không đo được độ dài {video}")
    sig_path = work_dir / SIG
    try:
        old = json.loads(sig_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    lib = library()
    if not any(lib.values()):
        raise RuntimeError(f"kho nhạc trống: {lib_dir()}")
    sep_sig = {"v": VERSION, "video": _fstat(video), "models": model_files(), "chunk": [CHUNK_S, OVERLAP_S]}
    bgm_sig = {"v": VERSION, "lib": {k: [(Path(f).name, round(d, 1)) for f, d in v] for k, v in lib.items()},
               "total": round(total, 2), "job": work_dir.name}
    if force or old.get("sep") != sep_sig or not (work_dir / EFFECT).exists() or not (work_dir / MUSIC).exists():
        log(f"[sfx] tách tiếng động/nhạc gốc bằng CDX23 ({len(model_files())} checkpoint)…")
        separate(video, work_dir, total, log=log)
        old = {"sep": sep_sig}
        sig_path.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
    if force or old.get("bgm") != bgm_sig or not (work_dir / BGM).exists() or not (work_dir / LEVELS).exists():
        log("[sfx] đo mức nhạc gốc theo đoạn…")
        rms, flux = window_features(work_dir / MUSIC)
        segs = classify(rms, flux)
        plan = plan_bgm(segs, total, lib, work_dir.name)
        (work_dir / LEVELS).write_text(json.dumps({"segments": segs, "plan": plan}, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
        cnt = {k: round(sum(s["end"] - s["start"] for s in segs if s["level"] == k) / 60, 1) for k in LEVEL_NAMES}
        log(f"[sfx] mức nhạc (phút): {cnt} · {len(plan)} lượt bài → ghép {BGM}")
        render_bgm(plan, total, work_dir / BGM)
        old["bgm"] = bgm_sig
        sig_path.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
    log("[sfx] sẵn sàng")
    return {"effect": str(work_dir / EFFECT), "bgm": str(work_dir / BGM), "levels": str(work_dir / LEVELS)}


def run_subprocess(work_dir: Path, video: Path, total: float, on_line=None) -> None:
    """Gọi prepare ở tiến trình con (nhả VRAM/RAM khi xong); on_line(dòng log) để cập nhật tiến độ."""
    cmd = [sys.executable, "-u", str(ROOT / "sfx_lib.py"), str(work_dir), "--video", str(video),
           "--total", f"{total:.3f}"]
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)
    tail: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-15:]
        print(line, flush=True)
        if on_line:
            on_line(line)
    if proc.wait() != 0:
        raise RuntimeError("sfx_lib lỗi:\n" + "\n".join(tail))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chuẩn bị audio sfx_lib (tiếng động gốc + nhạc kho).")
    ap.add_argument("work_dir")
    ap.add_argument("--video", required=True)
    ap.add_argument("--total", type=float, default=0.0)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    prepare(Path(a.work_dir), Path(a.video), a.total or None, force=a.force,
            log=lambda s: print(s, flush=True))


if __name__ == "__main__":
    main()
