"""
hybrid_asr.py — ASR chốt: Nano chữ + Para timing + ForcedAlign + Repair.

Pipeline (sidecar rồi promote → asr.json):
  1) Fun-ASR-Nano     → asr.fun_nano.json
  2) Paraformer-zh    → asr.paraformer_zh.json + .chars.json
  3) Align DTW        → asr.aligned.json
  4) Fix + FA         → asr.aligned_fa.json
  5) Repair A+B+FA    → asr.aligned_repaired.json  (lấp lỗ + sửa nghe sai)
  6) Promote          → asr.json + sub.zh.srt

Chạy:
  python hybrid_asr.py work/<job>
  python hybrid_asr.py work/<job> --force
  python hybrid_asr.py work/<job> --no-fa      # bỏ ForcedAligner
  python hybrid_asr.py work/<job> --no-repair  # bỏ bước lấp lỗ/CER

UI engine id: hybrid_fa
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
import shutil
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from engines.asr.asr_align_nano_para import run_align
from engines.asr.asr_fix_align import run_fix
from engines.asr.asr_repair import run_repair
from engines.asr.fun_asr_nano import run_asr_nano
from core.manifest import Manifest
from state import job_state as js  # V6 F1: mọi ghi manifest qua state
from engines.asr.paraformer_zh import run_asr_paraformer
from engines.vram import free_gpu_vram

STAGE = "asr"
ENGINE = "hybrid_fa"
MODEL_ID = "Nano+Para+FA+Repair"


def _prog(m: Manifest, **fields) -> None:
    st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
    st.update({
        "engine": ENGINE, "asr_engine": ENGINE, "model": MODEL_ID,
        "status": "running", "updated_at": time.time(), **fields,
    })
    m.data["stages"][STAGE] = st
    js.persist(m)


def run_hybrid_fa(
    work_dir: str | Path,
    device: str = "auto",
    force: bool = False,
    no_fa: bool = False,
    no_repair: bool = True,
    hub_nano: str = "hf",
    hub_para: str = "ms",
) -> dict:
    work_dir = Path(work_dir)
    m = Manifest(work_dir)

    if m.is_done(STAGE) and not force:
        st = m.stage(STAGE)
        if st and Path(st.get("asr_path", "")).exists():
            print(f"[skip] ASR đã xong: {st['asr_path']}")
            return st

    asr_path = work_dir / "asr.json"
    srt_path = work_dir / "sub.zh.srt"
    if force:
        asr_path.unlink(missing_ok=True)

    m.mark_running(STAGE)
    t0 = time.time()
    try:
        _prog(m, cur_text="Hybrid: Fun-ASR-Nano…", progress=5)
        free_gpu_vram("trước Nano (hybrid)")
        run_asr_nano(
            work_dir, device=device, force=True, hub=hub_nano, sidecar="fun_nano",
        )

        _prog(m, cur_text="Hybrid: Paraformer char-ts…", progress=35)
        free_gpu_vram("trước Paraformer (hybrid)")
        run_asr_paraformer(
            work_dir, device=device, force=True, hub=hub_para, sidecar="paraformer_zh",
        )

        _prog(m, cur_text="Hybrid: align Nano↔Para…", progress=55)
        run_align(
            work_dir,
            nano_name="asr.fun_nano.json",
            chars_name="asr.paraformer_zh.chars.json",
            para_name="asr.paraformer_zh.json",
            out_tag="aligned",
        )

        if no_fa:
            _prog(m, cur_text="Hybrid: Nano timing fallback…", progress=70)
            run_fix(work_dir, step1_only=True, device=device)
            src = work_dir / "asr.aligned_fix.json"
            src_srt = work_dir / "sub.zh.aligned_fix.srt"
            mode_note = "aligned_fix (no FA)"
        else:
            _prog(m, cur_text="Hybrid: ForcedAlign chỗ xấu…", progress=65)
            free_gpu_vram("trước ForcedAligner (hybrid)")
            run_fix(work_dir, device=device)
            src = work_dir / "asr.aligned_fa.json"
            src_srt = work_dir / "sub.zh.aligned_fa.srt"
            mode_note = "aligned_fa"

        if no_repair:
            print("[hybrid_fa] Bước Repair (lấp lỗ / sửa CER): ĐÃ TẮT VĨNH VIỄN để triệt tiêu 100% rủi ro đúp câu và câu ma.", flush=True)
        elif src.exists():
            _prog(m, cur_text="Hybrid: Repair thiếu/sai (A+B+FA)…", progress=82)
            free_gpu_vram("trước Repair (hybrid)")
            run_repair(
                work_dir,
                src_name=src.name,
                device=device if device != "auto" else "cuda:0",
                no_fa=no_fa,
            )
            rep = work_dir / "asr.aligned_repaired.json"
            if rep.exists():
                src = rep
                src_srt = work_dir / "sub.zh.aligned_repaired.srt"
                mode_note = "aligned_repaired"

        if not src.exists():
            raise RuntimeError(f"Thiếu kết quả hybrid: {src}")

        shutil.copy2(src, asr_path)
        if src_srt.exists():
            shutil.copy2(src_srt, srt_path)
        else:
            from engines.subs import to_srt
            rows = json.loads(asr_path.read_text(encoding="utf-8"))
            srt_path.write_text(to_srt(rows, key="text"), encoding="utf-8")

        rows = json.loads(asr_path.read_text(encoding="utf-8"))
        try:
            from engines.asr.asr_spam import sanitize_asr_rows
            from engines.subs import to_srt as _to_srt
            rows, spam_info = sanitize_asr_rows(
                rows, work_dir=work_dir, source_path=asr_path, heal=True)
            asr_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            srt_path.write_text(_to_srt(rows, key="text"), encoding="utf-8")
            if spam_info.get("n_cleaned") or spam_info.get("n_healed"):
                print(f"[{ENGINE}] spam: cleaned={spam_info['n_cleaned']} "
                      f"healed={spam_info['n_healed']}", flush=True)
        except Exception as _se:  # noqa: BLE001
            print(f"[{ENGINE}] asr_spam skip: {_se}", flush=True)
        n = len(rows)
        result = m.mark_done(
            STAGE,
            asr_path=str(asr_path),
            audio_path=str(work_dir / "audio.wav"),
            n_segments=n,
            language="zh",
            model=MODEL_ID,
            engine=ENGINE,
            asr_engine=ENGINE,
            hybrid=mode_note,
            sec=round(time.time() - t0, 1),
        )
        print(f"[ok] {ENGINE}: {n} câu → {asr_path} ({mode_note}, {result.get('sec', '')}s)")
        return result
    except Exception as e:  # noqa: BLE001
        st = m.data.setdefault("stages", {}).setdefault(STAGE, {})
        st.update({"status": "failed", "error": str(e), "updated_at": time.time()})
        m.data["stages"][STAGE] = st
        js.persist(m)
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hybrid ASR: Nano + Para + FA + Repair → asr.json")
    ap.add_argument("work_dir")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-fa", action="store_true",
                    help="Bỏ Qwen ForcedAligner — chỉ Nano fallback")
    ap.add_argument("--repair", dest="no_repair", action="store_false", default=True,
                    help="Bật bước lấp lỗ / sửa CER (mặc định TẮT để chống đúp câu)")
    ap.add_argument("--no-repair", dest="no_repair", action="store_true", default=True,
                    help="Bỏ bước lấp lỗ / sửa CER (mặc định ĐÃ TẮT vĩnh viễn)")
    ap.add_argument("--hub-nano", default="hf", choices=("hf", "ms"))
    ap.add_argument("--hub-para", default="ms", choices=("ms", "hf"))
    args = ap.parse_args(argv)
    run_hybrid_fa(
        args.work_dir, device=args.device, force=args.force, no_fa=args.no_fa,
        no_repair=args.no_repair, hub_nano=args.hub_nano, hub_para=args.hub_para,
    )
    os._exit(0)


if __name__ == "__main__":
    main()
