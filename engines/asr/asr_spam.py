"""asr_spam.py — Chặn ASR nuốt tiếng nền (cười/rên 哈·啊·呃 lặp).

Không dựa vào check tay: sau mỗi lần ghi ASR / promote / compare,
tự detect + rút spam; nếu còn filler thuần thì mượn chữ từ sidecar khác
(cùng cửa sổ thời gian) nếu sidecar đó có lời thật.

Dùng:
    from asr_spam import sanitize_asr_rows, sanitize_asr_file, is_bg_filler_spam

    rows, info = sanitize_asr_rows(rows, work_dir=wd)
    sanitize_asr_file(wd / "asr.fun_nano.json", work_dir=wd)
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[2])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import json
import re
from pathlib import Path
from typing import Any

# Ký tự filler điển hình khi model “ăn” tiếng cười/rên nền
_FILLER_CHARS = set("哈呵嘿嘻呃嗯啊哦噢哼")

# Ngưỡng: số filler tuyệt đối HOẶC tỉ lệ filler trong chữ Hán
_ABS_HA = 8
_ABS_AH = 8
_ABS_EH = 8
_RATIO = 0.55
_MIN_LEN_RATIO = 12  # chỉ xét ratio khi chuỗi đủ dài


def _strip_punct(t: str) -> str:
    return re.sub(r"[\s，。、！？,.!?;；：:\-—…·\"'“”‘’（）()【】\[\]《》<>]", "", t or "")


def filler_stats(text: str) -> dict[str, Any]:
    t = text or ""
    plain = _strip_punct(t)
    ha = t.count("哈") + t.count("呵") + t.count("嘿") + t.count("嘻")
    ah = t.count("啊") + t.count("哦") + t.count("噢")
    eh = t.count("呃") + t.count("嗯") + t.count("哼")
    fill = sum(1 for c in plain if c in _FILLER_CHARS)
    n = len(plain) or 1
    return {
        "ha": ha,
        "ah": ah,
        "eh": eh,
        "fill": fill,
        "plain_len": len(plain),
        "ratio": fill / n,
    }


def is_phrase_loop_spam(text: str) -> bool:
    """True nếu có cụm 2–12 chữ lặp ≥6 lần (vd «区域的»×200) — ASR kẹt loop."""
    t = text or ""
    if len(t) < 24:
        return False
    m = re.search(r"(.{2,12}?)\1{5,}", t)
    if not m:
        return False
    unit = m.group(1)
    # Đếm cụm lặp (không dùng unit{5,} — chỉ lặp ký tự cuối)
    block = re.search(r"(?:" + re.escape(unit) + r"){6,}", t)
    if not block:
        return False
    blen = len(block.group(0))
    return blen >= 24 or (blen / max(len(_strip_punct(t)), 1) >= 0.45)


# Reduplication bình thường — không coi là ASR loop remnant
_OK_DOUBLE = frozenset({
    "天天", "慢慢", "看看", "想想", "试试", "谢谢", "常常", "悄悄",
    "好好", "多多", "少少", "久久", "早早", "晚晚", "真真", "明明",
})


def is_phrase_loop_remnant(text: str) -> bool:
    """Sau clean_zh_spam giữ tối đa 2× unit — vẫn sót «…区域的区域».

    Chỉ bắt cụm dính liền (không dấu phẩy), tránh «不是，不是，不是» thoại thật.
    """
    t = (text or "").strip()
    if len(t) < 8:
        return False
    if "区域的区域" in t or "你们的你们" in t:
        return True
    # Double dính liền (không có ，。 giữa hai unit) — dấu ASR loop đã rút
    m = re.search(
        r"([^\s，。、！？,.!?;；：:]{2,8}?)\1(?:[。！？!,.…]*)$",
        t,
    )
    if m:
        unit = m.group(1)
        if unit not in _OK_DOUBLE and (
            len(unit) >= 3 or unit.endswith(("的", "了", "吗", "呢", "吧"))
        ):
            return True
    # «X的X的» dính liền
    if re.search(r"([^\s，。、！？,]{2,6}的)\1", t):
        return True
    return False


def is_bg_filler_spam(text: str) -> bool:
    """True nếu giống tiếng nền (cười/rên lặp), không phải thoại bình thường."""
    if is_phrase_loop_spam(text) or is_phrase_loop_remnant(text):
        return True
    st = filler_stats(text)
    if st["ha"] >= _ABS_HA or st["ah"] >= _ABS_AH or st["eh"] >= _ABS_EH:
        return True
    if st["plain_len"] >= _MIN_LEN_RATIO and st["ratio"] >= _RATIO:
        return True
    # ``啊，啊，啊，…`` cách dấu phẩy
    if len(re.findall(r"啊[，,]?", text or "")) >= 6:
        return True
    return False


def is_filler_only(text: str) -> bool:
    plain = _strip_punct(text or "")
    if not plain:
        return True
    return all(c in _FILLER_CHARS for c in plain)


def clean_filler_text(text: str, max_rep: int = 3) -> str:
    """Rút chuỗi filler; giữ phần thoại thật nếu có."""
    try:
        from engines.translate import clean_zh_spam
        return clean_zh_spam(text or "", max_rep=max_rep)
    except Exception:  # noqa: BLE001
        t = text or ""
        t = re.sub(r"[哈呵嘿嘻]{4,}", "哈哈哈", t)
        t = re.sub(r"[啊哦噢]{4,}", "啊", t)
        t = re.sub(r"[呃嗯哼]{4,}", "呃", t)
        t = re.sub(r"(?:啊[，,\s]*){4,}", "啊", t)
        return t.strip()


def _load_heal_sources(work_dir: Path, exclude: Path | None = None) -> list[tuple[str, list[dict]]]:
    """Sidecar ASR khác để vá filler (ưu tiên nano → para → sense → firered)."""
    order = [
        "asr.fun_nano.json",
        "asr.paraformer_zh.json",
        "asr.sensevoice.json",
        "asr.hardsub.json",
        "asr.firered.json",
        "asr.aligned_repaired.json",
        "asr.aligned_fa.json",
        "asr.aligned.json",
        "asr.consensus.json",
    ]
    out: list[tuple[str, list[dict]]] = []
    ex = exclude.resolve() if exclude else None
    for name in order:
        p = work_dir / name
        if not p.exists():
            continue
        try:
            if ex and p.resolve() == ex:
                continue
        except OSError:
            pass
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(rows, list) and rows:
            out.append((name, rows))
    return out


def _overlap_iou(a0: float, a1: float, b0: float, b1: float) -> float:
    lo = max(a0, b0)
    hi = min(a1, b1)
    inter = max(0.0, hi - lo)
    if inter <= 0:
        return 0.0
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def _heal_from_sources(
    start: float,
    end: float,
    sources: list[tuple[str, list[dict]]],
) -> tuple[str | None, str | None]:
    """Tìm câu thật overlap đủ (IoU) — tránh lấy câu kế cận lệch thời gian.

    Cửa sổ spam dài (loop «区域的»×N kéo hàng chục giây): ghép mọi câu sạch
    overlap trong cửa sổ (stitch) thay vì IoU 1-1 (IoU luôn thấp).
    """
    dur = max(end - start, 0.01)

    # --- Stitch: cửa sổ dài ---
    if dur >= 5.0:
        for src_name, rows in sources:
            parts: list[tuple[float, float, str]] = []
            for r in rows:
                try:
                    fs = float(r.get("start", 0) or 0)
                    fe = float(r.get("end", fs) or fs)
                except (TypeError, ValueError):
                    continue
                inter = max(0.0, min(end, fe) - max(start, fs))
                if inter < 0.35:
                    continue
                raw = (r.get("text") or r.get("zh") or "").strip()
                if not raw or is_bg_filler_spam(raw):
                    continue
                cleaned = clean_filler_text(raw)
                if is_filler_only(cleaned) or len(_strip_punct(cleaned)) < 2:
                    continue
                if is_phrase_loop_spam(cleaned):
                    continue
                parts.append((fs, fe, cleaned))
            if len(parts) >= 1:
                parts.sort(key=lambda x: x[0])
                # gộp không đè trùng gần như cùng câu
                merged: list[str] = []
                last_e = -1.0
                for fs, fe, tx in parts:
                    if fs < last_e - 0.15 and merged:
                        continue
                    merged.append(tx)
                    last_e = fe
                text = "".join(merged).strip()
                if len(_strip_punct(text)) >= 4:
                    return text, f"{src_name}|stitch"

    # --- 1 câu IoU cao (cửa sổ ngắn / fallback) ---
    best: tuple[float, str, str] | None = None  # (score, text, src)
    for src_name, rows in sources:
        for r in rows:
            try:
                fs = float(r.get("start", 0) or 0)
                fe = float(r.get("end", fs) or fs)
            except (TypeError, ValueError):
                continue
            iou = _overlap_iou(start, end, fs, fe)
            contained = fs >= start - 0.3 and fe <= end + 0.3
            if iou < 0.25 and not contained:
                continue
            raw = (r.get("text") or r.get("zh") or "").strip()
            if not raw or is_bg_filler_spam(raw):
                continue
            cleaned = clean_filler_text(raw)
            if is_filler_only(cleaned):
                continue
            if len(_strip_punct(cleaned)) < 2:
                continue
            fd = max(fe - fs, 0.01)
            len_pen = min(fd, dur) / max(fd, dur)
            # contained trong blob dài: ưu tiên độ dài overlap tuyệt đối
            if contained and dur >= 5.0:
                score = min(fd, dur) / dur
            else:
                score = iou * 0.7 + len_pen * 0.3
            if best is None or score > best[0]:
                best = (score, cleaned, src_name)
    if best and best[0] >= 0.15:
        return best[1], best[2]
    return None, None


def sanitize_asr_rows(
    rows: list[dict],
    *,
    work_dir: str | Path | None = None,
    source_path: str | Path | None = None,
    heal: bool = True,
    drop_pure_filler: bool = False,
) -> tuple[list[dict], dict[str, Any]]:
    """Rút spam + (tuỳ chọn) vá từ sidecar khác.

    Returns: (rows_mới, info)
      info = {n_cleaned, n_healed, n_dropped, healed: [...]}
    """
    wd = Path(work_dir) if work_dir else None
    src_p = Path(source_path) if source_path else None
    sources: list[tuple[str, list[dict]]] = []
    if heal and wd and wd.is_dir():
        sources = _load_heal_sources(wd, exclude=src_p)

    n_cleaned = 0
    n_healed = 0
    n_dropped = 0
    healed_log: list[dict] = []
    out: list[dict] = []

    for r in rows:
        if not isinstance(r, dict):
            continue
        raw = (r.get("text") or r.get("zh") or "").strip()
        if not raw:
            continue
        rr = dict(r)
        text = raw
        if is_bg_filler_spam(raw):
            cleaned = clean_filler_text(raw)
            text = cleaned
            n_cleaned += 1
            st = filler_stats(raw)
            try:
                t0 = float(rr.get("start", 0) or 0)
                t1 = float(rr.get("end", t0) or t0)
            except (TypeError, ValueError):
                t0, t1 = 0.0, 0.0
            dur = max(t1 - t0, 0.0)
            # Chỉ heal khi spam DÀI (nuốt thoại), không vá cười ngắn 哈哈→câu kế
            need_heal = (
                heal and sources and (
                    st["plain_len"] >= 40
                    or st["ha"] >= 20
                    or st["ah"] >= 20
                    or st["eh"] >= 16
                    or is_phrase_loop_spam(raw)
                    or is_phrase_loop_remnant(raw)
                    or is_phrase_loop_remnant(cleaned)
                    or (is_filler_only(cleaned) and dur >= 2.5)
                    # Blob dài đã bị rút còn mẩu loop → luôn thử stitch sidecar
                    or (dur >= 5.0 and (
                        is_phrase_loop_spam(raw)
                        or is_phrase_loop_remnant(cleaned)
                        or len(_strip_punct(cleaned)) <= 16
                    ))
                )
            )
            if need_heal:
                alt, alt_src = _heal_from_sources(t0, t1, sources)
                if alt:
                    text = alt
                    n_healed += 1
                    healed_log.append({
                        "i": rr.get("i"),
                        "start": t0,
                        "from": alt_src,
                        "was": raw[:80],
                        "now": alt[:80],
                    })
            elif drop_pure_filler and is_filler_only(text):
                n_dropped += 1
                continue
        rr["text"] = text
        if "zh" in rr and rr.get("zh") == raw:
            rr["zh"] = text
        out.append(rr)

    # Lọc chuỗi câu lặp liên tiếp (hallucination loop: period 1 (A A A) hoặc period 2 (A B A B))
    dedup_out: list[dict] = []
    p1_rep = 0
    p2_rep = 0
    for r in out:
        curr_t = _strip_punct(r.get("text") or "")
        if not curr_t:
            continue
        if dedup_out and curr_t == _strip_punct(dedup_out[-1].get("text") or ""):
            p1_rep += 1
            if p1_rep >= 2:
                n_dropped += 1
                continue
        else:
            p1_rep = 0

        if len(dedup_out) >= 2 and curr_t == _strip_punct(dedup_out[-2].get("text") or ""):
            p2_rep += 1
            if p2_rep >= 3:
                n_dropped += 1
                continue
        else:
            p2_rep = 0

        dedup_out.append(r)
    out = dedup_out

    for i, r in enumerate(out):
        r["i"] = i

    info = {
        "n_in": len(rows),
        "n_out": len(out),
        "n_cleaned": n_cleaned,
        "n_healed": n_healed,
        "n_dropped": n_dropped,
        "healed": healed_log[:50],
    }
    return out, info


def sanitize_asr_file(
    path: str | Path,
    *,
    work_dir: str | Path | None = None,
    heal: bool = True,
    write_srt: bool = True,
) -> dict[str, Any]:
    """Đọc JSON ASR → sanitize → ghi đè (+ SRT nếu có cặp)."""
    path = Path(path)
    wd = Path(work_dir) if work_dir else path.parent
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path.name} không phải list")
    rows2, info = sanitize_asr_rows(rows, work_dir=wd, source_path=path, heal=heal)
    path.write_text(json.dumps(rows2, ensure_ascii=False, indent=2), encoding="utf-8")
    if write_srt:
        try:
            from engines.subs import to_srt
            if path.name == "asr.json":
                srt = wd / "sub.zh.srt"
            elif path.name.startswith("asr.") and path.name.endswith(".json"):
                tag = path.name[len("asr."):-len(".json")]
                srt = wd / f"sub.zh.{tag}.srt"
            else:
                srt = None
            if srt is not None:
                srt.write_text(to_srt(rows2, key="text"), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    info["path"] = str(path)
    return info


def prefer_non_spam(a: str, b: str, prefer_a: bool = True) -> tuple[str, str]:
    """Khi 2 bản lệch: ưu tiên bản không phải filler nền.

    Returns: (pick_text, reason_tag)
    """
    sa, sb = is_bg_filler_spam(a), is_bg_filler_spam(b)
    if sa and not sb and (b or "").strip():
        return b, "spam_a→b"
    if sb and not sa and (a or "").strip():
        return a, "spam_b→a"
    if sa and sb:
        ca, cb = clean_filler_text(a), clean_filler_text(b)
        if is_filler_only(ca) and not is_filler_only(cb):
            return cb, "both_spam→b_clean"
        if is_filler_only(cb) and not is_filler_only(ca):
            return ca, "both_spam→a_clean"
        return (ca if prefer_a else cb), "both_spam→clean"
    return (a if prefer_a else b), "ok"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Rút spam filler ASR (tiếng nền).")
    ap.add_argument("work_dir")
    ap.add_argument("--file", default="asr.json", help="asr.json hoặc asr.*.json")
    ap.add_argument("--no-heal", action="store_true")
    args = ap.parse_args(argv)
    wd = Path(args.work_dir)
    info = sanitize_asr_file(wd / args.file, work_dir=wd, heal=not args.no_heal)
    print(
        f"[asr_spam] {info['path']}: cleaned={info['n_cleaned']} "
        f"healed={info['n_healed']} dropped={info['n_dropped']} "
        f"out={info['n_out']}/{info['n_in']}"
    )
    for h in info.get("healed") or []:
        print(f"  heal i={h.get('i')} @{h.get('start'):.1f} ← {h.get('from')}")
        print(f"    was: {h.get('was')}")
        print(f"    now: {h.get('now')}")


if __name__ == "__main__":
    main()
