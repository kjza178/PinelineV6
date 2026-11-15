"""A8 (2026-11-15): ngữ cảnh CẢ PHIM cho soát / vá — dùng khi model có ngữ cảnh lớn (DeepSeek, Alibaba…).

Cũ (chế độ «window»): mỗi lô 30 câu chỉ thấy khối plan cắt theo lô + ±4 câu ngữ cảnh → không biết chuyện ở đoạn khác,
xưng hô giữa các lô dễ lệch; khối plan khác nhau mỗi lô nên phần đầu prompt không bao giờ trùng → không trúng cache.

Mới (chế độ «full»): mọi lô gửi CÙNG MỘT phần đầu, giống nhau từng byte:
    [system] hướng dẫn  →  [user] HỒ SƠ PHIM (plan ĐẦY ĐỦ) + TOÀN BỘ PHIM (i | giây | zh, chỉ đọc)
rồi mới tới phần riêng của lô (gợi ý thành ngữ, ngữ cảnh VI ±SIMPLE_CTX, DANH SÁCH). Nhà cung cấp có cache phần đầu
(DeepSeek) chỉ tính tiền đầy đủ lần đầu; model thấy cả phim → hiểu người nói / quan hệ / cảnh đúng hơn.
Lô to hơn (SIMPLE_FULL_BATCH, mặc định 60) → ít lần gọi hơn.

Phần đầu dựng từ ẢNH CHỤP zh lúc bắt đầu bước (không đổi giữa các lô dù lô khác ghi vi song song). VI KHÔNG nằm
trong phần đầu (vi đổi liên tục → phá cache); VI của câu lân cận vẫn đi ở phần riêng của lô.

Chọn chế độ — SIMPLE_CONTEXT:
  auto (mặc định)  full khi model thuộc nhà cung cấp API (engines/providers: deepseek/…, alibaba/…), còn lại window
  full / window    ép

2026-11-15 — giảm token + phim rất dài (9–12 giờ, 6–10 nghìn câu):
  * Phần đầu gọn: mỗi câu «i|zh» (bỏ cột giây — phần riêng của lô đã có).
  * Phần đầu ước lượng > SIMPLE_FULL_MAX_TOKENS (mặc định 40 000) → chia CHƯƠNG: mỗi chương ~đều nhau, phần đầu riêng
    = plan LỌC theo chương (nhân vật / cặp / cảnh / thuật ngữ xuất hiện trong chương) + câu Trung của chương ± lề
    SIMPLE_CHAPTER_MARGIN câu (mặc định 100). Lô KHÔNG vắt qua hai chương → mọi lô trong chương dùng chung phần đầu
    (cache được). Không chia nổi (chương < 200 câu vẫn vượt) → window.
  * Làm nóng cache: stage_review chạy lô ĐẦU của mỗi chương trước, xong mới gửi đồng loạt các lô còn lại.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import math

from . import cfg
from .plan_view import PlanView, pair_line

_SKIP_TERM_TYPES = ("chengyu", "idiom", "style", "combat_taunt")


@dataclass(frozen=True)
class Chapter:
    idx: int
    lo: int                 # vị trí hàng (trong rows) đầu tiên chương PHỤ TRÁCH
    hi: int                 # vị trí sau cùng (không gồm)
    text: str               # phần đầu cố định của chương (message user thứ nhất)
    est_tokens: int
    label: str              # «TOÀN BỘ PHIM» | «ĐOẠN PHIM chương k/N»

    @property
    def digest(self) -> str:
        return hashlib.sha1(self.text.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class FilmContext:
    chapters: tuple
    n_lines: int

    @property
    def mode(self) -> str:
        return "full" if len(self.chapters) == 1 else "chapters"

    @property
    def text(self) -> str:                  # chế độ full (1 chương)
        return self.chapters[0].text

    @property
    def est_tokens(self) -> int:            # phần đầu lớn nhất
        return max(c.est_tokens for c in self.chapters)

    @property
    def digest(self) -> str:
        return self.chapters[0].digest if len(self.chapters) == 1 else \
            hashlib.sha1("".join(c.digest for c in self.chapters).encode()).hexdigest()[:12]

    def chapter_at(self, pos: int) -> Chapter:
        for c in self.chapters:
            if c.lo <= pos < c.hi:
                return c
        return self.chapters[-1]


def est_tokens(text: str) -> int:
    """Ước lượng THÔ, nghiêng về dư: chữ Hán ≈ 1 token/chữ, phần còn lại ≈ 1 token / 3 ký tự."""
    cjk = sum(1 for ch in text if "\u3400" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 3 + 1


def _char_lines(chars: list[dict]) -> list[str]:
    out = []
    for c in chars:
        bits = [f"{c.get('zh')} = {c.get('vi')}"]
        if c.get("aliases"):
            bits.append("còn gọi: " + ", ".join(str(a) for a in list(c.get("aliases"))[:6]))
        if c.get("gender"):
            bits.append(f"giới {c.get('gender')}")
        if c.get("role"):
            bits.append(str(c.get("role"))[:120])
        out.append("  - " + " · ".join(bits))
    return out


def _scene_line(sc: dict) -> str:
    rng = sc.get("range") or [sc.get("start_i"), sc.get("end_i")]
    try:
        head = f"[câu {rng[0]}→{rng[1]}]"
    except (TypeError, IndexError):
        head = "[?]"
    bits = [f"{head} {sc.get('location') or ''}".strip()]
    if sc.get("characters"):
        bits.append("có mặt: " + ", ".join(str(x) for x in sc["characters"][:10]))
    if sc.get("tone"):
        bits.append("tone: " + str(sc["tone"])[:120])
    if sc.get("pronouns_in_scene"):
        bits.append("xưng hô cảnh: " + str(sc["pronouns_in_scene"])[:200])
    return "  - " + " · ".join(bits)


def _plan_block(pv: PlanView, chars: list[dict], pairs: list[dict], scenes: list[dict], terms: list[dict]) -> str:
    L = pv.header_lines()
    if chars:
        L.append("NHÂN VẬT:")
        L += _char_lines(chars)
    if pairs:
        L.append("XƯNG HÔ BẮT BUỘC (người nói → người nghe):")
        L += [pair_line(p) for p in pairs]
    if scenes:
        L.append("CẢNH:")
        L += [_scene_line(sc) for sc in scenes]
    terms = [t for t in terms if str(t.get("type") or "").lower() not in _SKIP_TERM_TYPES]
    if terms:
        L.append("TÊN / THUẬT NGỮ (dùng đúng chữ VI):")
        L += [f"  - {t['zh']} = {t['vi']}" for t in sorted(terms, key=lambda t: str(t["zh"]))]
    return "\n".join(L)


def plan_block_full(pv: PlanView) -> str:
    """Plan ĐẦY ĐỦ (không cắt theo lô): phim, mọi nhân vật, mọi cặp xưng hô, mọi cảnh, mọi thuật ngữ."""
    return _plan_block(pv, pv.chars, list(pv.pairs.values()), pv.scenes, pv.terms)


def plan_block_range(pv: PlanView, rows: list[dict], i0: int, i1: int) -> str:
    """Plan LỌC cho một đoạn phim (câu i0..i1): nhân vật / cặp / cảnh / thuật ngữ xuất hiện trong đoạn."""
    zh_blob = "\n".join(str(r.get("zh") or "") for r in rows)
    scenes = pv.scenes_for(i0, i1)
    chars = pv.chars_for(zh_blob, scenes, limit=10_000)
    pairs = pv.pairs_for(chars, limit=10_000)
    terms = pv.terms_for(zh_blob, limit=10_000)
    return _plan_block(pv, chars, pairs, scenes, terms)


def _lines(rows: list[dict]) -> str:
    return "\n".join(f"{r.get('i')}|{(r.get('zh') or '').strip()}" for r in rows if not r.get("junk"))


_NOTE = "KHÔNG trả các câu này; chỉ trả câu trong DANH SÁCH ở tin nhắn sau."


def build(rows: list[dict], pv: PlanView) -> FilmContext:
    """Một chương = cả phim (chế độ full)."""
    text = ("HỒ SƠ PHIM (dùng cho MỌI lô):\n" + (plan_block_full(pv) or "(chưa có plan)")
            + "\n\nTOÀN BỘ PHIM — CHỈ ĐỌC để hiểu ngữ cảnh, người nói, quan hệ (i|zh). " + _NOTE + "\n"
            + _lines(rows))
    n = sum(1 for r in rows if not r.get("junk"))
    ch = Chapter(0, 0, len(rows), text, est_tokens(text), "TOÀN BỘ PHIM")
    return FilmContext((ch,), n)


def build_chapters(rows: list[dict], pv: PlanView, cap: int, *, margin: int | None = None,
                   min_lines: int = 200) -> FilmContext | None:
    """Chia phim thành chương vừa `cap` token phần đầu. Không chia nổi → None."""
    n = len(rows)
    margin = cfg.chapter_margin() if margin is None else margin
    n_lines = sum(1 for r in rows if not r.get("junk"))
    full_tokens = est_tokens(_lines(rows))
    k = max(2, math.ceil(full_tokens / max(1, cap * 0.8)))
    while True:
        size = math.ceil(n / k)
        if size < min_lines:
            return None
        chs = []
        ok = True
        for c in range(k):
            lo, hi = c * size, min(n, (c + 1) * size)
            if lo >= hi:
                break
            a, b = max(0, lo - margin), min(n, hi + margin)
            seg = rows[a:b]
            i0, i1 = rows[lo].get("i"), rows[hi - 1].get("i")
            ia, ib = rows[a].get("i"), rows[b - 1].get("i")
            label = f"ĐOẠN PHIM chương {c + 1}/{k}"
            text = (f"HỒ SƠ PHIM (phần liên quan {label}, câu {i0}→{i1}):\n"
                    + (plan_block_range(pv, seg, int(ia or 0), int(ib or 0)) or "(chưa có plan)")
                    + f"\n\n{label}: câu {ia}→{ib} (gồm lề ngữ cảnh) — CHỈ ĐỌC (i|zh). " + _NOTE + "\n"
                    + _lines(seg))
            t = est_tokens(text)
            if t > cap:
                ok = False
                break
            chs.append(Chapter(c, lo, hi, text, t, label))
        if ok:
            return FilmContext(tuple(chs), n_lines)
        k += 1


def wanted(model: str | None) -> bool:
    mode = cfg.context_mode()
    if mode in ("full", "window"):
        return mode == "full"
    try:
        from engines import providers
        return providers.resolve(model or "") is not None
    except Exception as e:  # noqa: BLE001
        print(f"[simple-ctx] không xác định được nhà cung cấp của {model!r}: {e} → window", flush=True)
        return False


def for_stage(rows: list[dict], pv: PlanView, model: str | None, *, label: str = "[simple-ctx]") -> FilmContext | None:
    """FilmContext (1 chương = cả phim, hoặc nhiều chương với phim dài) — None = chế độ window như cũ."""
    if not wanted(model):
        return None
    cap = cfg.full_max_tokens()
    fc = build(rows, pv)
    if fc.est_tokens <= cap:
        print(f"{label} ngữ cảnh cả phim: {fc.n_lines} câu · ~{fc.est_tokens} token · {fc.digest}", flush=True)
        return fc
    ch = build_chapters(rows, pv, cap)
    if ch is None:
        print(f"{label} phần đầu ~{fc.est_tokens} token > SIMPLE_FULL_MAX_TOKENS={cap} và không chia chương được "
              f"→ dùng window", flush=True)
        return None
    print(f"{label} phim dài ({fc.n_lines} câu, ~{fc.est_tokens} token) → {len(ch.chapters)} chương, mỗi chương "
          f"≤ ~{ch.est_tokens} token phần đầu · {ch.digest}", flush=True)
    return ch


def make_jobs(rows: list[dict], todo_idx: list[int], batch: int, ctx: int, film: FilmContext | None) -> list[dict]:
    """Chia lô như stage_review.make_jobs nhưng KHÔNG vắt qua hai chương; job["ch"] = chương."""
    from .stage_review import make_jobs as _mk
    if film is None:
        return _mk(rows, todo_idx, batch, ctx)
    jobs = []
    for ch in film.chapters:
        part = [p for p in todo_idx if ch.lo <= p < ch.hi]
        for j in _mk(rows, part, batch, ctx):
            j["ch"] = ch
            jobs.append(j)
    return jobs


def lot_size(model: str | None, film: FilmContext | None, n_todo: int = 0) -> int:
    """Cỡ lô ở chế độ full / chương — mục tiêu: mọi lô chạy trong MỘT đợt song song.
      ép SIMPLE_FULL_BATCH  → dùng giá trị đó
      tự tính (mặc định)    → ceil(số câu cần làm ÷ số request song song), kẹp [SIMPLE_LOT_MIN, SIMPLE_LOT_MAX]
    Luôn ≤ trần output của model (≈ SIMPLE_OUT_TOKENS_PER_LINE token / câu + 600 dự phòng) — lô quá to bị cắt cụt.
    Chế độ window (film=None) → SIMPLE_BATCH như cũ."""
    if film is None:
        return cfg.batch()
    cap = cfg.out_cap(model)
    fit = max(10, (cap - 600) // max(20, cfg.out_tokens_per_line()))
    forced = cfg.full_batch()
    if forced is not None:
        want = forced
    else:
        w = max(1, cfg.workers_for(model))
        want = max(cfg.lot_min(), min(cfg.lot_max(), math.ceil(max(1, n_todo) / w)))
    return max(4, min(want, fit))
