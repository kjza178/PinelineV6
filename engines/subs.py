"""
subs.py — Sinh phụ đề .srt từ danh sách câu có timestamp.

Dùng chung: asr.json (tiếng Trung, key 'text') và script.json (tiếng Việt, key
'vi') đều là list [{start, end, text/vi, ...}] -> đổ ra .srt chuẩn để import vào
Premiere/CapCut/... hoặc nạp lại vào trình phát.

Định dạng SRT:
    1
    00:00:00,300 --> 00:00:02,000
    câu thoại

    2
    ...
"""
from __future__ import annotations

import re

try:
    from core.config import get as _cfg_get
except Exception:  # noqa: BLE001
    def _cfg_get(key: str, default: str | None = None) -> str | None:
        return default


def _plain_len(text: str) -> int:
    return len((text or "").replace(" ", "").replace("\n", ""))


_NUM_DOT_RE = re.compile(r"(?<=\d)\.(?=\d)")  # 3.000 / 31.000 / 3.100
_NUM_COMMA_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")  # 31,000 (không đụng thập phân 3,5)
# Số Việt + đơn vị liền (không cắt «31.000| tỷ» / «31 000| tỷ»)
_NUM_TOKEN_RE = re.compile(
    r"\d{1,3}(?:[.\s]\d{3})+(?:\s*tỷ)?|\d{1,3}(?:,\d{3})+(?:\s*tỷ)?|\d+[.,]\d+(?:\s*tỷ)?",
    re.IGNORECASE,
)
# Mảnh số bị cắt: «3.» / «31.» / «31,» / «31 » + «000 tỷ» / «000亿»
_NUM_TAIL_ORPHAN_RE = re.compile(r"\d[.,]\s*$|(?<=\d)\s+$")
_NUM_HEAD_FRAG_RE = re.compile(
    r"^\d{3}(?:[.,\s]\d{3})*(?:\s*tỷ\b|亿)?",
    re.IGNORECASE,
)

_MASK_DOT = "\uE000"
_MASK_COMMA = "\uE001"


def _mask_number_dots(text: str) -> str:
    """Che dấu nghìn trong số để không bị tách câu tại «3.000» / «31,000»."""
    t = _NUM_DOT_RE.sub(_MASK_DOT, text or "")
    return _NUM_COMMA_RE.sub(_MASK_COMMA, t)


def _unmask_number_dots(text: str) -> str:
    return (text or "").replace(_MASK_DOT, ".").replace(_MASK_COMMA, ",")


_VI_COMPOUNDS = (
    "tấn công", "thực lực", "nâng cấp", "chống đỡ", "phòng thủ",
    "dị năng", "phần trăm", "hồi máu", "sát thương",
)

# Khoảng trắng không ngắt — ASS WrapStyle=2 / soft-wrap không cắt giữa tên
_NBSP = "\u00A0"

# Tên riêng / thuật ngữ nhiều từ — giữ 1 khối (CUT + burn)
_VI_NAME_PHRASE_RE = re.compile(
    r"(?:"
    r"Thiên\s+Lang\s+Tinh|"
    r"Thánh\s+Quang\s+Tinh|"
    r"Huyết\s+Vi[eê]n?\s+Tinh|"
    r"Lam\s+Tinh|"
    r"Lang\s+Vân\s+Tiêu|"
    r"Lang\s+Ảnh|"
    r"Cấp\s+Vĩnh\s+Hằng|"
    r"Cấp\s+Quy\s+Tắc|"
    r"Hư\s+Không\s+Truyền\s+Tống|"
    r"Hư\s+Không\s+Xuyên\s+Thoi|"
    r"Thông\s+Thiên\s+Tháp|"
    r"Vạn\s+Long\s+Triều|"
    r"Chủ\s+Tể\s+Sinh\s+Mệnh|"
    r"Truyền\s+Tống\s+Trận|"
    r"Thiên\s+Diện\s+Huyễn\s+Cảnh|"
    r"Khế\s+ước\s+Nô\s+dịch|"
    r"Phân\s+thân|"
    r"Phụ\s+Vương|"
    r"Bí\s+Cảnh|"
    r"Dị\s+năng|"
    r"đàn\s+ông|"
    r"Công\s+chúa|"
    r"trang\s+bị|"
    # «Xxx Tinh» / «Xxx Yyy Tinh» (tinh cầu)
    r"[A-ZÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ]"
    r"[\w]*\s+(?:[A-ZÀ-Ỵ][\w]*\s+)?Tinh"
    r")",
    re.UNICODE,
)


def glue_vi_names(text: str) -> str:
    """Dán NBSP trong tên riêng — không xuống dòng giữa «Thiên Lang Tinh»."""
    t = text or ""
    if not t or " " not in t:
        return t

    def _glue(m: re.Match) -> str:
        return m.group(0).replace(" ", _NBSP)

    return _VI_NAME_PHRASE_RE.sub(_glue, t)


def unglue_vi_names(text: str) -> str:
    return (text or "").replace(_NBSP, " ")


def _is_compound_break(text: str, pos: int) -> bool:
    """True nếu cắt tại pos đang tách đôi compound (…tấn| công…)."""
    if pos <= 0 or pos >= len(text):
        return False
    # chuẩn hóa: điểm cắt thường sau space đầu từ sau
    left = text[:pos].rstrip()
    right = text[pos:].lstrip()
    for c in _VI_COMPOUNDS:
        w0, w1 = c.split(" ", 1)
        if left.endswith(w0) and right.startswith(w1):
            return True
    return False


