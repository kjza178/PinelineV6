"""Dịch thô tiêu đề Trung → Việt (Google gtx, không cần API key).

Chỉ để duyệt Hongguo cho biết nội dung — không phải bản dịch kênh.
Cache theo series_id + theo chuỗi title gốc.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_HERE = Path(__file__).resolve().parents[1]
_CACHE_PATH = _HERE / "work" / "_hg_title_cache.json"
_lock = threading.Lock()
_cache: dict | None = None

_CJK = re.compile(r"[\u4e00-\u9fff]")
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _has_cjk(s: str) -> bool:
    return bool(_CJK.search(s or ""))


def _load_cache() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        try:
            if _CACHE_PATH.exists():
                _cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            else:
                _cache = {"by_id": {}, "by_zh": {}}
        except Exception:  # noqa: BLE001
            _cache = {"by_id": {}, "by_zh": {}}
        _cache.setdefault("by_id", {})
        _cache.setdefault("by_zh", {})
        return _cache


def _save_cache() -> None:
    with _lock:
        if _cache is None:
            return
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _CACHE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(_cache, ensure_ascii=False, indent=0), encoding="utf-8")
            tmp.replace(_CACHE_PATH)
        except Exception:  # noqa: BLE001
            pass


def _gtx(text: str, sl: str = "zh-CN", tl: str = "vi", timeout: float = 12) -> str:
    q = (text or "").strip()
    if not q:
        return ""
    params = urllib.parse.urlencode(
        {"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": q}
    )
    url = "https://translate.googleapis.com/translate_a/single?" + params
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    data = json.loads(raw)
    parts: list[str] = []
    try:
        for chunk in data[0] or []:
            if chunk and chunk[0]:
                parts.append(str(chunk[0]))
    except (TypeError, IndexError, KeyError):
        return ""
    out = "".join(parts).strip().strip('"').strip("'")
    return out.splitlines()[0][:240] if out else ""


def translate_one(title: str) -> str:
    """Dịch 1 title; không CJK → trả nguyên."""
    t = (title or "").strip()
    if not t:
        return ""
    if not _has_cjk(t):
        return t
    try:
        vi = _gtx(t)
    except Exception:  # noqa: BLE001
        return ""
    if not vi or _has_cjk(vi):
        return ""
    return vi


def translate_titles(items: list[dict], workers: int = 8) -> dict:
    """Dịch list {series_id?, title} → gắn title_vi. Dùng cache.

    Trả {items: [...], cached: n, translated: n, failed: n}.
    """
    cache = _load_cache()
    out: list[dict] = []
    todo: list[tuple[int, str, str]] = []  # idx, sid, zh
    cached = translated = failed = 0

    for it in items or []:
        if not isinstance(it, dict):
            continue
        sid = str(it.get("series_id") or "").strip()
        zh = str(it.get("title") or it.get("zh") or "").strip()
        row = {"series_id": sid, "title": zh, "title_vi": ""}
        if not zh:
            out.append(row)
            continue
        hit = None
        if sid and sid in cache["by_id"]:
            hit = cache["by_id"][sid]
        elif zh in cache["by_zh"]:
            hit = cache["by_zh"][zh]
        if hit and hit.get("vi") and hit.get("zh") == zh:
            row["title_vi"] = hit["vi"]
            cached += 1
            out.append(row)
            continue
        if not _has_cjk(zh):
            row["title_vi"] = zh
            cached += 1
            out.append(row)
            continue
        out.append(row)
        todo.append((len(out) - 1, sid, zh))

    if todo:
        def _work(job: tuple[int, str, str]) -> tuple[int, str, str, str]:
            idx, sid, zh = job
            vi = translate_one(zh)
            return idx, sid, zh, vi

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as ex:
            futs = [ex.submit(_work, j) for j in todo]
            for fut in as_completed(futs):
                try:
                    idx, sid, zh, vi = fut.result()
                except Exception:  # noqa: BLE001
                    failed += 1
                    continue
                if not vi:
                    failed += 1
                    continue
                out[idx]["title_vi"] = vi
                translated += 1
                ent = {"zh": zh, "vi": vi, "at": time.time()}
                with _lock:
                    if sid:
                        cache["by_id"][sid] = ent
                    cache["by_zh"][zh] = ent
        _save_cache()

    return {
        "items": out,
        "cached": cached,
        "translated": translated,
        "failed": failed,
        "n": len(out),
    }
