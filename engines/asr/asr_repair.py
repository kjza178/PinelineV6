"""
asr_repair.py — Hậu kỳ Hybrid: A lấp lỗ + B sửa nghe sai + FA.

A) Gap: Para có thoại, bản hiện tại trống/ít phủ → Nano ASR lại → chèn câu
B) CER: chữ Nano lệch Para mạnh quanh mốc → Nano ASR lại cửa sổ → thay chữ
Rồi ForcedAlign chỉ trên câu mới/sửa.

Đầu vào:
  asr.aligned_fa.json (hoặc --src)
  asr.paraformer_zh.json + .chars.json
  audio.wav

Đầu ra:
  asr.aligned_repaired.json / sub.zh.aligned_repaired.srt
  asr.aligned_repaired.meta.json

Chạy:
  python asr_repair.py work/<job>
  python asr_repair.py work/<job> --detect-only
  python asr_repair.py work/<job> --src asr.aligned_fa.json --max-gaps 80 --max-cer 80
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
import tempfile
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

from engines.asr.asr_fix_align import DEFAULT_PAD_S, _save_asr, step2_forced_align
from engines.asr.asr_vad_utils import DEFAULT_PAD_S as VAD_PAD
from engines.asr.fun_asr_nano import MODEL_ID as NANO_ID, _pick_device, _spread_sentences, _wav_dur
from engines.subs import split_long_segments, sub_split_cfg, _plain_len
from engines.vram import free_gpu_vram

_PUNCT_RE = re.compile(r"[^\u4e00-\u9fff0-9a-zA-Z]+")

# Align lệch time → cùng thoại cách nhau vài chục giây. Gap-fill cũ coi là
# "lỗ" rồi chèn thêm → nhân bản. Cửa sổ này chặn gap giả + dedupe hậu kỳ.
DEFAULT_DUP_WINDOW_S = 90.0
DEFAULT_DUP_MIN_CHARS = 6
DEFAULT_DUP_SIM = 0.72


def _norm(s: str) -> str:
    return _PUNCT_RE.sub("", s or "")


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _text_sim(a: str, b: str) -> float:
    """Độ giống 0..1 (containment + overlap tuần tự) trên chuỗi đã _norm."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return min(len(na), len(nb)) / max(len(na), len(nb))
    # sequential char hit (giống CER hit-rate)
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    j = hit = 0
    for ch in shorter:
        k = longer.find(ch, j)
        if k >= 0:
            hit += 1
            j = k + 1
    return hit / len(shorter)


def _mid(r: dict) -> float:
    return (float(r["start"]) + float(r["end"])) / 2.0


def _already_has_similar(
    base: list[dict],
    text: str,
    around_t: float,
    window_s: float = DEFAULT_DUP_WINDOW_S,
    min_chars: int = DEFAULT_DUP_MIN_CHARS,
    min_sim: float = DEFAULT_DUP_SIM,
) -> bool:
    """True nếu base đã có câu gần nghĩa trong ±window_s (align lệch time)."""
    nt = _norm(text)
    if len(nt) < min_chars:
        return False
    for b in base:
        bt = b.get("text") or ""
        if len(_norm(bt)) < min_chars:
            continue
        if abs(_mid(b) - around_t) > window_s:
            continue
        if _text_sim(text, bt) >= min_sim:
            return True
    return False


def _para_cover(row: dict, para: list[dict]) -> float:
    st, en = float(row["start"]), float(row["end"])
    dur = max(en - st, 1e-6)
    return sum(_overlap(st, en, float(p["start"]), float(p["end"])) for p in para) / dur


def _align_rank(align: str | None) -> int:
    """Ưu tiên giữ khi dedupe (cao hơn = tốt hơn)."""
    a = (align or "").lower()
    if a == "forced_align":
        return 4
    if a == "repair_nano":
        return 3
    if a == "para_char":
        return 2
    if a == "nano_fallback":
        return 1
    return 0


