"""Cài đặt AI (core/ai_models.py): chức năng nào dùng model nào, ghi cấu hình, kiểm tra hợp lệ.
Dùng pineline.config.json TẠM (không đụng file thật của máy)."""
from __future__ import annotations

import json

import pytest

import core.config as cfg
from core import ai_models

MODEL_KEYS = ("SIMPLE_MODEL", "REVIEW_MODEL", "TRANSLATE_MODEL", "LLM_MODEL", "PLAN_MODEL", "PLAN_MODE", "NAME_MODEL",
              "PRONOUN_MODEL", "CHATGPT_MODEL", "AGENT_MODEL", "AGENT_FALLBACK_MODEL", "HANG_FIX_MODEL",
              "SIMPLE_PAIR_FILL_MODEL", "PRONOUN_ANALYZE_MODEL", "PRONOUN_ANALYZE_MODE", "SIMPLE_DRAFT", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
              "DASHSCOPE_API_KEY", "ALIBABA_API_KEY", "ALIBABA_BASE_URL", "DEEPSEEK_MAX_TOKENS", "ALIBABA_MAX_TOKENS",
              "DEEPSEEK_CONCURRENCY", "ALIBABA_CONCURRENCY")


@pytest.fixture
def conf(tmp_path, monkeypatch):
    f = tmp_path / "pineline.config.json"
    f.write_text(json.dumps({"OTHER_KEY": "giữ nguyên", "REVIEW_MODEL": "chatgpt/gpt-6"}), encoding="utf-8")
    monkeypatch.setattr(cfg, "_cfg_file", f)
    monkeypatch.setattr(cfg, "_CFG", {})
    monkeypatch.setattr(cfg, "_cfg_mtime", 0.0)
    for k in MODEL_KEYS:
        monkeypatch.delenv(k, raising=False)
    cfg._load_cfg(force=True)
    return f


def _file(f) -> dict:
    return json.loads(f.read_text(encoding="utf-8"))


def _row(snap, fid):
    return next(x for x in snap["functions"] if x["id"] == fid)


def test_snapshot_reports_real_effective_model_and_source(conf):
    s = ai_models.snapshot()
    ids = {f["id"] for f in s["functions"]}
    assert {"review", "plan", "name", "pronoun", "agent", "meta", "thumb"} <= ids
    r = _row(s, "review")
    assert r["value"] == "" and r["effective"] == "chatgpt/gpt-6" and r["source"].startswith("dự phòng")
    assert r["provider"] == "Aurora"
    assert _row(s, "meta")["fixed"] and _row(s, "thumb")["needs"] == "aurora_image"


def test_apply_writes_keys_and_effective_follows(conf):
    out = ai_models.apply({"review": "deepseek/deepseek-flash", "agent": "alibaba/qwen-plus"}, plan_mode="sample")
    assert set(out["changed"]) == {"SIMPLE_MODEL", "AGENT_MODEL", "PLAN_MODE"}
    d = _file(conf)
    assert d["SIMPLE_MODEL"] == "deepseek/deepseek-flash" and d["AGENT_MODEL"] == "alibaba/qwen-plus"
    assert d["OTHER_KEY"] == "giữ nguyên" and d["REVIEW_MODEL"] == "chatgpt/gpt-6"     # khoá khác không mất
    # code chạy job đọc đúng giá trị mới (cùng hàm mà job dùng)
    from engines.translate_simple import cfg as scfg
    from core.config import agent_llm
    assert scfg.review_model(None) == "deepseek/deepseek-flash"
    assert agent_llm()["model"] == "alibaba/qwen-plus"
    r = _row(out, "review")
    assert r["provider"] == "DeepSeek" and r["source"] == "SIMPLE_MODEL"


def test_empty_value_removes_key_and_falls_back(conf):
    ai_models.apply({"review": "deepseek/deepseek-flash"})
    ai_models.apply({"review": ""})
    assert "SIMPLE_MODEL" not in _file(conf)
    assert _row(ai_models.snapshot(), "review")["effective"] == "chatgpt/gpt-6"


def test_invalid_choices_are_rejected_and_nothing_is_written(conf):
    before = conf.read_text(encoding="utf-8")
    for models in ({"review": "ollama/gemma4:31b-cloud"},            # 9router: V6 bỏ
                   {"pronoun_analyze": "deepseek/deepseek-flash"},     # cần upload file → chỉ Aurora
                   {"meta": "deepseek/deepseek-flash"},                # cố định trong code
                   {"khong_co": "local"}):
        with pytest.raises(ValueError):
            ai_models.apply(models)
    with pytest.raises(ValueError):
        ai_models.apply(None, plan_mode="tuy_y")
    with pytest.raises(ValueError):
        ai_models.apply(None, providers_cfg={"deepseek": {"base_url": "ftp://x"}})
    assert conf.read_text(encoding="utf-8") == before


