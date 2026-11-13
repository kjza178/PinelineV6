"""core/job_opts.py — MỘT nguồn quyết định tuỳ chọn job (V6 F2).

Trước F2 tuỳ chọn job (giọng, âm nền, zoom, brand…) được quyết ở 5 chỗ khác nhau
(`batch_config.resolve_job_opts`, `server._req_audio`, đoạn stamp queue_opts trong `_queue_run_one`,
`batch_config.render_kwargs`, dict `render_kw` dựng tay trong run_all / queue) → cùng 1 job bấm từ UI,
từ hàng chờ hay từ agent có thể ra opts khác nhau. Giờ mọi đường gọi đi qua module này.

Thứ tự ưu tiên (lớp sau đè lớp trước; giá trị `None` KHÔNG đè; `pin_opts` luôn bị bỏ):

    DEFAULTS  <  config batch  <  queue_opts đã ghim của job  <  giá trị đặt TƯỜNG MINH cho lần chạy này

Ngoại lệ có chủ ý: `voice` KHÔNG lấy từ queue_opts ghim — giọng của job lưu ở stage tts
(`_save_tts_voice`, UI «chọn giọng»); queue_opts có thể cũ hơn. Thiếu giọng tường minh → server tự
lấy giọng đã lưu của job (`_resolve_queue_voice` / `_resolve_voice`), như V5.

Thuần: không ghi gì. Đọc duy nhất: `batch_config.load()` (tra thuộc tính lúc gọi → monkeypatch được)
và `manifest.json` (chỉ đọc khoá queue_opts) trong `read_pinned` / `for_job`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from core import batch_config as _bc

DEFAULTS: dict[str, Any] = _bc.DEFAULTS
DROP_KEYS = frozenset({"pin_opts"})
PIN_EXCLUDE = frozenset({"voice"})          # xem docstring module
AUDIO_KEYS = ("audio_mode", "voice_vol", "orig_vol")
TTS_ENGINES = ("vieneu", "omnivoice")       # = server.TTS_ENGINES
TTS_ENGINE_DEFAULT = "vieneu"
ASR_ENGINES = ("nano_fa", "hybrid_fa", "fun_asr_nano", "sensevoice", "paraformer_zh", "firered_asr")
ASR_ENGINE_DEFAULT = "nano_fa"              # = server.ASR_ENGINE_DEFAULT

# Khoá thuộc "yêu cầu chạy" (không phải opts job) — bỏ khi lấy explicit từ request model.
REQ_ONLY_KEYS = frozenset({"job_ids", "skip_done", "opts", "group_id"})

as_bool = _bc.as_bool


# ───────────── đọc ─────────────

def load_batch() -> dict[str, Any]:
    """Config batch hiện tại (DEFAULTS ⊕ work/logs/batch_config.json)."""
    return _bc.load()


def read_pinned(work_dir: Path | str) -> dict[str, Any]:
    """queue_opts đã ghim trong manifest của job (chỉ đọc; lỗi/thiếu → {})."""
    try:
        data = json.loads((Path(work_dir) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    qo = data.get("queue_opts") if isinstance(data, dict) else None
    return dict(qo) if isinstance(qo, dict) else {}


def explicit_from_req(req: Any, *, keys: Iterable[str] | None = None,
                      exclude: Iterable[str] = REQ_ONLY_KEYS) -> dict[str, Any]:
    """Trường người dùng GỬI THẬT trong request (pydantic `model_fields_set`), không lấy mặc định của model.

    Object không có `model_fields_set` (dict / SimpleNamespace cũ) → coi mọi trường là tường minh.
    """
    ex = set(exclude or ())
    wanted = set(keys) if keys is not None else None
    if isinstance(req, Mapping):
        items = dict(req)
    else:
        sent = getattr(req, "model_fields_set", None)
        if sent is None:
            names = wanted if wanted is not None else [k for k in vars(req) if not k.startswith("_")]
            items = {k: getattr(req, k, None) for k in names}
        else:
            items = {k: getattr(req, k, None) for k in sent}
    return {k: v for k, v in items.items()
            if k not in ex and k not in DROP_KEYS and (wanted is None or k in wanted)}


# ───────────── quyết định ─────────────

def _layer(out: dict, src: Mapping | None, *, skip: frozenset = frozenset()) -> None:
    for k, v in (src or {}).items():
        if v is None or k in DROP_KEYS or k in skip:
            continue
        out[k] = v


def merge(*layers: Mapping | None, batch: Mapping | None = None, fill_voice: bool = True) -> dict[str, Any]:
    """DEFAULTS ⊕ batch ⊕ từng lớp theo thứ tự (lớp sau thắng). Ngữ nghĩa cũ của
    `batch_config.resolve_job_opts` (giữ để code/test cũ không vỡ)."""
    out: dict[str, Any] = dict(DEFAULTS)
    _layer(out, load_batch() if batch is None else batch)
    if not fill_voice:
        out["voice"] = None
    for layer in layers:
        _layer(out, layer)
    return out


def resolve(explicit: Mapping | None = None, pinned: Mapping | None = None, batch: Mapping | None = None,
            *, fill_voice: bool = True) -> dict[str, Any]:
    """Opts THÔ của 1 lần chạy: explicit > pinned (trừ voice) > batch > DEFAULTS. None không đè.

    batch=None → đọc `batch_config.load()`. fill_voice=False → không lấy giọng từ batch/DEFAULTS
    (job dùng giọng đã lưu của nó; thiếu thì server báo «chưa chọn giọng»).
    """
    out: dict[str, Any] = dict(DEFAULTS)
    _layer(out, load_batch() if batch is None else batch)
    if not fill_voice:
        out["voice"] = None
    _layer(out, pinned, skip=PIN_EXCLUDE)
    _layer(out, explicit)
    return out


def for_job(work_dir: Path | str, explicit: Mapping | None = None, *, fill_voice: bool = True,
            batch: Mapping | None = None) -> dict[str, Any]:
    """resolve() với queue_opts đọc từ manifest của job + config batch hiện tại."""
    return resolve(explicit, read_pinned(work_dir), batch, fill_voice=fill_voice)


# ───────────── chuẩn hoá kiểu (giống đoạn stamp queue_opts của V5) ─────────────

def clamp_zoom(zoom: Any) -> float:
    """= engines.render_fx.clamp_zoom (chép để module này không phụ thuộc engines)."""
    if zoom is None:
        return 1.12
    if isinstance(zoom, str):
        z_str = zoom.strip().lower()
        if z_str in ("off", "none", "0", "false", "no", "1", "1.0", "1.00"):
            return 1.0
        try:
            zoom = float(z_str)
        except ValueError:
            return 1.12
    try:
        z = float(zoom)
    except (TypeError, ValueError):
        return 1.12
    if z <= 1.01:
        return 1.0
    allowed = (1.0, 1.10, 1.12, 1.15)
    return min(allowed, key=lambda a: abs(a - z))


def _float(v: Any, default: float) -> float:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def speed(o: Mapping) -> float:
    s = _float(o.get("speed"), 1.0)
    return s if s > 0 else 1.0


def normalize(o: Mapping) -> dict[str, Any]:
    """Opts đã resolve → kiểu chuẩn (bool/float/str). Khoá lạ giữ nguyên; pin_opts bỏ.

    Đây chính là bộ được ghim vào manifest.queue_opts khi job chạy (HOLD/QA/resume dùng lại nguyên bộ).
    """
    out = {k: v for k, v in o.items() if k not in DROP_KEYS}
    tts = o.get("tts_engine") or TTS_ENGINE_DEFAULT
    asr = o.get("asr_engine") or ASR_ENGINE_DEFAULT
    out.update({
        "voice": o.get("voice") or None,
        "speed": speed(o),
        "tts_engine": tts if tts in TTS_ENGINES else TTS_ENGINE_DEFAULT,
        "asr_engine": asr if asr in ASR_ENGINES else ASR_ENGINE_DEFAULT,
        "audio_mode": str(o.get("audio_mode") or DEFAULTS["audio_mode"]),
        "voice_vol": _float(o.get("voice_vol"), DEFAULTS["voice_vol"]),
        "orig_vol": _float(o.get("orig_vol"), DEFAULTS["orig_vol"]),
        "ocr": as_bool(o.get("ocr"), False),
        "fit": as_bool(o.get("fit"), True),
        "resolution": str(o.get("resolution") or "source"),
        "translate_mode": str(o.get("translate_mode") or "batch"),
        "zoom": clamp_zoom(o.get("zoom")),
        "motion": as_bool(o.get("motion"), True),
        "lut": as_bool(o.get("lut"), True),
        "grain": str(o.get("grain") or "weak"),
        "flip": str(o.get("flip") or "").strip(),
        "vbitrate": str(o.get("vbitrate") or DEFAULTS["vbitrate"]),
        "hardsub_blur": as_bool(o.get("hardsub_blur"), True),
        "brand_mode": str(o.get("brand_mode") or "off").strip() or "off",
        "brand_text": str(o.get("brand_text") or ""),
        "brand_logo": str(o.get("brand_logo") or "off"),
        "frame": str(o.get("frame") or "off"),
    })
    if "brand" in o:
        out["brand"] = as_bool(o.get("brand"), False)
    return out


# ───────────── đầu ra cho từng chỗ dùng ─────────────

# Bộ "đầu ra" (âm thanh + render) mà run-all / Mux tay ghim vào queue_opts (merge) để agent mux_run,
# resume, run_stage mux sau đó render y hệt lần người dùng bấm. Hàng chờ ghim TOÀN BỘ opts (như V5).
PIN_KEYS = AUDIO_KEYS + ("add_sub", "fit", "resolution", "zoom", "motion", "lut", "grain", "flip",
                         "vbitrate", "hardsub_blur", "brand", "brand_mode", "brand_text", "brand_logo",
                         "frame")


def pin_subset(o: Mapping) -> dict[str, Any]:
    return {k: o[k] for k in PIN_KEYS if k in o and o[k] is not None}


def audio(o: Mapping) -> tuple[str, float, float]:
    """(audio_mode, voice_vol, orig_vol) — mặc định duck_bgm / 0.2 / 0.22 như V5."""
    return (str(o.get("audio_mode") or "duck_bgm"),
            _float(o.get("voice_vol"), 0.2),
            _float(o.get("orig_vol"), 0.22))


def brand_on(o: Mapping) -> bool:
    mode = str(o.get("brand_mode") or "off").strip() or "off"
    return as_bool(o.get("brand"), False) or mode not in ("off", "", "0", "false")


def pipeline_render_kw(o: Mapping) -> dict[str, Any]:
    """dict `render_kw` mà run_all / hàng chờ truyền cho _auto_pipeline / auto-split (zoom đã clamp)."""
    return {
        "flip": str(o.get("flip") or "").strip(),
        "zoom": clamp_zoom(o.get("zoom")),
        "motion": as_bool(o.get("motion"), True),
        "lut": as_bool(o.get("lut"), True),
        "grain": str(o.get("grain") or "weak"),
        "brand": brand_on(o),
        "brand_mode": str(o.get("brand_mode") or "off").strip() or "off",
        "brand_text": str(o.get("brand_text") or ""),
        "brand_logo": str(o.get("brand_logo") or "off"),
        "frame": str(o.get("frame") or "off"),
        "vbitrate": str(o.get("vbitrate") or DEFAULTS["vbitrate"]),
        "hardsub_blur": as_bool(o.get("hardsub_blur"), True),
    }


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


def render_kwargs(o: Mapping) -> dict[str, Any]:
    """Tham số _do_render / MuxReq từ opts đã resolve (= batch_config.render_kwargs của V5, nguyên văn)."""
    brand_mode = str(o.get("brand_mode") or "off").strip() or "off"
    return {
        "audio_mode": str(o.get("audio_mode") or DEFAULTS["audio_mode"]),
        "voice_vol": float(o["voice_vol"]) if o.get("voice_vol") is not None else DEFAULTS["voice_vol"],
        "orig_vol": float(o["orig_vol"]) if o.get("orig_vol") is not None else DEFAULTS["orig_vol"],
        "add_sub": bool(o["add_sub"]) if o.get("add_sub") is not None else True,
        "fit": bool(o.get("fit", True)),
        "resolution": str(o.get("resolution") or "source"),
        "flip": str(o.get("flip") or "").strip(),
        "zoom": _norm_zoom(o.get("zoom")),
        "motion": bool(o.get("motion", True)),
        "lut": bool(o.get("lut", True)),
        "grain": str(o.get("grain") or "weak"),
        "brand": bool(o.get("brand")) or brand_mode not in ("off", "", "0", "false"),
        "brand_mode": brand_mode,
        "brand_text": str(o.get("brand_text") or ""),
        "brand_logo": str(o.get("brand_logo") or "off"),
        "frame": str(o.get("frame") or "off"),
        "vbitrate": str(o.get("vbitrate") or DEFAULTS["vbitrate"]),
        "speed_factor": float(o.get("render_speed") or o.get("speed_factor") or 1.0),
        "hardsub_blur": (
            _bc._coerce("hardsub_blur", o["hardsub_blur"])  # noqa: SLF001 — cùng parser với /set
            if o.get("hardsub_blur") is not None
            else True
        ),
    }
