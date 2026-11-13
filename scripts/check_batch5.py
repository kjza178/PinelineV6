"""Kiểm thử đợt 5 (agent an toàn): lệnh / bắt buộc, phân tầng tool + xác nhận ✅/❌,
hg_auto_flow mode + chạy nền chống trùng, cắt kết quả tool, /stop không bị chặn.
Không gọi LLM / Telegram / Hongguo thật — mọi I/O ngoài đều mock."""
import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest  # noqa: F401  — server không nạp queue / khởi worker khi có pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("  PASS" if cond else "  FAIL", name)


import agents.agent_hongguo as hga  # noqa: E402
import agents.agent_core as agent_core  # noqa: E402

# ---------- 1. A13 detect_flow_mode ----------
print("[1] hg_auto_flow: chỉ nhận cả chuỗi là từ khoá")
cases = {
    "lib": "lib", "Thư viện": "lib", "chưa làm": "lib", "kho": "lib",
    "top": "rank", "top 1": "rank", "TOP #3": "rank", "bxh": "rank", "leaderboard": "rank",
    "Kho Báu Hot": "name", "Hot Mom": "name", "Top Gun 2": "name", "Thần Y Trở Lại": "name",
    "7425123456789": "name", "3": "name",
}
for txt, want in cases.items():
    check(f"'{txt}' → {want}", hga.detect_flow_mode(txt) == want)

# ---------- 2. tool_hg_auto_flow mode ----------
print("[2] tool_hg_auto_flow: mode tường minh / tên chứa 'kho' không bốc thư viện")
picked = []


def _fake_pick(items, target):
    picked.append(target)
    return None, [{"name": "A"}, {"name": "B"}]


with mock.patch.object(hga, "ensure_hongguo_running", lambda: None), \
        mock.patch.object(hga.hg, "library", lambda: {"output_dir": "X:\\none", "items": [{"name": "Lib1"}]}), \
        mock.patch.object(hga, "_get_existing_pineline_titles", lambda rt: set()), \
        mock.patch.object(hga, "_pick_lib_item", _fake_pick):
    r = json.loads(hga.tool_hg_auto_flow("Kho Báu Hot", voice="v2", tts_engine="vieneu"))
    check("'Kho Báu Hot' đi nhánh tên (ambiguous), không bốc Lib1", r.get("stage") == "ambiguous"
          and picked == ["Kho Báu Hot"])
    r = json.loads(hga.tool_hg_auto_flow("x", voice="v2", tts_engine="vieneu", mode="bogus"))
    check("mode lạ → lỗi", "không hợp lệ" in (r.get("error") or ""))
with mock.patch.object(hga, "ensure_hongguo_running", lambda: None), \
        mock.patch.object(hga.hg, "library", lambda: {"output_dir": "X:\\none", "items": []}), \
        mock.patch.object(hga, "_get_existing_pineline_titles", lambda rt: set()):
    r = json.loads(hga.tool_hg_auto_flow("", voice="v2", tts_engine="vieneu", mode="lib"))
    check("mode=lib, query rỗng, thư viện trống → select_lib", r.get("stage") == "select_lib")
    r = json.loads(hga.tool_hg_auto_flow("", voice="v2", tts_engine="vieneu"))
    check("mode=name + query rỗng → lỗi thiếu tên", "thiếu" in (r.get("error") or ""))

# ---------- 3. A7 chạy nền + chống trùng ----------
print("[3] start_auto_flow_bg: trả ngay, trùng target không chạy thêm")
gate = threading.Event()
calls = []


def _slow_flow(target, **kw):
    calls.append((target, kw.get("mode")))
    gate.wait(5)
    return json.dumps({"ok": False, "message": "giả lập lỗi"})


