"""2026-11-21 vận hành: (1) bấm Chạy gỡ dấu park hệ thống + gỡ tạm dừng hàng chờ; (2) AI dịch hỏng → chuỗi dự phòng
(model dự phòng → AI máy), model hỏng bị bỏ qua tạm, đổi model trong Cài đặt áp dụng ngay + đánh thức job chờ soát;
(3) chạy lại sau lỗi/restart chỉ dịch câu còn thiếu."""
import json
import re
from pathlib import Path

import pytest

from engines.translate_simple import cfg, stage_review


def _cfg(monkeypatch, d):
    monkeypatch.setattr(cfg, "get", lambda k, default=None: d.get(k, default))


def _no_retry(monkeypatch):
    import engines.cloud_retry as cr
    monkeypatch.setattr(cr, "call_with_cloud_retry", lambda fn, **kw: fn())


def _chat(monkeypatch, bad: set, used: list):
    import engines.llm as llm

    def chat(msgs, model=None, **kw):
        used.append(model)
        if model in bad:
            raise ConnectionError(f"{model} 402 Insufficient Balance")
        return json.dumps({"lines": [{"i": 1, "vi": f"ok từ {model}"}]})
    monkeypatch.setattr(llm, "chat", chat)


# ── (2) chuỗi dự phòng ──
def test_fallback_chain_configured_then_local(monkeypatch):
    _cfg(monkeypatch, {"SIMPLE_FALLBACK_MODEL": "alibaba/qwen-plus"})
    cfg._LOCAL_PROBE.update(t=float("inf"), ok=True)
    assert cfg.fallback_models("deepseek/x") == ["alibaba/qwen-plus", "local"]
    _cfg(monkeypatch, {"SIMPLE_FALLBACK_MODEL": "alibaba/qwen-plus", "SIMPLE_LOCAL_FALLBACK": "0"})
    assert cfg.fallback_models("deepseek/x") == ["alibaba/qwen-plus"]
    _cfg(monkeypatch, {})
    assert cfg.fallback_models("local") == []                     # chính đã là AI máy → không lặp
    cfg._LOCAL_PROBE.update(t=float("inf"), ok=False)
    assert cfg.fallback_models("deepseek/x") == [] and cfg.fallback_model("deepseek/x") is None


def test_no_backup_configured_uses_local_llama(monkeypatch):
    _cfg(monkeypatch, {})
    cfg._LOCAL_PROBE.update(t=float("inf"), ok=True)
    _no_retry(monkeypatch)
    used = []
    _chat(monkeypatch, {"deepseek/x"}, used)
    out = stage_review.call_llm("s", "u", {1}, model="deepseek/x")
    assert out[1]["vi"] == "ok từ local" and used == ["deepseek/x", "local"]


def test_dead_primary_skipped_for_next_lots(monkeypatch):
    _cfg(monkeypatch, {"SIMPLE_FALLBACK_MODEL": "alibaba/qwen-plus"})
    _no_retry(monkeypatch)
    used = []
    _chat(monkeypatch, {"deepseek/x"}, used)
    stage_review.call_llm("s", "u", {1}, model="deepseek/x")
    stage_review.call_llm("s", "u", {1}, model="deepseek/x")
    assert used == ["deepseek/x", "alibaba/qwen-plus", "alibaba/qwen-plus"]   # lô 2 không chờ model hỏng lại


def test_backup_also_dead_falls_to_local(monkeypatch):
    _cfg(monkeypatch, {"SIMPLE_FALLBACK_MODEL": "alibaba/qwen-plus"})
    cfg._LOCAL_PROBE.update(t=float("inf"), ok=True)
    _no_retry(monkeypatch)
    used = []
    _chat(monkeypatch, {"deepseek/x", "alibaba/qwen-plus"}, used)
    assert stage_review.call_llm("s", "u", {1}, model="deepseek/x")[1]["vi"] == "ok từ local"
    assert used == ["deepseek/x", "alibaba/qwen-plus", "local"]


def test_all_dead_raises(monkeypatch):
    _cfg(monkeypatch, {"SIMPLE_FALLBACK_MODEL": "alibaba/qwen-plus"})
    _no_retry(monkeypatch)
    _chat(monkeypatch, {"deepseek/x", "alibaba/qwen-plus"}, [])
    with pytest.raises(ConnectionError):
        stage_review.call_llm("s", "u", {1}, model="deepseek/x")


