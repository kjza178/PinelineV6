"""Mốc thời gian tiếng của file nguồn — sửa tiếng "dồn mẫu" ở file ghép nhiều tập.

File ghép copy nhiều tập AAC giữ lại 1024 mẫu mồi (priming) đầu mỗi tập: gói đó có
mốc gần như trùng gói trước (stts ~0) nhưng vẫn chứa 23 ms tiếng. Giải mã thường
(ffmpeg không aresample) dồn hết mẫu → audio.wav dài hơn hình, lệch tăng dần theo
số chỗ nối (0281: +1.2 s ở phút 54, +25 s ở cuối). ASR/TTS/sub/tiếng nền đều trễ theo.

Sửa: mọi chỗ tách tiếng từ video dùng AUDIO_TS_FIX (bám mốc, bỏ mẫu chồng, đệm khe).
Job cũ đã ASR trên audio dồn mẫu: ensure_job_audio_ts() dời mốc asr/script/tts/srt
về trục hình rồi ghép lại dub_fit — không cần ASR/TTS lại.
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import bisect
import json
import re
import shutil
import struct
import subprocess
import time
from pathlib import Path

AUDIO_TS_FIX = "aresample=async=1:min_hard_comp=0.01:first_pts=0"
DRIFT_MIN_SEC = 0.3
MARK = ".audio_ts_fixed.json"
MARK_PENDING = ".audio_ts_pending.json"
_JSON_FILES = ("asr.json", "script.json", "script.display.json", "script.pre_review.json", "tts.json")
_SRT_FILES = ("sub.zh.srt", "sub.vi.srt", "sub.vi.burn.srt")
_STALE_AUDIO = ("mixed_audio.wav", "mixed_audio.partial.wav", "orig_audio.flac", "orig_audio.wav")
_DEMUCS = ("no_vocals.wav", "vocals.wav")
# AAC hỏng giữa file (Hongguo merge): decoder báo N ch → aresample rematrix -22.
# extract_pcm_from_video: thử full; fail → salvage partial + chunk + silence chỗ chết.
_EXTRACT_CHUNK_SEC = 30.0
_EXTRACT_SUB_SEC = 5.0
_EXTRACT_OK_RATIO = 0.85   # chỉ để nhận 1 KHÚC chunk (khúc sau đó bị ép đúng độ dài cửa sổ)
# Nhận bản trích full chỉ khi thiếu so với hình ≤ ngưỡng này (s). Thiếu hơn → giữ head
# + chunk phần còn (fix review 2026-11-12 agentK: 0.85×vdur từng làm mất tới 15% cuối phim).
_EXTRACT_FULL_TOL_SEC = 1.0


# Bản trích "đủ dài" chưa chắc đúng nội dung: ffmpeg 8.x (Windows production) khi AAC chết giữa file
# có thể rc=0 và ĐỆM IM LẶNG (mẫu = 0 tuyệt đối) tới hết; 4.4 (VM test) lại hay rc=0 + dài thừa,
# bản Windows cũ hơn rc=-22 rematrix. Nhận diện lỗi theo từ khoá (chịu được thông điệp cả 4.4 lẫn 8.x,
# không phân biệt hoa thường; bỏ cảnh báo dts/pts của muxer) + dò đoạn 0 tuyệt đối.
_DECODE_ERR_RE = re.compile(
    r"\baac\b|invalid data|error (while )?decoding|decoding error|channel element|rematrix|"
    r"reinitializ|prediction is not allowed|number of (bands|scalefactor)|reserved bit|"
    r"too large remapped|input buffer exhausted|invalid bitstream|sbr|corrupt|"
    r"error submitting packet|failed to (decode|send)|channel (layout|count) change",
    re.I,
)
_DECODE_ERR_SKIP_RE = re.compile(r"non[- ]?monoton|dts|pts|timestamp", re.I)
_DEAD_ZERO_SEC = 2.0        # có lỗi giải mã: đoạn 0 tuyệt đối ≥ 2 s sau tiếng = decoder chết
_DEAD_TAIL_SEC = 5.0        # không có stderr: đuôi 0 tuyệt đối ≥ 5 s tới hết file
_WINDOW_DEAD_SEC = 1.0      # trong 1 khúc chunk có lỗi: 0 tuyệt đối ≥ 1 s → coi khúc hỏng


def _decode_errors(stderr) -> int:
    """Số dòng lỗi GIẢI MÃ (không tính cảnh báo mốc dts/pts của muxer)."""
    if not stderr:
        return 0
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    n = 0
    for ln in str(stderr).splitlines():
        if ln.strip() and _DECODE_ERR_RE.search(ln) and not _DECODE_ERR_SKIP_RE.search(ln):
            n += 1
    return n


def _dead_zero_run(wav: Path, min_sec: float, *, tail_only: bool = False) -> float | None:
    """Mốc (s) bắt đầu đoạn mẫu = 0 tuyệt đối dài ≥ min_sec, SAU khi đã có tiếng.

    tail_only: chỉ nhận đoạn 0 kéo tới hết file. None nếu không có / không đọc được (RF64…).
    """
    import wave
    try:
        with wave.open(str(wav), "rb") as r:
            ar, ch, sw, n = r.getframerate(), r.getnchannels(), r.getsampwidth(), r.getnframes()
            fb = ch * sw
            blk = max(1, ar // 10)                       # khối 0.1 s
            need = int(round(min_sec * ar))
            seen_sound = False
            run_start = None
            pos = 0
            while pos < n:
                data = r.readframes(blk)
                if not data:
                    break
                k = len(data) // fb
                if data.count(0) == len(data):
                    if seen_sound and run_start is None:
                        run_start = pos
                else:
                    if run_start is not None and not tail_only:
                        # đoạn 0 chỉ tính tới mẫu khác 0 đầu tiên trong khối này
                        first_nz = next(i for i, b in enumerate(data) if b) // fb
                        if pos + first_nz - run_start >= need:
                            return run_start / ar
                    seen_sound = True
                    # mẫu 0 ở đuôi khối có thể là đầu đoạn chết
                    tail = len(data) - len(data.rstrip(b"\x00"))
                    run_start = pos + k - tail // fb if tail // fb > 0 else None
                pos += k
            if run_start is not None and seen_sound and n - run_start >= need:
                return run_start / ar
    except (OSError, EOFError, wave.Error, StopIteration):
        return None
    return None


def _full_tol_sec() -> float:
    """EXTRACT_FULL_TOL_SEC (env / pineline.config.json), mặc định 1.0 s."""
    import os as _os
    v = _os.getenv("EXTRACT_FULL_TOL_SEC")
    if not v:
        try:
            import core.config as _cfg
            v = _cfg.get("EXTRACT_FULL_TOL_SEC")
        except Exception:  # noqa: BLE001
            v = None
    try:
        return max(0.0, float(v)) if v not in (None, "") else _EXTRACT_FULL_TOL_SEC
    except (TypeError, ValueError):
        return _EXTRACT_FULL_TOL_SEC


def _ffprobe_duration(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        return float((r.stdout or "0").strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def _ffprobe_audio_duration(path: Path) -> float:
    """Độ dài khai báo của luồng tiếng đầu tiên (s); 0 nếu không đọc được."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        return float((r.stdout or "0").strip().splitlines()[0] or 0)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0.0


