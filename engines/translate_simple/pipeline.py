"""Điểm vào luồng dịch simple: `run(work_dir, target, *, progress, stop_check) -> dict`.

Ghi: script.json (1–1 với ASR, giữ mọi trường), script.display.json + sub.vi.srt (CUT như luồng cũ),
glossary.json (+ style_lock.json), trace.jsonl, manifest.stages.translate (V6: qua state.job_state.mutate):
  done    → status=done, reviewed=True, n_missing=0, mode="simple", simple={cổng}
  park/hold → status=pending, cur_text «soát dở — …» / «HOLD simple — …» (server park/HOLD).
Cổng TTS của server (translate_ready_for_tts / count_script_missing / rv) đọc đúng các file này.

Cờ thử nghiệm 2026-11-12 «next» (mặc định TẮT — tắt thì mọi nhánh dưới đây không chạy, kết quả y hệt trước):
  SIMPLE_ZH_SOURCE=1      zh_source.prepare trước khi dựng script (OCR hardsub thắng ASR khi khớp; sửa tên gần đúng)
  SIMPLE_PAIR_RULES=1     pair_rules.fill sau plan (điền cặp còn trống theo bảng quan hệ, src=rule)
  SIMPLE_PAIR_FILL=1      pair_fill.run sau review lượt 1 (1 lần gọi model, src=llm_fill)
  SIMPLE_LEARN_INFERRED=1 cổng done → cặp rule/llm_fill ghi vào glossary.json trước khi gộp bảng bộ
Cặp suy luận ghi work_dir/plan_inferred.json (KHÔNG vào glossary.json trừ SIMPLE_LEARN_INFERRED=1).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import cfg, gate, post, repair, stage_draft, stage_review, suggest, validate
from .plan_view import PlanView
from .script_store import ScriptStore, is_manual

STAGE = "translate"
INFERRED = "plan_inferred.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _manifest_update(work_dir: Path, fn) -> bool:
    """V6: mọi ghi manifest qua state.job_state.mutate (= MỘT Manifest.update: đọc lại đĩa → sửa → ghi trong
    khoá thread + liên tiến trình → JobIndex + event SSE). Chưa có manifest.json → tạo khung tối thiểu rồi update."""
    from state import job_state as js
    mp = work_dir / "manifest.json"
    if not mp.exists():
        from core.fileio import atomic_write_json
        atomic_write_json(mp, {"created_at": _now(), "stages": {}}, fsync=False)
    try:
        js.mutate(work_dir, fn)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[simple] manifest update lỗi: {e}", flush=True)
        return False


def _set_stage(work_dir: Path, **kv) -> None:
    def fn(d):
        st = dict((d.setdefault("stages", {})).get(STAGE) or {})
        st.update(kv)
        st["updated_at"] = time.time()
        d["stages"][STAGE] = st
    _manifest_update(work_dir, fn)


def _write_outputs(work_dir: Path, rows: list[dict]) -> None:
    from core.fileio import atomic_write_json
    from engines.subs import split_bilingual_rows, sub_split_cfg, to_srt
    max_zh, max_vi, max_dur = sub_split_cfg()
    display = split_bilingual_rows([dict(r) for r in rows], max_zh=max_zh, max_vi=max_vi, max_dur=max_dur)
    atomic_write_json(work_dir / "script.display.json", display, fsync=False)
    (work_dir / "sub.vi.srt").write_text(to_srt(display, key="vi"), encoding="utf-8")


def _load_prev_fill(wd: Path) -> list[dict]:
    try:
        d = json.loads((wd / INFERRED).read_text(encoding="utf-8"))
        return [p for p in (d.get("pairs") or []) if isinstance(p, dict) and p.get("src") == "llm_fill"]
    except Exception:  # noqa: BLE001
        return []


def _write_inferred(wd: Path, pv: PlanView, info: dict) -> None:
    from core.fileio import atomic_write_json
    atomic_write_json(wd / INFERRED, {
        "generated": _now(),
        "note": ("Cặp xưng hô do luồng simple TỰ SUY (src rule = bảng quan hệ, llm_fill = model bổ sung). KHÔNG có trong "
                 "glossary.json / bảng bộ trừ khi SIMPLE_LEARN_INFERRED=1 và cổng done. Đúng thì chép vào pronouns."),
        "pairs": pv.inferred_pairs(), "info": info,
    }, fsync=False)


def _learn_inferred(wd: Path, pv: PlanView) -> int:
    """Cổng done + SIMPLE_LEARN_INFERRED=1: thêm cặp suy luận vào glossary.json (không đè cặp có sẵn)."""
    from core.fileio import atomic_write_json
    from .plan_view import norm_name
    gp = wd / "glossary.json"
    try:
        plan = json.loads(gp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(plan, dict):
        return 0
    prs = plan.setdefault("pronouns", [])
    have = {(norm_name(pv.canon(p.get("who"))), norm_name(pv.canon(p.get("to"))))
            for p in prs if isinstance(p, dict)}
    n = 0
    for p in pv.inferred_pairs():
        k = (norm_name(p["who"]), norm_name(p["to"]))
        if k in have:
            continue
        prs.append({"who": p["who"], "to": p["to"], "self": " / ".join(p["self"]), "call": " / ".join(p["call"]),
                    "note": p.get("note") or "", "src": p.get("src")})
        have.add(k)
        n += 1
    if n:
        atomic_write_json(gp, plan, fsync=False)
    return n


def _asr_stutter_i(segments: list[dict]) -> set:
    """i của câu mà chữ ASR gốc có lặp tất định (để post.apply chỉ gỡ lặp VI ở đúng các câu đó — review agent D)."""
    try:
        from engines.translate import heal_obvious_internal_stutter
        from .script_store import _clean_zh
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    for s in segments:
        if not isinstance(s, dict):
            continue
        zh = _clean_zh((s.get("text") or s.get("zh") or "").strip())
        try:
            if zh and heal_obvious_internal_stutter(zh) != zh:
                out.add(int(s.get("i")))
        except (TypeError, ValueError):
            continue
    return out


def _heal_zh_stutter(store: ScriptStore) -> int:
    """Sửa lỗi 2026-11-12 (agent A, f0834acf0cc0 i=542 «杀吧杀吧，»): luồng cũ gỡ lặp ASR tất định
    (translate.apply_asr_stutter_heal — trong apply_style_polish và đầu run_review), simple trước đây KHÔNG gọi.
    Áp CÙNG hàm translate.heal_obvious_internal_stutter cho zh của câu KHÔNG manual, CHƯA rv (câu đã soát giữ nguyên
    để zh/vi không lệch nhau). vi được gỡ lặp ở post.apply. Mỗi câu đổi → 1 dòng trace (note, không đụng old/new vi)."""
    try:
        from engines.translate import heal_obvious_internal_stutter
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for r in store.rows:
        if r.get("manual") or r.get("rv"):
            continue
        old = str(r.get("zh") or "")
        new = heal_obvious_internal_stutter(old)
        if new != old:
            r["zh"] = new
            store.note("asr_heal", r.get("i"), f"gỡ lặp ZH: {old} → {new}")
            n += 1
    if n:
        store.write()
        print(f"[simple] gỡ lặp ASR (zh) {n} câu", flush=True)
    return n


# ------------------------------------------------------------------ fix 2026-11-13 agentM (60892cc63cb7_p01)
# Sự cố: câu ASR rác zh="None." (FireRed) → GPT không soát → «còn 1 câu chưa soát» → park mãi → part kẹt.
JUNK_VI = "…"   # cùng quy ước luồng cũ (translate._trivial_vi): TTS coi vi không chữ = trivial → slot im lặng đúng dài


def _mark_junk(store: ScriptStore) -> int:
    """Câu zh rác (text_junk.is_junk_text: rỗng / chỉ dấu / None-null-nan) → tự soát: vi=«…», rv=1, junk=1,
    skip_tts=1 (clone_tts._is_trivial_vi(«…») → _silence_entry: giữ đúng slot timeline, không gọi TTS).
    Không đếm «chưa soát», không gửi review/repair, bộ kiểm không đánh cờ. Câu manual giữ nguyên.
    Hàng từng junk mà zh nay hết rác (vd nguồn ZH OCR sync) → gỡ cờ để dịch bình thường."""
    try:
        from engines.text_junk import is_junk_text
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for r in store.rows:
        if is_manual(r):
            continue
        zh = str(r.get("zh") or "")
        if not is_junk_text(zh):
            if r.get("junk"):
                for k in ("junk", "skip_tts"):
                    r.pop(k, None)
                if (r.get("vi") or "").strip() == JUNK_VI:
                    r.pop("rv", None)
                    store.set_vi(r, "", "junk", "zh không còn rác → dịch lại")
            continue
        if r.get("junk") and r.get("rv") and (r.get("vi") or "").strip() == JUNK_VI:
            continue
        store.set_vi(r, JUNK_VI, "junk", f"zh rác {zh!r} → không dịch, không lồng tiếng (slot im lặng)")
        r["rv"] = 1
        r["junk"] = 1
        r["skip_tts"] = 1
        for k in ("s_flags", "fill_pending", "unreviewed_accepted"):
            r.pop(k, None)
        n += 1
    if n:
        store.write()
        print(f"[simple] {n} câu ZH rác → tự soát (vi «{JUNK_VI}», không TTS)", flush=True)
    return n


def _review_missing(store: ScriptStore, pv: PlanView, plan: dict, flags: dict, *, model, stop_check=None,
                    progress=None) -> tuple[dict | None, dict]:
    """Trước khi park: 1 lượt review CHỈ các câu còn chưa rv (lô nhỏ SIMPLE_MISSING_BATCH) → hậu kỳ + kiểm lại
    đúng các câu vừa soát. Trả (thống kê | None, cờ cập nhật)."""
    left = {int(r["i"]) for r in store.rows if not r.get("rv") and not r.get("junk") and r.get("i") is not None}
    if not left:
        return None, flags
    st = stage_review.run(store, pv, model=model, stop_check=stop_check, progress=progress,
                          batch=cfg.missing_batch())
    got = [r for r in store.rows if r.get("i") is not None and int(r["i"]) in left and r.get("rv")]
    if got:
        post.apply(store, plan, got)
        done_i = {int(r["i"]) for r in got}
        new_f = validate.validate(store.rows, pv, cfg.conf_min(), only=done_i)
        flags = {i: f for i, f in flags.items() if i not in done_i}
        flags.update(new_f)
    info = {"n": len(left), "ok": len(got), "lots": st.get("lots", 0)}
    store.note("review_missing", None, f"lượt bổ sung câu chưa soát: {info}")
    store.write()
    return info, flags


def _accept_unreviewed(store: ScriptStore) -> tuple[list[int], int]:
    """Còn ≤ SIMPLE_UNREVIEWED_TOLERANCE câu chưa soát VÀ mọi câu đó có bản nháp vi (không trống, không chữ Hán)
    → nhận bản nháp: rv=1 + unreviewed_accepted=1 (+ trace). Vượt ngưỡng / thiếu nháp → không đổi gì (park).
    Trả (danh sách i đã nhận, ngưỡng)."""
    from .script_store import has_cjk
    tol = cfg.unreviewed_tolerance(len(store.rows))
    left = [r for r in store.rows if not r.get("rv")]
    if not left or len(left) > tol:
        return [], tol
    if any(not (r.get("vi") or "").strip() or has_cjk(r.get("vi")) for r in left):
        return [], tol
    out = []
    for r in left:
        r["rv"] = 1
        r["unreviewed_accepted"] = 1
        store.note("unreviewed_accepted", r.get("i"), f"nhận bản nháp chưa soát (≤ {tol} câu): {r.get('vi')!r}")
        try:
            out.append(int(r.get("i")))
        except (TypeError, ValueError):
            pass
    store.write()
    return out, tol


def _brain_line(wd: Path, text: str) -> None:
    """1 dòng cho Second Brain nếu brain đang chạy trong tiến trình (best-effort, không import mới, không test)."""
    import sys
    ab = sys.modules.get("agents.agent_brain") or sys.modules.get("agent_brain")
    if ab is None or "pytest" in sys.modules:
        return
    try:
        if ab.brain_enabled():
            ab._emit(wd.name, "need", level="decision", text=text)
    except Exception as e:  # noqa: BLE001
        print(f"[simple] brain notify skip: {e}", flush=True)


def run(work_dir: str | Path, target: str = "Tiếng Việt", *, force: bool = False, rereview: bool = False,
        progress=None, stop_check=None) -> dict:
    wd = Path(work_dir)
    asr_p = wd / "asr.json"
    if not asr_p.exists():
        raise RuntimeError("Chưa có asr.json. Chạy asr.py trước.")
    segments = json.loads(asr_p.read_text(encoding="utf-8"))
    if stop_check is None:
        def stop_check():  # noqa: E306
            return (wd / ".stop").exists()
    last = {"t": 0.0}

    def prog(msg: str, force_write: bool = False) -> None:
        print(f"[simple] {msg}", flush=True)
        if progress:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001
                pass
        if force_write or time.time() - last["t"] >= 2.0:
            last["t"] = time.time()
            _set_stage(wd, cur_text=f"simple · {msg}"[:200])

    t0 = time.time()
    _set_stage(wd, status="running", started_at=_now(), mode="simple", cur_text="simple · bắt đầu",
               n_total=len(segments))
    zh_rep = None
    if cfg.zh_source():
        from . import zh_source
        segments, zh_rep = zh_source.prepare(wd, segments)
    store = ScriptStore(wd)
    if store.rotate_trace(cfg.trace_max_bytes()):
        print("[simple] trace.jsonl vượt ngưỡng → xoay sang trace.1.jsonl", flush=True)
    store.ensure_from_asr(segments, force=force)
    if zh_rep is not None:
        from . import zh_source
        n_sync = zh_source.sync_rows(store, segments)
        store.write()
        store.note("zh_source", None, f"nguồn ZH: hardsub={(zh_rep.get('detect') or {}).get('has_hardsub')} "
                                      f"ocr={zh_rep.get('n_ocr')} namefix={len(zh_rep.get('name_fixes') or [])} "
                                      f"sync={n_sync}{(' lỗi=' + zh_rep['error']) if zh_rep.get('error') else ''}")
    # review agent D: gỡ lặp SAU sync nguồn ZH (trước đây sync trả lại chữ lặp của OCR/namefix)
    store.stutter_i = _asr_stutter_i(segments)
    _heal_zh_stutter(store)
    n_junk = _mark_junk(store)
    store.note("start", None, f"simple run force={force} rereview={rereview} n_asr={len(segments)}")
    model = cfg.review_model(wd)
    try:
        # 2) plan ∥ NMT
        plan, dinfo = stage_draft.run(store, segments, target, force=force, progress=prog)
        pv = PlanView(plan)
        extra: dict = {}           # thống kê cờ «next» — rỗng khi mọi cờ tắt (manifest y hệt trước)
        if cfg.pair_rules():
            from . import pair_rules
            added = pair_rules.fill(pv)
            extra["pair_rules"] = {"n_added": len(added)}
            store.note("pair_rules", None, f"điền {len(added)} cặp theo bảng quan hệ: "
                       + "; ".join(f"{p['who']}→{p['to']}" for p in added[:20]))
        if cfg.pair_fill():
            prev = [p for p in _load_prev_fill(wd) if pv.add_pair(p)]
            if prev:
                extra["pair_fill_prev"] = len(prev)
        if stop_check():
            raise RuntimeError("stopped by user")
        # 3) review theo lô
        pair_raw: list[dict] = []
        rstats = stage_review.run(store, pv, model=model, stop_check=stop_check, progress=prog,
                                  rereview=rereview or force,
                                  pair_sink=pair_raw if cfg.suggest_pairs() else None)
        if cfg.pair_fill():
            from . import pair_fill
            pf = pair_fill.run(store.rows, pv, model=cfg.pair_fill_model(wd), conf_review=cfg.conf_min(),
                               min_n=cfg.pair_fill_min(), conf_min=cfg.pair_fill_conf(), stop_check=stop_check)
            extra["pair_fill"] = {"asked": len(pf["asked"]), "added": len(pf["added"]), "rows": len(pf["rows"]),
                                  **({"error": pf["error"]} if pf.get("error") else {})}
            store.note("pair_fill", None, f"hỏi {len(pf['asked'])} cặp, nhận {len(pf['added'])}: "
                       + "; ".join(f"{p['who']}→{p['to']}" for p in pf["added"])
                       + (f" · lỗi: {pf['error']}" if pf.get("error") else ""))
        if extra:
            rstats.update(extra)
        # 4) hậu kỳ tất định + kiểm
        post.apply(store, plan)
        flags = validate.validate(store.rows, pv, cfg.conf_min())
        store.write()
        prog(f"kiểm: {len(flags)} câu bị cờ", force_write=True)
        # 5) vá câu bị cờ (bỏ qua khi cloud chết hẳn: review không lô nào qua)
        rlog: list[dict] = []
        cloud_dead = rstats["n_todo"] > 0 and rstats["n_ok"] == 0
        if flags and not cloud_dead:
            flags, rlog = repair.run(store, pv, flags, model=model, stop_check=stop_check, progress=prog)
        store.write()
        # 5b) fix 2026-11-13: còn câu chưa soát → 1 lượt review CHỈ các câu đó (lô nhỏ); rồi ≤ ngưỡng → nhận nháp
        miss_info = None
        if store.n_unreviewed() > 0 and not cloud_dead:
            miss_info, flags = _review_missing(store, pv, plan, flags, model=model, stop_check=stop_check,
                                               progress=prog)
        accepted, unrev_tol = _accept_unreviewed(store)
        if n_junk or miss_info or accepted:
            rstats["junk"] = n_junk
            if miss_info:
                rstats["review_missing"] = miss_info
            if accepted:
                rstats["unreviewed_accepted"] = accepted
                rstats["unreviewed_tolerance"] = unrev_tol
        _write_outputs(wd, store.rows)
        # đề xuất cặp xưng hô thiếu (KHÔNG chèn vào plan / bảng bộ)
        try:
            sug = suggest.write(wd, store.rows, pv, pair_raw, cfg.conf_min(),
                                extra=({"inferred_pairs": pv.inferred_pairs()} if extra else None))
            rstats["suggest"] = sug
            if sug.get("n_model") or sug.get("n_missing"):
                store.note("suggest", None, f"plan_suggestions.json: {sug}")
        except Exception as e_sg:  # noqa: BLE001
            print(f"[simple] plan_suggestions lỗi: {e_sg}", flush=True)
        if cfg.pair_rules() or cfg.pair_fill():
            try:
                _write_inferred(wd, pv, extra)
            except Exception as e_pi:  # noqa: BLE001
                print(f"[simple] plan_inferred lỗi: {e_pi}", flush=True)
        # 6) cổng
        try:
            from engines.translate import translate_coverage_gap
            gap = max(0, int(translate_coverage_gap(wd)))
        except Exception:  # noqa: BLE001
            gap = 0
        g = gate.decide(n_rows=len(store.rows), n_asr=len(segments), coverage_gap=gap,
                        n_missing=store.n_missing(), n_unreviewed=store.n_unreviewed(), flags=flags)
    except Exception as e:  # noqa: BLE001
        store.write()
        stopped = "stopped by user" in str(e).lower()
        _set_stage(wd, status="pending", n_missing=store.n_missing(),
                   cur_text=("simple · đã dừng theo yêu cầu" if stopped else f"simple lỗi: {str(e)[:150]}"),
                   **({} if stopped else {"last_error": str(e)[:200]}))
        store.note("error", None, str(e)[:300])
        raise
    res = {"status": g["status"], "gate": g, "draft": dinfo, "review": rstats, "repair": rlog,
           "model": model, "t_total": round(time.time() - t0, 1), "script_path": str(store.path)}
    acc_txt = ""
    if g["status"] == "done" and accepted:
        acc_txt = (f"nhận {len(accepted)} câu nháp chưa soát (≤ {unrev_tol}): i="
                   + ",".join(str(i) for i in accepted[:20]))
    store.note("gate", None, f"{g['status']}: {'; '.join(g['reasons']) or 'ok'}" + (f" · {acc_txt}" if acc_txt else ""))
    simple_info = {k: g[k] for k in ("status", "reasons", "n_flagged", "n_flag_soft", "flag_limit",
                                     "flags_by_kind", "n_unreviewed")}
    simple_info.update({"review": rstats, "repair": rlog, "t_total": res["t_total"], "at": _now()})
    if acc_txt:
        simple_info["unreviewed_accepted"] = accepted
        simple_info["accept_reason"] = acc_txt
    if g["status"] == "done":
        def fn_done(d):
            prev = dict((d.setdefault("stages", {})).get(STAGE) or {})
            keep = {k: prev[k] for k in ("started_at", "voice", "speed", "engine") if k in prev}
            d["stages"][STAGE] = {
                **keep, "status": "done", "finished_at": _now(), "script_path": str(store.path),
                "glossary_path": str(wd / "glossary.json"), "n_segments": len(store.rows), "n_missing": 0,
                "model": f"simple:{model}", "mode": "simple", "reviewed": True, "review_model": model,
                "progress": 100, "cur_text": f"simple · đủ bản dịch ({g['n_flagged']} cờ ≤ {g['flag_limit']})"
                + (f" · nhận {len(accepted)} câu nháp chưa soát" if acc_txt else ""),
                "simple": simple_info, "updated_at": time.time(),
            }
        _manifest_update(wd, fn_done)
        if acc_txt:
            _brain_line(wd, f"dịch simple: {acc_txt}")
        # cặp suy luận (rule / llm_fill) chỉ vào hồ sơ bộ phim khi bật SIMPLE_LEARN_INFERRED (tránh lan sai)
        if cfg.learn_inferred() and pv.inferred_pairs():
            try:
                n_l = _learn_inferred(wd, pv)
                store.note("learn_inferred", None, f"ghi {n_l} cặp suy luận vào glossary.json")
            except Exception as e_li:  # noqa: BLE001
                print(f"[simple] learn_inferred lỗi: {e_li}", flush=True)
        # như run_review cũ: soát xong → gộp plan tập này vào bảng bộ (best-effort)
        try:
            import engines.series_glossary as series_glossary
            series_glossary.save_from(wd, reason="hoàn thành rà soát (simple)")
        except Exception as e_sg:  # noqa: BLE001
            print(f"[simple] bảng bộ: {e_sg}", flush=True)
    else:
        why = "; ".join(g["reasons"])
        cur = (f"soát dở — {why}" if g["status"] == "park" else f"HOLD simple — {why}")[:240]
        _set_stage(wd, status="pending", n_missing=g["n_missing"], cur_text=cur, simple=simple_info,
                   mode="simple", review_model=model)
    print(f"[simple] cổng={g['status']} · {g['reasons'] or 'ok'} · {res['t_total']}s", flush=True)
    return res