def test_settings_change_applies_to_next_lot(monkeypatch):
    conf = {"SIMPLE_MODEL": "deepseek/x"}
    _cfg(monkeypatch, conf)
    _no_retry(monkeypatch)
    used = []
    _chat(monkeypatch, set(), used)
    stage_review.call_llm("s", "u", {1}, model="deepseek/x")
    conf["SIMPLE_MODEL"] = "alibaba/qwen-plus"                 # người dùng đổi trong Cài đặt giữa chừng
    stage_review.call_llm("s", "u", {1}, model="deepseek/x")      # pipeline vẫn cầm model cũ
    stage_review.call_llm("s", "u", {1}, model="deepseek/x")
    stage_review.call_llm("s", "u", {1}, model="local")           # dự phòng gọi có chủ đích → giữ nguyên
    assert used == ["deepseek/x", "alibaba/qwen-plus", "alibaba/qwen-plus", "local"]


# ── (3) làm tiếp sau lỗi / restart ──
def test_rerun_only_translates_missing_and_says_so(tmp_path, monkeypatch):
    import engines.llm as llm
    import engines.translate as T
    from engines.translate_simple import pipeline
    wd = tmp_path / "job"
    wd.mkdir()
    segs = [{"i": i, "start": i * 2.0, "end": i * 2 + 1.5, "text": f"你好世界第{i}句"} for i in range(40)]
    (wd / "asr.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
    (wd / "manifest.json").write_text(json.dumps({"stages": {"asr": {"status": "done"}}}), encoding="utf-8")
    monkeypatch.setattr(T, "build_plan", lambda segments, **k: {"characters": [{"name": "A", "vi": "A"}],
                                                                  "terms": [], "pronouns": [], "scenes": []})
    _no_retry(monkeypatch)
    real_get = cfg.get
    monkeypatch.setattr(cfg, "get", lambda k, d=None: {"SIMPLE_BATCH": "10", "SIMPLE_LOCAL_FALLBACK": "0",
                                                       "SIMPLE_FALLBACK_MODEL": ""}.get(k, real_get(k, d)))
    st = {"n": 0, "fail_after": 1, "ids": []}

    def chat(msgs, model=None, **kw):
        ids = [int(x) for x in re.findall(r'"i"\s*:\s*(\d+)', msgs[-1]["content"])]
        st["n"] += 1
        if st["fail_after"] is not None and st["n"] > st["fail_after"]:
            raise ConnectionError("402 Insufficient Balance")
        st["ids"] += ids
        return json.dumps({"lines": [{"i": i, "vi": f"Câu {i}", "conf": 0.95} for i in ids]})
    monkeypatch.setattr(llm, "chat", chat)
    pipeline.run(wd, "Tiếng Việt")
    done1 = sum(1 for r in json.loads((wd / "script.json").read_text(encoding="utf-8")) if r.get("rv"))
    assert 0 < done1 < 40
    assert stage_review._DEAD                                     # lượt 1: model bị đánh dấu hỏng
    st.update(fail_after=None, ids=[])
    msgs = []
    pipeline.run(wd, "Tiếng Việt", progress=msgs.append)
    assert len(set(st["ids"])) == 40 - done1                      # chỉ gửi AI câu còn thiếu
    assert any(m.startswith(f"làm tiếp: đã dịch {done1}/40") for m in msgs)


# ── (1) bấm Chạy ──
@pytest.fixture
def srv(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(server.job_queue, "WORK_ROOT", tmp_path)
    server._STAGE_BUSY.clear()
    return server


def _mk(tmp_path, jid, agent):
    wd = tmp_path / jid
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"agent": agent, "stages": {}, "last_stop": {"why": "x"}}),
                                      encoding="utf-8")
    return wd


def _agent(wd):
    return json.loads((wd / "manifest.json").read_text(encoding="utf-8"))


