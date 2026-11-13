"""
fun_asr_nano.py — STAGE ASR bằng Fun-ASR-Nano (chất lượng tiếng Trung cao nhất).

Model : FunAudioLLM/Fun-ASR-Nano-2512 (+ fsmn-vad cắt đoạn dài)
Đầu vào : work/<job>/source.mp4
Đầu ra  : work/<job>/audio.wav, work/<job>/asr.json  ({i, start, end, text})
          work/<job>/sub.zh.srt

Chạy tay:
    python fun_asr_nano.py work/<job>
    python fun_asr_nano.py work/<job> --force
    python fun_asr_nano.py work/<job> --device cuda:0

Yêu cầu (KHÔNG pip install funasr trần — sẽ kéo torch CPU):
    install_funasr_safe.bat
    hoặc: pip install funasr modelscope tiktoken ... rồi KHÓA lại
         torch==2.11.0+cu128 (index https://download.pytorch.org/whl/cu128)

Ghi chú timestamp: Nano dùng mốc đoạn VAD (độ chính xác câu). Cần timestamp
ký tự siêu mịn -> dùng paraformer_zh.py.
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
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from engines.asr.asr import ensure_audio_wav, resolve_asr_source, _salvage_asr
from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
from engines.vram import free_gpu_vram
from engines.subs import to_srt, split_text_by_punct, split_long_segments, sub_split_cfg, _plain_len

STAGE = "asr"
ENGINE = "fun_asr_nano"
MODEL_ID = "FunAudioLLM/Fun-ASR-Nano-2512"
VAD_ID = "fsmn-vad"


def _pick_device(device: str) -> str:
    if device and device != "auto":
        return device
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda:0"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def _wav_dur(wav: Path) -> float:
    try:
        import wave
        with wave.open(str(wav), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 16000)
    except Exception:  # noqa: BLE001
        try:
            import soundfile as sf
            return float(sf.info(str(wav)).duration)
        except Exception:  # noqa: BLE001
            return 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _ms_to_s(v) -> float:
    """FunASR timestamp luôn là milliseconds."""
    try:
        return round(float(v) / 1000.0, 3)
    except (TypeError, ValueError):
        return 0.0


def _split_text_by_punct(text: str) -> list[str]:
    """Tách câu theo dấu câu (mỏng: gọi subs.split_text_by_punct)."""
    max_zh, _, _ = sub_split_cfg()
    return split_text_by_punct(text, max_chars=max_zh, weak=True)


def _spread_sentences(text: str, start: float, end: float) -> list[dict]:
    """Không có timestamp ký tự -> chia thời gian theo độ dài chữ."""
    sents = _split_text_by_punct(text)
    if not sents:
        return []
    if len(sents) == 1:
        return [{"start": round(start, 3), "end": max(round(end, 3), round(start, 3) + 0.3),
                 "text": sents[0]}]
    weights = [max(len(s.replace(" ", "")), 1) for s in sents]
    total_w = float(sum(weights)) or 1.0
    dur = max(end - start, 0.3 * len(sents))
    out: list[dict] = []
    cur = start
    for i, (s, w) in enumerate(zip(sents, weights)):
        piece = dur * (w / total_w)
        en = end if i == len(sents) - 1 else cur + piece
        out.append({"start": round(cur, 3), "end": round(max(en, cur + 0.2), 3), "text": s})
        cur = en
    return out


def _parse_funasr_result(res, audio_dur: float = 0.0) -> list[dict]:
    """Chuẩn hoá output FunASR -> [{start, end, text}, ...]."""
    out: list[dict] = []
    if not res:
        return out
    items = res if isinstance(res, list) else [res]
    for item in items:
        if not isinstance(item, dict):
            continue
        # Ưu tiên sentence_info (có start/end theo câu)
        sents = item.get("sentence_info") or item.get("sentence_list")
        if isinstance(sents, list) and sents:
            for s in sents:
                if not isinstance(s, dict):
                    continue
                text = (s.get("text") or s.get("sentence") or "").strip()
                if not text:
                    continue
                st = s.get("start", s.get("start_time", 0))
                en = s.get("end", s.get("end_time", st))
                if "timestamp" in s and isinstance(s["timestamp"], list) and s["timestamp"]:
                    ts = s["timestamp"]
                    st = ts[0][0] if isinstance(ts[0], (list, tuple)) else st
                    en = ts[-1][1] if isinstance(ts[-1], (list, tuple)) else en
                # sentence quá dài không timestamp ký tự -> tách dấu câu
                st_s, en_s = _ms_to_s(st), _ms_to_s(en)
                if len(text) > 40 and ("。" in text or "？" in text or "！" in text):
                    out.extend(_spread_sentences(text, st_s, en_s if en_s > st_s else st_s + 2.0))
                else:
                    out.append({"start": st_s, "end": en_s, "text": text})
            continue

        text = (item.get("text") or "").strip()
        if not text:
            continue
        ts = item.get("timestamp") or item.get("timestamps")
        if isinstance(ts, list) and ts and isinstance(ts[0], (list, tuple)):
            st = ts[0][0]
            en = ts[-1][1]
            parts = _split_by_punct(text, ts)
            if parts:
                out.extend(parts)
            else:
                # timestamp có nhưng lệch độ dài chữ -> tách câu + map thời gian theo tỉ lệ
                out.extend(_spread_sentences(text, _ms_to_s(st), _ms_to_s(en)))
            continue

        # Fun-ASR-Nano: chỉ có text (CTC timestamp tắt) — tách câu + trải đều
        st = item.get("start", item.get("begin_time", 0))
        en = item.get("end", item.get("end_time", st))
        st_s, en_s = _ms_to_s(st), _ms_to_s(en)
        if en_s <= st_s:
            # không có mốc thời gian hợp lệ
            st_s = 0.0
            en_s = float(audio_dur) if audio_dur > 0 else max(len(text) * 0.18, 1.0)
        if len(text) > 40:
            out.extend(_spread_sentences(text, st_s, en_s))
        else:
            out.append({"start": st_s, "end": en_s, "text": text})
    # sắp + lọc
    out.sort(key=lambda r: r["start"])
    cleaned = []
    for r in out:
        t = (r.get("text") or "").strip()
        if not t:
            continue
        if r["end"] <= r["start"]:
            r["end"] = r["start"] + 0.3
        cleaned.append(r)
    return cleaned


def _split_by_punct(text: str, char_ts: list) -> list[dict]:
    """Gộp timestamp ký tự thành câu theo dấu câu Trung/Việt."""
    # Bỏ khoảng trắng để khớp timestamp (Paraformer đôi khi lệch do space/ITN)
    raw = text.replace(" ", "").replace("\n", "")
    chars = list(raw)
    if not chars or not char_ts:
        return []
    # lệch độ dài: cắt/pad theo min, hoặc map tỉ lệ
    if len(chars) != len(char_ts):
        if abs(len(chars) - len(char_ts)) <= max(3, len(chars) // 20):
            n = min(len(chars), len(char_ts))
            chars, char_ts = chars[:n], char_ts[:n]
        else:
            # không căn được -> trả rỗng để caller dùng spread
            return []
    punct = set("。！？；.!?;")
    rows: list[dict] = []
    buf_ch: list[str] = []
    buf_ts: list = []
    for ch, ts in zip(chars, char_ts):
        buf_ch.append(ch)
        buf_ts.append(ts)
        if ch in punct and buf_ts:
            rows.append({
                "start": _ms_to_s(buf_ts[0][0]),
                "end": _ms_to_s(buf_ts[-1][1]),
                "text": "".join(buf_ch).strip(),
            })
            buf_ch, buf_ts = [], []
    if buf_ch and buf_ts:
        rows.append({
            "start": _ms_to_s(buf_ts[0][0]),
            "end": _ms_to_s(buf_ts[-1][1]),
            "text": "".join(buf_ch).strip(),
        })
    return [r for r in rows if r["text"]]


def _vad_ranges_chunked(
    vad_model,
    wav: Path,
    audio_dur: float,
    vad_kw: dict | None = None,
    chunk_s: float = 480.0,
    overlap_s: float = 2.0,
    on_progress=None,
) -> list[tuple[float, float]]:
    """VAD theo cửa sổ ~chunk_s — tránh treo generate() trên file dài."""
    import soundfile as sf
    from engines.asr.asr_vad_utils import parse_vad_ranges, merge_close_ranges

    vad_kw = dict(vad_kw or {})
    chunk_s = float(os.environ.get("ASR_VAD_CHUNK_S", chunk_s) or chunk_s)
    # audio ngắn: một phát
    if audio_dur <= chunk_s + 30:
        print(f"[fun_asr_nano] VAD 1-shot {audio_dur:.0f}s…", flush=True)
        vad_res = vad_model.generate(input=str(wav), **vad_kw)
        return merge_close_ranges(parse_vad_ranges(vad_res, _ms_to_s))

    ranges: list[tuple[float, float]] = []
    step = max(60.0, chunk_s - overlap_s)
    n_chunks = int((audio_dur + step - 1e-6) // step) + 1
    with sf.SoundFile(str(wav), "r") as f:
        sr = int(f.samplerate)
        for ci, i in enumerate(range(n_chunks)):
            t0 = i * step
            if t0 >= audio_dur:
                break
            t1 = min(audio_dur, t0 + chunk_s)
            a0, a1 = int(t0 * sr), int(t1 * sr)
            f.seek(max(a0, 0))
            clip = f.read(max(a1 - a0, 0), dtype="float32")
            if clip is None or len(clip) < sr // 4:
                continue
            if getattr(clip, "ndim", 1) > 1:
                clip = clip.mean(axis=1)
            print(f"[fun_asr_nano] VAD chunk {ci+1}/{n_chunks} "
                  f"{t0:.0f}-{t1:.0f}s…", flush=True)
            if on_progress:
                pct = 10 + int(5 * (ci + 1) / max(n_chunks, 1))
                on_progress(min(pct, 14), f"VAD chunk {ci+1}/{n_chunks}", 0, t1)
            try:
                vad_res = vad_model.generate(
                    input=[clip], fs=sr, **vad_kw,
                )
            except TypeError:
                # một số bản không nhận fs= trong generate
                import tempfile
                tp = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                        tp = tmp.name
                    sf.write(tp, clip, sr)
                    vad_res = vad_model.generate(input=tp, **vad_kw)
                finally:
                    if tp:
                        Path(tp).unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                print(f"[fun_asr_nano] VAD chunk fail @{t0:.0f}s: {e}", flush=True)
                continue
            for st, en in parse_vad_ranges(vad_res, _ms_to_s):
                # timestamp trong clip → absolute
                abs_st, abs_en = st + t0, en + t0
                # bỏ mép overlap (trừ chunk đầu/cuối) để giảm trùng
                if ci > 0 and abs_en <= t0 + overlap_s * 0.5:
                    continue
                if abs_st < t0:
                    abs_st = t0
                if abs_en > t1:
                    abs_en = t1
                if abs_en - abs_st >= 0.12:
                    ranges.append((abs_st, abs_en))
    ranges = merge_close_ranges(sorted(ranges))
    print(f"[fun_asr_nano] VAD chunked → {len(ranges)} speech ranges", flush=True)
    return ranges


def _vad_slice_asr(
    asr_model,
    vad_model,
    wav: Path,
    language: str = "中文",
    on_progress=None,
    pad_s: float | None = None,
    overlap_s: float | None = None,
    vad_kw: dict | None = None,
) -> list[dict]:
    """VAD cắt đoạn -> ASR từng đoạn có pad/overlap (Fun-ASR-Nano không có CTC ts)."""
    import soundfile as sf
    from engines.asr.asr_vad_utils import (
        DEFAULT_OVERLAP_S, DEFAULT_PAD_S, pad_ranges, parse_vad_ranges,
    )

    pad_s = DEFAULT_PAD_S if pad_s is None else pad_s
    overlap_s = DEFAULT_OVERLAP_S if overlap_s is None else overlap_s

    audio_dur = _wav_dur(wav)
    print(f"[fun_asr_nano] VAD trên {wav} ({audio_dur:.0f}s)…", flush=True)
    if on_progress:
        on_progress(10, "VAD cắt đoạn…", 0, 0)
    # Full-file VAD dễ treo/OOM với audio dài (~1.5h+). Chạy theo chunk.
    ranges = _vad_ranges_chunked(
        vad_model, wav, audio_dur, vad_kw=vad_kw or {}, on_progress=on_progress,
    )
    slices = pad_ranges(ranges, audio_dur, pad_s=pad_s, overlap_s=overlap_s)
    print(f"[fun_asr_nano] VAD: {len(ranges)} đoạn → {len(slices)} slice "
          f"(pad={pad_s}s overlap={overlap_s}s)", flush=True)
    if not slices:
        return []

    with sf.SoundFile(str(wav), "r") as f:
        sr = int(f.samplerate)
        out: list[dict] = []
        n = len(slices)
        for i, sl in enumerate(slices):
            st, en = sl["vad_start"], sl["vad_end"]
            ss, ee = sl["slice_start"], sl["slice_end"]
            a0, a1 = int(ss * sr), int(ee * sr)
            f.seek(max(a0, 0))
            clip = f.read(max(a1 - a0, 0), dtype="float32")
            if clip is None or len(clip) < 160:
                continue
            if getattr(clip, "ndim", 1) > 1:
                clip = clip.mean(axis=1)
            text_asr = ""
            import tempfile
            from pathlib import Path as _P
            tp = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tp = tmp.name
                sf.write(tp, clip, sr)
                res = asr_model.generate(
                    input=tp,
                    cache={},
                    batch_size=1,
                    language=language,
                    itn=True,
                )
            except Exception:  # noqa: BLE001
                continue
            finally:
                if tp:
                    try:
                        _P(tp).unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass
            if res:
                it = res[0] if isinstance(res, list) else res
                if isinstance(it, dict):
                    text_asr = (it.get("text") or "").strip()
            if not text_asr:
                continue

            # Chống Hallucination Loop / Repetition trap của model autoregressive
            import re
            m_loop = re.search(r"([^\s，。！？,!?]{3,20}?)\1{2,}", text_asr)
            if m_loop:
                unit = m_loop.group(1)
                text_asr = re.sub(r"(?:" + re.escape(unit) + r"[，。！？,!?\s]*){2,}", unit + "。", text_asr).strip()

            # Timing theo VAD gốc; pad/overlap chỉ nuôi context ASR
            parts = _spread_sentences(text_asr, st, en) if _plain_len(text_asr) > 18 else [
                {"start": st, "end": en, "text": text_asr}
            ]
            for p in parts:
                p_text = (p.get("text") or "").strip()
                if not p_text:
                    continue
                # Bỏ câu trùng hệt câu liền trước (do overlap hoặc lặp ảo)
                if out and out[-1]["text"] == p_text:
                    continue
                out.append(p)
            if on_progress and (i % 5 == 0 or i + 1 == n):
                pct = 15 + int(80 * (i + 1) / max(n, 1))
                on_progress(min(pct, 95), f"ASR {i+1}/{n} · {len(out)} câu", len(out), en)
            if (i + 1) % 20 == 0 or i + 1 == n:
                print(f"[fun_asr_nano] {i+1}/{n} · {len(out)} câu · @{en:.0f}s", flush=True)
    return out

def run_asr_nano(
    work_dir: str | Path,
    device: str = "auto",
    force: bool = False,
    language: str = "中文",
    hub: str = "hf",
    sidecar: str | None = None,
) -> dict:
    """sidecar: ghi asr.<tag>.json + sub.zh.<tag>.srt, không đụng asr.json / manifest ASR chính."""
    work_dir = Path(work_dir)
    m = Manifest(work_dir)
    tag = (sidecar or "").strip().replace(" ", "_")
    is_side = bool(tag)

    if not is_side and m.is_done(STAGE) and not force:
        st = m.stage(STAGE)
        if st and Path(st.get("asr_path", "")).exists():
            print(f"[skip] ASR đã xong: {st['asr_path']}")
            return st

    src = resolve_asr_source(work_dir, force_demucs=force and not is_side, device=device if device != "auto" else "cuda")

    asr_path = work_dir / (f"asr.{tag}.json" if is_side else "asr.json")
    srt_path = work_dir / (f"sub.zh.{tag}.srt" if is_side else "sub.zh.srt")
    prog_path = (work_dir / f"asr.{tag}.progress.json") if is_side else None
    wav = work_dir / "audio.wav"
    rows: list[dict] = []
    total = 0.0

    if force and not is_side:
        try:
            asr_path.unlink(missing_ok=True)
        except OSError:
            pass
    elif force and is_side:
        asr_path.unlink(missing_ok=True)
        srt_path.unlink(missing_ok=True)

    if not is_side:
        m.mark_running(STAGE)
    free_gpu_vram("trước ASR Fun-ASR-Nano")
    try:
        # Preflight: báo rõ thiếu deps (funasr hay gói lỗi thành "needs torch")
        import sys as _sys
        try:
            import torch as _torch
            print(f"[{ENGINE}] python={_sys.executable} torch={_torch.__version__} "
                  f"cuda={_torch.cuda.is_available()}"
                  + (f" sidecar={tag}" if is_side else ""), flush=True)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"Thiếu PyTorch trong {_sys.executable}: {e}. "
                "Cài: python -m pip install torch torchaudio "
                "--index-url https://download.pytorch.org/whl/cu126"
            ) from e
        try:
            import transformers as _tf  # noqa: F401
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"Fun-ASR-Nano cần transformers: {e}. "
                "Cài: python -m pip install -U transformers"
            ) from e

        def _write_prog(**fields):
            payload = {
                "engine": ENGINE, "model": MODEL_ID, "status": "running",
                "asr_path": str(asr_path), "srt_path": str(srt_path),
                "updated_at": time.time(), **fields,
            }
            if is_side and prog_path is not None:
                prog_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
                # Hybrid FA: đưa % Nano vào stages.asr (UI đọc chỗ này, không đọc sidecar)
                try:
                    from engines.asr.asr_hybrid_progress import mirror_to_asr_stage
                    mirror_to_asr_stage(
                        work_dir, tag=tag,
                        progress=fields.get("progress"),
                        cur_text=fields.get("cur_text"),
                        n_segments=fields.get("n_segments"),
                        done_sec=fields.get("done_sec"),
                        total_sec=fields.get("total_sec"),
                    )
                except Exception:  # noqa: BLE001
                    pass
            else:
                st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
                st.update(payload)
                m.data["stages"][STAGE] = st
                js.persist(m)

        _write_prog(cur_text="tách audio…", progress=2)

        if ensure_audio_wav(src, wav, force=force and not is_side, keep_legacy=is_side):
            print(f"[{ENGINE}] Đã tách audio 16k mono")
        total = _wav_dur(wav)

        dev = _pick_device(device)
        from funasr import AutoModel
        # Nano: CTC timestamp tắt trên bản HF này -> VAD cắt + ASR từng đoạn
        print(f"[{ENGINE}] Nạp VAD {VAD_ID} + ASR {MODEL_ID} trên {dev} (hub={hub})...")
        _write_prog(cur_text=f"nạp {ENGINE}…", progress=8, total_sec=total)
        from engines.asr.asr_vad_utils import vad_kwargs_from_env
        _vad_kw = vad_kwargs_from_env()
        print(f"[{ENGINE}] vad_kwargs={_vad_kw}", flush=True)
        vad = AutoModel(model=VAD_ID, device=dev, hub=hub, disable_update=True)
        asr_m = AutoModel(
            model=MODEL_ID,
            trust_remote_code=True,
            device=dev,
            hub=hub,
            disable_update=True,
        )

        _write_prog(cur_text="đang nhận diện (Fun-ASR-Nano · VAD)…", progress=15, total_sec=total)
        print(f"[{ENGINE}] VAD+ASR lang={language} · audio {total:.0f}s..."
              + (f" → {asr_path.name}" if is_side else ""), flush=True)

        def _prog(pct, text, n_seg, done_sec):
            _write_prog(
                progress=int(pct), cur_text=text, n_segments=n_seg,
                done_sec=float(done_sec or 0), total_sec=total,
                asr_engine=ENGINE,
            )

        segs = _vad_slice_asr(asr_m, vad, wav, language=language, on_progress=_prog, vad_kw=_vad_kw)
        if not segs:
            # fallback 1 phát (không timestamp mịn)
            res = asr_m.generate(
                input=str(wav), cache={}, batch_size=1, language=language, itn=True,
            )
            segs = _parse_funasr_result(res, audio_dur=total)
        rows = [{"i": i, "start": s["start"], "end": s["end"], "text": s["text"]}
                for i, s in enumerate(segs)]
        max_zh, _, max_dur = sub_split_cfg()
        rows = split_long_segments(rows, max_chars=max_zh, key="text", max_dur=max_dur)
        # Chặn tiếng nền (哈哈/啊啊 spam) — rút + vá từ sidecar khác nếu có
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
        srt_path.write_text(to_srt(rows, key="text"), encoding="utf-8")

    except Exception as e:  # noqa: BLE001
        if is_side:
            if rows:
                asr_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
                srt_path.write_text(to_srt(rows, key="text"), encoding="utf-8")
            if prog_path is not None:
                prog_path.write_text(json.dumps({
                    "status": "failed", "error": str(e), "n_segments": len(rows),
                    "asr_path": str(asr_path), "updated_at": time.time(),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            _salvage_asr(m, asr_path, rows, wav if wav.exists() else None, total, err=e)
        raise

    if is_side:
        result = {
            "status": "done", "engine": ENGINE, "model": MODEL_ID,
            "asr_path": str(asr_path), "srt_path": str(srt_path),
            "n_segments": len(rows), "language": "zh", "sidecar": tag,
        }
        if prog_path is not None:
            prog_path.write_text(json.dumps({**result, "progress": 100,
                                             "updated_at": time.time()},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        result = m.mark_done(
            STAGE, asr_path=str(asr_path), audio_path=str(wav),
            n_segments=len(rows), language="zh", model=MODEL_ID, engine=ENGINE,
        )
    print(f"[ok] {ENGINE}: {len(rows)} câu -> {asr_path}")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="ASR Fun-ASR-Nano (tiếng Trung chất lượng cao).")
    ap.add_argument("work_dir")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--language", default="中文", help="中文 / en / ja …")
    ap.add_argument("--hub", default="hf", choices=("hf", "ms"),
                    help="hf=HuggingFace, ms=ModelScope")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sidecar", default=None,
                    help="Ghi riêng asr.<tag>.json + sub.zh.<tag>.srt (không đụng asr.json)")
    args = ap.parse_args(argv)
    run_asr_nano(args.work_dir, device=args.device, force=args.force,
                 language=args.language, hub=args.hub, sidecar=args.sidecar)
    os._exit(0)


if __name__ == "__main__":
    main()
