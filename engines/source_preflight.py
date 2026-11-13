"""Preflight + chuẩn hoá video nguồn TRƯỚC asr/separate/ocr/render (2026-11-12, agentK).

Vì sao: nguồn AAC hỏng giữa file (Hongguo merge → rematrix -22) làm ASR fail mã 1 khó hiểu,
và mỗi stage tự trích audio theo cách riêng. Preflight kiểm 1 lần ngay sau download/import:

  a) ffprobe: có hình + tiếng, codec, số kênh, sample rate, độ dài hình vs tiếng, nghi VFR;
     giải mã thử TOÀN BỘ audio (cùng filter AUDIO_TS_FIX như ASR) bắt lỗi AAC/rematrix/mốc;
     drift dồn mẫu (media_ts.audio_ts_drift); giải mã thử vài khung hình đầu/giữa.
  b) Lành → chỉ ghi kết quả: manifest stages.download.preflight = {ok: true, …}.
  c) Audio lỗi → nguồn chuẩn hoá trong work dir (KHÔNG đụng file gốc / D:\\Lib):
     hình -c:v copy + audio dựng lại bằng media_ts.extract_pcm_from_video (bám trục hình,
     đúng độ dài, chỗ chết = im lặng) → AAC. Ghi download.video_path_norm (+ lý do, silence).
  d) Không cứu được (không có hình/tiếng, hình không giải mã được, tiếng hỏng gần hết)
     → PreflightError với thông báo rõ (download.py: stage download failed; import: stage
     đầu tiên dùng nguồn fail kèm lý do).

Mọi stage lấy video qua MỘT hàm: resolve_source_video() (chỉ đọc) /
stage_source_video() (= ensure_preflight + resolve; dùng ở đầu stage xử lý).

Cờ (env > pineline.config.json > mặc định):
  VIDEO_PREFLIGHT=1   kiểm sau download/import (rẻ: ffprobe + giải mã audio)
  VIDEO_NORMALIZE=1   chỉ chạy khi preflight thấy lỗi audio
  VIDEO_NORMALIZE_DRIFT=0  drift dồn mẫu một mình KHÔNG kích chuẩn hoá (AUDIO_TS_FIX +
                           ensure_job_audio_ts đã xử lý); bật 1 để chuẩn hoá luôn.
Job cũ (download xong trước bản này, không có cờ preflight_pending) không bị chạy lại tự động;
quét chỉ-đọc: tools/preflight_scan.py.
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import json
import os
import re
import subprocess
import time
from pathlib import Path

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".flv", ".ts", ".m4v"}
NORM_STEM = "source_norm"          # KHÔNG khớp glob "source.*" (tránh lẫn với nguồn gốc)
PREFLIGHT_DIR = "_preflight"
LOCK_NAME = ".preflight.lock"
SHORT_AUDIO_WARN_SEC = 2.0
_GOOD_AR = (16000, 22050, 24000, 32000, 44100, 48000)

_RE_REMATRIX = re.compile(r"rematrix|reinitializing filters|channel layout change", re.I)
_RE_AAC = re.compile(
    r"aac|prediction is not allowed|channel element|invalid data|error while decoding|"
    r"number of (bands|scalefactor)|reserved bit|too large remapped|input buffer exhausted|"
    r"decode_|corrupt", re.I)
_RE_TS = re.compile(r"non[- ]monoton|dts|pts|timestamp", re.I)


class PreflightError(RuntimeError):
    """Nguồn video không dùng được — fail sớm với lý do rõ."""


# ───────────────────────────── cờ ─────────────────────────────

def _flag(name: str, default: str) -> bool:
    """env NAME (đặt tay, thắng) → cờ instance PINELINE_FLAG_NAME (core.instance.apply_env, port V6 Q6)
    → pineline.config.json → mặc định."""
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        v = os.getenv(f"PINELINE_FLAG_{name}")
    if v is None or str(v).strip() == "":
        try:
            import core.config as _cfg
            v = _cfg.get(name, default)
        except Exception:  # noqa: BLE001
            v = default
    return str(v if v is not None else default).strip().lower() not in ("0", "false", "no", "off", "")


def preflight_enabled() -> bool:
    return _flag("VIDEO_PREFLIGHT", "1")


def normalize_enabled() -> bool:
    return _flag("VIDEO_NORMALIZE", "1")


def normalize_on_drift() -> bool:
    return _flag("VIDEO_NORMALIZE_DRIFT", "0")


def pending_payload() -> dict:
    """Trường gắn vào stage download khi import (preflight chạy lười ở stage đầu tiên)."""
    return {"preflight_pending": True} if preflight_enabled() else {}


# ───────────────────────────── resolve nguồn ─────────────────────────────

def _read_download_stage(work_dir: Path) -> dict:
    try:
        data = json.loads((Path(work_dir) / "manifest.json").read_text(encoding="utf-8"))
        st = (data.get("stages") or {}).get("download") or {}
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def original_source_video(work_dir: str | Path, dl: dict | None = None) -> Path | None:
    """Video gốc của job (download.video_path → source.*), bỏ qua bản chuẩn hoá."""
    work_dir = Path(work_dir)
    dl = _read_download_stage(work_dir) if dl is None else dl
    vp = dl.get("video_path")
    if vp and Path(vp).exists():
        return Path(vp)
    for c in sorted(work_dir.glob("source.*")):
        if c.suffix.lower() in VIDEO_EXT:
            return c
    return None


def resolve_source_video(work_dir: str | Path, dl: dict | None = None) -> Path | None:
    """HÀM DUY NHẤT chọn video nguồn cho mọi stage. Chỉ đọc, không side effect.

    download.video_path_norm (nguồn chuẩn hoá còn file) → download.video_path → source.*
    """
    work_dir = Path(work_dir)
    dl = _read_download_stage(work_dir) if dl is None else dl
    norm = dl.get("video_path_norm")
    if norm and Path(norm).is_file():
        return Path(norm)
    return original_source_video(work_dir, dl)


def stage_source_video(work_dir: str | Path) -> Path | None:
    """Đầu stage xử lý (asr/separate/ocr/render/mux/split): preflight nếu đang chờ, rồi resolve."""
    ensure_preflight(work_dir)
    return resolve_source_video(work_dir)


# ───────────────────────────── kiểm ─────────────────────────────

def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _ratio(s: str | None) -> float:
    try:
        a, b = str(s or "0/1").split("/")
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(video: Path) -> dict:
    """ffprobe tóm tắt: format + luồng hình/tiếng đầu tiên."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration,start_time,format_name:stream=index,codec_type,codec_name,channels,"
             "channel_layout,sample_rate,duration,start_time,r_frame_rate,avg_frame_rate,width,height",
             "-of", "json", str(video)],
            capture_output=True, text=True, timeout=120,
        )
        data = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": f"ffprobe lỗi: {e}"}
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    if not streams and not fmt:
        return {"ok": False, "error": "ffprobe không đọc được file"}
    v = next((s for s in streams if s.get("codec_type") == "video"
              and s.get("codec_name") not in ("mjpeg", "png", "bmp")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    out: dict = {
        "ok": True,
        "format": fmt.get("format_name"),
        "duration": round(_f(fmt.get("duration")), 3),
        "n_audio": sum(1 for s in streams if s.get("codec_type") == "audio"),
        "video": None,
        "audio": None,
    }
    if v:
        fr, fa = _ratio(v.get("r_frame_rate")), _ratio(v.get("avg_frame_rate"))
        out["video"] = {
            "codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height"),
            "duration": round(_f(v.get("duration")), 3), "start": round(_f(v.get("start_time")), 3),
            "fps": round(fa or fr, 3),
            # rẻ: r_frame_rate ≠ avg_frame_rate rõ rệt → nghi VFR (chỉ ghi nhận)
            "vfr_suspect": bool(fr and fa and abs(fr - fa) / max(fr, fa) > 0.02),
        }
    if a:
        out["audio"] = {
            "codec": a.get("codec_name"), "channels": int(_f(a.get("channels"))),
            "layout": a.get("channel_layout"), "sample_rate": int(_f(a.get("sample_rate"))),
            "duration": round(_f(a.get("duration")), 3), "start": round(_f(a.get("start_time")), 3),
        }
    return out


def decode_audio_check(video: Path, *, timeout: float | None = None, dur_hint: float = 0.0) -> dict:
    """Giải mã TOÀN BỘ audio như ASR (AUDIO_TS_FIX, mono 16k) ra null — bắt lỗi AAC/rematrix/mốc."""
    from engines.media_ts import AUDIO_TS_FIX
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1",
           "-i", str(video), "-map", "0:a:0", "-vn", "-sn", "-dn",
           "-af", AUDIO_TS_FIX, "-ac", "1", "-ar", "16000", "-f", "null", "-"]
    to = timeout if timeout is not None else max(300.0, float(dur_hint or 0) * 0.5)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=to,
                           encoding="utf-8", errors="replace")
        rc, so, se = r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return {"rc": None, "timeout": True, "elapsed": round(time.time() - t0, 2)}
    except OSError as e:
        return {"rc": None, "error": str(e), "elapsed": round(time.time() - t0, 2)}
    decoded = 0.0
    for m in re.finditer(r"out_time_us=(\d+)", so):
        decoded = int(m.group(1)) / 1e6
    lines = [ln.strip() for ln in se.splitlines() if ln.strip()]
    kinds: dict[str, int] = {}
    for ln in lines:
        k = ("rematrix" if _RE_REMATRIX.search(ln) else "aac" if _RE_AAC.search(ln)
             else "timestamp" if _RE_TS.search(ln) else "other")
        kinds[k] = kinds.get(k, 0) + 1
    return {
        "rc": rc, "decoded_sec": round(decoded, 3), "n_err": len(lines), "kinds": kinds,
        "sample": lines[:5], "elapsed": round(time.time() - t0, 2),
    }