def dedupe_shifted_repeats(
    rows: list[dict],
    para: list[dict] | None = None,
    window_s: float = DEFAULT_DUP_WINDOW_S,
    min_chars: int = DEFAULT_DUP_MIN_CHARS,
    min_sim: float = DEFAULT_DUP_SIM,
) -> tuple[list[dict], int]:
    """
    Xóa nhân bản lệch giờ: cùng/_gần text trong ±window_s → giữ 1 bản.
    Ưu tiên: phủ Para cao hơn → align rank → sớm hơn.
    """
    para = para or []
    if not rows:
        return [], 0
    ordered = sorted(range(len(rows)), key=lambda i: float(rows[i]["start"]))
    keep = [True] * len(rows)
    removed = 0
    for ai, i in enumerate(ordered):
        if not keep[i]:
            continue
        ti = _norm(rows[i].get("text") or "")
        if len(ti) < min_chars:
            continue
        mi = _mid(rows[i])
        for j in ordered[ai + 1 :]:
            if not keep[j]:
                continue
            mj = _mid(rows[j])
            if mj - mi > window_s:
                break
            if _text_sim(rows[i].get("text") or "", rows[j].get("text") or "") < min_sim:
                continue
            # chọn giữ i hoặc j
            ci = _para_cover(rows[i], para) if para else 0.0
            cj = _para_cover(rows[j], para) if para else 0.0
            ri = _align_rank(rows[i].get("align"))
            rj = _align_rank(rows[j].get("align"))
            # giữ bản tốt hơn; mặc định giữ sớm (i)
            drop_i = False
            if cj > ci + 0.08:
                drop_i = True
            elif abs(cj - ci) <= 0.08 and rj > ri:
                drop_i = True
            if drop_i:
                keep[i] = False
                removed += 1
                break
            keep[j] = False
            removed += 1
    out = [rows[i] for i in ordered if keep[i]]
    for i, r in enumerate(out):
        r["i"] = i
    return out, removed


def _merge_windows(
    wins: list[tuple[float, float, str]],
    gap_s: float = 0.8,
) -> list[dict]:
    """wins: (start, end, reason) → merged {start,end,reasons}."""
    if not wins:
        return []
    wins = sorted(wins, key=lambda x: x[0])
    out = [{"start": wins[0][0], "end": wins[0][1], "reasons": {wins[0][2]}}]
    for st, en, reason in wins[1:]:
        if st <= out[-1]["end"] + gap_s:
            out[-1]["end"] = max(out[-1]["end"], en)
            out[-1]["reasons"].add(reason)
        else:
            out.append({"start": st, "end": en, "reasons": {reason}})
    for w in out:
        w["reasons"] = sorted(w["reasons"])
    return out


def detect_gaps(
    base: list[dict],
    para: list[dict],
    min_dur: float = 0.7,
    max_cover_ratio: float = 0.25,
    dup_window_s: float = DEFAULT_DUP_WINDOW_S,
    dup_min_chars: int = DEFAULT_DUP_MIN_CHARS,
    dup_sim: float = DEFAULT_DUP_SIM,
) -> list[tuple[float, float, str]]:
    """Para có đoạn mà base phủ < max_cover_ratio → nghi thiếu câu.

    Bỏ qua gap giả: base đã có câu gần nghĩa trong ±dup_window_s
    (thường do Align đẩy thoại sang mốc khác).
    """
    gaps = []
    for p in para:
        st, en = float(p["start"]), float(p["end"])
        dur = en - st
        if dur < min_dur:
            continue
        text = (p.get("text") or "").strip()
        if len(_norm(text)) < 3:
            continue
        cov = sum(_overlap(st, en, float(b["start"]), float(b["end"])) for b in base)
        if cov / dur > max_cover_ratio:
            continue
        # Align lệch: chữ đã có nhưng đứng sai giờ → không lấp (tránh nhân bản)
        mid = (st + en) / 2.0
        if _already_has_similar(
            base, text, mid,
            window_s=dup_window_s, min_chars=dup_min_chars, min_sim=dup_sim,
        ):
            continue
        gaps.append((st, en, "gap"))
    return gaps


