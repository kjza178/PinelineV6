"""
Mẫu tin Telegram của Second Brain — do CODE dựng, không dùng văn tự do của LLM (fix 2026-11-12 agentH).

Nguyên tắc:
  * Plain text (agent_watch.telegram_send KHÔNG dùng parse_mode) → không markdown, không thẻ HTML,
    không «|»; mọi chuỗi lấy từ manifest / lỗi engine đều qua clean().
  * Mỗi job 1 khối: dòng tiêu đề (emoji + tên phim tiếng Việt rút gọn + P<n> + job_id rút gọn)
    + tối đa 3 dòng: chuyện gì xảy ra / brain đã làm gì + KẾT QUẢ / người cần làm gì.
  * Gộp mọi sự kiện cùng tick: render_tick(blocks) → 1 tin.

Sự kiện (dict, khoá "kind"):
  stage_start   {stage}
  stage_done    {stage}
  stage_failed  {stage, error, at}          (at: ISO/epoch — giờ địa phương HH:MM)
  remind_failed {stage, error, at}          (nhắc lại, tối đa mỗi AGENT_BRAIN_REMIND_SEC)
  job_done      {}                          (mux xong + có output.mp4)
  hold_set      {reason, cls?, need?}     HOLD CẦN NGƯỜI → «⛔ … — HOLD: lý do. / Cần bạn: …»
  park_set      {cls: park|split_wait, n_lines, retry_at, try, max, parts}
                → «⏸ … — tạm dừng soát dở N câu, tự thử lại lúc HH:MM (lần k/max)» — KHÔNG «Cần bạn»
                (fix 2026-11-13 agentN: park tự động simple/B2 trước bị báo nhầm «Đã đặt HOLD … Cần bạn»)
  hold_cleared  {why: job_done|qa_clean|manual|llm, stage?}
  heal          {outcome: ok|noop|fail|deferred|skipped, actions: [...], reason, by: brain|watch}
  llm_action    {tool, outcome, detail}
  need          {text}                      (dòng «Cần bạn: …» tường minh)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

STAGE_VI = {
    "download": "Tải",
    "asr": "ASR",
    "translate": "Dịch",
    "tts": "Lồng tiếng",
    "mux": "Render",
    "separate": "Tách giọng",
    "ocr": "OCR",
    "hardsub": "Hardsub",
    "review": "Soát",
}

# Ưu tiên emoji tiêu đề khi 1 job có nhiều sự kiện trong 1 tick
_PRIO = [
    ("stage_failed", "❌"),
    ("remind_failed", "❌"),
    ("hold_set", "⛔"),
    ("park_set", "⏸"),
    ("job_done", "✅"),
    ("heal", "🩹"),
    ("llm_action", "🩹"),
    ("hold_cleared", "🔓"),
    ("stage_start", "▶️"),
    ("stage_done", "☑️"),
    ("need", "ℹ️"),
]

_ACTION_VI = {
    "heal_stale_running": "sửa trạng thái treo",
    "translate_fill": "vá câu dịch trống",
    "script_review": "soát lại bản dịch",
    "tts_run": "chạy lại lồng tiếng",
    "tts_then_mux": "chạy lại lồng tiếng",
    "mux_retry": "render lại",
    "asr_run_force": "chạy lại ASR",
    "translate_rerun": "dịch lại",
    "reset_state": "đặt lại trạng thái",
    "unlink_partial": "xoá file render dở",
    "download_or_import": "tải/nhập lại video",
}

_TOOL_VI = {
    "pineline_heal_job": "heal job",
    "pineline_hold_after_translate": "đổi HOLD",
    "pineline_run_stage": "chạy bước",
    "pineline_run_stage_wait": "chạy bước",
    "pineline_tts_run": "chạy lồng tiếng",
    "pineline_mux_run": "render",
    "pineline_stop_job": "dừng job",
    "pineline_queue_add": "xếp hàng",
    "pineline_queue_stop": "dừng hàng chờ",
    "pineline_script_line": "sửa câu dịch",
    "pineline_reset_state": "đặt lại trạng thái",
}

_OUTCOME_VI = {
    "ok": "thành công",
    "noop": "không có việc để làm",
    "fail": "thất bại",
    "deferred": "hoãn",
    "skipped": "bỏ qua",
}

# Việc người dùng cần làm khi stage lỗi mà brain không tự sửa được
_NEED_BY_STAGE = {
    "download": "kiểm tra link/nguồn rồi tải lại.",
    "asr": "xếp lại ASR khi GPU rảnh.",
    "translate": "xem lỗi dịch rồi bấm Dịch lại.",
    "tts": "chạy lại Lồng tiếng.",
    "mux": "chạy lại Render.",
}


# ---------------------------------------------------------------------------
# Chuẩn hoá chữ
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]{0,200}>")
_MD_RE = re.compile(r"(\*\*|__|`+|~~)")


def clean(s: Any, maxlen: int = 160) -> str:
    """Plain text an toàn cho Telegram: bỏ thẻ <...>, markdown, «|», gộp khoảng trắng, cắt theo từ."""
    t = str(s if s is not None else "")
    t = _TAG_RE.sub(" ", t)
    t = _MD_RE.sub("", t)
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.M)
    t = t.replace("|", ",")
    t = re.sub(r"\s+", " ", t).strip(" ,;")
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r",(\s*,)+", ",", t)
    if len(t) > maxlen:
        cut = t[: maxlen - 1]
        sp = cut.rfind(" ")
        if sp > maxlen * 0.6:
            cut = cut[:sp]
        t = cut.rstrip(" ,;:-") + "…"
    return t


def stage_vi(stage: str) -> str:
    return STAGE_VI.get(str(stage or "").lower(), str(stage or "?"))


# ---------------------------------------------------------------------------
# Nhãn job: tên phim tiếng Việt rút gọn + P<n> + id rút gọn
# ---------------------------------------------------------------------------
_PART_SUFFIX = re.compile(r"_p(\d{1,3})$", re.I)
_SEASON_RE = re.compile(r"[\s\-–—,:(]*\b(?:phần|mùa|season)\s*(\d{1,3})(?:\s*/\s*\d+)?\s*\)?\s*$", re.I)
_CJK = re.compile(r"[㐀-鿿]")


def split_part(jid: str) -> tuple[str, int | None]:
    m = _PART_SUFFIX.search(str(jid or ""))
    if not m:
        return str(jid or ""), None
    return str(jid)[: m.start()], int(m.group(1))


def short_id(jid: str) -> str:
    base, part = split_part(jid)
    s = base[:6]
    return f"{s}_p{part:02d}" if part is not None else s


def _raw_title(data: dict | None) -> tuple[str, bool]:
    """(title, is_vi). Ưu tiên title_vi / meta.title_vi / series; fallback title gốc."""
    data = data or {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    req = data.get("request") if isinstance(data.get("request"), dict) else {}
    for t in (data.get("title_vi"), meta.get("title_vi"), data.get("series_vi"), meta.get("series_vi")):
        if t and str(t).strip():
            return str(t).strip(), True
    dl = ((data.get("stages") or {}).get("download") or {}) if isinstance(data.get("stages"), dict) else {}
    for t in (data.get("title"), req.get("title"), meta.get("title"), dl.get("title"), data.get("title_zh")):
        if t and str(t).strip():
            return str(t).strip(), not bool(_CJK.search(str(t)))
    return "", False


def short_title(title: str, maxlen: int = 40) -> tuple[str, int | None]:
    """«Lớp học huyền thoại bị lộ, các đại ca … - Phần 3» → («Lớp học huyền thoại bị lộ», 3)."""
    t = clean(title, 300)
    season = None
    m = _SEASON_RE.search(t)
    if m:
        season = int(m.group(1))
        t = t[: m.start()].rstrip(" -–—,:(")
    # cắt ở dấu phân đoạn đầu tiên nếu phần trước đủ dài (≥ 2 từ)
    for sep in (":", ",", " - ", " – ", " — ", ";", "(", "!", "?"):
        i = t.find(sep)
        if i > 0 and len(t[:i].split()) >= 2:
            t = t[:i]
            break
    t = t.strip(" -–—,:;!?")
    if season is None:   # «Tạo Thần Phần 1: Chương Hải Đại» → số phần đứng trước dấu cắt
        m = _SEASON_RE.search(t)
        if m and m.start() > 0:
            season = int(m.group(1))
            t = t[: m.start()].rstrip(" -–—,:(")
    if len(t) > maxlen:
        t = clean(t, maxlen)
    return t, season


def job_label(jid: str, data: dict | None = None, parent: dict | None = None) -> str:
    """Tên phim tiếng Việt + P<n> + (id rút gọn). Part con (_pNN) không có title_vi → lấy của job cha."""
    _, part = split_part(jid)
    title, is_vi = _raw_title(data)
    if (not is_vi or not title) and parent:
        pt, pvi = _raw_title(parent)
        if pt and (pvi or not title):
            title, is_vi = pt, pvi
    st, season = short_title(title) if title else ("", None)
    if not st:
        st = "Job"
    if part is not None and season is not None:
        num = f" P{season}.{part}"
    elif part is not None:
        num = f" P{part}"
    elif season is not None:
        num = f" P{season}"
    else:
        num = ""
    return f"{st}{num} ({short_id(jid)})"


# ---------------------------------------------------------------------------
# Giờ & lỗi
# ---------------------------------------------------------------------------
def hhmm(at: Any, tz: timezone | None = None) -> str:
    """ISO/epoch → HH:MM giờ địa phương (tz=None: giờ máy)."""
    if at in (None, ""):
        return ""
    try:
        if isinstance(at, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(at)):
            dt = datetime.fromtimestamp(float(at), tz=timezone.utc)
        else:
            s = str(at).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                return dt.strftime("%H:%M")
        dt = dt.astimezone(tz) if tz is not None else dt.astimezone()
        return dt.strftime("%H:%M")
    except Exception:  # noqa: BLE001
        return ""


def tz_from_cfg(v: str | None) -> timezone | None:
    """'+7' / '7' / '+07:00' → timezone; rỗng → None (giờ máy)."""
    s = str(v or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"([+-]?)(\d{1,2})(?::?(\d{2}))?", s)
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0)))


def short_error(stage: str, err: Any) -> str:
    """«firered_asr.py lỗi (mã 1)» → «firered mã 1»; còn lại: chữ sạch ≤ 70 ký tự."""
    e = clean(err, 200)
    m = re.search(r"([A-Za-z0-9_\-]+)\.py lỗi \(mã (-?\d+)\)", e)
    if m:
        name = re.sub(r"_(asr|tts|batch)$", "", m.group(1))
        return f"{name} mã {m.group(2)}"
    return clean(e, 70)


# ---------------------------------------------------------------------------
# Kết quả heal / tool
# ---------------------------------------------------------------------------
def heal_summary(r: Any) -> dict:
    """Kết quả agent_heal.heal_job (có thể {"safe":…, "aggressive":…}) → {outcome, actions, reason}."""
    if not isinstance(r, dict):
        return {"outcome": "fail", "actions": [], "reason": clean(r, 80)}
    if "safe" in r and isinstance(r.get("safe"), dict):
        parts = [r["safe"]] + ([r["aggressive"]] if isinstance(r.get("aggressive"), dict) else [])
    else:
        parts = [r]
    if r.get("deferred"):
        return {"outcome": "deferred", "actions": [], "reason": clean(r.get("note") or r.get("deferred"), 80)}
    if r.get("skipped") and not r.get("results"):
        return {"outcome": "skipped", "actions": [], "reason": clean(r.get("note") or r.get("skipped"), 80)}
    ok_acts: list[str] = []
    fail: list[str] = []
    for p in parts:
        if p.get("ok") is False and p.get("error"):
            fail.append(clean(p.get("error"), 80))
        for step in p.get("results") or []:
            if not isinstance(step, dict):
                continue
            act = str(step.get("action") or "")
            res = step.get("result")
            if hasattr(res, "body"):
                res = {"error": f"HTTP {getattr(res, 'status_code', '?')}"}
            if not isinstance(res, dict):
                res = {"value": res}
            if res.get("skipped"):
                continue
            if res.get("error") or res.get("status_code") in (400, 409, 500):
                fail.append(f"{_ACTION_VI.get(act, act)}: {clean(res.get('error') or res.get('status_code'), 60)}")
                continue
            ok_acts.append(act)
    if ok_acts:
        return {"outcome": "ok", "actions": ok_acts, "reason": "; ".join(fail)[:120]}
    if fail:
        return {"outcome": "fail", "actions": [], "reason": "; ".join(fail)[:120]}
    return {"outcome": "noop", "actions": [], "reason": ""}


def tool_outcome(tool: str, preview: Any) -> tuple[str, str]:
    """Kết quả 1 tool mutate của LLM (result_preview JSON có thể bị cắt) → (outcome, chi tiết ngắn)."""
    p = str(preview or "")
    m = re.search(r'"error"\s*:\s*"([^"]{0,160})', p)
    if m:
        return "fail", clean(m.group(1), 80)
    if '"results": []' in p or '"plan": []' in p or "KHÔNG có action safe" in p:
        return "noop", ""
    if re.search(r'"ok"\s*:\s*false', p):
        return "fail", ""
    acts = re.findall(r'"action"\s*:\s*"([a-z_]+)"', p)
    if acts:
        return "ok", ", ".join(dict.fromkeys(_ACTION_VI.get(a, a) for a in acts))
    return "ok", ""


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def _join_stages(stages: Iterable[str]) -> str:
    return ", ".join(stage_vi(s) for s in stages)


def _heal_line(ev: dict) -> tuple[str, str | None]:
    """→ (dòng «Brain: …», need hoặc None)."""
    who = "Watcher" if ev.get("by") == "watch" else "Brain"
    out = str(ev.get("outcome") or "noop")
    acts = [a for a in (ev.get("actions") or []) if a]
    reason = clean(ev.get("reason"), 90)
    need = None
    if out == "ok":
        line = f"{who}: tự sửa ({', '.join(_ACTION_VI.get(a, a) for a in acts[:3])}) → thành công."
    elif out == "noop":
        line = f"{who}: chẩn đoán xong, heal an toàn không có việc để làm."
    elif out == "deferred":
        line = f"{who}: hoãn heal ({reason or 'vừa khởi động'})."
    elif out == "skipped":
        line = f"{who}: không heal ({reason or 'job đang chạy'})."
    else:
        line = f"{who}: heal thất bại" + (f" — {reason}." if reason else ".")
        need = "xem log job rồi chạy lại bước lỗi."
    return line, need


def park_text(ev: dict, tz: timezone | None = None) -> str:
    """Câu «tạm dừng … tự thử lại …» cho park tự động (không HOLD, không «Cần bạn»)."""
    if ev.get("cls") == "split_wait":
        parts = []
        for sid in ev.get("parts") or []:
            _, pn = split_part(str(sid))
            parts.append(f"P{pn}" if pn is not None else short_id(str(sid)))
        return ("tạm dừng chờ " + (f"part {', '.join(parts[:4])}" if parts else "các part")
                + " soát dở, tự chạy tiếp khi part tới hạn")
    n = ev.get("n_lines")
    txt = "tạm dừng soát dở" + (f" {n} câu" if n else "")
    when = hhmm(ev.get("retry_at"), tz)
    txt += f", tự thử lại lúc {when}" if when else ", tự thử lại khi tới lượt"
    try:
        k = int(ev.get("try") or 0)
        mx = int(ev.get("max") or 0)
    except (TypeError, ValueError):
        k, mx = 0, 0
    if k:
        txt += f" (lần {k}/{mx})" if mx else f" (lần {k})"
    return txt


def render_job(jid: str, data: dict | None, events: list[dict], *, parent: dict | None = None,
               tz: timezone | None = None) -> str:
    """1 khối/job: tiêu đề + ≤ 3 dòng."""
    if not events:
        return ""
    kinds = [e.get("kind") for e in events]
    emoji = next((em for k, em in _PRIO if k in kinds), "ℹ️")
    label = job_label(jid, data, parent)
    head_bits: list[str] = []
    lines: list[str] = []
    needs: list[str] = []
    head_extra = ""   # agentN: «HOLD: …» / «tạm dừng …» nằm ngay dòng tiêu đề

    started = [e.get("stage") for e in events if e.get("kind") == "stage_start"]
    done = [e.get("stage") for e in events if e.get("kind") == "stage_done"]
    failed = [e for e in events if e.get("kind") in ("stage_failed", "remind_failed")]
    job_done = "job_done" in kinds

    if job_done:
        head_bits.append("xong, có video")
    elif done:
        head_bits.append("xong " + _join_stages(done))
    if started:
        head_bits.append("bắt đầu " + _join_stages(started))

    for e in failed[:2]:
        stg = str(e.get("stage") or "")
        when = hhmm(e.get("at"), tz)
        err = short_error(stg, e.get("error"))
        if e.get("kind") == "remind_failed":
            txt = (f"Vẫn lỗi {stage_vi(stg)}" + (f" ({err})" if err else "")
                   + (f" từ {when}" if when else "") + ", chưa xử lý (nhắc lại)")
        else:
            txt = f"{stage_vi(stg)} lỗi" + (f" ({err})" if err else "") + (f" lúc {when}" if when else "")
        lines.append(txt + ".")
        n = _NEED_BY_STAGE.get(stg)
        if n:
            needs.append(n)

    for e in events:
        k = e.get("kind")
        if k == "hold_set":
            r = clean(e.get("reason"), 110)
            head_extra = "HOLD" + (f": {r}" if r else "") + "."
            needs.append(e.get("need") or "duyệt bản dịch rồi bấm Chạy tiếp (hoặc chạy tay Lồng tiếng).")
        elif k == "park_set" and not head_extra:
            head_extra = park_text(e, tz)
        elif k == "hold_cleared":
            why = e.get("why")
            if why == "job_done":
                head_bits.append("Đã gỡ HOLD cũ")
            elif why == "manual":
                lines.append(f"Đã gỡ HOLD vì bạn chạy tay {stage_vi(e.get('stage') or '')}.")
            elif why == "qa_clean":
                lines.append("Brain: QA sạch (high=0) → đã gỡ HOLD, chạy tiếp được.")
            else:
                lines.append("Đã gỡ HOLD" + (f" ({clean(e.get('reason'), 60)})" if e.get("reason") else "") + ".")
        elif k == "heal":
            ln, nd = _heal_line(e)
            lines.append(ln)
            if nd:
                needs.append(nd)
            if e.get("outcome") == "ok":
                needs = [x for x in needs if x not in _NEED_BY_STAGE.values()]
        elif k == "llm_action":
            tool = str(e.get("tool") or "")
            out = str(e.get("outcome") or "ok")
            det = clean(e.get("detail"), 70)
            lines.append(
                f"Brain: {_TOOL_VI.get(tool, tool.replace('pineline_', ''))}"
                + (f" ({det})" if det and out == "ok" else "")
                + f" → {_OUTCOME_VI.get(out, out)}"
                + (f": {det}" if det and out != "ok" else "") + "."
            )
            if out == "fail":
                needs.append("xem log brain/job.")
        elif k == "need" and e.get("text"):
            needs.append(clean(e.get("text"), 100))

    head = f"{emoji} {label}"
    if head_bits:
        # «— xong, có video. Đã gỡ HOLD cũ.» / «— bắt đầu Dịch»
        main, rest = head_bits[0], head_bits[1:]
        tail = main
        for b in rest:
            tail += (". " + b) if b[:1].isupper() else (", " + b)
        if any(b[:1].isupper() for b in rest):
            tail += "."
        head += " — " + tail
    if head_extra:
        if head_bits:
            head = head.rstrip(".") + ". " + head_extra[:1].upper() + head_extra[1:]
        else:
            head += " — " + head_extra
    out_lines = [head]
    seen: set[str] = set()
    for ln in lines:
        if ln not in seen:
            out_lines.append(ln)
            seen.add(ln)
    if needs:
        uniq = list(dict.fromkeys(needs))
        out_lines.append("Cần bạn: " + " ".join(uniq[:2]))
    # tiêu đề + tối đa 3 dòng
    if len(out_lines) > 4:
        out_lines = out_lines[:3] + [out_lines[-1]]
    return "\n".join(out_lines)


def render_tick(blocks: list[str], extra: list[str] | None = None, maxlen: int = 3800) -> str:
    """Nhiều khối job (+ dòng hệ thống) → 1 tin; cách nhau 1 dòng trống."""
    parts = [b for b in blocks if b] + [clean(x, 200) for x in (extra or []) if x]
    msg = "\n\n".join(parts)
    if len(msg) > maxlen:
        msg = msg[: maxlen - 20].rsplit("\n", 1)[0] + "\n…(còn nữa)"
    return msg
