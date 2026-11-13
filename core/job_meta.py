# -*- coding: utf-8 -*-
"""Parse metadata hàng chờ (series/season UI) — dùng chung film_db sync + job_queue."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_CN_NUMS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def parse_season_from_text(*texts: str) -> int | None:
    """Bóc số phần/mùa từ title (Phần N / P N / 第N季)."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return None
    m_head = re.match(
        r"^(?:phần|mùa|season|tập|p)\s*(\d+)\s*[|:\-–—]",
        blob.strip(),
        re.I,
    )
    if m_head:
        return int(m_head.group(1))
    m_vi = re.search(
        r"(?:^|[\s_\-\.\(:|])(?:p|phần|mùa|season|s|tập|t)\s*(\d+)(?:[\s_\-\.\):|]|$)",
        blob,
        re.I,
    )
    if m_vi:
        return int(m_vi.group(1))
    m_cn = re.search(r"第([一二三四五六七八九十\d]+)季", blob)
    if m_cn:
        val = m_cn.group(1)
        if val.isdigit():
            return int(val)
        if val in _CN_NUMS:
            return _CN_NUMS[val]
    return None


def norm_series_key(name: str) -> str:
    """Chuẩn hoá tên bộ để sort."""
    s = (name or "").strip().lower()
    if not s:
        return ""
    s = s.replace("nghìn", "ngan").replace("ngàn", "ngan")
    s = s.replace("đế vương", "de").replace("đế giả", "de").replace("đế", "de")
    nfkd = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_ui_meta(
    *,
    job_id: str = "",
    title_vi: str = "",
    req_title: str = "",
    title_zh: str = "",
    group_id: Any = None,
    group_title: Any = None,
    group_index: Any = None,
) -> dict[str, Any]:
    """Trả title/series/season/group_* theo semantics hàng chờ (VI)."""
    jid = str(job_id or "").strip()
    title_vi = (title_vi or "").strip()
    req_title = (req_title or "").strip()
    title_zh = (title_zh or "").strip()
    gid = (str(group_id).strip() if group_id not in (None, "") else None) or None
    gtitle = (str(group_title).strip() if group_title not in (None, "") else None) or None
    gi = group_index
    if gi is not None and str(gi).strip().isdigit():
        gi = int(gi)
    else:
        gi = None

    title = title_vi or req_title or title_zh or jid or "?"
    season_no = parse_season_from_text(title_vi, title, req_title, title_zh)
    if season_no is None and gi is not None:
        season_no = gi

    target_text = title_vi or title
    extracted_series = ""
    m_head = re.match(r"^(?:phần|mùa|season|tập|p)\s*(\d+)\s*[|:\-–—]\s*(.*)$", target_text, re.I)
    if m_head:
        rest = m_head.group(2)
        parts = re.split(r"[:|–—]", rest)
        extracted_series = parts[0].strip(" -_()[]")
    else:
        cleaned = re.sub(
            r"(?:^|[\s_\-\.\(:])(?:p|phần|mùa|season|s|tập|t)\s*\d+.*$",
            "",
            target_text,
            flags=re.I,
        )
        parts = re.split(r"[:|–—]", cleaned)
        extracted_series = parts[0].strip(" -_()[]") if parts else cleaned.strip(" -_()[]")

    if season_no is None:
        season_no = 1

    if extracted_series and not re.search(r"[\u4e00-\u9fff]", extracted_series):
        series_name = extracted_series
    else:
        series_name = extracted_series or gtitle or (gid or "") or title_vi or title

    return {
        "job_id": jid,
        "title": title,
        "series": series_name.strip(),
        "season": int(season_no),
        "group_id": gid,
        "group_index": gi,
        "group_title": gtitle,
        "series_ui": series_name.strip(),
        "season_ui": int(season_no),
    }


def info_from_manifest_dict(job_id: str, data: dict) -> dict[str, Any]:
    req = data.get("request") or {} if isinstance(data, dict) else {}
    return extract_ui_meta(
        job_id=job_id,
        title_vi=str(data.get("title_vi") or ""),
        req_title=str(req.get("title") or ""),
        title_zh=str(data.get("title_zh") or ""),
        group_id=req.get("group_id"),
        group_title=req.get("group_title"),
        group_index=req.get("group_index"),
    )


def info_from_film_row(row: dict | None) -> dict[str, Any] | None:
    """Map films.db row → shape get_job_info. None nếu row thiếu title."""
    if not isinstance(row, dict) or not row.get("job_id"):
        return None
    title_vi = (row.get("title_vi") or "").strip()
    title = (row.get("title") or "").strip()
    if not title_vi and not title:
        return None
    series_ui = (row.get("series_ui") or "").strip()
    season_ui = row.get("season_ui")
    if season_ui is None:
        season_ui = row.get("season")
    try:
        season_ui = int(season_ui) if season_ui is not None else 1
    except (TypeError, ValueError):
        season_ui = 1
    gi = row.get("group_index")
    try:
        gi = int(gi) if gi is not None and str(gi).strip() != "" else None
    except (TypeError, ValueError):
        gi = None
    series = series_ui or (row.get("group_title") or "").strip() or (row.get("series") or "").strip() or title_vi or title
    return {
        "job_id": row["job_id"],
        "title": title_vi or title,
        "series": series,
        "season": season_ui or 1,
        "group_id": (row.get("group_id") or None) or None,
        "group_index": gi,
        "group_title": (row.get("group_title") or None) or None,
    }
