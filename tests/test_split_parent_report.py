"""2026-11-20: job cha auto-split lỗi → nói part nào chưa xong, vì sao (trước: «Lỗi ở ASR: pipeline kết thúc…»)."""
import json
from pathlib import Path

import pytest

import server
from state.index import summarize


class _SyncExecutor:
    def submit(self, fn):
        fn()


def _write(wd: Path, data: dict) -> Path:
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return wd


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(server.job_queue, "WORK_ROOT", tmp_path)
    server._STOP_JOBS.clear()
    server._STAGE_BUSY.clear()
    yield
    server._STOP_JOBS.clear()
    server._STAGE_BUSY.clear()


def _part(tmp_path, sid, **extra):
    st = {"download": {"status": "done"}, "asr": {"status": "done"}, "translate": {"status": "done"},
          "tts": {"status": "pending"}, "mux": {"status": "pending"}}
    st.update(extra.pop("stages", {}))
    return _write(tmp_path / sid, {"request": {"parent_job_id": "abc"}, "stages": st, **extra})


def test_report_lists_unfinished_parts_with_reason(tmp_path):
    _part(tmp_path, "abc_p02", last_stop={"stage": "translate", "why": "còn 3 câu VI trống"})
    _part(tmp_path, "abc_p03", stages={"tts": {"status": "failed", "error": "CUDA OOM"}})
    _part(tmp_path, "abc_p04")
    rep = server._split_parts_report(["abc_p02", "abc_p03", "abc_p04"])
    assert "p02 dừng ở Dịch: còn 3 câu VI trống" in rep
    assert "p03 lỗi ở Lồng tiếng: CUDA OOM" in rep
    assert "p04 chưa chạy tới Lồng tiếng" in rep


def test_split_parent_failure_is_mux_not_asr(tmp_path, monkeypatch):
    parent = _write(tmp_path / "abc", {
        "request": {"url": "https://example.invalid/v", "target": "vi"},
        "stages": {"download": {"status": "done"}, "asr": {"status": "failed", "error": "cũ"},
                   "tts": {"status": "pending", "voice": "v"}},
        "split_info": {"sub_job_ids": ["abc_p01", "abc_p02"]}})
    _part(tmp_path, "abc_p01")
    _part(tmp_path, "abc_p02", last_stop={"stage": "translate", "why": "dịch chưa đủ"})
    video = parent / "source.mp4"
    video.write_bytes(b"v")
    monkeypatch.setattr(server.job_queue, "is_wired_done", lambda _wd: False)
    monkeypatch.setattr(server, "list_clone_voices", lambda: ["v"])
    monkeypatch.setattr(server, "_job_any_busy", lambda _j: None)
    monkeypatch.setattr(server, "_manifest_running", lambda _wd: None)
    monkeypatch.setattr(server, "_claim_stage", lambda *_a: True)
    monkeypatch.setattr(server, "_next_incomplete_stage", lambda _wd: "asr")
    monkeypatch.setattr(server, "_find_video", lambda _wd: video)
    monkeypatch.setattr(server, "_check_and_run_auto_split_parts", lambda *a, **k: True)
    monkeypatch.setattr(server, "_gpu", _SyncExecutor())

    status, detail = server._queue_run_one("abc", {})
    assert status == "failed"
    assert "part chưa xong" in detail and "p02 dừng ở Dịch: dịch chưa đủ" in detail
    saved = json.loads((parent / "manifest.json").read_text(encoding="utf-8"))
    assert saved["stages"]["mux"]["status"] == "failed"
    assert (saved["stages"].get("asr") or {}).get("status") != "failed"


def test_note_stop_roundtrip_and_summary(tmp_path):
    wd = _part(tmp_path, "abc_p05")
    server._note_stop(wd, "translate", "còn 2 câu VI trống")
    data = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))
    assert data["last_stop"]["why"] == "còn 2 câu VI trống"
    assert summarize("abc_p05", data)["stop_note"] == "còn 2 câu VI trống"
    server._clear_last_stop(wd)
    data = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))
    assert "last_stop" not in data
    assert summarize("abc_p05", data)["stop_note"] == ""


