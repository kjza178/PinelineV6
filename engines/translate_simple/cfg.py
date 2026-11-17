"""Cấu hình luồng simple — mọi khoá prefix SIMPLE_ đọc qua config.get (env > pineline.config.json > mặc định).

Không sửa config.json. Khoá:
  TRANSLATE_PIPELINE       V6: mặc định simple; "legacy" = luồng cũ (rollback). Instance: "translate_pipeline".
                           Job đã bắt đầu luồng nào thì giữ luồng đó (manifest.translate_pipeline) — job_pipeline().
  SIMPLE_MODEL             model REVIEW cloud (trống = REVIEW model như luồng cũ: _resolve_review_model)
  SIMPLE_BATCH             số câu / lô review (30)
  SIMPLE_CTX               số câu ngữ cảnh chỉ đọc mỗi phía lô (4)
  SIMPLE_CONTEXT           A8: auto | full | window — full = mọi lô gửi CẢ PHIM + plan đầy đủ ở phần đầu cố định
                           (cache được); auto = full khi model là deepseek/… alibaba/… (film_context.py) (auto)
  SIMPLE_FULL_BATCH        ép cỡ lô ở chế độ full (trống = TỰ TÍNH: số câu ÷ số request song song, kẹp
                           SIMPLE_LOT_MIN..SIMPLE_LOT_MAX = 40..200) — luôn ≤ trần output (out_cap)
  SIMPLE_FULL_MAX_TOKENS   trần phần đầu MỖI CHƯƠNG; cả phim vượt → chia chương (40000)
  SIMPLE_CHAPTER_MARGIN    số câu ngữ cảnh hai bên mỗi chương (100)
  SIMPLE_OUT_TOKENS_PER_LINE  ước lượng token output / câu để giới hạn cỡ lô (90)
  SIMPLE_WORKERS           số lô gọi song song cho model KHÔNG phải nhà cung cấp API (Aurora, local) (8).
                           deepseek/… alibaba/… dùng <TÊN>_CONCURRENCY (mặc định 32) — workers_for(model)
  SIMPLE_REPAIR_ROUNDS     số lượt vá câu bị cờ (2, tối đa 2)
  SIMPLE_CONF_MIN          ngưỡng conf (độ chắc spk/to) để kiểm xưng hô (0.7)
  SIMPLE_GATE_FLAG_RATIO   tỉ lệ câu còn cờ tối đa để qua cổng (0.03)
  SIMPLE_GATE_FLAG_MAX     số câu còn cờ tối đa tuyệt đối (trống = chỉ dùng tỉ lệ; tối thiểu 1)
  SIMPLE_MAX_TOKENS        max_tokens mỗi lần gọi (8192)
  SIMPLE_TIMEOUT           timeout giây mỗi lần gọi (300)
  SIMPLE_TEMPERATURE       (0.2)
  SIMPLE_PRONOUN_ANALYZE   1 = chạy pronoun_consistency.ensure_analyze_before_translate sau plan (0)
  SIMPLE_REQUIRE_PLAN      1 = lập plan hỏng (lỗi cloud / plan trống) → không soát, park để lần sau lập lại (1)
  (2026-11-15: V6 bỏ hẳn NMT — các khoá SIMPLE_DRAFT / SIMPLE_NMT_WAIT_PLAN / SIMPLE_NMT_UNLOAD không còn dùng)
  SIMPLE_TRACE_MAX_MB      trace.jsonl vượt ngưỡng → xoay sang trace.1.jsonl (8; 0 = không xoay)
  SIMPLE_SUGGEST_PAIRS     1 = REVIEW được đề xuất cặp xưng hô thiếu → plan_suggestions.json (1)

Cờ thử nghiệm 2026-11-12 «next» (docs/fixes/2026-11-12-translate-simple-next.md) — MẶC ĐỊNH TẮT, tắt = y hệt trước:
  SIMPLE_LEXICON_EXCLUDE_GOLDEN 1 = bỏ mục lexicon src=golden_derived khỏi gợi ý (đo trung thực) (0)
  SIMPLE_PAIR_RULES        1 = điền cặp xưng hô CÒN TRỐNG theo bảng quan hệ data/lexicon/pronoun_rules_zh_vi.json (0)
  SIMPLE_PAIR_FILL         1 = sau review lượt 1: 1 lần gọi model bổ sung cặp thiếu (≥ SIMPLE_PAIR_FILL_MIN câu) (0)
  SIMPLE_PAIR_FILL_MIN     số câu tối thiểu của cặp spk→to thiếu để hỏi model (3)
  SIMPLE_PAIR_FILL_MODEL   model «mini» cho bước bổ sung cặp (trống = model REVIEW của simple)
  SIMPLE_PAIR_FILL_CONF    conf tối thiểu model trả để nhận cặp (0.6)
  SIMPLE_LEARN_INFERRED    1 = khi cổng done, cặp src rule/llm_fill được ghi vào glossary.json → bảng bộ (0)
  SIMPLE_ZH_SOURCE         1 = chọn nguồn ZH: phát hiện hardsub → OCR thắng ASR khi khớp; sửa tên gần đúng (0)
  SIMPLE_ZH_HS_SAMPLES     số khung lấy mẫu đều theo phim để phát hiện hardsub (24)
  SIMPLE_ZH_HS_MIN_RATIO   tỉ lệ khung có chữ ở dải phụ đề để coi là có hardsub (0.3)
  SIMPLE_ZH_NAMEFIX        1 = sửa tên ZH gần đúng theo hồ sơ phim khi SIMPLE_ZH_SOURCE=1 (1)
  SIMPLE_ZH_NAMEFIX_MIN    điểm giống tối thiểu để thay tên (0.75)
  SIMPLE_UNREVIEWED_TOLERANCE  số câu CHƯA SOÁT tối đa được nhận bản nháp (cờ unreviewed_accepted) thay vì park
                           (trống = max(3, 0.2% số câu); 0 = tắt, park như cũ) — fix 2026-11-13 agentM
  SIMPLE_MISSING_BATCH     cỡ lô của lượt review bổ sung CHỈ câu chưa soát trước khi park (4)
"""
from __future__ import annotations

