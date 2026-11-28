"""Nhà cung cấp AI tùy chỉnh (engines/providers/custom.py + core/ai_models.save/remove_custom_provider, 2026-11-28).
Dùng pineline.config.json TẠM; mạng giả (requests.post / get bị thay)."""
from __future__ import annotations

import json

import pytest

import core.config as cfg
from core import ai_models
from engines import providers
from engines.providers import _openai, custom


@pytest.fixture
def conf(tmp_path, monkeypatch):
    f = tmp_path / "pineline.config.json"
    f.write_text(json.dumps({"OTHER_KEY": "giữ"}), encoding="utf-8")
    monkeypatch.setattr(cfg, "_cfg_file", f)
    monkeypatch.setattr(cfg, "_CFG", {})
    monkeypatch.setattr(cfg, "_cfg_mtime", 0.0)
    # save_config ghi đè cả os.environ nếu khoá đã có ở env → xoá trước (monkeypatch trả lại sau test, không rò)
    keys = {f.key for f in ai_models.FUNCTIONS if f.key} | {f.enabled_key for f in ai_models.FUNCTIONS if f.enabled_key}
    keys |= {"CUSTOM_PROVIDERS", "CUSTOM_OPENROUTER_API_KEY", "CUSTOM_OPENROUTER_BASE_URL", "CUSTOM_OR2_API_KEY",
             "PLAN_MODE"}
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    cfg._load_cfg(force=True)
    return f


def _file(f) -> dict:
    return json.loads(f.read_text(encoding="utf-8"))


class _Resp:
    def __init__(self, code, data=None, text=""):
        self.status_code, self._d, self.text, self.headers = code, data, text or json.dumps(data or {}), {}

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def _add(**kw):
    spec = {"name": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1/"}
    spec.update(kw)
    return ai_models.save_custom_provider(spec, api_key="sk-or-test-123456")


def test_add_custom_provider_resolves_and_hides_key(conf):
    out = _add()
    d = _file(conf)
    assert json.loads(d["CUSTOM_PROVIDERS"])[0]["base_url"] == "https://openrouter.ai/api/v1"
    assert d["CUSTOM_OPENROUTER_API_KEY"] == "sk-or-test-123456" and d["OTHER_KEY"] == "giữ"
    p, bare = providers.resolve("openrouter/meta-llama/llama-3.3-70b")
    assert p.custom and p.label == "OpenRouter" and bare == "meta-llama/llama-3.3-70b"
    row = next(x for x in out["providers"] if x["name"] == "openrouter")
    assert row["custom"] and row["has_key"] and "sk-or-test" not in json.dumps(out)
    assert row["concurrency_key"] == "CUSTOM_OPENROUTER_CONCURRENCY"
    assert any(x["id"] == "custom:openrouter" for x in out["presets"])
    # dùng được cho chức năng dịch (tên model có «/»)
    ai_models.apply({"review": "openrouter/meta-llama/llama-3.3-70b"})
    assert _file(conf)["SIMPLE_MODEL"] == "openrouter/meta-llama/llama-3.3-70b"
    assert ai_models.provider_of("openrouter/x") == "OpenRouter"


@pytest.mark.parametrize("spec,msg", [
    ({"name": "deepseek", "base_url": "https://x/v1"}, "đã dùng"),
    ({"name": "Open Router", "base_url": "https://x/v1"}, "tên ngắn"),
    ({"name": "abc", "base_url": "ftp://x"}, "http"),
])
def test_invalid_custom_provider_rejected_nothing_written(conf, spec, msg):
    with pytest.raises(ValueError, match=msg):
        ai_models.save_custom_provider(spec)
    assert "CUSTOM_PROVIDERS" not in _file(conf)


def test_unknown_prefix_model_rejected(conf):
    with pytest.raises(ValueError, match="không có nhà cung cấp «groq»"):
        ai_models.apply({"review": "groq/llama-3"})


def test_remove_refused_while_used_then_clears_keys(conf):
    _add()
    ai_models.apply({"agent": "openrouter/x-model"})
    with pytest.raises(ValueError, match="Trợ lý|đang có chức năng"):
        ai_models.remove_custom_provider("openrouter")
    ai_models.apply({"agent": ""})
    ai_models.remove_custom_provider("openrouter")
    d = _file(conf)
    assert "CUSTOM_PROVIDERS" not in d and "CUSTOM_OPENROUTER_API_KEY" not in d
    assert providers.resolve("openrouter/x") is None


def test_rename_moves_key(conf):
    _add()
    ai_models.save_custom_provider({"name": "or2", "label": "OR", "base_url": "https://openrouter.ai/api/v1"},
                                   editing="openrouter")
    d = _file(conf)
    assert d["CUSTOM_OR2_API_KEY"] == "sk-or-test-123456" and "CUSTOM_OPENROUTER_API_KEY" not in d


def test_chat_and_models_go_to_custom_base(conf, monkeypatch):
    _add()
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, auth=headers["Authorization"], model=json["model"])
        return _Resp(200, {"choices": [{"message": {"content": "OK"}}]})
    monkeypatch.setattr(_openai.requests, "post", post)
    monkeypatch.setattr(_openai.requests, "get", lambda url, headers=None, timeout=None:
                        _Resp(200, {"data": [{"id": "models/gemini-flash"}, {"id": "openai/gpt-mini"}]}))
    from engines.llm import chat
    assert chat([{"role": "user", "content": "hi"}], model="openrouter/openai/gpt-mini") == "OK"
    assert seen == {"url": "https://openrouter.ai/api/v1/chat/completions", "auth": "Bearer sk-or-test-123456",
                    "model": "openai/gpt-mini"}
    r = ai_models.fetch_models("openrouter")
    assert "openrouter/gemini-flash" in r["models"]                 # Gemini trả «models/…» → bỏ tiền tố


