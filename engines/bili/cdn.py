"""Chọn node CDN: bỏ MCDN/P2P, Akamai → cụm UPOS, đo tốc độ 128 KB đầu chọn node nhanh nhất."""
from __future__ import annotations

import time
import urllib.parse

UPOS_MIRRORS = (
    "upos-sz-mirrorcosov.bilivideo.com",
    "upos-sz-mirrorali.bilivideo.com",
    "upos-sz-mirrorcos.bilivideo.com",
    "upos-sz-mirrorhw.bilivideo.com",
)


def substitute_host(url: str, host: str) -> str:
    p = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(p._replace(netloc=host))


def expand_candidates(urls: list[str]) -> list[str]:
    out: list[str] = []
    for u in urls:
        host = urllib.parse.urlparse(u).netloc.lower()
        if "mcdn" in host:                       # P2P / MCDN: chập chờn
            continue
        if "akamaized.net" not in host:          # Akamai hay bị bóp → chỉ dùng bản đổi host
            out.append(u)
        out.extend(substitute_host(u, m) for m in UPOS_MIRRORS)
    return list(dict.fromkeys(out)) or list(urls)


def probe_best(urls: list[str], session, headers: dict, log=print, timeout: float = 3.5) -> tuple[str, list[str]]:
    """(node nhanh nhất, danh sách mirror theo tốc độ giảm dần — dùng làm dự phòng khi tải đoạn)."""
    cands = expand_candidates(urls)
    scored: list[tuple[float, str]] = []
    for u in cands:
        host = urllib.parse.urlparse(u).netloc
        try:
            t0 = time.time()
            r = session.get(u, headers={**headers, "Range": "bytes=0-131071"}, timeout=timeout)
            if r.status_code in (200, 206) and r.content:
                sp = len(r.content) / max(time.time() - t0, 1e-3)
                scored.append((sp, u))
                log(f"[bili] cdn {host}: {sp / 1048576:.2f} MB/s")
            else:
                log(f"[bili] cdn {host}: HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001 — node chết là chuyện thường, ghi lại rồi thử node khác
            log(f"[bili] cdn {host}: không phản hồi ({type(e).__name__})")
    if not scored:
        return cands[0], cands
    scored.sort(key=lambda x: -x[0])
    order = [u for _, u in scored] + [u for u in cands if u not in {x for _, x in scored}]
    log(f"[bili] chọn node {urllib.parse.urlparse(order[0]).netloc}")
    return order[0], order
