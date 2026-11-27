"""services/yt_publish.py — đăng phim lên kênh YouTube đã chọn (V6 nhiều kênh).

- publish_many: đăng TAY nhiều phim lên 1 kênh (Phim đã làm → tích → «Đăng N phim»).
- set_job_targets / set_targets: gán 1 HOẶC NHIỀU kênh cho job (tab «YouTube» của job, hoặc thanh công cụ Công việc) —
  job chưa xong: render xong tự đăng; job đã xong: xếp đăng ngay. Mỗi kênh đăng 1 lần (manifest.yt_uploads[kênh]).
- job_status: tình trạng đăng của 1 job cho UI.
Xếp vào hàng đăng tuần tự của agents.auto_publisher (metadata + thumbnail + upload, 1 video một lúc).
2026-11-21 (1 kênh/job) → 2026-11-27 (nhiều kênh/job, tab YouTube trong chi tiết job).
"""
from __future__ import annotations

import json


def _work_dir(job_id: str):
    from pathlib import Path
    from core.instance import work_root_now
    return work_root_now(Path(__file__).resolve().parents[1] / "work") / job_id


def _manifest(job_id: str) -> dict:
    mf = _work_dir(job_id) / "manifest.json"
    try:
        d = json.loads(mf.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"[yt-publish] đọc manifest {job_id}: {e}", flush=True)
        return {}


def uploads_of(data: dict) -> dict:
    """{account_id: url} đã đăng. Job cũ (trước 11-27) chỉ có yt_url (+ yt_account)."""
    ups = data.get("yt_uploads") if isinstance(data.get("yt_uploads"), dict) else {}
    out = {str(k): str(v) for k, v in ups.items() if v}
    if not out and data.get("yt_url"):
        out[str(data.get("yt_account") or "?")] = str(data["yt_url"])
    return out


def targets_of(data: dict) -> list[dict]:
    """[{id, channel}] kênh đã gán cho job. Dạng cũ {account_id, channel} → 1 phần tử."""
    pt = data.get("publish_to")
    if not isinstance(pt, dict):
        return []
    if isinstance(pt.get("accounts"), list):
        return [a for a in pt["accounts"] if isinstance(a, dict) and a.get("id")]
    if pt.get("account_id"):
        return [{"id": pt["account_id"], "channel": pt.get("channel") or pt["account_id"]}]
    return []


def _uploaded_url(job_id: str, account_id: str | None = None) -> str:
    """URL đã đăng của job (ở kênh account_id nếu nêu; không nêu = bất kỳ kênh)."""
    ups = uploads_of(_manifest(job_id))
    if account_id:
        return ups.get(account_id, "")
    if ups:
        return next(iter(ups.values()))
    try:
        import core.film_db as film_db
        return (film_db.get(job_id) or {}).get("yt_url") or ""
    except Exception as e:  # noqa: BLE001
        print(f"[yt-publish] đọc films.db {job_id}: {e}", flush=True)
        return ""


