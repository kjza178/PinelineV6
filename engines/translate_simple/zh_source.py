"""Bước 1 (2026-11-12 next) — chọn NGUỒN chữ ZH cho luồng simple (cờ SIMPLE_ZH_SOURCE=1, mặc định tắt).

1) Phát hiện hardsub (detect):
   - có cache của render (hardsub_track.json: bands rỗng = không hardsub; hardsub_band.json: band null = không) → dùng;
   - không có → lấy mẫu SIMPLE_ZH_HS_SAMPLES khung ĐỀU theo phim (ffmpeg -ss), OCR (RapidOCR), phân vùng bằng
     ocr._classify_zone; khung có chữ Hán có nghĩa (ocr._meaningful) ở vùng «sub» ≥ SIMPLE_ZH_HS_MIN_RATIO → có hardsub.
   Không có video / thiếu RapidOCR / ffmpeg lỗi → coi là KHÔNG hardsub (giữ ASR).
2) Có hardsub → cue OCR (hardsub_asr.extract_hardsub_cues; dùng lại hardsub_cues.json nếu có) → ghép bằng
   hardsub_asr.merge_hardsub_into_asr: chữ OCR THẮNG ASR khi khớp thời gian + đủ giống; ASR GIỮ TIMING.
   Mỗi câu ghi zh_src = "ocr" | "asr" (+ zh_asr = chữ ASR gốc khi bị thay).
3) Sửa tên (FireRed không có hotword — đã đọc firered_asr.py): tên/thuật ngữ từ hồ sơ phim (bảng bộ series_glossary +
   glossary.json nếu có) → so khớp gần đúng có bảo vệ: chỉ cửa sổ CÙNG ĐỘ DÀI, tên ≥ 3 chữ, lệch ĐÚNG 1 chữ, cửa sổ
   không phải tên/thuật ngữ khác, điểm giống ≥ SIMPLE_ZH_NAMEFIX_MIN (pypinyin có → chữ lệch phải đồng âm mới tính
   0.9). KHÔNG có pypinyin ⇒ không sửa gì (review agent D). Mọi lần thay ghi log zh_source.json (name_fixes) +
   zh_src «…+namefix».
   Review agent D: _guard_ocr trả lại ASR khi 1 dòng OCR bị nhân sang ≥2 câu ASR / dính câu kế.
KHÔNG ghi asr.json (đếm câu/timing giữ nguyên) — bản dùng cho dịch ghi asr.zhsource.json + báo cáo zh_source.json.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from . import cfg

REPORT = "zh_source.json"
OUT_ASR = "asr.zhsource.json"
_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")


# ---------------------------------------------------------------------------- phát hiện hardsub
def _cached(work_dir: Path) -> dict | None:
    tp = work_dir / "hardsub_track.json"
    if tp.is_file():
        try:
            d = json.loads(tp.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("bands"), list):
                return {"has_hardsub": bool(d["bands"]), "method": "cache:hardsub_track.json"}
        except Exception:  # noqa: BLE001
            pass
    bp = work_dir / "hardsub_band.json"
    if bp.is_file():
        try:
            d = json.loads(bp.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "band" in d:
                return {"has_hardsub": bool(d.get("band")), "method": "cache:hardsub_band.json"}
        except Exception:  # noqa: BLE001
            pass
    return None


def _ffmpeg_grab(video: Path, t: float, out: Path) -> Path | None:
    try:
        subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "4",
                        str(out), "-loglevel", "error"], check=True, timeout=60)
        return out if out.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def _probe_dur(video: Path) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(video)], capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out.split(",")[0])
    except Exception:  # noqa: BLE001
        return 0.0


def _img_size(img) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(img) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return 0, 0


def detect(work_dir: str | Path, *, n: int | None = None, min_ratio: float | None = None,
           find_video=None, probe_dur=None, grab=None, ocr=None, img_size=None) -> dict:
    """{"has_hardsub", "method", "n_samples", "n_hits", "ratio"}. Các hàm I/O tiêm được (test dùng khung giả)."""
    wd = Path(work_dir)
    c = _cached(wd)
    if c is not None:
        return c
    n = cfg.zh_hs_samples() if n is None else n
    min_ratio = cfg.zh_hs_min_ratio() if min_ratio is None else min_ratio
    if find_video is None:
        from engines.asr.hardsub_asr import _find_video as find_video
    video = find_video(wd)
    if not video:
        return {"has_hardsub": False, "method": "no_video"}
    dur = (probe_dur or _probe_dur)(Path(video))
    if dur <= 0:
        return {"has_hardsub": False, "method": "no_duration"}
    if ocr is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _eng = RapidOCR()

            def ocr(img):  # noqa: E306
                res, _ = _eng(str(img))
                return res or []
        except Exception as e:  # noqa: BLE001
            return {"has_hardsub": False, "method": f"no_ocr ({type(e).__name__})"}
    grab = grab or _ffmpeg_grab
    img_size = img_size or _img_size
    from engines.ocr import _classify_zone, _meaningful
    fdir = wd / "_zhsrc_frames"
    fdir.mkdir(parents=True, exist_ok=True)
    hits = got = 0
    times = [round((k + 0.5) * dur / n, 2) for k in range(n)]
    for k, t in enumerate(times):
        img = grab(Path(video), t, fdir / f"z_{k:04d}.jpg")
        if not img:
            continue
        got += 1
        W, H = img_size(img)
        if not W or not H:
            continue
        hit = False
        for box, txt, conf in ocr(img):
            tx = (txt or "").strip()
            if float(conf or 0) < 0.5 or not _meaningful(tx):
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            cx, cy = (min(xs) + max(xs)) / 2 / W * 100, (min(ys) + max(ys)) / 2 / H * 100
            if _classify_zone(cx, cy, tx) == "sub":
                hit = True
                break
        hits += hit
    for f in fdir.glob("z_*.jpg"):
        try:
            f.unlink()
        except OSError:
            pass
    try:
        fdir.rmdir()
    except OSError:
        pass
    ratio = hits / got if got else 0.0
    return {"has_hardsub": bool(got) and ratio >= min_ratio, "method": "sample_ocr", "n_samples": got,
            "n_hits": hits, "ratio": round(ratio, 3)}


# ---------------------------------------------------------------------------- tên / thuật ngữ
def profile_names(work_dir: Path) -> list[str]:
    """Tên nhân vật (+ alias) và thuật ngữ ZH từ hồ sơ phim: bảng bộ (series_glossary) + glossary.json."""
    names: list[str] = []
    plans = []
    try:
        import engines.series_glossary as series_glossary
        sp = series_glossary.load(series_glossary.series_key(work_dir))
        if isinstance(sp, dict):
            plans.append(sp)
    except Exception:  # noqa: BLE001
        pass
    gp = work_dir / "glossary.json"
    if gp.is_file():
        try:
            g = json.loads(gp.read_text(encoding="utf-8"))
            if isinstance(g, dict):
                plans.append(g)
        except Exception:  # noqa: BLE001
            pass
    for p in plans:
        for c in p.get("characters") or []:
            if isinstance(c, dict):
                names += [str(c.get("zh") or "")] + [str(x) for x in (c.get("aliases") or [])]
        for t in p.get("terms") or []:
            if isinstance(t, dict):
                names.append(str(t.get("zh") or ""))
    out = [n.strip() for n in names if len(n.strip()) >= 2 and all(_CJK_RE.match(ch) for ch in n.strip())]
    return list(dict.fromkeys(out))


def _pinyin():
    try:
        from pypinyin import lazy_pinyin
        return lambda ch: (lazy_pinyin(ch) or [""])[0]
    except Exception:  # noqa: BLE001
        return None


def fix_names(segments: list[dict], names: list[str], *, min_score: float | None = None,
              pinyin=None) -> list[dict]:
    """Sửa tên gần đúng TẠI CHỖ trên segments (key text). Trả log [{i, name, from, to, score}].
    pinyin: hàm chữ → âm không dấu (None = tự dò pypinyin; False = không dùng)."""
    min_score = cfg.zh_namefix_min() if min_score is None else min_score
    py = _pinyin() if pinyin is None else (pinyin or None)
    known = set(names)
    log: list[dict] = []
    if not py:
        # review agent D: không kiểm được đồng âm ⇒ KHÔNG sửa (trước đây tên/thuật ngữ ≥4 chữ lệch 1 chữ BẤT KỲ bị
        # thay: 天下第二 → 天下第一). Cài pypinyin để bật sửa tên.
        return log
    for nm in sorted(names, key=len, reverse=True):
        L = len(nm)
        if L < 3:
            continue
        for s in segments:
            t = s.get("text") or ""
            if nm in t or len(t) < L:
                continue
            new = t
            for k in range(0, len(t) - L + 1):
                w = t[k:k + L]
                if w in known or not all(_CJK_RE.match(ch) for ch in w):
                    continue
                diff = [j for j in range(L) if w[j] != nm[j]]
                if len(diff) != 1:
                    continue
                j = diff[0]
                homo = bool(py and py(w[j]) and py(w[j]) == py(nm[j]))
                if py and not homo:
                    continue           # có pypinyin: chữ lệch phải đồng âm (lỗi ASR điển hình)
                score = (L - 1 + (0.9 if homo else 0.0)) / L
                if score < min_score:
                    continue
                new = new[:k] + nm + new[k + L:]
                log.append({"i": s.get("i"), "name": nm, "from": w, "to": nm, "score": round(score, 3),
                            "homophone": homo})
                break
            if new != t:
                s.setdefault("zh_asr", t)
                s["text"] = new
                s["zh_src"] = (s.get("zh_src") or "asr") + "+namefix"
    return log


# ---------------------------------------------------------------------------- bảo vệ ghép OCR (review agent D)
def _guard_ocr(merged: list[dict], H) -> int:
    """merge_hardsub_into_asr chọn cue theo từng câu ASR độc lập ⇒ khi ASR tách 1 dòng hardsub thành 2 câu, cả hai
    (hoặc một) câu nhận NGUYÊN dòng OCR ⇒ lồng tiếng đọc lặp. Trả lại chữ ASR khi:
      - chữ OCR dài hơn rõ chữ ASR (> max(ASR+3, ASR×1.4) ký tự — dính câu kế), hoặc
      - cùng một chữ OCR thay cho ≥2 câu mà chữ ASR các câu đó khác nhau.
    Trả số câu bị trả lại ASR. Timing/i không đổi."""
    norm = getattr(H, "_norm_zh", lambda x: re.sub(r"\W", "", x or ""))
    n = 0

    def revert(m):
        m["text"] = m.get("text_asr") or m.get("text")
        m.pop("align", None)
        m["hardsub"] = "guard_keep_asr"

    groups: dict[str, list[dict]] = {}
    for m in merged:
        if m.get("align") != "hardsub_ocr":
            continue
        na, nb = norm(m.get("text_asr") or ""), norm(m.get("text") or "")
        if len(nb) > max(len(na) + 3, len(na) * 1.4):
            revert(m)
            n += 1
            continue
        groups.setdefault(nb, []).append(m)
    for ms in groups.values():
        if len(ms) > 1 and len({norm(m.get("text_asr") or "") for m in ms}) > 1:
            for m in ms:
                revert(m)
                n += 1
    return n


# ---------------------------------------------------------------------------- điểm vào
def prepare(work_dir: str | Path, segments: list[dict], *, detect_fn=None, cues_fn=None) -> tuple[list[dict], dict]:
    """Trả (segments dùng cho dịch, báo cáo). Không ghi asr.json. Lỗi bất kỳ → trả segments gốc + báo cáo lỗi."""
    wd = Path(work_dir)
    rep: dict = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "n": len(segments)}
    try:
        det = (detect_fn or detect)(wd)
        rep["detect"] = det
        segs = [dict(s) for s in segments if isinstance(s, dict)]
        for s in segs:
            s["zh_src"] = "asr"
        if det.get("has_hardsub"):
            import engines.asr.hardsub_asr as H
            cues_p = wd / "hardsub_cues.json"
            if cues_fn is not None:
                cues = cues_fn(wd)
            elif cues_p.is_file():
                cues = json.loads(cues_p.read_text(encoding="utf-8"))
            else:
                cues = H.extract_hardsub_cues(wd)
                cues_p.write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
            merged, st = H.merge_hardsub_into_asr(segs, cues)
            st["guard_reverted"] = _guard_ocr(merged, H)
            for m in merged:
                if m.get("align") == "hardsub_ocr":
                    m["zh_src"] = "ocr"
                    m["zh_asr"] = m.pop("text_asr", None)
                for k in ("hardsub", "align", "text_asr"):
                    m.pop(k, None)
            segs = merged
            rep["merge"] = st
        if cfg.zh_namefix():
            names = profile_names(wd)
            rep["n_names"] = len(names)
            rep["name_fixes"] = fix_names(segs, names)
        rep["n_ocr"] = sum(1 for s in segs if str(s.get("zh_src", "")).startswith("ocr"))
        rep["n_asr"] = len(segs) - rep["n_ocr"]
        from core.fileio import atomic_write_json
        atomic_write_json(wd / OUT_ASR, segs, fsync=False)
        atomic_write_json(wd / REPORT, rep, fsync=False)
        return segs, rep
    except Exception as e:  # noqa: BLE001
        rep["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        print(f"[simple-zh] lỗi chọn nguồn ZH ({rep['error']}) → giữ ASR", flush=True)
        try:
            from core.fileio import atomic_write_json
            atomic_write_json(wd / REPORT, rep, fsync=False)
        except Exception:  # noqa: BLE001
            pass
        return segments, rep


def sync_rows(store, segs: list[dict]) -> int:
    """Hàng script đã có (chạy lại): câu KHÔNG manual, CHƯA rv → cập nhật zh theo nguồn mới (giữ zh_asr)."""
    by_i = {}
    for s in segs:
        try:
            by_i[int(s.get("i"))] = s
        except (TypeError, ValueError):
            continue
    n = 0
    for r in store.rows:
        try:
            s = by_i.get(int(r.get("i")))
        except (TypeError, ValueError):
            continue
        if s is None or r.get("manual") or r.get("rv"):
            continue
        new = (s.get("text") or "").strip()
        if new:
            from .script_store import _clean_zh
            new = _clean_zh(new)
        if s.get("zh_src"):
            r["zh_src"] = s["zh_src"]
        if new and new != (r.get("zh") or "").strip() and s.get("zh_src") != "asr":
            r.setdefault("zh_asr", r.get("zh"))
            r["zh"] = new
            n += 1
    return n
