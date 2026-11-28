"""Mức âm lượng khi trộn tiếng (render) — các khoá RENDER_* trong pineline.config.json.

2026-11-15: trang Cài đặt cũ gắn thanh «Âm lượng giọng Việt» vào `voice_vol` của cấu hình batch, nhưng render dùng
`voice_vol` cho GIỌNG TRUNG nền (chỉ chế độ demucs_keep). Âm lượng giọng Việt thật là RENDER_DUB_VOL
(engines/render_fx.dub_volume), nhạc thư viện RENDER_BGM_VOL, nhạc kho / tiếng động của sfx_lib ở engines/sfx_lib.
Module này là một cửa đọc / ghi các mức đó (route mỏng ở app/main.py).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    key: str            # khoá pineline.config.json
    label: str
    default: float
    lo: float
    hi: float
    modes: tuple[str, ...]   # chế độ âm nền dùng mức này ("*" = mọi chế độ có giọng Việt)


LEVELS: dict[str, Level] = {
    "dub": Level("RENDER_DUB_VOL", "Giọng Việt", 1.25, 0.5, 2.0, ("*",)),
    "bgm": Level("RENDER_BGM_VOL", "Nhạc nền thư viện", 0.14, 0.0, 0.6, ("voice_bgm", "duck_bgm")),
    "lib_music": Level("RENDER_LIB_BGM_VOL", "Nhạc kho", 0.25, 0.0, 0.8, ("sfx_lib",)),
    "sfx": Level("RENDER_SFX_VOL", "Tiếng động gốc", 0.62, 0.0, 1.2, ("sfx_lib",)),
}


def _read(lv: Level) -> float:
    from core.config import get
    raw = get(lv.key, "")
    try:
        v = float(raw) if str(raw).strip() else lv.default
    except (TypeError, ValueError):
        print(f"[audio_levels] {lv.key}={raw!r} không phải số → dùng mặc định {lv.default}", flush=True)
        v = lv.default
    return v


def snapshot() -> dict:
    return {name: {"key": lv.key, "label": lv.label, "value": _read(lv), "default": lv.default,
                   "min": lv.lo, "max": lv.hi, "modes": list(lv.modes)} for name, lv in LEVELS.items()}


def validate(patch: dict) -> dict:
    """{tên: số | None} → {khoá RENDER_*: số | None}. None = về mặc định (xoá khoá).
    Sai tên / không phải số / ngoài khoảng → ValueError."""
    if not isinstance(patch, dict):
        raise ValueError("levels phải là object")
    out: dict = {}
    for name, val in patch.items():
        lv = LEVELS.get(name)
        if not lv:
            raise ValueError(f"mức âm lượng lạ: {name}")
        if val is None or val == "":
            out[lv.key] = None
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            raise ValueError(f"{lv.label}: «{val}» không phải số") from None
        if not lv.lo <= f <= lv.hi:
            raise ValueError(f"{lv.label}: {f} ngoài khoảng {lv.lo}–{lv.hi}")
        out[lv.key] = round(f, 3)
    return out


def update(patch: dict) -> dict:
    """Kiểm rồi ghi pineline.config.json (core.config.save_config). Sai → ValueError, không ghi gì."""
    out = validate(patch)
    if out:
        from core.config import save_config
        save_config(out)
    return {"changed": sorted(out)}