def detect_cer_mismatches(
    base: list[dict],
    para_chars: list[dict],
    miss_thr: float = 0.40,
    min_chars: int = 4,
) -> list[tuple[float, float, str]]:
    """Chữ base khó khớp Para char-stream quanh mốc → nghi nghe sai."""
    para_keep = []
    for c in para_chars:
        nc = _norm(c.get("ch") or "")
        if nc:
            para_keep.append((nc[0], float(c["start"]), float(c["end"])))

    bad = []
    for b in base:
        nt = _norm(b.get("text") or "")
        if len(nt) < min_chars:
            continue
        # bỏ qua đã forced_align gần đây? vẫn check — CER vẫn có thể xấu
        t0, t1 = float(b["start"]) - 0.6, float(b["end"]) + 0.6
        idxs = [i for i, (_, s, e) in enumerate(para_keep) if e >= t0 and s <= t1]
        if not idxs:
            bad.append((float(b["start"]), float(b["end"]), "cer"))
            continue
        window = "".join(para_keep[i][0] for i in idxs)
        if nt in window:
            continue
        j = hit = 0
        for ch in nt:
            k = window.find(ch, j)
            if k >= 0:
                hit += 1
                j = k + 1
        miss = 1 - hit / len(nt)
        if miss >= miss_thr:
            bad.append((float(b["start"]), float(b["end"]), "cer"))
    return bad


def _asr_nano_clip(asr_m, clip, sr: int, language: str = "中文") -> str:
    try:
        res = asr_m.generate(
            input=[clip], cache={}, batch_size=1, language=language, itn=True, fs=sr,
        )
    except Exception:  # noqa: BLE001
        tp = None
        try:
            import soundfile as sf
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tp = tmp.name
            sf.write(tp, clip, sr)
            res = asr_m.generate(
                input=tp, cache={}, batch_size=1, language=language, itn=True,
            )
        except Exception:  # noqa: BLE001
            return ""
        finally:
            if tp:
                Path(tp).unlink(missing_ok=True)
    if not res:
        return ""
    it = res[0] if isinstance(res, list) else res
    if isinstance(it, dict):
        return (it.get("text") or "").strip()
    return ""


