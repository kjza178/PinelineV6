"""Route chatgpt/aurora trong llm.chat + pass_llm_model."""
from __future__ import annotations

import core.config as config
import engines.llm as llm


def test_is_chatgpt_prefix():
    assert llm.is_chatgpt("chatgpt/gpt-5-6-mini")
    assert llm.is_chatgpt("aurora/auto")
    assert not llm.is_chatgpt("local")
    assert not llm.is_chatgpt("gpt-5-6-mini")  # thiếu tiền tố → không tự nhảy Aurora


def test_pass_llm_model_prefers_name_pronoun(monkeypatch):
    monkeypatch.setitem(config._CFG, "NAME_MODEL", "chatgpt/gpt-5-6-mini")
    monkeypatch.setitem(config._CFG, "PRONOUN_MODEL", "chatgpt/gpt-5-6")
    monkeypatch.setitem(config._CFG, "CHATGPT_MODEL", "gpt-4o")
    assert config.pass_llm_model("name") == "chatgpt/gpt-5-6-mini"
    assert config.pass_llm_model("pronoun") == "chatgpt/gpt-5-6"


def test_pass_llm_model_falls_back_chatgpt(monkeypatch):
    monkeypatch.setitem(config._CFG, "NAME_MODEL", "")
    monkeypatch.setitem(config._CFG, "PRONOUN_MODEL", "")
    monkeypatch.setitem(config._CFG, "CHATGPT_MODEL", "gpt-5-6-mini")
    assert config.pass_llm_model("name") == "chatgpt/gpt-5-6-mini"
