"""sync_from_v2.py — Đồng bộ code từ cây nguồn (V2, bố cục phẳng) sang cây đích.

Thay cho việc chép tay file root ("Sync V2/V4/V5") — cách đã làm V5 hỏng 11/2026.

  • Đích PHẲNG (V4): chép file runtime có trong allowlist, giữ file riêng của cây.
  • Đích PACKAGE (V5, có core/ engines/ agents/ …): đặt mỗi module vào đúng chỗ trong
    package, viết lại import phẳng → package (`from manifest import` → `from core.manifest import`),
    đổi `Path(__file__).parent` về gốc dự án theo độ sâu, thêm bootstrap sys.path cho script
    chạy trực tiếp, dời bản trùng ở root vào _to_delete/.

Dùng (chạy ở cây ĐÍCH):
  python scripts/sync_from_v2.py --src Z:\\PinelineV2                # dry-run: in kế hoạch
  python scripts/sync_from_v2.py --src Z:\\PinelineV2 --apply        # backup + ghi + dời bản trùng
  python scripts/sync_from_v2.py --src Z:\\PinelineV2 --apply --only translate,server
Sau --apply luôn chạy: python scripts/tree_audit.py   (ERROR phải = 0)

Backup: mọi file bị ghi/dời đều chép trước vào .backup_sync_<YYYYmmdd_HHMM>/ (giữ đường dẫn).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

DST = Path(__file__).resolve().parent.parent

# Thư mục package của đích, theo thứ tự ưu tiên khi một tên có ở nhiều nơi
PKG_DIRS = ("core", "engines/asr", "engines", "agents", "http_routes", "tools")
# File entry / riêng từng cây — không bao giờ chép đè từ nguồn
TREE_SPECIFIC = {"restart_server", "app_desktop", "app", "install"}
# Script vận hành / test rời của V2 — không đồng bộ
OPS_SKIP = {
    "_read_hg_ui_token", "asr_compare", "asr_retry_nano", "backup", "backup_5060ti", "backup_voices",
    "check_cookies", "check_gpu_tts", "cleanup_old_work", "disk_spike_watch", "import_dolly_voices",
    "resplit_subs", "test_asr_compare", "test_asr_spam", "test_hardsub_asr", "test_split_after_translate",
    # B11 (F6): code chết — V6 đã chuyển engines/mux.py vào _to_delete (render_job + render.py thay).
    "mux",
}
# Chỗ ở mới cho module nguồn chưa có trong package đích (khớp tiền tố, theo thứ tự)
NEW_HOME = [
    ("agent_", "agents"), ("auto_publisher", "agents"), ("meta_studio", "agents"), ("canva_", "agents"),
    ("hongguo", "agents"), ("hg_", "agents"), ("films_merge_api", "agents"), ("test_channel", "tools"),
    ("uploader_settings_api", "agents"),
    ("asr", "engines/asr"), ("fun_asr", "engines/asr"), ("nano_fa", "engines/asr"), ("firered", "engines/asr"),
    ("paraformer", "engines/asr"), ("sensevoice", "engines/asr"), ("hybrid_asr", "engines/asr"),
    ("hardsub_asr", "engines/asr"),
    ("routes_", "http_routes"),
    ("config", "core"), ("batch_config", "core"), ("job_queue", "core"), ("manifest", "core"),
    ("fileio", "core"), ("film_db", "core"),
    ("lib_check", "tools"), ("migrate_work", "tools"), ("preset_switcher", "tools"), ("pick_dirs", "tools"),
    ("vram", "engines"),
]
DEFAULT_HOME = "engines"


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else ""


def read(p: Path) -> tuple[str, bool, bool]:
    raw = p.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    t = raw.decode("utf-8-sig")
    crlf = "\r\n" in t
    return t.replace("\r\n", "\n"), crlf, bom


def write(p: Path, text: str, crlf: bool, bom: bool) -> None:
    if crlf:
        text = text.replace("\n", "\r\n")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))


def is_package_tree(root: Path) -> bool:
    return (root / "core").is_dir() and (root / "engines").is_dir()


def package_index(root: Path) -> dict[str, list[str]]:
    """stem → [đường dẫn tương đối trong package] (một tên có thể ở nhiều chỗ)."""
    idx: dict[str, list[str]] = {}
    for d in PKG_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.glob("*.py")):
            if p.stem == "__init__":
                continue
            idx.setdefault(p.stem, []).append(p.relative_to(root).as_posix())
    return idx


def home_for(stem: str) -> str:
    for prefix, home in NEW_HOME:
        if stem == prefix or stem.startswith(prefix):
            return home
    return DEFAULT_HOME


# ───────────────────────── biến đổi mã ─────────────────────────

def _line_offsets(text: str) -> list[int]:
    offs, n = [0], 0
    for line in text.split("\n"):
        n += len(line) + 1
        offs.append(n)
    return offs


def rewrite_imports(text: str, dotted: dict[str, str]) -> tuple[str, int]:
    """import phẳng → package theo `dotted` (stem → 'core.manifest'). Trả (text, số chỗ đổi)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text, 0
    offs = _line_offsets(text)
    edits: list[tuple[int, int, str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            if not any(a.name.split(".")[0] in dotted for a in n.names):
                continue
            parts = []
            for a in n.names:
                top, _, rest = a.name.partition(".")
                if top in dotted:
                    target = dotted[top] + ("." + rest if rest else "")
                    parts.append(f"import {target} as {a.asname or top}" if not rest or a.asname
                                 else f"import {target}")
                else:
                    parts.append(f"import {a.name}" + (f" as {a.asname}" if a.asname else ""))
            new = "; ".join(parts)
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            top, _, rest = n.module.partition(".")
            if top not in dotted:
                continue
            seg_start = offs[n.lineno - 1] + n.col_offset
            seg_end = offs[n.end_lineno - 1] + n.end_col_offset
            seg = text[seg_start:seg_end]
            new = re.sub(rf"^from\s+{re.escape(n.module)}\b", f"from {dotted[top]}{'.' + rest if rest else ''}",
                         seg, count=1)
            if new == seg:
                continue
            edits.append((seg_start, seg_end, new))
            continue
        else:
            # importlib.import_module("x") với chuỗi hằng
            if (isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)):
                fn = n.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                val = n.args[0].value
                if name == "import_module" and val.split(".")[0] in dotted:
                    a0 = n.args[0]
                    s = offs[a0.lineno - 1] + a0.col_offset
                    e = offs[a0.end_lineno - 1] + a0.end_col_offset
                    top, _, rest = val.partition(".")
                    edits.append((s, e, json.dumps(dotted[top] + ("." + rest if rest else ""))))
            continue
        s = offs[n.lineno - 1] + n.col_offset
        e = offs[n.end_lineno - 1] + n.end_col_offset
        edits.append((s, e, new))
    for s, e, new in sorted(edits, reverse=True):
        text = text[:s] + new + text[e:]
    return text, len(edits)


_FILE_PATH_RE = re.compile(r"\bPath\(__file__\)(\.resolve\(\)|\.absolute\(\))?((?:\.parent)+)")
_OS_DIRNAME_RE = re.compile(r"os\.path\.dirname\(\s*os\.path\.(?:abspath|realpath)\(\s*__file__\s*\)\s*\)")


def rewrite_file_paths(text: str, depth: int) -> tuple[str, int, int]:
    """Module ở root được chuyển xuống `depth` cấp: thư mục của file == gốc dự án → giữ ý nghĩa đó."""
    if depth <= 0:
        return text, 0, 0
    n = 0

    def sub_path(m: re.Match) -> str:
        nonlocal n
        n += 1
        k = m.group(2).count(".parent")
        return f"Path(__file__).resolve().parents[{depth + k - 1}]"

    text = _FILE_PATH_RE.sub(sub_path, text)

    def sub_os(m: re.Match) -> str:
        nonlocal n
        n += 1
        return "os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__))" + ', ".."' * depth + "))"

    text = _OS_DIRNAME_RE.sub(sub_os, text)
    left = len(re.findall(r"__file__", text)) - text.count("Path(__file__).resolve().parents[") \
        - text.count("os.path.dirname(os.path.abspath(__file__))")
    return text, n, max(0, left)


BOOT_MARK = "# sync_from_v2: bootstrap"


def add_bootstrap(text: str, depth: int) -> tuple[str, bool]:
    """Cho phép `python engines/asr/x.py` import `core.*` khi chạy trực tiếp."""
    if depth <= 0 or BOOT_MARK in text or "__main__" not in text:
        return text, False
    boot = (
        f"import sys as _sync_sys  {BOOT_MARK} — chạy trực tiếp vẫn import được core.*/engines.*\n"
        "from pathlib import Path as _SyncP\n"
        f"_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[{depth}])\n"
        "if _SYNC_ROOT not in _sync_sys.path:\n"
        "    _sync_sys.path.insert(0, _SYNC_ROOT)\n"
    )
    lines = text.split("\n")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text, False
    insert_at = 0
    for node in tree.body:
        is_doc = isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) \
            and isinstance(node.value.value, str)
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        if is_doc or is_future:
            insert_at = node.end_lineno
            continue
        break
    lines[insert_at:insert_at] = boot.rstrip("\n").split("\n")
    return "\n".join(lines), True


