"""
canva_client.py — Tích hợp Canva Connect API chính thức cho Pineline.
Quản lý OAuth2 (PKCE), Upload Assets, Tạo Design YouTube Thumbnail (16:9) và Autofill.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_FILE = DATA_DIR / "canva_config.json"
TOKEN_FILE = DATA_DIR / "canva_token.json"

CANVA_AUTH_BASE = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
CANVA_API_BASE = "https://api.canva.com/rest/v1"

# Temporary state storage for PKCE verification
_PKCE_STORE: dict[str, dict] = {}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "client_id": os.getenv("CANVA_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("CANVA_CLIENT_SECRET", "").strip(),
        "redirect_uri": os.getenv("CANVA_REDIRECT_URI", "http://127.0.0.1:8802/canva/callback").strip(),
    }


def save_config(client_id: str, client_secret: str, redirect_uri: str = "http://127.0.0.1:8802/canva/callback") -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = {
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "redirect_uri": redirect_uri.strip(),
    }
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def load_token() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        # Kiểm tra token hết hạn chưa, nếu hết hạn thì tự refresh
        if data.get("refresh_token") and data.get("expires_at", 0) <= time.time() + 60:
            refreshed = refresh_access_token(data["refresh_token"])
            if refreshed:
                return refreshed
        return data
    except Exception:
        return None


def save_token(token_data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if "expires_in" in token_data:
        token_data["expires_at"] = time.time() + int(token_data["expires_in"])
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2, ensure_ascii=False), encoding="utf-8")


def is_connected() -> bool:
    tok = load_token()
    return bool(tok and tok.get("access_token"))


def generate_auth_url() -> tuple[str, str]:
    """Tạo URL đăng nhập OAuth2 Canva kèm PKCE (code_verifier, code_challenge)."""
    cfg = load_config()
    client_id = cfg.get("client_id")
    redirect_uri = cfg.get("redirect_uri")
    if not client_id:
        raise ValueError("Chưa cấu hình Canva Client ID")

    # Sinh PKCE
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(16)

    # Lưu PKCE vào file tạm để không bị mất khi đổi process
    PKCE_FILE = DATA_DIR / "canva_pkce.json"
    PKCE_FILE.write_text(json.dumps({
        "verifier": code_verifier,
        "state": state,
        "created_at": time.time(),
    }), encoding="utf-8")
    _PKCE_STORE[state] = {
        "verifier": code_verifier,
        "created_at": time.time(),
    }

    scopes = [
        "design:content:read",
        "design:content:write",
        "design:meta:read",
        "asset:read",
        "asset:write",
    ]
    scope_str = " ".join(scopes)

    params = {
        "code_challenge": code_challenge,
        "code_challenge_method": "s256",
        "scope": scope_str,
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    from urllib.parse import urlencode
    auth_url = f"{CANVA_AUTH_BASE}?{urlencode(params)}"
    return auth_url, state


def exchange_code(code: str, state: str = "") -> dict:
    """Đổi authorization code lấy access_token."""
    cfg = load_config()
    client_id = cfg.get("client_id")
    client_secret = cfg.get("client_secret")
    redirect_uri = cfg.get("redirect_uri")

    verifier = ""
    PKCE_FILE = DATA_DIR / "canva_pkce.json"
    if PKCE_FILE.exists():
        try:
            p_data = json.loads(PKCE_FILE.read_text(encoding="utf-8"))
            verifier = p_data.get("verifier", "")
        except Exception:
            pass
    if not verifier:
        pkce_info = _PKCE_STORE.pop(state, None) if state else None
        verifier = pkce_info["verifier"] if pkce_info else ""

    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
    }
    r = requests.post(CANVA_TOKEN_URL, headers=headers, data=data, timeout=20)
    r.raise_for_status()
    res = r.json()
    save_token(res)
    return res


def refresh_access_token(refresh_token: str) -> dict | None:
    """Tự động làm mới access_token bằng refresh_token."""
    cfg = load_config()
    client_id = cfg.get("client_id")
    client_secret = cfg.get("client_secret")
    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    try:
        r = requests.post(CANVA_TOKEN_URL, headers=headers, data=data, timeout=20)
        r.raise_for_status()
        res = r.json()
        save_token(res)
        return res
    except Exception:
        return None


def _get_auth_headers() -> dict:
    tok = load_token()
    if not tok or not tok.get("access_token"):
        raise PermissionError("Chưa kết nối tài khoản Canva. Vui lòng bấm 'Kết nối Canva'.")
    return {
        "Authorization": f"Bearer {tok['access_token']}",
    }


def upload_asset(image_data_or_path: bytes | str | Path, name: str = "thumbnail_poster.jpg") -> dict:
    """Upload ảnh lên Canva Asset Library để sử dụng trong thiết kế."""
    headers = _get_auth_headers()
    url = f"{CANVA_API_BASE}/asset-uploads"

    # Đọc bytes
    raw: bytes
    if isinstance(image_data_or_path, (str, Path)) and Path(image_data_or_path).is_file():
        raw = Path(image_data_or_path).read_bytes()
    elif isinstance(image_data_or_path, bytes):
        raw = image_data_or_path
    else:
        raise ValueError("Không tìm thấy dữ liệu ảnh để upload")

    # Header Asset-Upload-Metadata
    clean_name = re.sub(r"[^\w\.-]", "_", name).strip("_")
    if not clean_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        clean_name += ".jpg"
    meta = {
        "name_base64": base64.b64encode(clean_name.encode("utf-8")).decode("ascii")
    }
    headers["Asset-Upload-Metadata"] = json.dumps(meta)
    headers["Content-Type"] = "application/octet-stream"

    r = requests.post(url, headers=headers, data=raw, timeout=60)
    if not r.ok:
        err_msg = r.text
        try:
            ej = r.json()
            err_msg = f"{ej.get('code')}: {ej.get('message')}"
        except Exception:
            pass
        raise RuntimeError(f"Upload asset thất bại ({r.status_code}): {err_msg}")

    job_data = r.json()
    job = job_data.get("job") or job_data
    job_id = job.get("id")

    # Poll chờ asset upload xong (tối đa 20s)
    if job_id and job.get("status") in ("in_progress", "pending"):
        poll_url = f"{CANVA_API_BASE}/asset-uploads/{job_id}"
        for _ in range(20):
            time.sleep(1)
            pr = requests.get(poll_url, headers=_get_auth_headers(), timeout=10)
            if pr.ok:
                pj = pr.json().get("job", {})
                if pj.get("status") == "success":
                    return pj.get("asset") or {}
                if pj.get("status") == "failed":
                    raise RuntimeError(f"Canva upload asset thất bại: {pj.get('error')}")

    # Chỉ trả về asset nếu đã thành công và có id thực sự
    if job.get("status") == "success" and job.get("asset"):
        return job["asset"]
    return {}


def import_design_from_image(image_bytes: bytes, title: str) -> dict | None:
    """Import ảnh trực tiếp vào Canva để tạo thiết kế có sẵn hình ảnh (không bị trang trắng)."""
    headers = _get_auth_headers()
    url = f"{CANVA_API_BASE}/imports"

    clean_title = re.sub(r"[\r\n\t]+", " ", str(title or "")).strip()[:45]
    meta = {
        "title_base64": base64.b64encode(clean_title.encode("utf-8")).decode("ascii"),
        "mime_type": "image/jpeg",
    }
    headers["Import-Metadata"] = json.dumps(meta)
    headers["Content-Type"] = "application/octet-stream"

    try:
        r = requests.post(url, headers=headers, data=image_bytes, timeout=60)
        if not r.ok:
            print(f"[Canva] Import design response not ok: {r.status_code} {r.text}")
            return None
        job_data = r.json()
        job = job_data.get("job") or job_data
        job_id = job.get("id")
        if not job_id:
            return None

        # Poll chờ Canva import xong
        poll_url = f"{CANVA_API_BASE}/imports/{job_id}"
        for _ in range(25):
            time.sleep(1)
            pr = requests.get(poll_url, headers=_get_auth_headers(), timeout=10)
            if pr.ok:
                pj = pr.json().get("job", {})
                if pj.get("status") == "success":
                    designs = (pj.get("result") or {}).get("designs") or []
                    if designs:
                        d = designs[0]
                        d_id = d.get("id")
                        urls = d.get("urls") or {}
                        edit_url = urls.get("edit_url") or d.get("url") or f"https://www.canva.com/design/{d_id}"
                        return {
                            "ok": True,
                            "design_id": d_id,
                            "title": d.get("title") or clean_title,
                            "edit_url": edit_url,
                        }
                elif pj.get("status") == "failed":
                    print(f"[Canva] Import design failed: {pj.get('error')}")
                    return None
        return None
    except Exception as e:
        print(f"[Canva] Import design exception: {e}")
        return None


def create_youtube_thumbnail(
    title: str,
    poster_source: bytes | str | Path | None = None,
    thumb_image_source: bytes | str | Path | None = None,
) -> dict:
    """Tạo một thiết kế YouTube Thumbnail 16:9 mới trên Canva và trả về edit_url.
    Ưu tiên 1: Import thẳng ảnh Thumbnail 16:9 hoàn chỉnh vào Canva (toàn bộ hình ảnh & chữ có sẵn).
    Ưu tiên 2: Tự động gắn poster vào asset library hoặc tạo canvas 1280x720 sạch sẵn sàng thiết kế.
    """
    # Làm sạch tiêu đề
    clean_title = re.sub(r"[\r\n\t]+", " ", str(title or "")).strip()
    if not clean_title:
        clean_title = "Thumbnail YouTube"
    clean_title = clean_title[:45]

    headers = _get_auth_headers()
    headers["Content-Type"] = "application/json"
    url = f"{CANVA_API_BASE}/designs"

    # Chọn ảnh tốt nhất để upload làm asset:
    # Ưu tiên số 1 là ảnh Thumbnail 16:9 đã ghép hook text & poster hoàn chỉnh (thumb_image_source).
    # Nếu không có thì dùng poster gốc (poster_source).
    target_img = thumb_image_source or poster_source
    asset_id = None
    if target_img:
        try:
            raw_target = None
            if isinstance(target_img, (str, Path)) and Path(target_img).is_file():
                raw_target = Path(target_img).read_bytes()
            elif isinstance(target_img, bytes):
                raw_target = target_img
            if raw_target and len(raw_target) > 500:
                asset_name = f"{clean_title[:20]}_thumb169.jpg" if thumb_image_source else f"{clean_title[:20]}_poster.jpg"
                asset = upload_asset(raw_target, name=asset_name)
                if asset and asset.get("id"):
                    asset_id = asset["id"]
        except Exception as e:
            print(f"[Canva] Upload asset warning: {e}")

    # Payload tạo design 1280x720
    base_payload: dict[str, Any] = {
        "design_type": {
            "type": "custom",
            "width": 1280,
            "height": 720
        },
        "title": f"[Pineline] {clean_title}",
    }

    res = None
    # Bước 1: Thử tạo kèm asset_id nếu có
    if asset_id:
        payload_with_asset = {**base_payload, "asset_id": asset_id}
        try:
            r = requests.post(url, headers=headers, json=payload_with_asset, timeout=30)
            if r.ok:
                res = r.json()
            else:
                print(f"[Canva] Tạo design kèm asset_id thất bại ({r.status_code}: {r.text}), tự động chuyển sang canvas 1280x720 trống...")
        except Exception as e:
            print(f"[Canva] Lỗi gửi design kèm asset_id: {e}")

    # Bước 2: Tạo design 1280x720 trống (nếu không có asset_id hoặc bước 1 bị từ chối)
    if not res:
        r = requests.post(url, headers=headers, json=base_payload, timeout=30)
        if not r.ok:
            err_msg = r.text
            try:
                ej = r.json()
                err_msg = f"{ej.get('code')}: {ej.get('message')}"
            except Exception:
                pass
            raise RuntimeError(f"Canva API error ({r.status_code}): {err_msg}")
        res = r.json()

    design = res.get("design") or res
    urls = design.get("urls") or {}
    edit_url = urls.get("edit_url") or f"https://www.canva.com/design/{design.get('id')}"

    return {
        "ok": True,
        "design_id": design.get("id"),
        "title": design.get("title"),
        "edit_url": edit_url,
    }
