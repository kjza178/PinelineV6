"""
paraformer_zh.py — STAGE ASR bằng FunASR Paraformer-zh (timestamp tốt cho phụ đề/TTS).

Model : paraformer-zh (+ fsmn-vad + ct-punc)
Đầu vào : work/<job>/source.mp4
Đầu ra  : work/<job>/audio.wav, work/<job>/asr.json  ({i, start, end, text})
          work/<job>/sub.zh.srt

Sidecar (--sidecar TAG, mặc định khuyến nghị: paraformer_zh):
  asr.<tag>.json
  asr.<tag>.chars.json   — [{ch, start, end}, ...] cho hybrid Nano+Para
  sub.zh.<tag>.srt
  (không đụng asr.json / manifest ASR chính)

Chạy tay:
    python paraformer_zh.py work/<job>
    python paraformer_zh.py work/<job> --force
    python paraformer_zh.py work/<job> --sidecar paraformer_zh --device cuda:0
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

from engines.asr.asr import audio_wav_stale, extract_audio, ensure_audio_wav, resolve_asr_source, _salvage_asr
from engines.asr.asr_align_nano_para import extract_char_timeline, chars_from_segments_spread
from engines.asr.fun_asr_nano import _parse_funasr_result, _pick_device, _wav_dur, _ms_to_s
from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
from engines.vram import free_gpu_vram
from engines.subs import to_srt, split_long_segments, sub_split_cfg

STAGE = "asr"
ENGINE = "paraformer_zh"
MODEL_ID = "paraformer-zh"
VAD_ID = "fsmn-vad"
PUNC_ID = "ct-punc"


def run_asr_paraformer(
    work_dir: str | Path,
    device: str = "auto",
    force: bool = False,
    hub: str = "ms",
    sidecar: str | None = None,
    slice_mode: bool = False,
) -> dict:
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
    chars_path = work_dir / (f"asr.{tag}.chars.json" if is_side else "asr.chars.json")
    prog_path = (work_dir / f"asr.{tag}.progress.json") if is_side else None
    wav = work_dir / "audio.wav"
    rows: list[dict] = []
    chars: list[dict] = []
    total = 0.0

    if force:
        asr_path.unlink(missing_ok=True)
        if is_side:
            srt_path.unlink(missing_ok=True)
            chars_path.unlink(missing_ok=True)

    if not is_side:
        m.mark_running(STAGE)
    free_gpu_vram("trước ASR Paraformer")
    try:
        def _write_prog(**fields):
            payload = {
                "engine": ENGINE, "model": MODEL_ID, "status": "running",
                "asr_path": str(asr_path), "srt_path": str(srt_path),
                "chars_path": str(chars_path), "updated_at": time.time(), **fields,
            }
            if is_side and prog_path is not None:
                prog_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
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
            print(f"[{ENGINE}] Đã tách audio 16k mono từ file tiếng ({Path(src).name})")
        else:
            print(f"[{ENGINE}] Dùng lại audio.wav có sẵn")
        total = _wav_dur(wav)

        dev = _pick_device(device)
        print(f"[{ENGINE}] Nạp {MODEL_ID} + {VAD_ID} + {PUNC_ID} trên {dev}"
              + (f" sidecar={tag}" if is_side else "") + "...")
        _write_prog(cur_text=f"nạp {ENGINE}…", progress=8, total_sec=total)

        from funasr import AutoModel
        from engines.asr.asr_vad_utils import (
            DEFAULT_OVERLAP_S, DEFAULT_PAD_S, pad_ranges, parse_vad_ranges,
            shift_char_timeline, vad_kwargs_from_env,
        )

        _vad_kw = vad_kwargs_from_env()
        print(f"[{ENGINE}] vad_kwargs={_vad_kw}", flush=True)
        kw = dict(
            model=MODEL_ID if hub == "ms" else "funasr/paraformer-zh",
            vad_model=VAD_ID if hub == "ms" else "funasr/fsmn-vad",
            vad_kwargs=_vad_kw,
            punc_model=PUNC_ID if hub == "ms" else "funasr/ct-punc",
            device=dev,
            disable_update=True,
        )
        if hub == "hf":
            kw["hub"] = "hf"
        model = AutoModel(**kw)

        _write_prog(cur_text="đang nhận diện (Paraformer)…", progress=15, total_sec=total)
        print(f"[{ENGINE}] generate · audio {total:.0f}s..."
              + (f" → {asr_path.name}" if is_side else ""), flush=True)

        res = model.generate(
            input=str(wav),
            batch_size_s=300,
            batch_size_threshold_s=60,
            merge_vad=False,
        )
        _write_prog(cur_text="parse + char-ts…", progress=70, total_sec=total)

        chars = extract_char_timeline(res)
        segs = _parse_funasr_result(res, audio_dur=total)
        rows = [{"i": i, "start": s["start"], "end": s["end"], "text": s["text"]}
                for i, s in enumerate(segs)]

        if slice_mode:
            print(f"[{ENGINE}] slice-mode pad={DEFAULT_PAD_S}s overlap={DEFAULT_OVERLAP_S}s…",
                  flush=True)
            vad_only = AutoModel(
                model=VAD_ID if hub == "ms" else "funasr/fsmn-vad",
                device=dev, disable_update=True, vad_kwargs=_vad_kw,
                **({"hub": "hf"} if hub == "hf" else {}),
            )
            # ASR-only (không VAD) trên từng slice đã pad
            asr_only_kw = dict(
                model=MODEL_ID if hub == "ms" else "funasr/paraformer-zh",
                punc_model=PUNC_ID if hub == "ms" else "funasr/ct-punc",
                device=dev,
                disable_update=True,
            )
            if hub == "hf":
                asr_only_kw["hub"] = "hf"
            asr_only = AutoModel(**asr_only_kw)

            vad_res = vad_only.generate(input=str(wav), **_vad_kw)
            ranges = parse_vad_ranges(vad_res, _ms_to_s)
            slices = pad_ranges(ranges, total, pad_s=DEFAULT_PAD_S, overlap_s=DEFAULT_OVERLAP_S)
            import soundfile as sf
            import tempfile
            chars2: list[dict] = []
            segs2: list[dict] = []
            with sf.SoundFile(str(wav), "r") as f:
                sr = int(f.samplerate)
                nsl = len(slices)
                for i, sl in enumerate(slices):
                    ss, ee = sl["slice_start"], sl["slice_end"]
                    a0, a1 = int(ss * sr), int(ee * sr)
                    f.seek(max(a0, 0))
                    clip = f.read(max(a1 - a0, 0), dtype="float32")
                    if clip is None or len(clip) < 160:
                        continue
                    if getattr(clip, "ndim", 1) > 1:
                        clip = clip.mean(axis=1)
                    tp = None
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                            tp = tmp.name
                        sf.write(tp, clip, sr)
                        r1 = asr_only.generate(input=tp, batch_size_s=60)
                    finally:
                        if tp:
                            Path(tp).unlink(missing_ok=True)
                    ch = shift_char_timeline(
                        extract_char_timeline(r1),
                        offset_s=ss,
                        keep_start=sl["vad_start"],
                        keep_end=sl["vad_end"],
                    )
                    chars2.extend(ch)
                    for s in _parse_funasr_result(r1, audio_dur=ee - ss):
                        segs2.append({
                            "start": round(s["start"] + ss, 3),
                            "end": round(s["end"] + ss, 3),
                            "text": s["text"],
                        })
                    if (i + 1) % 30 == 0 or i + 1 == nsl:
                        pct = 70 + int(20 * (i + 1) / max(nsl, 1))
                        _write_prog(cur_text=f"slice {i+1}/{nsl}", progress=min(pct, 92),
                                    total_sec=total, n_segments=len(segs2))
                        print(f"[{ENGINE}] slice {i+1}/{nsl} · chars={len(chars2)}", flush=True)
            if chars2:
                chars = sorted(chars2, key=lambda c: c["start"])
            if segs2:
                segs = sorted(segs2, key=lambda s: s["start"])
                rows = [{"i": i, "start": s["start"], "end": s["end"], "text": s["text"]}
                        for i, s in enumerate(segs)]

        max_zh, _, max_dur = sub_split_cfg()
        rows = split_long_segments(rows, max_chars=max_zh, key="text", max_dur=max_dur)
        if not chars:
            chars = chars_from_segments_spread(rows)
            print(f"[{ENGINE}] char-ts thô (spread): {len(chars)}", flush=True)
        else:
            print(f"[{ENGINE}] char-ts: {len(chars)}", flush=True)

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
        chars_path.write_text(json.dumps(chars, ensure_ascii=False), encoding="utf-8")
        srt_path.write_text(to_srt(rows, key="text"), encoding="utf-8")

    except Exception as e:  # noqa: BLE001
        if is_side:
            if rows:
                asr_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            if chars:
                chars_path.write_text(json.dumps(chars, ensure_ascii=False), encoding="utf-8")
            if prog_path is not None:
                prog_path.write_text(json.dumps({
                    "status": "failed", "error": str(e), "n_segments": len(rows),
                    "updated_at": time.time(),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            _salvage_asr(m, asr_path, rows, wav if wav.exists() else None, total, err=e)
        raise

    if is_side:
        result = {
            "status": "done", "engine": ENGINE, "model": MODEL_ID,
            "asr_path": str(asr_path), "srt_path": str(srt_path),
            "chars_path": str(chars_path), "n_segments": len(rows),
            "n_chars": len(chars), "language": "zh", "sidecar": tag,
        }
        if prog_path is not None:
            prog_path.write_text(json.dumps({**result, "progress": 100,
                                             "updated_at": time.time()},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        result = m.mark_done(
            STAGE, asr_path=str(asr_path), audio_path=str(wav),
            n_segments=len(rows), language="zh", model=MODEL_ID, engine=ENGINE,
            chars_path=str(chars_path), n_chars=len(chars),
        )
    print(f"[ok] {ENGINE}: {len(rows)} câu, {len(chars)} chars -> {asr_path}")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="ASR Paraformer-zh (timestamp tốt).")
    ap.add_argument("work_dir")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--hub", default="ms", choices=("ms", "hf"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sidecar", default=None,
                    help="Ghi asr.<tag>.json + .chars.json + sub.zh.<tag>.srt")
    ap.add_argument("--slice", action="store_true",
                    help="2-pass VAD+pad/overlap rồi ASR từng slice (chậm hơn, ranh giới tốt hơn)")
    args = ap.parse_args(argv)
    run_asr_paraformer(args.work_dir, device=args.device, force=args.force,
                       hub=args.hub, sidecar=args.sidecar, slice_mode=args.slice)
    os._exit(0)


if __name__ == "__main__":
    main()
