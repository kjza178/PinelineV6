"""Smoke test agent_core (đọc work/ trực tiếp, không cần restart server)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import agents.agent_core as agent_core
from core.manifest import Manifest

WORK = ROOT / "work"
WIRED = agent_core.WIRED


def _read_job(work_dir: Path) -> dict:
    m = Manifest(work_dir)
    stages = m.data.get("stages") or {}
    dl = stages.get("download") or {}
    req = m.data.get("request") or {}
    done = sum(1 for s in WIRED if (stages.get(s) or {}).get("status") == "done")
    if any((stages.get(s) or {}).get("status") == "failed" for s in WIRED):
        status = "failed"
    elif done == len(WIRED):
        status = "done"
    elif any((stages.get(s) or {}).get("status") == "running" for s in WIRED):
        status = "running"
    else:
        status = "pending"
    return {
        "job_id": work_dir.name,
        "title": m.data.get("title_vi") or dl.get("title") or req.get("title") or work_dir.name,
        "status": status,
        "overall": int(done / len(WIRED) * 100),
        "stages": {s: stages.get(s) or {} for s in WIRED},
        "group_id": req.get("group_id"),
        "group_title": req.get("group_title"),
    }


def list_jobs():
    out = []
    if not WORK.exists():
        return out
    for d in sorted(WORK.iterdir()):
        if (d / "manifest.json").is_file():
            out.append(_read_job(d))
    return out


def main():
    agent_core.bind(agent_core.Runtime(
        work_root=WORK,
        list_jobs=list_jobs,
        get_job=lambda jid: _read_job(WORK / jid),
        queue_state=lambda: {"queue": [], "current": None, "history": [], "note": "offline-test"},
        queue_add=lambda ids, opts: {"ok": False, "note": "offline-test blocked", "ids": ids},
        queue_stop=lambda: {"ok": False, "note": "offline-test"},
        list_voices=lambda: {"vieneu": [{"id": "v5"}]},
    ))
    snap = agent_core.status_snapshot()
    print("SNAPSHOT", json.dumps(snap["counts"], ensure_ascii=False))
    r = agent_core.run_agent(
        "Tóm tắt jobs đang chạy và need_queue. Không xếp hàng.",
        allow_mutate=False,
        max_steps=6,
    )
    print("MODEL", r.model)
    print("STEPS", len(r.steps), [s["tool"] for s in r.steps])
    print("REPLY", r.reply[:800])


if __name__ == "__main__":
    main()
