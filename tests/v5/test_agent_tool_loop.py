"""Unit tests for agent_tool_loop (skip-if-done, wait terminal, pipeline)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

# V6: chỉ dùng code của cây này (bỏ fallback chèn Z:\PinelineV2 vào sys.path — trộn code V2 vào test V6)
from agents.agent_tool_loop import pipeline_loop, run_stage_wait, stage_snapshot, wait_stage


def _write_manifest(work: Path, job: str, stages: dict) -> Path:
    d = work / job
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps({"stages": stages}, ensure_ascii=False), encoding="utf-8"
    )
    return d


def test_skip_when_done_no_force(tmp_path: Path):
    job = "j1"
    _write_manifest(tmp_path, job, {"translate": {"status": "done", "progress": 100}})
    calls = []

    def run(jid, stage, opts):
        calls.append((jid, stage, opts))
        return {"started": True}

    r = run_stage_wait(run, tmp_path, job, "translate", {"force": False})
    assert r["ok"] and r.get("skipped")
    assert calls == []


def test_force_starts_even_if_done(tmp_path: Path):
    job = "j2"
    _write_manifest(tmp_path, job, {"tts": {"status": "done"}})

    def run(jid, stage, opts):
        # mark running then done quickly via disk
        mf = tmp_path / jid / "manifest.json"
        data = json.loads(mf.read_text(encoding="utf-8"))
        data["stages"][stage] = {"status": "running", "progress": 10}
        mf.write_text(json.dumps(data), encoding="utf-8")

        def _finish():
            time.sleep(0.15)
            data2 = json.loads(mf.read_text(encoding="utf-8"))
            data2["stages"][stage] = {"status": "done", "progress": 100}
            mf.write_text(json.dumps(data2), encoding="utf-8")

        import threading
        threading.Thread(target=_finish, daemon=True).start()
        return {"started": True}

    r = run_stage_wait(run, tmp_path, job, "tts", {"force": True}, timeout_sec=5, poll_sec=0.05)
    assert r["ok"]
    assert not r.get("skipped")
    assert (r.get("final") or {}).get("status") == "done"


def test_pipeline_stops_on_fail(tmp_path: Path):
    job = "j3"
    _write_manifest(tmp_path, job, {
        "translate": {"status": "done"},
        "tts": {"status": "pending"},
    })

    def run(jid, stage, opts):
        mf = tmp_path / jid / "manifest.json"
        data = json.loads(mf.read_text(encoding="utf-8"))
        data["stages"][stage] = {"status": "failed", "error": "boom"}
        mf.write_text(json.dumps(data), encoding="utf-8")
        return {"started": True}

    r = pipeline_loop(
        run, tmp_path, job, ["translate", "tts", "mux"],
        {"force": False}, timeout_sec=5, poll_sec=0.05,
    )
    assert r.get("failed_stage") == "tts"
    assert len(r["results"]) == 2  # translate skipped, tts failed — no mux
    assert r["results"][0].get("skipped")


def test_wait_detects_stop_to_pending(tmp_path: Path):
    job = "j4"
    _write_manifest(tmp_path, job, {"mux": {"status": "running", "progress": 50}})

    def flip():
        time.sleep(0.1)
        mf = tmp_path / job / "manifest.json"
        data = json.loads(mf.read_text(encoding="utf-8"))
        data["stages"]["mux"] = {"status": "pending", "cur_text": "stopped"}
        mf.write_text(json.dumps(data), encoding="utf-8")

    import threading
    threading.Thread(target=flip, daemon=True).start()
    r = wait_stage(tmp_path, job, "mux", timeout_sec=3, poll_sec=0.05)
    assert r.get("stopped")
    assert not r.get("ok")