def apply_repairs(
    base: list[dict],
    windows: list[dict],
    wav: Path,
    device: str = "cuda:0",
    hub: str = "hf",
    pad_s: float = 0.45,
    language: str = "中文",
) -> tuple[list[dict], list[int], dict]:
    """
    Nano ASR từng cửa sổ:
      - gap: chèn câu mới
      - cer: xóa câu cũ overlap, chèn câu mới
    Trả (rows, indices cần FA, stats).
    """
    import soundfile as sf
    from funasr import AutoModel

    stats = {"n_windows": len(windows), "n_ok": 0, "n_fail": 0, "n_inserted": 0}
    if not windows:
        return [dict(r) for r in base], [], stats

    total = _wav_dur(wav)
    free_gpu_vram("trước Nano repair")
    print(f"[repair] nạp {NANO_ID} · {len(windows)} windows…", flush=True)
    asr_m = AutoModel(
        model=NANO_ID, trust_remote_code=True, device=_pick_device(device),
        hub=hub, disable_update=True,
    )

    rows = [dict(r) for r in base]
    fa_indices: list[int] = []

    with sf.SoundFile(str(wav), "r") as f:
        sr = int(f.samplerate)
        for wi, w in enumerate(windows):
            st, en = float(w["start"]), float(w["end"])
            reasons = set(w.get("reasons") or [])
            ss = max(0.0, st - pad_s)
            ee = min(total, en + pad_s) if total > 0 else en + pad_s
            a0, a1 = int(ss * sr), int(ee * sr)
            f.seek(max(a0, 0))
            clip = f.read(max(a1 - a0, 0), dtype="float32")
            if clip is None or len(clip) < 160:
                stats["n_fail"] += 1
                continue
            if getattr(clip, "ndim", 1) > 1:
                clip = clip.mean(axis=1)
            text = _asr_nano_clip(asr_m, clip, sr, language=language)
            if not text:
                stats["n_fail"] += 1
                continue
            parts = _spread_sentences(text, st, en) if _plain_len(text) > 18 else [
                {"start": st, "end": en, "text": text}
            ]
            # cer/gap: bỏ câu đè cửa sổ thời gian
            if "cer" in reasons or "gap" in reasons:
                rows = [
                    r for r in rows
                    if not (float(r["end"]) > st and float(r["start"]) < en)
                ]
            new_rows = []
            for p in parts:
                new_rows.append({
                    "start": round(float(p["start"]), 3),
                    "end": round(float(p["end"]), 3),
                    "text": p["text"],
                    "align": "repair_nano",
                })
            # gap: thêm xóa bản lệch giờ cùng nghĩa (Align đẩy time)
            if "gap" in reasons:
                drop = set()
                for nr in new_rows:
                    nmid = (float(nr["start"]) + float(nr["end"])) / 2.0
                    for i, r in enumerate(rows):
                        if abs(_mid(r) - nmid) > DEFAULT_DUP_WINDOW_S:
                            continue
                        if _text_sim(nr.get("text") or "", r.get("text") or "") >= DEFAULT_DUP_SIM:
                            drop.add(i)
                if drop:
                    rows = [r for i, r in enumerate(rows) if i not in drop]
            rows.extend(new_rows)
            stats["n_ok"] += 1
            stats["n_inserted"] += len(new_rows)
            if (wi + 1) % 10 == 0 or wi + 1 == len(windows):
                print(f"[repair] {wi+1}/{len(windows)} ok={stats['n_ok']} "
                      f"fail={stats['n_fail']} +{stats['n_inserted']} câu", flush=True)

    rows.sort(key=lambda r: float(r["start"]))
    # dọn overlap
    for i in range(1, len(rows)):
        if rows[i]["start"] < rows[i - 1]["end"]:
            mid = (rows[i - 1]["end"] + rows[i]["start"]) / 2
            rows[i - 1]["end"] = round(mid, 3)
            rows[i]["start"] = round(mid, 3)
        if rows[i]["end"] <= rows[i]["start"]:
            rows[i]["end"] = round(rows[i]["start"] + 0.2, 3)

    max_zh, _, max_dur = sub_split_cfg()
    rows = split_long_segments(rows, max_chars=max_zh, key="text", max_dur=max_dur)
    for i, r in enumerate(rows):
        r["i"] = i
        if r.get("align") == "repair_nano":
            fa_indices.append(i)

    return rows, fa_indices, stats


