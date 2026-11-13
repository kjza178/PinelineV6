"""
asr_hybrid_progress.py — Đưa tiến độ sidecar (Nano/Para) vào stages.asr cho UI Hybrid.

UI đọc manifest stages.asr; sidecar chỉ ghi asr.*.progress.json → UI kẹt 5%.
Hàm này map % sidecar vào khoảng Hybrid và ghi/đè vào stages.asr (giữ engine=hybrid_fa).
"""
from __future__ import annotations

from state import job_state as js  # V6 F1: mọi ghi manifest qua state
import json
import time
from pathlib import Path

# Hybrid pipeline bands (khớp hybrid_asr._prog)
# Nano 5–34 · Para 35–54 · align/FA/repair sau đó do hybrid_asr ghi
_BANDS = {
    "fun_nano": (5, 34),
    "paraformer_zh": (35, 54),
}


def _load_json(p: Path) -> dict | None:
    try:
        if p.exists() and p.stat().st_size > 2:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return None


def map_sidecar_pct(tag: str, pct: float | int | None) -> int:
    lo, hi = _BANDS.get(tag, (5, 34))
    p = max(0.0, min(100.0, float(pct or 0)))
    return int(round(lo + (hi - lo) * (p / 100.0)))


def active_sidecar_progress(work_dir: str | Path) -> tuple[str, dict] | None:
    """Trả (tag, payload) sidecar đang chạy mới nhất; ưu tiên Para nếu cả hai."""
    work = Path(work_dir)
    cands: list[tuple[str, Path, float]] = []
    for tag in ("fun_nano", "paraformer_zh"):
        p = work / f"asr.{tag}.progress.json"
        d = _load_json(p)
        if not d or d.get("status") not in (None, "running"):
            # status done/failed → bỏ nếu hybrid đã qua bước
            if d and d.get("status") == "running":
                pass
            elif d and d.get("status") == "done":
                continue
            elif not d:
                continue
        if d and d.get("status") == "failed":
            continue
        if d and d.get("status") == "running":
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            cands.append((tag, p, mtime))
    if not cands:
        # fallback: file mới nhất còn progress < 100 dù status thiếu
        for tag in ("fun_nano", "paraformer_zh"):
            p = work / f"asr.{tag}.progress.json"
            d = _load_json(p)
            if d and int(d.get("progress") or 0) < 100 and d.get("status") != "failed":
                try:
                    cands.append((tag, p, p.stat().st_mtime))
                except OSError:
                    cands.append((tag, p, 0.0))
    if not cands:
        return None
    # ưu tiên para nếu running; không thì file mới nhất
    para = [c for c in cands if c[0] == "paraformer_zh"]
    pick = para[0] if para else max(cands, key=lambda x: x[2])
    d = _load_json(pick[1])
    return (pick[0], d) if d else None


def mirror_to_asr_stage(
    work_dir: str | Path,
    *,
    tag: str,
    progress: float | int | None,
    cur_text: str | None = None,
    n_segments=None,
    done_sec=None,
    total_sec=None,
) -> dict | None:
    """Ghi tiến độ sidecar vào stages.asr (giữ hybrid_fa). Trả stage đã cập nhật."""
    from core.manifest import Manifest

    work = Path(work_dir)
    m = Manifest(work)
    st = m.data.setdefault("stages", {}).setdefault("asr", {})
    eng = (st.get("asr_engine") or st.get("engine") or "")
    if st.get("status") != "running":
        return None
    if eng and eng != "hybrid_fa":
        return None

    mapped = map_sidecar_pct(tag, progress)
    label = "Nano" if tag == "fun_nano" else ("Para" if tag == "paraformer_zh" else tag)
    text = cur_text or ""
    st.update({
        "status": "running",
        "engine": "hybrid_fa",
        "asr_engine": "hybrid_fa",
        "progress": mapped,
        "cur_text": f"Hybrid {label}: {text}" if text else f"Hybrid {label}…",
        "updated_at": time.time(),
    })
    if n_segments is not None:
        st["n_segments"] = n_segments
    if done_sec is not None:
        st["done_sec"] = done_sec
    if total_sec is not None:
        st["total_sec"] = total_sec
    m.data["stages"]["asr"] = st
    js.persist(m)
    return st


def overlay_job_asr(work_dir: str | Path, asr_stage: dict) -> dict:
    """Overlay tiến độ sidecar lên dict stages.asr (cho API, không ghi đĩa)."""
    if not asr_stage or asr_stage.get("status") != "running":
        return asr_stage
    eng = (asr_stage.get("asr_engine") or asr_stage.get("engine") or "")
    if eng != "hybrid_fa":
        return asr_stage
    found = active_sidecar_progress(work_dir)
    if not found:
        return asr_stage
    tag, d = found
    if d.get("status") == "failed":
        return asr_stage
    out = dict(asr_stage)
    out["progress"] = map_sidecar_pct(tag, d.get("progress"))
    label = "Nano" if tag == "fun_nano" else "Para"
    ct = d.get("cur_text") or ""
    out["cur_text"] = f"Hybrid {label}: {ct}" if ct else out.get("cur_text")
    out["updated_at"] = d.get("updated_at") or time.time()
    for k in ("n_segments", "done_sec", "total_sec"):
        if d.get(k) is not None:
            out[k] = d[k]
    return out
