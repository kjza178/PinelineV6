"""clone_tts.py — STAGE Lồng tiếng bằng TTS zero-shot clone tiếng Việt.

Hai engine, cùng registry giọng (voices/f5_voices.json: name -> {ref, text}):
  - "vieneu"    : VieNeu-TTS v3-Turbo (G2P tiếng Việt riêng, chạy CPU/ONNX)
  - "omnivoice" : omnivoice-vietnamese (non-AR diffusion, chạy GPU)

Mỗi engine chạy trong venv riêng qua 1 worker (rvc_bridge/<engine>_batch.py) đọc job json,
ghi thẳng tts/seg_XXXX.wav. Orchestrator gom -> tts.json + mark_done (khớp assemble/render).
"""
from __future__ import annotations

from state import job_state as js  # V6 F1: mọi ghi manifest qua state
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from core.manifest import Manifest
from engines.vram import free_gpu_vram

STAGE = "tts"
_PROJ = Path(__file__).resolve().parents[1]

# Câu "rác" ASR/dịch (chỉ dấu câu/khoảng trắng) — VieNeu ra wav 0 byte → báo thiếu mãi.
import re as _re
_TRIVIAL_VI = _re.compile(r"^[\s\W_0-9]*$", _re.UNICODE)

# venv + worker theo engine — đường dẫn PORTABLE lấy từ config.py (env > pineline.config.json > default)
import core.config as _cfg

ENGINE_CFG = {
    "vieneu": {
        "py": _cfg.VIENEU_PY,
        "worker": str(_PROJ / "rvc_bridge" / "vieneu_batch.py"),
        "device": _cfg.VIENEU_DEVICE,                   # GPU (PyTorch); đặt cpu nếu cần nhường VRAM
        # GPU: gộp N câu/forward (infer_batch). Env VIENEU_BATCH hoặc pineline.config.json.
        "batch_size": int(os.getenv("VIENEU_BATCH") or _cfg.get("VIENEU_BATCH", "24") or "24"),
        "cwd": str(_PROJ),
    },
    "omnivoice": {
        "py": _cfg.OMNI_PY,
        "worker": str(_PROJ / "rvc_bridge" / "omnivoice_batch.py"),
        "device": _cfg.OMNI_DEVICE,
        "cwd": _cfg.OMNI_CWD,
        "model": _cfg.OMNI_MODEL,
        "num_step": int(os.getenv("OMNI_NUM_STEP", "48")),      # 32→48: ổn định hơn, ít glitch
        "guidance_scale": float(os.getenv("OMNI_GUIDANCE", "2.0")),
    },
}
HF_HOME = _cfg.HF_HOME
VOICES_FILE = _PROJ / "voices" / "f5_voices.json"


def resolve_engine_python(engine: str) -> str:
    """Tìm python.exe của engine. Config sai path (thường sau install) -> thử chỗ mặc định."""
    if engine not in ENGINE_CFG:
        raise RuntimeError(f"Engine '{engine}' khong ho tro")
    raw = ENGINE_CFG[engine]["py"]
    candidates = [Path(raw)] if raw else []
    if engine == "vieneu":
        candidates += [
            _PROJ.parent / "vieneu_env" / "Scripts" / "python.exe",
            _PROJ / "vieneu_env" / "Scripts" / "python.exe",
            Path("D:/vieneu_env/Scripts/python.exe"),
            Path(os.path.expandvars(r"%USERPROFILE%/vieneu_env/Scripts/python.exe")),
        ]
    elif engine == "omnivoice":
        candidates += [
            Path(_cfg.OMNI_PY) if _cfg.OMNI_PY else None,
            Path("D:/OmniVoiceStudio-v2/.venv/Scripts/python.exe"),
        ]
    seen = set()
    for c in candidates:
        if not c:
            continue
        key = os.path.normcase(str(c))
        if key in seen:
            continue
        seen.add(key)
        if c.exists():
            ENGINE_CFG[engine]["py"] = str(c)  # cache cho lần sau
            return str(c)
    raise RuntimeError(
        f"Khong tim thay Python cho '{engine}'. Da thu:\n  - " +
        "\n  - ".join(str(c) for c in candidates if c) +
        "\nChay: python install.py --gpu   (tao vieneu_env canh thu muc Pineline)"
    )


def _load_registry() -> dict:
    if VOICES_FILE.exists():
        return json.loads(VOICES_FILE.read_text(encoding="utf-8"))
    return {}


def list_clone_voices() -> list[str]:
    """Tên các giọng clone đã có ref audio hợp lệ."""
    out = []
    for name, v in _load_registry().items():
        ref = _resolve_ref(v.get("ref", ""))
        if ref and ref.exists():
            out.append(name)
    return out


def _resolve_ref(ref: str) -> Path | None:
    if not ref:
        return None
    p = Path(str(ref).replace("\\", "/"))
    if not p.is_absolute():
        p = _PROJ / p
    return p


def voice_ref_text(voice: str) -> tuple[Path, str] | None:
    """(ref_path, ref_text) của 1 giọng, hoặc None."""
    v = _load_registry().get(voice)
    if not v:
        return None
    ref = _resolve_ref(v.get("ref", ""))
    if not ref or not ref.exists():
        return None
    return ref, (v.get("text") or "")


def enroll_voice(name: str, ref_path: str | Path, ref_text: str) -> str:
    """Đăng ký giọng clone vào voices/f5_voices.json (VieNeu + OmniVoice dùng chung)."""
    import shutil

    name = (name or "").strip()
    if not name:
        raise ValueError("thiếu tên giọng")
    src = Path(ref_path)
    if not src.exists():
        raise FileNotFoundError(str(src))
    ref_dir = _PROJ / "voices" / "clone_refs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    dst = ref_dir / f"{name}{src.suffix.lower() or '.wav'}"
    shutil.copyfile(src, dst)
    rel = dst.relative_to(_PROJ).as_posix()
    data = _load_registry()
    data[name] = {"ref": rel, "text": (ref_text or "").strip()}
    VOICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    VOICES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return name