def decode_video_check(video: Path, dur: float) -> dict:
    """Giải mã thử 3 khung đầu + 3 khung giữa (rẻ). ok=False nếu cả hai đều không ra khung."""
    res = []
    for ss in (0.0, max(0.0, dur / 2.0)):
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error"]
        if ss > 0:
            cmd += ["-ss", f"{ss:.3f}"]
        cmd += ["-i", str(video), "-map", "0:v:0", "-frames:v", "3", "-f", "null", "-"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                               encoding="utf-8", errors="replace")
            res.append({"ss": round(ss, 1), "rc": r.returncode,
                        "err": (r.stderr or "").strip().splitlines()[:2]})
        except (OSError, subprocess.SubprocessError) as e:
            res.append({"ss": round(ss, 1), "rc": None, "err": [str(e)]})
    ok = any(x["rc"] == 0 for x in res)
    return {"ok": ok, "probes": res}


def check_source(video: str | Path, *, quick: bool = False) -> dict:
    """Kiểm chỉ-đọc. Trả {ok, fatal?, issues[], warnings[], probe, decode, drift, …}.

    ok=True  → nguồn dùng được nguyên trạng.
    issues   → lỗi audio cần chuẩn hoá (ok=False, fatal=None).
    fatal    → không cứu được.
    quick    → bỏ giải mã audio toàn bộ (cho quét hàng loạt).
    """
    video = Path(video)
    t0 = time.time()
    res: dict = {"video": str(video), "issues": [], "warnings": [], "fatal": None}
    try:
        st = video.stat()
        res["sig"] = {"size": st.st_size, "mtime": int(st.st_mtime)}
    except OSError as e:
        res.update(ok=False, fatal=f"không đọc được file nguồn: {e}")
        return res
    p = probe(video)
    res["probe"] = p
    if not p.get("ok"):
        res.update(ok=False, fatal=f"ffprobe không đọc được video ({p.get('error')})")
        return res
    if not p.get("video"):
        res.update(ok=False, fatal="nguồn KHÔNG có luồng hình (video stream)")
        return res
    if not p.get("audio"):
        res.update(ok=False, fatal="nguồn KHÔNG có luồng tiếng (audio stream) — không ASR/lồng tiếng được")
        return res
    vdur = p["duration"] or p["video"]["duration"]
    if vdur <= 0:
        res.update(ok=False, fatal="không xác định được độ dài video (ffprobe duration=0)")
        return res
    a, v = p["audio"], p["video"]
    if a["channels"] <= 0 or a["sample_rate"] <= 0:
        res["issues"].append(f"audio header lạ (channels={a['channels']}, sr={a['sample_rate']})")
    if a["duration"] and v["duration"] and v["duration"] - a["duration"] > SHORT_AUDIO_WARN_SEC:
        res["warnings"].append(f"tiếng ngắn hơn hình {v['duration'] - a['duration']:.1f}s (khi trích sẽ đệm im lặng)")
    if v.get("vfr_suspect"):
        res["warnings"].append("nghi VFR (r_frame_rate ≠ avg_frame_rate)")
    try:
        from engines.media_ts import DRIFT_MIN_SEC, audio_ts_drift
        drift = float(audio_ts_drift(video))
    except Exception:  # noqa: BLE001
        drift, DRIFT_MIN_SEC = 0.0, 0.3
    res["drift"] = round(drift, 3)
    if abs(drift) >= DRIFT_MIN_SEC:
        msg = f"tiếng dồn mẫu {drift:+.2f}s so với hình (file ghép nhiều tập)"
        (res["issues"] if normalize_on_drift() else res["warnings"]).append(msg)
    vd = decode_video_check(video, vdur)
    res["video_decode"] = vd
    if not vd["ok"]:
        res.update(ok=False, fatal="luồng hình không giải mã được (đầu + giữa phim)")
        return res
    if not quick:
        d = decode_audio_check(video, dur_hint=vdur)
        res["decode"] = d
        if d.get("timeout"):
            res["warnings"].append("giải mã thử audio quá thời gian — bỏ qua")
        elif d.get("rc") is None:
            res["warnings"].append(f"không chạy được ffmpeg giải mã thử: {d.get('error')}")
        else:
            kinds = d.get("kinds") or {}
            if d["rc"] != 0:
                res["issues"].append(f"giải mã audio lỗi (ffmpeg rc={d['rc']}, dừng ở {d.get('decoded_sec')}s"
                                     f"/{vdur:.0f}s)")
            if kinds.get("rematrix"):
                res["issues"].append(f"AAC đổi số kênh giữa file → rematrix ({kinds['rematrix']} dòng lỗi)")
            if kinds.get("aac"):
                res["issues"].append(f"AAC hỏng ({kinds['aac']} dòng lỗi giải mã)")
            if kinds.get("timestamp"):
                res["warnings"].append(f"lỗi mốc thời gian audio ({kinds['timestamp']} dòng)")
            if kinds.get("other"):
                res["warnings"].append(f"{kinds['other']} dòng lỗi khác khi giải mã audio")
    res["ok"] = not res["issues"]
    res["elapsed_sec"] = round(time.time() - t0, 2)
    return res


