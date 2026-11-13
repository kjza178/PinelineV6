"""
sensevoice_asr.py — STAGE ASR bằng FunASR SenseVoiceSmall (nhanh, đa ngôn ngữ).

Model : iic/SenseVoiceSmall (+ fsmn-vad)
Đầu vào : work/<job>/source.* hoặc audio.wav có sẵn
Đầu ra  : work/<job>/audio.wav, asr.json, sub.zh.srt

Chạy tay:
    python sensevoice_asr.py work/<job>
    python sensevoice_asr.py work/<job> --force
    python sensevoice_asr.py work/<job> --hub hf   # FunAudioLLM/SenseVoiceSmall

Yêu cầu: funasr đã cài (install_funasr_safe.bat). Không cần pip thêm.
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
import re
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from engines.asr.asr import audio_wav_stale, extract_audio, ensure_audio_wav, resolve_asr_source, _salvage_asr
from engines.asr.fun_asr_nano import _parse_funasr_result, _pick_device, _spread_sentences, _wav_dur
from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
from engines.vram import free_gpu_vram
from engines.subs import to_srt, split_long_segments, sub_split_cfg, _plain_len

STAGE = "asr"
ENGINE = "sensevoice"
MODEL_MS = "iic/SenseVoiceSmall"
MODEL_HF = "FunAudioLLM/SenseVoiceSmall"
VAD_ID = "fsmn-vad"

# SenseVoice nhét tag <|zh|><|NEUTRAL|><|Speech|>… — tẩy trước khi ghi asr.json
_TAG_RE = re.compile(r"<\|[^|>]+\|>")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # misc symbols & pictographs / supplemental
    "\U00002700-\U000027BF"  # dingbats
    "\U0001F600-\U0001F64F"
    "]+",
    flags=re.UNICODE,
)


def _clean_sv_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    try:
        from funasr.utils.postprocess_utils import rich_transcription_postprocess
        t = rich_transcription_postprocess(t)
    except Exception:  # noqa: BLE001
        t = _TAG_RE.sub("", t)
    t = _TAG_RE.sub("", t or "")
    t = _EMOJI_RE.sub("", t)
    return (t or "").strip()


def _post_clean_segs(segs: list[dict]) -> list[dict]:
    out = []
    for s in segs:
        text = _clean_sv_text(s.get("text") or "")
        if not text:
            continue
        row = dict(s)
        row["text"] = text
        out.append(row)
    return out


def run_asr_sensevoice(
    work_dir: str | Path,
    device: str = "auto",
    force: bool = False,
    language: str = "zh",
    hub: str = "ms",
    sidecar: str | None = None,
) -> dict:
    """sidecar: ghi asr.<tag>.json + sub.zh.<tag>.srt, không đụng asr.json / manifest ASR chính."""
    work_dir = Path(work_dir)
    m = Manifest(work_dir)
    model_id = MODEL_HF if hub == "hf" else MODEL_MS
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

    if force:
        asr_path.unlink(missing_ok=True)
        if is_side:
            srt_path.unlink(missing_ok=True)

    if not is_side:
        m.mark_running(STAGE)
    free_gpu_vram("trước ASR SenseVoice")
    try:
        def _write_prog(**fields):
            payload = {
                "engine": ENGINE, "asr_engine": ENGINE, "model": model_id,
                "status": "running", "asr_path": str(asr_path), "srt_path": str(srt_path),
                "updated_at": time.time(), **fields,
            }
            if is_side and prog_path is not None:
                prog_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
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
        print(f"[{ENGINE}] Nạp {model_id} + {VAD_ID} trên {dev} (hub={hub})"
              + (f" sidecar={tag}" if is_side else "") + "...")
        _write_prog(cur_text=f"nạp {ENGINE}…", progress=8, total_sec=total)

        from funasr import AutoModel
        from engines.asr.asr_vad_utils import vad_kwargs_from_env
        _vad_kw = vad_kwargs_from_env()
        # SenseVoice: đoạn VAD ngắn hơn một chút (giữ 30s như trước, trừ khi env ghi đè)
        _vad_kw.setdefault("max_single_segment_time", 30000)
        if not os.environ.get("ASR_VAD_MAX_SINGLE_SEGMENT_MS"):
            _vad_kw["max_single_segment_time"] = 30000
        print(f"[{ENGINE}] vad_kwargs={_vad_kw}", flush=True)
        kw = dict(
            model=model_id,
            vad_model=VAD_ID,
            vad_kwargs=_vad_kw,
            device=dev,
            disable_update=True,
        )
        if hub == "hf":
            kw["hub"] = "hf"
        model = AutoModel(**kw)

        _write_prog(cur_text="đang nhận diện (SenseVoice)…", progress=15, total_sec=total)
        print(f"[{ENGINE}] generate lang={language} · audio {total:.0f}s..."
              + (f" → {asr_path.name}" if is_side else ""), flush=True)

        res = model.generate(
            input=str(wav),
            cache={},
            language=language,  # zh | en | yue | ja | ko | auto
            use_itn=True,
            batch_size_s=60,
            merge_vad=True,
            merge_length_s=15,
        )
        _write_prog(cur_text="parse…", progress=85, total_sec=total)
        segs = _post_clean_segs(_parse_funasr_result(res, audio_dur=total))
        if not segs and res:
            # fallback: 1 khối text
            item = res[0] if isinstance(res, list) else res
            text = _clean_sv_text((item or {}).get("text") or "") if isinstance(item, dict) else ""
            if text:
                segs = _spread_sentences(text, 0.0, total) if _plain_len(text) > 18 else [
                    {"start": 0.0, "end": max(total, 0.5), "text": text}
                ]

        rows = [{"i": i, "start": s["start"], "end": s["end"], "text": s["text"]}
                for i, s in enumerate(segs)]
        max_zh, _, max_dur = sub_split_cfg()
        rows = split_long_segments(rows, max_chars=max_zh, key="text", max_dur=max_dur)
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
                    "updated_at": time.time(),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            _salvage_asr(m, asr_path, rows, wav if wav.exists() else None, total, err=e)
        raise

    if is_side:
        result = {
            "status": "done", "engine": ENGINE, "model": model_id,
            "asr_path": str(asr_path), "srt_path": str(srt_path),
            "n_segments": len(rows), "language": language, "sidecar": tag,
        }
        if prog_path is not None:
            prog_path.write_text(json.dumps({**result, "progress": 100,
                                             "updated_at": time.time()},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        result = m.mark_done(
            STAGE, asr_path=str(asr_path), audio_path=str(wav),
            n_segments=len(rows), language=language, model=model_id,
            engine=ENGINE, asr_engine=ENGINE,
        )
    print(f"[ok] {ENGINE}: {len(rows)} câu -> {asr_path}")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="ASR SenseVoiceSmall (FunASR).")
    ap.add_argument("work_dir")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--hub", default="ms", choices=("ms", "hf"))
    ap.add_argument("--lang", default="zh", help="zh|en|yue|ja|ko|auto")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sidecar", default=None,
                    help="Ghi asr.<tag>.json + sub.zh.<tag>.srt (không đụng asr.json)")
    args = ap.parse_args(argv)
    run_asr_sensevoice(
        args.work_dir, device=args.device, force=args.force,
        language=args.lang, hub=args.hub, sidecar=args.sidecar,
    )
    os._exit(0)


if __name__ == "__main__":
    main()