def test_provider_key_is_stored_but_never_returned_in_full(conf):
    out = ai_models.apply(None, providers_cfg={"deepseek": {"api_key": "sk-abcdef123456WXYZ"}})
    assert _file(conf)["DEEPSEEK_API_KEY"] == "sk-abcdef123456WXYZ"
    p = next(x for x in out["providers"] if x["name"] == "deepseek")
    assert p["has_key"] and p["key_hint"] == "****WXYZ"
    assert "sk-abcdef123456WXYZ" not in json.dumps(out)
    # để trống key = giữ nguyên; clear_key = xoá
    ai_models.apply(None, providers_cfg={"deepseek": {"api_key": ""}})
    assert _file(conf)["DEEPSEEK_API_KEY"] == "sk-abcdef123456WXYZ"
    ai_models.apply(None, providers_cfg={"deepseek": {"clear_key": True}})
    assert "DEEPSEEK_API_KEY" not in _file(conf)


def test_preset_deepseek_sets_every_editable_function(conf, monkeypatch):
    """Bộ cài sẵn KHÔNG ghi cứng tên model: hỏi GET /models rồi chọn bản «flash»."""
    from engines import providers
    seen = []
    monkeypatch.setattr(providers, "list_models",
                        lambda p, **k: seen.append(p.name) or ["deepseek-v4-pro", "deepseek-flash", "deepseek-reasoner"])
    out = ai_models.apply(preset="deepseek")
    assert seen == ["deepseek"]
    d = _file(conf)
    for k in ("SIMPLE_MODEL", "PLAN_MODEL", "NAME_MODEL", "PRONOUN_MODEL", "AGENT_MODEL", "HANG_FIX_MODEL", "TRANSLATE_MODEL"):
        assert d[k] == "deepseek/deepseek-flash", k
    assert d["PLAN_MODE"] == "sample"
    for f in out["functions"]:
        if f["id"] in ("review", "plan", "name", "pronoun", "agent", "hang_fix", "translate"):
            assert f["effective"] == "deepseek/deepseek-flash", f["id"]


def test_plan_uses_plan_model_in_sample_mode(conf, monkeypatch):
    """fix 2026-11-15: chế độ sample trước đây bỏ qua PLAN_MODEL (dùng LLM_MODEL)."""
    import engines.translate as T
    ai_models.apply({"plan": "deepseek/deepseek-flash"}, plan_mode="file")
    assert T.plan_model() == "deepseek/deepseek-flash"
    assert T.plan_mode_effective() == "sample"            # model không phải Aurora → không thử upload file
    seen = []
    monkeypatch.setattr(T, "chat", lambda msgs, model=None, **k: seen.append(model) or '{"characters":[{"zh":"林","vi":"Lâm"}]}')
    T.build_plan([{"i": 0, "start": 0, "end": 1, "text": "你好"}], work_dir=None)
    assert seen and seen[0] == "deepseek/deepseek-flash"


def test_save_config_fixes_legacy_settings_page(conf):
    """POST /api/settings (trang cài đặt cũ) gọi core.config.save_config — trước đây hàm không tồn tại."""
    out = cfg.save_config({"TRANSLATE_MODEL": "local", "OTHER_KEY": None})
    assert out["TRANSLATE_MODEL"] == "local" and "OTHER_KEY" not in out
    assert cfg.get("TRANSLATE_MODEL") == "local"


