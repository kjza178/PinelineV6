"""F6 port 2026-11-12: luồng dịch simple + Demucs trước ASR từ V2 sang kiến trúc V6.

Kiểm phần RIÊNG V6 (test chức năng V2 đã port: test_translate_simple*.py, test_separate_demucs_chunks.py,
test_asr_demucs.py): Stage/Gate, state.apply_simple_gate, cờ instance (translate_pipeline, asr_use_demucs),
dữ liệu lexicon đúng chỗ, luật V6 (không Manifest trực tiếp ở lớp mới, không .save()).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

from pipeline.base import Context, Runner
from pipeline.gates.simple_translate_gate import SimpleTranslateGate, simple_translate_steps
from pipeline.stages.simple_translate import TranslateSimple
from state import job_state as js

ROOT = Path(__file__).resolve().parents[1]


def _man(wd: Path) -> dict:
    return json.loads((wd / "manifest.json").read_text(encoding="utf-8"))


def _ctx(wd: Path, stopped=lambda: False, **opts) -> Context:
    return Context(job_id=wd.name, work_dir=wd, opts={"target": "Tiếng Việt", **opts}, is_stopped=stopped,
                   log=lambda s: None)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("TRANSLATE_PIPELINE", "PINELINE_TRANSLATE_PIPELINE", "ASR_USE_DEMUCS", "PINELINE_FLAG_ASR_USE_DEMUCS"):
        monkeypatch.delenv(k, raising=False)
    import core.config as config
    orig = config.get
    monkeypatch.setattr(config, "get", lambda k, d=None: (os.environ.get(k) or d) if k == "TRANSLATE_PIPELINE"
                        else orig(k, d))


# ───────────── Stage + Gate ─────────────

def test_stage_gate_done_pass_clears_park(job):
    js.mutate(job, lambda d: d.setdefault("agent", {}).update(
        held_at="waiting_review", simple_park=True, review_retries=2, hold_reason="soát dở (simple) — x"))
    calls = []
    steps = simple_translate_steps(runner=lambda wd, tg, **k: calls.append(k) or {"status": "done", "gate": {}})
    rr = Runner(steps).run(_ctx(job))
    assert rr.status == "done" and calls and set(calls[0]) == {"force", "rereview", "stop_check"}
    ag = _man(job).get("agent") or {}
    assert not {"held_at", "simple_park", "review_retries", "hold_reason"} & set(ag)


def test_stage_gate_park_then_review_failed(job):
    res = {"status": "park", "gate": {"reasons": ["còn 2 câu chưa soát"]}}
    steps = simple_translate_steps(runner=lambda *a, **k: res, retry_sec=60, max_tries=2)
    t0 = time.time()
    for n in (1, 2):
        rr = Runner(steps).run(_ctx(job))
        assert rr.status == "park" and rr.keeps_seat and rr.at == "translate_simple_gate"
        ag = _man(job)["agent"]
        assert ag["held_at"] == "waiting_review" and ag["simple_park"] is True and ag["review_retries"] == n
        assert ag["review_retry_at"] >= t0 + 59
        # unrev=0 nhưng simple_park → chưa tới hạn thì KHÔNG chạy lại (không lặp nóng)
        assert js.park_ready(_man(job), 0, now=t0) is False
        assert js.park_ready(_man(job), 0, now=ag["review_retry_at"] + 1) is True
    rr = Runner(steps).run(_ctx(job))
    ag = _man(job)["agent"]
    assert rr.status == "hold" and ag["held_at"] == "review_failed" and ag["hold_after_translate"] is True


def test_stage_gate_hold_and_qa_override(job):
    res = {"status": "hold", "gate": {"reasons": ["còn 9 câu bị cờ"]}}
    rr = Runner(simple_translate_steps(runner=lambda *a, **k: res)).run(_ctx(job))
    ag = _man(job)["agent"]
    assert rr.status == "hold" and ag["held_at"] == "simple_gate" and ag["hold_after_translate"] is True
    js.set_agent(job, qa_override=True)
    rr = Runner(simple_translate_steps(runner=lambda *a, **k: res)).run(_ctx(job))
    ag = _man(job)["agent"]
    assert rr.status == "done" and ag.get("held_at") != "simple_gate"


def test_stage_transient_error_parks_not_fail(job):
    """Lỗi tạm (cloud limit / timeout / mất kết nối) → park: hàng chờ tự thử lại (B2)."""
    for exc in (RuntimeError("429 rate limit"), TimeoutError("read timed out"), ConnectionError("reset")):
        def boom(*a, _e=exc, **k):
            raise _e
        rr = Runner(simple_translate_steps(runner=boom)).run(_ctx(job))
        assert rr.status == "park", exc
        assert "lỗi tạm" in _man(job)["agent"]["hold_reason"]
        assert _man(job)["stages"].get("translate", {}).get("status") != "failed"


def test_stage_code_error_fails_at_once_with_traceback(job):
    """R1.3: lỗi không phải cloud (bug code) → KHÔNG park / đếm lượt thử lại; stage translate = failed ngay,
    traceback đủ nằm trong <job>/translate_error.log."""
    def bug(*a, **k):
        raise KeyError("truong_khong_ton_tai")
    rr = Runner(simple_translate_steps(runner=bug)).run(_ctx(job))
    m = _man(job)
    assert rr.status == "fail" and "KeyError" in rr.detail
    assert m["stages"]["translate"]["status"] == "failed" and "KeyError" in m["stages"]["translate"]["error"]
    ag = m.get("agent") or {}
    assert "review_retries" not in ag and ag.get("held_at") != "waiting_review"
    log = (job / "translate_error.log").read_text(encoding="utf-8")
    assert "Traceback" in log and "truong_khong_ton_tai" in log


def test_stage_stop_is_stopped_not_park(job):
    def stop(*a, **k):
        raise RuntimeError("stopped by user")
    rr = Runner(simple_translate_steps(runner=stop)).run(_ctx(job))
    assert rr.status == "stopped" and "held_at" not in (_man(job).get("agent") or {})
    seen = []
    rr = Runner(simple_translate_steps(runner=lambda *a, stop_check, **k: seen.append(stop_check()) or
                                       {"status": "done"})).run(_ctx(job))
    assert seen == [False]


def test_stage_skip_if_done_reads_manifest_gate(job):
    js.set_stage(job, "translate", status="done", mode="simple", simple={"status": "done", "reasons": []})
    calls = []
    st = TranslateSimple(lambda *a, **k: calls.append(1))
    assert st.done(_ctx(job)) is False                 # mặc định luôn chạy (pipeline.run tự resume)
    assert st.done(_ctx(job, skip_if_done=True)) is True
    assert st.done(_ctx(job, skip_if_done=True, force=True)) is False
    assert SimpleTranslateGate().check(_ctx(job)).kind == "pass"


def test_engine_writes_manifest_via_state(job, events):
    """engines.translate_simple ghi stages.translate qua state.job_state.mutate → JobIndex + event."""
    from engines.translate_simple import pipeline as SP
    SP._set_stage(job, cur_text="simple · thử", mode="simple")
    assert _man(job)["stages"]["translate"]["cur_text"] == "simple · thử"
    assert any(t == "job.updated" for t, _ in events)


# ───────────── cờ / cấu hình ─────────────

def test_translate_pipeline_default_simple_and_rollback(monkeypatch):
    from engines.translate_simple import cfg
    assert cfg.translate_pipeline() == "simple" and cfg.pipeline_is_simple()
    monkeypatch.setenv("PINELINE_TRANSLATE_PIPELINE", "legacy")         # instance
    assert cfg.translate_pipeline() == "legacy" and not cfg.pipeline_is_simple()
    monkeypatch.setenv("TRANSLATE_PIPELINE", "simple")                  # env đặt tay thắng instance
    assert cfg.pipeline_is_simple()
    monkeypatch.setenv("TRANSLATE_PIPELINE", "lạ")
    assert cfg.pipeline_is_simple()


def test_instance_translate_pipeline_and_demucs_flag(tmp_path, monkeypatch):
    from core.instance import load
    for name in ("v6", "test", "prod"):
        raw = json.loads((ROOT / "instances" / f"{name}.json").read_text(encoding="utf-8"))
        assert raw["translate_pipeline"] == "simple" and raw["asr_use_demucs"] is True, name
        inst = load(name)
        assert inst.translate_pipeline == "simple" and inst.flags()["asr_use_demucs"] is True
    (tmp_path / "rb.json").write_text(json.dumps({"port": 9101, "work_root": str(tmp_path / "w"),
                                                  "temp": str(tmp_path / "t"), "translate_pipeline": "legacy",
                                                  "asr_use_demucs": False}), encoding="utf-8")
    inst = load("rb", directory=tmp_path)
    keep = dict(os.environ)
    try:
        inst.apply_env()
        assert os.environ["PINELINE_TRANSLATE_PIPELINE"] == "legacy"
        assert os.environ["PINELINE_FLAG_ASR_USE_DEMUCS"] == "0"
        from engines.asr import asr
        from engines.translate_simple import cfg
        assert asr.asr_use_demucs() is False and cfg.pipeline_is_simple() is False
        os.environ["ASR_USE_DEMUCS"] = "1"                               # env đặt tay thắng instance
        assert asr.asr_use_demucs() is True
    finally:
        os.environ.clear()
        os.environ.update(keep)


def test_asr_use_demucs_default_on(monkeypatch):
    from engines.asr import asr
    assert asr.asr_use_demucs() is True
    monkeypatch.setenv("ASR_USE_DEMUCS", "0")
    assert asr.asr_use_demucs() is False


# ───────────── vị trí + luật V6 ─────────────

def test_lexicon_data_in_v6_data_dir():
    from engines.translate_simple import lexicon, pair_rules
    assert lexicon.LEXICON_PATH == ROOT / "data" / "lexicon" / "idioms_zh_vi.json" and lexicon.LEXICON_PATH.is_file()
    assert pair_rules.RULES_PATH == ROOT / "data" / "lexicon" / "pronoun_rules_zh_vi.json" and pair_rules.RULES_PATH.is_file()
    assert lexicon.load()


def test_ported_code_follows_v6_rules():
    files = [*sorted((ROOT / "engines" / "translate_simple").glob("*.py")), ROOT / "engines" / "cloud_retry.py",
             ROOT / "engines" / "separate.py", ROOT / "engines" / "asr" / "asr.py",
             *sorted((ROOT / "pipeline").rglob("*.py"))]
    for p in files:
        src = p.read_text(encoding="utf-8")
        code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
        assert "Manifest(work_dir).update" not in code and not re.search(r"\b\w+\.save\(\)", code), p
        assert "import translate_simple" not in src and "from translate_simple" not in src, p
        for flat in ("from manifest import", "from fileio import", "from config import", "import translate as"):
            assert flat not in src, (p, flat)
    for p in (ROOT / "pipeline").rglob("*.py"):
        assert "Manifest(" not in p.read_text(encoding="utf-8"), p


def test_server_branches_present():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "def _auto_translate_simple" in src and "simple_translate_steps" in src
    # P6 11-12 (V2 F2): luồng THEO JOB — _process_gpu, _auto_gpu, _run_stage, _translate_flow, translate_fill,
    # _run_requested_review, retranslate_job đều hỏi _translate_pipeline_simple(<work_dir>…)
    assert len(re.findall(r"_translate_pipeline_simple\((?:work_dir|wd)\b", src)) >= 6
    assert 'agent.get("simple_park")' in src                          # _queue_park_ready