def _ffmpeg_pcm_cmd(
    src: Path,
    out: Path,
    *,
    ac: int,
    ar: int,
    af: str,
    ss: float | None = None,
    t: float | None = None,
    rf64: bool = False,
    pre_in: list[str] | None = None,
) -> list[str]:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-fflags", "+discardcorrupt",
    ]
    if ss is not None:
        cmd += ["-ss", f"{float(ss):.3f}"]
    if pre_in:
        cmd += list(pre_in)
    cmd += ["-i", str(src)]
    if t is not None:
        cmd += ["-t", f"{float(t):.3f}"]
    cmd += ["-vn", "-af", af, "-ac", str(int(ac)), "-ar", str(int(ar)), "-c:a", "pcm_s16le"]
    if rf64:
        cmd += ["-rf64", "auto"]
    cmd.append(str(out))
    return cmd


def _write_silence_wav(out: Path, dur: float, *, ac: int, ar: int, rf64: bool = False) -> None:
    layout = "mono" if int(ac) == 1 else "stereo"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"anullsrc=channel_layout={layout}:sample_rate={int(ar)}",
        "-t", f"{max(0.01, float(dur)):.3f}", "-ac", str(int(ac)), "-c:a", "pcm_s16le",
    ]
    if rf64:
        cmd += ["-rf64", "auto"]
    cmd.append(str(out))
    subprocess.run(cmd, check=True, capture_output=True)


def _write_silence_frames(out: Path, n_frames: int, *, ac: int, ar: int, rf64: bool = False) -> None:
    """Im lặng ĐÚNG n_frames mẫu (pcm_s16le). wave lỗi → lavfi (độ chính xác ms)."""
    import wave
    try:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(int(ac))
            w.setsampwidth(2)
            w.setframerate(int(ar))
            left = max(0, int(n_frames))
            blk = b"\x00\x00" * int(ac) * 65536
            while left > 0:
                k = min(left, 65536)
                w.writeframes(blk[: k * 2 * int(ac)])
                left -= k
    except (OSError, wave.Error, EOFError):
        _write_silence_wav(out, max(1, int(n_frames)) / float(ar), ac=ac, ar=ar, rf64=rf64)


def _force_wav_frames(wav: Path, n_frames: int, *, ac: int, ar: int, rf64: bool = False) -> int:
    """Ép wav về ĐÚNG n_frames mẫu: thừa → cắt đuôi, thiếu → đệm im lặng ở đuôi.

    Giữ mốc đầu khúc = mốc cửa sổ (fix review 2026-11-12 agentK: khúc ≥85% cửa sổ từng được
    nhận nguyên → các khúc sau bị dồn sớm, lệch timing giữa phim). Trả số mẫu sau khi ép.
    """
    import wave
    n_frames = max(0, int(n_frames))
    tmp = wav.with_name(wav.stem + ".frames" + wav.suffix)
    try:
        with wave.open(str(wav), "rb") as r:
            if r.getsampwidth() != 2 or r.getnchannels() != int(ac) or r.getframerate() != int(ar):
                raise wave.Error("định dạng khác mong đợi")
            cur = r.getnframes()
            if cur == n_frames:
                return cur
            tmp.unlink(missing_ok=True)
            with wave.open(str(tmp), "wb") as w:
                w.setnchannels(int(ac))
                w.setsampwidth(2)
                w.setframerate(int(ar))
                left = min(cur, n_frames)
                while left > 0:
                    data = r.readframes(min(left, 1 << 20))
                    if not data:
                        break
                    w.writeframes(data)
                    left -= len(data) // (2 * int(ac))
                pad = n_frames - min(cur, n_frames) + max(0, left)
                blk = b"\x00\x00" * int(ac) * 65536
                while pad > 0:
                    k = min(pad, 65536)
                    w.writeframes(blk[: k * 2 * int(ac)])
                    pad -= k
        tmp.replace(wav)
        return n_frames
    except (OSError, wave.Error, EOFError):
        tmp.unlink(missing_ok=True)
    # RF64 / WAVE_FORMAT_EXTENSIBLE (py cũ) → ffmpeg theo số mẫu
    af = f"apad=whole_len={n_frames},atrim=end_sample={n_frames}"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(wav), "-af", af,
        "-ac", str(int(ac)), "-ar", str(int(ar)), "-c:a", "pcm_s16le",
    ]
    if rf64:
        cmd += ["-rf64", "auto"]
    cmd.append(str(tmp))
    subprocess.run(cmd, check=True, capture_output=True)
    tmp.replace(wav)
    return n_frames


