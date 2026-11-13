"""Lệnh NGƯỜI DÙNG gửi qua Telegram (agent_tele_cmd → /api/agent/chat|confirm) chạy stage/dịch/tts/mux
= CHẠY TAY → gỡ HOLD + 1 tin. Brain/LLM tự phát, heal/av-sync hook → KHÔNG.
fix 2026-11-12 agentI2. Không LLM thật, không HTTP thật, không Telegram thật.

routes_agent.py cần fastapi/server (thiếu ở bản sao cloud) → các hàm liên quan được nạp riêng bằng ast
với S/Manifest/JSONResponse giả; S.run_stage giả gọi agent_hold.maybe_manual_override(None, …) đúng như
server._manual_hold_override (request=None khi gọi Python trực tiếp)."""
from __future__ import annotations

import ast
import contextlib
import json
import threading
import types
from pathlib import Path

import pytest

from agents import agent_hold   # V6 (port P6): package agents/
from core.manifest import Manifest
from state import job_state as js

ROOT = Path(__file__).resolve().parents[1]
JID = "f0834acf0cc0"
MF = {"title_vi": "Độc Đế Vô Song 3D Phần 6", "request": {"title": "毒帝无双3D版第六季"}}


class _Req:
    def __init__(self, headers: dict):
        self.headers = {k.lower(): v for k, v in headers.items()}


def _held_job(root: Path, jid: str = JID) -> Path:
    d = root / jid
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(MF | {
        "stages": {"translate": {"status": "done", "cur_text": "HOLD Brain — 1 high QA flags"},
                   "tts": {"status": "pending"}},
        "agent": {"hold_after_translate": True, "qa_blocked": True, "held_at": "qa_gate",
                  "hold_reason": "Second Brain: 1 high QA — chặn TTS/mux"}}, ensure_ascii=False), encoding="utf-8")
    (d / "qa_brain.json").write_text(json.dumps({"high_open": 1, "high_i": [12]}), encoding="utf-8")
    return d


def _agent(d: Path) -> dict:
    return json.loads((d / "manifest.json").read_text(encoding="utf-8"))["agent"]


@pytest.fixture
def sent(monkeypatch):
    out: list = []
    monkeypatch.setattr(agent_hold, "_notify_override", lambda wd, st, data: out.append((wd.name, st)))
    return out


# ---------------------------------------------------------------------------
# agent_hold: nguồn lệnh
# ---------------------------------------------------------------------------
def test_manual_source_matrix():
    assert agent_hold.manual_source(None) is None                                    # Python trực tiếp
    with agent_hold.user_context("agent_chat:telegram"):
        assert agent_hold.manual_source(None) == "agent_chat:telegram"
        with agent_hold.auto_context("brain"):                                       # brain luôn thắng
            assert agent_hold.manual_source(None) is None
        with agent_hold.auto_context("heal"):
            assert agent_hold.manual_source(None) is None
        assert agent_hold.manual_source(None) == "agent_chat:telegram"
    assert agent_hold.manual_source(None) is None and agent_hold.user_caller() is None
    with agent_hold.auto_context("brain"):
        with agent_hold.user_context("x"):                                           # lồng ngược cũng vậy
            assert agent_hold.manual_source(None) is None
    # HTTP
    tg = _Req({"x-pineline-caller": "user", "x-pineline-source": "telegram", "user-agent": "python-requests/2.32"})
    assert agent_hold.is_manual_request(tg) and agent_hold.manual_source(tg) == "http:telegram"
    assert agent_hold.manual_source(_Req({"user-agent": "python-requests/2.32"})) is None    # autopilot tick
    assert agent_hold.manual_source(_Req({"x-pineline-caller": "brain", "user-agent": "Mozilla"})) is None
    assert agent_hold.manual_source(_Req({"user-agent": "Mozilla/5.0 WebView2"})) == "http:ui"
    assert agent_hold.request_source(_Req({"x-pineline-source": "Tele gram<b>"})) == "telegramb"


