"""Peek / tóm tắt từng stage — chỉ đọc disk (an toàn)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _mf(work: Path) -> dict:
    p = work / "manifest.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _stage(data: dict, name: str) -> dict:
    return dict((data.get("stages") or {}).get(name) or {})


def _file_info(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        st = path.stat()
        return {"path": str(path), "size": st.st_size, "mtime": st.st_mtime}
    except OSError:
        return None


def _sample_list(path: Path, text_keys: tuple[str, ...], n: int = 3) -> dict:
    info = _file_info(path)
    if not info:
        return {"exists": False}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"exists": True, "error": str(e), **info}
    if not isinstance(rows, list):
        return {"exists": True, "type": type(rows).__name__, "n": None, **info}
    samples = []
    for r in rows[:n]:
        if not isinstance(r, dict):
            continue
        item = {"i": r.get("i")}
        for k in text_keys:
            if k in r:
                v = r.get(k)
                item[k] = (v[:160] if isinstance(v, str) else v)
        samples.append(item)
    missing = 0
    if "vi" in text_keys:
        missing = sum(1 for r in rows if isinstance(r, dict) and not (r.get("vi") or "").strip())
    audio_miss = 0
    if path.name == "tts.json":
        audio_miss = sum(
            1 for r in rows
            if isinstance(r, dict) and not (r.get("audio") or r.get("path"))
        )
    return {
        "exists": True,
        "n": len(rows),
        "missing_vi": missing if "vi" in text_keys else None,
        "missing_audio": audio_miss if path.name == "tts.json" else None,
        "head": samples,
        **info,
    }


def peek_download(work_root: Path, job_id: str) -> dict:
    work = work_root / job_id
    data = _mf(work)
    st = _stage(data, "download")
    src = None
    for pat in ("source.mp4", "source.mkv", "source.webm", "source.mov"):
        p = work / pat
        if p.is_file():
            src = _file_info(p)
            break
    if not src and st.get("video_path"):
        src = _file_info(Path(st["video_path"]))
    return {
        "stage": "download",
        "job_id": job_id,
        "status": st.get("status"),
        "title": data.get("title_vi") or st.get("title"),
        "duration_sec": st.get("duration_sec"),
        "height": st.get("height"),
        "vcodec": st.get("vcodec"),
        "source": src,
        "platform": st.get("platform"),
    }


def peek_asr(work_root: Path, job_id: str) -> dict:
    work = work_root / job_id
    data = _mf(work)
    st = _stage(data, "asr")
    asr = _sample_list(work / "asr.json", ("text", "zh"), n=3)
    sidecars = [p.name for p in work.glob("asr.*.json")]
    return {
        "stage": "asr",
        "job_id": job_id,
        "status": st.get("status"),
        "engine": st.get("asr_engine") or st.get("engine") or st.get("model"),
        "n_segments": st.get("n_segments") or asr.get("n"),
        "language": st.get("language"),
        "cur_text": st.get("cur_text"),
        "progress": st.get("progress"),
        "asr": asr,
        "sidecars": sidecars[:20],
    }


def peek_translate(work_root: Path, job_id: str) -> dict:
    work = work_root / job_id
    data = _mf(work)
    st = _stage(data, "translate")
    script = _sample_list(work / "script.json", ("zh", "vi"), n=3)
    agent = dict(data.get("agent") or {})
    return {
        "stage": "translate",
        "job_id": job_id,
        "status": st.get("status"),
        "model": st.get("model"),
        "mode": st.get("mode"),
        "n_segments": st.get("n_segments") or script.get("n"),
        "n_missing": st.get("n_missing") if st.get("n_missing") is not None else script.get("missing_vi"),
        "cur_text": st.get("cur_text"),
        "script": script,
        "hold_after_translate": agent.get("hold_after_translate"),
        "held_at": agent.get("held_at"),
    }


def peek_tts(work_root: Path, job_id: str) -> dict:
    work = work_root / job_id
    data = _mf(work)
    st = _stage(data, "tts")
    tts = _sample_list(work / "tts.json", ("vi", "audio"), n=3)
    cfg = {}
    cp = work / "tts_config.json"
    if cp.is_file():
        try:
            cfg = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cfg = {}
    return {
        "stage": "tts",
        "job_id": job_id,
        "status": st.get("status"),
        "voice": st.get("voice") or cfg.get("voice"),
        "engine": st.get("tts_engine") or st.get("engine") or cfg.get("engine"),
        "speed": st.get("speed") or cfg.get("speed"),
        "n_clips": st.get("n_clips") or tts.get("n"),
        "n_missing": st.get("n_missing") if st.get("n_missing") is not None else tts.get("missing_audio"),
        "progress": st.get("progress"),
        "cur_text": st.get("cur_text"),
        "tts": tts,
        "dub_fit": _file_info(work / "dub_fit.wav"),
    }


def peek_ocr(work_root: Path, job_id: str) -> dict:
    work = work_root / job_id
    data = _mf(work)
    st = _stage(data, "ocr")
    ocr = _sample_list(work / "ocr.json", ("text", "vi", "zh"), n=3)
    return {
        "stage": "ocr",
        "job_id": job_id,
        "status": st.get("status"),
        "cur_text": st.get("cur_text"),
        "progress": st.get("progress"),
        "ocr": ocr,
    }


def peek_mux(work_root: Path, job_id: str) -> dict:
    work = work_root / job_id
    data = _mf(work)
    st = _stage(data, "mux")
    out = _file_info(work / "output.mp4")
    partial = _file_info(work / "output.partial.mp4")
    keys = (
        "audio_mode", "zoom", "flip", "lut", "grain", "brand", "frame",
        "resolution", "vbitrate", "has_dub", "phase", "progress", "cur_text",
    )
    opts = {k: st.get(k) for k in keys if st.get(k) is not None}
    return {
        "stage": "mux",
        "job_id": job_id,
        "status": st.get("status"),
        "progress": st.get("progress"),
        "phase": st.get("phase"),
        "cur_text": st.get("cur_text"),
        "opts": opts,
        "output": out,
        "partial": partial,
    }


PEEKERS = {
    "download": peek_download,
    "asr": peek_asr,
    "translate": peek_translate,
    "tts": peek_tts,
    "ocr": peek_ocr,
    "mux": peek_mux,
}


def peek_stage(work_root: Path, job_id: str, stage: str) -> dict:
    stage = (stage or "").strip().lower()
    fn = PEEKERS.get(stage)
    if not fn:
        return {"error": f"stage '{stage}' không hỗ trợ", "allowed": list(PEEKERS)}
    return fn(work_root, job_id)


def peek_all(work_root: Path, job_id: str) -> dict:
    return {
        "job_id": job_id,
        "stages": {name: fn(work_root, job_id) for name, fn in PEEKERS.items()},
    }


def peek_style_pack(work_root: Path, job_id: str) -> dict:
    """Pack kịch bản / văn phong (Context Studio + glossary Pineline)."""
    work = work_root / job_id
    out: dict[str, Any] = {"job_id": job_id, "files": {}}

    def _load(name: str, max_chars: int = 4000) -> Any:
        p = work / name
        if not p.is_file():
            return None
        try:
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
            text = json.dumps(data, ensure_ascii=False)
            if len(text) > max_chars:
                return {
                    "truncated": True,
                    "size": p.stat().st_size,
                    "preview": data if not isinstance(data, (list, dict)) else (
                        {k: data[k] for k in list(data)[:12]} if isinstance(data, dict) else data[:8]
                    ),
                }
            return data
        except Exception as e:  # noqa: BLE001
            return {"error": str(e), "size": p.stat().st_size}

    for name in (
        "glossary.json",
        "style_lock.json",
        "qa_flags.json",
        "relation_matrix.json",
        "context_pack.json",
    ):
        out["files"][name] = _load(name)

    # tóm tắt nhanh
    gloss = out["files"].get("glossary.json")
    lock = out["files"].get("style_lock.json")
    qa = out["files"].get("qa_flags.json")
    summary = {
        "has_glossary": bool(gloss) and not (isinstance(gloss, dict) and gloss.get("error")),
        "has_style_lock": bool(lock) and not (isinstance(lock, dict) and lock.get("error")),
        "has_qa_flags": bool(qa) and not (isinstance(qa, dict) and qa.get("error")),
    }
    if isinstance(lock, dict) and not lock.get("error"):
        summary["style_mode"] = lock.get("mode") or (lock.get("preview") or {}).get("mode")
    if isinstance(gloss, dict) and isinstance(gloss.get("terms"), list):
        summary["n_terms"] = len(gloss["terms"])
    elif isinstance(gloss, dict) and isinstance(gloss.get("preview"), dict):
        terms = gloss["preview"].get("terms")
        if isinstance(terms, list):
            summary["n_terms"] = len(terms)
    out["summary"] = summary
    return out


def json_dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
