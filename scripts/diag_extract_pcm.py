"""Diag: how does THIS ffmpeg (e.g. 8.1.2 Windows) behave on AAC corrupted mid-file?

Builds the same synthetic test video as tests/test_extract_pcm_timing.py (beep 1 kHz 0.1 s at
3, 13, 23, ... s; gray 64x36 h264 10 fps; AAC stereo 44.1k), corrupts bytes 40-48 % of the file
(seed 7, same as the test), then runs several ffmpeg command variants for windows
25-35 s, 30-35 s and the full file. For every run: rc, decode-error lines, beeps found,
zero runs (exact 0 samples), nonzero fraction.

Optional: --v2 Z:\\PinelineV2  -> also run media_ts.extract_pcm_from_video from that tree.

Writes JSON (default: %TEMP%\\diag_extract_pcm\\result.json) and prints a summary.
Read-only for every Pineline tree; temp files only in --out.
No GPU, no HTTP, no LLM.
"""
from __future__ import annotations

import argparse
import array
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

AF = "aresample=async=1:min_hard_comp=0.01:first_pts=0"
BEEP_FIRST, BEEP_EVERY, DUR = 3.0, 10.0, 60.0
ERR_RE = re.compile(
    r"\baac\b|invalid data|error (while )?decoding|decoding error|channel element|rematrix|"
    r"reinitializ|prediction is not allowed|number of (bands|scalefactor)|reserved bit|"
    r"too large remapped|input buffer exhausted|invalid bitstream|sbr|corrupt|"
    r"error submitting packet|failed to (decode|send)|channel (layout|count) change", re.I)
SKIP_RE = re.compile(r"non[- ]?monoton|dts|pts|timestamp", re.I)


def ffmpeg_version() -> str:
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        return (r.stdout or "").splitlines()[0].strip()
    except OSError as e:
        return f"ffmpeg not found: {e}"


def make_video(p: Path) -> None:
    expr = (f"if(lt(mod(t-{BEEP_FIRST}\\,{BEEP_EVERY})\\,0.1)*gte(t\\,{BEEP_FIRST})\\,"
            f"0.8*sin(2*PI*1000*t)\\,0.02*sin(2*PI*220*t))")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=gray:s=64x36:r=10:d={DUR}",
        "-f", "lavfi", "-i", f"aevalsrc='{expr}|{expr}':s=44100:d={DUR}",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "20",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(p),
    ], check=True, capture_output=True)


def corrupt(src: Path, dst: Path, lo: float = 0.40, hi: float = 0.48, seed: int = 7) -> None:
    random.seed(seed)
    b = bytearray(src.read_bytes())
    s, e = int(len(b) * lo), int(len(b) * hi)
    for pos in range(s, e, 97):
        for k in range(40):
            b[pos + k] = random.randrange(256)
    dst.write_bytes(bytes(b))


