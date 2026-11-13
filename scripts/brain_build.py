"""brain_build.py — Sinh/làm mới phần TỰ ĐỘNG của Second Brain (docs/brain/).

Đọc mã nguồn bằng AST (không import, không chạy code) → viết:

  docs/brain/modules/<module>.md     thẻ module: AUTO (sinh lại) + MANUAL (giữ nguyên)
  docs/brain/generated/*.md          index module, config keys, API routes, phụ thuộc,
                                     hotspots, hàm dài, artifact file, cổng
  docs/brain/_meta.json              sha1 từng file nguồn → phát hiện thẻ cũ

Dùng:
  python scripts/brain_build.py              # build toàn bộ (an toàn, chạy bao nhiêu lần cũng được)
  python scripts/brain_build.py --check      # chỉ liệt kê module đổi/ mới/ xoá từ lần build trước
  python scripts/brain_build.py --module translate   # in tóm tắt 1 module ra stdout (cho agent)
  python scripts/brain_build.py --only server,translate   # build lại một số module

Chỉ ghi vào docs/brain/. Không đụng runtime, không cần restart.
Phần giữa <!-- MANUAL:START --> và <!-- MANUAL:END --> trong thẻ module KHÔNG bao giờ bị ghi đè.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:  # console Windows in tiếng Việt
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "docs" / "brain"
MOD_DIR = BRAIN / "modules"
GEN_DIR = BRAIN / "generated"
META = BRAIN / "_meta.json"

# Thư mục con được quét (ngoài *.py ở gốc)
SUB_PACKAGES = ("context_studio", "rvc_bridge", "core", "scripts", "tests")
SKIP_PARTS = {
    "vendor", "work", "data", "voices", "voice_samples", "finetune_xuyenthanh", "assets",
    "static", "install", "docs", "scratch", "archive", "__pycache__", ".venv", "venv",
    "node_modules", ".git", ".claude", ".cursor", ".agents", ".superpowers", "PineLine",
    "src-tauri", "desktop", "models", "cookies", "claude_setup", "config", "apps",
}

# (tiền tố/tên, lớp) — sửa danh sách này khi thêm module mới. Khớp theo thứ tự.
LAYER_RULES: list[tuple[str, str]] = [
    ("tests.", "test"), ("test_channel", "publish"), ("test_", "test"),
    ("scripts.", "ops"), ("context_studio", "context"), ("rvc_bridge", "tts"), ("core.", "core"),
    ("agent_tele_cmd", "sidecar"), ("agent_autopilot", "sidecar"), ("agent_feeder", "sidecar"),
    ("agent_", "agent"),
    ("server", "control"), ("routes_", "control"), ("config", "control"), ("job_queue", "control"),
    ("manifest", "control"), ("batch_config", "control"), ("restart_server", "control"),
    ("fileio", "control"), ("app", "control"),
    ("asr", "asr"), ("fun_asr", "asr"), ("nano_fa", "asr"), ("firered", "asr"), ("paraformer", "asr"),
    ("sensevoice", "asr"), ("hybrid_asr", "asr"), ("hardsub_asr", "asr"), ("separate", "asr"), ("ocr", "asr"),
    ("translate", "translate"), ("llm", "translate"), ("mt_zh_vi", "translate"), ("series_glossary", "translate"),
    ("pronoun", "translate"), ("hv_auditor", "translate"), ("title_mt", "translate"), ("script_lint", "translate"),
    ("clone_tts", "tts"), ("check_gpu_tts", "tts"), ("import_dolly", "tts"),
    ("render", "render"), ("assemble", "render"), ("mux", "render"), ("subs", "render"), ("resplit_subs", "render"),
    ("media_ts", "render"), ("sfx_lib", "render"), ("split_parts", "render"),
    ("hongguo", "source"), ("hg_", "source"), ("_read_hg", "source"), ("download", "source"), ("playlist", "source"),
    ("backup", "ops"), ("check_", "ops"), ("lib_check", "ops"), ("migrate_work", "ops"), ("install", "ops"),
    ("preset_switcher", "ops"), ("pick_dirs", "ops"), ("cleanup_old", "ops"), ("vram", "ops"), ("disk_spike", "ops"),
    ("film_db", "publish"), ("auto_publisher", "publish"), ("meta_studio", "publish"), ("canva", "publish"),
    ("test_channel", "publish"), ("uploader_settings", "publish"), ("films_merge", "publish"),
]
LAYER_ORDER = ["control", "agent", "sidecar", "source", "asr", "translate", "tts", "render",
               "publish", "context", "core", "ops", "misc", "test"]

CFG_FUNCS = {"cfg", "_cfg", "get_cfg", "cfg_int", "cfg_bool", "cfg_float", "cfg_str", "_env",
             "env_int", "env_bool", "env_float", "_env_int", "_env_bool", "_env_float", "_getenv"}
ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "websocket", "api_route"}
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
PORT_RE = re.compile(r"(?:127\.0\.0\.1|localhost|0\.0\.0\.0):(\d{4,5})")
ARTIFACT_RE = re.compile(r"^[\w\-.{}]+\.(json|jsonl|srt|wav|mp4|txt|db|lock|log|ass|m4a)$")
LONG_FN = 120  # dòng

MANUAL_TEMPLATE = """## Vai trò (viết tay)