def _fit_wav_to_duration(
    wav: Path,
    target_sec: float,
    *,
    ac: int,
    ar: int,
    rf64: bool = False,
) -> float:
    """Cắt/pad PCM về đúng target_sec (tránh async từng khúc làm wav dài hơn hình)."""
    if target_sec <= 0.5:
        return _ffprobe_duration(wav)
    cur = _ffprobe_duration(wav)
    if abs(cur - target_sec) <= 0.5:
        return cur
    tmp = wav.with_name(wav.stem + ".fit" + wav.suffix)
    tmp.unlink(missing_ok=True)
    # atrim cắt phần thừa; apad đệm nếu thiếu; -t chốt đúng dài
    af = f"atrim=0:{target_sec:.3f},asetpts=PTS-STARTPTS,apad=whole_dur={target_sec:.3f}"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(wav), "-af", af, "-t", f"{target_sec:.3f}",
        "-ac", str(int(ac)), "-ar", str(int(ar)), "-c:a", "pcm_s16le",
    ]
    if rf64:
        cmd += ["-rf64", "auto"]
    cmd.append(str(tmp))
    subprocess.run(cmd, check=True, capture_output=True)
    tmp.replace(wav)
    return _ffprobe_duration(wav)


def _concat_wav_parts(parts: list[Path], out: Path, *, rf64: bool = False) -> None:
    if not parts:
        raise RuntimeError("extract_pcm: không có khúc audio để ghép")
    if len(parts) == 1:
        shutil.copy2(parts[0], out)
        return
    lst = out.with_name(out.name + ".concat.txt")
    try:
        lines = []
        for p in parts:
            ap = str(p.resolve()).replace("'", r"'\''")
            lines.append(f"file '{ap}'")
        lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
        ]
        if rf64:
            cmd += ["-rf64", "auto"]
        cmd.append(str(out))
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        lst.unlink(missing_ok=True)


def _extract_window(
    src: Path,
    out: Path,
    *,
    ss: float,
    t: float,
    ac: int,
    ar: int,
    af: str,
    rf64: bool,
    dead_check: bool = True,
    pre_in: list[str] | None = None,
) -> float:
    """Trích [ss, ss+t). Trả duration thực; 0 nếu thất bại/rỗng. Số dòng lỗi giải mã → _LAST_WIN.

    dead_check (khúc 30 s): có lỗi giải mã + ĐUÔI 0 tuyệt đối ≥ _WINDOW_DEAD_SEC tới hết khúc — kể cả
    cả khúc = 0 từ đầu (ffmpeg 8.1.2 mở file giữa vùng hỏng → decoder chết ngay) → 0 → vòng gọi chia
    khúc con. Im lặng tự nhiên GIỮA khúc không tính (file lỗi rải rác: loại cả khúc vì nó làm chậm ~8×).
    Khúc con (dead_check=False) tự quyết (thử cách khác, giữ phần có tiếng).
    """
    _LAST_WIN["errs"] = 0
    out.unlink(missing_ok=True)
    try:
        r = subprocess.run(
            _ffmpeg_pcm_cmd(src, out, ac=ac, ar=ar, af=af, ss=ss, t=t, rf64=rf64, pre_in=pre_in),
            capture_output=True,
        )
    except OSError:
        return 0.0
    _LAST_WIN["errs"] = _decode_errors(getattr(r, "stderr", None))
    if not out.exists() or out.stat().st_size < 64:
        out.unlink(missing_ok=True)
        return 0.0
    if dead_check and _LAST_WIN["errs"] and _zero_tail_sec(out) >= _WINDOW_DEAD_SEC:
        out.unlink(missing_ok=True)
        return 0.0
    return _ffprobe_duration(out)


_LAST_WIN: dict = {"errs": 0}