notes = []
with mock.patch.object(hga, "tool_hg_auto_flow", _slow_flow):
    t0 = time.time()
    r1 = json.loads(hga.start_auto_flow_bg(None, {"query_or_id": "Phim A"}, notify=notes.append))
    check("lần 1 started, trả ngay (<1s)", r1.get("started") and time.time() - t0 < 1)
    r2 = json.loads(hga.start_auto_flow_bg(None, {"query_or_id": "  phim   a "}, notify=notes.append))
    check("cùng target (khác hoa/thường/khoảng trắng) → already_running", r2.get("already_running"))
    r3 = json.loads(hga.start_auto_flow_bg(None, {"query_or_id": "Phim B"}, notify=notes.append))
    check("target khác → started", r3.get("started"))
    r4 = json.loads(hga.start_auto_flow_bg(None, {"query_or_id": "lib"}, notify=notes.append))
    r5 = json.loads(hga.start_auto_flow_bg(None, {"query_or_id": "", "mode": "lib"}, notify=notes.append))
    check("'lib' và mode=lib cùng khoá → lần 2 already_running", r4.get("started") and r5.get("already_running"))
    gate.set()
    for _ in range(50):
        if not hga.running_auto_flows():
            break
        time.sleep(0.05)
    check("xong thì nhả khoá", not hga.running_auto_flows())
    check("kết quả lỗi được báo qua notify", any("giả lập lỗi" in n for n in notes))
    check("mode truyền xuống flow", ("Phim A", "name") in calls and ("lib", "lib") in calls)
    r6 = json.loads(hga.start_auto_flow_bg(None, {"query_or_id": "Phim A"}, notify=notes.append))
    check("chạy lại được sau khi xong", r6.get("started"))
    for _ in range(50):
        if not hga.running_auto_flows():
            break
        time.sleep(0.05)

with mock.patch.object(hga, "start_auto_flow_bg", lambda rt, args, notify=None: "BG") as _m:
    out = hga.handle_hongguo_tool(None, "hg_auto_flow", {"query_or_id": "x"}, allow_mutate=True)
    check("handle_hongguo_tool hg_auto_flow → chạy nền", out == "BG")
check("schema hg_auto_flow có mode enum",
      next(t for t in hga.HONGGUO_TOOLS if t["function"]["name"] == "hg_auto_flow")
      ["function"]["parameters"]["properties"]["mode"]["enum"] == ["name", "lib", "rank"])

# ---------- 4. tool_tier ----------
print("[4] agent_core.tool_tier")
tt = agent_core.tool_tier
tier_cases = [
    ("pineline_status", {}, "read"),
    ("pineline_queue_add", {"job_ids": ["a"]}, "mutate"),
    ("pineline_queue_stop", {}, "confirm"),
    ("pineline_stop_job", {"job_id": "a"}, "confirm"),
    ("hg_clean", {"folder_name": "x"}, "confirm"),
    ("hg_auto_flow", {"query_or_id": "x"}, "confirm"),
    ("hg_download", {"series_ids": ["1"]}, "mutate"),
    ("pineline_tts_run", {"job_id": "a"}, "mutate"),
    ("pineline_tts_run", {"job_id": "a", "force": True}, "confirm"),
    ("pineline_run_stage", {"job_id": "a", "stage": "asr", "force": "true"}, "confirm"),
    ("pineline_config_set", {"voice": "v2"}, "mutate"),
    ("pineline_config_set", {"reset": True}, "confirm"),
    ("pineline_script_edit_many", {"job_id": "a", "edits": [{}] * 3}, "mutate"),
    ("pineline_script_edit_many", {"job_id": "a", "edits": [{}] * 25}, "confirm"),
    ("pineline_heal_failed_batch", {"apply": False}, "read"),
    ("pineline_heal_failed_batch", {"apply": True}, "confirm"),
    ("pineline_heal_job", {"job_id": "a", "apply": True}, "mutate"),
    ("pineline_heal_job", {"job_id": "a", "apply": True, "level": "aggressive"}, "confirm"),
    ("pineline_library", {"action": "list"}, "read"),
    ("pineline_library", {"action": "add", "target": "2"}, "mutate"),
    ("pineline_library", {"action": "merge", "target": "2"}, "confirm"),
    ("pineline_feed", {"action": "show"}, "read"),
    ("pineline_feed", {"action": "now"}, "mutate"),
    ("pineline_feed", {"action": "off"}, "confirm"),
]
for name, args, want in tier_cases:
    check(f"{name} {args if len(str(args)) < 50 else '…'} → {want}", tt(name, args) == want)

