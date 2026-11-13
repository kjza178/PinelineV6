"""
asr_vad_utils.py — VAD kwargs FunASR thật + pad/overlap khi cắt đoạn.

FunASR fsmn-vad (1.4.x) dùng tên khác Silero/Whisper:
  max_end_silence_time, max_start_silence_time,
  lookback_time_start_point, lookahead_time_end_point,
  max_single_segment_time, …

Không có generate(..., overlap=10) — overlap tự làm khi slice.
"""
from __future__ import annotations

import os
from typing import Callable, Iterable

# Ít cắt aggressive hơn default: cần im lặng vừa phải để khép đoạn,
# tránh khối quá dài (>12s) khiến model ASR autoregressive (Nano/LLM) kẹt loop lặp câu.
DEFAULT_VAD_KWARGS: dict = {
    "max_single_segment_time": 12000, # ms (12s) — tránh VAD gom cả bài nói dài
    "max_end_silence_time": 400,       # 400ms tĩnh ngắt đoạn dứt khoát
    "max_start_silence_time": 3000,
    "lookback_time_start_point": 200,  # nới đầu đoạn vừa đủ
    "lookahead_time_end_point": 150,   # nới cuối đoạn vừa đủ
}

# Pad/overlap khi ASR từng slice (giây) — tránh mất từ ở ranh giới
DEFAULT_PAD_S = float(os.environ.get("ASR_VAD_PAD_S", "0.35"))
DEFAULT_OVERLAP_S = float(os.environ.get("ASR_VAD_OVERLAP_S", "0.50"))
DEFAULT_MERGE_GAP_S = float(os.environ.get("ASR_VAD_MERGE_GAP_S", "0.25"))
DEFAULT_MAX_SEGMENT_S = float(os.environ.get("ASR_VAD_MAX_SEGMENT_S", "12.0"))


def vad_kwargs_from_env(base: dict | None = None) -> dict:
    """Cho phép override qua env ASR_VAD_* (ms)."""
    kw = dict(base or DEFAULT_VAD_KWARGS)
    mapping = {
        "ASR_VAD_MAX_SINGLE_SEGMENT_MS": "max_single_segment_time",
        "ASR_VAD_MAX_END_SILENCE_MS": "max_end_silence_time",
        "ASR_VAD_MAX_START_SILENCE_MS": "max_start_silence_time",
        "ASR_VAD_LOOKBACK_MS": "lookback_time_start_point",
        "ASR_VAD_LOOKAHEAD_MS": "lookahead_time_end_point",
    }
    for env, key in mapping.items():
        v = os.environ.get(env)
        if v is None or v == "":
            continue
        try:
            kw[key] = int(v)
        except ValueError:
            pass
    return kw


def parse_vad_ranges(vad_res, ms_to_s: Callable[[object], float]) -> list[tuple[float, float]]:
    """FunASR VAD generate → [(start_s, end_s), ...]."""
    ranges: list[tuple[float, float]] = []
    if not vad_res:
        return ranges
    item = vad_res[0] if isinstance(vad_res, list) else vad_res
    val = item.get("value") if isinstance(item, dict) else None
    if not isinstance(val, list):
        return ranges
    for pair in val:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            st, en = ms_to_s(pair[0]), ms_to_s(pair[1])
            if en - st >= 0.12:
                ranges.append((st, en))
    return ranges


def merge_close_ranges(
    ranges: Iterable[tuple[float, float]],
    gap_s: float = DEFAULT_MERGE_GAP_S,
    max_dur_s: float = DEFAULT_MAX_SEGMENT_S,
) -> list[tuple[float, float]]:
    """Gộp đoạn kề nhau (khoảng hở < gap_s), nhưng không gộp nếu tổng độ dài vượt max_dur_s."""
    rs = sorted((float(a), float(b)) for a, b in ranges if b > a)
    if not rs:
        return []
    out = [list(rs[0])]
    for st, en in rs[1:]:
        if st <= out[-1][1] + gap_s and (en - out[-1][0]) <= max_dur_s:
            out[-1][1] = max(out[-1][1], en)
        else:
            out.append([st, en])
    # Nếu có bất kỳ đoạn nào vẫn > max_dur_s * 1.5, chẻ nhỏ để bảo vệ model ASR khỏi loop
    split_out: list[tuple[float, float]] = []
    for st, en in out:
        dur = en - st
        if dur > max_dur_s * 1.5:
            cur = st
            while cur < en:
                nxt = min(en, cur + max_dur_s)
                if nxt - cur >= 0.15:
                    split_out.append((round(cur, 3), round(nxt, 3)))
                cur = nxt
        else:
            split_out.append((round(st, 3), round(en, 3)))
    return split_out


def pad_ranges(
    ranges: list[tuple[float, float]],
    audio_dur: float,
    pad_s: float = DEFAULT_PAD_S,
    overlap_s: float = DEFAULT_OVERLAP_S,
    max_dur_s: float = DEFAULT_MAX_SEGMENT_S,
) -> list[dict]:
    """
    Mỗi VAD range → slice ASR có pad + overlap với hàng xóm.

    Trả:
      {vad_start, vad_end, slice_start, slice_end}
    Text thuộc [vad_start, vad_end]; audio đọc [slice_start, slice_end].
    """
    if not ranges:
        return []
    merged = merge_close_ranges(ranges, gap_s=DEFAULT_MERGE_GAP_S, max_dur_s=max_dur_s)
    dur = max(float(audio_dur), 0.0)
    pad = max(0.0, float(pad_s))
    ov = max(0.0, float(overlap_s))
    out: list[dict] = []
    n = len(merged)
    for i, (st, en) in enumerate(merged):
        # pad + thêm overlap nửa phía hàng xóm
        left = pad + (ov if i > 0 else 0.0)
        right = pad + (ov if i + 1 < n else 0.0)
        ss = max(0.0, st - left)
        ee = min(dur, en + right) if dur > 0 else en + right
        if ee - ss < 0.12:
            continue
        out.append({
            "vad_start": round(st, 3),
            "vad_end": round(en, 3),
            "slice_start": round(ss, 3),
            "slice_end": round(ee, 3),
        })
    return out


def shift_char_timeline(
    chars: list[dict],
    offset_s: float,
    keep_start: float | None = None,
    keep_end: float | None = None,
) -> list[dict]:
    """Cộng offset (slice_start) vào char-ts; optional cắt ngoài cửa sổ VAD gốc."""
    out: list[dict] = []
    for c in chars:
        st = float(c["start"]) + offset_s
        en = float(c["end"]) + offset_s
        mid = (st + en) / 2.0
        if keep_start is not None and mid < keep_start - 0.05:
            continue
        if keep_end is not None and mid > keep_end + 0.05:
            continue
        out.append({"ch": c["ch"], "start": round(st, 3), "end": round(en, 3)})
    return out