def analyse(wav: Path, offset: float) -> dict:
    if not wav.exists() or wav.stat().st_size < 64:
        return {"exists": False}
    with wave.open(str(wav), "rb") as w:
        ar, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    a = array.array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    x = a[::ch]
    lim = int(0.3 * 32768)
    beeps, last = [], -10.0
    for i, v in enumerate(x):
        if v > lim or v < -lim:
            t = i / ar
            if t - last > 1.0:
                beeps.append(round(offset + t, 3))
            last = t
    runs, start = [], None
    blk = max(1, ar // 100)
    nz = tot = 0
    for i in range(0, len(x), blk):
        seg = x[i:i + blk]
        tot += 1
        zero = not any(seg)
        if not zero:
            nz += 1
        if zero and start is None:
            start = i
        if not zero and start is not None:
            if (i - start) / ar >= 0.5:
                runs.append([round(offset + start / ar, 2), round((i - start) / ar, 2)])
            start = None
    if start is not None and (len(x) - start) / ar >= 0.5:
        runs.append([round(offset + start / ar, 2), round((len(x) - start) / ar, 2), "to_end"])
    return {"exists": True, "dur": round(n / ar, 3), "beeps": beeps, "zero_runs_ge_0.5s": runs,
            "nonzero_frac": round(nz / tot, 3) if tot else 0.0}


def run(cmd: list[str], out: Path, offset: float) -> dict:
    out.unlink(missing_ok=True)
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
    errs = [ln for ln in lines if ERR_RE.search(ln) and not SKIP_RE.search(ln)]
    res = {"cmd": " ".join(cmd), "rc": r.returncode, "sec": round(time.time() - t0, 2),
           "n_stderr": len(lines), "n_decode_err": len(errs), "first_err": errs[:3]}
    res.update(analyse(out, offset))
    return res


def pcm_cmd(src, out, *, ss=None, t=None, out_seek=False, discard=True, extra=()):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if discard:
        cmd += ["-fflags", "+discardcorrupt"]
    if ss is not None and not out_seek:
        cmd += ["-ss", f"{ss:.3f}"]
    cmd += list(extra) + ["-i", str(src)]
    if ss is not None and out_seek:
        cmd += ["-ss", f"{ss:.3f}"]
    if t is not None:
        cmd += ["-t", f"{t:.3f}"]
    cmd += ["-vn", "-af", AF, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "diag_extract_pcm"))
    ap.add_argument("--v2", default="", help="Pineline tree with media_ts.py (e.g. Z:\\PinelineV2)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    good, bad = out / "good.mp4", out / "bad.mp4"
    make_video(good)
    corrupt(good, bad)
    m4a = out / "bad_audio.m4a"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(bad),
                    "-map", "0:a:0", "-vn", "-c", "copy", str(m4a)], capture_output=True)
    expected = [BEEP_FIRST + k * BEEP_EVERY for k in range(6)]
    report = {"ffmpeg": ffmpeg_version(), "python": sys.version.split()[0], "expected_beeps": expected,
              "damage_bytes": "40-48 % of file (~24-29 s)", "runs": {}}
    variants = {
        "A_input_seek": dict(),
        "B_no_discardcorrupt": dict(discard=False),
        "C_output_seek": dict(out_seek=True),
        "D_err_detect_ignore": dict(extra=("-err_detect", "ignore_err")),
        "E_audio_only_m4a": dict(src=m4a),
        "F_audio_only_m4a_ignore_err": dict(src=m4a, extra=("-err_detect", "ignore_err")),
    }
    windows = {"full": (None, None), "w25_35": (25.0, 10.0), "w30_35": (30.0, 5.0), "w30_60": (30.0, 30.0)}
    for wname, (ss, t) in windows.items():
        for vname, kw in variants.items():
            kw = dict(kw)
            src = kw.pop("src", bad)
            if wname == "full" and vname == "C_output_seek":
                continue
            o = out / f"{wname}_{vname}.wav"
            report["runs"][f"{wname}/{vname}"] = run(pcm_cmd(src, o, ss=ss, t=t, **kw), o, ss or 0.0)
    report["runs"]["full/good_reference"] = run(pcm_cmd(good, out / "good.wav"), out / "good.wav", 0.0)
    if a.v2:
        sys.path.insert(0, a.v2)
        try:
            import media_ts as mt  # noqa: E402
            o = out / "media_ts_extract.wav"
            t0 = time.time()
            info = mt.extract_pcm_from_video(bad, o, ac=1, ar=16000)
            r = {"info": info, "sec": round(time.time() - t0, 2), "media_ts": str(Path(mt.__file__))}
            r.update(analyse(o, 0.0))
            report["runs"]["media_ts.extract_pcm_from_video"] = r
        except Exception as e:  # noqa: BLE001
            report["runs"]["media_ts.extract_pcm_from_video"] = {"error": f"{type(e).__name__}: {e}"}
    js = out / "result.json"
    js.write_text(json.dumps(report, ensure_ascii=True, indent=1), encoding="utf-8")
    print(report["ffmpeg"])
    print(f"{'run':44s} {'rc':>4s} {'err':>5s} {'nz':>6s}  beeps / zero runs (start,len)")
    for k, r in report["runs"].items():
        if "error" in r:
            print(f"{k:44s} ERROR {r['error']}")
            continue
        print(f"{k:44s} {str(r.get('rc', '-')):>4s} {r.get('n_decode_err', '-')!s:>5s} "
              f"{r.get('nonzero_frac', 0):>6} {r.get('beeps')} {r.get('zero_runs_ge_0.5s')}")
    print(f"\nJSON: {js}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
