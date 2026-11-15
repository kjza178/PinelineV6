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
Phần đầu ước lượng > SIMPLE_FULL_MAX_TOKENS (mặc định 100 000) → tự về window (ghi log), không gửi cụt.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import cfg
from .plan_view import PlanView, pair_line

_SKIP_TERM_TYPES = ("chengyu", "idiom", "style", "combat_taunt")


@dataclass(frozen=True)
class FilmContext:
    text: str               # phần đầu cố định (message user thứ nhất)
    n_lines: int
    est_tokens: int

    @property
    def digest(self) -> str:
        return hashlib.sha1(self.text.encode("utf-8")).hexdigest()[:12]


def est_tokens(text: str) -> int:
    """Ước lượng THÔ, nghiêng về dư: chữ Hán ≈ 1 token/chữ, phần còn lại ≈ 1 token / 3 ký tự."""
    cjk = sum(1 for ch in text if "㐀" <= ch <= "鿿")
    return cjk + (len(text) - cjk) // 3 + 1


def plan_block_full(pv: PlanView) -> str:
    """Plan ĐẦY ĐỦ (không cắt theo lô): phim, mọi nhân vật, mọi cặp xưng hô, mọi cảnh, mọi thuật ngữ."""
    L = pv.header_lines()
    if pv.chars:
        L.append("NHÂN VẬT:")
        for c in pv.chars:
            bits = [f"{c.get('zh')} = {c.get('vi')}"]
            if c.get("aliases"):
                bits.append("còn gọi: " + ", ".join(str(a) for a in list(c.get("aliases"))[:6]))
            if c.get("gender"):
                bits.append(f"giới {c.get('gender')}")
            if c.get("role"):
                bits.append(str(c.get("role"))[:120])
            L.append("  - " + " · ".join(bits))
    if pv.pairs:
        L.append("XƯNG HÔ BẮT BUỘC (người nói → người nghe):")
        for p in pv.pairs.values():
            L.append(pair_line(p))
    if pv.scenes:
        L.append("CẢNH:")
        for sc in pv.scenes:
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
            L.append("  - " + " · ".join(bits))
    terms = [t for t in pv.terms if str(t.get("type") or "").lower() not in _SKIP_TERM_TYPES]
    if terms:
        L.append("TÊN / THUẬT NGỮ (dùng đúng chữ VI):")
        for t in sorted(terms, key=lambda t: str(t["zh"])):
            L.append(f"  - {t['zh']} = {t['vi']}")
    return "\n".join(L)


def build(rows: list[dict], pv: PlanView) -> FilmContext:
    lines = []
    for r in rows:
        if r.get("junk"):
            continue
        try:
            sec = round(float(r.get("end") or 0) - float(r.get("start") or 0), 1)
        except (TypeError, ValueError):
            sec = 0.0
        lines.append(f"{r.get('i')} | {sec} | {(r.get('zh') or '').strip()}")
    text = ("HỒ SƠ PHIM (dùng cho MỌI lô):\n" + (plan_block_full(pv) or "(chưa có plan)")
            + "\n\nTOÀN BỘ PHIM — CHỈ ĐỌC để hiểu ngữ cảnh, người nói, quan hệ (i | giây | zh). "
              "KHÔNG trả các câu này; chỉ trả câu trong DANH SÁCH ở tin nhắn sau.\n" + "\n".join(lines))
    return FilmContext(text=text, n_lines=len(lines), est_tokens=est_tokens(text))


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
    """FilmContext nếu dùng chế độ full (và vừa giới hạn), không thì None (= chế độ window như cũ)."""
    if not wanted(model):
        return None
    fc = build(rows, pv)
    cap = cfg.full_max_tokens()
    if fc.est_tokens > cap:
        print(f"{label} phần đầu cả phim ~{fc.est_tokens} token > SIMPLE_FULL_MAX_TOKENS={cap} → dùng window",
              flush=True)
        return None
    print(f"{label} ngữ cảnh cả phim: {fc.n_lines} câu · ~{fc.est_tokens} token · {fc.digest}", flush=True)
    return fc