def test_user_run_clears_system_parks_only(srv, tmp_path):
    a = _mk(tmp_path, "a", {"held_at": "review_failed", "hold_after_translate": True, "review_retries": 4,
                            "hold_reason": "REVIEW thất bại"})
    b = _mk(tmp_path, "b", {"held_at": "waiting_review", "review_retry_at": 9e9, "simple_park": True})
    c = _mk(tmp_path, "c", {"held_at": "qa_brain", "qa_blocked": True})
    assert srv._clear_system_park(a) and srv._clear_system_park(b)
    da, db = _agent(a), _agent(b)
    assert "held_at" not in da["agent"] and da["agent"]["hold_after_translate"] is False
    assert "review_retries" not in da["agent"] and "last_stop" not in da
    assert "held_at" not in db["agent"] and "review_retry_at" not in db["agent"]
    assert not srv._clear_system_park(c)
    assert _agent(c)["agent"]["held_at"] == "qa_brain" and _agent(c)["agent"]["qa_blocked"] is True


def test_user_run_resumes_paused_queue(srv, tmp_path, monkeypatch):
    _mk(tmp_path, "a", {})
    calls = []
    monkeypatch.setattr(srv.job_queue, "queue_add",
                        lambda ids, **k: {"ok": True, "queued": ids, "paused": {"reason": "3 lỗi liên tiếp"}})
    monkeypatch.setattr(srv.job_queue, "queue_resume", lambda: calls.append(1) or {"ok": True, "resumed": True})
    r = srv.api_queue_jobs_add(srv.JobQueueReq(job_ids=["a"]))
    assert calls == [1] and r["resumed_from_pause"] is True


def test_ai_change_wakes_review_parks(srv, tmp_path, monkeypatch):
    from services import translate_wake
    _mk(tmp_path, "a", {"held_at": "waiting_review", "review_retry_at": 9e9})
    _mk(tmp_path, "b", {"held_at": "qa_brain", "qa_blocked": True})
    added = []
    monkeypatch.setattr(srv.job_queue, "queue_add", lambda ids, **k: added.extend(ids) or {"queued": ids})
    assert translate_wake.on_ai_changed(["PLAN_MODE"]) == []          # không phải model/key → không đụng
    assert translate_wake.on_ai_changed(["SIMPLE_MODEL"]) == ["a"]
    assert added == ["a"] and "held_at" not in _agent(tmp_path / "a")["agent"]


# ── Aurora «có địa chỉ» ≠ «đang chạy»: bộ DeepSeek không giao plan cho Aurora khi Aurora không trả lời ──
def test_aurora_reachable_probe(monkeypatch):
    import requests
    from core import ai_models
    assert ai_models._aurora_reachable("") is False

    def boom(*a, **k):
        raise requests.ConnectionError("WinError 10061")
    monkeypatch.setattr(requests, "get", boom)
    assert ai_models._aurora_reachable("http://127.0.0.1:3377/v1") is False

    class R:
        status_code = 200
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    assert ai_models._aurora_reachable("http://127.0.0.1:3377/v1") is True


def test_deepseek_card_avoids_aurora_when_unreachable():
    js = (Path(__file__).resolve().parents[1] / "desktop" / "ui" / "app.js").read_text(encoding="utf-8")
    assert 'p.id === "deepseek" && !ai.aurora?.reachable ? "deepseek" : p.preset' in js


# ── sót chữ Hán sau mọi lượt vá: gỡ chữ Hán, chạy tiếp (cờ mềm) thay vì park cả job ──
def test_residual_cjk_stripped_when_few(tmp_path, monkeypatch):
    from engines.translate_simple import pipeline
    from engines.translate_simple.script_store import ScriptStore
    wd = tmp_path / "j"
    wd.mkdir()
    st = ScriptStore(wd)
    st.rows = [{"i": 0, "zh": "县主", "vi": "Huyện chủ 县主 đến rồi", "rv": 1},
               {"i": 1, "zh": "好", "vi": "好", "rv": 1},
               {"i": 2, "zh": "你", "vi": "Chào", "rv": 1}]
    _cfg(monkeypatch, {})
    flags = {0: [{"code": "cjk"}], 1: [{"code": "cjk"}]}
    done = pipeline._strip_residual_cjk(st, flags)
    assert done == [0] and st.rows[0]["vi"] == "Huyện chủ đến rồi" and st.rows[0]["cjk_stripped"] == 1
    assert [f["code"] for f in flags[0]] == ["cjk_stripped"]
    assert flags[1] == [{"code": "cjk"}]                  # gỡ xong không còn chữ Việt → giữ cờ, cổng park như cũ
    from engines.translate_simple import gate
    g = gate.decide(n_rows=3, n_asr=3, coverage_gap=0, n_missing=0, n_unreviewed=0, flags={0: flags[0]})
    assert g["status"] == "done"


