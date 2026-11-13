"""Parse SRT / asr.json → list[{i, start, end, text}]."""
from __future__ import annotations

import json
import re
from pathlib import Path

_TS = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    ms = (ms + "000")[:3]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(path: str | Path) -> list[dict]:
    """Đọc file .srt → segments Pineline-compatible."""
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\n\s*\n", raw.strip())
    out: list[dict] = []
    for bi, block in enumerate(blocks):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # dòng 0 có thể là index hoặc timestamp
        ts_i = 0
        idx = bi
        if _TS.search(lines[0]):
            ts_i = 0
        elif len(lines) >= 2 and _TS.search(lines[1]):
            try:
                idx = int(lines[0])
            except ValueError:
                idx = bi
            ts_i = 1
        else:
            continue
        m = _TS.search(lines[ts_i])
        if not m:
            continue
        start = _ts_to_sec(*m.groups()[:4])
        end = _ts_to_sec(*m.groups()[4:])
        text = "\n".join(lines[ts_i + 1 :]).strip()
        if not text:
            continue
        out.append({"i": idx if idx >= 0 else len(out), "start": round(start, 3),
                    "end": round(end, 3), "text": text})
    # reindex tuần tự nếu trùng
    for i, r in enumerate(out):
        r["i"] = i
    return out


def parse_asr_json(path: str | Path) -> list[dict]:
    """Đọc asr.json Pineline → segments."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("asr.json phải là list[{i,start,end,text}]")
    out = []
    for i, r in enumerate(data):
        if not isinstance(r, dict):
            continue
        text = (r.get("text") or r.get("zh") or "").strip()
        if not text:
            continue
        out.append({
            "i": int(r["i"]) if "i" in r else i,
            "start": float(r.get("start") or 0),
            "end": float(r.get("end") or 0),
            "text": text,
        })
    return out


def load_segments(path: str | Path) -> list[dict]:
    """Tự nhận .srt / asr.json / script.json (lấy zh)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    suf = p.suffix.lower()
    if suf == ".srt":
        return parse_srt(p)
    if suf == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list) and data and isinstance(data[0], dict):
            if "zh" in data[0] and "text" not in data[0]:
                # script.json
                return [{
                    "i": int(r.get("i", i)),
                    "start": float(r.get("start") or 0),
                    "end": float(r.get("end") or 0),
                    "text": (r.get("zh") or "").strip(),
                } for i, r in enumerate(data) if (r.get("zh") or "").strip()]
            return parse_asr_json(p)
        raise ValueError(f"JSON không phải list segments: {p}")
    raise ValueError(f"Định dạng không hỗ trợ: {suf} (dùng .srt hoặc .json)")
