"""services/yt_accounts.py — Kênh YouTube để ĐĂNG phim (V6, nhiều kênh) — 2026-11-21.

Thay cho thư mục ngoài `yt_uploader\\accounts` (máy mới không có): đăng nhập Google ngay trong app, mỗi lần thêm 1 kênh.
  data/yt_accounts/accounts.json   [{id, channel_id, title, thumb, email, project_id, added_at}], + "default"
  data/yt_accounts/tokens/<id>.json token OAuth (refresh_token) — KHÔNG commit (gitignore data/yt_accounts/)
OAuth client: data/yt_accounts/client_secret.json (tải từ Google Cloud) nếu có, không thì dùng chung project của
«Kênh test» (data/test_channel/projects.json). Đăng video: agents.auto_publisher.get_youtube_service(id) → service(id).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "yt_accounts"
ACCOUNTS = DATA / "accounts.json"
TOKENS = DATA / "tokens"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",
]
_lock = threading.Lock()


# ── kho tài khoản ──
def _read() -> dict:
    try:
        d = json.loads(ACCOUNTS.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("accounts", [])
            return d
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print(f"[yt-accounts] đọc {ACCOUNTS.name} lỗi: {e}", flush=True)
    return {"accounts": [], "default": None}


def _write(d: dict) -> None:
    from core.fileio import atomic_write_json
    DATA.mkdir(parents=True, exist_ok=True)
    atomic_write_json(ACCOUNTS, d, fsync=False)


def _token_path(acc_id: str) -> Path:
    safe = "".join(c for c in str(acc_id) if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("id kênh không hợp lệ")
    return TOKENS / f"{safe}.json"


def list_accounts() -> dict:
    d = _read()
    out = []
    for a in d["accounts"]:
        out.append({**{k: a.get(k) for k in ("id", "channel_id", "title", "thumb", "email", "added_at", "project_id")},
                    "default": a.get("id") == d.get("default"), "token": _token_path(a["id"]).is_file()})
    return {"accounts": out, "default": d.get("default"), "client": client_info()}


def get(acc_id: str | None) -> dict | None:
    if not acc_id:
        return None
    d = _read()
    if acc_id == "default":
        acc_id = d.get("default")
    return next((a for a in d["accounts"] if a.get("id") == acc_id), None)


def has(acc_id: str | None) -> bool:
    a = get(acc_id)
    return bool(a and _token_path(a["id"]).is_file())


def default_id() -> str | None:
    d = _read()
    ids = [a["id"] for a in d["accounts"]]
    if d.get("default") in ids:
        return d["default"]
    return ids[0] if ids else None


def set_default(acc_id: str) -> dict:
    with _lock:
        d = _read()
        if not any(a.get("id") == acc_id for a in d["accounts"]):
            raise ValueError("không có kênh này")
        d["default"] = acc_id
        _write(d)
    return list_accounts()


def remove(acc_id: str) -> dict:
    with _lock:
        d = _read()
        d["accounts"] = [a for a in d["accounts"] if a.get("id") != acc_id]
        if d.get("default") == acc_id:
            d["default"] = d["accounts"][0]["id"] if d["accounts"] else None
        _write(d)
    try:
        _token_path(acc_id).unlink(missing_ok=True)
    except OSError as e:
        print(f"[yt-accounts] xoá token {acc_id} lỗi: {e}", flush=True)
    return list_accounts()


# ── OAuth client ──
def _client_config(redirect_uri: str) -> tuple[dict, str]:
    """(client_config kiểu «web», project_id). Lỗi rõ ràng khi chưa có OAuth client nào."""
    own = DATA / "client_secret.json"
    if own.is_file():
        raw = json.loads(own.read_text(encoding="utf-8"))
        c = raw.get("web") or raw.get("installed") or raw
        return {"web": {"client_id": c["client_id"], "client_secret": c["client_secret"],
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": [redirect_uri]}}, "own"
    try:
        import tools.test_channel as tc
        projs = json.loads(tc.PROJECTS_FILE.read_text(encoding="utf-8")) if tc.PROJECTS_FILE.is_file() else []
    except Exception as e:  # noqa: BLE001
        print(f"[yt-accounts] đọc project kênh test lỗi: {e}", flush=True)
        projs = []
    if not projs:
        raise ValueError("Chưa có OAuth client Google: đặt file client_secret.json (Google Cloud → APIs & Services → "
                         f"Credentials → OAuth client «Web») vào {own}, hoặc thêm project ở Kênh test.")
    p = projs[0]
    return {"web": {"client_id": p["client_id"], "client_secret": p["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": [redirect_uri]}}, p["id"]


def client_info() -> dict:
    own = DATA / "client_secret.json"
    if own.is_file():
        return {"ok": True, "source": "client_secret.json"}
    try:
        _client_config("http://127.0.0.1/")
        return {"ok": True, "source": "project Kênh test"}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


def auth_url(redirect_uri: str) -> dict:
    from google_auth_oauthlib.flow import Flow
    cfg, pid = _client_config(redirect_uri)
    flow = Flow.from_client_config(cfg, scopes=SCOPES, redirect_uri=redirect_uri)
    import secrets
    # state «yt-…»: callback chung /test-channel/callback (redirect URI đã khai báo cho OAuth client) nhận ra đây là
    # đăng nhập kênh ĐĂNG phim, không phải kênh test
    url, state = flow.authorization_url(prompt="select_account consent", access_type="offline",
                                        include_granted_scopes="true", state="yt-" + secrets.token_urlsafe(16))
    DATA.mkdir(parents=True, exist_ok=True)
    pend = {"state": state, "project_id": pid, "verifier": getattr(flow, "code_verifier", None), "at": time.time()}
    (DATA / "pending_oauth.json").write_text(json.dumps(pend), encoding="utf-8")
    return {"ok": True, "auth_url": url}


def finish(code: str, state: str, redirect_uri: str) -> dict:
    """Đổi code → token, hỏi kênh (channels.list mine) → lưu tài khoản (id = channel id)."""
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    pf = DATA / "pending_oauth.json"
    pend = json.loads(pf.read_text(encoding="utf-8")) if pf.is_file() else {}
    if pend.get("state") and state and pend["state"] != state:
        raise ValueError("phiên đăng nhập không khớp (state) — bấm «Thêm kênh» lại")
    cfg, pid = _client_config(redirect_uri)
    flow = Flow.from_client_config(cfg, scopes=SCOPES, redirect_uri=redirect_uri, state=state or None)
    if pend.get("verifier"):
        flow.code_verifier = pend["verifier"]
    flow.fetch_token(code=code)
    creds = flow.credentials
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    items = (yt.channels().list(mine=True, part="snippet").execute() or {}).get("items") or []
    if not items:
        raise ValueError("tài khoản Google này chưa có kênh YouTube")
    ch = items[0]
    sn = ch.get("snippet") or {}
    acc = {"id": ch["id"], "channel_id": ch["id"], "title": sn.get("title") or ch["id"],
           "thumb": ((sn.get("thumbnails") or {}).get("default") or {}).get("url") or "",
           "email": "", "project_id": pid, "added_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    TOKENS.mkdir(parents=True, exist_ok=True)
    _token_path(acc["id"]).write_text(creds.to_json(), encoding="utf-8")
    with _lock:
        d = _read()
        d["accounts"] = [a for a in d["accounts"] if a.get("id") != acc["id"]] + [acc]
        if not d.get("default"):
            d["default"] = acc["id"]
        _write(d)
    try:
        pf.unlink(missing_ok=True)
    except OSError as e:
        print(f"[yt-accounts] xoá pending_oauth lỗi: {e}", flush=True)
    return {"ok": True, "account": acc}


def service(acc_id: str):
    """YouTube API service của kênh (tự làm mới token)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    a = get(acc_id)
    if not a:
        raise FileNotFoundError(f"không có kênh «{acc_id}» trong V6 — thêm ở Phim đã làm → Kênh YouTube")
    tp = _token_path(a["id"])
    if not tp.is_file():
        raise FileNotFoundError(f"kênh «{a.get('title')}» chưa có token — bấm «Đăng nhập lại»")
    creds = Credentials.from_authorized_user_info(json.loads(tp.read_text(encoding="utf-8")))
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        tp.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)