def test_user_context_restores_on_error_and_is_thread_local():
    with pytest.raises(RuntimeError):
        with agent_hold.user_context("agent_chat:telegram"):
            raise RuntimeError("x")
    assert agent_hold.user_caller() is None
    seen: list = []
    ready, done = threading.Event(), threading.Event()

    def _other():
        ready.wait(5)
        seen.append(agent_hold.manual_source(None))
        done.set()

    t = threading.Thread(target=_other)
    t.start()
    with agent_hold.user_context("agent_chat:telegram"):
        ready.set()
        done.wait(5)
    t.join(5)
    assert seen == [None]          # luồng khác (vd tick brain/queue) không thấy ngữ cảnh người dùng


def test_maybe_override_in_user_context(tmp_path, sent):
    d = _held_job(tmp_path)
    assert agent_hold.maybe_manual_override(None, d, "tts", source="run_stage") is None   # chưa có ngữ cảnh
    assert _agent(d)["qa_blocked"] is True
    with agent_hold.user_context("agent_confirm:telegram"):
        with agent_hold.auto_context("brain"):
            assert agent_hold.maybe_manual_override(None, d, "tts", source="run_stage") is None
        ov = agent_hold.maybe_manual_override(None, d, "mux", source="mux")
    assert ov and ov["by"] == "manual" and ov["stage"] == "mux" and ov["source"] == "mux+agent_confirm:telegram"
    ag = _agent(d)
    assert not ag["qa_blocked"] and not ag["hold_after_translate"] and "held_at" not in ag
    assert sent == [(JID, "mux")]
    # HTTP tay giữ nguyên nhãn source cũ (agentH)
    d2 = _held_job(tmp_path, "aaaaaaaaaaaa")
    ov2 = agent_hold.maybe_manual_override(_Req({"user-agent": "Mozilla"}), d2, "tts", source="run_stage")
    assert ov2["source"] == "run_stage"


# ---------------------------------------------------------------------------
# routes_agent: nạp riêng các hàm (không fastapi)
# ---------------------------------------------------------------------------
_FUNCS = ("_as_auto", "_agent_user_ctx", "_agent_run_stage", "_agent_mark_qa_override",
          "api_agent_chat", "api_agent_confirm")


class _JSONResponse:
    def __init__(self, content=None, status_code=200):
        self.body = json.dumps(content or {}).encode()
        self.status_code = status_code