def test_preset_with_chosen_model_skips_models_endpoint(conf, monkeypatch):
    _add()
    real_get = _openai.requests.get

    def get(url, *a, **k):
        assert "openrouter" not in url, "không được gọi /models của nhà cung cấp"
        return real_get(url, *a, **k)
    monkeypatch.setattr(_openai.requests, "get", get)
    ai_models.apply(preset="custom:openrouter", preset_model="openrouter/anthropic/claude-x")
    d = _file(conf)
    assert d["SIMPLE_MODEL"] == "openrouter/anthropic/claude-x" and d["AGENT_MODEL"] == "openrouter/anthropic/claude-x"
    with pytest.raises(ValueError, match="không thuộc"):
        ai_models.apply(preset="custom:openrouter", preset_model="deepseek/x")


@pytest.mark.parametrize("err,fixed", [
    ("Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.",
     "max_completion_tokens"),
    ("Unsupported value: 'temperature' does not support 0.3 with this model.", "temperature"),
    ("response_format json_object is not supported", "response_format"),
])
def test_third_party_400_param_autofix(conf, monkeypatch, err, fixed):
    _add()
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(dict(json))
        if len(calls) == 1:
            return _Resp(400, None, err)
        return _Resp(200, {"choices": [{"message": {"content": "{}"}}]})
    monkeypatch.setattr(_openai.requests, "post", post)
    p, bare = providers.resolve("openrouter/m")
    assert _openai.chat_text(p, bare, [{"role": "user", "content": "json"}], json_mode=True, retries=1) == "{}"
    assert len(calls) == 2
    if fixed == "max_completion_tokens":
        assert "max_tokens" not in calls[1] and calls[1]["max_completion_tokens"] == calls[0]["max_tokens"]
    else:
        assert fixed in calls[0] and fixed not in calls[1]


def test_broken_custom_json_is_ignored(conf):
    cfg.save_config({"CUSTOM_PROVIDERS": "[{không phải json"})
    assert custom.providers() == {} and providers.resolve("x/y") is None
