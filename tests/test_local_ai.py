"""Cài đặt AI trên máy / Aurora (core/local_ai.py) — trước gắn cứng hoặc chỉ đặt được bằng env. Config TẠM."""
from __future__ import annotations

import json

import pytest

import core.config as cfg
from core import local_ai

KEYS = [o.key for o in local_ai.OPTS.values()] + ["LOCAL_LLM_BASE_URL", "LLM_BASE_URL", "CHATGPT_BASE_URL",
                                                  "SIMPLE_WORKERS", "SIMPLE_MAX_TOKENS", "ALLOW_LAN_LLM"]


@pytest.fixture
def conf(tmp_path, monkeypatch):
    f = tmp_path / "pineline.config.json"
    f.write_text(json.dumps({"OTHER": "giữ"}), encoding="utf-8")
    monkeypatch.setattr(cfg, "_cfg_file", f)
    monkeypatch.setattr(cfg, "_CFG", {})
    monkeypatch.setattr(cfg, "_cfg_mtime", 0.0)
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    cfg._load_cfg(force=True)
    return f


def test_defaults_and_legacy_env(conf, monkeypatch):
    assert local_ai.value("ollama_url") == "http://127.0.0.1:11434/v1"
    assert local_ai.value("llama_url") == "http://127.0.0.1:8083/v1"
    assert local_ai.value("concurrency") == 8 and local_ai.value("thinking") is False
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://10.0.0.5:11434/v1/")
    assert local_ai.value("ollama_url") == "http://10.0.0.5:11434/v1"           # env cũ vẫn được tôn trọng


def test_apply_writes_config_and_readers_follow(conf, monkeypatch):
    import engines.llm as llm
    from engines.translate_simple import cfg as scfg
    out = local_ai.apply({"ollama_url": "http://192.168.1.9:11434/v1", "concurrency": "3", "max_tokens": "4096",
                          "thinking": True, "num_ctx": "16384", "llama_url": "http://127.0.0.1:8083/v1"})
    data = json.loads(conf.read_text(encoding="utf-8"))
    assert data["OLLAMA_BASE_URL"] == "http://192.168.1.9:11434/v1" and data["LOCAL_CONCURRENCY"] == "3"
    assert "LLAMA_BASE_URL" not in data and data["OTHER"] == "giữ"          # = mặc định → không ghi
    assert "OLLAMA_BASE_URL" in out["changed"]
    # 2026-11-19: Ollama ở máy LAN bị bỏ (về :11434) trừ khi ALLOW_LAN_LLM=1
    assert llm._ollama_base() == "http://127.0.0.1:11434/v1" and llm._thinking() is True
    monkeypatch.setenv("ALLOW_LAN_LLM", "1")
    assert llm._ollama_base() == "http://192.168.1.9:11434/v1"
    monkeypatch.delenv("ALLOW_LAN_LLM")
    assert llm._ollama_num_ctx() == 16384
    assert scfg.workers_for("local") == 3 and scfg.workers_for("local/qwen3:8b") == 3
    assert scfg.out_cap("llama/gemma") == 4096
    assert scfg.workers_for("chatgpt/gpt-5-6-mini") == scfg.workers()      # Aurora không theo cài đặt local


def test_unset_local_falls_back_to_simple(conf):
    from engines.translate_simple import cfg as scfg
    cfg.save_config({"SIMPLE_WORKERS": "5"})
    assert scfg.workers_for("local") == 5


@pytest.mark.parametrize("patch", [{"llama_url": "127.0.0.1:8083"}, {"concurrency": "0"}, {"max_tokens": "abc"}, {"la": 1}])
def test_bad_values_rejected(conf, patch):
    before = conf.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        local_ai.apply(patch)
    assert conf.read_text(encoding="utf-8") == before


def test_is_local_model():
    assert all(local_ai.is_local_model(m) for m in ("local", "llama", "llama/x", "local/qwen3:8b", "auto"))
    assert not any(local_ai.is_local_model(m) for m in ("deepseek/deepseek-v4-flash", "chatgpt/gpt-6", "", None))


def test_desktop_has_local_settings():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "desktop" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "/api/v6/ai/local" in js and 'data-local="${esc(name)}"' in js and "loc-thinking" in js