# ───────────────────────────── chuẩn hoá ─────────────────────────────

def normalize_source(work_dir: str | Path, video: str | Path, chk: dict | None = None) -> dict:
    """Hình -c:v copy + audio dựng lại (extract_pcm_from_video) → work_dir/source_norm.<mp4|mkv>.

    Không đụng file gốc. Trả {path, extract, duration, …}; lỗi → raise RuntimeError.
    """
    from engines.media_ts import extract_pcm_from_video
    work_dir, video = Path(work_dir), Path(video)
    chk = chk or {}
    pa = ((chk.get("probe") or {}).get("audio") or {})
    vdur = float((chk.get("probe") or {}).get("duration") or 0.0)
    ac = 1 if int(pa.get("channels") or 2) == 1 else 2
    ar = int(pa.get("sample_rate") or 0)
    ar = ar if ar in _GOOD_AR else 48000
    pdir = work_dir / PREFLIGHT_DIR
    pdir.mkdir(parents=True, exist_ok=True)
    wav = pdir / "norm_audio.wav"
    ext = ".mp4" if video.suffix.lower() in (".mp4", ".m4v", ".mov") else ".mkv"
    out = work_dir / f"{NORM_STEM}{ext}"
    tmp = work_dir / f"{NORM_STEM}.tmp{ext}"
    t0 = time.time()
    ex = extract_pcm_from_video(video, wav, ac=ac, ar=ar, rf64=True)
    t_ex = time.time() - t0
    tmp.unlink(missing_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
           "-i", str(video), "-i", str(wav),
           "-map", "0:v:0", "-map", "1:a:0", "-map_metadata", "0",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(ar), "-ac", str(ac),
           str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"remux nguồn chuẩn hoá lỗi rc={r.returncode}: {(r.stderr or '')[-300:]}")
    p2 = probe(tmp)
    if not (p2.get("ok") and p2.get("video") and p2.get("audio")):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("nguồn chuẩn hoá thiếu luồng hình/tiếng")
    if vdur > 0 and abs(p2["duration"] - vdur) > 1.0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"nguồn chuẩn hoá lệch độ dài {p2['duration']:.2f}s vs {vdur:.2f}s")
    d2 = decode_audio_check(tmp, dur_hint=vdur)
    if d2.get("rc") not in (0, None):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"nguồn chuẩn hoá vẫn lỗi giải mã audio rc={d2.get('rc')}")
    tmp.replace(out)
    try:
        wav.unlink()
    except OSError:
        pass
    return {
        "path": str(out.resolve()), "extract": ex, "duration": p2["duration"],
        "audio": {"ac": ac, "ar": ar, "codec": "aac"}, "elapsed_sec": round(time.time() - t0, 2),
        "extract_sec": round(t_ex, 2), "verify_decode": {k: d2.get(k) for k in ("rc", "n_err", "decoded_sec")},
    }