def _load_routes(work_root: Path, calls: list, *, busy: bool = False):
    src = (ROOT / "http_routes" / "routes_agent.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    nodes = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in _FUNCS:
            n.decorator_list = []
            nodes.append(n)
    assert {n.name for n in nodes} == set(_FUNCS)
    mod = ast.Module(body=nodes, type_ignores=[])

    def run_stage(job_id, stage, force=False, voice=None, speed=None, engine=None, request=None):
        calls.append(("run_stage", job_id, stage))
        if busy:
            return _JSONResponse({"error": "busy"}, 409)
        # = server.run_stage: claim OK → _manual_hold_override(request, job_id, stage, "run_stage")
        agent_hold.maybe_manual_override(request, work_root / job_id, stage, source="run_stage")
        return {"ok": True, "stage": stage}

    S = types.SimpleNamespace(WORK_ROOT=work_root, run_stage=run_stage, _job_cache={},
                              AgentChatReq=object, AgentConfirmReq=object)

    class _Core:
        @staticmethod
        def run_agent(msg, **kw):
            # LLM chọn tool tts_run → Runtime.run_stage = _agent_run_stage (in-process, request=None)
            ns["_agent_run_stage"](JID, "tts", {})
            return types.SimpleNamespace(reply="ok", steps=[], model="m", confirms=[])

        @staticmethod
        def confirm_action(cid, approve):
            ns["_agent_run_stage"](JID, "translate", {"force": True})
            return {"ok": True}

    def _set_hold(job_id, enabled):
        calls.append(("set_hold", job_id, enabled))
        return {"ok": True}

    ns = {"__builtins__": __builtins__, "json": json, "S": S, "Manifest": Manifest, "js": js, "JSONResponse": _JSONResponse,
          "_agent_core": _Core, "_agent_get_hold": lambda jid: _agent(work_root / jid),
          "_agent_set_hold": _set_hold, "Request": object}
    exec(compile(mod, "routes_agent_subset", "exec"), ns)
    return ns


def test_telegram_chat_tool_tts_counts_as_manual(tmp_path, sent):
    d = _held_job(tmp_path)
    calls: list = []
    ns = _load_routes(tmp_path, calls)
    req = types.SimpleNamespace(message="lồng tiếng lại job f0834a", history=[], allow_mutate=True,
                                max_steps=10, require_confirm=True, force_first_tool=None)
    tg = _Req({"x-pineline-caller": "user", "x-pineline-source": "telegram", "user-agent": "python-requests/2.32"})
    r = ns["api_agent_chat"](req, tg)
    assert r["ok"] is True
    ag = _agent(d)
    assert not ag["qa_blocked"] and not ag["hold_after_translate"] and "held_at" not in ag
    assert ag["hold_override"]["source"] == "run_stage+agent_chat:telegram"
    assert ag["hold_override"]["was"]["held_at"] == "qa_gate"
    assert ag["qa_override"] is True and ag["qa_gate"] is True      # giữ ngữ nghĩa cũ của Agent TTS
    assert ("set_hold", JID, False) not in calls                     # không gỡ trước claim
    assert sent == [(JID, "tts")]                                    # đúng 1 tin
    assert agent_hold.user_caller() is None                          # thoát ngữ cảnh


def test_telegram_confirm_button_counts_as_manual(tmp_path, sent):
    d = _held_job(tmp_path)
    ns = _load_routes(tmp_path, [])
    tg = _Req({"x-pineline-caller": "user", "x-pineline-source": "telegram", "user-agent": "python-requests/2.32"})
    assert ns["api_agent_confirm"](types.SimpleNamespace(id="ab12", approve=True), tg)["ok"]
    assert _agent(d)["hold_override"]["source"] == "run_stage+agent_confirm:telegram"
    assert sent == [(JID, "translate")]


@pytest.mark.parametrize("headers", [
    {"user-agent": "python-requests/2.32"},                                  # autopilot/script không header
    {"x-pineline-caller": "brain", "user-agent": "Python-urllib/3.12"},     # brain HTTP fallback
    {"x-pineline-caller": "brain", "user-agent": "Mozilla"},
])
def test_non_user_http_chat_does_not_override(tmp_path, sent, headers):
    d = _held_job(tmp_path)
    calls: list = []
    ns = _load_routes(tmp_path, calls)
    req = types.SimpleNamespace(message="x", history=[], allow_mutate=True, max_steps=10,
                                require_confirm=False, force_first_tool=None)
    ns["api_agent_chat"](req, _Req(headers))
    ag = _agent(d)
    assert "hold_override" not in ag and sent == []
    assert ("set_hold", JID, False) in calls                         # đường tự động cũ giữ nguyên


def test_brain_in_process_does_not_override(tmp_path, sent):
    d = _held_job(tmp_path)
    calls: list = []
    ns = _load_routes(tmp_path, calls)
    with agent_hold.auto_context("brain"):                            # = agent_brain._run_agent_llm
        ns["_agent_run_stage"](JID, "tts", {})
    assert "hold_override" not in _agent(d) and sent == []
    assert ("set_hold", JID, False) in calls


def test_heal_hook_inside_user_chat_is_auto(tmp_path, sent):
    d = _held_job(tmp_path)
    calls: list = []
    ns = _load_routes(tmp_path, calls)
    hook = ns["_as_auto"](lambda jid, st: ns["_agent_run_stage"](jid, st, {}), "heal")
    with agent_hold.user_context("agent_chat:telegram"):              # user bảo «heal job» → heal tự quyết
        hook(JID, "tts")
        assert agent_hold.manual_source(None) == "agent_chat:telegram"
    assert "hold_override" not in _agent(d) and sent == []


def test_user_busy_job_keeps_hold(tmp_path, sent):
    d = _held_job(tmp_path)
    ns = _load_routes(tmp_path, [], busy=True)
    with agent_hold.user_context("agent_chat:telegram"):
        r = ns["_agent_run_stage"](JID, "tts", {})
    assert isinstance(r, dict) and r.get("error") == "busy"
    ag = _agent(d)
    assert ag["qa_blocked"] is True and "hold_override" not in ag and not ag.get("qa_override") and sent == []


def test_routes_wiring_source():
    src = (ROOT / "http_routes" / "routes_agent.py").read_text(encoding="utf-8")
    assert '_HEAL_HOOKS = {k: _as_auto(v, "heal")' in src
    assert '_AV_SYNC_HOOKS = {k: _as_auto(v, "av_sync")' in src
    # bind Runtime sau khi bọc hook
    assert src.index('_HEAL_HOOKS = {k: _as_auto') < src.index("heal_hooks=_HEAL_HOOKS")
    assert 'with _agent_user_ctx(request, "agent_chat")' in src
    assert 'with _agent_user_ctx(request, "agent_confirm")' in src


# ---------------------------------------------------------------------------
# Client: agent_autopilot / agent_tele_cmd gắn header đúng nguồn
# ---------------------------------------------------------------------------
class _Resp:
    status_code = 200
    text = "{}"

    def json(self):
        return {"ok": True}


def test_autopilot_headers(monkeypatch):
    import agents.agent_autopilot as ap
    got: list = []
    monkeypatch.setattr(ap.requests, "post", lambda url, **kw: got.append((url, kw["headers"])) or _Resp())
    ap.agent_chat("http://x", "hi", caller="user", source="telegram")
    ap.agent_confirm("http://x", "c1", True, caller="user", source="telegram")
    ap.agent_chat("http://x", "hi")                                   # mặc định: không gắn
    ap._post("http://x", "/api/agent/watch-once", {})                 # tick autopilot: không gắn
    assert got[0] == ("http://x/api/agent/chat", {"Content-Type": "application/json",
                                                  "X-Pineline-Caller": "user", "X-Pineline-Source": "telegram"})
    assert got[1][1]["X-Pineline-Caller"] == "user" and got[1][0].endswith("/api/agent/confirm")
    assert "X-Pineline-Caller" not in got[2][1] and "X-Pineline-Caller" not in got[3][1]


def test_tele_cmd_marks_only_telegram_user(monkeypatch):
    import agents.agent_tele_cmd as tc
    chats: list = []
    confirms: list = []
    monkeypatch.setattr(tc, "agent_chat", lambda base, msg, **kw: chats.append(kw) or {"reply": "ok"})
    monkeypatch.setattr(tc, "agent_confirm", lambda base, cid, ok, **kw: confirms.append(kw) or {"ok": True})
    monkeypatch.setattr(tc, "add_user_history", lambda *a, **k: None)
    monkeypatch.setattr(tc, "get_user_history", lambda uid: [])
    monkeypatch.setattr(tc, "_edit_or_send", lambda *a, **k: None)
    tc._agent_ask("lồng tiếng lại f0834a", "123")                      # người gõ trên Telegram
    tc._agent_ask("x", "")                                             # /cmd nội bộ (không uid)
    tc._confirm_callback("123", {}, "agc:ab12:y")                      # người bấm ✅
    assert chats[0]["caller"] == "user" and chats[0]["source"] == "telegram"
    assert chats[1]["caller"] is None and chats[1]["source"] is None
    assert confirms == [{"caller": "user", "source": "telegram"}]
