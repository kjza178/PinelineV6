"""services/title_vi.py — Cài đặt «Tên phim tiếng Việt» (2026-11-21).

Bật/tắt dịch tên phim (TITLE_TRANSLATE trong pineline.config.json, cộng với cờ instance title_translate) và dịch lại tên
các job hiện có. Dịch bằng model «Soát & dịch» chính + dự phòng (server._title_model / _title_chat)."""
from __future__ import annotations


def _server():
    import server
    return server


def status() -> dict:
    srv = _server()
    from core.config import get
    jobs = cjk = done = 0
    if srv.WORK_ROOT.is_dir():
        import json
        for d in srv.WORK_ROOT.iterdir():
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                print(f"[title] đọc {d.name}: {e}", flush=True)
                continue
            jobs += 1
            req = data.get("request") or {}
            t = ((data.get("stages") or {}).get("download") or {}).get("title") or req.get("part_title") or req.get("title")
            if srv._has_cjk(str(t or "")):
                cjk += 1
                if data.get("title_vi"):
                    done += 1
    return {"enabled": srv._title_translate_on(), "instance_flag": srv._instance_flag("title_translate"),
            "setting": str(get("TITLE_TRANSLATE") or ""), "model": srv._title_model() or "",
            "jobs": jobs, "chinese_titles": cjk, "translated": done}


def set_enabled(on: bool) -> dict:
    from core.config import save_config
    save_config({"TITLE_TRANSLATE": "1" if on else None})
    if on:
        _server()._title_backfill_start(force=True)
    return status()


def backfill() -> dict:
    srv = _server()
    if not srv._title_translate_on():
        raise ValueError("chưa bật «Dịch tên phim sang tiếng Việt»")
    n = srv._title_backfill_start(force=True)
    return {**status(), "queued_scan": n}