_Chưa viết. 2–4 câu: module này chịu trách nhiệm gì trong pipeline, ai gọi nó, nó KHÔNG làm gì._

## Luồng chính

_Các hàm vào (entry) → bước chính → output/side effect. Ghi tên hàm thật._

## Bất biến & hợp đồng

_Điều luôn phải đúng (vd: chỉ ghi manifest qua `Manifest.update`; 1 job / ghế). Vi phạm = bug._

## Bẫy đã biết

_Lỗi từng gặp + link `docs/fixes/...`. Những chỗ dễ sửa sai._

## Ghi chú vận hành

_Config key quan trọng, cần restart gì khi sửa (:8801 / :8802 / hot-reload), cách test._
"""


# ───────────────────────────── dữ liệu ─────────────────────────────

@dataclass
class Fn:
    name: str
    line: int
    end: int
    args: str
    doc: str
    is_async: bool = False

    @property
    def length(self) -> int:
        return self.end - self.line + 1


@dataclass
class Mod:
    name: str
    rel: str
    layer: str
    sha1: str
    loc: int = 0
    sloc: int = 0
    doc: str = ""
    syntax_error: str = ""
    functions: list[Fn] = field(default_factory=list)          # top-level
    classes: list[tuple[str, int, int, str]] = field(default_factory=list)  # name, line, n_methods, doc
    all_fns: list[tuple[str, Fn]] = field(default_factory=list)  # (qualname, Fn) gồm method
    imports_int: set[str] = field(default_factory=set)
    imports_ext: set[str] = field(default_factory=set)
    reloads: set[str] = field(default_factory=set)
    routes: list[tuple[str, str, str, int]] = field(default_factory=list)  # method, path, handler, line
    cfg_keys: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    ports: set[str] = field(default_factory=set)
    artifacts: set[str] = field(default_factory=set)
    smells: Counter = field(default_factory=Counter)
    imported_by: set[str] = field(default_factory=set)
    py_refs: set[str] = field(default_factory=set)       # chuỗi "x.py" nhắc trong code (subprocess/script)
    launched_by: set[str] = field(default_factory=set)
    tests: set[str] = field(default_factory=set)
    fix_docs: list[tuple[str, str]] = field(default_factory=list)   # (path rel docs/, title)
    review_docs: list[str] = field(default_factory=list)
    changelog_hits: int = 0
    decision_hits: int = 0


# ───────────────────────────── quét nguồn ─────────────────────────────

def layer_of(name: str) -> str:
    for base in (name, name.rsplit(".", 1)[-1]):
        for prefix, layer in LAYER_RULES:
            if base == prefix or base.startswith(prefix):
                return layer
    return "misc"


def _sub_dirs() -> list[str]:
    """Thư mục con chứa code: SUB_PACKAGES + mọi thư mục cấp 1 có .py (bố cục package V5)."""
    out = [s for s in SUB_PACKAGES if (ROOT / s).is_dir()]
    for d in sorted(ROOT.iterdir()):
        if d.name in SKIP_PARTS or d.name.startswith((".", "_")):
            continue
        try:
            if not d.is_dir() or d.is_symlink():
                continue
        except OSError:  # junction hỏng / ổ không truy cập được
            continue
        if d.name in out:
            continue
        try:
            if any(d.glob("*.py")) or any(d.glob("*/*.py")):
                out.append(d.name)
        except OSError:
            continue
    return out


def discover() -> list[Path]:
    files: list[Path] = sorted(p for p in ROOT.glob("*.py") if p.is_file())
    for sub in _sub_dirs():
        d = ROOT / sub
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.py")):
            rel_parts = p.relative_to(ROOT).parts
            if any(part in SKIP_PARTS for part in rel_parts[1:-1]):
                continue
            files.append(p)
    return files


def mod_name(p: Path) -> str:
    rel = p.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    return ".".join(parts)


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _first_line(doc: str | None, n: int = 1) -> str:
    if not doc:
        return ""
    lines = [ln.strip() for ln in doc.strip().splitlines() if ln.strip()]
    return " ".join(lines[:n])[:220]


def _args(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    a = fn.args
    names = [x.arg for x in a.posonlyargs + a.args]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    names += [x.arg for x in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    s = ", ".join(n for n in names if n not in ("self", "cls"))
    return s if len(s) <= 70 else s[:67] + "…"


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _call_name(func.value)
        return f"{base}.{func.attr}" if base else func.attr
    return ""


def analyze(p: Path, known: set[str]) -> Mod:
    raw = p.read_bytes()
    src = raw.decode("utf-8-sig", errors="replace")
    name = mod_name(p)
    m = Mod(name=name, rel=p.relative_to(ROOT).as_posix(), layer=layer_of(name),
            sha1=hashlib.sha1(raw).hexdigest())
    lines = src.splitlines()
    m.loc = len(lines)
    m.sloc = sum(1 for ln in lines if ln.strip() and not ln.strip().startswith("#"))
    m.smells["todo"] = len(re.findall(r"#\s*(TODO|FIXME|HACK|XXX)\b", src))
    m.ports = set(PORT_RE.findall(src))
    try:
        tree = ast.parse(src, filename=str(p))
    except SyntaxError as e:
        m.syntax_error = f"line {e.lineno}: {e.msg}"
        return m
    m.doc = _first_line(ast.get_docstring(tree), 3)

    # top-level defs
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            m.functions.append(Fn(node.name, node.lineno, node.end_lineno or node.lineno, _args(node),
                                  _first_line(ast.get_docstring(node)), isinstance(node, ast.AsyncFunctionDef)))
        elif isinstance(node, ast.ClassDef):
            n_methods = sum(isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) for b in node.body)
            m.classes.append((node.name, node.lineno, n_methods, _first_line(ast.get_docstring(node))))

    # mọi hàm (kể cả method / lồng) để tìm hàm dài
    def walk_defs(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{prefix}{child.name}"
                m.all_fns.append((q, Fn(child.name, child.lineno, child.end_lineno or child.lineno, "", "")))
                walk_defs(child, q + ".")
            elif isinstance(child, ast.ClassDef):
                walk_defs(child, f"{prefix}{child.name}.")
            else:
                walk_defs(child, prefix)
    walk_defs(tree)

    pkg = name.rsplit(".", 1)[0] if "." in name else ""
    # alias của config.get: `from config import get as cfg`, `import config as C` → C.get
    cfg_aliases: set[str] = {"get"} if name == "config" else set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "config":
            for a in node.names:
                if a.name == "get":
                    cfg_aliases.add(a.asname or "get")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "config":
                    cfg_aliases.add(f"{a.asname or 'config'}.get")
    for node in ast.walk(tree):
        # imports (cả import lười trong hàm)
        if isinstance(node, ast.Import):
            for a in node.names:
                _add_import(m, a.name, known)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level and pkg:
                base = f"{pkg}.{base}" if base else pkg
            if base:
                _add_import(m, base, known)
            for a in node.names:  # from pkg import mod
                if base and f"{base}.{a.name}" in known:
                    m.imports_int.add(f"{base}.{a.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) \
                        and dec.func.attr in ROUTE_METHODS:
                    path = _const_str(dec.args[0]) if dec.args else None
                    if path is None:
                        for kw in dec.keywords:
                            if kw.arg == "path":
                                path = _const_str(kw.value)
                    method = dec.func.attr.upper()
                    if dec.func.attr == "api_route":
                        for kw in dec.keywords:
                            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                                method = "|".join(filter(None, (_const_str(e) for e in kw.value.elts))) or "ANY"
                    m.routes.append((method, path or "?", node.name, node.lineno))
        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                m.smells["bare_except"] += 1
            elif isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException"):
                m.smells["broad_except"] += 1
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    m.smells["swallow_except"] += 1
        elif isinstance(node, ast.Global):
            m.smells["global_stmt"] += 1
        elif isinstance(node, ast.Call):
            cn = _call_name(node.func)
            short = cn.rsplit(".", 1)[-1]
            first = _const_str(node.args[0]) if node.args else None
            if (short in CFG_FUNCS or cn in cfg_aliases) and first and KEY_RE.match(first):
                m.cfg_keys[first].append(node.lineno)
            elif cn in ("os.getenv", "os.environ.get", "environ.get", "getenv", "os.environ.setdefault") \
                    and first and KEY_RE.match(first):
                m.cfg_keys[first].append(node.lineno)
            elif cn in ("importlib.reload", "reload") and node.args and isinstance(node.args[0], ast.Name):
                if node.args[0].id in known:
                    m.reloads.add(node.args[0].id)
            elif cn in ("importlib.import_module", "import_module") and first:
                _add_import(m, first, known)
            if cn in ("threading.Thread", "Thread"):
                m.smells["threads"] += 1
            elif cn.startswith("subprocess.") or cn in ("Popen",):
                m.smells["subprocess"] += 1
                if any(kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                       for kw in node.keywords):
                    m.smells["shell_true"] += 1
            elif short in ("ThreadPoolExecutor", "ProcessPoolExecutor"):
                m.smells["pools"] += 1
            elif cn in ("time.sleep", "sleep"):
                m.smells["sleep"] += 1
            elif cn in ("eval", "exec"):
                m.smells["eval_exec"] += 1
            elif cn in ("open",) or short in ("write_text", "write_bytes"):
                m.smells["file_writes"] += 1 if short != "open" else 0
        elif isinstance(node, ast.Subscript):
            if _call_name(node.value) == "os.environ":
                k = _const_str(node.slice)
                if k and KEY_RE.match(k):
                    m.cfg_keys[k].append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if len(v) < 60 and ARTIFACT_RE.match(v):
                m.artifacts.add(v)
            for ref in re.findall(r"([A-Za-z_]\w*)\.py\b", v):
                m.py_refs.add(ref)
    m.imports_int.discard(name)
    return m


def _add_import(m: Mod, dotted: str, known: set[str]) -> None:
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in known:
            m.imports_int.add(cand)
            return
    m.imports_ext.add(parts[0])


# ───────────────────────────── liên kết docs ─────────────────────────────

def link_docs(mods: dict[str, Mod]) -> None:
    docs = ROOT / "docs"
    if not docs.is_dir():
        return

    def read(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return ""

    pats = {}
    for name, m in mods.items():
        stem = Path(m.rel).stem
        if m.layer == "test" or stem in ("__init__", "__main__", "app", "config"):
            # config/app quá chung chung → chỉ khớp dạng file .py
            pats[name] = re.compile(rf"(?<![\w/]){re.escape(m.rel)}\b|(?<![\w.]){re.escape(stem)}\.py\b")
        else:
            pats[name] = re.compile(
                rf"(?<![\w.]){re.escape(stem)}\.py\b|`{re.escape(stem)}(?:\.[A-Za-z_]\w*)*(?:\(\))?`")

    for f in sorted((docs / "fixes").glob("*.md")) if (docs / "fixes").is_dir() else []:
        if f.name.lower() == "readme.md":
            continue
        txt = read(f)
        title = next((ln.lstrip("# ").strip() for ln in txt.splitlines() if ln.startswith("#")), f.stem)
        for name, pat in pats.items():
            if pat.search(txt):
                mods[name].fix_docs.append((f"fixes/{f.name}", title[:110]))
    for f in sorted((docs / "reviews").glob("*.md")) if (docs / "reviews").is_dir() else []:
        txt = read(f)
        for name, pat in pats.items():
            if pat.search(txt):
                mods[name].review_docs.append(f"reviews/{f.name}")
    cl = read(docs / "CHANGELOG.md")
    dec = read(docs / "DECISIONS.md")
    for name, pat in pats.items():
        mods[name].changelog_hits = len(pat.findall(cl))
        mods[name].decision_hits = len(pat.findall(dec))


# ───────────────────────────── render markdown ─────────────────────────────

def card_path(name: str) -> Path:
    return MOD_DIR / f"{name}.md"


def link(name: str, from_dir: str = "modules") -> str:
    if from_dir == "modules":
        return f"[{name}](./{name}.md)"
    return f"[{name}](../modules/{name}.md)"


def mermaid_id(name: str) -> str:
    return re.sub(r"\W", "_", name)


def render_auto(m: Mod, mods: dict[str, Mod]) -> str:
    out: list[str] = []
    w = out.append
    w(f"**File:** `{m.rel}` · **Lớp:** `{m.layer}` · **{m.loc} dòng** ({m.sloc} code) · "
      f"**{len(m.functions)} hàm**, **{len(m.classes)} class** · sha1 `{m.sha1[:10]}`")
    w("")
    if m.syntax_error:
        w(f"> ⚠️ **SyntaxError** {m.syntax_error} — AST không phân tích được.")
        w("")
    if m.doc:
        w(f"> {m.doc}")
        w("")

    # phụ thuộc
    w("### Phụ thuộc")
    w("")
    imp = sorted(m.imports_int)
    by = sorted(m.imported_by)
    w(f"- **Gọi tới (import nội bộ):** {', '.join(link(x) for x in imp) if imp else '—'}")
    if m.reloads:
        w(f"- **Hot-reload (`importlib.reload`):** {', '.join(f'`{x}`' for x in sorted(m.reloads))}")
    w(f"- **Được dùng bởi:** {', '.join(link(x) for x in by if mods[x].layer != 'test') or '—'}")
    if m.launched_by:
        w(f"- **Được nhắc tên file `.py` (subprocess/script) bởi:** {', '.join(link(x) for x in sorted(m.launched_by))}")
    tests = sorted(m.tests)
    w(f"- **Test:** {', '.join(f'`{mods[t].rel}`' for t in tests) if tests else '❌ chưa có test import trực tiếp'}")
    ext = sorted(x for x in m.imports_ext if x not in sys.stdlib_module_names) \
        if hasattr(sys, "stdlib_module_names") else sorted(m.imports_ext)
    if ext:
        w(f"- **Thư viện ngoài:** {', '.join(f'`{x}`' for x in ext)}")
    w("")
    if imp or by:
        w("```mermaid")
        w("flowchart LR")
        me = mermaid_id(m.name)
        w(f"  {me}[[{m.name}]]")
        for x in by[:15]:
            if mods[x].layer != "test":
                w(f"  {mermaid_id(x)}[{x}] --> {me}")
        for x in imp[:15]:
            w(f"  {me} --> {mermaid_id(x)}[{x}]")
        w("```")
        w("")

    # routes
    if m.routes:
        w(f"### API routes ({len(m.routes)})")
        w("")
        w("| Method | Path | Handler | Dòng |")
        w("|---|---|---|---:|")
        for method, path, handler, line in sorted(m.routes, key=lambda r: (r[1], r[0])):
            w(f"| {method} | `{path}` | `{handler}` | {line} |")
        w("")

    # functions
    if m.functions or m.classes:
        w("### Hàm & class top-level")
        w("")
        w("| Tên | Dòng | Dài | Tham số | Docstring |")
        w("|---|---:|---:|---|---|")
        for c, line, nm, doc in m.classes:
            w(f"| **class `{c}`** | {line} | {nm} method | | {doc.replace('|', '/')} |")
        for f in m.functions:
            flag = " ⚠️" if f.length >= LONG_FN else ""
            a = "async " if f.is_async else ""
            w(f"| {a}`{f.name}` | {f.line} | {f.length}{flag} | `{f.args.replace('|', '/')}` | "
              f"{f.doc.replace('|', '/')} |")
        w("")
    longs = sorted(((q, f) for q, f in m.all_fns if f.length >= LONG_FN), key=lambda t: -t[1].length)
    if longs:
        w(f"**Hàm dài ≥{LONG_FN} dòng:** " + ", ".join(f"`{q}` ({f.length}, L{f.line})" for q, f in longs[:12]))
        w("")

    # config
    if m.cfg_keys:
        w(f"### Config / env keys ({len(m.cfg_keys)})")
        w("")
        w(", ".join(f"`{k}`" for k in sorted(m.cfg_keys)))
        w("")
    if m.ports:
        w(f"**Cổng nhắc tới:** {', '.join(sorted(m.ports))}")
        w("")
    if m.artifacts:
        arts = sorted(m.artifacts)
        w(f"**File/artifact nhắc tới:** {', '.join(f'`{a}`' for a in arts[:40])}"
          + (f" … (+{len(arts) - 40})" if len(arts) > 40 else ""))
        w("")

    # smells
    s = m.smells
    smell_bits = [f"{k}={v}" for k, v in sorted(s.items()) if v]
    if smell_bits:
        w(f"**Tín hiệu rủi ro (đếm tĩnh):** {' · '.join(smell_bits)}")
        w("")

    # lịch sử
    w("### Lịch sử (từ docs/)")
    w("")
    if m.fix_docs:
        w(f"- **Fix logs ({len(m.fix_docs)}):**")
        for path, title in sorted(m.fix_docs, reverse=True)[:25]:
            w(f"  - [{title}](../../{path})")
        if len(m.fix_docs) > 25:
            w(f"  - … +{len(m.fix_docs) - 25} file nữa")
    else:
        w("- Fix logs: —")
    if m.review_docs:
        w(f"- **Reviews:** " + ", ".join(f"[{Path(r).stem}](../../{r})" for r in sorted(m.review_docs, reverse=True)[:10]))
    w(f"- Nhắc trong CHANGELOG: **{m.changelog_hits}** lần · DECISIONS: **{m.decision_hits}** lần")
    w("")
    return "\n".join(out)


def write_card(m: Mod, mods: dict[str, Mod], stamp: str) -> None:
    p = card_path(m.name)
    manual = MANUAL_TEMPLATE
    if p.exists():
        old = p.read_text(encoding="utf-8", errors="replace")
        mm = re.search(r"<!-- MANUAL:START -->\n?(.*?)<!-- MANUAL:END -->", old, re.S)
        if mm:
            manual = mm.group(1)
    body = (
        f"# `{m.name}`\n\n"
        f"[← Index](../generated/module-index.md) · [Brain home](../README.md)\n\n"
        f"<!-- AUTO:START — sinh bởi scripts/brain_build.py {stamp}. Đừng sửa tay khối này. -->\n"
        f"{render_auto(m, mods)}\n"
        f"<!-- AUTO:END -->\n\n"
        f"<!-- MANUAL:START -->\n{manual.rstrip()}\n<!-- MANUAL:END -->\n"
    )
    p.write_text(body, encoding="utf-8", newline="\n")


def manual_filled(name: str) -> bool:
    p = card_path(name)
    if not p.exists():
        return False
    txt = p.read_text(encoding="utf-8", errors="replace")
    mm = re.search(r"<!-- MANUAL:START -->(.*?)<!-- MANUAL:END -->", txt, re.S)
    return bool(mm) and "_Chưa viết." not in mm.group(1)


def hotspot_score(m: Mod) -> float:
    longs = sum(1 for _, f in m.all_fns if f.length >= LONG_FN)
    score = (m.loc / 500
             + 2.0 * len(m.fix_docs)
             + 0.15 * m.changelog_hits
             + 1.0 * longs
             + 0.1 * m.smells["broad_except"] + 1.0 * m.smells["bare_except"] + 0.5 * m.smells["swallow_except"]
             + 0.5 * m.smells["threads"] + 0.3 * m.smells["global_stmt"]
             + (3.0 if (not m.tests and m.loc > 800) else 0.0))
    return round(score, 1)


def write_generated(mods: dict[str, Mod], stamp: str) -> None:
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    head = f"_Sinh tự động bởi `scripts/brain_build.py` lúc {stamp}. Đừng sửa tay — chạy lại script._\n\n"
    prod = {k: v for k, v in mods.items() if v.layer != "test"}

    # module-index
    L = ["# Index module", "", head,
         f"Tổng: **{len(prod)} module** (+{len(mods) - len(prod)} test), "
         f"**{sum(m.loc for m in prod.values()):,} dòng**. "
         "Cột *Manual* = thẻ đã có phần viết tay (✅) hay chưa (·).", ""]
    for layer in LAYER_ORDER:
        group = sorted((m for m in prod.values() if m.layer == layer), key=lambda m: -m.loc)
        if not group:
            continue
        L += [f"## {layer} ({len(group)})", "",
              "| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |",
              "|---|---:|---:|---:|---:|---:|:-:|---|"]
        for m in group:
            L.append(f"| {link(m.name, 'gen')} | {m.loc} | {len(m.functions)} | {len(m.routes) or ''} | "
                     f"{len(m.fix_docs) or ''} | {len(m.tests) or '❌' if m.loc > 300 else len(m.tests) or ''} | "
                     f"{'✅' if manual_filled(m.name) else '·'} | {m.doc[:90].replace('|', '/')} |")
        L.append("")
    tests = sorted((m for m in mods.values() if m.layer == "test"), key=lambda m: m.name)
    if tests:
        L += ["## test", "", "| Test | Kiểm module |", "|---|---|"]
        for t in tests:
            L.append(f"| `{t.rel}` | {', '.join(link(x, 'gen') for x in sorted(t.imports_int)) or '—'} |")
        L.append("")
    (GEN_DIR / "module-index.md").write_text("\n".join(L), encoding="utf-8", newline="\n")

    # config keys
    keys: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for m in prod.values():
        for k, lines in m.cfg_keys.items():
            keys[k].append((m.name, len(lines)))
    L = ["# Config & env keys", "", head,
         "Thứ tự ưu tiên xem `config.py` (LLM keys: file thắng env; còn lại env → file → default) "
         "và `docs/ARCHITECTURE.md#config`. Bảng này chỉ cho biết **key được đọc ở đâu**.", "",
         f"**{len(keys)} key.**", "", "| Key | Đọc ở (số lần) |", "|---|---|"]
    for k in sorted(keys):
        L.append(f"| `{k}` | " + ", ".join(f"{link(n, 'gen')} ({c})" for n, c in sorted(keys[k])) + " |")
    (GEN_DIR / "config-keys.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")

    # routes
    routes = [(path, method, m.name, h, line) for m in prod.values() for method, path, h, line in m.routes]
    dup = Counter((p, me) for p, me, *_ in routes)
    L = ["# API routes", "", head, f"**{len(routes)} route.** ⚠️ = cùng method+path khai ở >1 nơi.", "",
         "| Path | Method | Module | Handler | Dòng |", "|---|---|---|---|---:|"]
    for path, method, name, h, line in sorted(routes):
        flag = " ⚠️" if dup[(path, method)] > 1 else ""
        L.append(f"| `{path}`{flag} | {method} | {link(name, 'gen')} | `{h}` | {line} |")
    (GEN_DIR / "api-routes.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")

    # dependency graph (theo lớp) + fan in/out
    edges: Counter = Counter()
    for m in prod.values():
        for x in m.imports_int:
            if x in prod and prod[x].layer != m.layer:
                edges[(m.layer, prod[x].layer)] += 1
    L = ["# Phụ thuộc giữa các lớp", "", head, "Cạnh A → B = module lớp A import module lớp B (số = số cặp module).", "",
         "```mermaid", "flowchart LR"]
    for (a, b), c in sorted(edges.items()):
        L.append(f"  {a} -->|{c}| {b}")
    L += ["```", "", "## Ngược chiều đáng chú ý", "",
          "Lớp pipeline/media (`source/asr/translate/tts/render`) import ngược `control`/`agent` "
          "là dấu hiệu coupling cần xem.", ""]
    media = {"source", "asr", "translate", "tts", "render"}
    bad = [(m.name, x) for m in prod.values() for x in sorted(m.imports_int)
           if m.layer in media and x in prod and prod[x].layer in ("control", "agent", "sidecar")
           and x not in ("config", "fileio", "manifest")]
    L += [f"- {link(a, 'gen')} → {link(b, 'gen')}" for a, b in bad] or ["- (không có)"]
    L += ["", "## Fan-in / fan-out", "", "| Module | Fan-in (được import) | Fan-out (import) | Hot-reload |",
          "|---|---:|---:|---|"]
    for m in sorted(prod.values(), key=lambda m: -(len(m.imported_by) + len(m.imports_int)))[:40]:
        L.append(f"| {link(m.name, 'gen')} | {len([x for x in m.imported_by if mods[x].layer != 'test'])} | "
                 f"{len(m.imports_int)} | {', '.join(sorted(m.reloads))} |")
    orphans = sorted(m.name for m in prod.values()
                     if not m.imported_by and not m.launched_by and m.layer not in ("ops",) and m.name not in ("server", "app")
                     and not m.routes and "__main__" not in m.name)
    L += ["", "## Không ai import và không ai nhắc tên file (ứng viên code chết — kiểm .bat/UI trước khi xoá)", "",
          ", ".join(link(x, 'gen') for x in orphans) or "—", ""]
    (GEN_DIR / "dependency-graph.md").write_text("\n".join(L), encoding="utf-8", newline="\n")

    # hotspots
    L = ["# Hotspots — nơi nên soi bug/refactor trước", "", head,
         "Điểm = LOC/500 + 2×fix log + 0.15×nhắc CHANGELOG + 1×hàm dài + 0.1×`except Exception` "
         "+ 1×bare except + 0.5×nuốt lỗi (`except: pass`) + 0.5×Thread + 0.3×`global` + 3 nếu >800 dòng mà không test.",
         "Đây là **heuristic để ưu tiên**, không phải kết luận có bug.", "",
         "| # | Module | Điểm | Dòng | Fix | Hàm dài | broad/bare/swallow except | Thread | Test |",
         "|---:|---|---:|---:|---:|---:|---|---:|---|"]
    ranked = sorted(prod.values(), key=hotspot_score, reverse=True)
    for i, m in enumerate(ranked[:40], 1):
        longs = sum(1 for _, f in m.all_fns if f.length >= LONG_FN)
        s = m.smells
        L.append(f"| {i} | {link(m.name, 'gen')} | {hotspot_score(m)} | {m.loc} | {len(m.fix_docs)} | {longs} | "
                 f"{s['broad_except']}/{s['bare_except']}/{s['swallow_except']} | {s['threads']} | "
                 f"{'✅' if m.tests else '❌'} |")
    (GEN_DIR / "hotspots.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")

    # long functions
    allf = sorted(((m.name, q, f) for m in prod.values() for q, f in m.all_fns if f.length >= LONG_FN),
                  key=lambda t: -t[2].length)
    L = ["# Hàm dài", "", head, f"Ngưỡng {LONG_FN} dòng. **{len(allf)} hàm.**", "",
         "| Module | Hàm | Dòng bắt đầu | Độ dài |", "|---|---|---:|---:|"]
    L += [f"| {link(n, 'gen')} | `{q}` | {f.line} | {f.length} |" for n, q, f in allf]
    (GEN_DIR / "long-functions.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")

    # artifacts + ports
    arts: dict[str, set[str]] = defaultdict(set)
    ports: dict[str, set[str]] = defaultdict(set)
    for m in prod.values():
        for a in m.artifacts:
            arts[a].add(m.name)
        for p in m.ports:
            ports[p].add(m.name)
    L = ["# File/artifact & cổng — ai đụng tới", "", head,
         "Dùng để trả lời: *file `X.json` do ai ghi/đọc?* (chỉ là chuỗi literal xuất hiện trong code). "
         "Artifact dùng bởi ≥2 module là điểm có thể race khi ghi.", "",
         "## Cổng", "", "| Cổng | Module |", "|---|---|"]
    for p in sorted(ports, key=int):
        L.append(f"| {p} | {', '.join(link(x, 'gen') for x in sorted(ports[p]))} |")
    L += ["", "## Artifact", "", "| File | Số module | Module |", "|---|---:|---|"]
    for a in sorted(arts, key=lambda a: (-len(arts[a]), a)):
        L.append(f"| `{a}` | {len(arts[a])} | {', '.join(link(x, 'gen') for x in sorted(arts[a]))} |")
    (GEN_DIR / "artifacts-ports.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")

    # duplicates: cùng tên file ở nhiều nơi (vd root copy + package) — nguồn lệch code
    by_stem: dict[str, list[Mod]] = defaultdict(list)
    for m in mods.values():
        stem = Path(m.rel).stem
        if stem not in ("__init__", "__main__") and m.layer != "test":
            by_stem[stem].append(m)
    dups = {k: v for k, v in by_stem.items() if len(v) > 1}
    L = ["# Module trùng tên — nguy cơ lệch code", "", head,
         "Cùng tên file ở ≥2 chỗ. **Khác nội dung** = sửa một bản, chạy bản kia. "
         "Kiểm cột *Được import* để biết bản nào đang sống.", "",
         f"**{len(dups)} nhóm**, trong đó **{sum(1 for v in dups.values() if len({m.sha1 for m in v}) > 1)} nhóm khác nội dung**.", "",
         "| Tên | Bản | Dòng | sha1 | Được import bởi | Nội dung |", "|---|---|---:|---|---:|---|"]
    for stem in sorted(dups, key=lambda s: (len({m.sha1 for m in dups[s]}) == 1, s)):
        group = dups[stem]
        differ = len({m.sha1 for m in group}) > 1
        for m in sorted(group, key=lambda m: m.rel):
            L.append(f"| `{stem}` | `{m.rel}` | {m.loc} | `{m.sha1[:8]}` | "
                     f"{len([x for x in m.imported_by if mods[x].layer != 'test'])} | {'⚠️ KHÁC' if differ else 'giống'} |")
    (GEN_DIR / "duplicates.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")


# ───────────────────────────── main ─────────────────────────────

def load_all() -> dict[str, Mod]:
    files = discover()
    known = {mod_name(p) for p in files}
    # gói con: cho phép "import context_studio"
    known |= {n.split(".")[0] for n in known if "." in n and n.split(".")[0] in SUB_PACKAGES}
    mods: dict[str, Mod] = {}
    for p in files:
        try:
            m = analyze(p, known)
        except Exception as e:  # noqa: BLE001 — một file lỗi không được làm hỏng cả build
            m = Mod(name=mod_name(p), rel=p.relative_to(ROOT).as_posix(), layer=layer_of(mod_name(p)),
                    sha1="", syntax_error=f"analyze failed: {e}")
        mods[m.name] = m
    for m in mods.values():
        for x in m.imports_int:
            if x in mods:
                mods[x].imported_by.add(m.name)
                if m.layer == "test":
                    mods[x].tests.add(m.name)
    by_stem = {Path(m.rel).stem: n for n, m in mods.items() if m.layer != "test"}
    for m in mods.values():
        for ref in m.py_refs:
            tgt = by_stem.get(ref)
            if tgt and tgt != m.name and m.layer != "test":
                mods[tgt].launched_by.add(m.name)
    link_docs(mods)
    return mods


def cmd_check(mods: dict[str, Mod]) -> int:
    old = {}
    if META.exists():
        try:
            old = json.loads(META.read_text(encoding="utf-8")).get("modules", {})
        except Exception:  # noqa: BLE001
            old = {}
    changed = sorted(n for n, m in mods.items() if n in old and old[n].get("sha1") != m.sha1)
    new = sorted(n for n in mods if n not in old)
    gone = sorted(n for n in old if n not in mods)
    no_manual = sorted(n for n, m in mods.items()
                       if m.layer != "test" and m.loc >= 300 and not manual_filled(n))
    print(f"Brain check — lần build trước: {json.loads(META.read_text(encoding='utf-8')).get('generated_at') if META.exists() else 'chưa có'}")
    print(f"  Đổi từ lần build trước ({len(changed)}): {', '.join(changed) or '—'}")
    print(f"  Module mới ({len(new)}): {', '.join(new) or '—'}")
    print(f"  Module đã xoá ({len(gone)}): {', '.join(gone) or '—'}")
    print(f"  Module ≥300 dòng chưa có phần MANUAL ({len(no_manual)}): {', '.join(no_manual) or '—'}")
    if changed or new or gone:
        print("→ Chạy: python scripts/brain_build.py  (rồi cập nhật MANUAL của module đã đổi hành vi)")
        return 1
    return 0


def cmd_module(mods: dict[str, Mod], name: str) -> int:
    cands = [n for n in mods if n == name or n.endswith("." + name) or Path(mods[n].rel).stem == name]
    if not cands:
        print(f"Không thấy module '{name}'.")
        return 2
    m = mods[cands[0]]
    print(f"# {m.name}  ({m.rel}, layer={m.layer}, {m.loc} dòng)")
    print(render_auto(m, mods))
    p = card_path(m.name)
    if p.exists():
        txt = p.read_text(encoding="utf-8", errors="replace")
        mm = re.search(r"<!-- MANUAL:START -->(.*?)<!-- MANUAL:END -->", txt, re.S)
        if mm:
            print("\n---- MANUAL ----\n" + mm.group(1).strip())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="chỉ báo module cũ/mới/xoá, không ghi")
    ap.add_argument("--module", help="in tóm tắt 1 module")
    ap.add_argument("--only", help="build lại thẻ của các module (phẩy) — vẫn làm mới generated/")
    args = ap.parse_args()

    mods = load_all()
    if args.check:
        return cmd_check(mods)
    if args.module:
        return cmd_module(mods, args.module)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    MOD_DIR.mkdir(parents=True, exist_ok=True)
    only = {x.strip() for x in args.only.split(",")} if args.only else None
    n = 0
    for m in mods.values():
        if m.layer == "test":
            continue
        if only and m.name not in only and Path(m.rel).stem not in only:
            continue
        write_card(m, mods, stamp)
        n += 1
    write_generated(mods, stamp)
    meta = {"generated_at": stamp,
            "modules": {k: {"sha1": v.sha1, "loc": v.loc, "rel": v.rel} for k, v in sorted(mods.items())}}
    if only and META.exists():  # giữ sha cũ cho module không build lại
        try:
            prev = json.loads(META.read_text(encoding="utf-8")).get("modules", {})
            for k, v in prev.items():
                if k in meta["modules"] and k not in only and Path(v.get("rel", k)).stem not in only:
                    meta["modules"][k] = v
        except Exception:  # noqa: BLE001
            pass
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    errs = [m.name for m in mods.values() if m.syntax_error]
    print(f"Brain build xong {stamp}: {n} thẻ module, {len(mods)} file quét → {BRAIN.relative_to(ROOT)}")
    if errs:
        print(f"  ⚠️ Không parse được: {', '.join(errs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