def _snap_cut_to_vi_word(text: str, pos: int) -> int:
    """Ưu tiên cắt tại khoảng trắng VI; không cắt giữa từ / giữa số / compound."""
    pos = _snap_cut_outside_number(text, pos)
    if pos <= 0 or pos >= len(text) or " " not in text:
        return pos
    if text[pos - 1:].startswith(" ") or (pos < len(text) and text[pos] == " "):
        pos2 = pos if text[pos - 1] != " " else pos
        if not _is_compound_break(text, pos2):
            return pos2
        pos = pos2
    left = text.rfind(" ", 0, pos)
    right = text.find(" ", pos)
    cands = []
    if left > 0:
        cands.append(left + 1)
    if right != -1:
        cands.append(right + 1)
    if not cands:
        return pos
    cands = [c for c in cands if not _is_compound_break(text, c)] or cands
    return min(cands, key=lambda c: abs(c - pos))


def _snap_cut_outside_number(text: str, pos: int) -> int:
    """Đẩy điểm cắt ra ngoài token số (tránh «31.» + «000 tỷ»)."""
    if pos <= 0 or pos >= len(text):
        return pos
    for m in _NUM_TOKEN_RE.finditer(text):
        if m.start() < pos < m.end():
            left, right = pos - m.start(), m.end() - pos
            return m.start() if left <= right else m.end()
    return pos


def split_text_by_punct(text: str, max_chars: int = 0, weak: bool = True) -> list[str]:
    """Tách câu theo dấu câu. weak=True: mỗi mệnh đề sau ，、, là 1 đoạn (thoại TTS).

    Trước đây: câu ≤ max_chars thì không tách phẩy — nhiều mệnh đề dính 1 dòng.
    Giờ luôn tách phẩy; chỉ dính lại mảnh cực ngắn (<4 chữ). Câu không có phẩy
    mà vẫn > max_chars thì giữ nguyên (không cắt cứng giữa chữ Hán).

    Không tách tại dấu «.» trong số Việt (3.000 / 31.000).
    """
    text = (text or "").strip()
    if not text:
        return []
    masked = _mask_number_dots(text)
    # Giữ mask qua cả strong + weak split — unmask sau cùng
    # (nếu unmask sớm, «31,000» lại bị weak_re cắt tại dấu ,)
    parts = [p.strip() for p in re.split(r"(?<=[。！？；.!?;])\s*", masked) if p.strip()]
    if not parts:
        parts = [masked or text]
    if not weak:
        return [_unmask_number_dots(p) for p in parts] or [text]
    weak_re = r"(?<=[，、,;])\s*"
    clauses: list[str] = []
    for p in parts:
        subs = [s.strip() for s in re.split(weak_re, p) if s.strip()]
        if len(subs) > 1:
            clauses.extend(subs)
        else:
            clauses.append(p)
    if not clauses:
        return [text]
    # Dính mảnh cực ngắn vào hàng trước/sau — tránh "哼。" / "他。" đơn lẻ quá vụn
    packed: list[str] = []
    for c in clauses:
        c = _unmask_number_dots(c)
        if packed and (_plain_len(c) < 4 or _plain_len(packed[-1]) < 4):
            packed[-1] = _join_clause(packed[-1], c)
        else:
            packed.append(c)
    # Nếu vẫn có 1 khúc dài không phẩy và max_chars>0: giữ nguyên (hard-cut CJK hại ASR)
    if max_chars > 0 and len(packed) == 1 and _plain_len(packed[0]) > max_chars:
        return packed
    return packed if packed else [text]


_CJK_EDGE_RE = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")


def _join_clause(a: str, b: str) -> str:
    """CJK dính liền; VI/Latin cần khoảng trắng («Đù,» + «thế…» → «Đù, thế…»)."""
    if not a or not b or _CJK_EDGE_RE.match(a[-1]) or _CJK_EDGE_RE.match(b[0]):
        return a + b
    return a.rstrip() + " " + b.lstrip()


_PUNCT_ONLY_RE = re.compile(r"^[\s，。！？、,.!?;:…\-—~～]+$")


def _is_punct_only(text: str) -> bool:
    return (not (text or "").strip()) or bool(_PUNCT_ONLY_RE.match((text or "").strip()))


def _split_for_display(text: str, max_chars: int, force_weak: bool = False,
                       hard: bool = False) -> list[str]:
    """Tách text cho sub: dấu câu -> dấu phẩy; hard=True thì cắt cứng phần còn dài.

    hard=True dùng SAU dịch (ZH+VI → CUT). ASR thuần nên hard=False để tránh
    cắt giữa từ trước khi có bản dịch căn hàng.
    VI (có space): đo bằng len(); CJK: _plain_len + _split_hard theo ký tự.
    """
    text = (text or "").strip()
    if not text:
        return []
    has_space = " " in text
    disp = len(text) if (hard and has_space) else _plain_len(text)
    if not force_weak and disp <= max_chars:
        return [text]
    chunks = split_text_by_punct(
        text, max_chars=max_chars, weak=force_weak or disp > max_chars,
    )
    out: list[str] = []
    for c in chunks:
        c_disp = len(c) if (hard and " " in c) else _plain_len(c)
        if hard and c_disp > max_chars:
            # VI có space hoặc CJK liền — đều cắt cứng khi còn quá dài
            out.extend(_split_hard(c, max_chars))
        else:
            out.append(c)
    cleaned: list[str] = []
    for p in out:
        if cleaned and _is_punct_only(p):
            cleaned[-1] += p
        elif p.strip():
            cleaned.append(p)
    if hard and has_space and cleaned:
        cleaned = _merge_short_orphans(cleaned, max_chars)
    return cleaned if cleaned else [text]


