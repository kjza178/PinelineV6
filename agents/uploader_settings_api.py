"""Settings + YouTube Uploader API — dùng được từ sidecar :8802 (stdlib) và FastAPI.

Trả về (http_status: int, body: dict). Không phụ thuộc FastAPI.
"""
from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
YT_DIR = _machine.yt_uploader_dir()
YT_QUEUE = YT_DIR / "queue_state.json"
YT_HISTORY = YT_DIR / "upload_history.jsonl"
YT_SETTINGS = YT_DIR / "settings.json"
SKIP_FLAG = YT_DIR / "skip_current.flag"
CFG_PATH = ROOT / "pineline.config.json"

# YouTube thường từ chối video quá dài (~12h). 11.5h = ngưỡng cảnh báo/chặn soft.
YT_MAX_DURATION_SEC = 41400  # 11.5h
YT_HUGE_SIZE_BYTES = 64 * 1024 * 1024 * 1024  # 64 GiB — cảnh báo dung lượng

# Status nhóm
ACTIVE = frozenset({"chờ", "cho", "pending", "queued", "đang tải", "dang tai", "đang upload", "dang upload", "uploading"})
DONE_LIKE = frozenset({"xong", "done", "completed", "xong (đang xử lý)", "xong (chờ xử lý)"})
SKIP_LIKE = frozenset({"bỏ qua", "bo qua", "skipped", "skip", "dừng", "dung"})


def _ok(data: dict | None = None, **extra: Any) -> tuple[int, dict]:
    body = {"ok": True, **(data or {}), **extra}
    return 200, body


def _err(msg: str, code: int = 400, **extra: Any) -> tuple[int, dict]:
    return code, {"ok": False, "error": msg, **extra}


def _norm_status(s: str) -> str:
    return (s or "").strip().lower()


def _is_active(st: str) -> bool:
    return _norm_status(st) in ACTIVE or "đang" in _norm_status(st) or "upload" in _norm_status(st)


def _is_done(st: str) -> bool:
    n = _norm_status(st)
    return n in DONE_LIKE or n.startswith("xong")


def _is_skip(st: str) -> bool:
    n = _norm_status(st)
    return n in SKIP_LIKE or "bỏ qua" in n or "skip" in n


def _ui_status(st: str) -> str:
    """Map status yt_uploader → badge UI (uploading/done/failed/skipped/queued)."""
    n = _norm_status(st)
    if _is_skip(st):
        return "skipped"
    if _is_done(st):
        return "done"
    if "fail" in n or "lỗi" in n or "thất bại" in n or "blocked" in n or "bị chặn" in n:
        return "failed"
    if "đang" in n or "upload" in n or n == "uploading":
        return "uploading"
    return "queued"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _read_queue() -> list[dict]:
    data = _read_json(YT_QUEUE, [])
    return data if isinstance(data, list) else []


def _write_queue(items: list[dict]) -> bool:
    return _write_json(YT_QUEUE, items)


