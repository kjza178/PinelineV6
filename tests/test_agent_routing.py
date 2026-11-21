"""Agent / Agent dự phòng chọn Aurora (chatgpt/…) hoặc Ollama (local/<tên>) — fix 2026-11-15: trước đây bị gửi nhầm
tới AGENT_BASE_URL / LLM_BASE_URL (llama :8083). Nay đi qua engines.llm.chat bằng giao thức JSON {tool,args}|{final}."""
from __future__ import annotations

import json

import pytest

from agents import agent_core


@pytest.fixture
def seen(monkeypatch):
    calls = []
    replies = []
    import engines.llm as llm

    def chat(messages, model=None, **kw):
        calls.append({"messages": messages, "model": model, **kw})
        return replies.pop(0) if replies else '{"final":"xong"}'
    monkeypatch.setattr(llm, "chat", chat)

    def boom(*a, **k):
        raise AssertionError("không được gửi request thẳng tới base của Agent")
    monkeypatch.setattr(agent_core.requests, "post", boom)
    return {"calls": calls, "replies": replies}


@pytest.mark.parametrize("model", ["chatgpt/gpt-5-6-mini", "aurora/gpt-5-6", "local/gemma4"])
def test_aurora_and_ollama_go_through_llm_chat(seen, model):
    seen["replies"].append('{"tool":"pineline_status","args":{}}')
    msg = agent_core._chat_turn_model([{"role": "user", "content": "job nào lỗi?"}], True, model)
    assert msg["tool_calls"][0]["function"]["name"] == "pineline_status"
    c = seen["calls"][0]
    assert c["model"] == model and c["json_mode"] is True
    assert "pineline_status" in c["messages"][0]["content"]            # menu công cụ trong system


def test_history_with_tool_messages_is_flattened(seen):
    hist = [{"role": "user", "content": "tóm tắt hàng chờ"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "pineline_status", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "name": "pineline_status", "content": '{"queue":[]}'}]
    msg = agent_core._chat_turn_model(hist, True, "chatgpt/gpt-5-6-mini")
    assert msg["content"] == "xong"
    roles = [m["role"] for m in seen["calls"][0]["messages"]]
    assert "tool" not in roles and roles[-1] == "user"
    assert "KẾT QUẢ CÔNG CỤ pineline_status" in seen["calls"][0]["messages"][-1]["content"]
    assert json.loads(seen["calls"][0]["messages"][-2]["content"])["tool"] == "pineline_status"


def test_plain_text_reply_is_returned_as_content(seen):
    seen["replies"].append("Không có job nào lỗi.")
    assert agent_core._chat_turn_model([{"role": "user", "content": "?"}], True, "chatgpt/x")["content"] == \
        "Không có job nào lỗi."