def _is_trivial_vi(text: str | None) -> bool:
    """True nếu không có chữ thật (vd ',', '...', '!!!') — không nên gọi TTS."""
    t = (text or "").strip()
    return (not t) or bool(_TRIVIAL_VI.match(t))


def vi_for_tts(text: str | None) -> str:
    """Chuẩn bị chữ cho VieNeu/Omni: gộp số có dấu nghìn để đọc đúng.

    Phụ đề/UI dùng «30 000» / «31.000» (tránh CUT); TTS dễ đọc sai khoảng trắng
    («ba mươi… không không không»). Gộp thành «30000» trước khi infer.
    """
    import re
    t = (text or "").strip()
    if not t:
        return t
    # 30 000 / 1 234 567 → 30000
    t = re.sub(
        r"\b(\d{1,3}(?: \d{3})+)\b",
        lambda m: m.group(0).replace(" ", ""),
        t,
    )
    # 31.000 / 31,000 → 31000 (phòng bản chưa normalize)
    t = re.sub(
        r"\b(\d{1,3})(?:[.,](\d{3}))+\b",
        lambda m: m.group(0).replace(".", "").replace(",", ""),
        t,
    )
    return t


def _write_silence_wav(path: Path, dur_sec: float, sr: int = 48000) -> float:
    """Ghi wav im lặng đúng độ dài timeline (tối thiểu 0.15s để qua kiểm tra dur)."""
    import wave
    import struct
    dur = max(0.15, float(dur_sec) if dur_sec and dur_sec > 0 else 0.2)
    n = int(sr * dur)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack(f"<{n}h", *([0] * n)))
    return round(dur, 3)


def _silence_entry(s: dict, out_dir: Path, engine: str) -> dict:
    """Placeholder im lặng cho câu vô nghĩa / wav hỏng — đủ slot timeline, không báo thiếu."""
    path = out_dir / f"seg_{s['i']:04d}.wav"
    dur = _write_silence_wav(path, float(s.get("end", 0) or 0) - float(s.get("start", 0) or 0))
    return {"i": s["i"], "start": s["start"], "end": s["end"], "vi": s.get("vi") or "",
            "audio": str(path), "dur": dur, "engine": engine, "silence": True}


def _dur(path: Path) -> float:
    """Độ dài wav (giây). Ưu tiên wave; fallback ffprobe."""
    try:
        import wave
        with wave.open(str(path), "rb") as w:
            fr = w.getframerate() or 1
            return round(w.getnframes() / float(fr), 3)
    except Exception:  # noqa: BLE001
        pass
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(path)],
                           capture_output=True, text=True, timeout=5)
        return round(float(r.stdout.strip()), 3)
    except Exception:  # noqa: BLE001
        return 0.0


def _entry_from_wav(s: dict, path: Path, engine: str) -> dict | None:
    """Tạo entry tts.json từ file wav đã có. None nếu file hỏng/quá ngắn."""
    if not path.exists() or path.stat().st_size < 1000:
        return None
    d = _dur(path)
    if d <= 0.1:
        return None
    return {"i": s["i"], "start": s["start"], "end": s["end"], "vi": s["vi"],
            "audio": str(path), "dur": d, "engine": engine}


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _drop_runaway_from_done(segments: list, done: dict[int, dict], out_dir: Path) -> int:
    """Resume/recheck: bỏ clip hú khỏi done + xóa wav → TTS sẽ đọc lại."""
    from engines.assemble import is_runaway_tts
    by_i = {s["i"]: s for s in segments}
    drop: list[int] = []
    for i, r in list(done.items()):
        if r.get("silence"):
            continue
        s = by_i.get(i) or r
        if is_runaway_tts(s, float(r.get("dur") or 0)):
            drop.append(i)
    for i in drop:
        done.pop(i, None)
        _unlink_quiet(out_dir / f"seg_{i:04d}.wav")
    if drop:
        print(f"[recheck] {len(drop)} câu runaway (dur bất thường) -> TTS lại", flush=True)
    return len(drop)


def _pack_clip(s: dict, path: Path, engine: str, speed: float, skip_pp: bool) -> dict | None:
    """Postprocess + entry. None nếu hỏng. Caller xử lý runaway."""
    if not path.exists() or path.stat().st_size < 1000 or _dur(path) <= 0.1:
        return None
    if not skip_pp:
        _postprocess_clip(path, engine, speed)
    return _entry_from_wav(s, path, engine)


def _recheck_pack_and_retry(
    engine: str, ref: Path, ref_text: str, out_dir: Path, speed: float,
    segments: list, done: dict[int, dict], packed_ids: list[int],
    pre_done: set[int], total_target: int, voice: str, m,
) -> tuple[int, int]:
    """Đóng gói todo vừa TTS; runaway -> xóa -> retry tối đa N lần -> silence slot.

    Trả (n_retry_ok, n_silence_runaway).
    """
    from engines.assemble import is_runaway_tts
    try:
        max_retries = max(0, int(os.getenv("TTS_RUNAWAY_RETRIES", "2")))
    except ValueError:
        max_retries = 2
    by_i = {s["i"]: s for s in segments}
    skip_pp = engine == "vieneu" and abs(float(speed) - 1.0) < 0.02
    retry: list[tuple[int, str]] = []
    n_retry_ok = 0
    n_sil = 0

    def _handle(i: int, phase: str) -> None:
        nonlocal n_sil
        s = by_i[i]
        f = out_dir / f"seg_{i:04d}.wav"
        ent = _pack_clip(s, f, engine, speed, skip_pp)
        if ent is None:
            done[i] = _silence_entry(s, out_dir, engine)
            return
        if is_runaway_tts(s, float(ent["dur"])):
            print(f"[recheck] i={i} dur={ent['dur']:.2f}s vi={s.get('vi')!r} ({phase})",
                  flush=True)
            _unlink_quiet(f)
            done.pop(i, None)
            retry.append((i, vi_for_tts(s["vi"])))
            return
        done[i] = ent

    for n, i in enumerate(packed_ids, 1):
        _handle(i, "pack")
        if n == 1 or n == len(packed_ids) or n % 200 == 0:
            _prog(m, len(pre_done) + n, total_target, engine,
                  f"recheck {n}/{len(packed_ids)}", voice=voice, speed=speed)

    for attempt in range(1, max_retries + 1):
        if not retry:
            break
        batch = list(retry)
        retry = []
        print(f"[recheck] retry {attempt}/{max_retries}: {len(batch)} câu", flush=True)
        _prog(m, len(done), total_target, engine,
              f"recheck retry {attempt}/{max_retries}", voice=voice, speed=speed)

        def _on_retry(cur: int, _a=attempt) -> None:
            if cur < 0:
                return
            _prog(m, len(done), total_target, engine,
                  f"recheck retry {_a}", voice=voice, speed=speed)

        _run_worker(engine, ref, ref_text, out_dir, speed, batch, on_prog=_on_retry)
        for i, _t in batch:
            _handle(i, f"retry{attempt}")
            if i in done and not done[i].get("silence"):
                n_retry_ok += 1

    for i, _t in retry:
        s = by_i[i]
        print(f"[recheck] i={i} vẫn runaway -> silence slot", flush=True)
        done[i] = _silence_entry(s, out_dir, engine)
        n_sil += 1
    return n_retry_ok, n_sil