def test_test_model_reports_error_without_raising(conf, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    r = ai_models.test_model("deepseek/deepseek-flash", timeout=2)
    assert r["ok"] is False and r["error"] and r["provider"] == "DeepSeek"
    assert ai_models.test_model("")["ok"] is False


def test_preset_without_key_fails_clearly_and_writes_nothing(conf):
    before = conf.read_text(encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        ai_models.apply(preset="alibaba")
    assert "chưa có API key" in str(ei.value)
    assert conf.read_text(encoding="utf-8") == before


def test_fetch_models_returns_prefixed_ids_and_suggestion(conf, monkeypatch):
    from engines import providers
    got = {}
    monkeypatch.setattr(providers, "list_models",
                        lambda p, **k: got.update(k) or ["qwen-max", "qwen-flash", "qwen-vl-plus"])
    r = ai_models.fetch_models("alibaba", api_key="sk-chua-luu")
    assert r["models"] == ["alibaba/qwen-max", "alibaba/qwen-flash", "alibaba/qwen-vl-plus"]
    assert r["suggested"] == "alibaba/qwen-flash" and got["api_key"] == "sk-chua-luu"
    assert "DASHSCOPE_API_KEY" not in _file(conf)                    # key gửi kèm để hỏi KHÔNG bị lưu
    with pytest.raises(ValueError):
        ai_models.fetch_models("khong_co")
    with pytest.raises(ValueError):
        ai_models.fetch_models("deepseek", base_url="ftp://x")


def test_model_without_name_after_prefix_is_rejected(conf):
    with pytest.raises(ValueError) as ei:
        ai_models.apply({"review": "deepseek/"})
    assert "thiếu tên model" in str(ei.value)


def test_pronoun_analyze_needs_aurora_only_in_file_mode(conf):
    """fix 2026-11-15: chế độ full / fast quét theo lô bằng llm.chat → DeepSeek dùng được."""
    with pytest.raises(ValueError):
        ai_models.apply({"pronoun_analyze": "deepseek/deepseek-flash"})          # mặc định file → chỉ Aurora
    cfg.save_config({"PRONOUN_ANALYZE_MODE": "full"})
    ai_models.apply({"pronoun_analyze": "deepseek/deepseek-flash"})
    assert _file(conf)["PRONOUN_ANALYZE_MODEL"] == "deepseek/deepseek-flash"
    assert _row(ai_models.snapshot(), "pronoun_analyze")["needs"] == ""



def test_provider_output_cap_saved_validated_and_reset(conf):
    out = ai_models.apply(None, providers_cfg={"deepseek": {"max_tokens": "32768"}})
    assert _file(conf)["DEEPSEEK_MAX_TOKENS"] == "32768"
    assert next(p for p in out["providers"] if p["name"] == "deepseek")["max_tokens"] == 32768
    for bad in ("abc", "100", "9999999"):
        with pytest.raises(ValueError):
            ai_models.apply(None, providers_cfg={"deepseek": {"max_tokens": bad}})
    ai_models.apply(None, providers_cfg={"deepseek": {"max_tokens": ""}})            # trống = về mặc định
    assert "DEEPSEEK_MAX_TOKENS" not in _file(conf)


def test_provider_concurrency_saved_validated_and_reset(conf):
    out = ai_models.apply(None, providers_cfg={"deepseek": {"concurrency": "100"}})
    assert _file(conf)["DEEPSEEK_CONCURRENCY"] == "100"
    assert next(p for p in out["providers"] if p["name"] == "deepseek")["concurrency"] == 100
    for bad in ("0", "abc", "999"):
        with pytest.raises(ValueError):
            ai_models.apply(None, providers_cfg={"deepseek": {"concurrency": bad}})
    ai_models.apply(None, providers_cfg={"deepseek": {"concurrency": ""}})
    assert "DEEPSEEK_CONCURRENCY" not in _file(conf)
    assert next(p for p in ai_models.snapshot()["providers"] if p["name"] == "deepseek")["concurrency"] == 32


def test_preset_recommended_mixes_deepseek_aurora_local(conf, monkeypatch):
    from engines import providers
    monkeypatch.setattr(providers, "list_models", lambda p, **k: ["deepseek-v4-pro", "deepseek-flash"])
    ai_models.apply(preset="recommended")
    d = _file(conf)
    for k in ("SIMPLE_MODEL", "AGENT_MODEL"):
        assert d[k] == "deepseek/deepseek-flash", k
    assert "NAME_MODEL" not in d and "PRONOUN_MODEL" not in d       # pass V2 — luồng V6 không dùng
    assert d["PLAN_MODEL"] == "chatgpt/gpt-5-6-mini" and d["PLAN_MODE"] == "file"
    assert d["AGENT_FALLBACK_MODEL"] == "chatgpt/gpt-5-6-mini"
    assert d["TRANSLATE_MODEL"] == "local" and d["HANG_FIX_MODEL"] == "local"
    assert "SIMPLE_PAIR_FILL_MODEL" not in d


def test_plan_sample_fallback_uses_review_model_not_small_translate_model(conf, monkeypatch):
    """fix 2026-11-15: Aurora (plan) lỗi → dự phòng SIMPLE_MODEL (DeepSeek), không phải TRANSLATE_MODEL=local."""
    import engines.translate as T
    cfg.save_config({"PLAN_MODEL": "chatgpt/gpt-5-6-mini", "PLAN_MODE": "sample",
                     "SIMPLE_MODEL": "deepseek/deepseek-flash", "TRANSLATE_MODEL": "local"})
    seen = []

    def chat(msgs, model=None, **k):
        seen.append(model)
        if model.startswith("chatgpt/"):
            raise RuntimeError("Aurora 503")
        return '{"characters":[{"zh":"林","vi":"Lâm"}]}'
    monkeypatch.setattr(T, "chat", chat)
    data = T.build_plan([{"i": 0, "start": 0, "end": 1, "text": "你好"}], work_dir=None)
    assert seen == ["chatgpt/gpt-5-6-mini", "deepseek/deepseek-flash"] and data["characters"]


def test_functions_not_used_by_v6_are_marked_legacy(conf):
    rows = {f["id"]: f for f in ai_models.snapshot()["functions"]}
    assert {i for i, f in rows.items() if f["legacy"]} == {"name", "pronoun", "pair_fill", "legacy_review",
                                                           "pronoun_analyze"}
    assert not rows["review"]["legacy"] and not rows["plan"]["legacy"] and not rows["agent"]["legacy"]