# ---------- 5. park / confirm ----------
print("[5] park_action / confirm_action")
agent_core._PENDING.clear()
p1 = agent_core.park_action("pineline_stop_job", {"job_id": "j1"})
p2 = agent_core.park_action("pineline_stop_job", {"job_id": "j1"})
p3 = agent_core.park_action("pineline_stop_job", {"job_id": "j2"})
check("cùng (tool,args) → cùng id", p1["id"] == p2["id"])
check("args khác → id khác", p1["id"] != p3["id"])
check("pending_actions liệt kê 2", len(agent_core.pending_actions()) == 2)
ran = []
with mock.patch.object(agent_core, "_dispatch",
                       lambda n, a, allow_mutate: (ran.append((n, a, allow_mutate)), '{"ok": true}')[1]):
    r = agent_core.confirm_action(p1["id"], True)
    check("approve → chạy với allow_mutate=True", r.get("ok") and ran == [("pineline_stop_job", {"job_id": "j1"}, True)])
    check("kết quả parse JSON", r.get("result") == {"ok": True})
    r = agent_core.confirm_action(p1["id"], True)
    check("bấm lần 2 → không chạy lại", not r.get("ok") and len(ran) == 1)
    r = agent_core.confirm_action(p3["id"], False)
    check("❌ → huỷ, không chạy", r.get("cancelled") and len(ran) == 1)
    p4 = agent_core.park_action("pineline_queue_stop", {})
    agent_core._PENDING[p4["id"]]["at"] -= agent_core.CONFIRM_TTL_S + 1
    r = agent_core.confirm_action(p4["id"], True)
    check("hết hạn 15 phút → không chạy", not r.get("ok") and len(ran) == 1)

# ---------- 6. run_agent require_confirm ----------
print("[6] run_agent: tool confirm đỗ lại khi require_confirm")


def _script(turns):
    it = iter(turns)
    return lambda messages, use_tools=True: next(it)


tc_msg = {"role": "assistant", "content": None, "tool_calls": [
    {"id": "c1", "function": {"name": "pineline_queue_stop", "arguments": "{}"}},
    {"id": "c2", "function": {"name": "pineline_status", "arguments": "{}"}},
]}
final = {"role": "assistant", "content": "đã gửi nút"}
agent_core._PENDING.clear()
ran = []
fake_dispatch = lambda n, a, allow_mutate: (ran.append(n), '{"ok": true}')[1]  # noqa: E731
with mock.patch.object(agent_core, "_llm_cfg", lambda: ("", "", "m")), \
        mock.patch.object(agent_core, "_dispatch", fake_dispatch), \
        mock.patch.object(agent_core, "_chat_turn", _script([tc_msg, final])):
    res = agent_core.run_agent("dừng hết", allow_mutate=True, require_confirm=True)
check("queue_stop KHÔNG chạy, status vẫn chạy", ran == ["pineline_status"])
check("confirms có 1 mục queue_stop", len(res.confirms) == 1 and res.confirms[0]["tool"] == "pineline_queue_stop")
check("step ghi awaiting_user_confirm", "awaiting_user_confirm" in res.steps[0]["result_preview"])
ran = []
with mock.patch.object(agent_core, "_llm_cfg", lambda: ("", "", "m")), \
        mock.patch.object(agent_core, "_dispatch", fake_dispatch), \
        mock.patch.object(agent_core, "_chat_turn", _script([tc_msg, final])):
    res = agent_core.run_agent("dừng hết", allow_mutate=True, require_confirm=False)
check("UI (require_confirm=False) chạy thẳng như cũ", ran == ["pineline_queue_stop", "pineline_status"]
      and not res.confirms)
check("SYSTEM không còn tool qa_override ma", "qa_override" not in agent_core.SYSTEM)

loop_msg = {"role": "assistant", "content": None, "tool_calls": [
    {"id": "q", "function": {"name": "pineline_queue_add", "arguments": '{"job_ids": ["abc"]}'}},
    {"id": "s", "function": {"name": "pineline_status", "arguments": "{}"}},
]}
with mock.patch.object(agent_core, "_llm_cfg", lambda: ("", "", "m")), \
        mock.patch.object(agent_core, "_dispatch", lambda n, a, allow_mutate: '{"ok": true}'), \
        mock.patch.object(agent_core, "_chat_turn", lambda messages, use_tools=True: loop_msg):
    res = agent_core.run_agent("x", allow_mutate=True, max_steps=2)
check("hết bước → liệt kê thao tác đã đổi trạng thái (không liệt kê đọc)",
      "Đã chạy" in res.reply and "pineline_queue_add" in res.reply and "pineline_status" not in res.reply)

