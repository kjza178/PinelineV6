"""2026-11-20: (1) tab Lời thoại hiện lời gốc ASR khi chưa dịch; (2) dịch có model dự phòng; (3) Agent «local» gọi đúng
llama-server ở Cài đặt → AI trên máy và báo lỗi cả model chính lẫn dự phòng."""
from __future__ import annotations

import json
import types

import pytest


# ── (3) Agent ──
def test_agent_local_uses_llama_base_and_reports_all_errors(monkeypatch):
    from agents import agent_core
    import core.config as cfg
    from core import machine
    vals = {"AGENT_MODEL": "local", "AGENT_FALLBACK_MODEL": "chatgpt/gpt-5-6-mini",
            "LLM_BASE_URL": "http://127.0.0.1:20128/v1"}
    monkeypatch.setattr(cfg, "get", lambda k, d=None: vals.get(k, d))
    monkeypatch.setattr(machine, "llama_base_url", lambda: "http://127.0.0.1:18082/v1")
    urls = []

    def post(url, **kw):
        urls.append(url)
        raise ConnectionError("llama tắt")
    monkeypatch.setattr(agent_core.requests, "post", post)
    import engines.llm as llm

    def chat(*a, **k):
        raise ConnectionError("Aurora/ChatGPT thất bại: 127.0.0.1:3377")
    monkeypatch.setattr(llm, "chat", chat)
    with pytest.raises(RuntimeError) as ei:
        agent_core._chat_turn([{"role": "user", "content": "hi"}])
    assert urls == ["http://127.0.0.1:18082/v1/chat/completions"]       # llama ở Cài đặt, không phải LLM_BASE_URL
    msg = str(ei.value)
    assert msg.startswith("local: ") and " | chatgpt/gpt-5-6-mini: " in msg and "llama tắt" in msg


def test_agent_local_ignores_old_agent_base_url(monkeypatch):
    """2026-11-21 (đổi luật theo yêu cầu chủ dự án): chỉ 3 loại AI — AGENT_BASE_URL cũ (vd :3377 = cổng Aurora) KHÔNG
    còn được dùng cho «local»; «local» luôn tới llama-server ở Cài đặt → AI trên máy."""
    from agents import agent_core
    from core import machine
    import core.config as cfg
    vals = {"AGENT_MODEL": "local", "AGENT_BASE_URL": "http://127.0.0.1:3377/v1",
            "LLM_BASE_URL": "http://192.168.1.50:20128/v1"}
    monkeypatch.setattr(cfg, "get", lambda k, d=None: vals.get(k, d))
    monkeypatch.setattr(machine, "llama_base_url", lambda: "http://127.0.0.1:9999/v1")
    urls = []

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "ok"}}]}
    monkeypatch.setattr(agent_core.requests, "post", lambda url, **kw: urls.append(url) or R())
    assert agent_core._chat_turn([{"role": "user", "content": "hi"}])["content"] == "ok"
    assert urls == ["http://127.0.0.1:9999/v1/chat/completions"]


def test_agent_cloud_router_model_rejected(monkeypatch):
    from agents import agent_core
    import core.config as cfg
    monkeypatch.setattr(cfg, "get", lambda k, d=None: {"AGENT_MODEL": "ollama/gemma4:31b-cloud"}.get(k, d))
    monkeypatch.setattr(agent_core.requests, "post", lambda *a, **k: pytest.fail("không được gọi mạng"))
    with pytest.raises(RuntimeError, match="9router"):
        agent_core._chat_turn([{"role": "user", "content": "hi"}])


def test_llm_settings_never_use_lan_router(monkeypatch):
    import core.config as cfg
    from core import machine
    monkeypatch.setattr(machine, "llama_base_url", lambda: "http://127.0.0.1:8083/v1")
    monkeypatch.setattr(cfg, "get", lambda k, d=None: {"LLM_BASE_URL": "http://192.168.1.50:20128/v1",
                                                       "AGENT_BASE_URL": "http://127.0.0.1:3377/v1",
                                                       "LLM_MODEL": "ollama/gemma4:31b-cloud",
                                                       "ALLOW_LAN_LLM": "1"}.get(k, d))
    assert cfg.llm_settings()["base"] == "http://127.0.0.1:8083/v1"
    assert cfg.agent_llm()["base"] == "http://127.0.0.1:8083/v1"
    assert cfg.llm_settings()["model"] == "local"


# ── (2) Dịch dự phòng ──
def test_call_llm_falls_back_on_provider_failure(monkeypatch):
    from engines.translate_simple import cfg, stage_review
    import engines.cloud_retry as cr
    import engines.llm as llm
    monkeypatch.setattr(cfg, "get", lambda k, d=None: {"SIMPLE_FALLBACK_MODEL": "alibaba/qwen-plus"}.get(k, d))
    monkeypatch.setattr(cr, "call_with_cloud_retry", lambda fn, **kw: fn())          # bỏ chờ thử lại
    used = []

    def chat(msgs, model=None, **kw):
        used.append(model)
        if model.startswith("deepseek"):
            raise ConnectionError("DeepSeek 402 hết tiền")
        return json.dumps({"lines": [{"i": 1, "vi": "xin chào"}]})
    monkeypatch.setattr(llm, "chat", chat)
    out = stage_review.call_llm("sys", "user", {1}, model="deepseek/deepseek-v4-flash")
    assert out[1]["vi"] == "xin chào" and used == ["deepseek/deepseek-v4-flash", "alibaba/qwen-plus"]


