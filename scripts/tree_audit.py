"""tree_audit.py — Kiểm tra sức khoẻ cây code Pineline (V2/V4/V5) sau mỗi lần sync/port.

Chỉ đọc (AST + hash), không import, không chạy code. Bắt các lỗi kiểu:
  • import không resolve được trong cây này  → server/sidecar không khởi động (V5 11/2026)
  • cổng cứng của cây khác (vd 8801 trong V4) → agent điều khiển nhầm server khác
  • cùng tên module ở root và package, nội dung khác → sửa một bản, chạy bản kia
  • file giống hệt cây khác ở chỗ đáng lẽ khác (docs/STATE.md, server.py của V5) → sync đè

Dùng:
  python scripts/tree_audit.py                       # cây hiện tại
  python scripts/tree_audit.py --peers Z:\\PinelineV2 Z:\\PinelineV4   # + so với cây khác
  python scripts/tree_audit.py --write               # ghi docs/brain/generated/tree-audit.md
Exit code 1 nếu có lỗi mức ERROR (dùng được trong precheck trước restart).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"vendor", "work", "data", "voices", "voice_samples", "finetune_xuyenthanh", "assets", "static",
        "docs", "scratch", "archive", "__pycache__", ".venv", "venv", "node_modules", ".git", ".claude",
        ".cursor", ".agents", ".superpowers", "src-tauri", "desktop", "models", "cookies", "install",
        "claude_setup", "apps", "config", "PineLine", "_to_delete"}
# Thư viện ngoài / Windows-only: không báo thiếu
EXTERNAL = {
    "fastapi", "pydantic", "uvicorn", "starlette", "requests", "psutil", "numpy", "soundfile", "torch",
    "torchaudio", "transformers", "ctranslate2", "funasr", "faster_whisper", "huggingface_hub", "yt_dlp",
    "rapidocr_onnxruntime", "PIL", "cv2", "soxr", "demucs", "qwen_asr", "fireredasr2s", "google",
    "googleapiclient", "google_auth_oauthlib", "clr", "webview", "msvcrt", "winreg", "_winapi", "vieneu",
    "edge_tts", "streamlit", "win32pdh", "pyarrow", "onnxruntime", "librosa", "scipy", "pandas", "multipart", "pytest", "pythoncom",
    "win32api", "win32con", "win32gui", "pywintypes", "comtypes", "sentencepiece", "opencc", "jieba",
    "pypinyin", "httpx", "websocket", "websockets", "aiohttp", "dotenv", "omnivoice", "modelscope",
}
PORT_OF = {"PinelineV2": "8801", "PinelineV4": "8804", "PinelineV5": "8805", "Pineline": "8800", "PinelineV6": "8806"}
SHOULD_DIFFER = ("server.py", "docs/STATE.md", "docs/ARCHITECTURE.md", "docs/CHANGELOG.md",
                 "docs/DECISIONS.md", "CLAUDE.md", "README.md")


def md5(p: Path) -> str:
    try:
        return hashlib.md5(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def py_files(root: Path) -> list[Path]:
    out = [p for p in root.glob("*.py") if p.is_file()]
    for d in root.iterdir():
        if d.name in SKIP or d.name.startswith("."):
            continue
        try:
            if not d.is_dir() or d.is_symlink():
                continue
        except OSError:  # junction hỏng / ổ không truy cập được
            continue
        if True:
            for p in d.rglob("*.py"):
                if not any(part in SKIP for part in p.relative_to(root).parts[:-1]):
                    out.append(p)
    return sorted(out)


def module_exists(root: Path, base_dir: Path, dotted: str) -> bool:
    """Có resolve được `dotted` từ sys.path = [thư mục script, ROOT] không."""
    parts = dotted.split(".")
    for base in {base_dir, root}:
        p = base.joinpath(*parts)
        if p.with_suffix(".py").is_file() or (p.is_dir() and (any(p.glob("*.py")))):
            return True
        # import pkg.mod nhưng chỉ cần pkg tồn tại (attr có thể là biến)
        q = base / parts[0]
        if len(parts) > 1 and (q.with_suffix(".py").is_file()):
            return True
    return False


def guarded(node: ast.AST, parents: dict) -> bool:
    """Import nằm trong try/except (ImportError/Exception) hay trong hàm → coi là tuỳ chọn."""
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.Try):
            return True
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return True
        cur = parents.get(cur)
    return False


def audit_imports(root: Path) -> list[tuple[str, str, int, str, str]]:
    """(level, file, line, module, kind) — ERROR: import top-level không resolve; WARN: trong hàm/try."""
    std = set(getattr(sys, "stdlib_module_names", ()))
    res = []
    for p in py_files(root):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(("tests/", "scripts/")) or "/tests/" in rel:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8-sig", errors="replace"))
        except SyntaxError as e:
            res.append(("ERROR", rel, e.lineno or 0, "-", f"SyntaxError {e.msg}"))
            continue
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for n in ast.walk(tree):
            names: list[str] = []
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                names = [n.module]
            for name in names:
                top = name.split(".")[0]
                if top in std or top in EXTERNAL or top == "__future__":
                    continue
                if module_exists(root, p.parent, name):
                    continue
                lvl = "WARN" if guarded(n, parents) else "ERROR"
                res.append((lvl, rel, n.lineno, name, "không resolve trong cây"))
    return res


def audit_ports(root: Path) -> list[tuple[str, str, int, str]]:
    mine = PORT_OF.get(root.name)
    others = {v for k, v in PORT_OF.items() if v != mine}
    res = []
    pat = re.compile(r"(?:127\.0\.0\.1|localhost):(\d{4})")
    for p in py_files(root):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(("tests/", "scripts/")):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            for port in pat.findall(line):
                if port in others:
                    lvl = "ERROR" if ('"http' in line or "'http" in line) and "getenv" not in line else "WARN"
                    res.append((lvl, rel, i, f":{port} (cây này {mine or '?'}) — {s[:110]}"))
    return res


def audit_duplicates(root: Path) -> list[tuple[str, str, str]]:
    by: dict[str, list[Path]] = defaultdict(list)
    for p in py_files(root):
        if p.stem not in ("__init__", "__main__") and not p.relative_to(root).as_posix().startswith("tests/"):
            by[p.stem].append(p)
    res = []
    for stem, ps in sorted(by.items()):
        if len(ps) < 2:
            continue
        hs = {md5(p) for p in ps}
        lvl = "WARN" if len(hs) > 1 else "INFO"
        res.append((lvl, stem, ", ".join(f"{p.relative_to(root).as_posix()}[{md5(p)[:6]}]" for p in ps)))
    return res


def audit_peers(root: Path, peers: list[Path]) -> list[tuple[str, str]]:
    res = []
    for peer in peers:
        if not peer.is_dir() or peer.resolve() == root.resolve():
            continue
        for rel in SHOULD_DIFFER:
            a, b = root / rel, peer / rel
            if a.is_file() and b.is_file() and md5(a) == md5(b):
                res.append(("WARN", f"`{rel}` GIỐNG HỆT {peer.name} — có thể bị sync đè (file này nên riêng từng cây)"))
        mine = {p.name: md5(p) for p in root.glob("*.py")}
        theirs = {p.name: md5(p) for p in peer.glob("*.py")}
        same = sorted(k for k in mine if theirs.get(k) == mine[k])
        diff = sorted(k for k in mine if k in theirs and theirs[k] != mine[k])
        res.append(("INFO", f"so {peer.name}: root .py giống {len(same)}, khác {len(diff)}"
                            + (f" ({', '.join(diff[:25])}{'…' if len(diff) > 25 else ''})" if diff else "")))
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--peers", nargs="*", default=[], help="các cây khác để so (vd Z:\\PinelineV2)")
    ap.add_argument("--write", action="store_true", help="ghi docs/brain/generated/tree-audit.md")
    a = ap.parse_args()
    root = ROOT
    imp, ports, dups = audit_imports(root), audit_ports(root), audit_duplicates(root)
    peers = audit_peers(root, [Path(x) for x in a.peers])
    n_err = sum(1 for r in imp if r[0] == "ERROR") + sum(1 for r in ports if r[0] == "ERROR")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"# Tree audit — {root.name}", "",
         f"_Sinh bởi `scripts/tree_audit.py` lúc {stamp}. ERROR = sẽ hỏng khi chạy; WARN = nên xem._", "",
         f"**ERROR: {n_err}** · import lỗi {len(imp)} · cổng lạ {len(ports)} · module trùng tên {len(dups)}", ""]
    L += ["## Import không resolve", "", "| Mức | File | Dòng | Module |", "|---|---|---:|---|"]
    L += [f"| {lv} | `{f}` | {ln} | `{m}` |" for lv, f, ln, m, _k in sorted(imp, key=lambda r: (r[0] != "ERROR", r[1], r[2]))] or ["| — | | | |"]
    L += ["", "## Cổng của cây khác", "", "| Mức | File | Dòng | Chi tiết |", "|---|---|---:|---|"]
    L += [f"| {lv} | `{f}` | {ln} | {d.replace('|', '/')} |" for lv, f, ln, d in ports] or ["| — | | | |"]
    L += ["", "## Module trùng tên", "", "| Mức | Tên | Bản [md5] |", "|---|---|---|"]
    L += [f"| {lv} | `{s}` | {d} |" for lv, s, d in dups] or ["| — | | |"]
    if peers:
        L += ["", "## So với cây khác", ""] + [f"- **{lv}** {d}" for lv, d in peers]
    out = "\n".join(L) + "\n"
    if a.write:
        g = root / "docs" / "brain" / "generated"
        g.mkdir(parents=True, exist_ok=True)
        (g / "tree-audit.md").write_text(out, encoding="utf-8", newline="\n")
        print(f"→ {g / 'tree-audit.md'}")
    print(f"Tree audit {root.name}: ERROR={n_err} · import={len(imp)} · ports={len(ports)} · dups={len(dups)} · peers={len(peers)}")
    for lv, f, ln, m, _k in imp:
        if lv == "ERROR":
            print(f"  ERROR import {f}:{ln} {m}")
    for lv, f, ln, d in ports:
        if lv == "ERROR":
            print(f"  ERROR port   {f}:{ln} {d[:90]}")
    for lv, d in peers:
        if lv == "WARN":
            print(f"  WARN  {d}")
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