def _slice_contiguous(text: str, weights: list[int]) -> list[str]:
    """Cắt 1 chuỗi thành N đoạn liên tiếp theo trọng số — không trộn với câu khác."""
    text = text or ""
    if not weights:
        return []
    if len(weights) == 1 or not text:
        return [text] * len(weights) if not text else [text]
    n = len(text)
    total = float(sum(max(int(w), 1) for w in weights)) or 1.0
    punct = set("，。！？、,.;!?: ")
    cuts: list[int] = []
    acc = 0.0
    prev = 0
    for wi, w in enumerate(weights[:-1]):
        acc += max(int(w), 1) / total
        raw = int(round(acc * n))
        raw = min(max(raw, prev + 1), n)
        best = raw
        for d in range(0, 10):
            found = False
            for pos in (raw + d, raw - d):
                if prev < pos <= n and pos > 0 and text[pos - 1] in punct:
                    # không cắt sau «.» / «,» trong số 3.000 / 31,000
                    if (
                        text[pos - 1] in ".,"
                        and pos >= 2
                        and text[pos - 2].isdigit()
                        and pos < n
                        and text[pos].isdigit()
                    ):
                        continue
                    best = pos
                    found = True
                    break
            if found:
                break
        best = _snap_cut_to_vi_word(text, best) if " " in text else _snap_cut_outside_number(text, best)
        # giữ chỗ cho các khúc sau
        max_pos = n - (len(weights) - 2 - wi)
        best = min(max(best, prev + 1), max(prev + 1, max_pos))
        cuts.append(best)
        prev = best
    parts: list[str] = []
    prev = 0
    for c in cuts:
        parts.append(text[prev:c])
        prev = c
    parts.append(text[prev:])
    return parts


def merge_tiny_segments(
    segments: list[dict],
    key: str = "text",
    min_chars: int = 4,
    min_dur: float = 0.45,
) -> list[dict]:
    """Gộp mảnh ASR vụn: chỉ dấu câu, cắt giữa từ, clip < 0.45s quá ngắn."""
    if not segments:
        return []
    strong = set("。！？!?.")
    out: list[dict] = []
    for seg in segments:
        row = dict(seg)
        text = (row.get(key) or row.get("text") or "").strip()
        row[key] = text
        if not out:
            out.append(row)
            continue
        dur = float(row.get("end", 0) or 0) - float(row.get("start", 0) or 0)
        prev = out[-1]
        prev_text = (prev.get(key) or "").strip()
        prev_end_strong = bool(prev_text) and prev_text[-1] in strong
        tiny = _is_punct_only(text) or (
            _plain_len(text) < min_chars and (dur < min_dur or not prev_end_strong)
        )
        if tiny:
            prev[key] = prev_text + text
            prev["end"] = float(row.get("end") or prev.get("end") or 0)
            if "zh" in prev or "zh" in row:
                prev["zh"] = prev.get("zh") or prev.get(key) or ""
                if key == "text":
                    prev["zh"] = prev[key]
                else:
                    prev["zh"] = prev[key]
            if "vi" in prev or "vi" in row:
                a = (prev.get("vi") or "").strip()
                b = (row.get("vi") or "").strip()
                prev["vi"] = (a + (" " if a and b else "") + b).strip()
            continue
        out.append(row)
    for idx, row in enumerate(out):
        if "i" in row:
            row["i"] = idx
    return out


def _merge_parts(parts: list[str], n: int) -> list[str]:
    """Gộp parts dư thành đúng n cụm (ghép các cụm ngắn nhất liên tiếp)."""
    if n <= 0 or not parts:
        return parts
    if len(parts) <= n:
        return parts
    merged = list(parts)
    while len(merged) > n:
        best = 0
        best_len = _plain_len(merged[0]) + _plain_len(merged[1])
        for i in range(len(merged) - 1):
            ln = _plain_len(merged[i]) + _plain_len(merged[i + 1])
            if ln < best_len:
                best_len = ln
                best = i
        merged[best : best + 2] = [merged[best] + merged[best + 1]]
    return merged


