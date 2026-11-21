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


def _work_dir(job_id: str):
    from pathlib import Path
    from core.instance import work_root_now
    return work_root_now(Path(__file__).resolve().parents[1] / "work") / job_id


def set_targets(job_ids: list, account_id: str | None, *, privacy: str | None = None, schedule_hours=None) -> dict:
    """Gán (account_id) / bỏ gán (None) kênh tự đăng cho nhiều job. Job đã xong mà chưa đăng → xếp đăng luôn."""
    from services import yt_accounts
    from state import job_state as js
    import core.job_queue as jq
    ids = [str(j).strip() for j in job_ids or [] if str(j).strip()]
    if not ids:
        raise ValueError("chọn ít nhất 1 job")
    title = ""
    if account_id:
        if not yt_accounts.has(account_id):
            raise ValueError("kênh không tồn tại / mất đăng nhập — thêm lại ở Phim đã làm → Kênh YouTube")
        title = (yt_accounts.get(account_id) or {}).get("title") or account_id
    try:
        sched = float(schedule_hours) if schedule_hours not in (None, "") else None
    except (TypeError, ValueError):
        sched = None
    done_now, set_ok, errs = [], [], []
    for jid in ids:
        wd = _work_dir(jid)
        if not (wd / "manifest.json").is_file():
            errs.append(f"{jid}: không có job")
            continue
        try:
            js.set_publish_target(wd, account_id, privacy=privacy, schedule_hours=sched, channel=title)
            set_ok.append(jid)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{jid}: {e}")
            continue
        if account_id and jq.is_wired_done(wd) and publish_on_done(jid):
            done_now.append(jid)
    return {"ok": bool(set_ok), "set": set_ok, "published_now": done_now, "errors": errs, "channel": title}


def publish_on_done(job_id: str) -> bool:
    """Job xong: có kênh gán + chưa đăng → xếp đăng lên kênh đó. True = đã xếp."""
    import json
    import agents.auto_publisher as ap
    from services import yt_accounts
    mf = _work_dir(job_id) / "manifest.json"
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[yt-publish] đọc {job_id}: {e}", flush=True)
        return False
    pt = data.get("publish_to") if isinstance(data, dict) else None
    if not isinstance(pt, dict) or not pt.get("account_id") or data.get("yt_url") or _uploaded_url(job_id):
        return False
    acc = pt["account_id"]
    if not yt_accounts.has(acc):
        print(f"[yt-publish] {job_id}: kênh gán «{pt.get('channel') or acc}» không còn đăng nhập — không đăng", flush=True)
        return False
    r = ap.enqueue_publish(job_id, privacy=pt.get("privacy"), schedule_hours=pt.get("schedule_hours"), force=True,
                           allow_oversized=False, account_id=acc)
    print(f"[yt-publish] {job_id}: xong → xếp đăng lên «{pt.get('channel') or acc}»: {r.get('ok')} {r.get('error') or ''}",
          flush=True)
    return bool(r.get("ok"))