def _has_zero_run(wav: Path, min_sec: float) -> bool:
    """Có đoạn mẫu 0 tuyệt đối ≥ min_sec ở bất kỳ đâu (kể cả đầu khúc / cả khúc)."""
    import wave
    try:
        with wave.open(str(wav), "rb") as r:
            ar, fb, n = r.getframerate(), r.getnchannels() * r.getsampwidth(), r.getnframes()
            need = max(1, int(round(min_sec * ar)))
            run = 0
            left = n
            while left > 0:
                data = r.readframes(min(left, max(1, ar // 10)))
                if not data:
                    break
                k = len(data) // fb
                left -= k
                if data.count(0) == len(data):
                    run += k
                else:
                    lead = (len(data) - len(data.lstrip(b"\x00"))) // fb
                    if run + lead >= need:
                        return True
                    run = (len(data) - len(data.rstrip(b"\x00"))) // fb
                if run >= need:
                    return True
    except (OSError, EOFError, wave.Error):
        return False
    return False


def _zero_tail_sec(wav: Path) -> float:
    """Độ dài (s) đoạn mẫu 0 tuyệt đối ở ĐUÔI file (cả file 0 → cả độ dài). Đọc ngược theo khối."""
    import wave
    try:
        with wave.open(str(wav), "rb") as r:
            ar, fb, n = r.getframerate(), r.getnchannels() * r.getsampwidth(), r.getnframes()
            blk = max(1, ar // 10)
            zeros = 0
            pos = n
            while pos > 0:
                k = min(blk, pos)
                pos -= k
                r.setpos(pos)
                data = r.readframes(k)
                if data.count(0) == len(data):
                    zeros += k
                    continue
                zeros += (len(data) - len(data.rstrip(b"\x00"))) // fb
                break
            return zeros / float(ar)
    except (OSError, EOFError, wave.Error):
        return 0.0


def _nonzero_frac(wav: Path) -> float:
    """Tỉ lệ khối 10 ms có mẫu khác 0 (đo "còn tiếng" để chọn bản trích tốt hơn)."""
    import wave
    try:
        with wave.open(str(wav), "rb") as r:
            blk = max(1, r.getframerate() // 100)
            tot = nz = 0
            while True:
                data = r.readframes(blk)
                if not data:
                    break
                tot += 1
                if data.count(0) != len(data):
                    nz += 1
            return nz / tot if tot else 0.0
    except (OSError, EOFError, wave.Error):
        return 0.0


def _audio_seek_source(src: Path, tmp: Path) -> tuple[Path, float]:
    """Copy luồng tiếng ra file chỉ-tiếng để seek theo GÓI TIẾNG (không theo keyframe hình).

    Seek trên file có hình: ffmpeg lùi về keyframe hình → giải mã tiếng từ trước mốc vài giây; nếu
    đoạn đó nằm trong vùng AAC hỏng thì decoder (8.1.2) chết cả khúc dù khúc bắt đầu sau vùng hỏng.
    Trả (nguồn seek, dịch mốc ss); lỗi / không khớp độ dài → (src, 0.0).
    """
    try:
        codec = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
             "-of", "default=nw=1:nk=1", str(src)], capture_output=True, text=True, timeout=120,
        ).stdout.strip().lower()
        # Chỉ AAC → .m4a (giữ edit list/priming: seek ra ĐÚNG mẫu như file gốc — đo lệch 0 mẫu).
        # .mka lệch ±1 ms (mốc ms) + mất priming 1024 mẫu; .nut mất priming → không dùng.
        # Copy 2 h ≈ 2 s khi file đã trong cache (lần đọc nguội trên VM: tới ~70 s vì I/O).
        if codec != "aac":
            return src, 0.0
        dst = tmp / "audio_seek.m4a"
        r = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
             "-map", "0:a:0", "-vn", "-sn", "-dn", "-c", "copy", str(dst)],
            capture_output=True,
        )
        if getattr(r, "returncode", 1) != 0 or not dst.exists() or dst.stat().st_size < 1024:
            return src, 0.0
        ref = max(_ffprobe_audio_duration(src), 0.0) or _ffprobe_duration(src)
        if abs(_ffprobe_duration(dst) - ref) > 1.0:
            return src, 0.0
        st = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=start_time",
             "-of", "default=nw=1:nk=1", str(dst)], capture_output=True, text=True, timeout=120,
        ).stdout.strip()
        # file copy: mốc = mốc gốc − start_time gốc ⇒ seek T trên copy = T − start_time(copy)
        shift = -float(st) if st not in ("", "N/A") else 0.0
        if abs(shift) > 5.0:
            return src, 0.0
        return dst, shift
    except (OSError, ValueError, subprocess.SubprocessError):
        return src, 0.0