_KEYS_NAME = "_keys.json"


def _clip_key(vi: str, voice: str, engine: str, speed: float) -> str:
    raw = f"{vi or ''}\x1f{voice or ''}\x1f{engine or ''}\x1f{float(speed or 1.0):.3f}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _load_keys(out_dir: Path) -> dict[str, str] | None:
    try:
        d = json.loads((out_dir / _KEYS_NAME).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _save_keys(out_dir: Path, keys: dict[str, str]) -> None:
    p = out_dir / _KEYS_NAME
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        print(f"[tts] không ghi được {p.name}: {e}", flush=True)


def _stamp_keys(done: dict[int, dict], keys: dict[int, str] | None) -> None:
    for i, e in done.items():
        if keys and i in keys and "key" not in e:
            e["key"] = keys[i]


def _scan_done_from_disk(out_dir: Path, segments: list, engine: str,
                         done: dict[int, dict], disk_keys: dict[str, str] | None = None,
                         keyof: dict[int, str] | None = None) -> dict[int, dict]:
    """Resume: nhận diện seg_XXXX.wav đã ghi (sau khi Dừng) dù chưa kịp ghi tts.json.
    `_keys.json` ghi key khác key hiện tại (đổi lời / giọng / tốc độ) → bỏ wav đó."""
    for s in segments:
        if not s.get("vi"):
            continue
        i = s["i"]
        if i in done:
            continue
        f = out_dir / f"seg_{i:04d}.wav"
        if disk_keys and keyof and disk_keys.get(str(i)) not in (None, keyof.get(i)):
            _unlink_quiet(f)
            continue
        ent = _entry_from_wav(s, f, engine)
        if ent:
            done[i] = ent
    return done


def _postprocess_clip(path: Path, engine: str, speed: float) -> None:
    if engine == "omnivoice":
        _trim_onset(path)
    # 14k câu × ffmpeg fade = UI kẹt 99% hàng chục phút sau khi worker DONE.
    # VieNeu speed=1: wav đã sạch — bỏ fade trừ khi CLONE_CLEANUP=1.
    want_clean = os.getenv("CLONE_CLEANUP", "0" if engine == "vieneu" else "1") == "1"
    if want_clean:
        _cleanup_audio(path)
    _apply_speed(path, speed)


def _salvage_progress(m, segments, done, tts_path, out_dir, engine, voice, speed,
                      pre_done: set[int], err: BaseException | None = None,
                      keys: dict[int, str] | None = None) -> None:
    """Khi dừng/crash: gom wav đã có -> tts.json, status pending (để bấm Tiếp tục)."""
    by_i = {s["i"]: s for s in segments}
    for s in segments:
        if not s.get("vi"):
            continue
        i = s["i"]
        f = out_dir / f"seg_{i:04d}.wav"
        if i in done:
            continue
        if not f.exists():
            continue
        try:
            if i not in pre_done:
                _postprocess_clip(f, engine, speed)
            ent = _entry_from_wav(s, f, engine)
            if ent:
                done[i] = ent
        except Exception:  # noqa: BLE001
            pass
    _stamp_keys(done, keys)
    ordered = [done[s["i"]] for s in segments if s["i"] in done]
    try:
        tts_path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    total = sum(1 for s in segments if s.get("vi"))
    n_miss = total - len(done)
    st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
    st.update({
        "status": "pending",
        "progress": round(len(done) / total * 100, 1) if total else 0,
        "n_done": len(done),
        "n_total": total,
        "n_clips": len(done),
        "n_missing": n_miss,
        "engine": engine,
        "voice": voice,
        "speed": speed,
        "tts_path": str(tts_path),
        "tts_dir": str(out_dir),
        "cur_text": f"đã lưu {len(done)}/{total} — bấm Tiếp tục",
        "updated_at": time.time(),
    })
    if err is not None and "stop" not in str(err).lower() and "terminat" not in str(err).lower():
        st["last_error"] = str(err)[:200]
    m.data["stages"][STAGE] = st
    js.persist(m)
    try:
        (out_dir.parent / "tts_config.json").write_text(json.dumps(
            {"voice": voice, "speed": speed, "tts_engine": engine, "engine": engine},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    print(f"[resume] salvage {len(done)}/{total} clips -> tts.json (pending)", flush=True)


def run_clone_tts(work_dir: str | Path, engine: str, voice: str, speed: float = 1.0,
                  force: bool = False) -> dict:
    if engine not in ENGINE_CFG:
        raise RuntimeError(f"Engine '{engine}' không hỗ trợ. Có: {', '.join(ENGINE_CFG)}")
    cfg = ENGINE_CFG[engine]
    work_dir = Path(work_dir).resolve()   # tuyệt đối: worker chạy ở cwd venv khác vẫn đúng
    m = Manifest(work_dir)

    script_path = work_dir / "script.json"
    if not script_path.exists():
        raise RuntimeError("Chưa có script.json (dịch trước).")
    segments = json.loads(script_path.read_text(encoding="utf-8"))

    vr = voice_ref_text(voice)
    if not vr:
        raise RuntimeError(f"Chưa có giọng clone '{voice}' (thêm giọng: file mẫu + lời).")
    ref, ref_text = vr

    out_dir = work_dir / "tts"
    out_dir.mkdir(parents=True, exist_ok=True)
    tts_path = work_dir / "tts.json"

    # resume: tts.json + seg_*.wav trên đĩa (sau Dừng thường chỉ còn wav)
    keyof = {s["i"]: _clip_key(s["vi"], voice, engine, speed) for s in segments if s.get("vi")}
    vi_of = {s["i"]: s.get("vi") or "" for s in segments}
    keyless: set[int] = set()
    done: dict[int, dict] = {}
    if not force:
        if tts_path.exists():
            n_stale = 0
            for r in json.loads(tts_path.read_text(encoding="utf-8")):
                if (r.get("audio") and Path(r["audio"]).exists() and r.get("dur", 0) > 0.1
                        and r.get("engine") == engine):
                    i = r["i"]
                    # clip cũ không có key: chỉ so lời; có key: so cả giọng / engine / tốc độ
                    stale = (i not in keyof or (r.get("vi") or "") != vi_of.get(i)
                             or (r.get("key") and r["key"] != keyof[i]))
                    if stale:
                        n_stale += 1
                        _unlink_quiet(Path(r["audio"]))
                        continue
                    if not r.get("key"):
                        keyless.add(i)
                    done[i] = r
            if n_stale:
                print(f"[resume] bỏ {n_stale} clip cũ (lời / giọng / tốc độ đã đổi) — sẽ đọc lại",
                      flush=True)
        disk_keys = _load_keys(out_dir)
        before = set(done)
        done = _scan_done_from_disk(out_dir, segments, engine, done,
                                    disk_keys=disk_keys, keyof=keyof)
        for i in set(done) - before:
            if not disk_keys or disk_keys.get(str(i)) != keyof.get(i):
                keyless.add(i)
        n_drop = _drop_runaway_from_done(segments, done, out_dir)
        if done:
            msg = f"[resume] giữ {len(done)} câu đã có, chỉ đọc phần thiếu"
            if n_drop:
                msg += f" (+{n_drop} runaway sẽ đọc lại)"
            print(msg, flush=True)

    # Câu chỉ dấu câu / wav rỗng: cắm silence, không gọi VieNeu (tránh "thiếu 1" mãi)
    for s in segments:
        if not s.get("vi") or s["i"] in done:
            continue
        if _is_trivial_vi(s.get("vi")):
            done[s["i"]] = _silence_entry(s, out_dir, engine)
            print(f"[tts] skip trivial i={s['i']} {s.get('vi')!r} -> silence", flush=True)
            continue
        f = out_dir / f"seg_{s['i']:04d}.wav"
        if f.exists() and (f.stat().st_size < 1000 or _dur(f) <= 0.1):
            done[s["i"]] = _silence_entry(s, out_dir, engine)
            print(f"[tts] wav hỏng i={s['i']} -> silence", flush=True)

    total_target = sum(1 for s in segments if s.get("vi"))
    if force:
        for f in out_dir.glob("seg_*.wav"):
            try:
                f.unlink()
            except OSError:
                pass
        done = {}
        # sau force vẫn skip trivial ngay (không cần GPU)
        for s in segments:
            if s.get("vi") and _is_trivial_vi(s.get("vi")):
                done[s["i"]] = _silence_entry(s, out_dir, engine)
        keyless = set()
    pre_done = set(done.keys())
    todo = [(s["i"], vi_for_tts(s["vi"])) for s in segments
            if s.get("vi") and s["i"] not in done and not _is_trivial_vi(s.get("vi"))]
    stamp = {i: k for i, k in keyof.items() if i not in keyless}
    _save_keys(out_dir, {str(i): k for i, k in stamp.items()})

    m.mark_running(STAGE)
    free_gpu_vram("trước TTS")
    # Persist config ngay khi chạy (voice/speed/engine) — không phụ thuộc UI
    try:
        st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
        st["voice"] = voice
        st["speed"] = speed
        st["engine"] = engine
        st["tts_engine"] = engine
        js.persist(m)
        side = work_dir / "tts_config.json"
        side.write_text(json.dumps(
            {"voice": voice, "speed": speed, "tts_engine": engine, "engine": engine},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[tts] lưu config: {e}", flush=True)
    try:
        if not todo:
            return _finalize(m, segments, done, tts_path, out_dir, engine, voice, speed, keys=stamp)

        def _on_prog(cur: int, phase: str | None = None) -> None:
            # cur<0 = heartbeat load model (giữ n_done)
            if cur < 0:
                ph=(phase or "loading").strip().lower()
                if ph.startswith("vieneu") or ph=="ready":
                    label=f"{engine} ready"
                elif ph.startswith("enroll"):
                    label=f"{engine} enroll"
                elif ph.startswith("cpu"):
                    label=f"{engine} cpu"
                else:
                    label=f"{engine} loading"
                _prog(m, len(pre_done), total_target, engine, label,
                      voice=voice, speed=speed)
                return
            _prog(m, len(pre_done) + cur, total_target, engine, f"{engine} doc",
                  voice=voice, speed=speed)

        print(f"[tts] goi worker {engine} todo={len(todo)} py={resolve_engine_python(engine)}",
              flush=True)
        _prog(m, len(pre_done), total_target, engine, f"{engine} spawn worker",
              voice=voice, speed=speed)
        _run_worker(engine, ref, ref_text, out_dir, speed, todo, on_prog=_on_prog)

        print(f"[tts] worker DONE — đóng gói + recheck {len(todo)} wav...",
              flush=True)
        _prog(m, max(len(pre_done), total_target - 1), total_target, engine,
              "dong goi + recheck", voice=voice, speed=speed)
        n_ok, n_sil = _recheck_pack_and_retry(
            engine, ref, ref_text, out_dir, speed, segments, done,
            [i for i, _ in todo], pre_done, total_target, voice, m)
        if n_ok or n_sil:
            print(f"[recheck] retry OK={n_ok}, silence runaway={n_sil}", flush=True)
    except Exception as e:  # noqa: BLE001
        _salvage_progress(m, segments, done, tts_path, out_dir, engine, voice, speed,
                          pre_done, err=e, keys=stamp)
        raise

    return _finalize(m, segments, done, tts_path, out_dir, engine, voice, speed, keys=stamp)


def _cleanup_audio(path: Path) -> None:
    """Làm sạch clip gen ra:
    - LUÔN: fade-in/out ~8ms + cắt lặng đầu/cuối -> diệt click/pop onset ('lỗi đầu file'
      của OmniVoice) + timing gọn.
    - TUỲ CHỌN: high-shelf hạ tần cao (CLONE_HISHELF>0) khi giọng cụ thể bị gắt/rè.
    Tắt toàn bộ: CLONE_CLEANUP=0."""
    if os.getenv("CLONE_CLEANUP", "1") == "0":
        return
    # fade-in đầu + fade-out cuối (dùng areverse vì afade=out mặc định fade từ giây 0 -> câm hết)
    parts = ["afade=t=in:d=0.008", "areverse", "afade=t=in:d=0.012", "areverse"]
    hs = float(os.getenv("CLONE_HISHELF", "0"))         # 0=không hạ tần cao (mặc định)
    if hs > 0:
        parts.append(f"highshelf=g=-{hs}:f=6000")
    af = ",".join(parts)
    tmp = path.with_suffix(".cl.wav")
    try:
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                            "-i", str(path), "-af", af, str(tmp)], capture_output=True)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            path.unlink(missing_ok=True)
            tmp.replace(path)
        else:
            tmp.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _trim_onset(path: Path) -> bool:
    """Cắt 'âm rác đầu' của OmniVoice: nếu đoạn tiếng ĐẦU TIÊN ngắn (<0.45s) và có
    khoảng LẶNG (>0.09s) rồi mới tới đoạn tiếng chính -> đó là tiếng thừa onset,
    cắt bỏ tới sát đoạn chính. Trả True nếu có cắt. An toàn: chỉ cắt khi đúng cấu trúc."""
    import numpy as np
    import soundfile as sf
    a, sr = sf.read(str(path))
    if getattr(a, "ndim", 1) > 1:
        a = a.mean(axis=1)
    a = a.astype("float32")
    win = int(sr * 0.02); hop = int(sr * 0.01)
    e = np.array([np.sqrt(np.mean(a[i:i + win] ** 2)) for i in range(0, len(a) - win, hop)])
    if len(e) < 5:
        return False
    thr = max(0.012, float(e.max()) * 0.12)
    voiced = e > thr
    # gom thành đoạn (start_hop, end_hop)
    segs = []; s = None
    for k, v in enumerate(voiced):
        if v and s is None:
            s = k
        elif not v and s is not None:
            segs.append((s, k)); s = None
    if s is not None:
        segs.append((s, len(voiced)))
    if len(segs) < 2:
        return False
    first_dur = (segs[0][1] - segs[0][0]) * hop / sr
    gap = (segs[1][0] - segs[0][1]) * hop / sr
    if first_dur < 0.45 and gap > 0.09:                 # blip ngắn + lặng -> onset rác
        cut = max(0, int((segs[1][0] * hop) - int(sr * 0.03)))   # chừa 30ms trước câu chính
        if cut > int(sr * 0.05) and (len(a) - cut) > int(sr * 0.2):   # còn đủ nội dung
            sf.write(str(path), a[cut:], sr)
            return True
    return False


def _apply_speed(path: Path, speed: float) -> None:
    """Đổi tốc độ đọc bằng rubberband giữ formant (fallback atempo). speed>1 nhanh, <1 chậm.
    Áp đồng nhất cho vieneu/omnivoice -> nút tốc độ luôn có tác dụng mà không làm méo/rè sibilants."""
    if not speed or abs(speed - 1.0) < 0.02:
        return
    s = max(0.5, min(speed, 2.0))            # giới hạn 0.5–2.0
    tmp = path.with_suffix(".sp.wav")
    for filt in (
        f"rubberband=tempo={s:.3f}:formant=preserved:pitchq=quality:transients=crisp",
        f"rubberband=tempo={s:.3f}:formant=preserved",
        f"atempo={s:.3f}",
    ):
        try:
            r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                                "-i", str(path), "-filter:a", filt, str(tmp)],
                               capture_output=True)
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                path.unlink(missing_ok=True)
                tmp.replace(path)
                return
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _run_worker(engine: str, ref: Path, ref_text: str, out_dir: Path, speed: float,
                lines: list, on_prog=None) -> None:
    """Chạy worker venv cho `lines`=[(i,text)], ghi out_dir/seg_XXXX.wav. Raise nếu lỗi.

    Timeout treo: không có tiến độ (dòng prog HOẶC file wav mới) trong VIENEU_STALL_SEC
    (mặc định 600s) -> kill worker. Chống đơ CUDA/batch vô hạn.
    """
    import threading
    cfg = ENGINE_CFG[engine]
    py = resolve_engine_python(engine)
    worker = Path(cfg["worker"])
    if not worker.exists():
        raise RuntimeError(f"Thieu worker script: {worker}")

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    job = out_dir / f"_{engine}_job.json"
    payload = {"out_dir": str(out_dir), "ref": str(ref), "text": ref_text,
               "device": cfg["device"], "speed": speed, "lines": lines}
    for k in ("model", "num_step", "guidance_scale", "batch_size"):
        if k in cfg:
            payload[k] = cfg[k]
    job.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    env = dict(os.environ)
    env["HF_HOME"] = HF_HOME
    env.pop("PYTHONHASHSEED", None)

    stall_sec = int(os.getenv("VIENEU_STALL_SEC", "600"))
    # Lần đầu load model có thể lâu — cho 180s trước khi coi là treo nếu chưa có output gì
    boot_sec = int(os.getenv("VIENEU_BOOT_SEC", "180"))
    print(f"[{engine}] spawn: {py} {worker.name} lines={len(lines)} batch={payload.get('batch_size')}",
          flush=True)
    p = subprocess.Popen([py, str(worker), str(job)], cwd=cfg["cwd"], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", bufsize=1)
    from collections import deque
    tail = deque(maxlen=25)
    last_beat = [time.time()]          # mutable cho closure
    stalled = [False]
    known_wavs = {f.name: f.stat().st_mtime for f in out_dir.glob("seg_*.wav")}

    def _disk_beat() -> None:
        """Wav mới / mtime đổi = vẫn đang chạy (kể cả lúc infer_batch im lặng)."""
        try:
            for f in out_dir.glob("seg_*.wav"):
                mt = f.stat().st_mtime
                if f.name not in known_wavs or mt > known_wavs[f.name]:
                    known_wavs[f.name] = mt
                    last_beat[0] = time.time()
        except Exception:  # noqa: BLE001
            pass

    def _reader() -> None:
        assert p.stdout is not None
        for line in p.stdout:
            line = line.rstrip()
            tail.append(line)
            last_beat[0] = time.time()
            s = line.strip()
            if s.startswith("prog "):
                try:
                    cur = int(s.split()[1].split("/")[0])
                except Exception:  # noqa: BLE001
                    cur = 0
                if on_prog:
                    try:
                        on_prog(cur)
                    except Exception as e:  # noqa: BLE001
                        print(f"[{engine}] on_prog: {e}", flush=True)
            elif s.startswith(("ERR", "DONE", "loading", "vieneu ready", "cuda mem",
                               "WARN", "enroll", "cpu_threads", "spawn")):
                print(f"[{engine}] {s}", flush=True)
                # Đẩy trạng thái load lên UI (tránh kẹt chữ starting)
                if on_prog and s.startswith(("loading", "enroll", "vieneu ready", "cpu_threads")):
                    try:
                        # truyền phase thật (không gộp hết thành loading/enroll)
                        on_prog(-1, s.split()[0] if s else "loading")
                    except TypeError:
                        try:
                            on_prog(-1)
                        except Exception:  # noqa: BLE001
                            pass
                    except Exception:  # noqa: BLE001
                        pass

    th = threading.Thread(target=_reader, name=f"{engine}-out", daemon=True)
    th.start()
    t0 = time.time()
    saw_output = [False]
    # wrap last_beat updates already set saw via reader — track first stdout
    _orig_beat = last_beat[0]

    while th.is_alive():
        th.join(timeout=5.0)
        _disk_beat()
        # Chưa có stdout lần nào: dùng boot_sec (load model). Sau đó dùng stall_sec.
        limit = boot_sec if (time.time() - t0 < boot_sec and last_beat[0] == _orig_beat) else stall_sec
        # nếu đã có beat mới (stdout/wav) -> luôn stall_sec
        if last_beat[0] != _orig_beat:
            limit = stall_sec
        if time.time() - last_beat[0] > limit:
            stalled[0] = True
            print(f"[{engine}] TREO: khong tien do >{limit}s -> kill worker", flush=True)
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
            break
    th.join(timeout=10.0)
    try:
        p.wait(timeout=15)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass
    if stalled[0]:
        detail = "\n".join(tail)
        raise RuntimeError(
            f"{engine} treo (không tiến độ {stall_sec}s). Đã kill worker — bấm Tiếp tục để resume.\n"
            f"{detail[-400:]}"
        )
    if p.returncode not in (0, None):
        detail = "\n".join(tail)
        print(f"[{engine}] === LỖI worker (mã {p.returncode}), 25 dòng cuối ===\n{detail}")
        raise RuntimeError(f"{engine} lỗi (mã {p.returncode}): {(tail[-1] if tail else '')[:200]}")


def synth_one(engine: str, voice: str, text: str, out_path: str | Path,
              speed: float = 1.0) -> Path:
    """Đọc 1 câu bằng engine+giọng -> ghi ra out_path (dùng cho nghe thử / đọc lại 1 clip)."""
    if engine not in ENGINE_CFG:
        raise RuntimeError(f"Engine '{engine}' không hỗ trợ.")
    vr = voice_ref_text(voice)
    if not vr:
        raise RuntimeError(f"Chưa có giọng clone '{voice}'.")
    ref, ref_text = vr
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.parent / f"_one_{engine}"
    tmp.mkdir(parents=True, exist_ok=True)
    _run_worker(engine, ref, ref_text, tmp, speed, [[0, text]])
    src = tmp / "seg_0000.wav"
    if not src.exists():
        raise RuntimeError(f"{engine} không tạo được audio.")
    if out_path.exists():
        out_path.unlink()
    src.replace(out_path)
    # VieNeu worker không đổi tốc độ — atempo sau synth (giống batch).
    _apply_speed(out_path, float(speed or 1.0))
    return out_path


def resynth_clip(work_dir: str | Path, i: int, engine: str, voice: str,
                 speed: float = 1.0) -> dict:
    """Đọc lại DUY NHẤT câu index i -> tts/seg_i.wav + vá tts.json. Trả entry."""
    work_dir = Path(work_dir).resolve()
    segments = json.loads((work_dir / "script.json").read_text(encoding="utf-8"))
    seg = next((s for s in segments if s["i"] == i), None)
    if not seg or not seg.get("vi"):
        raise RuntimeError(f"Câu {i} không có bản dịch.")
    out = work_dir / "tts" / f"seg_{i:04d}.wav"
    synth_one(engine, voice, seg["vi"], out, speed)
    entry = {"i": i, "start": seg["start"], "end": seg["end"], "vi": seg["vi"],
             "audio": str(out), "dur": _dur(out), "engine": engine}
    tts_path = work_dir / "tts.json"
    data = json.loads(tts_path.read_text(encoding="utf-8")) if tts_path.exists() else []
    data = [e for e in data if e.get("i") != i] + [entry]
    data.sort(key=lambda e: e["i"])
    tts_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def _norm_txt(s: str) -> str:
    """Chuẩn hoá để so khớp: thường hoá, bỏ dấu câu, gộp khoảng trắng."""
    import re
    s = (s or "").lower()
    s = re.sub(r"[^0-9a-zàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, _norm_txt(a), _norm_txt(b)).ratio()


_WH_QA = None


def _asr_check(clips_texts: list, device: str = None):
    """Nghe ngược list [(wav_path, expected_vi)] -> [(score, transcript)]. Whisper small."""
    global _WH_QA
    from faster_whisper import WhisperModel
    dev = device or os.getenv("QA_ASR_DEVICE", "cuda")
    if _WH_QA is None:
        ct = "int8" if dev == "cpu" else "int8_float16"
        _WH_QA = WhisperModel(os.getenv("QA_ASR_MODEL", "small"), device=dev, compute_type=ct)
    out = []
    for wav, exp in clips_texts:
        try:
            segs, _ = _WH_QA.transcribe(str(wav), language="vi", beam_size=1)
            tr = " ".join(s.text.strip() for s in segs).strip()
            out.append((_similar(tr, exp), tr))
        except Exception:  # noqa: BLE001
            out.append((1.0, ""))    # lỗi ASR -> coi như ổn (không phá)
    return out


def verify_and_fix(work_dir, engine: str, voice: str, speed: float = 1.0,
                   min_score: float = 0.55, max_retries: int = 2) -> dict:
    """TỰ KIỂM & SỬA: ASR nghe ngược từng clip, câu nào lệch nhiều so với bản dịch
    (rớt/sai chữ) hoặc rỗng -> tự gen lại (theo lô), lặp tối đa max_retries.
    'méo/rè' KHÔNG bắt ở đây (do chất giọng, gen lại không đổi) — dùng _cleanup_audio."""
    work_dir = Path(work_dir).resolve()
    m = Manifest(work_dir)
    segments = json.loads((work_dir / "script.json").read_text(encoding="utf-8"))
    tts_path = work_dir / "tts.json"
    clips = json.loads(tts_path.read_text(encoding="utf-8"))
    by_i = {c["i"]: c for c in clips}
    vi_by_i = {s["i"]: s["vi"] for s in segments if s.get("vi")}
    vr = voice_ref_text(voice)
    if not vr:
        raise RuntimeError(f"Chưa có giọng '{voice}'.")
    ref, ref_text = vr
    out_dir = work_dir / "tts"

    # 1) chấm điểm toàn bộ
    targets = [(c["i"], Path(c["audio"]), vi_by_i.get(c["i"], "")) for c in clips
               if c.get("audio") and vi_by_i.get(c["i"])]
    scored = _asr_check([(p, t) for _, p, t in targets])
    bad = [(i, vi_for_tts(vi_by_i[i])) for (i, _p, _t), (sc, _tr) in zip(targets, scored) if sc < min_score]
    n_total = len(targets)
    print(f"[QA] {engine}/{voice}: {len(bad)}/{n_total} câu nghi lỗi (điểm < {min_score})")

    fixed = 0
    for attempt in range(max_retries):
        if not bad:
            break
        print(f"[QA] lượt {attempt+1}: gen lại {len(bad)} câu...")
        _run_worker(engine, ref, ref_text, out_dir, 1.0, [[i, vi] for i, vi in bad])
        recheck = []
        for i, vi in bad:
            f = out_dir / f"seg_{i:04d}.wav"
            if f.exists():
                _cleanup_audio(f); _apply_speed(f, speed)
                recheck.append((i, f, vi))
        res = _asr_check([(p, t) for _, p, t in recheck])
        still = []
        for (i, f, vi), (sc, _tr) in zip(recheck, res):
            if sc < min_score:
                still.append((i, vi))
            else:
                fixed += 1
                by_i[i]["dur"] = _dur(f)     # cập nhật độ dài mới
        bad = still

    tts_path.write_text(json.dumps([by_i[c["i"]] for c in clips], ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[QA] xong: sửa được {fixed} câu, còn {len(bad)} câu vẫn lệch (có thể do ASR/tên riêng).")
    return {"n_total": n_total, "n_fixed": fixed, "n_remaining": len(bad),
            "remaining": [i for i, _ in bad]}


def _roughness(path: Path) -> float:
    """Độ 'nhám/méo' của clip: HF-ratio ở các khung CÓ CAO ĐỘ (voiced) — loại âm gió
    (s/ch/ph unvoiced) để không báo nhầm. Glitch = khung voiced mà nhiễu -> điểm cao."""
    import numpy as np
    import soundfile as sf
    a, sr = sf.read(str(path))
    if getattr(a, "ndim", 1) > 1:
        a = a.mean(axis=1)
    a = a.astype("float32")
    win = int(sr * 0.025); hop = int(sr * 0.010); n = 1024
    lo, hi = int(sr / 400), int(sr / 70)          # lag ứng F0 70–400Hz
    fr = np.fft.rfftfreq(n, 1 / sr); hann = np.hanning(win)
    vals = []
    for i in range(0, len(a) - win, hop):
        seg = a[i:i + win]
        if np.sqrt(np.mean(seg ** 2)) < 0.01:
            continue
        ac = np.correlate(seg, seg, "full")[win - 1:]
        if hi >= len(ac):
            continue
        voiced = (ac[lo:hi].max() / (ac[0] + 1e-9)) > 0.4
        if not voiced:                            # bỏ khung âm gió/không cao độ
            continue
        S = np.abs(np.fft.rfft(seg * hann, n)) ** 2
        vals.append(S[fr > 6000].sum() / (S.sum() + 1e-9))
    return float(np.percentile(vals, 90)) if vals else 0.0


def fix_glitches(work_dir, engine: str, voice: str, speed: float = 1.0,
                 retries: int = 2, abs_thresh: float = 0.06, rel: float = 1.8) -> dict:
    """TỰ PHÁT HIỆN & SỬA câu bị vỡ/méo (âm): đo độ nhám voiced-frame, câu nào nhám
    bất thường (vượt ngưỡng tuyệt đối HOẶC gấp `rel` lần trung vị của giọng) -> gen lại,
    GIỮ bản sạch nhất trong {gốc + các lần gen}. Không thay nếu gen lại không tốt hơn."""
    import shutil
    work_dir = Path(work_dir).resolve()
    segments = json.loads((work_dir / "script.json").read_text(encoding="utf-8"))
    vi_by_i = {s["i"]: s["vi"] for s in segments if s.get("vi")}
    tts_path = work_dir / "tts.json"
    clips = json.loads(tts_path.read_text(encoding="utf-8"))
    by_i = {c["i"]: c for c in clips}
    vr = voice_ref_text(voice)
    if not vr:
        raise RuntimeError(f"Chưa có giọng '{voice}'.")
    ref, ref_text = vr
    out_dir = work_dir / "tts"

    import numpy as np
    scores = {c["i"]: _roughness(Path(c["audio"])) for c in clips if c.get("audio")}
    med = float(np.median(list(scores.values()))) if scores else 0.0
    thresh = max(abs_thresh, med * rel)
    bad = sorted(i for i, s in scores.items() if s > thresh and i in vi_by_i)
    print(f"[fix] {engine}/{voice}: trung vị nhám={med:.3f}, ngưỡng={thresh:.3f} -> "
          f"{len(bad)}/{len(scores)} câu nghi vỡ/méo")

    bak = work_dir / "_glitch_bak"; bak.mkdir(exist_ok=True)
    best = {i: (scores[i], Path(by_i[i]["audio"])) for i in bad}
    fixed = 0
    for attempt in range(retries):
        todo = [i for i in bad if best[i][0] > thresh]
        if not todo:
            break
        for i in todo:                             # lưu bản hiện tại trước khi gen đè
            shutil.copyfile(out_dir / f"seg_{i:04d}.wav", bak / f"seg_{i:04d}_a{attempt}.wav")
        print(f"[fix] lượt {attempt+1}: gen lại {len(todo)} câu, giữ bản sạch nhất...")
        _run_worker(engine, ref, ref_text, out_dir, 1.0, [[i, vi_for_tts(vi_by_i[i])] for i in todo])
        for i in todo:
            f = out_dir / f"seg_{i:04d}.wav"
            if not f.exists():
                continue
            _cleanup_audio(f); _apply_speed(f, speed)
            s = _roughness(f)
            if s < best[i][0]:                     # bản mới sạch hơn -> giữ
                best[i] = (s, f)
            else:                                  # không hơn -> khôi phục bản tốt nhất cũ
                shutil.copyfile(bak / f"seg_{i:04d}_a{attempt}.wav", f)
    for i in bad:
        f = out_dir / f"seg_{i:04d}.wav"
        by_i[i]["dur"] = _dur(f)
        if best[i][0] < scores[i] - 0.005:
            fixed += 1
    shutil.rmtree(bak, ignore_errors=True)
    tts_path.write_text(json.dumps([by_i[c["i"]] for c in clips], ensure_ascii=False, indent=2),
                        encoding="utf-8")
    remain = [i for i in bad if best[i][0] > thresh]
    print(f"[fix] xong: cải thiện {fixed} câu, còn {len(remain)} câu vẫn nhám (giọng vốn gắt?).")
    return {"n_bad": len(bad), "n_fixed": fixed, "n_remaining": len(remain), "remaining": remain}


def _prog(m, n_done, total, engine, phase, voice: str | None = None, speed: float | None = None):
    pct = round((n_done / total * 100) if total else 0, 1)
    st = dict(m.data.get("stages", {}).get(STAGE) or {})
    st.update({"status": "running", "progress": min(pct, 99.0),
               "n_done": n_done, "n_total": total,
               "n_done": n_done, "n_total": total,
               "cur_text": phase, "cur_text": phase, "engine": engine,
               "tts_engine": engine, "updated_at": time.time()})
    if voice:
        st["voice"] = voice
    if speed is not None:
        st["speed"] = speed
    m.data.setdefault("stages", {})[STAGE] = st
    js.persist(m)


def _finalize(m, segments, done, tts_path, out_dir, engine, voice, speed, keys=None):
    out_dir = Path(out_dir)
    # Vá nốt câu còn thiếu (trivial / wav hỏng) trước khi mark_done
    for s in segments:
        if not s.get("vi") or s["i"] in done:
            continue
        if _is_trivial_vi(s.get("vi")):
            done[s["i"]] = _silence_entry(s, out_dir, engine)
            continue
        f = out_dir / f"seg_{s['i']:04d}.wav"
        if f.exists() and (f.stat().st_size < 1000 or _dur(f) <= 0.1):
            done[s["i"]] = _silence_entry(s, out_dir, engine)
        elif not f.exists():
            # thiếu file thật sự — vẫn silence để pipeline không kẹt "thiếu 1"
            done[s["i"]] = _silence_entry(s, out_dir, engine)
            print(f"[tts] thiếu seg_{s['i']:04d}.wav -> silence", flush=True)

    _stamp_keys(done, keys)
    ordered = [done[s["i"]] for s in segments if s["i"] in done]
    print(f"[tts] ghi tts.json ({len(ordered)} câu)...", flush=True)
    tts_path.write_text(json.dumps(ordered, ensure_ascii=False), encoding="utf-8")
    n_missing = sum(1 for s in segments if s.get("vi") and s["i"] not in done)
    n_silence = sum(1 for e in done.values() if e.get("silence"))
    result = m.mark_done(STAGE, tts_path=str(tts_path), tts_dir=str(out_dir),
                         engine=engine, voice=voice, speed=speed,
                         n_clips=len(done), n_missing=n_missing,
                         n_silence=n_silence)
    try:
        extra = ""
        if n_silence:
            extra += f", {n_silence} silence"
        if n_missing:
            extra += f" (con {n_missing} thieu)"
        print(f"[ok] {engine} xong: {len(done)} cau, giong {voice}{extra}")
    except Exception:  # noqa: BLE001  (console cp1252 -> khong duoc lam sap stage da done)
        pass
    return result
