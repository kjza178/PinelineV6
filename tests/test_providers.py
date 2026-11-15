"""Nhà cung cấp LLM qua API (engines/providers): DeepSeek, Alibaba (Qwen).

Kiểm bằng một máy chủ chat/completions GIẢ chạy cục bộ — không gọi API thật, không cần key thật.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engines import providers
from engines.cloud_retry import classify_cloud_retry
from engines.llm import LLMUnavailable, chat


class _Fake:
    """Ghi lại request; trả lần lượt các phản hồi trong `script` (status, body)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.script: list[tuple[int, dict]] = []

    def handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):   # im lặng
                pass

            def do_GET(self):
                fake.calls.append({"path": self.path, "auth": self.headers.get("Authorization"), "method": "GET"})
                status, out = fake.script.pop(0) if fake.script else (200, {"object": "list", "data": []})
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                fake.calls.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                status, out = fake.script.pop(0) if fake.script else (200, _ok("xin chào"))
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if status == 429:
                    self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(data)
        return H


def _ok(text: str, **msg) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text, **msg}}]}


@pytest.fixture
def fake(monkeypatch):
    f = _Fake()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), f.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    for k in ("DEEPSEEK_BASE_URL", "ALIBABA_BASE_URL"):
        monkeypatch.setenv(k, base)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-dashscope")
    monkeypatch.delenv("ALIBABA_ENABLE_THINKING", raising=False)
    import core.config as cfg
    monkeypatch.setattr(cfg, "_CFG", {}, raising=False)          # bỏ giá trị trong pineline.config.json thật
    monkeypatch.setattr(cfg, "_load_cfg", lambda: None, raising=False)
    monkeypatch.setattr("time.sleep", lambda s: None)            # không chờ backoff thật
    yield f
    srv.shutdown()


def test_resolve_by_prefix():
    p, m = providers.resolve("deepseek/deepseek-flash")
    assert p.name == "deepseek" and m == "deepseek-flash"
    assert providers.resolve("alibaba/qwen-plus")[0].name == "alibaba"
    assert providers.resolve("qwen/qwen-max")[0].name == "alibaba"          # bí danh
    assert providers.resolve("deepseek/")[1] == ""                          # thiếu tên → KHÔNG đoán tên cứng
    for other in ("chatgpt/gpt-6", "local/gemma4", "local", "gemma4-12b", "", None):
        assert providers.resolve(other) is None


def test_deepseek_request_shape_via_llm_chat(fake):
    out = chat([{"role": "system", "content": "Dịch"}, {"role": "user", "content": "你好"}],
               model="deepseek/deepseek-flash", json_mode=True, max_tokens=20000, temperature=0.2)
    assert out == "xin chào"
    c = fake.calls[0]
    assert c["path"].endswith("/chat/completions") and c["auth"] == "Bearer sk-test-deepseek"
    b = c["body"]
    assert b["model"] == "deepseek-flash"                                  # bỏ tiền tố
    assert b["response_format"] == {"type": "json_object"} and b["stream"] is False
    assert b["max_tokens"] == 8192                                        # cắt theo trần nhà cung cấp
    assert "chat_template_kwargs" not in b                                # tham số riêng llama không lọt ra
    assert "json" in b["messages"][0]["content"].lower()                  # JSON mode cần chữ «json» trong prompt


def test_deepseek_reasoner_drops_temperature(fake):
    chat([{"role": "user", "content": "x"}], model="deepseek/deepseek-reasoner")
    assert "temperature" not in fake.calls[0]["body"]


def test_alibaba_disables_thinking_and_uses_dashscope_key(fake):
    chat([{"role": "user", "content": "x"}], model="alibaba/qwen-plus")
    c = fake.calls[0]
    assert c["auth"] == "Bearer sk-test-dashscope"
    assert c["body"]["model"] == "qwen-plus" and c["body"]["enable_thinking"] is False


def test_rate_limit_is_retried_then_succeeds(fake):
    fake.script = [(429, {"error": "rate limit"}), (503, {"error": "busy"}), (200, _ok("được rồi"))]
    assert chat([{"role": "user", "content": "x"}], model="deepseek/deepseek-flash") == "được rồi"
    assert len(fake.calls) == 3


def test_exhausted_rate_limit_is_classified_for_queue_retry(fake):
    fake.script = [(429, {"error": "rate limit"})] * 4
    with pytest.raises(LLMUnavailable) as ei:
        chat([{"role": "user", "content": "x"}], model="deepseek/deepseek-flash", retries=4)
    assert classify_cloud_retry(ei.value) == "limit"                      # cloud_retry / hàng chờ hiểu là lỗi tạm


def test_bad_key_fails_fast_with_clear_message(fake):
    fake.script = [(401, {"error": {"message": "invalid api key"}})]
    with pytest.raises(LLMUnavailable) as ei:
        chat([{"role": "user", "content": "x"}], model="deepseek/deepseek-flash")
    assert "401" in str(ei.value) and "sai API key" in str(ei.value)
    assert len(fake.calls) == 1                                           # không thử lại vô ích
    assert classify_cloud_retry(ei.value) is None                         # lỗi cấu hình, không phải lỗi tạm