def _sched(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


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
    sched = _sched(schedule_hours)
    out, refused = [], []
    for jid in ids:
        url = _uploaded_url(jid, acc)
        if url:      # đã đăng KÊNH NÀY → không đăng lại (force chỉ để bỏ qua công tắc «tự đăng» khi đăng TAY)
            refused.append({"job_id": jid, "error": f"đã đăng kênh này rồi: {url}"})
            continue
        r = ap.enqueue_publish(jid, privacy=privacy, schedule_hours=sched, force=True,
                               allow_oversized=allow_oversized, account_id=acc)
        (out if r.get("ok") else refused).append({"job_id": jid, **{k: r.get(k) for k in ("error", "warnings")}})
    title = (yt_accounts.get(acc) or {}).get("title") or acc
    return {"ok": bool(out), "account_id": acc, "channel": title, "queued": [x["job_id"] for x in out],
            "refused": refused}


def set_job_targets(job_ids: list, account_ids: list | None, *, privacy: str | None = None,
                    schedule_hours=None, mode: str = "replace") -> dict:
    """Gán kênh cho job. mode: replace (đặt đúng danh sách) | add (thêm vào) | clear (bỏ hết).
    Job đã xong → kênh chưa đăng được xếp đăng ngay."""
    from services import yt_accounts
    from state import job_state as js
    import core.job_queue as jq
    ids = [str(j).strip() for j in job_ids or [] if str(j).strip()]
    if not ids:
        raise ValueError("chọn ít nhất 1 job")
    accs = [str(a).strip() for a in (account_ids or []) if str(a).strip()]
    if mode == "clear":
        accs = []
    for a in accs:
        if not yt_accounts.has(a):
            raise ValueError("kênh không tồn tại / mất đăng nhập — thêm lại ở Phim đã làm → Kênh YouTube")
    names = {a: (yt_accounts.get(a) or {}).get("title") or a for a in accs}
    sched = _sched(schedule_hours)
    done_now, set_ok, errs = [], [], []
    for jid in ids:
        wd = _work_dir(jid)
        if not (wd / "manifest.json").is_file():
            errs.append(f"{jid}: không có job")
            continue
        cur = [t["id"] for t in targets_of(_manifest(jid))] if mode == "add" else []
        want = list(dict.fromkeys(cur + accs))
        targets = [{"id": a, "channel": names.get(a) or (yt_accounts.get(a) or {}).get("title") or a} for a in want]
        try:
            js.set_publish_target(wd, targets, privacy=privacy, schedule_hours=sched)
            set_ok.append(jid)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{jid}: {e}")
            continue
        if targets and jq.is_wired_done(wd):
            q = publish_on_done(jid)
            if q:
                done_now.append(jid)
    return {"ok": bool(set_ok), "set": set_ok, "published_now": done_now, "errors": errs,
            "channel": ", ".join(names.values()), "channels": list(names.values())}


def set_targets(job_ids: list, account_id: str | None, *, privacy: str | None = None, schedule_hours=None) -> dict:
    """Thanh công cụ Công việc (1 kênh): gán thay thế / None = bỏ gán."""
    return set_job_targets(job_ids, [account_id] if account_id else [], privacy=privacy,
                           schedule_hours=schedule_hours, mode="replace" if account_id else "clear")


def publish_on_done(job_id: str) -> list:
    """Job xong: xếp đăng mọi kênh đã gán mà CHƯA đăng. Trả danh sách kênh vừa xếp ([] = không có gì)."""
    import agents.auto_publisher as ap
    from services import yt_accounts
    data = _manifest(job_id)
    pt = data.get("publish_to") if isinstance(data.get("publish_to"), dict) else {}
    ups = uploads_of(data)
    queued = []
    for t in targets_of(data):
        acc = t["id"]
        if acc in ups:
            continue
        if not yt_accounts.has(acc):
            print(f"[yt-publish] {job_id}: kênh gán «{t.get('channel') or acc}» không còn đăng nhập — bỏ qua", flush=True)
            continue
        r = ap.enqueue_publish(job_id, privacy=pt.get("privacy"), schedule_hours=pt.get("schedule_hours"), force=True,
                               allow_oversized=False, account_id=acc)
        print(f"[yt-publish] {job_id}: xếp đăng lên «{t.get('channel') or acc}»: {r.get('ok')} {r.get('error') or ''}",
              flush=True)
        if r.get("ok"):
            queued.append(acc)
    return queued


def job_status(job_id: str) -> dict:
    """Tab «YouTube» của job: kênh đã gán, đã đăng kênh nào (link), đang chờ đăng, job xong chưa."""
    import agents.auto_publisher as ap
    import core.job_queue as jq
    data = _manifest(job_id)
    if not data:
        raise ValueError("không có job")
    pt = data.get("publish_to") if isinstance(data.get("publish_to"), dict) else {}
    try:
        pending = [str(it.get("account_id") or "") for it in ap._read_publish_queue() if it.get("job_id") == job_id]
    except Exception as e:  # noqa: BLE001
        print(f"[yt-publish] đọc hàng đăng: {e}", flush=True)
        pending = []
    return {"job_id": job_id, "targets": targets_of(data), "uploads": uploads_of(data), "pending": pending,
            "privacy": pt.get("privacy"), "schedule_hours": pt.get("schedule_hours"),
            "done": bool(jq.is_wired_done(_work_dir(job_id)))}