def test_residual_cjk_not_stripped_when_many_or_off(tmp_path, monkeypatch):
    from engines.translate_simple import pipeline
    from engines.translate_simple.script_store import ScriptStore
    st = ScriptStore(tmp_path)
    st.rows = [{"i": k, "vi": f"Câu {k} 好"} for k in range(20)]
    flags = {k: [{"code": "cjk"}] for k in range(20)}
    _cfg(monkeypatch, {"SIMPLE_CJK_STRIP_MAX": "5"})
    assert pipeline._strip_residual_cjk(st, flags) == []
    _cfg(monkeypatch, {"SIMPLE_CJK_STRIP": "0"})
    assert pipeline._strip_residual_cjk(st, dict(list(flags.items())[:2])) == []


def test_video_advanced_fields_keep_labels():
    js = (Path(__file__).resolve().parents[1] / "desktop" / "ui" / "app.js").read_text(encoding="utf-8")
    assert ".map((k) => cfgField(k)).join" in js and ".map(cfgField)" not in js


# ── «Chạy tiếp» khi job đang GIỮ GHẾ hàng chờ (HOLD qa_brain) ──
def test_resume_works_while_job_holds_queue_seat(srv, tmp_path, monkeypatch):
    from http_routes import routes_agent
    wd = _mk(tmp_path, "h1", {"held_at": "qa_brain", "qa_blocked": True, "hold_reason": "Second Brain: 1 high QA"})
    monkeypatch.setattr(routes_agent.S, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(srv.job_queue, "queue_state", lambda: {"current": "h1", "queue": ["h1"]})
    added = []
    monkeypatch.setattr(srv.job_queue, "queue_add", lambda *a, **k: added.append(a) or {"queued": []})
    r = routes_agent._agent_resume("h1")
    ag = _agent(wd)["agent"]
    assert r["ok"] is True and added == []
    assert ag["qa_blocked"] is False and ag["qa_override"] is True and "held_at" not in ag


def test_resume_refused_when_stage_really_running(srv, tmp_path, monkeypatch):
    from http_routes import routes_agent
    _mk(tmp_path, "h2", {"held_at": "qa_brain"})
    monkeypatch.setattr(routes_agent.S, "WORK_ROOT", tmp_path)
    srv._STAGE_BUSY.add(("h2", "tts"))
    try:
        assert routes_agent._agent_resume("h2")["ok"] is False
    finally:
        srv._STAGE_BUSY.discard(("h2", "tts"))


def test_tts_device_parsed_from_worker_line():
    from engines.clone_tts import tts_device_from_line
    g = tts_device_from_line("vieneu ready backend=pytorch engine.device=cuda")
    assert g["gpu"] is True and g["label"] == "GPU (pytorch)"
    c = tts_device_from_line("vieneu ready backend=onnx engine.device=cpu")
    assert c["gpu"] is False and c["label"].startswith("CPU")


def test_tts_engine_cfg_follows_settings_without_restart(monkeypatch):
    from engines import clone_tts as ct
    import core.config as C
    conf = {"VIENEU_DEVICE": "cuda", "VIENEU_BATCH": "24", "VIENEU_PY": "/a/python"}
    monkeypatch.setattr(C, "get", lambda k, d=None: conf.get(k, d))
    monkeypatch.delenv("VIENEU_BATCH", raising=False)
    ct._refresh_engine_cfg()
    assert ct.ENGINE_CFG["vieneu"]["device"] == "cuda" and ct.ENGINE_CFG["vieneu"]["py"] == "/a/python"
    conf.update(VIENEU_DEVICE="cpu", VIENEU_BATCH="8", VIENEU_PY="/b/python")   # đổi trong Cài đặt
    ct._refresh_engine_cfg()
    v = ct.ENGINE_CFG["vieneu"]
    assert v["device"] == "cpu" and v["batch_size"] == 8 and v["py"] == "/b/python"


def test_ffmpeg_passthrough_args_follow_ffmpeg_version(monkeypatch):
    import subprocess
    from engines import ocr

    class R:
        def __init__(self, out):
            self.stdout = out
    for out, want in (("-fps_mode[:stream_specifier] mode", ["-fps_mode", "passthrough"]),
                      ("-vsync  video sync method", ["-vsync", "0"])):
        monkeypatch.setattr(ocr, "_FPS_MODE", None)
        monkeypatch.setattr(subprocess, "run", lambda *a, _o=out, **k: R(_o))
        assert ocr.passthrough_frames_args() == want


# ── port V2 11-23 / 11-27 ──
def test_asr_salvage_assigns_indices():
    from engines.asr.asr import ensure_asr_indices
    rows = [{"start": 0, "end": 1, "text": "a"}, {"start": 1, "end": 2, "text": "b"}]
    assert [r["i"] for r in ensure_asr_indices(rows)] == [0, 1]


def test_store_asr_without_i_uses_order(tmp_path):
    from engines.translate_simple.script_store import ScriptStore
    st = ScriptStore(tmp_path)
    rows = st.ensure_from_asr([{"start": 0, "end": 1, "text": "你好"}, {"start": 1, "end": 2, "text": "再见"}])
    assert [r["i"] for r in rows] == [0, 1] and rows[1]["zh"]


def test_preflight_lock_dead_owner_is_stale(tmp_path, monkeypatch):
    import psutil
    from engines import source_preflight as sp
    p = tmp_path / "lock"
    p.write_text("999999")
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    assert sp._lock_owner_dead(p) is True
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    assert sp._lock_owner_dead(p) is False


# ── «Phim chờ làm»: Hongguo 2 không trả lời → nhớ thư mục lần trước; phim ở thư mục ngoài gốc vẫn hiện ──
def test_disk_library_falls_back_to_last_hg_folder(tmp_path, monkeypatch):
    from services import disk_library as dl
    lib = tmp_path / "HG"
    (lib / "Phim A").mkdir(parents=True)
    (lib / "Phim A" / "Phim A (merged).mp4").write_bytes(b"x" * 10)
    monkeypatch.setattr(dl, "_LAST_HG", tmp_path / "last.json")
    monkeypatch.setattr(dl, "_HG_CACHE", {"at": 0.0, "val": ({}, None, "")})
    monkeypatch.setattr(dl, "_hg_library", lambda: ({}, lib, ""))           # lần 1: Hongguo trả thư mục
    dl._HG_CACHE["at"] = 0.0
    dl._hg_library_cached(0)
    monkeypatch.setattr(dl, "_hg_library", lambda: ({}, None, "rpc down"))  # lần 2: Hongguo không trả lời
    dl._HG_CACHE["at"] = 0.0          # ép đọc lại (đồng hồ Windows ~15 ms: 2 lần gọi liền có thể cùng thời điểm)
    by, root, err = dl._hg_library_cached(0)
    assert root == lib and "lần trước" in err


def test_disk_library_missing_dir_flag(tmp_path, monkeypatch):
    from services import disk_library as dl
    monkeypatch.setattr(dl, "_hg_library_cached", lambda *a, **k: ({}, None, ""))
    r = dl.scan(lib=tmp_path / "khong_co")
    assert r["ok"] is False and r["missing_dir"] is True


def test_no_hardcoded_v2_accounts():
    """2026-11-27: films UI / test channel / lib_check không còn tên kênh + Gmail của V2 ghi cứng."""
    import re
    root = Path(__file__).resolve().parents[1]
    for rel in ("static/ui/films_ui.html", "static/films_ui.html", "films_ui.html", "tools/lib_check.py",
                "tools/test_channel.py", "agents/test_channel.py"):
        src = (root / rel).read_text(encoding="utf-8")
        code = re.sub(r"#.*|«[^»]*»", "", src)          # bỏ chú thích lịch sử
        assert "@gmail.com" not in src, rel
        assert "'Sai Kira'" not in code and '"Sai Kira"' not in code and "Sage Review 100%" not in code, rel


def test_lib_check_accounts_from_v6(monkeypatch):
    from tools import lib_check
    from services import yt_accounts
    monkeypatch.setattr(yt_accounts, "list_accounts", lambda: {"accounts": [{"id": "UC1", "title": "Kênh A"}]})
    assert lib_check.list_yt_accounts()[0]["channel_title"] == "Kênh A"
    monkeypatch.setattr(yt_accounts, "list_accounts", lambda: {"accounts": []})
    monkeypatch.setattr(lib_check, "YT_DIR", Path("/khong/co/yt_uploader"))
    assert lib_check.list_yt_accounts() == []