def test_missing_key_names_the_setting(fake, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(LLMUnavailable) as ei:
        chat([{"role": "user", "content": "x"}], model="deepseek/deepseek-flash")
    assert "DEEPSEEK_API_KEY" in str(ei.value) and fake.calls == []


def test_unreachable_server_is_transient(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://127.0.0.1:9/v1")      # cổng đóng
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(LLMUnavailable) as ei:
        chat([{"role": "user", "content": "x"}], model="deepseek/deepseek-flash", retries=2, timeout=2)
    assert classify_cloud_retry(ei.value) == "transient"


def test_agent_uses_provider_with_tools(fake):
    from agents import agent_core
    fake.script = [(200, _ok("", tool_calls=[{"id": "1", "type": "function",
                                              "function": {"name": "list_jobs", "arguments": "{}"}}]))]
    msg = agent_core._chat_turn_model([{"role": "user", "content": "job nào lỗi?"}], True,
                                      "deepseek/deepseek-flash", tool_choice="auto")
    assert msg["tool_calls"][0]["function"]["name"] == "list_jobs"
    b = fake.calls[0]["body"]
    assert b["model"] == "deepseek-flash" and b["tools"] and b["tool_choice"] == "auto"


def test_old_9router_model_explains_instead_of_guessing_lan_address(monkeypatch):
    import core.config as cfg
    monkeypatch.setattr(cfg, "_CFG", {}, raising=False)
    monkeypatch.setattr(cfg, "_load_cfg", lambda: None, raising=False)
    monkeypatch.delenv("CLOUD_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("CLOUD_LLM_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable) as ei:
        chat([{"role": "user", "content": "x"}], model="ollama/gemma4:31b-cloud")
    assert "deepseek/" in str(ei.value) and "9router" in str(ei.value)


# ── Danh sách model thật (GET /models) — không ghi cứng tên model ──

def _models(*ids):
    return {"object": "list", "data": [{"id": i, "object": "model", "owned_by": "x"} for i in ids]}


def test_list_models_reads_ids_and_caches_only_saved_key(fake):
    p = providers.resolve("deepseek/x")[0]
    providers._openai._MODELS_CACHE.clear()
    fake.script = [(200, _models("deepseek-v4-pro", "deepseek-flash"))]
    assert providers.list_models(p) == ["deepseek-flash", "deepseek-v4-pro"]
    c = fake.calls[-1]
    assert c["method"] == "GET" and c["path"].endswith("/v1/models") and c["auth"] == "Bearer sk-test-deepseek"
    assert providers.cached_models(p)[1] == ["deepseek-flash", "deepseek-v4-pro"]
    # key vừa gõ (chưa lưu) → dùng để hỏi nhưng không ghi đè danh sách của key đã lưu
    fake.script = [(200, _models("khac"))]
    assert providers.list_models(p, api_key="sk-moi") == ["khac"]
    assert fake.calls[-1]["auth"] == "Bearer sk-moi"
    assert providers.cached_models(p)[1] == ["deepseek-flash", "deepseek-v4-pro"]


def test_list_models_errors_are_clear(fake, monkeypatch):
    p = providers.resolve("alibaba/x")[0]
    fake.script = [(401, {"error": {"message": "Invalid API key"}})]
    with pytest.raises(LLMUnavailable) as ei:
        providers.list_models(p)
    assert "401" in str(ei.value) and "sai API key" in str(ei.value)
    fake.script = [(200, {"data": []})]
    with pytest.raises(LLMUnavailable):
        providers.list_models(p)
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable) as ei:
        providers.list_models(p)
    assert "chưa có API key" in str(ei.value)


def test_pick_model_prefers_flash_and_skips_non_text_models():
    assert providers.pick_model(["deepseek-v4-pro", "deepseek-reasoner", "deepseek-flash"]) == "deepseek-flash"
    ids = ["qwen-max", "qwen-plus", "qwen-flash-2025-07-28", "qwen-flash", "qwen-vl-plus", "qwen3-coder-plus",
           "text-embedding-v4", "qwen-mt-turbo", "qwen3-omni-flash", "qwen-plus-latest"]
    assert providers.pick_model(ids) == "qwen-flash"
    assert providers.pick_model(["qwen-plus-2025-01-25", "qwen-plus"]) == "qwen-plus"
    assert providers.pick_model(["text-embedding-v4", "qwen-vl-max"]) == ""


def test_missing_model_name_is_an_error_not_a_guess(fake):
    with pytest.raises(LLMUnavailable) as ei:
        chat([{"role": "user", "content": "x"}], model="deepseek/")
    assert "chưa chọn tên model" in str(ei.value)
    assert not fake.calls                                                   # không gửi request với tên đoán
