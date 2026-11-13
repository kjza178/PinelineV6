"""
Cấu hình batch dùng chung — 1 nguồn duy nhất cho Auto-pilot, Agent (queue_add),
Telegram (/config /set) và UI (nút «Lưu làm config batch»).
File: work/logs/batch_config.json
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.fileio import atomic_write_text, file_lock

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "work" / "logs" / "batch_config.json"

DEFAULTS: dict[str, Any] = {
    "voice": "v5",
    "speed": 1.0,
    "tts_engine": "vieneu",
    "asr_engine": "nano_fa",
    "audio_mode": "duck_bgm",
    "voice_vol": 0.2,
    "orig_vol": 0.22,
    "translate_mode": "batch",
    "review_backup": "off",
    "ocr": False,
    "fit": True,
    "resolution": "source",
    "zoom": "1.12",
    "lut": True,
    "motion": True,
    "grain": "weak",
    "flip": "",
    "vbitrate": "8M",
    "brand_mode": "off",
    "brand_text": "",
    "brand_logo": "off",
    "frame": "off",
    "render_speed": 1.0,
    "hardsub_blur": True,
}

CHOICES: dict[str, tuple[str, ...]] = {
    "tts_engine": ("vieneu", "omnivoice"),
    "asr_engine": ("nano_fa", "hybrid_fa", "fun_asr_nano", "paraformer_zh", "sensevoice", "firered_asr"),
    "audio_mode": ("duck_bgm", "sfx_lib", "voice_bgm", "voice_only", "duck", "demucs_keep", "demucs_clean"),
    "translate_mode": ("batch", "auto", "oneshot"),
    "review_backup": ("off", "aurora_parallel"),
    "zoom": ("off", "1.10", "1.12", "1.15"),
    "grain": ("off", "weak", "medium", "strong"),
}

LABELS: dict[str, str] = {
    "voice": "Giọng",
    "speed": "Tốc độ",
    "tts_engine": "TTS",
    "asr_engine": "ASR",
    "audio_mode": "Âm nền",
    "voice_vol": "Âm lượng giọng Việt",
    "orig_vol": "Tiếng gốc nền (khi ducking)",
    "translate_mode": "Dịch",
    "review_backup": "REVIEW backup (Aurora parallel)",
    "ocr": "OCR",
    "fit": "Fit",
    "resolution": "Độ phân giải",
    "zoom": "Zoom",
    "lut": "LUT",
    "motion": "Ken Burns + Drift",
    "grain": "Grain",
    "flip": "Lật",
    "vbitrate": "Bitrate (nén)",
    "brand_mode": "Brand (watermark)",
    "brand_text": "Tên kênh",
    "brand_logo": "Logo",
    "frame": "Khung",
    "render_speed": "Tốc độ Render (Lách bản quyền)",
    "hardsub_blur": "Che hardsub (blur/stretch)",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def as_bool(val: Any, default: bool = True) -> bool:
    """Parse bool an toàn — `bool("false")` là True nên không dùng raw bool()."""
    if val is None:
        return default
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("1", "true", "on", "yes", "bật", "bat", "có", "co"):
            return True
        if v in ("0", "false", "off", "no", "tắt", "tat", "không", "khong", ""):
            return False
        return default
    return bool(val)


def _coerce(key: str, val: Any) -> Any:
    if key == "zoom":
        s = "" if val is None else str(val).strip().lower()
        if s in ("off", "none", "0", "false", "no", "1", "1.0", "1.00"):
            return "off"
        try:
            f = float(s)
            if f <= 1.01:
                return "off"
            return f"{f:.2f}"
        except ValueError:
            return "1.12"
    base = DEFAULTS[key]
    if isinstance(base, bool):
        if isinstance(val, str):
            v = val.strip().lower()
            if v in ("1", "true", "on", "yes", "bật", "bat", "có", "co"):
                return True
            if v in ("0", "false", "off", "no", "tắt", "tat", "không", "khong", ""):
                return False
            raise ValueError(f"{key}: cần on/off, nhận {val!r}")
        return bool(val)
    if isinstance(base, float):
        return float(val)
    s = "" if val is None else str(val).strip()
    ch = CHOICES.get(key)
    if ch and s not in ch:
        raise ValueError(f"{key}: chọn 1 trong {', '.join(ch)} (nhận {s!r})")
    return s


_last_good: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    """File hỏng/đang ghi dở → trả bản đọc tốt gần nhất (không rơi về DEFAULTS: Auto-pilot
    sẽ xếp job sai giọng/âm nền)."""
    global _last_good
    out = dict(DEFAULTS)
    if CONFIG_PATH.is_file():
        err = None
        for _ in range(3):
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                break
            except Exception as e:  # noqa: BLE001
                err = e
                time.sleep(0.1)
        else:
            print(f"[batch_config] đọc {CONFIG_PATH} lỗi: {err} — dùng bản đọc tốt gần nhất", flush=True)
            return dict(_last_good) if _last_good else out
        for k, v in (data.get("opts") or {}).items():
            if k in DEFAULTS and v is not None:
                out[k] = v
    _last_good = dict(out)
    return out


def resolve_job_opts(*layers: dict[str, Any] | None, fill_voice: bool = True) -> dict[str, Any]:
    """Opts của 1 job = config batch hiện tại ⊕ từng lớp (lớp sau đè lớp trước, bỏ None/pin_opts).
    fill_voice=False: không lấy giọng từ config batch — job dùng giọng đã lưu của nó, thiếu thì báo lỗi.

    V6 F2: lớp mỏng — logic nằm ở core.job_opts (một nguồn cho UI / hàng chờ / agent).
    Code mới dùng `job_opts.resolve(explicit, pinned, batch)` / `job_opts.for_job(wd, explicit)`."""
    from core import job_opts
    return job_opts.merge(*layers, fill_voice=fill_voice)


def _norm_zoom(v: Any) -> float:
    if v is None:
        v = DEFAULTS["zoom"]
    s = str(v).strip().lower()
    if s in ("off", "none", "0", "false", "no", "1", "1.0", "1.00"):
        return 1.0
    try:
        f = float(s)
        return 1.0 if f <= 1.01 else f
    except ValueError:
        return 1.12


def render_kwargs(o: dict[str, Any]) -> dict[str, Any]:
    """Tham số render (_do_render / MuxReq) từ opts đã resolve. V6 F2: ủy quyền core.job_opts."""
    from core import job_opts
    return job_opts.render_kwargs(o)


def meta() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {"updated_at": None, "updated_by": None}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {"updated_at": data.get("updated_at"), "updated_by": data.get("updated_by")}
    except Exception:  # noqa: BLE001
        return {"updated_at": None, "updated_by": None}


def _write(opts: dict[str, Any], by: str) -> None:
    global _last_good
    atomic_write_text(
        CONFIG_PATH,
        json.dumps({"opts": opts, "updated_at": _now(), "updated_by": by}, ensure_ascii=False, indent=2),
    )
    _last_good = dict(opts)


def update(patch: dict[str, Any], by: str = "api") -> dict[str, Any]:
    """Gộp patch vào config hiện tại. Key lạ / giá trị sai → ValueError."""
    unknown = [k for k in patch if k not in DEFAULTS]
    if unknown:
        raise ValueError(f"Key không hỗ trợ: {', '.join(unknown)}. Hợp lệ: {', '.join(DEFAULTS)}")
    # server (UI) và sidecar (Telegram /set) cùng sửa → đọc-sửa-ghi trong khoá
    with file_lock(CONFIG_PATH):
        cur = load()
        changed: dict[str, Any] = {}
        for k, v in patch.items():
            if v is None:
                continue
            nv = _coerce(k, v)
            if cur.get(k) != nv:
                changed[k] = nv
            cur[k] = nv
        _write(cur, by)
    return {"opts": cur, "changed": changed}


def reset(by: str = "api") -> dict[str, Any]:
    with file_lock(CONFIG_PATH):
        _write(dict(DEFAULTS), by)
    return dict(DEFAULTS)


def parse_kv(text: str) -> dict[str, str]:
    """'audio_mode=voice_bgm orig_vol=0.3 brand_text="Kênh A"' → dict."""
    import shlex
    out: dict[str, str] = {}
    for tok in shlex.split(text or ""):
        if "=" not in tok:
            raise ValueError(f"Sai cú pháp {tok!r} — dùng key=value")
        k, v = tok.split("=", 1)
        out[k.strip()] = v
    return out


def describe(opts: dict[str, Any] | None = None) -> str:
    o = opts or load()
    lines = []
    for k in DEFAULTS:
        v = o.get(k)
        if isinstance(v, bool):
            v = "on" if v else "off"
        elif v == "":
            v = "—"
        lines.append(f"{LABELS.get(k, k)} ({k}) = {v}")
    return "\n".join(lines)


def review_backup_mode(opts: dict | None = None) -> str:
    """off | aurora_parallel — Aurora GPT làm REVIEW (qua REVIEW_MODEL / AURORA_REVIEW_MODEL).

    aurora_parallel: ép model chatgpt + `translate.run_review` fan-out
    `REVIEW_PARALLEL` worker (mặc định 6). Cloud lỗi → fallback local REVIEW_PRESET.
    """
    o = opts or load()
    v = str(o.get("review_backup") or "off").strip().lower()
    return v if v in ("off", "aurora_parallel") else "off"