# ---------- 7. clip_tool_result ----------
print("[7] cắt kết quả tool trước khi đưa vào context")
check("ngắn giữ nguyên", agent_core.clip_tool_result("abc", 10) == "abc")
c = agent_core.clip_tool_result("x" * 100, 10)
check("dài → cắt + ghi chú", c.startswith("x" * 10) and "cắt 90" in c and len(c) < 200)
big = "y" * 20000
seen = []
with mock.patch.object(agent_core, "_llm_cfg", lambda: ("", "", "m")), \
        mock.patch.object(agent_core, "_dispatch", lambda n, a, allow_mutate: big), \
        mock.patch.object(agent_core, "_chat_turn",
                          lambda messages, use_tools=True: (seen.append([m.get("content") for m in messages]),
                                                            final if len(seen) > 1 else
                                                            {"role": "assistant", "content": None, "tool_calls": [
                                                                {"id": "c", "function": {"name": "pineline_status",
                                                                                         "arguments": "{}"}}]})[1]):
    agent_core.run_agent("x")
check("tool message trong context ≤ giới hạn + ghi chú",
      len(seen[1][-1]) < agent_core.TOOL_RESULT_MAX + 200)

# ---------- 8. server endpoints ----------
print("[8] server /api/agent/chat · /pending · /confirm")
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

cl = TestClient(server.app, base_url="http://127.0.0.1:8801")
got = {}


def _fake_run(msg, **kw):
    got.update(kw)
    return agent_core.AgentResult(reply="r", confirms=[{"id": "ab", "tool": "t", "summary": "s"}])


with mock.patch.object(agent_core, "run_agent", _fake_run):
    r = cl.post("/api/agent/chat", json={"message": "hi", "allow_mutate": True, "require_confirm": True})
check("chat chuyển require_confirm xuống run_agent", got.get("require_confirm") is True)
check("chat trả confirms", r.json().get("confirms") == [{"id": "ab", "tool": "t", "summary": "s"}])
r = cl.post("/api/agent/confirm", json={"id": "khongco", "approve": True})
check("confirm id lạ → 404", r.status_code == 404)
agent_core._PENDING.clear()
pid = agent_core.park_action("pineline_queue_stop", {})["id"]
check("/pending thấy mục đỗ", any(x["id"] == pid for x in cl.get("/api/agent/pending").json()["pending"]))
r = cl.post("/api/agent/confirm", json={"id": pid, "approve": False})
check("confirm ❌ → 200 cancelled", r.status_code == 200 and r.json().get("cancelled"))
check("Runtime có hook notify", callable(agent_core._rt.notify))

# ---------- 9. sidecar A8 / A9 ----------
print("[9] sidecar: lệnh đổi trạng thái phải có /, /stop chạy ngay")
import agents.agent_tele_cmd as tc  # noqa: E402

hint_cases = [
    ("stop", "hint"), ("Stop", "hint"), ("reset", "hint"), ("tick", "hint"), ("heal", "hint"),
    ("set voice=v2", "hint"), ("restartserver", "hint"), ("on", "hint"), ("off", "hint"),
    ("feed", None), ("feed status", None), ("feed on", "hint"), ("feed now", "hint"),
    ("hg 2", "hint"), ("hg auto top 1", "hint"), ("hg_auto Phim A", "hint"), ("hg clean X", "hint"),
    ("hg rank", None), ("hg search Phim", None), ("hg_rank", None),
    ("lib", None), ("lib add 2", "hint"), ("lib ghép 3", "hint"), ("lib 2", None),
    ("autopilot on", "hint"), ("auto-pilot tắt", "hint"), ("autopilot", None),
    ("stop job abc ngay bây giờ đi", "agent"), ("hg auto cho tao bộ Thần Y luôn nhé", "hint"),
    ("hg auto Thần Y Trở Lại Phần 2", "hint"),
    ("/stop", None), ("/hg auto X", None), ("Top phim hot hôm nay là gì?", None),
    ("stop?", None), ("bật auto pilot giúp tao", None),
]
for txt, want in hint_cases:
    check(f"'{txt}' → {want}", tc._slash_hint(txt)[0] == want)
check("gợi ý autopilot on → /on", "/on" in tc._slash_hint("autopilot on")[1])
check("gợi ý stop → /stop", "/stop" in tc._slash_hint("stop")[1])