def test_ui_shows_stage_sentence_counts():
    js = (Path(__file__).resolve().parents[1] / "desktop" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "function stageFacts(" in js and "stageFacts(s, st)" in js
    assert "st.n_segments" in js


def test_clear_stop_still_removes_stop_flag(tmp_path):
    """2026-11-21: hàm ghi lý do dừng từng TRÙNG tên `_clear_stop` → che hàm xoá cờ Dừng; «Chạy» không gỡ .stop cũ."""
    wd = tmp_path / "abc_p09"
    wd.mkdir()
    (wd / ".stop").write_text("1", encoding="utf-8")
    server._STOP_JOBS.add("abc_p09")
    server._clear_stop("abc_p09")
    assert not (wd / ".stop").exists() and "abc_p09" not in server._STOP_JOBS
    assert not server._check_stopped("abc_p09")


def test_no_duplicate_top_level_defs_in_server():
    import ast
    src = (Path(server.__file__)).read_text(encoding="utf-8")
    seen, dup = set(), []
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.name in seen and not n.decorator_list:
                dup.append(n.name)
            seen.add(n.name)
    assert not dup, f"hàm trùng tên (bản sau che bản trước): {dup}"


def test_state_write_without_manifest_creates_nothing(tmp_path, monkeypatch):
    from state import job_state as js
    monkeypatch.chdir(tmp_path)
    assert js.try_mutate("ghost_job", lambda d: d.update(x=1)) is False
    assert not (tmp_path / "ghost_job").exists()


def test_render_failure_not_stamped_on_translate(tmp_path, monkeypatch):
    """2026-11-21: render lỗi → «Dịch» (bước đầu của lượt chạy) bị ghi «bước mux lỗi…» + mất trạng thái Xong."""
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    wd = _write(tmp_path / "r1", {
        "request": {"url": "https://example.invalid/v", "target": "vi"},
        "stages": {"download": {"status": "done"}, "asr": {"status": "done"}, "translate": {"status": "pending"},
                   "tts": {"status": "pending", "voice": "v"}, "mux": {"status": "pending"}}})
    video = wd / "source.mp4"
    video.write_bytes(b"v")
    monkeypatch.setattr(server.job_queue, "is_wired_done", lambda _wd: False)
    monkeypatch.setattr(server, "list_clone_voices", lambda: ["v"])
    monkeypatch.setattr(server, "_job_any_busy", lambda _j: None)
    monkeypatch.setattr(server, "_manifest_running", lambda _wd: None)
    monkeypatch.setattr(server, "_claim_stage", lambda *_a: True)
    monkeypatch.setattr(server, "_find_video", lambda _wd: video)
    monkeypatch.setattr(server, "_check_and_run_auto_split_parts", lambda *a, **k: False)

    def fake_gpu(*a, **k):        # dịch + TTS xong, render hỏng
        def fn(d):
            d["stages"]["translate"]["status"] = "done"
            d["stages"]["tts"]["status"] = "done"
            d["stages"]["mux"].update(status="failed", error="render worker thoát mã 2")
        server.js.mutate(wd, fn)
    monkeypatch.setattr(server, "_auto_gpu", fake_gpu)
    monkeypatch.setattr(server, "_gpu", type("E", (), {"submit": staticmethod(lambda fn: fn())})())
    status, detail = server._queue_run_one("r1", {})
    st = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))["stages"]
    assert status == "failed" and "bước mux lỗi" in detail
    assert st["translate"]["status"] == "done" and "error" not in st["translate"]
    assert st["mux"]["error"] == "render worker thoát mã 2"


def test_heal_misattributed_failure(tmp_path):
    wd = _write(tmp_path / "r2", {"stages": {
        "translate": {"status": "failed", "error": "bước mux lỗi: render worker thoát mã 2"},
        "mux": {"status": "failed", "error": "render worker thoát mã 2"}}})
    assert server._heal_misattributed_fail(wd) == ["translate"]
    st = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))["stages"]
    assert st["translate"]["status"] == "pending" and st["mux"]["status"] == "failed"