import os


def get(key: str, default: str | None = None) -> str | None:
    try:
        from core.config import get as _g
        return _g(key, default)
    except Exception:  # noqa: BLE001
        return os.getenv(key, default)


def _int(key: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        v = int(float(str(get(key, str(default)) or default).strip()))
    except (TypeError, ValueError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _float(key: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
    try:
        v = float(str(get(key, str(default)) or default).strip())
    except (TypeError, ValueError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _flag(key: str, default: str) -> bool:
    return str(get(key, default) or default).strip().lower() in ("1", "true", "yes", "on")


# V6 (port F6 2026-11-12): MẶC ĐỊNH = simple. Rollback = "legacy" (run_translate + run_review + cổng vá cũ).
LEGACY_VALUES = ("legacy", "old", "off", "0", "false", "no")   # quy ước như V4/V5


def translate_pipeline() -> str:
    """"simple" | "legacy". Thứ tự: env TRANSLATE_PIPELINE (đặt tay / A-B) → instance
    (`"translate_pipeline"` trong instances/<tên>.json → core.instance.apply_env → PINELINE_TRANSLATE_PIPELINE)
    → pineline.config.json TRANSLATE_PIPELINE → mặc định "simple". Giá trị lạ → simple."""
    v = (os.getenv("TRANSLATE_PIPELINE") or "").strip()
    if not v:
        v = (os.getenv("PINELINE_TRANSLATE_PIPELINE") or "").strip()
    if not v:
        v = str(get("TRANSLATE_PIPELINE", "") or "").strip()
    return "legacy" if v.lower() in LEGACY_VALUES else "simple"


def pipeline_is_simple() -> bool:
    return translate_pipeline() == "simple"


# ───────────── luồng THEO JOB (port P6 2026-11-12 ← V2 F2) ─────────────
# Job đã bắt đầu luồng nào thì làm nốt luồng đó; mặc định instance chỉ áp cho job MỚI / chưa dịch /
# «Dịch LẠI» từ đầu. Đóng dấu manifest.translate_pipeline (+ _why, _at) qua state.job_state.
PIPELINE_SIMPLE = "simple"
PIPELINE_LEGACY = "legacy"
JOB_PIPELINE_KEY = "translate_pipeline"          # manifest.json (cấp gốc): luồng job đã dùng


def pipeline_default() -> str:
    """Luồng mặc định cho job MỚI = translate_pipeline() (env > instance > config > simple)."""
    return translate_pipeline()


def _infer_job_pipeline(d: dict, wd) -> str | None:
    """Job chưa đóng dấu: suy luồng đã dùng. None = theo mặc định instance.

    2026-11-17: KHÔNG còn suy «legacy» từ script.json / stage translate đã chạy — job V2/V5 chép sang (hoặc dịch dở)
    bị kéo về luồng cũ = NMT + TRANSLATE_MODEL (local) thay vì model Soát & dịch chính (DeepSeek…). Luồng simple làm
    tiếp trên script.json cũ được (ScriptStore giữ câu đã có, câu chưa soát sẽ được soát). Luồng cũ chỉ khi
    instance/config chọn legacy."""
    st = (d.get("stages") or {}).get("translate") or {}
    ag = d.get("agent") or {}
    if not isinstance(st, dict):
        st = {}
    if str(st.get("mode") or "").lower() == PIPELINE_SIMPLE or (isinstance(ag, dict) and ag.get("simple_park")):
        return PIPELINE_SIMPLE
    return None


def job_pipeline(work_dir, *, fresh: bool = False, stamp: bool = True) -> str:
    """Luồng dịch cho 1 job — mọi đường gọi dịch trong server hỏi hàm này (server._translate_pipeline_simple(wd)).

    - Đã đóng dấu manifest.translate_pipeline → giữ nguyên (job dở luồng cũ hoàn tất bằng luồng cũ).
    - Chưa đóng dấu (hoặc dấu legacy «inferred» cũ khi instance là simple): dấu vết simple → simple; còn lại →
      pipeline_default() (mặc định instance). script.json có sẵn KHÔNG còn ép luồng cũ (2026-11-17).
    - fresh=True («Dịch LẠI» từ đầu — force, không phải Soát lại) → pipeline_default() và đóng dấu lại.
    stamp=True: ghi dấu qua state.job_state.try_mutate khi đổi (lỗi ghi → bỏ qua, vẫn trả luồng)."""
    import json
    import time
    from pathlib import Path
    wd = Path(work_dir)
    mp = wd / "manifest.json"
    try:
        d = json.loads(mp.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            d = {}
    except Exception:  # noqa: BLE001
        d = {}
    cur = str(d.get(JOB_PIPELINE_KEY) or "").strip().lower()
    cur_why = str(d.get(JOB_PIPELINE_KEY + "_why") or "").strip().lower()
    # dấu «legacy/inferred» cũ (đoán từ script.json) không còn giá trị → tính lại theo mặc định instance
    stale = cur == PIPELINE_LEGACY and cur_why == "inferred" and pipeline_default() == PIPELINE_SIMPLE
    if not fresh and not stale and cur in (PIPELINE_SIMPLE, PIPELINE_LEGACY):
        return cur
    if fresh:
        p, why = pipeline_default(), "fresh"
    else:
        inf = _infer_job_pipeline(d, wd)
        p, why = (inf, "inferred") if inf else (pipeline_default(), "default")
    if stamp and p != cur and mp.exists():
        def _fn(dd):
            dd[JOB_PIPELINE_KEY] = p
            dd[JOB_PIPELINE_KEY + "_why"] = why
            dd[JOB_PIPELINE_KEY + "_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            from state import job_state as js
            js.try_mutate(wd, _fn)
        except Exception as e:  # noqa: BLE001
            print(f"[simple] đóng dấu translate_pipeline={p} lỗi: {e}", flush=True)
    return p


def batch() -> int:
    return _int("SIMPLE_BATCH", 30, 4, 120)


def context_mode() -> str:
    v = str(get("SIMPLE_CONTEXT", "auto") or "auto").strip().lower()
    return v if v in ("auto", "full", "window") else "auto"


def full_batch() -> int | None:
    """Cỡ lô ép ở chế độ full; None = tự tính theo số request song song (film_context.lot_size)."""
    raw = str(get("SIMPLE_FULL_BATCH", "") or "").strip()
    if not raw:
        return None
    try:
        return max(4, min(int(float(raw)), 2000))
    except ValueError:
        print(f"[simple] SIMPLE_FULL_BATCH={raw!r} không phải số → tự tính", flush=True)
        return None


def full_max_tokens() -> int:
    return _int("SIMPLE_FULL_MAX_TOKENS", 40000, 2000, 2000000)


def chapter_margin() -> int:
    return _int("SIMPLE_CHAPTER_MARGIN", 100, 0, 2000)


def out_tokens_per_line() -> int:
    return _int("SIMPLE_OUT_TOKENS_PER_LINE", 90, 20, 1000)


def out_cap(model: str | None) -> int:
    """Trần max_tokens output cho model: nhà cung cấp API → <TÊN>_MAX_TOKENS (engines/providers), khác → SIMPLE_MAX_TOKENS."""
    try:
        from engines import providers
        hit = providers.resolve(model or "")
        if hit is not None:
            return hit[0].out_cap()
    except Exception as e:  # noqa: BLE001
        print(f"[simple] đọc trần output của {model!r} lỗi: {e}", flush=True)
    lv = _local_override(model, "max_tokens")
    return lv if lv else max_tokens()


def _local_override(model: str | None, name: str) -> int:
    """AI trên máy (core/local_ai): LOCAL_MAX_TOKENS / LOCAL_CONCURRENCY nếu ĐÃ đặt; chưa đặt → 0 (dùng SIMPLE_*)."""
    try:
        from core import local_ai
        if local_ai.is_local_model(model) and local_ai.snapshot(probe=False)[name]["set"]:
            return int(local_ai.value(name))
    except Exception as e:  # noqa: BLE001
        print(f"[simple] đọc cài đặt AI trên máy ({name}) lỗi: {e}", flush=True)
    return 0


def ctx() -> int:
    return _int("SIMPLE_CTX", 4, 0, 20)


def workers() -> int:
    return _int("SIMPLE_WORKERS", 8, 1, 512)


def workers_for(model: str | None) -> int:
    """Số lô gọi cùng lúc: nhà cung cấp API → <TÊN>_CONCURRENCY (mặc định 32); model khác → SIMPLE_WORKERS (8)."""
    try:
        from engines import providers
        hit = providers.resolve(model or "")
        if hit is not None:
            return hit[0].concurrency()
    except Exception as e:  # noqa: BLE001
        print(f"[simple] đọc số request song song của {model!r} lỗi: {e}", flush=True)
    lv = _local_override(model, "concurrency")
    return lv if lv else workers()


def lot_min() -> int:
    return _int("SIMPLE_LOT_MIN", 40, 4, 1000)


def lot_max() -> int:
    return _int("SIMPLE_LOT_MAX", 200, 4, 2000)


def repair_rounds() -> int:
    return _int("SIMPLE_REPAIR_ROUNDS", 2, 0, 2)


def conf_min() -> float:
    return _float("SIMPLE_CONF_MIN", 0.7, 0.0, 1.0)


def gate_flag_ratio() -> float:
    return _float("SIMPLE_GATE_FLAG_RATIO", 0.03, 0.0, 1.0)


def gate_flag_max(n_rows: int) -> int:
    raw = str(get("SIMPLE_GATE_FLAG_MAX", "") or "").strip()
    by_ratio = int(gate_flag_ratio() * max(0, n_rows))
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return max(1, by_ratio)


def unreviewed_tolerance(n_rows: int) -> int:
    """fix 2026-11-13 agentM: số câu chưa soát được nhận bản nháp (thay vì park). Trống = max(3, 0.2% số câu)."""
    raw = str(get("SIMPLE_UNREVIEWED_TOLERANCE", "") or "").strip()
    if raw:
        try:
            return max(0, int(float(raw)))
        except ValueError:
            pass
    return max(3, int(0.002 * max(0, n_rows)))


def missing_batch() -> int:
    return _int("SIMPLE_MISSING_BATCH", 4, 1, 30)


def max_tokens() -> int:
    return _int("SIMPLE_MAX_TOKENS", 8192, 512, 32768)


def timeout() -> int:
    return _int("SIMPLE_TIMEOUT", 300, 30, 1800)


def temperature() -> float:
    return _float("SIMPLE_TEMPERATURE", 0.2, 0.0, 1.0)


def pronoun_analyze() -> bool:
    return _flag("SIMPLE_PRONOUN_ANALYZE", "0")


def require_plan() -> bool:
    """R1.2: 1 (mặc định) = lập plan hỏng thì KHÔNG soát (park, lần sau lập lại) — không dịch cả phim thiếu bảng
    nhân vật / xưng hô. 0 = vẫn dịch với plan tạm (như trước), plan hỏng vẫn không được lưu."""
    return _flag("SIMPLE_REQUIRE_PLAN", "1")


def trace_max_bytes() -> int:
    return int(_float("SIMPLE_TRACE_MAX_MB", 8.0, 0.0, 512.0) * 1024 * 1024)


def suggest_pairs() -> bool:
    return _flag("SIMPLE_SUGGEST_PAIRS", "1")


# ------------------------------------------------------------------ cờ «next» (mặc định tắt)
def lexicon_exclude_golden() -> bool:
    return _flag("SIMPLE_LEXICON_EXCLUDE_GOLDEN", "0")


def pair_rules() -> bool:
    return _flag("SIMPLE_PAIR_RULES", "0")


def pair_fill() -> bool:
    return _flag("SIMPLE_PAIR_FILL", "0")


def pair_fill_min() -> int:
    return _int("SIMPLE_PAIR_FILL_MIN", 3, 1, 1000)


def pair_fill_conf() -> float:
    return _float("SIMPLE_PAIR_FILL_CONF", 0.6, 0.0, 1.0)


def pair_fill_model(work_dir=None) -> str | None:
    m = str(get("SIMPLE_PAIR_FILL_MODEL", "") or "").strip()
    return m or review_model(work_dir)


def learn_inferred() -> bool:
    return _flag("SIMPLE_LEARN_INFERRED", "0")


def zh_source() -> bool:
    return _flag("SIMPLE_ZH_SOURCE", "0")


def zh_hs_samples() -> int:
    return _int("SIMPLE_ZH_HS_SAMPLES", 24, 4, 400)


def zh_hs_min_ratio() -> float:
    return _float("SIMPLE_ZH_HS_MIN_RATIO", 0.3, 0.0, 1.0)


def zh_namefix() -> bool:
    return _flag("SIMPLE_ZH_NAMEFIX", "1")


def zh_namefix_min() -> float:
    return _float("SIMPLE_ZH_NAMEFIX_MIN", 0.75, 0.5, 1.0)


def review_model(work_dir=None) -> str | None:
    m = str(get("SIMPLE_MODEL", "") or "").strip()
    if m:
        return m
    try:
        import engines.translate as T
        return T._resolve_review_model(work_dir, None)
    except Exception:  # noqa: BLE001
        return get("REVIEW_MODEL") or get("TRANSLATE_MODEL") or None
