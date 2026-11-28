"""engines/render_job.py — Điều phối render 1 job (assemble nếu cần → run_render) — V6 F5.

Trước đây là thân `server._do_render` (V5), chạy TRONG tiến trình server và `importlib.reload`
core.config / engines.subs / engines.render / engines.assemble mỗi lần để sửa render có hiệu lực
không cần restart. V6 F5 tách ra đây để CẢ HAI đường dùng chung đúng một logic:

- `workers/render_worker.py` (tiến trình con, cờ instance `render_subprocess`): mỗi job là một
  tiến trình Python mới → import code render mới nhất, KHÔNG reload (`hot_reload=False`).
- `server._do_render` khi cờ tắt (prod cho tới khi so job thật): gọi trong tiến trình với
  `hot_reload=True` = hành vi V5 cũ (reload 4 module như trước).

Ghi manifest: các bước "phase" dùng hàm ngữ nghĩa `state.job_state` (set_stage / mutate — đọc lại đĩa
trong khoá) thay cho sửa `m.data[...]` rồi persist cả object như V5. mark_running / mark_failed giữ `Manifest.mark_*` như V5 (cùng ngữ nghĩa
giữ config stage). Chạy trong tiến trình con: watcher của server bắt mtime manifest → index + SSE.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.manifest import Manifest
from core.timeutil import now_iso as _now_iso
from state import job_state as js

STAGE = "mux"


def _phase(work_dir: Path, **fields) -> None:
    """Cập nhật vài khoá stage mux; lỗi ghi không chặn render (như try/except cũ)."""
    try:
        js.set_stage(work_dir, STAGE, **fields)
    except Exception:  # noqa: BLE001
        pass


def _load_modules(hot_reload: bool):
    """Trả (assemble_mod, render_mod). hot_reload=True: reload như V5 (đường in-process cũ)."""
    import core.config as _cfg_mod
    import engines.assemble as _asm_mod
    import engines.render as _render_mod
    import engines.subs as _subs_mod
    if hot_reload:
        import importlib
        _asm_mod = importlib.reload(_asm_mod)
        importlib.reload(_cfg_mod)
        importlib.reload(_subs_mod)
        _render_mod = importlib.reload(_render_mod)
    return _asm_mod, _render_mod


def do_render(work_dir: str | Path, force: bool = True, audio_mode: str = "voice_bgm",
              voice_vol: float = 0.2, orig_vol: float = 0.6, add_sub: bool = True,
              fit: bool = True, resolution: str = "source", flip: str = "",
              zoom: float | str = 1.12, motion: bool = True, lut: bool = True,
              grain: str = "weak", brand: bool = False, brand_text: str = "",
              frame: str = "off", brand_mode: str = "off", brand_logo: str = "off",
              vbitrate: str = "8M", speed_factor: float = 1.0,
              hardsub_blur: bool = True, *, hot_reload: bool = False) -> str | None:
    """Ghép giọng (assemble nếu chưa) rồi render video localize (che+sub+trộn nền).

    fit=True: khớp time (nén câu lố cho khớp phim). fit=False: giọng tốc độ tự nhiên
    (đặt tại mốc SRT, có thể lệch/đè nhưng nghe thoải mái).
    resolution: source = giữ gốc; 1080/1k = xuất Full HD.
    audio voice_bgm/voice_only: không dùng BGM gốc (an toàn bản quyền hơn demucs/duck).
    Trả đường output.mp4 (hoặc None nếu bỏ qua vì mux đã xong)."""
    from engines.render_fx import clamp_zoom
    from engines.separate import run_separate

    work_dir = Path(work_dir)
    out_mp4 = work_dir / "output.mp4"
    m_early = Manifest(work_dir)

    # Không mark running nếu sẽ skip — trước đây mark_running rồi run_render skip
    # → mux kẹt 'running' → 1-click 409 mãi.
    if (not force and m_early.is_done(STAGE) and out_mp4.exists()
            and out_mp4.stat().st_size > 1_000_000):
        tts_json = work_dir / "tts.json"
        dub = work_dir / "dub_fit.wav"
        dub_ok = dub.exists() and dub.stat().st_size > 1000
        stale_dub = False
        if tts_json.exists() and dub_ok:
            stale_dub = dub.stat().st_mtime < tts_json.stat().st_mtime
        if not stale_dub and dub_ok:
            print(f"[render] skip — mux đã done: {out_mp4}", flush=True)
            return None

    m_early.mark_running(STAGE)
    _phase(work_dir, progress=0, phase="prepare")

    try:
        # Chọn chế độ Demucs mà CHƯA tách nền -> TỰ chạy Tách nền trước (đỡ phải nhớ
        # bấm bước riêng; chọn Demucs ở Render là ra đúng Demucs, không âm thầm ducking).
        if audio_mode in ("demucs_keep", "demucs_clean") and not (work_dir / "no_vocals.wav").exists():
            print(f"[render] chế độ {audio_mode} nhưng chưa tách nền -> tự chạy Demucs trước")
            _phase(work_dir, phase="separate")
            run_separate(work_dir)
        _asm_mod, _render_mod = _load_modules(hot_reload)
        # Ghép lại dub_fit.wav nếu THIẾU hoặc CŨ HƠN tts.json (giọng vừa tạo lại) —
        # chặn bug render xài lại audio lỗi thời.
        # Video dài (>30 phút): assemble() naive KHÔNG ghi file — luôn dùng assemble_fit.
        tts_json = work_dir / "tts.json"
        if tts_json.exists():
            assemble, assemble_fit, NAIVE_MAX_SEC = (
                _asm_mod.assemble, _asm_mod.assemble_fit, _asm_mod.NAIVE_MAX_SEC
            )
            # Ước lượng độ dài từ clip cuối trong tts.json
            long_job = True
            try:
                clips = json.loads(tts_json.read_text(encoding="utf-8"))
                if clips:
                    long_job = float(max(c.get("end", 0) for c in clips)) > NAIVE_MAX_SEC
            except Exception:  # noqa: BLE001
                long_job = True
            use_fit = bool(fit) or long_job
            if long_job and not fit:
                print("[render] video dài: buộc ghép KHỚP TIME (naive không tạo dub_*.wav)",
                      flush=True)
            target = work_dir / ("dub_fit.wav" if use_fit else "dub_tts.wav")
            other = work_dir / ("dub_tts.wav" if use_fit else "dub_fit.wav")
            stale = (not target.exists()) or (target.stat().st_mtime < tts_json.stat().st_mtime)
            if stale:
                print(f"[render] ghép {'KHỚP TIME' if use_fit else 'TỰ NHIÊN'} -> {target.name}",
                      flush=True)
                _phase(work_dir, phase="assemble")
                (assemble_fit if use_fit else assemble)(work_dir)
                force = True     # dub vừa đổi -> BẮT BUỘC render lại (không trả output.mp4 cũ)
            if not target.exists() or target.stat().st_size < 1000:
                raise RuntimeError(
                    f"Chưa có track giọng Việt ({target.name}). "
                    "Ghép TTS thất bại — không render chỉ tiếng gốc.")
            try:
                other.unlink(missing_ok=True)   # bỏ bản kia để _pick_dub dùng đúng track
            except OSError:
                pass

        # hardsub track OCR ~20–30 phút/part 2.5h — UI phải thấy đang chạy, không phải encode
        def _mark_phase(d: dict) -> None:
            st = d.setdefault("stages", {}).get(STAGE)
            if not isinstance(st, dict):
                st = d["stages"][STAGE] = {}
            st["phase"] = "hardsub_track" if hardsub_blur else "encode"
            st["status"] = "running"
            st["cur_text"] = (
                "đang dò hardsub track (OCR khung)…" if hardsub_blur else "đang chạy…"
            )
            st.pop("healed", None)
            st["updated_at"] = time.time()
        try:
            js.mutate(work_dir, _mark_phase)
        except Exception:  # noqa: BLE001
            pass

        _render_mod.run_render(
            work_dir, force=force, audio_mode=audio_mode, voice_vol=voice_vol,
            orig_vol=orig_vol, add_sub=add_sub, resolution=resolution or "source",
            flip=flip or "", zoom=clamp_zoom(zoom), motion=motion, lut=lut, grain=grain,
            brand=bool(brand), brand_text=(brand_text or None),
            brand_mode=(brand_mode or None), brand_logo=(brand_logo or None),
            frame=(frame or None), vbitrate=(vbitrate or "8M"),
            speed_factor=speed_factor, hardsub_blur=hardsub_blur,
        )

        # Nếu run_render skip (done) sau khi ta đã mark_running → trả lại done
        def _finish_if_skipped(d: dict):
            prev = (d.get("stages") or {}).get(STAGE) or {}
            if not (isinstance(prev, dict) and prev.get("status") == "running"
                    and out_mp4.exists() and out_mp4.stat().st_size > 1_000_000):
                return False
            keep = {k: v for k, v in prev.items()
                    if k not in ("status", "error", "cur_text", "started_at")}
            d.setdefault("stages", {})[STAGE] = {
                **keep, "status": "done", "finished_at": _now_iso(),
                "output_path": str(out_mp4),
            }
            return None
        try:
            js.mutate(work_dir, _finish_if_skipped)
        except Exception:  # noqa: BLE001 — như persist cũ: lỗi ghi không biến render xong thành fail
            pass
        return str(out_mp4)
    except Exception as e:  # noqa: BLE001
        try:
            Manifest(work_dir).mark_failed(STAGE, e)
        except Exception:  # noqa: BLE001
            pass
        raise