def test_call_llm_no_fallback_for_bad_json_or_stop(monkeypatch):
    from engines.translate_simple import cfg, stage_review
    import engines.cloud_retry as cr
    import engines.llm as llm
    monkeypatch.setattr(cfg, "get", lambda k, d=None: {"SIMPLE_FALLBACK_MODEL": "alibaba/qwen-plus"}.get(k, d))
    monkeypatch.setattr(cr, "call_with_cloud_retry", lambda fn, **kw: fn())
    used = []
    monkeypatch.setattr(llm, "chat", lambda msgs, model=None, **kw: used.append(model) or "{không phải json")
    with pytest.raises(stage_review.BadOutput):
        stage_review.call_llm("s", "u", {1}, model="deepseek/x")
    assert used == ["deepseek/x"]                        # lô hỏng JSON: luồng soát tự thử lại, không đổi model

    def stopped(*a, **k):
        raise RuntimeError("stopped by user")
    monkeypatch.setattr(llm, "chat", stopped)
    with pytest.raises(RuntimeError, match="stopped by user"):
        stage_review.call_llm("s", "u", {1}, model="deepseek/x")


def test_fallback_model_listed_in_ai_settings():
    from core import ai_models
    f = ai_models.BY_ID["review_fallback"]
    assert f.key == "SIMPLE_FALLBACK_MODEL" and f.group == "Luồng dịch V6" and not f.legacy


# ── (4) Danh sách job + hàng chờ ──
def test_summary_has_group_and_failure_fields():
    from state.index import summarize
    s = summarize("j1", {"title": "T", "request": {"group_title": "Bộ A", "group_index": 2, "group_total": 10},
                         "stages": {"download": {"status": "done"}, "asr": {"status": "failed", "error": "kaldiio thiếu"}}})
    assert (s["group_title"], s["group_index"], s["group_total"]) == ("Bộ A", 2, 10)
    assert s["status"] == "failed" and s["failed_stage"] == "asr" and s["error"] == "kaldiio thiếu"


def test_desktop_jobs_view_has_queue_tools():
    from pathlib import Path
    ui = Path(__file__).resolve().parents[1] / "desktop" / "ui"
    html, js = (ui / "index.html").read_text(encoding="utf-8"), (ui / "app.js").read_text(encoding="utf-8")
    for el in ('id="job-filter"', 'id="qpanel"', 'id="btn-retry-failed"', 'id="btn-del-sel"', 'id="btn-q-panel"'):
        assert el in html, el
    for n in ("/api/queue/jobs/move", "/api/queue/jobs/sort", "/api/queue/jobs/remove", "jobMatch(j, jobFilter)"):
        assert n in js, n


# ── Port V5 2026-11-20 ──
def test_plan_uses_backup_model_when_main_fails(monkeypatch):
    import engines.translate as T
    monkeypatch.setattr(T, "plan_mode_effective", lambda: "sample")
    monkeypatch.setattr(T, "_cfg_get", lambda k, d=None: {"SIMPLE_FALLBACK_MODEL": "alibaba/qwen-plus"}.get(k, d))
    used = []

    def sample(segments, target, model, name_hints, series_plan):
        used.append(model)
        if model == "deepseek/x":
            raise ConnectionError("DeepSeek hết tiền")
        return {"characters": [{"zh": "林", "vi": "Lâm"}], "terms": []}
    monkeypatch.setattr(T, "_build_plan_sample", sample)
    data = T.build_plan([{"i": 0, "text": "你好"}], model="deepseek/x")
    assert used == ["deepseek/x", "alibaba/qwen-plus"] and data["characters"][0]["vi"] == "Lâm"
    assert (data.get("_meta") or {}).get("plan_model_role") == "backup"


def test_agent_resolves_short_job_id(tmp_path):
    from agents import agent_core
    for n in ("9aa0891fbfd1_p01", "9aa0891fbfd1_p02", "abc12345ffff"):
        (tmp_path / n).mkdir()
        (tmp_path / n / "manifest.json").write_text("{}", encoding="utf-8")
    assert agent_core.resolve_job_id(tmp_path, "9aa089_p01") == "9aa0891fbfd1_p01"
    assert agent_core.resolve_job_id(tmp_path, "9aa089_p2") == "9aa0891fbfd1_p02"
    assert agent_core.resolve_job_id(tmp_path, "abc1") == "abc12345ffff"
    with pytest.raises(FileNotFoundError, match="nhiều job khớp"):
        agent_core.resolve_job_id(tmp_path, "9aa0")
    with pytest.raises(FileNotFoundError):
        agent_core.resolve_job_id(tmp_path, "zzzz")
