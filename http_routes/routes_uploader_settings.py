"""routes_uploader_settings.py — API Router cho Quản lý Cài đặt (Settings) & YouTube Uploader.

Tích hợp trực tiếp vào Pineline V4 Server (:8804):
1. Settings:
   - Thay đổi thư viện phim (LIBRARY_DIR, WORK_DIR, YT_UPLOADER_DIR)
   - Cấu hình AI (LLM_BASE_URL, LLM_MODEL, TRANSLATE_MODEL, AGENT_MODEL...)
   - Cấu hình Telegram (TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS, AGENT_TELEGRAM)
   - Test kết nối LLM & gửi tin nhắn test Telegram
2. YouTube Uploader (Đồng bộ Z:\\yt_uploader):
   - Danh sách tài khoản / kênh YouTube
   - Thêm video vào hàng chờ upload (queue_state.json)
   - Theo dõi tiến độ upload thời gian thực
   - Lịch sử video đã upload (upload_history.jsonl)
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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent.parent  # project root (file lives in http_routes/)
sys.path.insert(0, str(ROOT))

import core.config as config
import core.film_db as film_db

router = APIRouter()

YT_DIR = _machine.yt_uploader_dir()
YT_QUEUE = YT_DIR / "queue_state.json"
YT_HISTORY = YT_DIR / "upload_history.jsonl"
YT_SETTINGS = YT_DIR / "settings.json"


# ══════════════════════════════════════════════════════════════════════════════
# 1. SETTINGS API
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/settings")
def get_all_settings():
    """Lấy toàn bộ cấu hình hiện tại của Pineline V4."""
    config._load_cfg(force=True)

    # Lấy danh sách model LLM có sẵn nếu có
    avail_models = []
    try:
        from engines.llm import list_available_models
        m_res = list_available_models()
        avail_models = m_res.get("models") or []
    except Exception:
        pass

    tele_token = config.get("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    tele_users = config.get("TELEGRAM_ALLOWED_USERS") or os.getenv("TELEGRAM_ALLOWED_USERS") or ""
    tele_active = (config.get("AGENT_TELEGRAM") or os.getenv("AGENT_TELEGRAM") or "0") in ("1", "true", "yes")

    storage_dict = {
        "LIBRARY_DIR": config.get("LIBRARY_DIR") or r"D:\Lib",
        # F6: mặc định theo instance (env WORK_DIR do core.instance.apply_env đặt), không cứng V5.
        "WORK_DIR": config.get("WORK_DIR") or os.getenv("WORK_DIR") or "",
        "YT_UPLOADER_DIR": config.get("YT_UPLOADER_DIR") or str(YT_DIR),
        "TEMP": os.getenv("TEMP") or "",
    }
    ai_dict = {
        "LLM_BASE_URL": config.get("LLM_BASE_URL") or "http://127.0.0.1:8083/v1",
        "LLM_API_KEY": config.get("LLM_API_KEY") or "sk-local",
        "LLM_MODEL": config.get("LLM_MODEL") or "local",
        "TRANSLATE_MODEL": config.get("TRANSLATE_MODEL") or "local",
        "REVIEW_MODEL": config.get("REVIEW_MODEL") or "local",
        "AGENT_MODEL": config.get("AGENT_MODEL") or "ollama/gemma4:31b-cloud",
        "AGENT_FALLBACK_MODEL": config.get("AGENT_FALLBACK_MODEL") or "openclaw",
        "AGENT_BASE_URL": config.get("AGENT_BASE_URL") or "",
        "AGENT_API_KEY": config.get("AGENT_API_KEY") or "",
        "available_models": avail_models,
    }
    tele_dict = {
        "TELEGRAM_BOT_TOKEN": tele_token,
        "TELEGRAM_ALLOWED_USERS": tele_users,
        "AGENT_TELEGRAM": tele_active,
    }
    tts_dict = {
        "VIENEU_DEVICE": config.get("VIENEU_DEVICE") or "cuda",
        "VIENEU_BATCH": config.get("VIENEU_BATCH") or "8",
        "OMNI_MODEL": config.get("OMNI_MODEL") or "splendor1811/omnivoice-vietnamese",
        "RENDER_ENCODER": config.get("RENDER_ENCODER") or "nvenc",
    }
    flat = {**storage_dict, **ai_dict, **tele_dict, **tts_dict}

    return {
        "ok": True,
        "config": flat,
        "available_models": avail_models,
        "storage": storage_dict,
        "ai": ai_dict,
        "telegram": tele_dict,
        "tts": tts_dict,
    }


@router.post("/api/settings")
def update_settings(payload: Dict[str, Any] = Body(...)):
    """Lưu cài đặt mới vào pineline.config.json và đồng bộ biến môi trường."""
    updates: Dict[str, Any] = {}

    # Storage
    if "storage" in payload:
        st = payload["storage"]
        for k in ("LIBRARY_DIR", "WORK_DIR", "YT_UPLOADER_DIR"):
            if k in st:
                updates[k] = str(st[k]).strip()

    # AI
    if "ai" in payload:
        ai = payload["ai"]
        for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "TRANSLATE_MODEL",
                  "REVIEW_MODEL", "AGENT_MODEL", "AGENT_FALLBACK_MODEL",
                  "AGENT_BASE_URL", "AGENT_API_KEY"):
            if k in ai:
                updates[k] = str(ai[k]).strip()

    # Telegram
    if "telegram" in payload:
        tg = payload["telegram"]
        if "TELEGRAM_BOT_TOKEN" in tg:
            updates["TELEGRAM_BOT_TOKEN"] = str(tg["TELEGRAM_BOT_TOKEN"]).strip()
        if "TELEGRAM_ALLOWED_USERS" in tg:
            updates["TELEGRAM_ALLOWED_USERS"] = str(tg["TELEGRAM_ALLOWED_USERS"]).strip()
        if "AGENT_TELEGRAM" in tg:
            updates["AGENT_TELEGRAM"] = "1" if tg["AGENT_TELEGRAM"] else "0"

    # TTS & Render
    if "tts" in payload:
        tt = payload["tts"]
        for k in ("VIENEU_DEVICE", "VIENEU_BATCH", "OMNI_MODEL", "RENDER_ENCODER"):
            if k in tt:
                updates[k] = str(tt[k]).strip()

    # Nếu người dùng gửi trực tiếp phẳng các key
    for k, v in payload.items():
        if k not in ("storage", "ai", "telegram", "tts") and isinstance(v, (str, int, float, bool)):
            updates[k] = str(v).strip() if isinstance(v, str) else v

    cfg = config.save_config(updates)
    return {"ok": True, "message": "Đã lưu cài đặt thành công!", "updated_keys": list(updates.keys())}


@router.post("/api/settings/test-telegram")
def test_telegram_msg(payload: Dict[str, Any] = Body(...)):
    """Thử gửi 1 tin nhắn test đến Telegram để kiểm tra bot token và chat ID."""
    import urllib.parse
    import urllib.request

    token = str(payload.get("token") or config.get("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    user_ids = str(payload.get("users") or config.get("TELEGRAM_ALLOWED_USERS") or os.getenv("TELEGRAM_ALLOWED_USERS") or "").strip()

    if not token:
        return JSONResponse({"ok": False, "error": "Chưa điền TELEGRAM_BOT_TOKEN"}, status_code=400)
    if not user_ids:
        return JSONResponse({"ok": False, "error": "Chưa điền Chat ID / User ID người nhận"}, status_code=400)

    uids = [u.strip() for u in user_ids.split(",") if u.strip()]
    sent_count = 0
    errors = []

    text = "🔔 [PineLine V4] Test kết nối Telegram thành công! Bot sẵn sàng nhận lệnh & gửi thông báo."

    for uid in uids:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": uid, "text": text, "parse_mode": "HTML"}).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    sent_count += 1
        except Exception as e:
            errors.append(f"UID {uid}: {e}")

    if sent_count > 0:
        return {"ok": True, "message": f"Đã gửi tin nhắn test thành công tới {sent_count}/{len(uids)} người nhận!", "errors": errors}
    return JSONResponse({"ok": False, "error": f"Gửi tin nhắn thất bại: {'; '.join(errors)}"}, status_code=500)


@router.post("/api/settings/test-llm")
def test_llm_conn(payload: Dict[str, Any] = Body(...)):
    """Thử gửi 1 prompt nhỏ đến LLM để đo độ trễ và kiểm tra model."""
    import urllib.request

    base_url = str(payload.get("base_url") or config.get("LLM_BASE_URL") or "http://127.0.0.1:8083/v1").rstrip("/")
    api_key = str(payload.get("api_key") or config.get("LLM_API_KEY") or "sk-local").strip()
    model = str(payload.get("model") or config.get("LLM_MODEL") or "local").strip()

    t0 = time.time()
    url = f"{base_url}/chat/completions"
    data = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Xin chào, hãy trả lời ngắn gọn: 1+1=?"}],
        "max_tokens": 20,
        "temperature": 0.1,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            elapsed = round(time.time() - t0, 2)
            res_body = json.loads(resp.read().decode("utf-8", errors="replace"))
            reply = res_body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return {
                "ok": True,
                "message": f"Kết nối LLM thành công ({elapsed}s)!",
                "reply": reply,
                "model": model,
                "elapsed_sec": elapsed,
            }
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Lỗi kết nối LLM ({url}): {e}"}, status_code=500)


# ══════════════════════════════════════════════════════════════════════════════
# 2. YOUTUBE UPLOADER API (Đồng bộ Z:\yt_uploader)
# ══════════════════════════════════════════════════════════════════════════════

def _is_yt_uploader_running() -> bool:
    """Kiểm tra xem tiến trình yt_uploader.py có đang chạy không."""
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            try:
                pname = (p.info.get("name") or "").lower()
                if "python" in pname:
                    cmd = p.cmdline()
                    if any("yt_uploader" in str(x).lower() for x in cmd):
                        return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _read_upload_history(limit: int = 50) -> List[dict]:
    r"""Đọc lịch sử upload từ Z:\yt_uploader\upload_history.jsonl."""
    if not YT_HISTORY.is_file():
        return []
    history = []
    try:
        with open(YT_HISTORY, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        history.append(json.loads(line))
                    except Exception:
                        pass
        return list(reversed(history[-limit:]))
    except Exception:
        return []


def _read_upload_queue() -> List[dict]:
    r"""Đọc hàng đợi upload từ Z:\yt_uploader\queue_state.json."""
    if not YT_QUEUE.is_file():
        return []
    try:
        data = json.loads(YT_QUEUE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_upload_queue(items: List[dict]) -> bool:
    r"""Ghi an toàn vào Z:\yt_uploader\queue_state.json."""
    try:
        YT_DIR.mkdir(parents=True, exist_ok=True)
        YT_QUEUE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


@router.get("/api/uploader/status")
def get_uploader_status():
    """Lấy trạng thái tổng thể của YouTube Uploader."""
    import tools.lib_check as lib_check

    accounts = []
    try:
        accounts = lib_check.list_yt_accounts()
    except Exception:
        pass

    queue = _read_upload_queue()
    history = _read_upload_history(limit=40)
    is_running = _is_yt_uploader_running()

    # Đọc settings của yt_uploader
    yt_opts = {}
    if YT_SETTINGS.is_file():
        try:
            yt_opts = json.loads(YT_SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "ok": True,
        "is_running": is_running,
        "accounts": accounts,
        "queue_count": len(queue),
        "queue": queue,
        "history_count": len(history),
        "history": history,
        "settings": yt_opts,
        "yt_dir": str(YT_DIR),
    }


@router.post("/api/uploader/enqueue")
def enqueue_upload(payload: Dict[str, Any] = Body(...)):
    """Thêm một hoặc nhiều video vào hàng chờ upload của yt_uploader."""
    path = str(payload.get("path") or "").strip()
    if not path or not Path(path).is_file():
        return JSONResponse({"ok": False, "error": f"Không tìm thấy file video: {path}"}, status_code=400)

    title = str(payload.get("title") or Path(path).stem).strip()
    description = str(payload.get("description") or f"Xem phim {title} trọn bộ lồng tiếng hay nhất.").strip()
    tags = str(payload.get("tags") or "").strip()
    privacy = str(payload.get("privacy") or "unlisted").strip().lower()
    category_id = str(payload.get("category_id") or "22").strip()
    account = str(payload.get("account") or "default").strip()
    thumb = str(payload.get("thumb") or "").strip()
    playlist_id = str(payload.get("playlist_id") or "").strip()

    queue = _read_upload_queue()

    # Kiểm tra xem đường dẫn này đã có trong hàng chờ chưa
    norm_target = os.path.normcase(os.path.normpath(path))
    for it in queue:
        if os.path.normcase(os.path.normpath(it.get("path") or "")) == norm_target and it.get("status") in ("chờ", "đang tải"):
            return {"ok": True, "already_queued": True, "message": "Video đã có sẵn trong hàng chờ!", "queue_len": len(queue)}

    new_item = {
        "path": str(Path(path).resolve()),
        "title": title,
        "description": description,
        "tags": tags,
        "privacy": privacy,
        "category_id": category_id,
        "thumb": str(Path(thumb).resolve()) if thumb and Path(thumb).is_file() else "",
        "account": account,
        "ip": "",
        "status": "chờ",
        "progress": 0.0,
        "url": "",
        "publish_at": str(payload.get("publish_at") or "").strip(),
        "playlist_id": playlist_id,
    }

    queue.append(new_item)
    if _write_upload_queue(queue):
        return {"ok": True, "message": "Đã thêm video vào hàng chờ Upload YouTube!", "queue_len": len(queue), "item": new_item}
    return JSONResponse({"ok": False, "error": "Không thể ghi vào queue_state.json"}, status_code=500)


@router.post("/api/uploader/remove")
def remove_upload_item(payload: Dict[str, Any] = Body(...)):
    """Xóa một item khỏi hàng chờ upload."""
    index = payload.get("index")
    path = payload.get("path")

    queue = _read_upload_queue()
    removed = False

    if index is not None and 0 <= int(index) < len(queue):
        queue.pop(int(index))
        removed = True
    elif path:
        norm_p = os.path.normcase(os.path.normpath(path))
        queue = [it for it in queue if os.path.normcase(os.path.normpath(it.get("path") or "")) != norm_p]
        removed = True

    if removed and _write_upload_queue(queue):
        return {"ok": True, "message": "Đã xóa khỏi hàng chờ", "queue_len": len(queue)}
    return JSONResponse({"ok": False, "error": "Không tìm thấy video để xóa"}, status_code=400)


@router.post("/api/uploader/clear-done")
def clear_done_uploads():
    """Xóa tất cả các video đã hoàn thành (xong/lỗi) khỏi hàng chờ."""
    queue = _read_upload_queue()
    initial_len = len(queue)
    queue = [it for it in queue if it.get("status") in ("chờ", "đang tải")]
    _write_upload_queue(queue)
    return {"ok": True, "removed": initial_len - len(queue), "queue_len": len(queue)}


@router.post("/api/uploader/launch")
def launch_yt_uploader_process():
    """Khởi động tiến trình yt_uploader.py chạy nền nếu đang tắt."""
    if _is_yt_uploader_running():
        return {"ok": True, "message": "yt_uploader đã đang chạy sẵn!"}

    yt_script = YT_DIR / "yt_uploader.py"
    if not yt_script.is_file():
        return JSONResponse({"ok": False, "error": f"Không tìm thấy {yt_script}"}, status_code=404)

    try:
        subprocess.Popen(
            [sys.executable, str(yt_script)],
            cwd=str(YT_DIR),
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        time.sleep(1.0)
        return {"ok": True, "message": "Đã gửi lệnh bật yt_uploader nền thành công!"}
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Lỗi khởi chạy yt_uploader: {e}"}, status_code=500)


def _usa_json(fn, *args, **kwargs):
    """Bridge sang uploader_settings_api (sidecar-compatible)."""
    import agents.uploader_settings_api as usa
    code, body = fn(*args, **kwargs)
    return JSONResponse(body, status_code=code)


@router.post("/api/uploader/skip")
def uploader_skip_item(payload: Dict[str, Any] = Body(...)):
    import agents.uploader_settings_api as usa
    return _usa_json(usa.skip_upload_item, payload)


@router.post("/api/uploader/skip-current")
def uploader_skip_current():
    import agents.uploader_settings_api as usa
    return _usa_json(usa.skip_current_upload)


@router.post("/api/uploader/clear-skipped")
def uploader_clear_skipped():
    import agents.uploader_settings_api as usa
    return _usa_json(usa.clear_skipped)


@router.post("/api/uploader/move")
def uploader_move_item(payload: Dict[str, Any] = Body(...)):
    import agents.uploader_settings_api as usa
    return _usa_json(usa.move_upload_item, payload)


@router.post("/api/uploader/settings")
def uploader_save_settings(payload: Dict[str, Any] = Body(...)):
    import agents.uploader_settings_api as usa
    return _usa_json(usa.save_uploader_settings, payload)


# ══════════════════════════════════════════════════════════════════════════════
# 3. AUTO-PUBLISH API
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/auto-publish/config")
@router.get("/auto-publish/config")
def auto_publish_get_config():
    import agents.auto_publisher as auto_publisher
    return {"ok": True, "config": auto_publisher.load_config()}


@router.post("/api/auto-publish/config")
@router.post("/auto-publish/config")
def auto_publish_save_config(body: dict):
    import agents.auto_publisher as auto_publisher
    cfg = auto_publisher.save_config(body)
    return {"ok": True, "config": cfg}


@router.post("/api/auto-publish/trigger")
@router.post("/auto-publish/trigger")
def auto_publish_trigger(body: dict):
    import agents.auto_publisher as auto_publisher
    job_id = body.get("job_id")
    if not job_id:
        return JSONResponse({"ok": False, "error": "Thiếu job_id"}, status_code=400)
    privacy = body.get("privacy")
    sched = body.get("schedule_hours")
    force = bool(body.get("force", False))
    allow_oversized = bool(
        body.get("allow_oversized") or body.get("allow_huge") or body.get("confirm_huge")
    )
    busy_now, active_owner = auto_publisher.is_any_job_uploading()
    if busy_now and active_owner != job_id:
        res = auto_publisher.enqueue_publish(
            job_id, privacy=privacy, schedule_hours=sched, force=force, allow_oversized=allow_oversized
        )
    else:
        res = auto_publisher.publish_job(
            job_id, privacy=privacy, schedule_hours=sched, force=force, allow_oversized=allow_oversized
        )
    if res.get("ok"):
        return res
    code = 400 if res.get("refused_oversized") else 500
    return JSONResponse(res, status_code=code)


@router.post("/api/auto-publish/scan")
@router.post("/auto-publish/scan")
def auto_publish_scan():
    import agents.auto_publisher as auto_publisher
    res = auto_publisher.scan_and_publish_completed_jobs()
    if isinstance(res, dict):
        return res
    return {"ok": True, "scanned": res}


@router.get("/api/auto-publish/status")
@router.get("/auto-publish/status")
@router.get("/api/auto-publish/state")
@router.get("/auto-publish/state")
def auto_publish_status():
    import agents.auto_publisher as auto_publisher
    return auto_publisher.AUTO_STATE


# ══════════════════════════════════════════════════════════════════════════════
# 4. CANVA CONNECT API
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/canva/status")
@router.get("/api/canva/status")
def canva_status():
    import agents.canva_client as canva_client
    cfg = canva_client.load_config()
    return {
        "ok": True,
        "configured": bool(cfg.get("client_id") and cfg.get("client_secret")),
        "connected": canva_client.is_connected(),
        "client_id": cfg.get("client_id", "")[:6] + "..." if cfg.get("client_id") else "",
    }


@router.get("/canva/auth-url")
@router.get("/api/canva/auth-url")
def canva_auth_url():
    import agents.canva_client as canva_client
    try:
        url, state = canva_client.generate_auth_url()
        return {"ok": True, "url": url, "state": state}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.get("/canva/callback")
@router.get("/api/canva/callback")
def canva_callback(code: str = "", state: str = ""):
    from fastapi.responses import HTMLResponse
    if not code:
        return HTMLResponse("<h3>❌ Không tìm thấy authorization code từ Canva.</h3>", status_code=400)
    try:
        import agents.canva_client as canva_client
        canva_client.exchange_code_for_token(code, state)
        html = """
        <!DOCTYPE html><html><head><meta charset="utf-8"><title>Kết nối Canva thành công</title>
        <style>body{background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:90vh;margin:0}
        .box{background:#161b22;padding:30px;border-radius:12px;border:1px solid #30363d;text-align:center;max-width:440px}
        h2{color:#7d2ae8;margin-top:0}
        a{color:#58a6ff;text-decoration:none}
        .b{display:inline-block;padding:10px 20px;background:#7d2ae8;color:#fff;border-radius:6px;font-weight:600;margin-top:15px}</style></head>
        <body><div class="box">
          <h2>🎉 Kết nối Canva Connect API Thành Công!</h2>
          <p>Token đã lưu vào <code>data/canva_token.json</code>.</p>
          <a href="/films/ui" class="b">Quay lại Giao diện Pineline</a>
          <script>setTimeout(function(){ if(window.opener){ window.close(); } else { window.location.href='/films/ui'; } }, 2500);</script>
        </div></body></html>
        """
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<h3>❌ Lỗi xác thực Canva: {e}</h3><br><a href='/films/ui'>Quay lại</a>", status_code=500)


@router.post("/canva/config")
@router.post("/api/canva/config")
def canva_save_config(body: dict):
    import agents.canva_client as canva_client
    cid = str(body.get("client_id") or "").strip()
    csec = str(body.get("client_secret") or "").strip()
    if not cid or not csec:
        return JSONResponse({"ok": False, "error": "Thiếu client_id hoặc client_secret"}, status_code=400)
    canva_client.save_config(cid, csec)
    return {"ok": True, "configured": True}


@router.post("/canva/create")
@router.post("/api/canva/create")
def canva_create(body: dict):
    import agents.canva_client as canva_client
    import agents.meta_studio as meta_studio
    title = str(body.get("title") or "Thumbnail YouTube").strip()
    poster_name = body.get("poster_name") or ""
    thumb_filename = body.get("thumb_filename") or ""

    thumb_bytes = None
    if thumb_filename:
        thumb_path = meta_studio.THUMB_DIR / thumb_filename
        if thumb_path.is_file():
            try:
                thumb_bytes = thumb_path.read_bytes()
            except Exception:
                pass

    poster_bytes = None
    if poster_name:
        try:
            import agents.hongguo_bridge as hg
            data, _ = hg.fetch_poster(poster_name)
            if data:
                poster_bytes = data
        except Exception:
            pass

    if not thumb_bytes and poster_bytes:
        try:
            h1 = str(body.get("hook_text_1") or "NGHỊCH THIÊN").strip()
            h2 = str(body.get("hook_text_2") or "ĐỒ THẦN").strip()
            b_badge = str(body.get("badge_text") or "TRỌN BỘ THUYẾT MINH").strip()
            s_sub = str(body.get("sub_text") or title).strip()
            st = str(body.get("style") or "fire_gold").strip()
            out_p = meta_studio.create_thumbnail(
                poster_source=poster_bytes,
                hook_text_1=h1,
                hook_text_2=h2,
                badge_text=b_badge,
                sub_text=s_sub,
                style=st,
                out_filename=thumb_filename or "thumb_auto.jpg",
            )
            if out_p and out_p.is_file():
                thumb_bytes = out_p.read_bytes()
        except Exception as e_render:
            print(f"[Canva] Auto render thumb warning: {e_render}")

    try:
        res = canva_client.create_youtube_thumbnail(
            title=title,
            poster_source=poster_bytes,
            thumb_image_source=thumb_bytes,
        )
        return res
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

