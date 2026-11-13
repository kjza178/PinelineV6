"""F1 gate: không còn Manifest.save() ngoài state/; số js.persist chỉ được GIẢM (F2–F5 thay bằng hàm ngữ nghĩa)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = ("state", "tests", "_to_delete", "work", "vendor", "node_modules", "desktop", "scratch")
PERSIST_BASELINE = 91          # 11-12 P6: agent_brain rule CLEAR → js.mutate (92→91; F5: 98→93). Chỉ GIẢM.
# 11-12 agentJ: thư mục nháp/backup của agent ngoài cây code (.backup_edit_*, _scratch_*, scratch*, _to_delete*).
SKIP_PREFIX = (".backup", "_scratch", "scratch", "_to_delete")
NEW_LAYERS = ("app", "pipeline", "workers", "events", "runtime", "services", "sidecar")


def _py_files():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).parts
        if rel[0].startswith(SKIP_PREFIX) or rel[0] in SKIP or p.name == "f1_convert_saves.py":
            continue
        yield p


def _code(p):
    # bỏ comment/docstring đơn giản: chỉ xét dòng không bắt đầu bằng #
    return "\n".join(ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
                     if not ln.lstrip().startswith("#"))


def test_no_manifest_save_outside_state():
    pat = re.compile(r"\b\w+\.save\(\)")
    hits = []
    for p in _py_files():
        if p.relative_to(ROOT).as_posix() == "core/manifest.py":
            continue
        for m in pat.finditer(_code(p)):
            hits.append(f"{p.relative_to(ROOT)}: {m.group(0)}")
    assert hits == [], "Dùng state.job_state thay cho .save():\n" + "\n".join(hits)


def test_persist_only_decreases():
    n = sum(_code(p).count("js.persist(") for p in _py_files())
    assert n <= PERSIST_BASELINE, f"js.persist tăng lên {n} (> {PERSIST_BASELINE}) — dùng hàm ngữ nghĩa trong state.job_state"


def test_new_layers_do_not_touch_manifest_directly():
    bad = []
    for layer in NEW_LAYERS:
        d = ROOT / layer
        if not d.is_dir():
            continue
        for p in d.rglob("*.py"):
            c = _code(p)
            if "Manifest(" in c or "js.persist(" in c or "_commit(" in c:
                bad.append(str(p.relative_to(ROOT)))
    assert bad == [], bad