def _read_history(limit: int = 50) -> list[dict]:
    if not YT_HISTORY.is_file():
        return []
    rows: list[dict] = []
    try:
        with open(YT_HISTORY, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        return list(reversed(rows[-limit:]))
    except Exception:
        return []


def _is_yt_uploader_running() -> tuple[bool, int | None]:
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            try:
                pname = (p.info.get("name") or "").lower()
                if "python" not in pname:
                    continue
                cmd = p.cmdline()
                if any("yt_uploader" in str(x).lower() for x in cmd):
                    return True, int(p.info["pid"])
            except Exception:
                pass
    except Exception:
        pass
    return False, None


def _enrich_queue_item(it: dict) -> dict:
    out = dict(it)
    # Aliases for Films UI
    out["video_path"] = out.get("video_path") or out.get("path") or ""
    out["path"] = out.get("path") or out.get("video_path") or ""
    out["account_name"] = out.get("account_name") or out.get("account") or ""
    out["account"] = out.get("account") or out.get("account_name") or "default"
    raw_st = str(out.get("status") or "chờ")
    out["status_raw"] = raw_st
    out["status_ui"] = _ui_status(raw_st)
    # Keep original status for yt_uploader; UI can use status_ui
    out["progress"] = float(out.get("progress") or 0)
    return out


# ── Settings ────────────────────────────────────────────────────────────────

def get_settings() -> tuple[int, dict]:
    try:
        import core.config as cfg
        cfg._load_cfg(force=True)
        get = cfg.get
    except Exception:
        def get(k, d=None):
            return os.getenv(k, d)

    avail_models: list[str] = []
    try:
        import engines.llm as llm
        m = llm.list_available_models()
        avail_models = (m or {}).get("models") or []
    except Exception:
        pass

    tele_token = get("TELEGRAM_BOT_TOKEN") or ""
    tele_users = get("TELEGRAM_ALLOWED_USERS") or ""
    tele_active = str(get("AGENT_TELEGRAM") or "0").lower() in ("1", "true", "yes")

    flat = {
        "LIBRARY_DIR": get("LIBRARY_DIR") or r"D:\Lib",
        "WORK_DIR": get("WORK_DIR") or os.getenv("WORK_DIR") or "work",
        "YT_UPLOADER_DIR": get("YT_UPLOADER_DIR") or str(YT_DIR),
        "TEMP": os.getenv("TEMP") or ".",
        "LLM_BASE_URL": get("LLM_BASE_URL") or "http://127.0.0.1:8083/v1",
        "LLM_API_KEY": get("LLM_API_KEY") or "sk-local",
        "LLM_MODEL": get("LLM_MODEL") or "local",
        "TRANSLATE_MODEL": get("TRANSLATE_MODEL") or "local",
        "REVIEW_MODEL": get("REVIEW_MODEL") or "local",
        "AGENT_MODEL": get("AGENT_MODEL") or "",
        "AGENT_FALLBACK_MODEL": get("AGENT_FALLBACK_MODEL") or "",
        "AGENT_BASE_URL": get("AGENT_BASE_URL") or "",
        "AGENT_API_KEY": get("AGENT_API_KEY") or "",
        "TELEGRAM_BOT_TOKEN": tele_token,
        "TELEGRAM_ALLOWED_USERS": tele_users,
        "AGENT_TELEGRAM": tele_active,
        "VIENEU_DEVICE": get("VIENEU_DEVICE") or "cuda",
        "VIENEU_BATCH": get("VIENEU_BATCH") or "8",
        "OMNI_MODEL": get("OMNI_MODEL") or "",
        "RENDER_ENCODER": get("RENDER_ENCODER") or "nvenc",
    }
    return _ok({
        "config": flat,
        "available_models": avail_models,
        "storage": {k: flat[k] for k in ("LIBRARY_DIR", "WORK_DIR", "YT_UPLOADER_DIR", "TEMP")},
        "ai": {k: flat[k] for k in flat if k.startswith(("LLM_", "TRANSLATE_", "REVIEW_", "AGENT_"))},
        "telegram": {
            "TELEGRAM_BOT_TOKEN": tele_token,
            "TELEGRAM_ALLOWED_USERS": tele_users,
            "AGENT_TELEGRAM": tele_active,
        },
    })


def _save_cfg_updates(updates: dict[str, Any]) -> tuple[int, dict]:
    try:
        cur = _read_json(CFG_PATH, {})
        if not isinstance(cur, dict):
            cur = {}
        for k, v in updates.items():
            cur[k] = v
            os.environ[str(k)] = str(v)
        if not _write_json(CFG_PATH, cur):
            return _err("Không ghi được pineline.config.json", 500)
        try:
            import core.config as cfg
            cfg._load_cfg(force=True)
        except Exception:
            pass
        return _ok({"message": "Đã lưu cài đặt thành công!", "updated_keys": list(updates.keys())})
    except Exception as e:
        return _err(str(e), 500)


def update_settings(payload: dict) -> tuple[int, dict]:
    updates: dict[str, Any] = {}
    if "storage" in payload and isinstance(payload["storage"], dict):
        for k in ("LIBRARY_DIR", "WORK_DIR", "YT_UPLOADER_DIR"):
            if k in payload["storage"]:
                updates[k] = str(payload["storage"][k]).strip()
    if "ai" in payload and isinstance(payload["ai"], dict):
        for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "TRANSLATE_MODEL",
                  "REVIEW_MODEL", "AGENT_MODEL", "AGENT_FALLBACK_MODEL",
                  "AGENT_BASE_URL", "AGENT_API_KEY"):
            if k in payload["ai"]:
                updates[k] = str(payload["ai"][k]).strip()
    if "telegram" in payload and isinstance(payload["telegram"], dict):
        tg = payload["telegram"]
        if "TELEGRAM_BOT_TOKEN" in tg:
            updates["TELEGRAM_BOT_TOKEN"] = str(tg["TELEGRAM_BOT_TOKEN"]).strip()
        if "TELEGRAM_ALLOWED_USERS" in tg:
            updates["TELEGRAM_ALLOWED_USERS"] = str(tg["TELEGRAM_ALLOWED_USERS"]).strip()
        if "AGENT_TELEGRAM" in tg:
            updates["AGENT_TELEGRAM"] = "1" if tg["AGENT_TELEGRAM"] else "0"
    # Flat keys from Films UI form
    for k in ("LIBRARY_DIR", "WORK_DIR", "YT_UPLOADER_DIR", "LLM_BASE_URL", "LLM_API_KEY",
              "LLM_MODEL", "TRANSLATE_MODEL", "AGENT_MODEL", "TELEGRAM_BOT_TOKEN",
              "TELEGRAM_ALLOWED_USERS"):
        if k in payload and payload[k] is not None:
            updates[k] = str(payload[k]).strip()
    if "AGENT_TELEGRAM" in payload:
        updates["AGENT_TELEGRAM"] = "1" if payload["AGENT_TELEGRAM"] else "0"
    if not updates:
        return _err("Không có key nào để lưu")
    return _save_cfg_updates(updates)


