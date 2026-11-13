"""
ocr.py — STAGE OCR: đọc chữ TRÊN HÌNH (tên nhân vật / địa danh / caption / thẻ) mà
KHÔNG nằm trong phụ đề thoại, rồi dịch sang tiếng Việt.

Đầu vào : work/<job>/source.mp4 + asr.json (để trừ chữ trùng thoại)
Đầu ra  : work/<job>/ocr.json  — [{start,end,box,zh,vi,kind}]
            kind = "static" (chữ đứng yên → render sẽ che gốc + chèn Việt)
                 | "moving" (chữ trên vật di chuyển → render ghi chú dưới màn)

Kỹ thuật:
  - Trích KEYFRAME (nhanh, chỉ giải mã I-frame) làm mẫu — bắt đúng lúc chữ xuất hiện.
  - RapidOCR (ONNX, tiếng Trung, không đụng torch/GPU).
  - Lọc theo VỊ TRÍ: góc = watermark (bỏ); dải giữa-dưới = hardsub thoại (bỏ);
    còn lại = ứng viên.
  - Bám chữ qua các frame -> static/moving + [giờ vào–ra].
  - Trừ-ASR: bỏ chữ trùng câu đã nói.
  - Dịch phần còn lại bằng gemma4 (local).

Chạy tay:
    python ocr.py work/<job>
    python ocr.py work/<job> --dur 1200        # chỉ 20 phút đầu (để test)
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
from engines.vram import free_gpu_vram

STAGE = "ocr"


def _meaningful(t: str) -> bool:
    """Giữ chữ CÓ NGHĨA: ≥2 chữ Hán, hoặc số ≥3 chữ số (ID). Bỏ rác latin / 1 chữ đơn."""
    zh = sum(1 for c in t if "一" <= c <= "鿿")
    if zh >= 2:
        return True
    if re.fullmatch(r"\d{3,}", t.strip()):
        return True
    return False

# Chuỗi watermark hay gặp (Bilibili/Douyin re-up)
WM_STRINGS = ("超高清", "4k修复", "4K修复", "bilibili", "bili", "抖音", "douyin", "高清修复")

_VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov"}


def _find_video(work_dir: Path) -> Path | None:
    # agentK 2026-11-12: 1 hàm resolve nguồn (preflight chờ → chạy; nguồn chuẩn hoá > gốc)
    from engines.source_preflight import stage_source_video
    return stage_source_video(work_dir)


def _probe_size(video: Path) -> tuple[int, int]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video)],
            check=True, capture_output=True, text=True).stdout.strip()
        w, h = out.split("x")[:2]
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return 1280, 720


def extract_keyframes(video: Path, out_dir: Path, dur: float | None = None,
                      stride: int = 1) -> list[tuple[Path, float]]:
    """Trích KEYFRAME + mốc giờ. Giải mã chỉ I-frame nên nhanh. stride: lấy cách quãng."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("kf_*.jpg"):
        f.unlink()
    ri = ["-read_intervals", f"%+{dur}"] if dur else []
    # 1) mốc giờ các keyframe (theo thứ tự)
    times_raw = subprocess.run(
        ["ffprobe", "-v", "error", "-skip_frame", "nokey", "-select_streams", "v:0",
         "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", *ri, str(video)],
        capture_output=True, text=True).stdout.split()
    times: list[float] = []
    for t in times_raw:
        if not t or t == "N/A":
            continue
        # ffprobe csv đôi khi trả "0.041016," (dấu phẩy thừa)
        t = t.strip().strip(",").split(",")[0].strip()
        try:
            times.append(float(t))
        except ValueError:
            continue
    # 2) ảnh keyframe (cùng thứ tự)
    tlim = ["-t", str(dur)] if dur else []
    subprocess.run(
        ["ffmpeg", "-y", "-skip_frame", "nokey", *tlim, "-i", str(video),
         "-an", "-vsync", "0", "-q:v", "4", str(out_dir / "kf_%06d.jpg"), "-loglevel", "error"],
        check=True)
    imgs = sorted(out_dir.glob("kf_*.jpg"))
    n = min(len(imgs), len(times))
    pairs = [(imgs[i], times[i]) for i in range(n)]
    if stride > 1:
        pairs = pairs[::stride]
    return pairs


def _classify_zone(cx: float, cy: float, text: str) -> str:
    """Trả 'wm' (watermark), 'sub' (hardsub thoại), hoặc 'cand' (ứng viên).
    cx/cy là % (0–100)."""
    t = (text or "").lower()
    if any(w.lower() in t for w in WM_STRINGS):
        return "wm"
    if cy < 16 and (cx < 30 or cx > 68):           # góc trên
        return "wm"
    if cy > 80 and (cx < 28 or cx > 68):           # góc dưới (logo kênh bili)
        return "wm"
    if cy > 78 and 22 < cx < 72:                   # dải giữa-dưới = phụ đề thoại
        return "sub"
    return "cand"