# ───────────────────────────── chạy + ghi manifest ─────────────────────────────

def _write_download(work_dir: Path, fields: dict, drop: tuple[str, ...] = ()) -> None:
    # V6 (port Q6): ghi trạng thái CHỈ qua state.job_state (một Manifest.update + index + event).
    from state import job_state as js

    def _fn(data: dict):
        st = data.setdefault("stages", {}).setdefault("download", {})
        for k in drop:
            st.pop(k, None)
        st.update(fields)

    js.mutate(work_dir, _fn)


def run_preflight(work_dir: str | Path, video: str | Path | None = None, *,
                  normalize: bool | None = None, write: bool = True) -> dict:
    """Kiểm nguồn gốc của job; lỗi audio → chuẩn hoá (nếu bật). Ghi download.preflight.

    Trả dict preflight. Fatal → dict có 'fatal' (người gọi quyết raise).
    """
    work_dir = Path(work_dir)
    t0 = time.time()
    src = Path(video) if video else original_source_video(work_dir)
    if src is None:
        pf = {"ok": False, "fatal": "chưa có file video nguồn", "action": "fatal"}
    else:
        print(f"[preflight] kiểm nguồn {src.name} …", flush=True)
        pf = check_source(src)
        pf["action"] = "none"
        do_norm = normalize_enabled() if normalize is None else bool(normalize)
        if pf.get("fatal"):
            pf["action"] = "fatal"
        elif pf.get("issues"):
            print(f"[preflight] nguồn có lỗi audio: {'; '.join(pf['issues'])}", flush=True)
            if not do_norm:
                pf["action"] = "normalize_off"
            else:
                try:
                    n = normalize_source(work_dir, src, pf)
                    ex = n.get("extract") or {}
                    sil = float(ex.get("silence_sec") or 0.0)
                    vdur = float((pf.get("probe") or {}).get("duration") or 0.0)
                    pf["normalize"] = n
                    if vdur > 0 and sil >= 0.9 * vdur:
                        pf["action"] = "fatal"
                        pf["fatal"] = (f"audio hỏng gần hết ({sil:.0f}s/{vdur:.0f}s phải thay bằng im lặng)"
                                       " — cần nguồn khác")
                        Path(n["path"]).unlink(missing_ok=True)
                    else:
                        pf["action"] = "normalized"
                        print(f"[preflight] đã chuẩn hoá → {Path(n['path']).name} · mode {ex.get('mode')}"
                              f" · im lặng {sil:.1f}s · {n.get('elapsed_sec')}s", flush=True)
                except Exception as e:  # noqa: BLE001
                    pf["action"] = "normalize_failed"
                    pf["normalize_error"] = str(e)[:400]
                    print(f"[preflight] chuẩn hoá lỗi ({e}) → dùng nguồn gốc (extract vẫn tự cứu)",
                          flush=True)
        else:
            print(f"[preflight] nguồn lành ({pf.get('elapsed_sec')}s)"
                  + (f" · cảnh báo: {'; '.join(pf['warnings'])}" if pf.get("warnings") else ""), flush=True)
    pf["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    pf["total_sec"] = round(time.time() - t0, 2)
    if write and (work_dir / "manifest.json").exists():
        fields: dict = {"preflight": pf}
        drop: tuple[str, ...] = ("preflight_pending", "video_path_norm", "norm_reason", "norm_silence_sec")
        if pf.get("action") == "normalized":
            fields["video_path_norm"] = pf["normalize"]["path"]
            fields["norm_reason"] = "; ".join(pf.get("issues") or [])
            fields["norm_silence_sec"] = float((pf["normalize"].get("extract") or {}).get("silence_sec") or 0.0)
        _write_download(work_dir, fields, drop)
    return pf


class _Lock:
    """Khoá file trong work dir (asr + separate có thể cùng gọi ensure)."""

    def __init__(self, work_dir: Path, wait_sec: float = 3 * 3600):
        self.p = Path(work_dir) / LOCK_NAME
        self.wait = wait_sec
        self.fd = None

    def __enter__(self):
        t0 = time.time()
        while True:
            try:
                self.fd = os.open(str(self.p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - self.p.stat().st_mtime > 4 * 3600:   # khoá mồ côi
                        self.p.unlink()
                        continue
                except OSError:
                    continue
                if time.time() - t0 > self.wait:
                    raise TimeoutError(f"preflight lock bận: {self.p}")
                time.sleep(1.0)

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                os.close(self.fd)
            self.p.unlink()
        except OSError:
            pass
        return False


def ensure_preflight(work_dir: str | Path) -> dict | None:
    """Gọi ở đầu stage xử lý. Rẻ khi không có gì phải làm (đọc manifest).

    - download.preflight_pending (job mới import/split) → chạy preflight (+ chuẩn hoá) 1 lần.
    - preflight fatal → raise PreflightError (fail sớm, lý do rõ).
    - Job cũ (không pending, không preflight) → không làm gì.
    """
    work_dir = Path(work_dir)
    dl = _read_download_stage(work_dir)
    pf = dl.get("preflight") if isinstance(dl.get("preflight"), dict) else None
    if dl.get("preflight_pending") and preflight_enabled():
        with _Lock(work_dir):
            dl = _read_download_stage(work_dir)
            if dl.get("preflight_pending"):
                pf = run_preflight(work_dir)
            else:
                pf = dl.get("preflight") if isinstance(dl.get("preflight"), dict) else None
    if pf and pf.get("fatal"):
        raise PreflightError(f"[preflight] nguồn video không dùng được: {pf['fatal']}")
    return pf


def inherit_payload(parent_dl: dict, parent_job_id: str) -> dict:
    """Trường preflight cho sub-job (split_parts) cắt từ nguồn của job cha."""
    pf = parent_dl.get("preflight") if isinstance(parent_dl.get("preflight"), dict) else None
    if pf is None:
        return pending_payload() if parent_dl.get("preflight_pending") else {}
    if pf.get("action") in ("none", "normalized", "inherited"):
        return {"preflight": {
            "ok": True, "action": "inherited", "inherited_from": parent_job_id,
            "parent_action": pf.get("action"), "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }}
    return pending_payload()


def after_download(work_dir: str | Path, video: str | Path | None = None) -> dict | None:
    """download.py gọi ngay sau mark_done (đang ở hàng tải → chạy luôn). Fatal → raise."""
    if not preflight_enabled():
        return None
    pf = run_preflight(work_dir, video)
    if pf.get("fatal"):
        raise PreflightError(f"[preflight] nguồn video không dùng được: {pf['fatal']}")
    return pf


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Preflight nguồn video của 1 job (mặc định chỉ kiểm, không ghi).")
    ap.add_argument("work_dir")
    ap.add_argument("--run", action="store_true", help="chạy thật: kiểm + chuẩn hoá nếu lỗi + ghi manifest")
    ap.add_argument("--no-normalize", action="store_true")
    ap.add_argument("--quick", action="store_true", help="chỉ ffprobe/drift, không giải mã audio")
    a = ap.parse_args(argv)
    wd = Path(a.work_dir)
    if a.run:
        pf = run_preflight(wd, normalize=False if a.no_normalize else None)
    else:
        src = original_source_video(wd)
        if not src:
            raise SystemExit("không thấy video nguồn")
        pf = check_source(src, quick=a.quick)
    print(json.dumps(pf, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
