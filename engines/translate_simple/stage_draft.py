"""Bước 2: Plan (cloud) ∥ NMT nháp (GPU) — chạy SONG SONG bằng 2 thread.

Plan: dùng lại glossary.json nếu có (trừ force — như run_translate cũ; context_pack.skip_plan
luôn giữ), không thì translate.build_plan (PLAN_MODE=file → upload SRT; lỗi → sample), rồi
series_glossary.apply + ensure_hard_terms (+ pronoun analyze nếu SIMPLE_PRONOUN_ANALYZE=1).
Plan lỗi → plan rỗng (vẫn dịch, kém nhất quán) — y như luồng cũ.

NMT: mt_zh_vi.draft_rows cho câu chưa có vi (không manual). Vì chạy song song nên NMT chưa có plan
của tập này: flavor base/QT chọn theo glossary.json cũ hoặc bảng bộ (series) nếu có, không thì base
(mặc định cho mixed/chưa rõ). SIMPLE_NMT_WAIT_PLAN=1 → NMT chờ plan (tuần tự, đúng flavor).
NMT tắt / lỗi → nháp rỗng → review dịch mới các câu đó (cờ «dịch»).
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import cfg
from .script_store import ScriptStore, has_cjk, is_manual


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _series_plan(work_dir: Path) -> dict | None:
    try:
        import engines.series_glossary as series_glossary
        return series_glossary.load(series_glossary.series_key(work_dir))
    except Exception as e:  # noqa: BLE001
        print(f"[simple-plan] bảng bộ: {e}", flush=True)
        return None


PLAN_FAILED = "plan_failed"      # plan["_meta"][PLAN_FAILED] = lý do (R1.2)


def plan_failed(plan: dict | None) -> str:
    """Lý do plan hỏng ("" = plan dùng được). Plan hỏng = lập lỗi, hoặc không có nhân vật lẫn thuật ngữ nào."""
    meta = plan.get("_meta") if isinstance(plan, dict) else None
    return str((meta or {}).get(PLAN_FAILED) or "") if isinstance(meta, dict) else ""


def _has_content(plan: dict | None) -> bool:
    return isinstance(plan, dict) and bool(plan.get("characters") or plan.get("terms") or plan.get("pronouns"))


def build_plan(work_dir: Path, segments: list[dict], target: str, *, force: bool = False) -> dict:
    """R1.2 (fix 2026-11-14): lập plan LỖI (cloud 429/timeout…) hoặc ra plan trống → đánh dấu `_meta.plan_failed`,
    KHÔNG ghi glossary.json trống, và glossary.json mang dấu này không được dùng lại (lần chạy sau lập lại).
    Trước đây plan trống bị ghi ra đĩa → mọi lần sau «dùng lại glossary.json có sẵn» → cả phim dịch không có bảng
    nhân vật / xưng hô mà bộ kiểm không có cặp nào để so → cổng vẫn done."""
    import engines.translate as T
    from core.fileio import atomic_write_json

    plan_path = work_dir / "glossary.json"
    skip_plan = bool((_load_json(work_dir / "context_pack.json") or {}).get("skip_plan"))
    plan = None
    fail_why = ""
    if plan_path.exists() and (not force or skip_plan):
        plan = _load_json(plan_path)
        if isinstance(plan, dict) and plan_failed(plan) and not skip_plan:
            print(f"[simple-plan] glossary.json mang dấu plan hỏng ({plan_failed(plan)[:80]}) → lập lại", flush=True)
            plan = None
        if isinstance(plan, dict):
            print("[simple-plan] dùng lại glossary.json có sẵn", flush=True)
            if skip_plan:
                try:
                    from tools.context_studio.schema import apply_style_lock_from_genre
                    plan = apply_style_lock_from_genre(plan)
                except Exception:  # noqa: BLE001
                    pass
        else:
            plan = None
    if plan is None:
        try:
            kw = dict(model=None, name_hints=T._ocr_name_hints(work_dir) or None,
                      series_plan=_series_plan(work_dir), work_dir=work_dir)
            # V6: engines.translate.build_plan chưa có tham số before_local_llm (V2 translate-b2) và không đổi
            # preset trong plan → chỉ truyền khi hàm nhận (tránh TypeError → plan rỗng).
            try:
                import inspect
                if "before_local_llm" in inspect.signature(T.build_plan).parameters:
                    kw["before_local_llm"] = None  # simple: KHÔNG đổi preset local
            except (TypeError, ValueError):
                pass
            plan = T.build_plan(segments, target=target, **kw)
            if not _has_content(plan):
                fail_why = "model không trả nhân vật / thuật ngữ nào"
            else:
                # review 2026-11-12: ghi plan vừa lập TRƯỚC save_from (như run_translate cũ) — trước đây
                # save_from đọc glossary.json chưa tồn tại / cũ → bảng bộ không học được tập mới.
                atomic_write_json(plan_path, plan, fsync=False)
        except Exception as e:  # noqa: BLE001
            fail_why = f"{type(e).__name__}: {e}"[:200]
            plan = None
        if fail_why:
            print(f"[simple-plan] LỖI lập plan ({fail_why}) → plan tạm, KHÔNG lưu glossary.json", flush=True)
            if not isinstance(plan, dict):
                plan = {"content_type": "", "genre": "", "tone": "", "characters": [], "terms": [],
                        "pronouns": [], "scenes": [], "pronoun_notes": ""}
    try:
        import engines.series_glossary as series_glossary
        plan = series_glossary.apply(work_dir, plan)
        if not fail_why:      # plan hỏng không được «dạy» bảng bộ
            series_glossary.save_from(work_dir, reason="cập nhật plan tập mới (simple)")
    except Exception as e:  # noqa: BLE001
        print(f"[simple-plan] bảng bộ: {e}", flush=True)
    if fail_why:
        meta = plan.get("_meta") if isinstance(plan.get("_meta"), dict) else {}
        plan["_meta"] = {**meta, PLAN_FAILED: fail_why}
        # Không ghi glossary.json: lần chạy sau phải lập lại. (Bảng bộ, nếu có, vẫn nằm trong `plan` trả về
        # để lần này dùng tạm khi SIMPLE_REQUIRE_PLAN=0.)
        return T.ensure_hard_terms(plan)
    plan = T.ensure_hard_terms(plan)
    if cfg.pronoun_analyze():
        try:
            from engines.pronoun_consistency import ensure_analyze_before_translate
            plan, _pa = ensure_analyze_before_translate(work_dir, plan, segments=segments, force=False)
            plan = T.ensure_hard_terms(plan)
        except Exception as e:  # noqa: BLE001
            print(f"[simple-plan] pronoun-analyze bỏ qua: {e}", flush=True)
    atomic_write_json(plan_path, plan, fsync=False)
    sl = plan.get("style_lock") if isinstance(plan, dict) else None
    if isinstance(sl, dict):
        atomic_write_json(work_dir / "style_lock.json", sl, fsync=False)
    return plan


def nmt_drafts(rows: list[dict], hint_plan: dict | None) -> dict[int, str]:
    """{i: nháp} — chỉ câu chưa có vi, không manual, chưa rv. NMT tắt/lỗi → {}."""
    todo = [r for r in rows if not is_manual(r) and not r.get("rv") and not (r.get("vi") or "").strip()
            and (r.get("zh") or "").strip()]
    if not todo:
        return {}
    try:
        import engines.mt_zh_vi as mt_zh_vi
        if not mt_zh_vi.enabled():
            print("[simple-nmt] MT_ZH_VI tắt → review dịch mới mọi câu", flush=True)
            return {}
        t0 = time.time()
        out = mt_zh_vi.draft_rows([{"i": r["i"], "text": r.get("zh") or ""} for r in todo], plan=hint_plan)
        out = {int(k): (v or "").strip() for k, v in (out or {}).items()
               if (v or "").strip() and not has_cjk(v)}
        print(f"[simple-nmt] nháp {len(out)}/{len(todo)} câu ({time.time() - t0:.1f}s, "
              f"{mt_zh_vi.resolve_model_id(hint_plan)})", flush=True)
        if cfg.nmt_unload():
            try:
                mt_zh_vi.unload()
            except Exception:  # noqa: BLE001
                pass
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[simple-nmt] lỗi ({e}) → nháp rỗng, review dịch mới", flush=True)
        return {}


def run(store: ScriptStore, segments: list[dict], target: str, *, force: bool = False,
        progress=None) -> tuple[dict, dict]:
    """Chạy plan ∥ NMT. Trả (plan, info). Ghi nháp NMT vào store (mt=1, trace 'nmt')."""
    wd = store.work_dir
    hint_plan = _load_json(wd / "glossary.json") if not force else None
    if not isinstance(hint_plan, dict):
        hint_plan = _series_plan(wd)
    rows_snapshot = [dict(r) for r in store.rows]
    info: dict = {"parallel": not cfg.nmt_wait_plan()}
    t0 = time.time()
    if cfg.nmt_wait_plan():
        plan = build_plan(wd, segments, target, force=force)
        info["t_plan"] = round(time.time() - t0, 2)
        drafts = nmt_drafts(rows_snapshot, plan)
        info["t_nmt"] = round(time.time() - t0 - info["t_plan"], 2)
    else:
        marks: dict = {}

        def _p():
            marks["plan_start"] = time.time()
            try:
                return build_plan(wd, segments, target, force=force)
            finally:
                marks["plan_end"] = time.time()

        def _n():
            marks["nmt_start"] = time.time()
            try:
                return nmt_drafts(rows_snapshot, hint_plan)
            finally:
                marks["nmt_end"] = time.time()

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="simple-draft") as ex:
            fp, fn = ex.submit(_p), ex.submit(_n)
            plan = fp.result()
            drafts = fn.result()
        info.update({k: round(v - t0, 3) for k, v in marks.items()})
    by_i = store.by_i()
    n = 0
    for i, vi in drafts.items():
        r = by_i.get(i)
        if r is None or is_manual(r) or (r.get("vi") or "").strip():
            continue
        if store.set_vi(r, vi, "nmt", "nháp NMT"):
            r["mt"] = 1
            n += 1
    store.write()
    info["n_nmt"] = n
    if progress:
        progress(f"plan ∥ NMT xong · nháp {n} câu")
    return plan, info