def run_ocr(work_dir: str | Path, dur: float | None = None, stride: int = 1,
            model: str | None = None, force: bool = False) -> dict:
    work_dir = Path(work_dir)
    m = Manifest(work_dir)
    if m.is_done(STAGE) and not force:
        st = m.stage(STAGE)
        if st and Path(st.get("ocr_path", "")).exists():
            print(f"[skip] OCR đã xong: {st['ocr_path']}")
            return st

    video = _find_video(work_dir)
    if not video:
        raise RuntimeError("Chưa có video (chạy download trước).")
    W, H = _probe_size(video)

    # ASR để trừ chữ trùng thoại
    asr_path = work_dir / "asr.json"
    asr_txt = []
    if asr_path.exists():
        asr_txt = [s["text"].strip() for s in json.loads(asr_path.read_text(encoding="utf-8"))]

    def in_dialogue(t: str) -> bool:
        t = t.strip()
        return any(t == a or (len(t) >= 2 and (t in a or a in t)) for a in asr_txt)

    m.mark_running(STAGE)
    free_gpu_vram("trước OCR")
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()

        print(f"[ocr] Trích keyframe ({video.name}, {W}x{H})...")
        frames = extract_keyframes(video, work_dir / "_frames", dur=dur, stride=stride)
        print(f"[ocr] {len(frames)} keyframe. Đang OCR...")

        # gom theo chữ: text -> list of (t, cx, cy, box, conf)
        groups: dict[str, list] = {}
        seen_wm: dict[str, int] = {}
        total = len(frames)
        last_save = 0.0
        for idx, (img, t) in enumerate(frames):
            res, _ = ocr(str(img))
            for box, txt, conf in (res or []):
                tx = txt.strip()
                if not tx or conf < 0.55:
                    continue
                xs = [p[0] for p in box]; ys = [p[1] for p in box]
                cx = sum(xs) / 4 / W * 100; cy = sum(ys) / 4 / H * 100
                zone = _classify_zone(cx, cy, tx)
                if zone == "wm":
                    seen_wm[tx] = seen_wm.get(tx, 0) + 1
                    continue
                if zone == "sub":
                    continue
                groups.setdefault(tx, []).append((t, cx, cy))
            # nhịp sống
            now = time.time()
            if now - last_save > 1.5:
                last_save = now
                st = m.data["stages"].get(STAGE, {})
                st.update({"status": "running", "progress": round(idx / total * 100, 1) if total else 0,
                           "n_frames": idx + 1, "n_total": total, "n_found": len(groups),
                           "updated_at": now})
                m.data["stages"][STAGE] = st
                js.persist(m)

        # dọn frame tạm
        for f in (work_dir / "_frames").glob("kf_*.jpg"):
            try: f.unlink()
            except OSError: pass

        # lọc lần cuối + phân static/moving + trừ ASR
        entries = []
        persist_cut = max(3, total // 3)      # xuất hiện quá nhiều lần ở mọi nơi -> coi như watermark ẩn
        for tx, occ in groups.items():
            if not _meaningful(tx):
                continue                       # bỏ rác latin / 1 chữ đơn
            if len(occ) >= persist_cut and len(tx) <= 6:
                continue                       # chữ ngắn xuất hiện khắp phim -> nhiều khả năng UI/watermark
            if in_dialogue(tx):
                continue                       # trùng thoại -> bỏ
            ts = [o[0] for o in occ]
            cxs = [o[1] for o in occ]; cys = [o[2] for o in occ]
            # static nếu vị trí ít dao động
            spread = (max(cxs) - min(cxs)) + (max(cys) - min(cys))
            kind = "static" if spread < 8 else "moving"
            cx = sum(cxs) / len(cxs); cy = sum(cys) / len(cys)
            entries.append({
                "start": round(min(ts), 2), "end": round(max(ts) + 2.0, 2),
                "box_pct": [round(cx, 1), round(cy, 1)], "zh": tx,
                "kind": kind, "n": len(occ),
            })
        entries.sort(key=lambda e: e["start"])

        # dịch bằng gemma4 (1 lần cho tất cả)
        if entries:
            print(f"[ocr] {len(entries)} chữ trên hình. Dịch bằng LLM...")
            _translate_entries(entries, model)

        ocr_path = work_dir / "ocr.json"
        ocr_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        m.mark_failed(STAGE, e)
        raise

    result = m.mark_done(
        STAGE, ocr_path=str(ocr_path), n_texts=len(entries),
        n_static=sum(1 for e in entries if e["kind"] == "static"),
        n_moving=sum(1 for e in entries if e["kind"] == "moving"),
        watermark=sorted(seen_wm, key=seen_wm.get, reverse=True)[:5],
    )
    print(f"[ok] OCR xong: {len(entries)} chữ trên hình -> {ocr_path}")
    return result


def _translate_entries(entries: list[dict], model: str | None) -> None:
    """Dịch danh sách chữ trên hình sang tiếng Việt (1 lời gọi LLM)."""
    from engines.llm import chat, parse_json
    from engines.translate import MODEL as DEFAULT_MODEL
    payload = [{"i": i, "zh": e["zh"]} for i, e in enumerate(entries)]
    sys = ("Dịch các CỤM CHỮ TRÊN HÌNH (tên nhân vật, địa danh, chú thích) sang tiếng Việt, "
           "ngắn gọn, giữ tên riêng theo Hán-Việt. CHỈ trả JSON {\"items\":[{\"i\":<int>,\"vi\":\"...\"}]}.")
    user = json.dumps(payload, ensure_ascii=False)
    try:
        out = chat([{"role": "system", "content": sys}, {"role": "user", "content": user}],
                   model=model or DEFAULT_MODEL, temperature=0.2, json_mode=True, max_tokens=4096)
        vi = {int(it["i"]): (it.get("vi") or "").strip() for it in parse_json(out).get("items", [])}
    except Exception as e:  # noqa: BLE001
        print(f"[ocr] dịch lỗi ({e}) -> để trống vi")
        vi = {}
    for i, e in enumerate(entries):
        e["vi"] = vi.get(i, "")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stage OCR — chữ trên hình.")
    ap.add_argument("work_dir")
    ap.add_argument("--dur", type=float, default=None, help="Chỉ quét N giây đầu (để test).")
    ap.add_argument("--stride", type=int, default=1, help="Lấy cách quãng keyframe.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    run_ocr(args.work_dir, dur=args.dur, stride=args.stride, force=args.force)


if __name__ == "__main__":
    main()
