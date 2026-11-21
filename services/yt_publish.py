"""services/yt_publish.py — đăng nhiều phim lên kênh đã chọn (V6 đa kênh, 2026-11-21).

Xếp từng job vào hàng đăng tuần tự của agents.auto_publisher (metadata + thumbnail + upload, 1 video một lúc)."""
from __future__ import annotations


def publish_many(job_ids: list, *, account_id: str | None = None, privacy: str | None = None,
                 schedule_hours=None, allow_oversized: bool = False) -> dict:
    from services import yt_accounts
    import agents.auto_publisher as ap
    ids = [str(j).strip() for j in job_ids or [] if str(j).strip()]
    if not ids:
        raise ValueError("chọn ít nhất 1 phim")
    acc = account_id or yt_accounts.default_id()
    if not acc or not yt_accounts.has(acc):
        raise ValueError("chưa có kênh YouTube — bấm «Thêm kênh» rồi chọn kênh để đăng")
    try:
        sched = float(schedule_hours) if schedule_hours not in (None, "") else None
    except (TypeError, ValueError):
        sched = None
    out, refused = [], []
    for jid in ids:
        url = _uploaded_url(jid)
        if url:      # đã đăng → không đăng lại (force bên dưới chỉ để bỏ qua công tắc «tự đăng» khi đăng TAY)
            refused.append({"job_id": jid, "error": f"đã đăng rồi: {url}"})
            continue
        r = ap.enqueue_publish(jid, privacy=privacy, schedule_hours=sched, force=True,
                               allow_oversized=allow_oversized, account_id=acc)
        (out if r.get("ok") else refused).append({"job_id": jid, **{k: r.get(k) for k in ("error", "warnings")}})
    title = (yt_accounts.get(acc) or {}).get("title") or acc
    return {"ok": bool(out), "account_id": acc, "channel": title, "queued": [x["job_id"] for x in out],
            "refused": refused}


def _uploaded_url(job_id: str) -> str:
    try:
        import core.film_db as film_db
        u = (film_db.get(job_id) or {}).get("yt_url") or ""
        if u:
            return u
    except Exception as e:  # noqa: BLE001
        print(f"[yt-publish] đọc films.db {job_id}: {e}", flush=True)
    try:
        import json
        from pathlib import Path
        from core.instance import work_root_now
        mf = work_root_now(Path(__file__).resolve().parents[1] / "work") / job_id / "manifest.json"
        return str((json.loads(mf.read_text(encoding="utf-8")) or {}).get("yt_url") or "") if mf.is_file() else ""
    except Exception as e:  # noqa: BLE001
        print(f"[yt-publish] đọc manifest {job_id}: {e}", flush=True)
        return ""