def test_telegram(payload: dict) -> tuple[int, dict]:
    import urllib.parse
    import urllib.request
    try:
        import core.config as cfg
        get = cfg.get
    except Exception:
        get = os.getenv  # type: ignore
    token = str(payload.get("token") or get("TELEGRAM_BOT_TOKEN") or "").strip()
    user_ids = str(payload.get("users") or get("TELEGRAM_ALLOWED_USERS") or "").strip()
    if not token:
        return _err("Chưa điền TELEGRAM_BOT_TOKEN")
    if not user_ids:
        return _err("Chưa điền Chat ID / User ID")
    uids = [u.strip() for u in user_ids.split(",") if u.strip()]
    sent, errors = 0, []
    text = "🔔 [Pineline] Test Telegram OK — sidecar :8802."
    for uid in uids:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": uid, "text": text}).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    sent += 1
        except Exception as e:
            errors.append(f"{uid}: {e}")
    if sent:
        return _ok({"message": f"Đã gửi test tới {sent}/{len(uids)}", "errors": errors})
    return _err("; ".join(errors) or "gửi thất bại", 500)


def test_llm(payload: dict) -> tuple[int, dict]:
    import urllib.request
    try:
        import core.config as cfg
        get = cfg.get
    except Exception:
        get = lambda k, d=None: os.getenv(k, d)  # noqa: E731
    base = str(payload.get("base_url") or get("LLM_BASE_URL") or "http://127.0.0.1:8083/v1").rstrip("/")
    key = str(payload.get("api_key") or get("LLM_API_KEY") or "sk-local").strip()
    model = str(payload.get("model") or get("LLM_MODEL") or "local").strip()
    t0 = time.time()
    url = f"{base}/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "1+1=?"}],
        "max_tokens": 20,
        "temperature": 0.1,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            elapsed = round(time.time() - t0, 2)
            res = json.loads(resp.read().decode("utf-8", "replace"))
            reply = (res.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return _ok({"message": f"OK ({elapsed}s)", "reply": reply, "model": model, "elapsed_sec": elapsed})
    except Exception as e:
        return _err(f"LLM lỗi ({url}): {e}", 500)


# ── Uploader ───────────────────────────────────────────────────────────────

def get_uploader_status() -> tuple[int, dict]:
    accounts: list[dict] = []
    try:
        import tools.lib_check as lib_check
        accounts = lib_check.list_yt_accounts() or []
    except Exception:
        pass
    queue = [_enrich_queue_item(it) for it in _read_queue()]
    history = _read_history(40)
    # Normalize history aliases
    hist_out = []
    for h in history:
        hh = dict(h)
        hh["youtube_url"] = hh.get("youtube_url") or hh.get("url") or (
            f"https://youtu.be/{hh['video_id']}" if hh.get("video_id") else ""
        )
        hh["source_path"] = hh.get("source_path") or hh.get("path") or ""
        hh["account_name"] = hh.get("account_name") or hh.get("account") or ""
        hist_out.append(hh)
    running, pid = _is_yt_uploader_running()
    yt_opts = _read_json(YT_SETTINGS, {})
    if not isinstance(yt_opts, dict):
        yt_opts = {}
    current = next((it for it in queue if it.get("status_ui") == "uploading"), None)
    return _ok({
        "is_running": running,
        "pid": pid,
        "accounts": accounts,
        "queue_count": len(queue),
        "queue": queue,
        "history_count": len(hist_out),
        "history": hist_out,
        "settings": yt_opts,
        "yt_dir": str(YT_DIR),
        "current": current,
        "skip_flag": SKIP_FLAG.exists(),
    })


def _oversized_warnings(path: Path, duration_sec: float = 0.0) -> list[str]:
    """Cảnh báo file quá dài / quá nặng cho YouTube."""
    warnings: list[str] = []
    try:
        dur = float(duration_sec or 0)
    except Exception:
        dur = 0.0
    if dur > YT_MAX_DURATION_SEC:
        hours = dur / 3600.0
        warnings.append(
            f"Thời lượng ~{hours:.1f}h (>11.5h) — YouTube thường từ chối file quá dài"
        )
    try:
        size = path.stat().st_size if path.is_file() else 0
    except Exception:
        size = 0
    if size >= YT_HUGE_SIZE_BYTES:
        gb = size / (1024 ** 3)
        warnings.append(f"Dung lượng ~{gb:.1f} GiB (≥64 GiB) — upload rất dễ fail/timeout")
    return warnings


def enqueue_upload(payload: dict) -> tuple[int, dict]:
    path = str(
        payload.get("path")
        or payload.get("video_path")
        or ""
    ).strip()
    if not path or not Path(path).is_file():
        return _err(f"Không tìm thấy file video: {path}")

    title = str(payload.get("title") or Path(path).stem).strip()
    description = str(payload.get("description") or f"Xem phim {title} trọn bộ.").strip()
    tags = payload.get("tags") or ""
    if isinstance(tags, list):
        tags = ", ".join(str(t).strip() for t in tags if str(t).strip())
    tags = str(tags).strip()
    privacy = str(payload.get("privacy") or "unlisted").strip().lower()
    account = str(payload.get("account") or payload.get("account_name") or "default").strip()
    thumb = str(payload.get("thumb") or payload.get("thumbnail_path") or "").strip()
    playlist_id = str(payload.get("playlist_id") or "").strip()
    publish_at = str(payload.get("publish_at") or "").strip()
    # Options
    skip_if_uploading = bool(payload.get("skip_if_uploading"))
    priority = bool(payload.get("priority"))
    allow_huge = bool(
        payload.get("allow_huge")
        or payload.get("confirm_huge")
        or payload.get("force_huge")
    )
    try:
        duration_sec = float(payload.get("duration_sec") or 0)
    except Exception:
        duration_sec = 0.0

    p_obj = Path(path)
    oversized = _oversized_warnings(p_obj, duration_sec)
    if oversized and not allow_huge:
        return _err(
            "File quá lớn/dài cho YouTube — cần xác nhận thủ công",
            400,
            need_confirm=True,
            warnings=oversized,
            duration_sec=duration_sec,
            size_bytes=p_obj.stat().st_size if p_obj.is_file() else 0,
        )

    queue = _read_queue()
    if skip_if_uploading and any(it.get("status_ui") == "uploading" or _is_active(str(it.get("status"))) and "đang" in _norm_status(str(it.get("status"))) for it in (_enrich_queue_item(x) for x in queue)):
        # still allow enqueue — just note
        pass

    norm_target = os.path.normcase(os.path.normpath(path))
    for it in queue:
        p = it.get("path") or it.get("video_path") or ""
        if os.path.normcase(os.path.normpath(p)) == norm_target and _is_active(str(it.get("status"))):
            return _ok({"already_queued": True, "message": "Video đã có trong hàng chờ!", "queue_len": len(queue)})

    new_item = {
        "path": str(Path(path).resolve()),
        "title": title,
        "description": description,
        "tags": tags,
        "privacy": privacy,
        "category_id": str(payload.get("category_id") or "22"),
        "thumb": str(Path(thumb).resolve()) if thumb and Path(thumb).is_file() else "",
        "account": account,
        "ip": "",
        "status": "chờ",
        "progress": 0.0,
        "url": "",
        "publish_at": publish_at,
        "playlist_id": playlist_id,
    }
    if duration_sec:
        new_item["duration_sec"] = duration_sec
    if priority:
        queue.insert(0, new_item)
    else:
        queue.append(new_item)
    if _write_queue(queue):
        msg = "Đã thêm video vào hàng chờ Upload YouTube!"
        if oversized:
            msg += " (đã xác nhận file dài/nặng)"
        return _ok({
            "message": msg,
            "queue_len": len(queue),
            "item": _enrich_queue_item(new_item),
            "warnings": oversized,
        })
    return _err("Không ghi được queue_state.json", 500)


def remove_upload_item(payload: dict) -> tuple[int, dict]:
    index = payload.get("index")
    path = payload.get("path") or payload.get("video_path")
    queue = _read_queue()
    removed = False
    if index is not None and 0 <= int(index) < len(queue):
        it = queue[int(index)]
        # Nếu đang upload → skip thay vì xoá cứng (an toàn hơn)
        if _is_active(str(it.get("status"))) and "đang" in _norm_status(str(it.get("status"))):
            return skip_upload_item({"index": int(index)})
        queue.pop(int(index))
        removed = True
    elif path:
        norm_p = os.path.normcase(os.path.normpath(str(path)))
        new_q = []
        for it in queue:
            p = it.get("path") or it.get("video_path") or ""
            if os.path.normcase(os.path.normpath(p)) == norm_p:
                removed = True
                continue
            new_q.append(it)
        queue = new_q
    if removed and _write_queue(queue):
        return _ok({"message": "Đã xoá khỏi hàng chờ", "queue_len": len(queue)})
    return _err("Không tìm thấy video để xoá")


def clear_done_uploads() -> tuple[int, dict]:
    raw = _read_queue()
    n0 = len(raw)
    kept = []
    for it in raw:
        ui = _ui_status(str(it.get("status")))
        if ui in ("queued", "uploading"):
            kept.append(it)
    removed = n0 - len(kept)
    _write_queue(kept)
    return _ok({"removed": removed, "cleared": removed, "queue_len": len(kept)})


def clear_skipped() -> tuple[int, dict]:
    raw = _read_queue()
    kept = [it for it in raw if not _is_skip(str(it.get("status")))]
    removed = len(raw) - len(kept)
    _write_queue(kept)
    return _ok({"removed": removed, "queue_len": len(kept)})


def skip_upload_item(payload: dict) -> tuple[int, dict]:
    """Đánh dấu 1 mục (chờ hoặc đang tải) = bỏ qua."""
    index = payload.get("index")
    queue = _read_queue()
    if index is None or not (0 <= int(index) < len(queue)):
        return _err("Thiếu/sai index")
    it = queue[int(index)]
    st = str(it.get("status") or "")
    if _is_done(st):
        return _err("Mục đã xong — không skip")
    was_uploading = _ui_status(st) == "uploading" or "đang" in _norm_status(st)
    it["status"] = "bỏ qua"
    it["progress"] = float(it.get("progress") or 0)
    it["error"] = str(payload.get("reason") or "skipped by Films UI")
    queue[int(index)] = it
    if not _write_queue(queue):
        return _err("Không ghi queue", 500)
    if was_uploading:
        try:
            SKIP_FLAG.write_text(str(time.time()), encoding="utf-8")
        except Exception:
            pass
    return _ok({"message": f"Đã bỏ qua mục #{int(index)+1}", "item": _enrich_queue_item(it), "signaled": was_uploading})


def skip_current_upload() -> tuple[int, dict]:
    """Skip video đang upload (+ báo hiệu yt_uploader dừng item hiện tại).

    Desktop GUI yt_uploader cũng đọc skip_current.flag — kể cả khi queue_state
    không có status 'đang upload' (queue trống / GUI tự quản), vẫn ghi flag nếu
    process đang chạy.
    """
    queue = _read_queue()
    idx = None
    for i, it in enumerate(queue):
        if _ui_status(str(it.get("status"))) == "uploading" or "đang" in _norm_status(str(it.get("status"))):
            idx = i
            break
    if idx is None:
        running, pid = _is_yt_uploader_running()
        if running:
            try:
                SKIP_FLAG.write_text(str(time.time()), encoding="utf-8")
            except Exception as e:
                return _err(f"Không tạo skip flag: {e}", 500)
            return _ok({
                "message": "Đã gửi tín hiệu skip tới yt_uploader (queue không có item đang tải)",
                "signaled": True,
                "pid": pid,
                "skip_flag": True,
            })
        return _err("Không có video đang upload (yt_uploader không chạy / queue trống)")
    return skip_upload_item({"index": idx, "reason": "skip current"})


def move_upload_item(payload: dict) -> tuple[int, dict]:
    """Đổi thứ tự hàng chờ: index → to_index."""
    try:
        i = int(payload.get("index"))
        j = int(payload.get("to_index"))
    except Exception:
        return _err("Cần index và to_index")
    queue = _read_queue()
    if not (0 <= i < len(queue)) or not (0 <= j < len(queue)):
        return _err("index ngoài phạm vi")
    it = queue.pop(i)
    queue.insert(j, it)
    _write_queue(queue)
    return _ok({"queue_len": len(queue), "message": f"Đã chuyển #{i+1} → #{j+1}"})


def save_uploader_settings(payload: dict) -> tuple[int, dict]:
    cur = _read_json(YT_SETTINGS, {})
    if not isinstance(cur, dict):
        cur = {}
    for k in ("default_privacy", "default_account", "auto_launch", "chunk_mb",
              "auto_clear_done", "notify_telegram"):
        if k in payload:
            cur[k] = payload[k]
    if not _write_json(YT_SETTINGS, cur):
        return _err("Không ghi settings.json", 500)
    return _ok({"settings": cur, "message": "Đã lưu tuỳ chọn uploader"})


def launch_uploader() -> tuple[int, dict]:
    running, pid = _is_yt_uploader_running()
    if running:
        return _ok({"message": f"yt_uploader đã chạy (PID {pid})", "pid": pid})
    yt_script = YT_DIR / "yt_uploader.py"
    if not yt_script.is_file():
        return _err(f"Không tìm thấy {yt_script}", 404)
    try:
        flags = 0
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags |= subprocess.DETACHED_PROCESS
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, str(yt_script)], cwd=str(YT_DIR), creationflags=flags)
        time.sleep(1.0)
        return _ok({"message": "Đã bật yt_uploader nền"})
    except Exception as e:
        return _err(f"Lỗi khởi chạy: {e}", 500)


