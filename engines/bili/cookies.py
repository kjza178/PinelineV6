"""Cookie Bilibili: một file `cookies/bilibili_cookies.txt` (định dạng Netscape) dùng chung cho bộ tải gốc và yt-dlp
(engines/download.find_cookie_file). Nhận dán: JSON của Cookie-Editor (list / dict) hoặc chuỗi «SESSDATA=…; bili_jct=…».
Không bao giờ trả giá trị cookie ra API — chỉ tên + trạng thái đăng nhập.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COOKIE_DIR = ROOT / "cookies"
MAIN = "bilibili_cookies.txt"
IMPORTANT = ("SESSDATA", "bili_jct", "DedeUserID", "buvid3")


def _files() -> list[Path]:
    if not COOKIE_DIR.is_dir():
        return []
    fs = sorted(p for p in COOKIE_DIR.iterdir() if p.is_file() and "bili" in p.name.lower()
                and p.suffix.lower() in (".txt", ".json"))
    return sorted(fs, key=lambda p: (p.name != MAIN, p.name))


def _parse_netscape(text: str) -> dict:
    out = {}
    for ln in text.splitlines():
        if ln.startswith("#HttpOnly_"):
            ln = ln[len("#HttpOnly_"):]
        if not ln.strip() or ln.startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) >= 7 and "bilibili" in parts[0]:
            out[parts[5]] = parts[6].strip()
    return out


def parse_input(raw: str) -> dict:
    """Chuỗi người dùng dán → {tên: giá trị}. Sai định dạng → ValueError."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("chưa dán cookie")
    if s[0] in "[{":
        try:
            d = json.loads(s)
        except ValueError as e:
            raise ValueError("JSON cookie không hợp lệ") from e
        if isinstance(d, dict):
            d = d.get("cookies", d)
        if isinstance(d, list):
            return {str(c["name"]): str(c.get("value", "")) for c in d if isinstance(c, dict) and c.get("name")}
        if isinstance(d, dict):
            return {str(k): str(v) for k, v in d.items()}
        raise ValueError("JSON cookie không đúng dạng")
    if "\t" in s and "bilibili" in s:
        out = _parse_netscape(s)
        if out:
            return out
    out = {}
    for part in s.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    if not out:
        raise ValueError("không đọc được cookie — dán JSON (Cookie-Editor) hoặc chuỗi SESSDATA=…; bili_jct=…")
    return out


def load() -> dict:
    for f in _files():
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            d = parse_input(text) if f.suffix.lower() == ".json" else _parse_netscape(text)
        except (OSError, ValueError) as e:
            print(f"[bili] đọc cookie {f.name} lỗi: {e}", flush=True)
            continue
        if d:
            return d
    return {}


def header() -> str:
    return "; ".join(f"{k}={v}" for k, v in load().items())


def save(raw: str) -> dict:
    d = parse_input(raw)
    if "SESSDATA" not in d:
        raise ValueError("thiếu SESSDATA — cookie chưa đăng nhập Bilibili")
    exp = str(int(time.time()) + 180 * 86400)
    lines = ["# Netscape HTTP Cookie File", "# Pineline V6 — Cài đặt → Bilibili"]
    lines += [f".bilibili.com\tTRUE\t/\tTRUE\t{exp}\t{k}\t{v}" for k, v in d.items()]
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = COOKIE_DIR / (MAIN + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(COOKIE_DIR / MAIN)
    return {"names": sorted(d)}


def clear() -> bool:
    p = COOKIE_DIR / MAIN
    if p.is_file():
        p.unlink()
        return True
    return False


def status(session=None, check: bool = True) -> dict:
    """{has_cookie, file, names(quan trọng), login{is_login, uname, vip} | error} — không trả giá trị cookie."""
    d = load()
    fs = _files()
    out = {"has_cookie": bool(d), "file": fs[0].name if fs else "", "names": [k for k in IMPORTANT if k in d]}
    if check:
        try:
            import requests
            from engines.bili import api
            out["login"] = api.nav(session or requests.Session(), api.headers(header()))
        except Exception as e:  # noqa: BLE001
            out["error"] = f"không kiểm được với Bilibili: {e}"
    return out
