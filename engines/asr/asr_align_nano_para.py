"""
asr_align_nano_para.py — Hybrid A: chữ Fun-ASR-Nano + timestamp Paraformer (char-ts).

Đầu vào (sidecar, không đụng asr.json):
  asr.fun_nano.json              — text tốt (VAD mốc thô)
  asr.paraformer_zh.chars.json   — [{ch, start, end}, ...] giây

Đầu ra:
  asr.aligned.json
  sub.zh.aligned.srt

Thuật toán: DTW / sequence align trên chuỗi chữ đã chuẩn hoá (bỏ punct),
rồi gán start/end từ char-ts Para cho từng câu Nano.

Chạy:
  python asr_align_nano_para.py work/<job>
  python asr_align_nano_para.py work/<job> --nano asr.fun_nano.json --chars asr.paraformer_zh.chars.json
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[2])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import json
import re
import time
from pathlib import Path

from engines.subs import to_srt, split_long_segments, sub_split_cfg

_PUNCT_RE = re.compile(r"[^\u4e00-\u9fff0-9a-zA-Z]+")


def _norm_chars(text: str) -> list[str]:
    return list(_PUNCT_RE.sub("", text or ""))


def _ms_pair_to_s(pair) -> tuple[float, float]:
    """FunASR Paraformer timestamp luôn là milliseconds (vd. [8530, 8730] = 8.53s)."""
    a, b = float(pair[0]) / 1000.0, float(pair[1]) / 1000.0
    return round(a, 3), round(b, 3)


def extract_char_timeline(res) -> list[dict]:
    """Từ raw FunASR generate() → [{ch, start, end}, ...] (giây)."""
    out: list[dict] = []
    if not res:
        return out
    items = res if isinstance(res, list) else [res]
    for item in items:
        if not isinstance(item, dict):
            continue
        # Ưu tiên sentence_info có timestamp từng câu
        sents = item.get("sentence_info") or item.get("sentence_list")
        if isinstance(sents, list) and sents:
            for s in sents:
                if not isinstance(s, dict):
                    continue
                text = (s.get("text") or s.get("sentence") or "").replace(" ", "")
                ts = s.get("timestamp") or s.get("timestamps")
                out.extend(_zip_text_ts(text, ts))
            if out:
                continue
        text = (item.get("text") or "").replace(" ", "").replace("\n", "")
        ts = item.get("timestamp") or item.get("timestamps")
        out.extend(_zip_text_ts(text, ts))
    return out


def _zip_text_ts(text: str, ts) -> list[dict]:
    if not text or not isinstance(ts, list) or not ts:
        return []
    chars = list(text)
    # chỉ lấy cặp [start,end]
    pairs = [p for p in ts if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not pairs:
        return []
    n = min(len(chars), len(pairs))
    # lệch lớn: trải đều thời gian toàn đoạn theo số chữ
    if abs(len(chars) - len(pairs)) > max(5, len(chars) // 10):
        st, _ = _ms_pair_to_s(pairs[0])
        _, en = _ms_pair_to_s(pairs[-1])
        dur = max(en - st, 0.05)
        step = dur / max(len(chars), 1)
        return [
            {"ch": ch, "start": round(st + i * step, 3),
             "end": round(st + (i + 1) * step, 3)}
            for i, ch in enumerate(chars)
        ]
    rows = []
    for i in range(n):
        a, b = _ms_pair_to_s(pairs[i])
        if b <= a:
            b = a + 0.04
        rows.append({"ch": chars[i], "start": a, "end": b})
    # thừa chữ: trải phần còn lại sau mốc cuối
    if len(chars) > n:
        last_en = rows[-1]["end"] if rows else 0.0
        for ch in chars[n:]:
            rows.append({"ch": ch, "start": last_en, "end": round(last_en + 0.05, 3)})
            last_en = rows[-1]["end"]
    return rows


def chars_from_segments_spread(segs: list[dict]) -> list[dict]:
    """Fallback khi không có char-ts: trải đều ký tự trong từng câu Para."""
    out: list[dict] = []
    for s in segs:
        text = _PUNCT_RE.sub("", s.get("text") or "")
        # giữ cả punct để đủ dài? align dùng norm — dùng full strip punct
        raw = (s.get("text") or "").replace(" ", "")
        chars = list(raw) if raw else []
        if not chars:
            continue
        st = float(s["start"])
        en = float(s["end"])
        step = max(en - st, 0.05) / len(chars)
        for i, ch in enumerate(chars):
            out.append({
                "ch": ch,
                "start": round(st + i * step, 3),
                "end": round(st + (i + 1) * step, 3),
            })
    return out


def _dtw_align(nano_chars: list[str], para_chars: list[str]) -> list[int]:
    """
    DTW: trả map nano_idx -> para_idx (độ dài = len(nano_chars)).
    Chi phí: 0 nếu cùng chữ, 1 nếu khác. Cho phép insert/delete.
    """
    n, m = len(nano_chars), len(para_chars)
    if n == 0:
        return []
    if m == 0:
        return [0] * n
    # Giới hạn bộ nhớ: nếu quá dài, chia cửa sổ theo tỉ lệ
    if n * m > 8_000_000:
        return _window_map(nano_chars, para_chars)

    INF = 10**9
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    bt = [[0] * (m + 1) for _ in range(n + 1)]  # 0 diag, 1 up (del nano), 2 left (ins para)
    dp[0][0] = 0
    for i in range(1, n + 1):
        dp[i][0] = i
        bt[i][0] = 1
    for j in range(1, m + 1):
        dp[0][j] = j
        bt[0][j] = 2
    for i in range(1, n + 1):
        nc = nano_chars[i - 1]
        row, prev = dp[i], dp[i - 1]
        for j in range(1, m + 1):
            cost = 0 if nc == para_chars[j - 1] else 1
            d = prev[j - 1] + cost
            u = prev[j] + 1
            l = row[j - 1] + 1
            if d <= u and d <= l:
                row[j] = d
                bt[i][j] = 0
            elif u <= l:
                row[j] = u
                bt[i][j] = 1
            else:
                row[j] = l
                bt[i][j] = 2
    # backtrack
    i, j = n, m
    mapping = [-1] * n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and bt[i][j] == 0:
            mapping[i - 1] = j - 1
            i -= 1
            j -= 1
        elif i > 0 and (j == 0 or bt[i][j] == 1):
            mapping[i - 1] = max(j - 1, 0)
            i -= 1
        else:
            j -= 1
    # fill gaps
    last = 0
    for k in range(n):
        if mapping[k] < 0:
            mapping[k] = last
        else:
            last = mapping[k]
    return mapping


def _window_map(nano_chars: list[str], para_chars: list[str]) -> list[int]:
    """Map tỉ lệ + chỉnh local khi quá dài cho full DTW."""
    n, m = len(nano_chars), len(para_chars)
    mapping = []
    for i in range(n):
        j = int(i * (m - 1) / max(n - 1, 1)) if n > 1 else 0
        # local search ±32
        best, bj = 1, j
        lo, hi = max(0, j - 32), min(m, j + 33)
        for jj in range(lo, hi):
            c = 0 if nano_chars[i] == para_chars[jj] else 1
            if c < best:
                best, bj = c, jj
                if best == 0:
                    break
        mapping.append(bj)
    # monotonic
    for i in range(1, n):
        if mapping[i] < mapping[i - 1]:
            mapping[i] = mapping[i - 1]
    return mapping


def align_nano_to_para(
    nano_rows: list[dict],
    para_chars: list[dict],
) -> list[dict]:
    """Giữ text Nano; start/end lấy từ char-ts Para đã align."""
    if not nano_rows:
        return []
    if not para_chars:
        # không có timeline → giữ timing Nano
        return [
            {"i": i, "start": float(r["start"]), "end": float(r["end"]),
             "text": r.get("text") or "", "align": "nano_fallback"}
            for i, r in enumerate(nano_rows)
        ]

    # stream Para: chỉ chữ “nội dung” để khớp Nano norm
    para_keep: list[dict] = []
    for c in para_chars:
        ch = c.get("ch") or ""
        if _PUNCT_RE.fullmatch(ch):
            continue
        if not ch or _PUNCT_RE.sub("", ch) == "":
            # punct đơn
            if ch and not re.search(r"[\u4e00-\u9fff0-9a-zA-Z]", ch):
                continue
        nc = _PUNCT_RE.sub("", ch)
        if not nc:
            continue
        para_keep.append({"ch": nc[0], "start": float(c["start"]), "end": float(c["end"])})

    para_str = [c["ch"] for c in para_keep]
    # flatten nano content chars + ownership (seg_idx, local_idx)
    nano_flat: list[str] = []
    owners: list[tuple[int, int]] = []
    seg_norms: list[list[str]] = []
    for si, r in enumerate(nano_rows):
        chars = _norm_chars(r.get("text") or "")
        seg_norms.append(chars)
        for li, ch in enumerate(chars):
            nano_flat.append(ch)
            owners.append((si, li))

    if not nano_flat or not para_str:
        return [
            {"i": i, "start": float(r["start"]), "end": float(r["end"]),
             "text": r.get("text") or "", "align": "empty_stream"}
            for i, r in enumerate(nano_rows)
        ]

    mapping = _dtw_align(nano_flat, para_str)

    # per-seg min/max para index
    seg_range: list[list[int]] = [[10**9, -1] for _ in nano_rows]
    for flat_i, (si, _) in enumerate(owners):
        pj = mapping[flat_i]
        lo, hi = seg_range[si]
        if pj < lo:
            lo = pj
        if pj > hi:
            hi = pj
        seg_range[si] = [lo, hi]

    out: list[dict] = []
    for i, r in enumerate(nano_rows):
        text = (r.get("text") or "").strip()
        lo, hi = seg_range[i]
        if hi < 0 or lo > hi or not seg_norms[i]:
            st, en = float(r["start"]), float(r["end"])
            mode = "nano_fallback"
        else:
            st = para_keep[lo]["start"]
            en = para_keep[hi]["end"]
            mode = "para_char"
            if en <= st:
                en = st + max(0.2, 0.12 * len(seg_norms[i]))
        out.append({
            "i": i,
            "start": round(st, 3),
            "end": round(en, 3),
            "text": text,
            "align": mode,
        })
    # sửa overlap / đi lùi — KHÔNG bịa mốc (monotonic_fix): trả về timing Nano
    for i in range(1, len(out)):
        prev, cur = out[i - 1], out[i]
        if cur["start"] < prev["end"]:
            if cur["start"] < prev["start"]:
                # đảo thứ tự → bỏ Para, lấy lại Nano
                cur["start"] = round(float(nano_rows[i]["start"]), 3)
                cur["end"] = round(float(nano_rows[i]["end"]), 3)
                cur["align"] = "nano_fallback"
                # nếu vẫn đè prev: cắt prev.end
                if cur["start"] < prev["end"]:
                    prev["end"] = round(cur["start"], 3)
                    if prev["end"] <= prev["start"]:
                        prev["end"] = round(prev["start"] + 0.2, 3)
            else:
                # đè nhẹ: chia đôi biên
                mid = (prev["end"] + cur["start"]) / 2
                prev["end"] = round(mid, 3)
                cur["start"] = round(mid, 3)
        if cur["end"] <= cur["start"]:
            cur["end"] = round(cur["start"] + 0.25, 3)
    return out


def run_align(
    work_dir: str | Path,
    nano_name: str = "asr.fun_nano.json",
    chars_name: str = "asr.paraformer_zh.chars.json",
    para_name: str = "asr.paraformer_zh.json",
    out_tag: str = "aligned",
) -> dict:
    work_dir = Path(work_dir)
    nano_path = work_dir / nano_name
    chars_path = work_dir / chars_name
    para_path = work_dir / para_name
    if not nano_path.exists():
        raise FileNotFoundError(f"Thiếu {nano_path}")

    nano = json.loads(nano_path.read_text(encoding="utf-8"))
    para_chars: list[dict] = []
    if chars_path.exists():
        para_chars = json.loads(chars_path.read_text(encoding="utf-8"))
        print(f"[align] chars từ {chars_path.name}: {len(para_chars)}")
    elif para_path.exists():
        para_segs = json.loads(para_path.read_text(encoding="utf-8"))
        para_chars = chars_from_segments_spread(para_segs)
        print(f"[align] fallback spread từ {para_path.name}: {len(para_chars)} chars")
    else:
        print("[align] không có Para — giữ timing Nano")

    t0 = time.time()
    rows = align_nano_to_para(nano, para_chars)
    max_zh, _, max_dur = sub_split_cfg()
    rows = split_long_segments(rows, max_chars=max_zh, key="text", max_dur=max_dur)
    for i, r in enumerate(rows):
        r["i"] = i

    asr_out = work_dir / f"asr.{out_tag}.json"
    srt_out = work_dir / f"sub.zh.{out_tag}.srt"
    asr_out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    srt_out.write_text(to_srt(rows, key="text"), encoding="utf-8")
    n_para = sum(1 for r in rows if r.get("align") == "para_char")
    info = {
        "status": "done",
        "n_segments": len(rows),
        "n_para_char": n_para,
        "asr_path": str(asr_out),
        "srt_path": str(srt_out),
        "sec": round(time.time() - t0, 2),
    }
    (work_dir / f"asr.{out_tag}.meta.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[ok] aligned {len(rows)} câu ({n_para} dùng para_char) -> {asr_out.name}")
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description="Align Nano text + Paraformer char-ts")
    ap.add_argument("work_dir")
    ap.add_argument("--nano", default="asr.fun_nano.json")
    ap.add_argument("--chars", default="asr.paraformer_zh.chars.json")
    ap.add_argument("--para", default="asr.paraformer_zh.json")
    ap.add_argument("--out-tag", default="aligned")
    args = ap.parse_args(argv)
    run_align(args.work_dir, nano_name=args.nano, chars_name=args.chars,
              para_name=args.para, out_tag=args.out_tag)


if __name__ == "__main__":
    main()