# ───────────────────────── kế hoạch ─────────────────────────

def build_plan(src: Path, dst: Path, only: set[str] | None) -> dict:
    pkg = is_package_tree(dst)
    idx = package_index(dst) if pkg else {}
    src_mods = sorted(p.stem for p in src.glob("*.py"))
    items = []
    dotted: dict[str, str] = {}
    for stem in src_mods:
        if stem in TREE_SPECIFIC or stem in OPS_SKIP:
            continue
        if stem == "server":
            targets = ["server.py"]
        elif not pkg:
            targets = [f"{stem}.py"]
        else:
            targets = idx.get(stem) or [f"{home_for(stem)}/{stem}.py"]
            # dotted ưu tiên bản đầu (thứ tự PKG_DIRS)
            dotted[stem] = targets[0][:-3].replace("/", ".")
        items.append({"stem": stem, "targets": targets})
    # module chỉ có trong package đích (không có ở nguồn) vẫn cần map import
    for stem, paths in idx.items():
        dotted.setdefault(stem, paths[0][:-3].replace("/", "."))
    if only:
        items = [it for it in items if it["stem"] in only]
    return {"package": pkg, "items": items, "dotted": dotted}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="cây nguồn (vd Z:\\PinelineV2)")
    ap.add_argument("--apply", action="store_true", help="ghi thật (mặc định chỉ in kế hoạch)")
    ap.add_argument("--only", help="chỉ các module (phẩy)")
    ap.add_argument("--no-move-dupes", action="store_true", help="không dời bản trùng ở root (V5)")
    a = ap.parse_args()
    src, dst = Path(a.src).resolve(), DST
    if src == dst:
        print("Nguồn trùng đích.")
        return 2
    only = {x.strip() for x in a.only.split(",")} if a.only else None
    plan = build_plan(src, dst, only)
    pkg, dotted = plan["package"], plan["dotted"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    bak = dst / f".backup_sync_{stamp}"
    report = {"src": str(src), "dst": str(dst), "package": pkg, "written": [], "same": [], "warn": [], "moved": []}
    n_imp = n_file = 0

    def backup(p: Path) -> None:
        if p.is_file():
            out = bak / p.relative_to(dst)
            out.parent.mkdir(parents=True, exist_ok=True)
            if not out.exists():
                shutil.copy2(p, out)

    print(f"Sync {src.name} → {dst.name} ({'PACKAGE' if pkg else 'phẳng'}) · {len(plan['items'])} module"
          f"{' · APPLY' if a.apply else ' · dry-run'}")
    for it in plan["items"]:
        text, crlf, bom = read(src / f"{it['stem']}.py")
        for rel in it["targets"]:
            depth = rel.count("/")
            out = text
            if pkg:
                out, k = rewrite_imports(out, dotted)
                n_imp += k
                out, kf, left = rewrite_file_paths(out, depth)
                n_file += kf
                if left:
                    report["warn"].append(f"{rel}: còn {left} chỗ dùng __file__ chưa tự đổi — kiểm tay")
                out, _ = add_bootstrap(out, depth)
                try:
                    ast.parse(out)
                except SyntaxError as e:
                    report["warn"].append(f"{rel}: SyntaxError sau biến đổi dòng {e.lineno}: {e.msg} — BỎ QUA")
                    continue
            target = dst / rel
            new_bytes = ((b"\xef\xbb\xbf" if bom else b"") + (out.replace("\n", "\r\n") if crlf else out).encode("utf-8"))
            if target.is_file() and target.read_bytes() == new_bytes:
                report["same"].append(rel)
                continue
            report["written"].append(rel + ("" if target.exists() else " (mới)"))
            if a.apply:
                backup(target)
                write(target, out, crlf, bom)

    # V5-only package files: viết lại import phẳng còn sót (idempotent)
    if pkg:
        synced = {t for it in plan["items"] for t in it["targets"]}
        extra = [p for d in PKG_DIRS + ("pineline", "tests") for p in (dst / d).rglob("*.py")
                 if (dst / d).is_dir() and "__pycache__" not in p.parts
                 and p.relative_to(dst).as_posix() not in synced]
        for p in sorted(set(extra)):
            text, crlf, bom = read(p)
            out, k = rewrite_imports(text, dotted)
            if k:
                rel = p.relative_to(dst).as_posix()
                report["written"].append(f"{rel} (chỉ sửa {k} import)")
                n_imp += k
                if a.apply:
                    backup(p)
                    write(p, out, crlf, bom)
        # bản trùng ở root
        if not a.no_move_dupes:
            entries = {"server", "app_desktop", "restart_server"}
            for p in sorted(dst.glob("*.py")):
                if p.stem in entries or p.stem not in dotted:
                    continue
                dest = dst / "_to_delete" / f"root_dupes_{stamp}" / p.name
                report["moved"].append(f"{p.name} → {dest.relative_to(dst).as_posix()}")
                if a.apply:
                    backup(p)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(dest))

    print(f"  ghi/cập nhật: {len(report['written'])} · không đổi: {len(report['same'])} · "
          f"dời bản trùng root: {len(report['moved'])} · import viết lại: {n_imp} · __file__ đổi: {n_file}")
    for w in report["written"][:200]:
        print("   +", w)
    for m in report["moved"]:
        print("   →", m)
    for w in report["warn"]:
        print("   ⚠", w)
    if a.apply:
        bak.mkdir(parents=True, exist_ok=True)
        (bak / "sync_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Backup + báo cáo: {bak.relative_to(dst)}  → giờ chạy: python scripts/tree_audit.py")
    else:
        print("(dry-run — thêm --apply để ghi)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