def _extract_pcm_chunked(
    src: Path,
    out: Path,
    *,
    start: float,
    end: float,
    ac: int,
    ar: int,
    af: str,
    rf64: bool,
    chunk_sec: float,
    head: Path | None = None,
    seek_src: Path | None = None,
    ss_shift: float = 0.0,
    seek_factory=None,
) -> dict:
    """Ghép head (optional) + các khúc [start, end); chỗ AAC chết → silence cùng độ dài.

    seek_src/ss_shift: nguồn chỉ-tiếng để seek (xem _audio_seek_source); khúc con lỗi thử lại trên
    nguồn gốc với -err_detect ignore_err rồi chọn bản còn nhiều tiếng hơn; chỉ thay im lặng khi
    cả hai cách đều không ra gì.

    Mỗi khúc (và head) bị ép ĐÚNG số mẫu của cửa sổ: mẫu thứ k của file ra = mốc k/ar
    trên trục hình (khúc ra thiếu vài % không còn kéo các khúc sau sớm lên).
    """
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="pcm_extract_"))
    parts: list[Path] = []
    silenced = 0.0
    padded = 0
    retried = 0
    wsrc = seek_src or src
    wsh = float(ss_shift) if seek_src is not None else 0.0
    lazy = {"done": seek_src is not None or seek_factory is None}

    def _n_at(sec: float) -> int:
        return int(round(float(sec) * int(ar)))

    try:
        if head is not None and head.exists() and head.stat().st_size > 64:
            hp = tmp / "head.wav"
            shutil.copy2(head, hp)
            _force_wav_frames(hp, _n_at(start), ac=ac, ar=ar, rf64=rf64)
            parts.append(hp)
        t = float(start)
        end = float(end)
        i = 0
        while t < end - 0.05:
            take = min(float(chunk_sec), end - t)
            piece = tmp / f"c{i:05d}.wav"
            got = _extract_window(wsrc, piece, ss=t + wsh, t=take, ac=ac, ar=ar, af=af, rf64=rf64)
            if got < take * _EXTRACT_OK_RATIO and not lazy["done"]:
                # khúc đầu tiên hỏng → mới dựng nguồn chỉ-tiếng (copy tốn I/O cả file), thử lại khúc này
                lazy["done"] = True
                s2, sh2 = seek_factory()
                if s2 != src:
                    wsrc, wsh = s2, float(sh2)
                    got = _extract_window(wsrc, piece, ss=t + wsh, t=take, ac=ac, ar=ar, af=af, rf64=rf64)
            if got >= take * _EXTRACT_OK_RATIO:
                n_want = _n_at(t + take) - _n_at(t)
                if abs(got - take) * int(ar) >= 1:
                    padded += 1
                _force_wav_frames(piece, n_want, ac=ac, ar=ar, rf64=rf64)
                parts.append(piece)
                t += take
                i += 1
                continue
            piece.unlink(missing_ok=True)
            win_end = t + take
            while t < win_end - 0.05:
                st = min(_EXTRACT_SUB_SEC, win_end - t, end - t)
                piece = tmp / f"c{i:05d}.wav"
                n_want = _n_at(t + st) - _n_at(t)
                got = _extract_window(wsrc, piece, ss=t + wsh, t=st, ac=ac, ar=ar, af=af, rf64=rf64,
                                      dead_check=False)
                errs = _LAST_WIN.get("errs", 0)
                if got > 0 and errs and _zero_tail_sec(piece) >= _WINDOW_DEAD_SEC:
                    # decoder chết trong khúc con → thử cách khác, giữ bản còn nhiều tiếng hơn
                    alt = tmp / f"c{i:05d}.alt.wav"
                    g2 = _extract_window(src, alt, ss=t, t=st, ac=ac, ar=ar, af=af, rf64=rf64,
                                         dead_check=False, pre_in=["-err_detect", "ignore_err"])
                    retried += 1
                    if g2 > 0 and _nonzero_frac(alt) > _nonzero_frac(piece):
                        alt.replace(piece)
                        got = g2
                    else:
                        alt.unlink(missing_ok=True)
                elif got < st * _EXTRACT_OK_RATIO:
                    alt = tmp / f"c{i:05d}.alt.wav"
                    g2 = _extract_window(src, alt, ss=t, t=st, ac=ac, ar=ar, af=af, rf64=rf64,
                                         dead_check=False, pre_in=["-err_detect", "ignore_err"])
                    retried += 1
                    if g2 > got:
                        alt.replace(piece)
                        got = g2
                    else:
                        alt.unlink(missing_ok=True)
                # khúc con có tiếng (dù có đoạn 0) vẫn nhận: thay im lặng chỉ làm mất phần tiếng thật
                if got >= st * _EXTRACT_OK_RATIO or (got > 0 and _nonzero_frac(piece) > 0):
                    _force_wav_frames(piece, n_want, ac=ac, ar=ar, rf64=rf64)
                    parts.append(piece)
                else:
                    piece.unlink(missing_ok=True)
                    _write_silence_frames(piece, n_want, ac=ac, ar=ar, rf64=rf64)
                    parts.append(piece)
                    silenced += st
                    print(f"[extract] AAC hỏng → silence {st:.1f}s @{t:.1f}s", flush=True)
                t += st
                i += 1
        final = out.with_name(out.stem + ".partial" + out.suffix)
        final.unlink(missing_ok=True)
        _concat_wav_parts(parts, final, rf64=rf64)
        if end > 0.5:
            _fit_wav_to_duration(final, float(end), ac=ac, ar=ar, rf64=rf64)
        final.replace(out)
        return {
            "mode": "chunked",
            "duration": _ffprobe_duration(out),
            "silence_sec": round(silenced, 3),
            "n_parts": len(parts),
            "from_sec": round(float(start), 3),
            "n_fixed_len": padded,
            "n_retry": retried,
            "seek_src": "audio_only" if wsrc != src else "source",
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def extract_pcm_from_video(
    src: str | Path,
    out: str | Path,
    *,
    ac: int = 1,
    ar: int = 16000,
    af: str | None = None,
    rf64: bool = False,
    chunk_sec: float = _EXTRACT_CHUNK_SEC,
) -> dict:
    """Tách PCM từ video. Full-file trước; AAC rematrix/hỏng → salvage + chunk + silence.

    Dùng chung cho ASR (16k mono) và Demucs orig (44.1k stereo).
    """
    import os as _os

    src_p, out_p = Path(src), Path(out)
    af = af if af is not None else AUDIO_TS_FIX
    out_p.parent.mkdir(parents=True, exist_ok=True)
    part = out_p.with_name(out_p.stem + ".partial" + out_p.suffix)
    part.unlink(missing_ok=True)
    vdur = _ffprobe_duration(src_p)
    if vdur <= 0:
        # fallback: vẫn thử full
        vdur = 0.0

    def _chunk(**kw) -> dict:
        """Chunk qua nguồn chỉ-tiếng (seek theo gói tiếng); dọn file tạm sau khi xong."""
        import tempfile
        tdir = Path(tempfile.mkdtemp(prefix="pcm_seek_"))
        try:
            return _extract_pcm_chunked(
                src_p, out_p, seek_factory=lambda: _audio_seek_source(src_p, tdir), **kw)
        finally:
            shutil.rmtree(tdir, ignore_errors=True)

    rc = 1
    try:
        r = subprocess.run(
            _ffmpeg_pcm_cmd(src_p, part, ac=ac, ar=ar, af=af, rf64=rf64),
            capture_output=True,
        )
        rc = int(r.returncode)
    except OSError as e:
        raise RuntimeError(f"ffmpeg extract lỗi: {e}") from e
    n_err = _decode_errors(getattr(r, "stderr", None))

    got = _ffprobe_duration(part) if part.exists() else 0.0
    # "Đủ dài" chưa chứng minh nội dung đúng: decoder chết → ffmpeg (8.x) đệm 0 tới hết, rc có thể 0.
    # Có lỗi giải mã / rc≠0: đoạn 0 tuyệt đối ≥ _DEAD_ZERO_SEC sau tiếng = điểm chết.
    # Không có stderr: chỉ tin dấu hiệu đuôi 0 tuyệt đối ≥ _DEAD_TAIL_SEC tới hết file.
    dead_at = None
    if part.exists() and got >= 1.0:
        if n_err or rc != 0:
            dead_at = _dead_zero_run(part, _DEAD_ZERO_SEC)
        else:
            dead_at = _dead_zero_run(part, _DEAD_TAIL_SEC, tail_only=True)
    if dead_at is not None and dead_at >= 1.0 and (vdur <= 0 or dead_at < vdur - 0.05):
        print(f"[extract] bản full có {n_err} dòng lỗi giải mã (rc={rc}), mẫu = 0 từ {dead_at:.1f}s "
              f"(decoder chết, ffmpeg đệm im lặng) → giữ head tới đó, chunk phần còn", flush=True)
        end = vdur if vdur > 0 else got
        info = _chunk(
            start=float(dead_at), end=end, ac=ac, ar=ar, af=af, rf64=rf64,
            chunk_sec=chunk_sec, head=part,
        )
        part.unlink(missing_ok=True)
        info.update(dead_at=round(float(dead_at), 3), decode_errors=n_err)
        return info
    if dead_at is not None and dead_at < 1.0:
        part.unlink(missing_ok=True)
        info = _chunk(
            start=0.0, end=vdur if vdur > 0 else got, ac=ac, ar=ar, af=af, rf64=rf64,
            chunk_sec=chunk_sec, head=None,
        )
        info.update(dead_at=round(float(dead_at), 3), decode_errors=n_err)
        return info
    # Đủ dài so với video: chỉ thiếu ≤ EXTRACT_FULL_TOL_SEC (mặc định 1 s). Thiếu hơn (rematrix
    # chết giữa chừng / AAC hỏng đoạn cuối, kể cả rc=0) → giữ head + chunk phần còn bên dưới.
    long_enough = got >= 1.0 and (vdur <= 0 or got >= vdur - _full_tol_sec())
    if long_enough and vdur > 0 and got > vdur + _full_tol_sec():
        # Dài hơn hình: hợp lệ nếu chính luồng tiếng khai dài vậy; nếu không → AAC rác sinh thêm
        # mẫu (đo trên merged.mp4.bak_aac_corrupt: 7810.9 s vs hình 7741.4 s) → trục thời gian
        # sai, không dùng được cả làm head → chunk lại từ 0 (mỗi khúc bị ép đúng cửa sổ).
        adecl = _ffprobe_audio_duration(src_p)
        if got > max(vdur, adecl) + _full_tol_sec():
            print(f"[extract] bản full DÀI hơn hình {got - vdur:+.1f}s (luồng tiếng khai {adecl:.1f}s)"
                  f" → AAC rác, chunk lại toàn bộ", flush=True)
            part.unlink(missing_ok=True)
            info = _chunk(
                start=0.0, end=vdur, ac=ac, ar=ar, af=af, rf64=rf64,
                chunk_sec=chunk_sec, head=None,
            )
            info["overlong_full_sec"] = round(got, 3)
            return info
    if part.exists() and long_enough and rc == 0:
        _os.replace(part, out_p)
        return {"mode": "full", "duration": got, "silence_sec": 0.0}
    if part.exists() and long_enough:
        # exit≠0 / có lỗi nhưng đủ dài và KHÔNG có đoạn 0 kiểu decoder chết — nhận (ghi số lỗi)
        _os.replace(part, out_p)
        return {"mode": "full_soft", "duration": got, "silence_sec": 0.0, "ffmpeg_rc": rc,
                "decode_errors": n_err}
    if got >= 1.0 and vdur > 0:
        print(f"[extract] bản full thiếu {vdur - got:.1f}s so với hình (rc={rc}) → chunk phần còn",
              flush=True)

    # Salvage phần đầu nếu đã có, rồi chunk phần còn lại
    start = got if got >= 1.0 else 0.0
    head = None
    if start >= 1.0 and part.exists():
        head = part
        print(f"[extract] salvage {start:.1f}s rồi chunk phần còn (AAC rematrix/hỏng)", flush=True)
    else:
        part.unlink(missing_ok=True)
        start = 0.0

    end = vdur if vdur > start else (start + 1.0)
    if end <= start + 0.05 and head is not None:
        _os.replace(head, out_p)
        return {"mode": "salvage_only", "duration": start, "silence_sec": 0.0}

    info = _chunk(
        start=start, end=end, ac=ac, ar=ar, af=af, rf64=rf64,
        chunk_sec=chunk_sec, head=head,
    )
    if head is not None:
        head.unlink(missing_ok=True)
    print(
        f"[extract] chunked xong · {info.get('duration', 0):.1f}s · "
        f"silence {info.get('silence_sec', 0)}s · {info.get('n_parts', 0)} khúc",
        flush=True,
    )
    return info


def _boxes(f, start: int, end: int):
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


def _child(f, s: int, e: int, name: bytes):
    return next(((s2, e2) for t, s2, e2 in _boxes(f, s, e) if t == name), None)


def mp4_track_stts(video: Path, kind: bytes = b"soun") -> tuple[int, list[tuple[int, int]]] | None:
    """(timescale, [(count, delta)]) của track đầu tiên loại `kind` (soun/vide)."""
    if Path(video).suffix.lower() not in (".mp4", ".mov", ".m4v"):
        return None
    try:
        with open(video, "rb") as f:
            f.seek(0, 2)
            moov = _child(f, 0, f.tell(), b"moov")
            if not moov:
                return None
            for t, s, e in _boxes(f, *moov):
                if t != b"trak":
                    continue
                mdia = _child(f, s, e, b"mdia")
                if not mdia:
                    continue
                hdlr, mdhd, minf = (_child(f, *mdia, n) for n in (b"hdlr", b"mdhd", b"minf"))
                if not (hdlr and mdhd and minf):
                    continue
                f.seek(hdlr[0] + 8)
                if f.read(4) != kind:
                    continue
                f.seek(mdhd[0])
                ver = f.read(1)[0]
                f.seek(mdhd[0] + (20 if ver else 12))
                tsc = struct.unpack(">I", f.read(4))[0]
                stbl = _child(f, *minf, b"stbl")
                stts = _child(f, *stbl, b"stts") if stbl else None
                if not stts:
                    return None
                f.seek(stts[0] + 4)
                n = struct.unpack(">I", f.read(4))[0]
                data = f.read(8 * n)
                return tsc, [struct.unpack_from(">II", data, 8 * i) for i in range(n)]
    except (OSError, struct.error, StopIteration):
        return None
    return None


def audio_drift_points(video: Path) -> tuple[list[float], list[float]] | None:
    """Trục 'dồn mẫu' → trục mốc: (vị trí theo mẫu [s], tiếng dư tích luỹ tới đó [s]).

    Mỗi gói AAC chứa `normal` mẫu (gói chuẩn có delta = normal). Gói lệch delta
    → dư (normal - delta) mẫu so với mốc (dương = chồng, âm = khe).
    """
    got = mp4_track_stts(video, b"soun")
    if not got:
        return None
    tsc, ent = got
    counts: dict[int, int] = {}
    for c, d in ent:
        counts[d] = counts.get(d, 0) + c
    normal = max(counts, key=counts.get)
    pos, acc = 0, 0
    xs: list[float] = []
    ys: list[float] = []
    last = len(ent) - 1
    for k, (c, d) in enumerate(ent):
        pos += c * normal
        if d != normal and not (k == last and c == 1):
            acc += c * (normal - d)
            xs.append(pos / tsc)
            ys.append(acc / tsc)
    return xs, ys


def audio_ts_drift(video: Path) -> float:
    """Tổng tiếng dư so với mốc (s). 0 nếu không đọc được."""
    pts = audio_drift_points(video)
    return pts[1][-1] if pts and pts[1] else 0.0


def make_retime(video: Path):
    """Hàm t(trục dồn mẫu) → t(trục hình). None nếu lệch không đáng kể."""
    pts = audio_drift_points(video)
    if not pts or not pts[1] or abs(pts[1][-1]) < DRIFT_MIN_SEC:
        return None
    xs, ys = pts

    def f(t: float) -> float:
        i = bisect.bisect_right(xs, t)
        return max(0.0, t - (ys[i - 1] if i else 0.0))
    return f


def _probe_dur(p: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(p)], capture_output=True, text=True, timeout=120)
        return float(r.stdout.strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        return 0.0


def _retime_srt(text: str, f) -> str:
    import re

    def conv(m):
        h, mi, s, ms = (int(x) for x in m.groups())
        t = f(h * 3600 + mi * 60 + s + ms / 1000)
        ms_t = int(round(t * 1000))
        return f"{ms_t // 3600000:02d}:{ms_t // 60000 % 60:02d}:{ms_t // 1000 % 60:02d},{ms_t % 1000:03d}"
    return re.sub(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", conv, text)


def ensure_job_audio_ts(work_dir: Path, video: Path) -> dict:
    """Job đã ASR trên audio dồn mẫu → dời mốc về trục hình + ghép lại dub_fit.

    Nhận diện qua độ dài audio.wav: bằng trục dồn mẫu (= hình + drift) thì cần dời.
    Ghi MARK để không dời 2 lần. Trả {"status": ok|fixed|skip, ...}.
    """
    work_dir = Path(work_dir)
    mark = work_dir / MARK
    if mark.exists():
        return {"status": "ok", "why": "đã dời mốc trước đó"}
    pending = work_dir / MARK_PENDING
    if pending.exists():
        # Crash giữa lúc thay file: tmp đã tính từ bản gốc → chỉ commit nốt, KHÔNG dời mốc lần 2
        info = json.loads(pending.read_text(encoding="utf-8"))
        print("[audio-ts] tiếp tục commit dở từ lần trước", flush=True)
        return _commit_retime(work_dir, info)
    drift = audio_ts_drift(video)
    if abs(drift) < DRIFT_MIN_SEC:
        return {"status": "ok", "drift": drift}
    wav = work_dir / "audio.wav"
    vdur = _probe_dur(video)
    adur = _probe_dur(wav) if wav.exists() else 0.0
    if adur and abs(adur - vdur) < min(1.0, abs(drift) / 2):
        return {"status": "ok", "drift": drift, "why": "audio.wav đã bám mốc"}
    if not adur or abs(adur - (vdur + drift)) > max(1.0, abs(drift) * 0.2):
        return {"status": "skip", "drift": drift, "why": f"audio.wav {adur:.1f}s không ở trục dồn mẫu"}
    f = make_retime(video)
    if f is None:
        return {"status": "ok", "drift": drift}
    print(f"[audio-ts] nguồn dư {drift:+.2f}s tiếng so với hình (ghép nhiều tập) → dời mốc job về trục hình",
        flush=True)
    bak = work_dir / "_pre_audio_ts"
    bak.mkdir(exist_ok=True)
    changed = []
    for name in _JSON_FILES:
        p = work_dir / name
        if not p.exists():
            continue
        rows = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            continue
        _backup_once(p, bak)
        for r in rows:
            if not isinstance(r, dict):
                continue
            for k in ("start", "end"):
                if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool):
                    r[k] = round(f(float(r[k])), 3)
            for w in r.get("words") or []:
                if isinstance(w, dict):
                    for k in ("start", "end"):
                        if isinstance(w.get(k), (int, float)):
                            w[k] = round(f(float(w[k])), 3)
        indent = 2 if name == "script.display.json" else None
        _tmp_of(p).write_text(json.dumps(rows, ensure_ascii=False, indent=indent), encoding="utf-8")
        changed.append(name)
    for name in _SRT_FILES:
        p = work_dir / name
        if p.exists():
            _backup_once(p, bak)
            _tmp_of(p).write_text(_retime_srt(p.read_text(encoding="utf-8"), f), encoding="utf-8")
            changed.append(name)
    tmp = work_dir / "audio.ts.wav"
    extract_pcm_from_video(video, tmp, ac=1, ar=16000, af=AUDIO_TS_FIX)
    info = {"drift": round(drift, 3), "video_dur": vdur, "audio_dur_before": adur,
            "changed": changed, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _atomic_write(work_dir / MARK_PENDING, json.dumps(info, ensure_ascii=False, indent=1))
    return _commit_retime(work_dir, info)


def _tmp_of(p: Path) -> Path:
    return p.with_name(p.name + ".audio_ts.tmp")


def _backup_once(p: Path, bak: Path) -> None:
    """Không bao giờ đè backup đã có — bản trong _pre_audio_ts luôn là bản gốc trước lần dời đầu."""
    if not (bak / p.name).exists():
        shutil.copy2(p, bak / p.name)


def _atomic_write(p: Path, text: str) -> None:
    t = p.with_name(p.name + ".tmp")
    t.write_text(text, encoding="utf-8")
    t.replace(p)


def _commit_retime(work_dir: Path, info: dict) -> dict:
    """Pha thay file — chạy lại an toàn: mỗi bước kiểm tra trạng thái trước khi làm."""
    bak = work_dir / "_pre_audio_ts"
    bak.mkdir(exist_ok=True)
    for name in info.get("changed") or []:
        t = _tmp_of(work_dir / name)
        if t.exists():
            t.replace(work_dir / name)
    for name in _STALE_AUDIO:
        (work_dir / name).unlink(missing_ok=True)
    for name in _DEMUCS:
        p = work_dir / name
        if p.exists():
            p.replace(work_dir / f"{p.stem}.drifted{p.suffix}")
            print(f"[audio-ts] {name} tách trên audio dồn mẫu → đổi tên .drifted, cần tách nền lại", flush=True)
    wav, tmp = work_dir / "audio.wav", work_dir / "audio.ts.wav"
    if tmp.exists():
        if wav.exists():
            if (bak / "audio.wav").exists():
                wav.unlink()
            else:
                wav.replace(bak / "audio.wav")
        tmp.replace(wav)
    from engines.assemble import assemble_fit
    if (work_dir / "tts.json").exists():
        r = assemble_fit(work_dir)
        print(f"[audio-ts] ghép lại dub_fit: lệch>0.5s {r.get('n_drift_gt_0_5')} · max {r.get('max_drift')}s",
            flush=True)
    _atomic_write(work_dir / MARK, json.dumps(info, ensure_ascii=False, indent=1))
    (work_dir / MARK_PENDING).unlink(missing_ok=True)
    print(f"[audio-ts] xong: {', '.join(info.get('changed') or [])} · bản cũ ở {bak.name}/", flush=True)
    return {"status": "fixed", **info}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Kiểm/sửa tiếng dồn mẫu của job (file ghép nhiều tập).")
    ap.add_argument("work_dir")
    ap.add_argument("--check", action="store_true", help="chỉ báo độ lệch, không sửa")
    a = ap.parse_args(argv)
    wd = Path(a.work_dir)
    vids = sorted(p for p in wd.glob("source.*") if p.suffix.lower() in (".mp4", ".mov", ".m4v", ".mkv"))
    try:
        import json as _json
        vp = (((_json.loads((wd / "manifest.json").read_text(encoding="utf-8")).get("stages") or {})
               .get("download") or {}).get("video_path"))
        if vp and Path(vp).is_file():
            vids = [Path(vp)]
    except (OSError, ValueError):
        pass
    if not vids:
        raise SystemExit("không thấy video nguồn (manifest video_path / source.*)")
    d = audio_ts_drift(vids[0])
    print(f"{vids[0].name}: tiếng dư {d:+.3f}s so với hình")
    if not a.check:
        print(ensure_job_audio_ts(wd, vids[0]))


if __name__ == "__main__":
    main()
