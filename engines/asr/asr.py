"""
asr.py — STAGE 2 (phần đầu): tách audio + nhận diện lời (Whisper).

Đầu vào : work/<job>/source.mp4  (từ stage download)
Đầu ra  : work/<job>/audio.wav   (16kHz mono — mặc định trích từ vocals.wav (Demucs), xem resolve_asr_source)
          work/<job>/asr.json     (danh sách câu: {i, start, end, text})

Engine: faster-whisper (nhanh hơn openai-whisper nhiều, chạy CUDA).

Chạy tay:
    python asr.py work/<job>
    python asr.py work/<job> --model large-v3 --lang zh

Yêu cầu:
    pip install faster-whisper
    ffmpeg trong PATH

VRAM: large-v3 ~10GB (float16). Card nhỏ dùng --model medium, hoặc
--compute-type int8_float16 để nhẹ hơn.

Treo/crash: ghi asr.json dần; ASR_STALL_SEC (mặc định 900) không tiến độ ->
pending + giữ partial; resume cắt tiếp từ mốc giây cuối.
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[2])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import os as _os
# FS không hỗ trợ symlink (ổ D:/exFAT/máy chưa bật Developer Mode) -> HF copy thay symlink,
# tránh 'OSError WinError 1' khi tải model Whisper/HF. Phải đặt TRƯỚC khi import huggingface.
_os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
_os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import argparse
import json
import subprocess
import threading
import time as _t
from pathlib import Path

from core.fileio import atomic_write_json, read_json_checkpoint
from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
from engines.vram import free_gpu_vram

STAGE = "asr"


def _audio_marker(wav: Path) -> Path:
    return wav.with_name(wav.name + ".json")


def _src_id(src: Path) -> dict:
    from engines.media_ts import AUDIO_TS_FIX
    s = src.stat()
    return {"src": src.name, "size": s.st_size, "mtime": int(s.st_mtime), "af": AUDIO_TS_FIX}


def extract_audio(src: str | Path, out_wav: str | Path) -> str:
    """Tách audio về 16kHz mono PCM — chuẩn đầu vào Whisper. Ghi .partial rồi replace + marker nguồn.

    AAC hỏng giữa file (rematrix Nch→mono -22): media_ts.extract_pcm_from_video salvage + chunk.
    """
    from engines.media_ts import extract_pcm_from_video
    src_p, out_p = Path(src), Path(out_wav)
    extract_pcm_from_video(src_p, out_p, ac=1, ar=16000)
    try:
        atomic_write_json(_audio_marker(out_p), _src_id(src_p), fsync=False)
    except OSError:
        pass
    return str(out_p)


def _wav_seconds(p: Path) -> float:
    try:
        import wave
        with wave.open(str(p), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 16000)
    except Exception:  # noqa: BLE001
        try:
            import soundfile as sf
            return float(sf.info(str(p)).duration)
        except Exception:  # noqa: BLE001
            return _probe_seconds(p)


def _probe_seconds(p: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True, timeout=60)
        return float((r.stdout or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def audio_wav_stale(src: str | Path, wav: str | Path, keep_legacy: bool = False) -> bool:
    """audio.wav có sẵn không dùng được: rỗng, marker khác nguồn / khác filter mốc, hoặc (chưa có
    marker) ngắn hơn video >1 s = trích dở. Wav cũ dài hơn video giữ nguyên — media_ts cần nó để dời mốc."""
    src_p, wav_p = Path(src), Path(wav)
    if not wav_p.exists() or wav_p.stat().st_size < 1000:
        return True
    try:
        if src_p.resolve() == wav_p.resolve() or not src_p.exists():
            return False
    except OSError:
        return False
    try:
        meta = json.loads(_audio_marker(wav_p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = None
    if meta is not None:
        return meta != _src_id(src_p)
    if keep_legacy:
        return False
    vd, ad = _probe_seconds(src_p), _wav_seconds(wav_p)
    if vd and ad and ad < vd - 1.0:
        print(f"[asr] audio.wav {ad:.1f}s ngắn hơn nguồn {vd:.1f}s → trích lại", flush=True)
        return True
    try:
        atomic_write_json(_audio_marker(wav_p), _src_id(src_p), fsync=False)
    except OSError:
        pass
    return False


def ensure_audio_wav(src: str | Path, wav: str | Path, force: bool = False,
                     keep_legacy: bool = False) -> bool:
    """Trích audio.wav khi thiếu / hỏng / khác nguồn (hoặc force). True nếu vừa trích."""
    if not force and not audio_wav_stale(src, wav, keep_legacy=keep_legacy):
        return False
    try:
        if Path(src).resolve() == Path(wav).resolve():
            return False
    except OSError:
        pass
    extract_audio(src, wav)
    return True


def asr_use_demucs() -> bool:
    """ASR_USE_DEMUCS — mặc định BẬT (2026-11-12): ASR chạy trên vocals.wav (Demucs).
    Tắt (rollback hành vi cũ): ASR_USE_DEMUCS=0 / false / no / off.

    V6 (port F6 2026-11-12): thứ tự = env ASR_USE_DEMUCS (đặt tay / A-B, thắng) → cờ instance
    `asr_use_demucs` (core.instance.apply_env → PINELINE_FLAG_ASR_USE_DEMUCS) → mặc định 1.
    Rollback theo instance: `"asr_use_demucs": false` trong instances/<tên>.json rồi restart."""
    v = (_os.getenv("ASR_USE_DEMUCS") or "").strip().lower()
    if not v:
        v = (_os.getenv("PINELINE_FLAG_ASR_USE_DEMUCS") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _asr_ui_phase(work_dir: Path, phase: str, cur_text: str) -> None:
    """Hiện pha của stage asr (vd 'separate' = đang tách giọng) — ghi qua Manifest.update.
    ASR đã xong thì không đụng."""
    def _fn(data: dict):
        st = data.setdefault("stages", {}).setdefault(STAGE, {})
        if st.get("status") == "done":
            return False
        if st.get("status") != "running":
            st["status"] = "running"
            st.setdefault("started_at", _t.strftime("%Y-%m-%dT%H:%M:%S"))
        st["phase"] = phase
        st["cur_text"] = cur_text
        st["updated_at"] = _t.time()
    try:
        js.mutate(work_dir, _fn)
    except Exception:  # noqa: BLE001
        pass


def _marker_src_name(wav: Path) -> str | None:
    try:
        return (json.loads(_audio_marker(wav).read_text(encoding="utf-8")) or {}).get("src")
    except (OSError, ValueError, AttributeError):
        return None


def resolve_asr_source(
    work_dir: str | Path,
    video_src: str | Path | None = None,
    force_demucs: bool = False,
    device: str = "cuda",
) -> Path:
    """Xác định nguồn audio cho ASR (engine sẽ ensure_audio_wav(src, audio.wav) 16k mono).

    MẶC ĐỊNH (2026-11-12, quyết định chủ dự án): chạy Demucs MỘT lần/job rồi ASR trên
    vocals.wav (giọng sạch). separate.py mới chia khúc tại khoảng lặng + cắt/ghép theo mẫu
    (tổng độ dài = đúng audio gốc) nên lý do cũ ("nuốt chữ, méo tiếng, lệch timing ở ranh
    giới chunk" do segment FLAC cắt cứng 60') không còn.
      • Demucs lỗi/OOM/thiếu gói -> log + FALLBACK audio gốc từ video (không fail job).
      • Đã có vocals.wav hợp lệ -> dùng lại (run_separate tự kiểm số mẫu/độ dài).
      • ASR đã xong (asr.json + manifest done) và không force -> không chạy Demucs, giữ
        đúng nguồn audio.wav hiện có (theo marker).
      • ASR_USE_DEMUCS=0 -> hành vi cũ: audio.wav trích thẳng từ video.
    force_demucs (= force của ASR) KHÔNG ép tách lại Demucs — chỉ trích lại audio.wav.
    """
    work_dir = Path(work_dir)
    voc = work_dir / "vocals.wav"
    wav = work_dir / "audio.wav"

    from engines.source_preflight import stage_source_video
    # agentK 2026-11-12: 1 hàm resolve nguồn (preflight chờ → chạy; nguồn chuẩn hoá > gốc)
    src_video = stage_source_video(work_dir)
    if video_src and Path(video_src).exists() and Path(video_src).suffix.lower() not in {".wav", ".flac"}:
        src_video = Path(video_src)
    m = Manifest(work_dir)

    asr_done = m.is_done(STAGE) and (work_dir / "asr.json").exists()
    if asr_done and not force_demucs:
        # Job đã ASR xong: không chạy Demucs, không đổi nguồn audio.wav (sidecar/repair dùng lại).
        if _marker_src_name(wav) == voc.name and voc.exists() and voc.stat().st_size > 1000:
            return voc
    elif asr_use_demucs() and src_video and src_video.exists():
        try:
            from engines.separate import outputs_valid
            if not outputs_valid(work_dir, src_video, m.stage("separate") if m.is_done("separate") else None)[0]:
                _asr_ui_phase(work_dir, "separate", "Tách giọng (Demucs) trước ASR…")
            from engines.separate import run_separate
            run_separate(work_dir, device=device, force=False)
            if voc.exists() and voc.stat().st_size > 1000:
                _asr_ui_phase(work_dir, "asr", "Tách giọng xong — nhận diện lời trên giọng sạch…")
                print("[asr] Nguồn ASR = vocals.wav (Demucs)", flush=True)
                return voc
            print("[asr] Demucs xong nhưng thiếu vocals.wav -> FALLBACK audio gốc", flush=True)
        except BaseException as e:  # noqa: BLE001
            if not isinstance(e, Exception) or (work_dir / ".stop").exists():
                raise   # Dừng theo yêu cầu / KeyboardInterrupt -> không nuốt
            print(f"[asr] CẢNH BÁO: Demucs lỗi ({type(e).__name__}: {str(e)[:300]}) "
                  f"-> FALLBACK ASR trên audio gốc (không tách giọng)", flush=True)
            try:
                js.mutate(work_dir, lambda d: d.setdefault("stages", {}).setdefault(STAGE, {}).update(
                    {"demucs_fallback": str(e)[:300]}) or None)
            except Exception:  # noqa: BLE001
                pass
        _asr_ui_phase(work_dir, "asr", "Tách giọng lỗi — ASR trên audio gốc…")

    # Hành vi cũ / fallback: audio.wav 16k mono trích thẳng từ video nguồn
    if src_video and src_video.exists():
        ensure_audio_wav(src_video, wav, force=force_demucs)
        return src_video

    if wav.exists() and wav.stat().st_size > 1000:
        return wav

    if voc.exists() and voc.stat().st_size > 1000:
        return voc

    raise RuntimeError("Chưa có video hoặc audio nguồn để ASR.")


def _cut_wav_from(src_wav: Path, out_wav: Path, start_sec: float) -> None:
    """Cắt phần còn lại từ start_sec (resume ASR giữa chừng)."""
    cmd = [
        "ffmpeg", "-y", "-ss", f"{start_sec:.3f}", "-i", str(src_wav),
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def pick_device(device: str) -> tuple[str, str]:
    """Chọn cuda nếu có GPU, không thì cpu. Trả (device, compute_type)."""
    if device != "auto":
        return device, ("float16" if device == "cuda" else "int8")
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:  # noqa: BLE001
        pass
    return "cpu", "int8"


def _salvage_asr(m: Manifest, asr_path: Path, rows: list, wav: Path | None,
                 total: float, err: BaseException | None = None) -> None:
    """Crash/treo: giữ asr.json đã có, status pending để bấm Tiếp tục."""
    if rows:
        try:
            atomic_write_json(asr_path, rows)
        except Exception:  # noqa: BLE001
            pass
    done_sec = float(rows[-1]["end"]) if rows else 0.0
    st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
    st.update({
        "status": "pending",
        "progress": round(min(done_sec / total * 100, 99.0), 1) if total else 0,
        "n_segments": len(rows),
        "done_sec": round(done_sec, 1),
        "total_sec": round(total, 1) if total else None,
        "asr_path": str(asr_path) if asr_path.exists() else None,
        "audio_path": str(wav) if wav and wav.exists() else None,
        "cur_text": f"đã lưu {len(rows)} câu (~{done_sec:.0f}s) — bấm Tiếp tục",
        "updated_at": _t.time(),
    })
    if err is not None:
        st["last_error"] = str(err)[:200]
    m.data["stages"][STAGE] = st
    js.persist(m)
    print(f"[resume] salvage ASR {len(rows)} câu -> pending", flush=True)


def run_asr(
    work_dir: str | Path,
    model_size: str = "large-v3",
    lang: str = "zh",
    device: str = "auto",
    compute_type: str | None = None,
    force: bool = False,
) -> dict:
    work_dir = Path(work_dir)
    m = Manifest(work_dir)

    if m.is_done(STAGE) and not force:
        st = m.stage(STAGE)
        if st and Path(st.get("asr_path", "")).exists():
            print(f"[skip] ASR đã xong: {st['asr_path']}")
            return st

    src = resolve_asr_source(work_dir, force_demucs=force)

    asr_path = work_dir / "asr.json"
    wav = work_dir / "audio.wav"
    rows: list[dict] = []
    resume_from = 0.0
    total = 0.0

    if force:
        try:
            asr_path.unlink(missing_ok=True)
        except OSError:
            pass
    elif asr_path.exists():
        prev = read_json_checkpoint(asr_path, default=None)
        try:
            if isinstance(prev, list) and prev:
                rows = prev
                resume_from = float(rows[-1].get("end") or 0)
                print(f"[resume] giữ {len(rows)} câu tới {resume_from:.1f}s, nhận diện phần còn",
                      flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[resume] asr.json không dùng được ({e}) — nhận diện lại từ đầu", flush=True)
            rows = []
            resume_from = 0.0

    m.mark_running(STAGE)
    free_gpu_vram("trước ASR Whisper")
    stall_sec = int(_os.getenv("ASR_STALL_SEC", "900"))  # 15 phút không segment mới
    last_beat = [_t.time()]
    stop_watch = threading.Event()
    stalled = [False]

    def _watchdog() -> None:
        while not stop_watch.wait(5.0):
            if _t.time() - last_beat[0] > stall_sec:
                stalled[0] = True
                print(f"[asr] TREO: không tiến độ >{stall_sec}s -> salvage + thoát", flush=True)
                _salvage_asr(m, asr_path, rows, wav if wav.exists() else None, total,
                             err=RuntimeError(f"ASR treo {stall_sec}s"))
                _os._exit(2)

    try:
        from faster_whisper import WhisperModel

        # resume giữa chừng: rows đã tính trên wav cũ → không trích lại wav cũ chưa có marker
        if ensure_audio_wav(src, wav, force=force, keep_legacy=resume_from > 0):
            print("[asr] Đã tách audio")
        else:
            print("[asr] Dùng lại audio.wav có sẵn")

        # Tổng thời lượng từ file wav (ffprobe / wave)
        try:
            import wave
            with wave.open(str(wav), "rb") as w:
                total = w.getnframes() / float(w.getframerate() or 16000)
        except Exception:  # noqa: BLE001
            total = float((m.stage(STAGE) or {}).get("total_sec") or 0)

        dev, ctype = pick_device(device)
        ctype = compute_type or ctype
        print(f"[asr] Nạp model {model_size} trên {dev}/{ctype}...")
        model = WhisperModel(model_size, device=dev, compute_type=ctype)

        audio_in = wav
        offset = 0.0
        if resume_from > 1.0 and total and resume_from < total - 1.0:
            part = work_dir / "audio_resume.wav"
            print(f"[asr] Cắt phần còn từ {resume_from:.1f}s...")
            _cut_wav_from(wav, part, resume_from)
            audio_in = part
            offset = resume_from
        elif resume_from > 0 and total and resume_from >= total - 1.0:
            print("[asr] Partial đã gần hết file — coi như xong, finalize")
            from engines.subs import to_srt
            (work_dir / "sub.zh.srt").write_text(to_srt(rows, key="text"), encoding="utf-8")
            result = m.mark_done(
                STAGE, asr_path=str(asr_path), audio_path=str(wav),
                n_segments=len(rows), language=lang, model=model_size,
                engine="whisper",
            )
            print(f"[ok] ASR xong (resume đủ): {len(rows)} câu -> {asr_path}")
            return result

        print(f"[asr] Nhận diện lời (lang={lang}"
              + (f", offset={offset:.1f}s" if offset else "") + ")...")
        segments, info = model.transcribe(
            str(audio_in),
            language=lang,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        clip_dur = float(getattr(info, "duration", 0) or 0)
        if not total:
            total = offset + clip_dur

        last_save = 0.0
        next_i = (max((r["i"] for r in rows), default=-1) + 1) if rows else 0

        def _tick(done_sec: float, force_save: bool = False):
            nonlocal last_save
            now = _t.time()
            last_beat[0] = now
            if not force_save and now - last_save < 1.5:
                return
            last_save = now
            atomic_write_json(asr_path, rows, fsync=force_save)
            st = m.data["stages"].get("asr", {})
            st.update({
                "status": "running",
                "progress": round(min(done_sec / total * 100, 99.9), 1) if total else None,
                "n_segments": len(rows),
                "done_sec": round(done_sec, 1), "total_sec": round(total, 1),
                "updated_at": now,
            })
            m.data["stages"]["asr"] = st
            js.persist(m)

        wd = threading.Thread(target=_watchdog, name="asr-stall", daemon=True)
        wd.start()
        try:
            for seg in segments:
                text = (seg.text or "").strip()
                abs_start = round(offset + float(seg.start), 3)
                abs_end = round(offset + float(seg.end), 3)
                if text:
                    rows.append({"i": next_i, "start": abs_start, "end": abs_end, "text": text})
                    next_i += 1
                _tick(abs_end)
            _tick(total or (offset + clip_dur), force_save=True)
        finally:
            stop_watch.set()

        from engines.subs import to_srt, split_long_segments, sub_split_cfg
        max_zh, _, max_dur = sub_split_cfg()
        rows = split_long_segments(rows, max_chars=max_zh, key="text", max_dur=max_dur)
        (work_dir / "sub.zh.srt").write_text(to_srt(rows, key="text"), encoding="utf-8")
        try:
            (work_dir / "audio_resume.wav").unlink(missing_ok=True)
        except OSError:
            pass
    except Exception as e:  # noqa: BLE001
        stop_watch.set()
        if stalled[0]:
            raise
        _salvage_asr(m, asr_path, rows, wav if wav.exists() else None, total, err=e)
        raise

    result = m.mark_done(
        STAGE, asr_path=str(asr_path), audio_path=str(wav),
        n_segments=len(rows), language=lang, model=model_size,
        engine="whisper",
    )
    print(f"[ok] ASR xong: {len(rows)} câu -> {asr_path}")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stage 2 — ASR tiếng Trung có timestamp.")
    ap.add_argument("work_dir", help="Thư mục job, vd work/abc123")
    ap.add_argument("--model", default="large-v3", help="Kích thước model Whisper.")
    ap.add_argument("--lang", default="zh", help="Ngôn ngữ nguồn (zh = tiếng Trung).")
    ap.add_argument("--device", default="auto", help="auto / cuda / cpu")
    ap.add_argument("--compute-type", default=None, help="float16 / int8_float16 / int8")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    run_asr(
        work_dir=args.work_dir, model_size=args.model, lang=args.lang,
        device=args.device, compute_type=args.compute_type, force=args.force,
    )
    # Thoát NGAY, bỏ qua destructor CUDA/CTranslate2 (nguồn crash 0xC0000409 lúc
    # teardown). Chạy tới đây là đã mark_done -> an toàn. HĐH tự thu hồi VRAM.
    import os
    os._exit(0)


if __name__ == "__main__":
    main()