flips = []
with mock.patch.object(tc, "set_enabled", lambda v: flips.append(v)), \
        mock.patch.object(tc, "tick_once", lambda *a, **k: {}), \
        mock.patch.object(tc, "load_state", lambda: {"enabled": True}), \
        mock.patch.object(tc, "status_brief", lambda b: {}), \
        mock.patch.object(tc, "format_status", lambda s: "S"), \
        mock.patch.object(tc, "hard_stop", lambda b: flips.append("HARD") or {}), \
        mock.patch.object(tc, "_agent_ask", lambda m, u="": {"reply": "agent:" + m}):
    out = tc.handle_command("autopilot", uid="1")
    check("'autopilot' chỉ hiện trạng thái, không bật", "ON" in out and flips == [])
    out = tc.handle_command("bật auto pilot giúp tao", uid="1")
    check("câu có auto+pilot → agent, không tự bật (bỏ fuzzy)", out.startswith("agent:") and flips == [])
    out = tc.handle_command("stop", uid="1")
    check("'stop' trơn → gợi ý, không hard_stop", "/stop" in out and flips == [])
    out = tc.handle_command("/stop", uid="1")
    check("'/stop' → hard_stop + OFF", flips == [False, "HARD"])
    out = tc.handle_command("/agent Xem Job ABCdef 末世", uid="1")
    check("/agent giữ nguyên hoa/thường", out == "agent:Xem Job ABCdef 末世")

check("/stop là lệnh ưu tiên", tc.is_priority_cmd("/stop") and tc.is_priority_cmd("/stop@PinelineBot"))
check("/status ưu tiên, /agent không", tc.is_priority_cmd("/status") and not tc.is_priority_cmd("/agent x"))
check("chữ trơn không ưu tiên", not tc.is_priority_cmd("stop"))

sent = []
chat_kw = {}


def _fake_chat(base, msg, **kw):
    chat_kw.update(kw)
    return {"reply": "ok", "confirms": [{"id": "c0ffee01", "tool": "pineline_queue_stop", "summary": "pineline_queue_stop {}"}]}


with mock.patch.object(tc, "agent_chat", _fake_chat), \
        mock.patch.object(tc, "tg_api", lambda m, p: sent.append((m, p)) or {"ok": True}), \
        mock.patch.object(tc, "get_user_history", lambda u: []), \
        mock.patch.object(tc, "add_user_history", lambda *a: None):
    tc._agent_ask("dừng", uid="42")
    check("Telegram → require_confirm=True", chat_kw.get("require_confirm") is True)
    kb = [p for m, p in sent if m == "sendMessage" and "reply_markup" in p]
    check("gửi nút ✅/❌ với callback agc:<id>:y|n",
          len(kb) == 1 and "agc:c0ffee01:y" in kb[0]["reply_markup"] and "agc:c0ffee01:n" in kb[0]["reply_markup"])
    sent.clear()
    tc._agent_ask("dừng", uid="")
    check("HTTP /cmd (không uid) → require_confirm=False, không gửi nút",
          chat_kw.get("require_confirm") is False and not sent)

edits = []
with mock.patch.object(tc, "agent_confirm", lambda b, cid, ap: {"ok": True, "summary": "S", "result": {"x": 1}}
                       if ap else {"ok": True, "cancelled": True, "summary": "S"}), \
        mock.patch.object(tc, "_edit_or_send", lambda uid, cq, text, buttons: edits.append((text, buttons))):
    tc._confirm_callback("42", {}, "agc:abc:y")
    tc._confirm_callback("42", {}, "agc:abc:n")
check("✅ → sửa tin thành 'Đã chạy', bỏ nút", edits[0][0].startswith("✅ Đã chạy") and edits[0][1] == [])
check("❌ → 'Đã huỷ'", edits[1][0].startswith("🚫 Đã huỷ"))

bg = []
with mock.patch.object(tc.hg_agent, "start_auto_flow_bg",
                       lambda rt, args, notify=None: (bg.append(args), json.dumps(
                           {"ok": True, "already_running": True, "running_min": 3.0}))[1]), \
        mock.patch.object(tc, "tg_api", lambda m, p: sent.append((m, p)) or {"ok": True}):
    sent.clear()
    tc.hg_tele_command("/hg 5", uid="42")
check("/hg <số> đi qua start_auto_flow_bg", bg and bg[0]["query_or_id"] == "5")
check("trùng → báo 'đang chạy'", any("đang chạy Auto Flow" in p.get("text", "") for m, p in sent))

print(f"\nTỔNG: {ok} PASS, {fail} FAIL")
sys.exit(1 if fail else 0)