def dispatch(method: str, path: str, body: dict | None = None) -> tuple[int, dict] | None:
    """Router nhỏ cho agent_tele_cmd. None = không khớp."""
    m = (method or "GET").upper()
    p = (path or "").rstrip("/") or "/"
    body = body or {}

    if m == "GET" and p == "/api/settings":
        return get_settings()
    if m == "POST" and p == "/api/settings":
        return update_settings(body)
    if m == "POST" and p == "/api/settings/test-telegram":
        return test_telegram(body)
    if m == "POST" and p == "/api/settings/test-llm":
        return test_llm(body)

    if m == "GET" and p == "/api/uploader/status":
        return get_uploader_status()
    if m == "POST" and p == "/api/uploader/enqueue":
        return enqueue_upload(body)
    if m == "POST" and p == "/api/uploader/remove":
        return remove_upload_item(body)
    if m == "POST" and p == "/api/uploader/clear-done":
        return clear_done_uploads()
    if m == "POST" and p == "/api/uploader/clear-skipped":
        return clear_skipped()
    if m == "POST" and p == "/api/uploader/launch":
        return launch_uploader()
    if m == "POST" and p == "/api/uploader/skip":
        return skip_upload_item(body)
    if m == "POST" and p == "/api/uploader/skip-current":
        return skip_current_upload()
    if m == "POST" and p == "/api/uploader/move":
        return move_upload_item(body)
    if m == "POST" and p == "/api/uploader/settings":
        return save_uploader_settings(body)
    return None