def run_repair(
    work_dir: str | Path,
    src_name: str = "asr.aligned_fa.json",
    para_name: str = "asr.paraformer_zh.json",
    chars_name: str = "asr.paraformer_zh.chars.json",
    out_tag: str = "aligned_repaired",
    device: str = "cuda:0",
    hub: str = "hf",
    max_gaps: int = 80,
    max_cer: int = 80,
    max_windows: int = 120,
    miss_thr: float = 0.40,
    detect_only: bool = False,
    no_fa: bool = False,
) -> dict:
    work = Path(work_dir)
    wav = work / "audio.wav"
    src = work / src_name
    if not src.exists():
        # fallback chain
        for alt in ("asr.aligned_fix.json", "asr.aligned.json", "asr.json"):
            if (work / alt).exists():
                src = work / alt
                src_name = alt
                break
    if not src.exists():
        raise FileNotFoundError(f"Thiếu ASR nguồn trong {work}")
    if not wav.exists():
        raise FileNotFoundError(wav)

    t0 = time.time()
    base = _load(src)
    para = _load(work / para_name) if (work / para_name).exists() else []
    chars = _load(work / chars_name) if (work / chars_name).exists() else []

    gap_wins = detect_gaps(base, para)[:max_gaps]
    cer_wins = detect_cer_mismatches(base, chars, miss_thr=miss_thr)[:max_cer]
    merged = _merge_windows(gap_wins + cer_wins)[:max_windows]

    print(f"[repair] src={src.name} base={len(base)} "
          f"gaps={len(gap_wins)} cer={len(cer_wins)} merged={len(merged)}", flush=True)

    meta = {
        "status": "detect" if detect_only else "done",
        "src": str(src),
        "n_base": len(base),
        "n_gap_raw": len(gap_wins),
        "n_cer_raw": len(cer_wins),
        "n_windows": len(merged),
        "sample_windows": [
            {"start": round(w["start"], 2), "end": round(w["end"], 2),
             "reasons": w["reasons"]}
            for w in merged[:15]
        ],
    }
    if detect_only:
        (work / f"asr.{out_tag}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[repair] detect-only → {len(merged)} windows", flush=True)
        return meta

    if not merged:
        asr, srt = _save_asr(work, out_tag, base)
        meta.update({
            "n_segments": len(base), "note": "nothing_to_repair",
            "asr_path": str(asr), "srt_path": str(srt),
            "sec": round(time.time() - t0, 1),
            "align_modes": dict(Counter(r.get("align") for r in base)),
        })
        (work / f"asr.{out_tag}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[repair] không có cửa sổ cần sửa — copy qua", flush=True)
        return meta

    rows, fa_idx, nano_stats = apply_repairs(
        base, merged, wav, device=device, hub=hub, pad_s=max(DEFAULT_PAD_S, VAD_PAD),
    )
    meta["nano"] = nano_stats

    if not no_fa and fa_idx:
        print(f"[repair] FA {len(fa_idx)} câu mới/sửa…", flush=True)
        rows, fa_meta = step2_forced_align(
            rows, wav, fa_idx, device=device, max_windows=max_windows,
        )
        meta["fa"] = fa_meta

    # Lớp bảo vệ: xóa nhân bản lệch giờ còn sót (gap giả / CER + chèn)
    rows, n_dedup = dedupe_shifted_repeats(rows, para=para)
    meta["n_dedup_shifted"] = n_dedup
    if n_dedup:
        print(f"[repair] dedupe lệch giờ: -{n_dedup} câu", flush=True)

    asr, srt = _save_asr(work, out_tag, rows)
    meta.update({
        "n_segments": len(rows),
        "asr_path": str(asr),
        "srt_path": str(srt),
        "align_modes": dict(Counter(r.get("align") for r in rows)),
        "sec": round(time.time() - t0, 1),
    })
    (work / f"asr.{out_tag}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] repair → {asr.name} n={len(rows)} modes={meta['align_modes']} "
          f"({meta['sec']}s)", flush=True)
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser(description="Repair ASR: gap fill + CER retry + FA")
    ap.add_argument("work_dir")
    ap.add_argument("--src", default="asr.aligned_fa.json")
    ap.add_argument("--out-tag", default="aligned_repaired")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--hub", default="hf", choices=("hf", "ms"))
    ap.add_argument("--max-gaps", type=int, default=80)
    ap.add_argument("--max-cer", type=int, default=80)
    ap.add_argument("--max-windows", type=int, default=120)
    ap.add_argument("--miss", type=float, default=0.40)
    ap.add_argument("--detect-only", action="store_true")
    ap.add_argument("--no-fa", action="store_true")
    args = ap.parse_args(argv)
    run_repair(
        args.work_dir, src_name=args.src, out_tag=args.out_tag,
        device=args.device, hub=args.hub, max_gaps=args.max_gaps,
        max_cer=args.max_cer, max_windows=args.max_windows,
        miss_thr=args.miss, detect_only=args.detect_only, no_fa=args.no_fa,
    )


if __name__ == "__main__":
    main()