def _expand_parts(parts: list[str], n: int, max_chars: int) -> list[str]:
    """Chia parts ít hơn n thành đủ n cụm."""
    out = list(parts)
    while len(out) < n:
        idx = max(range(len(out)), key=lambda i: _plain_len(out[i]))
        piece = out.pop(idx)
        sub = _split_hard(piece, max(8, max_chars // 2))
        if len(sub) < 2:
            mid = max(1, _plain_len(piece) // 2)
            sub = [piece[:mid].strip(), piece[mid:].strip()]
        out[idx:idx] = sub
        if len(out) > n * 3:
            break
    if len(out) > n:
        out = _merge_parts(out, n)
    return out


def _align_zh_vi(zh_parts: list[str], vi_parts: list[str],
                 max_zh: int, max_vi: int) -> list[tuple[str, str]]:
    """Căn số cụm zh/vi; ưu tiên zh làm mốc thời gian."""
    if not vi_parts or not vi_parts[0].strip():
        return [(z, "") for z in zh_parts]
    n = max(len(zh_parts), len(vi_parts))
    if len(zh_parts) != n:
        zh_parts = _expand_parts(zh_parts, n, max_zh) if len(zh_parts) < n else _merge_parts(zh_parts, n)
    if len(vi_parts) != n:
        vi_parts = _expand_parts(vi_parts, n, max_vi) if len(vi_parts) < n else _merge_parts(vi_parts, n)
    return list(zip(zh_parts, vi_parts))


def _spread_time_pairs(
    pairs: list[tuple[str, str]],
    start: float,
    end: float,
    min_dur: float = 0.35,
) -> list[tuple[str, str, float, float]]:
    """Chia [start,end] theo độ dài vi (fallback zh)."""
    if not pairs:
        return []
    weights = [max(_plain_len(v or z), 1) for z, v in pairs]
    total_w = float(sum(weights)) or 1.0
    dur = max(end - start, min_dur * len(pairs))
    cur = start
    out: list[tuple[str, str, float, float]] = []
    for i, ((z, v), w) in enumerate(zip(pairs, weights)):
        piece = dur * (w / total_w)
        en = end if i == len(pairs) - 1 else cur + piece
        en = max(en, cur + min_dur)
        out.append((z, v, round(cur, 3), round(en, 3)))
        cur = en
    return out


def _split_hard(text: str, max_chars: int) -> list[str]:
    """Cắt cứng theo từ (Việt) hoặc ký tự (CJK) khi không còn dấu câu."""
    text = (text or "").strip()
    if not text:
        return []
    # VI (có space): đo len — khoảng trắng vẫn chiếm chỗ trên màn hình.
    # CJK: _plain_len.
    if " " in text or _NBSP in text:
        # Dán tên («Thiên Lang Tinh») thành 1 token trước khi cắt
        text = glue_vi_names(text)
        if len(text) <= max_chars:
            return [text]
        words = text.split(" ")  # NBSP giữ trong token
        chunks: list[str] = []
        buf = ""
        for w in words:
            trial = (buf + " " + w).strip() if buf else w
            # giữ compound dính (tấn + công) — so trên bản unglue
            if buf:
                pair = (unglue_vi_names(buf.split(" ")[-1]) + " " + unglue_vi_names(w)).lower()
                if pair in _VI_COMPOUNDS and len((buf + " " + w).strip()) <= max_chars:
                    buf = (buf + " " + w).strip()
                    continue
            # giữ «3.000» + «tỷ» dính nhau kể cả khi vượt max một chút
            if buf:
                last = unglue_vi_names(buf.split(" ")[-1])
                w_plain = unglue_vi_names(w)
                if (
                    re.match(r"^tỷ\b", w_plain, re.IGNORECASE)
                    and (_NUM_TOKEN_RE.fullmatch(last.rstrip(".,;:!?…")) or last[-1:].isdigit())
                ):
                    buf = (buf + " " + w).strip()
                    continue
            if len(trial) <= max_chars:
                buf = trial
            else:
                if buf:
                    # không để «3.» / «31.» ở cuối rồi đẩy «000» sang dòng sau
                    if _NUM_TAIL_ORPHAN_RE.search(unglue_vi_names(buf)) and _NUM_HEAD_FRAG_RE.match(
                        unglue_vi_names(w)
                    ):
                        buf = (buf + " " + w).strip() if not buf.endswith(".") else (buf + w)
                        continue
                    chunks.append(buf)
                w_plain = unglue_vi_names(w)
                # không cắt cứng giữa số 31.000 / 3.000 — giữ cả cụm «3.000 tỷ»
                if _NUM_TOKEN_RE.fullmatch(w_plain.rstrip(".,;:!?…")) or (
                    len(w) > max_chars and _NUM_DOT_RE.search(w_plain)
                ):
                    buf = w
                elif len(w) <= max_chars:
                    buf = w
                else:
                    buf = ""
                    for i in range(0, len(w), max_chars):
                        chunks.append(w[i : i + max_chars])
        if buf:
            # dính orphan cuối
            if chunks and _NUM_TAIL_ORPHAN_RE.search(unglue_vi_names(chunks[-1])) and _NUM_HEAD_FRAG_RE.match(
                unglue_vi_names(buf)
            ):
                chunks[-1] = chunks[-1] + ("" if chunks[-1].endswith(".") else " ") + buf
            else:
                chunks.append(buf)
        return _merge_short_orphans(chunks, max_chars) or [text]
    if _plain_len(text) <= max_chars:
        return [text]
    # CJK: ưu tiên cắt sau trợ từ / dấu gần mốc max_chars
    prefer = set("，、；;吗呢吧了的着过么啊呀哦噢嘛")
    chunks: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if n - i <= max_chars:
            piece = text[i:]
            if piece.strip():
                chunks.append(piece)
            break
        target = i + max_chars
        cut = target
        for back in range(0, min(8, max_chars // 2)):
            pos = target - back
            if pos > i and text[pos - 1] in prefer:
                cut = pos
                break
        if cut <= i:
            cut = target
        piece = text[i:cut]
        if piece.strip():
            chunks.append(piece)
        i = cut
    return chunks or [text]


def split_long_segments(
    segments: list[dict],
    max_chars: int = 18,
    key: str = "text",
    min_dur: float = 0.35,
    max_dur: float = 4.0,
) -> list[dict]:
    """Tách câu quá dài / quá lâu thành nhiều đoạn; chia thời gian theo độ dài chữ."""
    out: list[dict] = []
    reindex = bool(segments and "i" in segments[0])
    for seg in segments:
        text = (seg.get(key) or seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start", 0) or 0)
        end = float(seg.get("end", start) or start)
        if end <= start:
            end = start + min_dur
        dur = end - start
        force = dur > max_dur
        if _plain_len(text) <= max_chars and not force:
            out.append({**seg, key: text, "start": round(start, 3), "end": round(end, 3)})
            continue
        final = _split_for_display(text, max_chars, force_weak=force or _plain_len(text) > max_chars)
        if len(final) <= 1:
            out.append({**seg, key: text, "start": round(start, 3), "end": round(end, 3)})
            continue
        pairs = [(c, "") for c in final]
        timed = _spread_time_pairs(pairs, start, end, min_dur=min_dur)
        for chunk, _, st, en in timed:
            row = {k: v for k, v in seg.items() if k not in ("i", key, "start", "end")}
            row[key] = chunk
            row["start"] = st
            row["end"] = en
            if key != "text" and seg.get("text"):
                row["text"] = seg["text"]
            out.append(row)
    out = merge_tiny_segments(out, key=key, min_dur=min_dur)
    if reindex:
        for idx, row in enumerate(out):
            row["i"] = idx
    return out


def split_bilingual_rows(
    rows: list[dict],
    max_zh: int = 18,
    max_vi: int = 50,
    max_dur: float = 4.0,
    min_dur: float = 0.35,
) -> list[dict]:
    """Sau dịch ZH→VI: tách sub dài (CUT). Ưu tiên cắt theo VI rồi cắt ZH liền mạch.

    Không dùng trước khi có vi — ASR giữ nguyên khối để dịch đủ ngữ cảnh.
    """
    out: list[dict] = []
    for row in rows:
        zh = (row.get("zh") or row.get("text") or "").strip()
        vi = (row.get("vi") or "").strip()
        start = float(row.get("start", 0) or 0)
        end = float(row.get("end", start) or start)
        if end <= start:
            end = start + min_dur
        dur = end - start
        vi_len = len(vi) if vi else 0
        need = (
            _plain_len(zh) > max_zh
            or vi_len > max_vi
            or dur > max_dur
        )
        if not need or not zh:
            out.append(row)
            continue
        force = dur > max_dur or vi_len > max_vi or _plain_len(zh) > max_zh
        zh_parts = _split_for_display(zh, max_zh, force_weak=True, hard=True)
        vi_parts: list[str] = []
        # VI quá dài: ưu tiên cắt theo từ VI (tránh ZH-weight cắt «tấn|công»)
        if vi and vi_len > max_vi:
            vi_parts = _split_for_display(vi, max_vi, force_weak=True, hard=True)
            if len(vi_parts) > 1:
                weights = [max(len(p), 1) for p in vi_parts]
                zh_parts = _slice_contiguous(zh, weights)
        elif len(zh_parts) > 1 and vi:
            weights = [max(_plain_len(p), 1) for p in zh_parts]
            vi_parts = _slice_contiguous(vi, weights)
            # VI mảnh vẫn dài → cắt lại theo từ
            rebuilt_v: list[str] = []
            rebuilt_z: list[str] = []
            for z_part, v_part in zip(zh_parts, vi_parts):
                if len(v_part) > max_vi and " " in v_part:
                    sub_v = _split_for_display(v_part, max_vi, force_weak=True, hard=True)
                    if len(sub_v) > 1:
                        sub_z = _slice_contiguous(z_part, [max(len(p), 1) for p in sub_v])
                        rebuilt_v.extend(sub_v)
                        rebuilt_z.extend(sub_z)
                        continue
                rebuilt_v.append(v_part)
                rebuilt_z.append(z_part)
            vi_parts, zh_parts = rebuilt_v, rebuilt_z
        elif vi and (len(zh_parts) <= 1 and force):
            vi_parts = _split_for_display(vi, max_vi, force_weak=True, hard=True)
            if len(vi_parts) > 1:
                weights = [max(len(p), 1) for p in vi_parts]
                zh_parts = _slice_contiguous(zh, weights)
        if len(zh_parts) <= 1 and force and _plain_len(zh) >= max_zh:
            zh_parts = _split_hard(zh, max_zh)
            if vi and len(zh_parts) > 1:
                weights = [max(_plain_len(p), 1) for p in zh_parts]
                vi_parts = _slice_contiguous(vi, weights)
        if len(zh_parts) <= 1 and not (vi_parts and len(vi_parts) > 1):
            out.append(row)
            continue
        if vi_parts and len(vi_parts) > 1 and len(zh_parts) <= 1:
            zh_parts = _slice_contiguous(zh, [max(len(p), 1) for p in vi_parts])
        n = min(len(zh_parts), len(vi_parts) or len(zh_parts))
        if n <= 1:
            out.append(row)
            continue
        zh_parts = zh_parts[:n]
        vi_parts = (vi_parts or [""] * n)[:n]
        pairs = list(zip(zh_parts, vi_parts))
        timed = _spread_time_pairs(pairs, start, end, min_dur=min_dur)
        src_i = row.get("src_i", row.get("i"))
        for z, v, st, en in timed:
            out.append({
                **{k: val for k, val in row.items()
                   if k not in ("i", "zh", "text", "vi", "start", "end", "src_i")},
                "zh": z,
                "vi": v,
                "start": st,
                "end": en,
                "src_i": src_i,
            })
    # Pass 2: ép cắt lại mọi VI vẫn > max_vi (sót từ slice/zh-first)
    final: list[dict] = []
    for row in out:
        vi = (row.get("vi") or "").strip()
        zh = (row.get("zh") or "").strip()
        if vi and len(vi) > max_vi and " " in vi:
            vparts = _split_for_display(vi, max_vi, force_weak=True, hard=True)
            if len(vparts) > 1:
                zparts = _slice_contiguous(zh, [max(len(p), 1) for p in vparts]) if zh else [""] * len(vparts)
                start = float(row.get("start", 0) or 0)
                end = float(row.get("end", start) or start)
                pairs = list(zip(zparts, vparts))
                timed = _spread_time_pairs(pairs, start, end, min_dur=min_dur)
                src_i = row.get("src_i", row.get("i"))
                for z, v, st, en in timed:
                    final.append({
                        **{k: val for k, val in row.items()
                           if k not in ("i", "zh", "text", "vi", "start", "end", "src_i")},
                        "zh": z,
                        "vi": v,
                        "start": st,
                        "end": en,
                        "src_i": src_i,
                    })
                continue
        final.append(row)
    out = merge_broken_vi_number_rows(final)
    # Pass 3: sau gộp số gãy có thể vượt max lại — ép cắt cứng lần cuối.
    capped: list[dict] = []
    for row in out:
        vi = (row.get("vi") or "").strip()
        zh = (row.get("zh") or "").strip()
        if vi and len(vi) > max_vi and (" " in vi or _NBSP in vi):
            vparts = _split_hard(vi, max_vi)
            if len(vparts) > 1:
                zparts = (
                    _slice_contiguous(zh, [max(len(p), 1) for p in vparts])
                    if zh else [""] * len(vparts)
                )
                start = float(row.get("start", 0) or 0)
                end = float(row.get("end", start) or start)
                timed = _spread_time_pairs(
                    list(zip(zparts, vparts)), start, end, min_dur=min_dur,
                )
                src_i = row.get("src_i", row.get("i"))
                for z, v, st, en in timed:
                    capped.append({
                        **{k: val for k, val in row.items()
                           if k not in ("i", "zh", "text", "vi", "start", "end", "src_i")},
                        "zh": z,
                        "vi": v,
                        "start": st,
                        "end": en,
                        "src_i": src_i,
                    })
                continue
        capped.append(row)
    out = carry_tail_fragments(capped, max_vi)
    for idx, row in enumerate(out):
        row["i"] = idx
    return out


_TAIL_FRAG_RE = re.compile(r"^(.*[.!?…])\s+(\S+(?:\s\S+){0,2})$")
_ZH_TAIL_FRAG_RE = re.compile(r"^(.*[。！？!?])([^。！？!?]{1,8})$")


def carry_tail_fragments(rows: list[dict], max_vi: int = 37, max_gap: float = 0.6) -> list[dict]:
    """«…sáu mươi đứa. Đúng,» + «cùng lên…» → mảnh đầu câu sau sang cue kế tiếp.

    Chỉ dời chữ (giữ timing) khi 2 cue liền nhau và đuôi chưa kết câu.
    """
    for k in range(len(rows) - 1):
        a, b = rows[k], rows[k + 1]
        m = _TAIL_FRAG_RE.match((a.get("vi") or "").strip())
        if not m or m.group(2)[-1:] in ".!?…":
            continue
        try:
            if float(b.get("start", 0)) - float(a.get("end", 0)) > max_gap:
                continue
        except (TypeError, ValueError):
            continue
        vb = (b.get("vi") or "").strip()
        if not vb or len(m.group(2)) + 1 + len(vb) > max_vi + 10:
            continue
        a["vi"], b["vi"] = m.group(1), m.group(2) + " " + vb
        mz = _ZH_TAIL_FRAG_RE.match((a.get("zh") or "").strip())
        if mz:
            a["zh"], b["zh"] = mz.group(1), mz.group(2) + (b.get("zh") or "")
            for r in (a, b):
                if "text" in r:
                    r["text"] = r["zh"]
    return rows


def merge_broken_vi_number_rows(rows: list[dict]) -> list[dict]:
    """Gộp mảnh lệch số: «…31,» + «000 tỷ» / «当前血量31,» + «000亿» → 1 câu."""
    if not rows:
        return rows
    out: list[dict] = []
    for r in rows:
        if not out:
            out.append(dict(r))
            continue
        prev = out[-1]
        a = (prev.get("vi") or "").rstrip()
        b = (r.get("vi") or "").lstrip()
        za = (prev.get("zh") or prev.get("text") or "").rstrip()
        zb = (r.get("zh") or r.get("text") or "").lstrip()
        vi_break = bool(a and b and _NUM_TAIL_ORPHAN_RE.search(a) and _NUM_HEAD_FRAG_RE.match(b))
        zh_break = bool(
            za and zb and _NUM_TAIL_ORPHAN_RE.search(za) and _NUM_HEAD_FRAG_RE.match(zb)
        )
        if not (vi_break or zh_break):
            out.append(dict(r))
            continue
        if vi_break:
            joined = re.sub(r"(\d)[.,]?\s+(\d{3}\b)", r"\1.\2", a + b)
            joined = re.sub(
                r"\b\d{1,3}(?:,\d{3})+\b", lambda m: m.group(0).replace(",", "."), joined,
            )
            prev["vi"] = joined
        elif b and not (prev.get("vi") or "").strip():
            prev["vi"] = b
        elif a and b and not vi_break:
            # ZH gãy nhưng VI đã ổn / thiếu — vẫn nối VI nếu mảnh sau là orphan số
            if _NUM_HEAD_FRAG_RE.match(b) and a[-1:].isdigit():
                prev["vi"] = (a + " " + b).strip()
            elif b:
                prev["vi"] = ((prev.get("vi") or "") + " " + b).strip()
        prev["end"] = r.get("end", prev.get("end"))
        if zb and zb not in za:
            prev["zh"] = (za + zb).strip()
            if "text" in prev:
                prev["text"] = prev["zh"]
        continue
    return out


def heal_script_broken_numbers(rows: list[dict]) -> tuple[list[dict], int]:
    """Gộp số gãy trên script (giữ i của câu đầu) — trả (rows_mới, số_cặp_đã_gộp)."""
    before = len(rows or [])
    merged = merge_broken_vi_number_rows([dict(r) for r in (rows or []) if isinstance(r, dict)])
    return merged, max(0, before - len(merged))

def sub_split_cfg() -> tuple[int, int, float]:
    # Font ~45 @1080 + bar: đo thực tế ~37 chữ VI / 1 dòng đầy.
    max_zh = int(_cfg_get("SUB_SPLIT_CHARS_ZH", "22") or 22)
    max_vi = int(_cfg_get("SUB_SPLIT_CHARS_VI", "37") or 37)
    max_dur = float(_cfg_get("SUB_MAX_DUR", "3.5") or 3.5)
    return max_zh, max_vi, max_dur


def _merge_short_orphans(parts: list[str], max_chars: int, orphan_max: int = 12) -> list[str]:
    """Gộp đuôi ngắn («này?», «Tinh,») — không nuốt lại «nhiều.» / «trường,»."""
    if len(parts) < 2:
        return parts
    out = [p for p in parts if (p or "").strip()]
    while len(out) >= 2:
        last = out[-1].strip()
        last_plain = unglue_vi_names(last)
        n_words = len(last_plain.split())
        core = last_plain.rstrip(".,!?;:…،")
        # Mảnh cực ngắn bất kỳ («này?», «Đúng,») → gộp nếu không vượt max nhiều
        if n_words <= 2 and len(last_plain) <= 8:
            merged = (out[-2].rstrip() + " " + last).strip()
            if len(unglue_vi_names(merged)) <= max_chars:
                out[-2] = merged
                out.pop()
                continue
        if n_words != 1 or len(last_plain) > orphan_max:
            break
        merged = (out[-2].rstrip() + " " + last).strip()
        if len(merged) <= max_chars:
            out[-2] = merged
            out.pop()
            continue
        # Tên riêng: kéo 1–2 từ trước vào mảnh sau (giữ «Thiên Lang Tinh» đủ ngắn)
        if core[:1].isupper() and len(core) <= 8:
            prev_words = unglue_vi_names(out[-2]).split()
            placed = False
            for k in (2, 1, 3):
                if len(prev_words) <= k:
                    continue
                head = " ".join(prev_words[:-k]).strip()
                tail = glue_vi_names((" ".join(prev_words[-k:]) + " " + last_plain).strip())
                if head and len(head) <= max_chars and len(tail) <= max_chars:
                    out[-2] = glue_vi_names(head) if " " in head else head
                    out[-1] = tail
                    placed = True
                    break
            if placed:
                break
        break
    return out or parts


def wrap_subtitle_text(text: str, max_chars: int = 50, max_lines: int = 1) -> str:
    """Gói phụ đề tối đa max_lines dòng, mỗi dòng ≤ max_chars.

    max_lines=1: KHÔNG soft-wrap (giữ 1 dòng — CUT display lo cắt câu dài).
    Tránh dòng 2 mồ côi ngắn («hai giây.»).
    """
    text = (text or "").strip()
    if not text or max_lines <= 0:
        return text
    if max_lines == 1:
        # Không cắt «…» — ASS WrapStyle=2 + CUT display; cắt chữ làm mất nghĩa.
        return text.replace("\n", " ").replace("\r", " ")

    def _pack_words(words: list[str]) -> list[str]:
        lines: list[str] = []
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip() if cur else w
            if len(trial) <= max_chars:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            if len(w) > max_chars:
                for i in range(0, len(w), max_chars):
                    chunk = w[i : i + max_chars]
                    if i + max_chars < len(w) and len(lines) >= max_lines - 1:
                        lines.append(chunk[: max(1, max_chars - 1)] + "…")
                        return lines[:max_lines]
                    lines.append(chunk)
                cur = ""
            else:
                cur = w
            if len(lines) >= max_lines:
                return lines[:max_lines]
        if cur:
            if len(lines) >= max_lines:
                last = lines[-1]
                room = max_chars - len(last)
                if room > 4:
                    lines[-1] = (last + " " + cur)[: max_chars - 1] + "…"
                else:
                    lines[-1] = last[: max(1, max_chars - 1)] + "…"
            else:
                lines.append(cur)
        return lines[:max_lines]

    if " " in text or _NBSP in text:
        lines = _pack_words(text.split(" "))
        # Orphan: dòng cuối quá ngắn → gộp 1 dòng (đừng đẩy «hai giây.» xuống)
        if len(lines) == 2 and len(lines[1]) <= max(12, max_chars // 3):
            joined = (lines[0] + " " + lines[1]).strip()
            # Cho phép vượt nhẹ (~15%) để giữ 1 dòng
            if len(joined) <= int(max_chars * 1.15) + 2:
                return joined
            # Cân 2 dòng gần đều hơn
            words = text.split()
            best = lines
            best_score = abs(len(lines[0]) - len(lines[1]))
            for k in range(1, len(words)):
                a, b = " ".join(words[:k]), " ".join(words[k:])
                if not a or not b:
                    continue
                if len(a) > max_chars or len(b) > max_chars:
                    continue
                sc = abs(len(a) - len(b))
                if sc < best_score:
                    best, best_score = [a, b], sc
            return "\n".join(best)
        return "\n".join(lines)

    # CJK / không space
    lines = []
    for i in range(0, len(text), max_chars):
        if len(lines) >= max_lines - 1:
            rest = text[i:]
            if len(rest) <= max_chars:
                lines.append(rest)
            else:
                lines.append(rest[: max(1, max_chars - 1)] + "…")
            break
        lines.append(text[i : i + max_chars])
    return "\n".join(lines[:max_lines])


def _wrap_cfg(key: str = "vi") -> tuple[int, int]:
    # Mặc định 1 dòng: không soft-wrap; câu dài do CUT (script.display) lo.
    wrap_chars = int(_cfg_get("SUB_WRAP_CHARS", "37") or 37)
    wrap_lines = int(_cfg_get("SUB_WRAP_LINES", "1") or 1)
    return wrap_chars, wrap_lines


def expand_placed_with_display(
    placed: list[dict],
    display: list[dict],
    key: str = "vi",
) -> list[dict]:
    """Tách mỗi câu TTS (script 1:1) theo mảnh CUT trong script.display (src_i).

    Giữ start/end thật trên dub_fit — chia tỉ lệ theo độ dài VI (hoặc dur display).
    Không có display / không cắt → giữ nguyên placed.
    """
    by_src: dict[int, list[dict]] = {}
    for r in display or []:
        if not isinstance(r, dict):
            continue
        sid = r.get("src_i")
        if sid is None:
            continue
        try:
            sid = int(sid)
        except Exception:  # noqa: BLE001
            continue
        by_src.setdefault(sid, []).append(r)

    out: list[dict] = []
    for p in placed or []:
        if not isinstance(p, dict):
            continue
        try:
            i = int(p.get("i"))
        except Exception:  # noqa: BLE001
            out.append(dict(p))
            continue
        parts = [x for x in by_src.get(i, []) if (x.get(key) or x.get("vi") or "").strip()]
        if len(parts) <= 1:
            out.append(dict(p))
            continue
        start = float(p.get("start", 0) or 0)
        end = float(p.get("end") or 0) or (start + float(p.get("dur") or 0))
        if end <= start:
            end = start + max(float(p.get("dur") or 0), 0.4)
        weights: list[float] = []
        for r in parts:
            st = float(r.get("start", 0) or 0)
            en = float(r.get("end", st) or st)
            w = en - st if en > st else 0.0
            if w < 0.05:
                w = float(max(len((r.get(key) or r.get("vi") or "").strip()), 1))
            weights.append(w)
        total_w = sum(weights) or 1.0
        span = end - start
        t = start
        for idx, (r, w) in enumerate(zip(parts, weights)):
            t1 = end if idx == len(parts) - 1 else (t + span * (w / total_w))
            if t1 < t + 0.12:
                t1 = min(end, t + 0.12)
            np = dict(p)
            np["vi"] = glue_vi_names((r.get(key) or r.get("vi") or "").strip())
            if r.get("zh"):
                np["zh"] = r.get("zh")
            np["start"] = round(t, 3)
            np["end"] = round(t1, 3)
            np["dur"] = round(max(0.0, t1 - t), 3)
            np["src_i"] = i
            out.append(np)
            t = t1
    return out


def _maybe_wrap(text: str, key: str) -> str:
    if key != "vi":
        return (text or "").replace("\n", " ").replace("\r", " ").strip()
    # NBSP trong tên — không cắt «Thiên|Lang|Tinh» / «đàn|ông» nếu ASS soft-wrap
    text = glue_vi_names((text or "").replace("\n", " ").replace("\r", " ").strip())
    wc, wl = _wrap_cfg(key)
    out = wrap_subtitle_text(text, max_chars=wc, max_lines=wl)
    if wl <= 1:
        out = out.replace("\n", " ").replace("\r", " ")
    return out


def fmt_ts(sec: float) -> str:
    """Giây (float) -> 'HH:MM:SS,mmm' theo chuẩn SRT."""
    if sec is None or sec < 0:
        sec = 0.0
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list[dict], key: str = "text") -> str:
    """Đổ list câu ra chuỗi SRT. Bỏ câu rỗng, đánh số lại liên tục."""
    out: list[str] = []
    n = 0
    for seg in segments:
        text = (seg.get(key) or "").strip()
        if not text:
            continue
        n += 1
        out.append(str(n))
        out.append(f"{fmt_ts(seg.get('start', 0))} --> {fmt_ts(seg.get('end', 0))}")
        out.append(_maybe_wrap(text, key))
        out.append("")
    return "\n".join(out)


def to_srt_fit(segments: list[dict], durs: dict, key: str = "vi",
               margin: float = 0.35, min_dur: float = 0.8) -> str:
    """SRT burn theo script + dur TTS gốc (KHÔNG biết đàn hồi assemble).

    Ưu tiên dùng to_srt_placed(tts_fit.json) khi đã ghép khớp time.
    """
    out: list[str] = []
    n = 0
    segs = [s for s in segments if (s.get(key) or "").strip()]
    for idx, seg in enumerate(segs):
        text = (seg.get(key) or "").strip()
        start = float(seg.get("start", 0) or 0)
        orig_end = float(seg.get("end", start) or start)
        d = durs.get(seg.get("i"))
        end = start + max(float(d), min_dur) + margin if (d and d > 0.1) else orig_end
        nxt = float(segs[idx + 1].get("start", 0) or 0) if idx + 1 < len(segs) else None
        if nxt is not None and nxt > start:
            end = min(end, nxt - 0.05)          # đừng đè câu sau
        if end <= start:
            end = start + min_dur
        n += 1
        out.append(str(n))
        out.append(f"{fmt_ts(start)} --> {fmt_ts(end)}")
        out.append(_maybe_wrap(text, key))
        out.append("")
    return "\n".join(out)


def to_srt_placed(placed: list[dict], key: str = "vi",
                  margin: float = 0.12, min_dur: float = 0.5) -> str:
    """SRT burn khớp giọng sau assemble_fit: dùng start/end thật trên dub_fit.wav."""
    out: list[str] = []
    n = 0
    rows = [p for p in placed if (p.get(key) or p.get("vi") or "").strip()]
    rows.sort(key=lambda p: float(p.get("start", 0) or 0))
    for idx, p in enumerate(rows):
        text = (p.get(key) or p.get("vi") or "").strip()
        start = float(p.get("start", 0) or 0)
        end = float(p.get("end") or 0) or (start + float(p.get("dur") or 0))
        end = max(end, start + min_dur) + margin
        nxt = float(rows[idx + 1].get("start", 0) or 0) if idx + 1 < len(rows) else None
        if nxt is not None and nxt > start:
            end = min(end, nxt - 0.04)
        if end <= start:
            end = start + min_dur
        n += 1
        out.append(str(n))
        out.append(f"{fmt_ts(start)} --> {fmt_ts(end)}")
        out.append(_maybe_wrap(text, key))
        out.append("")
    return "\n".join(out)
