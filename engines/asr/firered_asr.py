"""
firered_asr.py — STAGE ASR bằng FireRedASR2-AED (system VAD+LID+ASR+Punc).

Cần clone repo + tải model local (không pip một dòng):

    scripts\\install_firered_asr.bat

Hoặc thủ công:
    git clone https://github.com/FireRedTeam/FireRedASR2S.git vendor/FireRedASR2S
    pip install -r vendor/FireRedASR2S/requirements.txt
    huggingface-cli download FireRedTeam/FireRedASR2-AED --local-dir <FIRERED_MODELS>/FireRedASR2-AED
    ... (VAD, LID, Punc tương tự)

Config (pineline.config.json / env):
    FIRERED_ROOT     = Z:/PinelineV2/vendor/FireRedASR2S
    FIRERED_MODELS   = D:/AI_CACHE/firered   (chứa FireRedASR2-AED, FireRedVAD, ...)
    FIRERED_USE_HALF = 1  (mặc định: GPU → BF16/FP16; đặt 0 để FP32)

Chạy tay:
    python firered_asr.py work/<job>
    python firered_asr.py work/<job> --force
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[2])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from engines.asr.asr import ensure_audio_wav, resolve_asr_source, _salvage_asr
from core.config import get as cfg
from engines.asr.fun_asr_nano import _wav_dur
from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
from engines.subs import split_long_segments, sub_split_cfg, to_srt
from engines.vram import free_gpu_vram

STAGE = "asr"
ENGINE = "firered_asr"
MODEL_ID = "FireRedASR2-AED"


def _roots() -> tuple[Path, Path]:
    root = Path(os.getenv("FIRERED_ROOT") or cfg("FIRERED_ROOT", "") or
                str(Path(__file__).resolve().parents[2] / "vendor" / "FireRedASR2S"))
    models = Path(os.getenv("FIRERED_MODELS") or cfg("FIRERED_MODELS", "") or
                  str(root / "pretrained_models"))
    return root, models


def _ensure_firered_importable(root: Path) -> None:
    if not root.is_dir():
        raise RuntimeError(
            f"Chưa có FireRedASR2S tại {root}. Chạy scripts/install_firered_asr.bat "
            "hoặc git clone https://github.com/FireRedTeam/FireRedASR2S.git vendor/FireRedASR2S"
        )
    # package nằm trong repo root
    p = str(root.resolve())
    if p not in sys.path:
        sys.path.insert(0, p)
    bin_dir = root / "fireredasr2s"
    if bin_dir.is_dir():
        os.environ["PATH"] = str(bin_dir.resolve()) + os.pathsep + os.environ.get("PATH", "")


def _parse_firered_result(result: dict) -> list[dict]:
    """FireRedAsr2System.process → [{start,end,text}] — đã bỏ câu rác (text_junk.is_junk_text).

    fix 2026-11-13 agentM: sự cố 60892cc63cb7_p01 i=1039 text="None." (AED giải mã đoạn ồn 0.48 s
    thành token «none», FireRedPunc viết hoa + thêm «.») → dịch simple không soát được → park mãi.
    Câu rác (None/null/nan ± dấu, hoặc không có CJK/chữ/số) bị bỏ ở đây; số câu bỏ in ra log.
    """
    rows = _parse_firered_result_raw(result)
    try:
        from engines.text_junk import is_junk_text
    except Exception:  # noqa: BLE001
        return rows
    keep = [r for r in rows if not is_junk_text(r.get("text"))]
    if len(keep) != len(rows):
        dropped = [r.get("text") for r in rows if is_junk_text(r.get("text"))]
        print(f"[{ENGINE}] bỏ {len(rows) - len(keep)} câu rác (None/không chữ): {dropped[:8]}", flush=True)
    return keep


def _parse_firered_result_raw(result: dict) -> list[dict]:
    """FireRedAsr2System.process → [{start,end,text}] (chưa lọc rác)."""
    rows: list[dict] = []
    sents = result.get("sentences") or []
    if isinstance(sents, list) and sents:
        for s in sents:
            if not isinstance(s, dict):
                continue
            text = (s.get("text") or "").strip()
            if not text:
                continue
            st = float(s.get("start_ms", 0) or 0) / 1000.0
            en = float(s.get("end_ms", st) or st) / 1000.0
            rows.append({"start": round(st, 3), "end": round(max(en, st + 0.2), 3), "text": text})
        return rows
    # fallback: words → gom theo dấu / khoảng nghỉ
    words = result.get("words") or []
    if words:
        buf, st0, en0 = [], None, None
        for w in words:
            if not isinstance(w, dict):
                continue
            tok = (w.get("text") or "").strip()
            a = float(w.get("start_ms", 0) or 0) / 1000.0
            b = float(w.get("end_ms", a) or a) / 1000.0
            if not tok:
                continue
            if st0 is None:
                st0 = a
            buf.append(tok)
            en0 = b
            if tok.endswith(("。", "！", "？", ".", "!", "?")) or "。" in tok:
                piece = "".join(buf).strip()
                if piece:
                    rows.append({"start": round(st0, 3), "end": round(en0 or st0, 3), "text": piece})
                buf, st0, en0 = [], None, None
        if buf and st0 is not None:
            rows.append({"start": round(st0, 3), "end": round(en0 or st0, 3),
                         "text": "".join(buf).strip()})
        return rows
    text = (result.get("text") or "").strip()
    if text:
        dur = float(result.get("dur_s") or 1.0)
        rows.append({"start": 0.0, "end": round(dur, 3), "text": text})
    return rows


# ─────────────── Chia slice tại KHOẢNG LẶNG + đệm chồng lấn (fix 2026-11-13) ───────────────
# Trước: cắt cứng tại k×FIRERED_SLICE_SEC bằng ffmpeg -ss/-to -> câu vắt biên bị cắt đôi/lặp/mất.
# Nay: điểm cắt = khoảng lặng gần mốc nhất (RMS khung 20 ms, chỉ đọc cửa sổ ±search quanh mốc),
# mỗi slice thêm đệm 2 đầu, sau ASR chỉ giữ câu có TÂM nằm trong lõi slice. Cắt theo số mẫu
# (chép byte PCM, không re-encode) -> offset = start_frame / rate, chính xác tuyệt đối.

_SIL_FRAME_SEC = 0.02


def _num_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        try:
            raw = cfg(name, None)
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def slice_cfg() -> dict:
    """FIRERED_SLICE_SEC=0 -> không chia (một lượt cả file)."""
    return {
        "slice_sec": max(0.0, _num_env("FIRERED_SLICE_SEC", 1200.0)),        # 20 phút (OOM)
        "search_sec": max(0.0, _num_env("FIRERED_CUT_SEARCH_SEC", 60.0)),    # ±quanh mốc
        "pad_sec": max(0.0, _num_env("FIRERED_SLICE_PAD_SEC", 2.0)),         # đệm biên có lặng
        "pad_nosil_sec": max(0.0, _num_env("FIRERED_SLICE_PAD_NOSIL_SEC", 10.0)),  # biên không lặng
        "min_sil_sec": max(0.02, _num_env("FIRERED_MIN_SILENCE_SEC", 0.3)),
        "sil_db": _num_env("FIRERED_SILENCE_DB", -40.0),
    }


def _is_pcm16_mono16k(info) -> bool:
    fmt = info.fmt_raw
    tag = int.from_bytes(fmt[0:2], "little") if len(fmt) >= 2 else 0
    bits = int.from_bytes(fmt[14:16], "little") if len(fmt) >= 16 else 0
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = int.from_bytes(fmt[24:26], "little")
    return tag == 1 and bits == 16 and info.channels == 1 and info.rate == 16000


def _read_i16(info, f, start: int, count: int):
    import numpy as np
    start = max(0, int(start))
    count = max(0, min(int(count), info.n_frames - start))
    f.seek(info.data_offset + start * info.block_align)
    buf = f.read(count * info.block_align)
    return np.frombuffer(buf[: (len(buf) // 2) * 2], dtype="<i2")


def find_cut_frame(info, f, target_sec: float, lo_sec: float, hi_sec: float,
                   min_sil_sec: float = 0.3, sil_db: float = -40.0) -> tuple[int, float]:
    """Điểm cắt (frame) trong [lo_sec, hi_sec] gần target_sec.

    Trả (cut_frame, sil_len_sec). sil_len_sec > 0: cắt ở GIỮA khoảng lặng (ưu tiên dài + gần);
    = 0: không có khoảng lặng ≥ min_sil -> điểm năng lượng thấp nhất (caller bật đệm chồng lấn lớn).
    Chỉ đọc đúng cửa sổ [lo, hi] (không nạp cả file).
    """
    import numpy as np
    rate = info.rate
    a = max(0, int(round(lo_sec * rate)))
    b = min(info.n_frames, int(round(hi_sec * rate)))
    tgt = int(round(target_sec * rate))
    hop = max(1, int(round(_SIL_FRAME_SEC * rate)))
    if b - a < 4 * hop:
        return min(max(tgt, a), max(a, b)), 0.0
    x = _read_i16(info, f, a, b - a).astype(np.float32) / 32768.0
    nfr = len(x) // hop
    if nfr < 4:
        return min(max(tgt, a), max(a, b)), 0.0
    fr = x[: nfr * hop].reshape(nfr, hop)
    db = 20.0 * np.log10(np.sqrt(np.mean(fr * fr, axis=1)) + 1e-10)
    # Ngưỡng: tuyệt đối sil_db; nền ồn cao (vocals còn bleed) -> nền+6 dB, nhưng luôn
    # thấp hơn trung vị cửa sổ ≥10 dB và ≤ -25 dBFS (audio nói liên tục => không "lặng").
    thr = max(sil_db, float(np.percentile(db, 5)) + 6.0)
    thr = min(thr, float(np.median(db)) - 10.0, -25.0)
    quiet = db < thr
    min_fr = max(1, int(round(min_sil_sec / _SIL_FRAME_SEC)))
    radius = max(hop, max(tgt - a, b - tgt))
    best = None
    i = 0
    while i < nfr:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < nfr and quiet[j]:
            j += 1
        if j - i >= min_fr:
            mid = a + ((i + j) * hop) // 2
            ln = (j - i) * _SIL_FRAME_SEC
            score = abs(mid - tgt) / radius - 0.25 * min(ln, 2.0)   # gần + dài (≤2 s)
            if best is None or score < best[0]:
                best = (score, mid, ln)
        i = j
    if best is not None:
        return int(best[1]), round(float(best[2]), 3)
    # Không có khoảng lặng: năng lượng thấp nhất (trung bình trượt 0.2 s), hơi ưu tiên gần mốc
    k = max(1, int(round(0.2 / _SIL_FRAME_SEC)))
    sm = np.convolve(db, np.ones(k) / k, mode="same")
    dist = np.abs((np.arange(nfr) * hop + hop // 2 + a) - tgt) / radius
    idx = int(np.argmin(sm + 3.0 * dist))
    return int(a + idx * hop + hop // 2), 0.0


def plan_slices(wav_path: str | Path, conf: dict | None = None) -> list[dict]:
    """[{start,end,core0,core1,sil_left,sil_right}] theo FRAME. Lõi phủ kín [0,n) không chồng."""
    from engines.separate import wav_info
    conf = dict(slice_cfg() if conf is None else conf)
    info = wav_info(wav_path)
    n, rate = info.n_frames, info.rate
    total = n / float(rate or 1)
    slice_sec = float(conf.get("slice_sec") or 0.0)
    if n <= 0 or slice_sec <= 0 or total <= slice_sec:
        return [{"start": 0, "end": n, "core0": 0, "core1": n, "sil_left": None, "sil_right": None}]
    import math
    search = min(float(conf.get("search_sec", 60.0)), 0.4 * slice_sec)
    n_slice = max(1, int(math.ceil(total / slice_sec)))
    if n_slice > 1 and total - (n_slice - 1) * slice_sec < max(search, 1.0):
        n_slice -= 1   # đuôi quá ngắn -> gộp vào slice cuối
    cuts: list[tuple[int, float]] = []
    prev = 0.0
    with open(info.path, "rb") as f:
        for k in range(1, n_slice):
            target = k * slice_sec
            lo = max(target - search, prev + 1.0)
            hi = min(target + search, total - 1.0)
            if hi <= lo:
                c, sil = int(round(target * rate)), 0.0
            else:
                c, sil = find_cut_frame(info, f, target, lo, hi,
                                        float(conf.get("min_sil_sec", 0.3)),
                                        float(conf.get("sil_db", -40.0)))
            cuts.append((c, sil))
            prev = c / float(rate)
    bounds = [0, *[c for c, _ in cuts], n]
    sils = [None, *[s for _, s in cuts], None]
    pad = int(round(float(conf.get("pad_sec", 2.0)) * rate))
    pad_ns = int(round(max(float(conf.get("pad_nosil_sec", 10.0)),
                           float(conf.get("pad_sec", 2.0))) * rate))
    out = []
    for i in range(len(bounds) - 1):
        sl, sr = sils[i], sils[i + 1]
        pl = 0 if sl is None else (pad if sl > 0 else pad_ns)
        pr = 0 if sr is None else (pad if sr > 0 else pad_ns)
        out.append({"start": max(0, bounds[i] - pl), "end": min(n, bounds[i + 1] + pr),
                    "core0": bounds[i], "core1": bounds[i + 1],
                    "sil_left": sl, "sil_right": sr})
    return out


def describe_plan(plan: list[dict], rate: int) -> str:
    if len(plan) <= 1:
        return "[firered] 1 slice (cả file một lượt)"
    parts = []
    for p in plan[1:]:
        t = p["core0"] / float(rate)
        sil = p["sil_left"] or 0.0
        parts.append(f"{t:.2f}s (lặng {sil:.2f}s)" if sil > 0
                     else f"{t:.2f}s (KHÔNG lặng -> chồng lấn đệm lớn)")
    return f"[firered] {len(plan)} slice, cắt tại khoảng lặng: " + ", ".join(parts)


def transcribe_sliced(process_fn, wav: str | Path, tmp_dir: str | Path,
                      conf: dict | None = None, on_slice=None) -> list[dict]:
    """Chạy process_fn(path, uttid) -> dict FireRed trên từng slice; trả rows tuyệt đối.

    Câu giữ lại khi TÂM (start+end)/2 thuộc lõi [core0, core1) của slice -> không trùng,
    không mất câu vắt biên (câu đó nằm trọn trong phần đệm của ít nhất một slice).
    """
    from engines.separate import wav_info, write_wav_slice
    wav = Path(wav)
    tmp_dir = Path(tmp_dir)
    info = wav_info(wav)
    src = wav
    if not _is_pcm16_mono16k(info):
        import subprocess
        src = tmp_dir / "asr16k.wav"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav),
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(src)], check=True)
        info = wav_info(src)
    rate = info.rate
    plan = plan_slices(src, conf)
    print(describe_plan(plan, rate), flush=True)
    rows: list[dict] = []
    last = len(plan) - 1
    for si, p in enumerate(plan):
        if len(plan) == 1:
            clip = src
        else:
            clip = tmp_dir / f"slice_{si:03d}.wav"
            write_wav_slice(src, clip, p["start"], p["end"] - p["start"])
        result = process_fn(str(clip), f"slice{si}")
        if not isinstance(result, dict):
            result = result[0] if result else {}
        off = p["start"] / float(rate)
        c0 = p["core0"] / float(rate)
        c1 = p["core1"] / float(rate)
        for r in _parse_firered_result(result or {}):
            st = float(r["start"]) + off
            en = float(r["end"]) + off
            mid = (st + en) / 2.0
            if si > 0 and mid < c0:
                continue
            if si < last and mid >= c1:
                continue
            rows.append({"start": round(st, 3), "end": round(en, 3), "text": r["text"]})
        if clip != src:
            try:
                clip.unlink()
            except OSError:
                pass
        if on_slice:
            on_slice(si, len(plan), rows, c1)
    rows.sort(key=lambda r: (r["start"], r["end"]))
    return rows


def run_asr_firered(
    work_dir: str | Path,
    device: str = "auto",
    force: bool = False,
) -> dict:
    work_dir = Path(work_dir)
    m = Manifest(work_dir)
    root, models = _roots()
    _ensure_firered_importable(root)

    need = {
        "FireRedASR2-AED": models / "FireRedASR2-AED",
        "FireRedVAD": models / "FireRedVAD",
        "FireRedLID": models / "FireRedLID",
        "FireRedPunc": models / "FireRedPunc",
    }
    missing = [k for k, p in need.items() if not p.exists()]
    if missing:
        raise RuntimeError(
            f"Thiếu model FireRed: {', '.join(missing)} trong {models}. "
            "Chạy scripts/install_firered_asr.bat"
        )

    # Skip theo FILE (không chỉ is_done) — tránh start_job mark_running làm chạy lại
    # ASR đè lên TTS đang đọc.
    asr_file = work_dir / "asr.json"
    if not force and asr_file.exists():
        st = m.stage(STAGE) or {}
        if not m.is_done(STAGE):
            try:
                n = len(json.loads(asr_file.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                n = st.get("n_segments") or 0
            m.mark_done(
                STAGE,
                asr_path=str(st.get("asr_path") or asr_file),
                audio_path=st.get("audio_path"),
                n_segments=n,
                language=st.get("language") or "zh",
                model=st.get("model") or MODEL_ID,
                engine=st.get("engine") or ENGINE,
            )
            st = m.stage(STAGE) or {}
        print(f"[skip] ASR đã xong: {st.get('asr_path') or asr_file}")
        return st

    src = resolve_asr_source(work_dir, force_demucs=force, device=device if device != "auto" else "cuda")

    asr_path = work_dir / "asr.json"
    wav = work_dir / "audio.wav"
    rows: list[dict] = []
    total = 0.0

    if force:
        try:
            asr_path.unlink(missing_ok=True)
        except OSError:
            pass

    m.mark_running(STAGE)
    free_gpu_vram("trước ASR FireRed")
    use_gpu = True
    if device and device != "auto" and "cpu" in device.lower():
        use_gpu = False

    try:
        st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
        st.update({"engine": ENGINE, "model": MODEL_ID, "cur_text": "tách audio…",
                   "updated_at": time.time(), "status": "running", "progress": 2})
        m.data["stages"][STAGE] = st
        js.persist(m)

        if ensure_audio_wav(src, wav, force=force):
            print(f"[{ENGINE}] Đã tách audio 16k mono từ file tiếng ({Path(src).name})")
        total = _wav_dur(wav)

        # RTX 30xx+: use_half → BF16 (~1.4× nhanh, VRAM ~½ vs FP32; CER giữ — bench 2026-11-10)
        use_half = bool(use_gpu) and (
            str(os.getenv("FIRERED_USE_HALF", cfg("FIRERED_USE_HALF", "1") or "1")).strip().lower()
            not in ("0", "false", "no", "off")
        )
        print(f"[{ENGINE}] Nạp system AED+VAD+LID+Punc · gpu={use_gpu} half={use_half}…")
        st.update({"cur_text": f"nạp {ENGINE}…", "progress": 10, "updated_at": time.time(),
                   "total_sec": total})
        js.persist(m)

        from fireredasr2s import FireRedAsr2System, FireRedAsr2SystemConfig
        from fireredasr2s.fireredasr2 import FireRedAsr2Config
        from fireredasr2s.fireredlid import FireRedLidConfig
        from fireredasr2s.fireredpunc import FireRedPuncConfig
        from fireredasr2s.fireredvad import FireRedVadConfig

        vad_dir = need["FireRedVAD"] / "VAD"
        if not vad_dir.exists():
            # một số bản gói VAD trực tiếp dưới FireRedVAD/
            vad_dir = need["FireRedVAD"]

        vad_config = FireRedVadConfig(
            use_gpu=False,
            smooth_window_size=5,
            speech_threshold=0.4,
            min_speech_frame=20,
            max_speech_frame=2000,
            min_silence_frame=20,
            merge_silence_frame=0,
            extend_speech_frame=0,
            chunk_max_frame=30000,
        )
        lid_config = FireRedLidConfig(use_gpu=use_gpu, use_half=use_half)
        asr_config = FireRedAsr2Config(
            use_gpu=use_gpu,
            use_half=use_half,
            beam_size=3,
            nbest=1,
            decode_max_len=0,
            softmax_smoothing=1.25,
            aed_length_penalty=0.6,
            eos_penalty=1.0,
            return_timestamp=True,
        )
        punc_config = FireRedPuncConfig(use_gpu=use_gpu)

        asr_system_config = FireRedAsr2SystemConfig(
            str(vad_dir),
            str(need["FireRedLID"]),
            "aed", str(need["FireRedASR2-AED"]),
            str(need["FireRedPunc"]),
            vad_config, lid_config, asr_config, punc_config,
            enable_vad=1, enable_lid=1, enable_punc=1,
        )
        asr_system = FireRedAsr2System(asr_system_config)

        st.update({"cur_text": "đang nhận diện (FireRedASR2)…", "progress": 20,
                   "updated_at": time.time()})
        js.persist(m)
        print(f"[{ENGINE}] process · audio {total:.0f}s...")

        # Video rất dài: chia slice tránh OOM — cắt tại KHOẢNG LẶNG gần mốc k×FIRERED_SLICE_SEC,
        # đệm chồng lấn + lọc theo tâm câu (fix 2026-11-13). FIRERED_SLICE_SEC=0 -> cả file.
        import shutil
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="firered_"))

        def _on_slice(si, n_slice, cur_rows, en0):
            pct = 20 + int(70 * (si + 1) / n_slice)
            st.update({
                "progress": min(pct, 95),
                "cur_text": f"ASR slice {si+1}/{n_slice} · {len(cur_rows)} câu",
                "n_segments": len(cur_rows),
                "done_sec": float(en0),
                "total_sec": total,
                "updated_at": time.time(),
                "status": "running",
                "engine": ENGINE, "asr_engine": ENGINE, "model": MODEL_ID,
            })
            m.data["stages"][STAGE] = st
            js.persist(m)
            rows[:] = list(cur_rows)   # _salvage_asr giữ được phần đã xong nếu lỗi giữa chừng
            print(f"[{ENGINE}] slice {si+1}/{n_slice} · {len(cur_rows)} câu · @{en0:.0f}s",
                  flush=True)

        try:
            rows = transcribe_sliced(asr_system.process, wav, tmp_dir, on_slice=_on_slice)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        rows = [{"i": i, "start": r["start"], "end": r["end"], "text": r["text"]}
                for i, r in enumerate(rows)]
        max_zh, _, max_dur = sub_split_cfg()
        rows = split_long_segments(rows, max_chars=max_zh, key="text", max_dur=max_dur)
        for i, r in enumerate(rows):
            r["i"] = i
        try:
            from engines.asr.asr_spam import sanitize_asr_rows
            rows, spam_info = sanitize_asr_rows(
                rows, work_dir=work_dir, source_path=asr_path, heal=True)
            if spam_info.get("n_cleaned") or spam_info.get("n_healed"):
                print(f"[{ENGINE}] spam: cleaned={spam_info['n_cleaned']} "
                      f"healed={spam_info['n_healed']}", flush=True)
        except Exception as _se:  # noqa: BLE001
            print(f"[{ENGINE}] asr_spam skip: {_se}", flush=True)
        asr_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        (work_dir / "sub.zh.srt").write_text(to_srt(rows, key="text"), encoding="utf-8")

    except Exception as e:  # noqa: BLE001
        _salvage_asr(m, asr_path, rows, wav if wav.exists() else None, total, err=e)
        raise

    result = m.mark_done(
        STAGE, asr_path=str(asr_path), audio_path=str(wav),
        n_segments=len(rows), language="zh", model=MODEL_ID, engine=ENGINE,
    )
    print(f"[ok] {ENGINE}: {len(rows)} câu -> {asr_path}")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="ASR FireRedASR2-AED (system).")
    ap.add_argument("work_dir")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    run_asr_firered(args.work_dir, device=args.device, force=args.force)
    os._exit(0)


if __name__ == "__main__":
    main()
