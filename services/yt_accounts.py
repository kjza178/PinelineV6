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
        key = a.get("client_key") or a.get("project_id")
        out.append({**{k: a.get(k) for k in ("id", "channel_id", "title", "thumb", "email", "added_at")},
                    "client_key": key, "default": a.get("id") == d.get("default"), "token": _token_path(a["id"]).is_file(),
                    "shares_client": [b.get("title") for b in d["accounts"]
                                      if b.get("id") != a.get("id") and (b.get("client_key") or b.get("project_id")) == key]})
    return {"accounts": out, "default": d.get("default"), "client": client_info(), "clients": clients_info()}


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


# ── OAuth client — MỖI KÊNH MỘT CLIENT (2026-11-21) ──
# Mỗi kênh nên đăng qua Google Cloud project / OAuth client RIÊNG (giảm dấu hiệu «kênh liên quan», quota riêng).
# Kho: data/yt_accounts/clients/<key>.json {"web": {client_id, client_secret}, "name"}; key = số project (đầu client_id).
# Nguồn cũ vẫn nhận: data/yt_accounts/client_secret.json (key "default"), project Kênh test (key "tc:<id>").
CLIENTS = DATA / "clients"


def _web(c: dict, redirect_uri: str) -> dict:
    return {"web": {"client_id": c["client_id"], "client_secret": c["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": [redirect_uri]}}


def _clients() -> list[dict]:
    """[{key, name, client_id, client_secret, source}] — mọi OAuth client dùng được."""
    out: list[dict] = []
    if CLIENTS.is_dir():
        for f in sorted(CLIENTS.glob("*.json")):
            try:
                raw = f.read_text(encoding="utf-8")
                c = _parse_client(raw)
                name = (json.loads(raw).get("name") or "") if raw.strip().startswith("{") else ""
                out.append({"key": f.stem, "name": name or f"Project {f.stem}", **c, "source": "app"})
            except (OSError, ValueError) as e:
                print(f"[yt-accounts] client {f.name} hỏng: {e}", flush=True)
    own = DATA / "client_secret.json"
    if own.is_file():
        try:
            c = _parse_client(own.read_text(encoding="utf-8"))
            if not any(x["client_id"] == c["client_id"] for x in out):
                out.append({"key": "default", "name": "client_secret.json", **c, "source": "app"})
        except (OSError, ValueError) as e:
            print(f"[yt-accounts] client_secret.json hỏng: {e}", flush=True)
    try:
        import tools.test_channel as tc
        projs = json.loads(tc.PROJECTS_FILE.read_text(encoding="utf-8")) if tc.PROJECTS_FILE.is_file() else []
        for p in projs:
            if p.get("client_id") and p.get("client_secret") and not any(x["client_id"] == p["client_id"] for x in out):
                out.append({"key": f"tc:{p['id']}", "name": f"Kênh test · {p.get('name') or p['id']}",
                            "client_id": p["client_id"], "client_secret": p["client_secret"], "source": "test_channel"})
    except Exception as e:  # noqa: BLE001
        print(f"[yt-accounts] đọc project kênh test lỗi: {e}", flush=True)
    return out


def _client_by_key(key: str | None) -> dict:
    cs = _clients()
    if not cs:
        raise ValueError("Chưa có OAuth client Google — thêm file JSON ở khung «Kênh YouTube» (mỗi kênh 1 client).")
    if not key:
        used = {a.get("client_key") for a in _read()["accounts"]}
        free = [c for c in cs if c["key"] not in used]
        if len(cs) > 1 and not free:
            raise ValueError("mọi OAuth client đều đã có kênh — thêm client mới cho kênh này (mỗi kênh 1 client)")
        return (free or cs)[0]
    c = next((c for c in cs if c["key"] == key), None)
    if not c:
        raise ValueError(f"không có OAuth client «{key}»")
    return c


def _client_config(redirect_uri: str, key: str | None = None) -> tuple[dict, str]:
    c = _client_by_key(key)
    return _web(c, redirect_uri), c["key"]


def _parse_client(raw: str) -> dict:
    """JSON tải từ Google Cloud (loại «Web application» hoặc «Desktop app») → {client_id, client_secret}."""
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise ValueError(f"không phải JSON hợp lệ: {e}") from e
    c = (d.get("web") or d.get("installed") or d) if isinstance(d, dict) else {}
    cid, sec = str(c.get("client_id") or "").strip(), str(c.get("client_secret") or "").strip()
    if not cid.endswith(".apps.googleusercontent.com") or not sec:
        raise ValueError("file thiếu client_id / client_secret (tải đúng file OAuth client ID ở Google Cloud → Credentials)")
    return {"client_id": cid, "client_secret": sec}


def save_client(raw: str, name: str = "") -> dict:
    """Thêm 1 OAuth client (dán / chọn file JSON trong app) → data/yt_accounts/clients/<số project>.json."""
    c = _parse_client(raw)
    from core.fileio import atomic_write_json
    key = "".join(ch for ch in c["client_id"].split("-")[0] if ch.isalnum())[:40] or "client"
    CLIENTS.mkdir(parents=True, exist_ok=True)
    atomic_write_json(CLIENTS / f"{key}.json", {"web": c, "name": (name or "").strip()[:60]}, fsync=False)
    return list_accounts()


def remove_client(key: str) -> dict:
    used = [a.get("title") for a in _read()["accounts"] if a.get("client_key") == key]
    if used:
        raise ValueError(f"client đang dùng cho kênh: {', '.join(used)} — gỡ kênh trước")
    f = CLIENTS / f"{''.join(ch for ch in key if ch.isalnum())}.json"
    if not f.is_file():
        raise ValueError("chỉ gỡ được client thêm trong app")
    f.unlink()
    return list_accounts()


def clients_info() -> list[dict]:
    accs = _read()["accounts"]
    return [{"key": c["key"], "name": c["name"], "source": c["source"], "client_tail": c["client_id"].split("-")[0][-6:],
             "used_by": [a.get("title") for a in accs if a.get("client_key") == c["key"]]} for c in _clients()]


def client_info() -> dict:
    cs = _clients()
    return {"ok": True, "n": len(cs)} if cs else {"ok": False, "error": "Chưa có OAuth client Google"}


def auth_url(redirect_uri: str, client_key: str | None = None) -> dict:
    from google_auth_oauthlib.flow import Flow
    cfg, key = _client_config(redirect_uri, client_key)
    flow = Flow.from_client_config(cfg, scopes=SCOPES, redirect_uri=redirect_uri)
    import secrets
    # state «yt-…»: callback chung /test-channel/callback (redirect URI đã khai báo cho OAuth client) nhận ra đây là
    # đăng nhập kênh ĐĂNG phim, không phải kênh test
    url, state = flow.authorization_url(prompt="select_account consent", access_type="offline",
                                        include_granted_scopes="true", state="yt-" + secrets.token_urlsafe(16))
    DATA.mkdir(parents=True, exist_ok=True)
    pend = {"state": state, "client_key": key, "verifier": getattr(flow, "code_verifier", None), "at": time.time()}
    (DATA / "pending_oauth.json").write_text(json.dumps(pend), encoding="utf-8")
    shared = [a.get("title") for a in _read()["accounts"] if a.get("client_key") == key]
    return {"ok": True, "auth_url": url, "client_key": key, "shared_with": shared}


def finish(code: str, state: str, redirect_uri: str) -> dict:
    """Đổi code → token, hỏi kênh (channels.list mine) → lưu tài khoản (id = channel id) + client đã dùng."""
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    pf = DATA / "pending_oauth.json"
    pend = json.loads(pf.read_text(encoding="utf-8")) if pf.is_file() else {}
    if pend.get("state") and state and pend["state"] != state:
        raise ValueError("phiên đăng nhập không khớp (state) — bấm «Thêm kênh» lại")
    cfg, key = _client_config(redirect_uri, pend.get("client_key") or None)
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
           "email": "", "client_key": key, "added_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
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
