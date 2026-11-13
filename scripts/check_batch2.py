"""Kiểm thử đợt 2 (bảo mật + config 1 nguồn + LLM lỗi dừng stage). KHÔNG chạm server/sidecar live:
middleware & handler được tách bằng AST rồi exec vào app/handler giả."""
import ast
import io
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
AGENTS = ROOT / "agents"
ENGINES = ROOT / "engines"
ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS", name)
    else:
        fail += 1
        print("  FAIL", name)


def extract(path: Path, names: set[str]) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    parts = []
    for node in tree.body:
        nm = getattr(node, "name", None)
        if nm is None and isinstance(node, ast.Assign):
            nm = getattr(node.targets[0], "id", None)
        if nm in names:
            parts.append(ast.get_source_segment(src, node))
    return "\n\n".join(parts)


# ---------- 1. FastAPI middleware (S4) ----------
print("[1] server middleware")
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app = FastAPI()
ns = {"app": app, "JSONResponse": JSONResponse}
code = extract(ROOT / "server.py", {"_LOCAL_HOSTS", "_url_host", "_local_only_guard"})
check("đủ 3 khối code", code.count("def ") == 2 and "_LOCAL_HOSTS" in code)
exec(code, ns)
check("server.py có @app.middleware trên guard",
      '@app.middleware("http")\nasync def _local_only_guard' in (ROOT / "server.py").read_text(encoding="utf-8"))
app.middleware("http")(ns["_local_only_guard"])   # get_source_segment bỏ decorator


@app.get("/api/x")
def _x():
    return {"ok": True}


@app.get("/ui")
def _ui():
    return {"ok": True}


c = TestClient(app, base_url="http://127.0.0.1:8801")
check("local, không Origin → 200", c.get("/api/x").status_code == 200)
check("Host localhost:8801 → 200", c.get("/api/x", headers={"host": "localhost:8801"}).status_code == 200)
check("Host [::1] → 200", c.get("/api/x", headers={"host": "[::1]:8801"}).status_code == 200)
check("Host lạ (rebinding) → 403", c.get("/api/x", headers={"host": "evil.com"}).status_code == 403)
check("Host lạ trên /ui → 403", c.get("/ui", headers={"host": "evil.com:8801"}).status_code == 403)
check("Origin lạ → 403", c.get("/api/x", headers={"origin": "http://evil.com"}).status_code == 403)
check("Origin null → 403", c.get("/api/x", headers={"origin": "null"}).status_code == 403)
check("Origin local → 200", c.get("/api/x", headers={"origin": "http://127.0.0.1:8801"}).status_code == 200)
check("Referer lạ, không Origin → 403",
      c.get("/api/x", headers={"referer": "http://evil.com/a"}).status_code == 403)
check("Referer local → 200",
      c.get("/api/x", headers={"referer": "http://localhost:8801/ui4"}).status_code == 200)
check("Referer lạ trên /ui (không phải /api) → 200",
      c.get("/ui", headers={"referer": "http://evil.com/"}).status_code == 200)
check("Host rác → 403", c.get("/api/x", headers={"host": "[bad"}).status_code == 403)

# ---------- 2. Sidecar handler (A2) ----------
print("[2] sidecar CtrlHandler guard/CORS")
src = (AGENTS / "agent_tele_cmd.py").read_text(encoding="utf-8")
tree = ast.parse(src)
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CtrlHandler")
meths = [ast.get_source_segment(src, f) for f in cls.body
         if isinstance(f, ast.FunctionDef) and f.name in ("_is_local", "_blocked", "_cors")]
deco_src = []
for f in cls.body:
    if isinstance(f, ast.FunctionDef) and f.name in ("_is_local", "_blocked", "_cors"):
        seg = ast.get_source_segment(src, f)
        if f.decorator_list:
            seg = "@staticmethod\n" + seg
        deco_src.append(seg)
cls_code = "class H:\n" + "\n".join("    " + l for s in deco_src for l in s.splitlines())
ns2 = {"_LOCAL_HOSTS": {"127.0.0.1", "localhost", "::1"}}
exec(cls_code, ns2)
H = ns2["H"]


def mk(headers, path="/status"):
    h = H()
    h.headers = headers
    h.path = path  # _blocked() đọc path (OAuth callback whitelist)
    h.sent = []
    h.send_response = lambda c: h.sent.append(("status", c))
    h.send_header = lambda k, v: h.sent.append((k, v))
    h.end_headers = lambda: None
    h.wfile = io.BytesIO()
    return h


h = mk({"Host": "127.0.0.1:8802", "Origin": "http://127.0.0.1:8801"})
check("Origin UI local → không chặn", h._blocked() is False)
h._cors()
check("CORS echo đúng origin + Vary", ("Access-Control-Allow-Origin", "http://127.0.0.1:8801") in h.sent
      and ("Vary", "Origin") in h.sent)
check("không còn '*'", not any(v == "*" for _, v in h.sent))
h = mk({"Host": "127.0.0.1:8802", "Origin": "http://evil.com"})
check("Origin lạ → chặn 403", h._blocked() is True and ("status", 403) in h.sent)
h = mk({"Host": "127.0.0.1:8802", "Origin": "http://evil.com"})
h._cors()
check("Origin lạ → không có ACAO", not any(k == "Access-Control-Allow-Origin" for k, _ in h.sent))
h = mk({"Host": "evil.com:8802"})
check("Host lạ → chặn", h._blocked() is True)
h = mk({"Host": "127.0.0.1:8802", "Referer": "http://evil.com/x"})
check("Referer lạ → chặn", h._blocked() is True)
h = mk({"Host": "127.0.0.1:8802"})
check("script/curl không Origin → qua", h._blocked() is False)
import re as _re  # noqa: E402
check("guard gọi đầu do_GET/do_POST/do_OPTIONS",
      all(_re.search(rf"def {m}\(self\):[^\n]*\n\s+if self\._blocked\(\):\n\s+return", src)
          for m in ("do_GET", "do_POST", "do_OPTIONS")))

# ---------- 3. safe_child (H5) ----------
print("[3] hongguo safe_child")
import agents.hongguo_bridge as hg  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    r = Path(td)
    check("tên thường → ok", hg.safe_child(r, "Bo phim 1") == r / "Bo phim 1")
    for bad in ("", ".", "..", "../x", "..\\x", "a/b", "a\\b", "C:x", "C:\\Windows", "a..b/.."):
        check(f"chặn {bad!r}", hg.safe_child(r, bad) is None)
    check("root None → None", hg.safe_child(None, "x") is None)

# ---------- 4. escapeHtml (H6) — live UI is static/ui/index3.html (Phase A) ----------
print("[4] UI escape")
ui = ROOT / "static" / "ui" / "index3.html"
t = ui.read_text(encoding="utf-8-sig")
check("static/ui/index3.html: escapeHtml có &#39;", "&#39;" in t)
check("static/ui/index3.html: không còn ${JSON.stringify(gt)} trần", "${JSON.stringify(gt)}" not in t)
check("static/ui/index3.html: không còn '${j.group_id}' trần", "'${j.group_id}'" not in t)

# ---------- 5. config 1 nguồn ----------
print("[5] config.agent_llm / llm_settings")
import core.config as config  # noqa: E402

fake = {"LLM_BASE_URL": "http://llm/v1", "LLM_API_KEY": "k-llm", "LLM_MODEL": "m-llm",
        "TRANSLATE_MODEL": "m-tr", "REVIEW_MODEL": "m-rv"}
with mock.patch.object(config, "_load_cfg", lambda force=False: None), \
        mock.patch.object(config, "_CFG", dict(fake)), \
        mock.patch.dict(os.environ, {"AGENT_API_KEY": "", "AGENT_BASE_URL": "", "AGENT_MODEL": "",
                                     "LLM_API_KEY": "env-stale"}, clear=False):
    a = config.agent_llm()
    check("agent dùng base LLM khi thiếu AGENT_BASE_URL", a["base"] == "http://llm/v1")
    check("agent dùng key file (không lấy env cũ)", a["key"] == "k-llm")
    s = config.llm_settings()
    check("llm_settings key file-first", s["key"] == "k-llm" and s["model"] == "m-llm")
with mock.patch.object(config, "_load_cfg", lambda force=False: None), \
        mock.patch.object(config, "_CFG", {**fake, "AGENT_MODEL": "ag-file"}), \
        mock.patch.dict(os.environ, {"AGENT_MODEL": "ag-env"}, clear=False):
    check("AGENT_MODEL file-first", config.agent_llm()["model"] == "ag-file")

# ---------- 6. llm.py: live settings + LLMUnavailable ----------
print("[6] llm")
import engines.llm as llm  # noqa: E402

_llm_src = (ENGINES / "llm.py").read_text(encoding="utf-8")
check("docstring không còn key", "sk-79" not in _llm_src)
check("LOCAL_BASE_URL mặc định Ollama 11434",
      'os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")' in _llm_src)
seen = {}


class R:
    def __init__(self, code, js=None):
        self.status_code, self._js, self.text = code, js or {}, "x"

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}", response=self)


def post_ok(url, headers=None, json=None, timeout=None):
    seen["url"], seen["auth"], seen["model"] = url, headers["Authorization"], json["model"]
    return R(200, {"choices": [{"message": {"content": "hi"}}]})


with mock.patch.object(llm, "_settings", return_value=("http://live/v1", "live-key", "live-model")), \
        mock.patch.object(llm.requests, "post", post_ok):
    check("chat trả nội dung", llm.chat([{"role": "user", "content": "a"}]) == "hi")
    check("chat dùng base/key/model đọc lúc gọi",
          seen == {"url": "http://live/v1/chat/completions", "auth": "Bearer live-key", "model": "live-model"})

with mock.patch.object(llm, "_settings", return_value=("http://live/v1", "k", "m")), \
        mock.patch.object(llm.requests, "post", side_effect=ConnectionError("refused")), \
        mock.patch.object(llm.time, "sleep", lambda s: None):
    try:
        llm.chat([{"role": "user", "content": "a"}], retries=2)
        check("mất kết nối → LLMUnavailable", False)
    except llm.LLMUnavailable:
        check("mất kết nối → LLMUnavailable", True)

calls = {"n": 0}


def post_400(url, headers=None, json=None, timeout=None):
    calls["n"] += 1
    return R(400)


with mock.patch.object(llm, "_settings", return_value=("http://live/v1", "k", "m")), \
        mock.patch.object(llm.requests, "post", post_400), mock.patch.object(llm.time, "sleep", lambda s: None):
    try:
        llm.chat([{"role": "user", "content": "a"}], retries=4)
        check("400 → RuntimeError thường", False)
    except llm.LLMUnavailable:
        check("400 → RuntimeError thường (không phải Unavailable)", False)
    except RuntimeError:
        check("400 → RuntimeError thường, không retry", calls["n"] == 1)

with mock.patch.object(llm.requests, "post", side_effect=ConnectionError("refused")), \
        mock.patch.object(llm.time, "sleep", lambda s: None):
    try:
        llm._ollama_chat("m", [{"role": "user", "content": "a"}], 0.2, 100, False, 2, 10)
        check("ollama mất kết nối → LLMUnavailable", False)
    except llm.LLMUnavailable:
        check("ollama mất kết nối → LLMUnavailable", True)

# ---------- 7. translate: dừng stage khi LLM sập ----------
print("[7] translate")
import engines.translate as tr  # noqa: E402

rows = [{"i": k, "text": "你好", "start": 0, "end": 1} for k in range(8)]
n_calls = {"n": 0}


def boom(*a, **k):
    n_calls["n"] += 1
    raise llm.LLMUnavailable("down")


with mock.patch.object(tr, "translate_batch", boom):
    try:
        tr._translate_safe(rows, "", [], "Tiếng Việt", None)
        check("LLMUnavailable xuyên qua _translate_safe", False)
    except llm.LLMUnavailable:
        check("LLMUnavailable xuyên qua _translate_safe, không chia lô", n_calls["n"] == 1)


def parse_err(rows_, *a, **k):
    if len(rows_) > 1:
        raise ValueError("parse")
    return {rows_[0]["i"]: "ok"}


with mock.patch.object(tr, "translate_batch", parse_err):
    check("lỗi parse vẫn chia đôi như cũ", len(tr._translate_safe(rows, "", [], "Tiếng Việt", None)) == 8)

with mock.patch.object(tr, "_cfg_get", lambda k, d=None: {"TRANSLATE_MODEL": "t1", "REVIEW_MODEL": None}.get(k, d)):
    check("_tmodel đọc lúc gọi", tr._tmodel() == "t1")
    check("_rmodel rơi về TRANSLATE_MODEL", tr._rmodel() == "t1")
tsrc = (ENGINES / "translate.py").read_text(encoding="utf-8")
check("không còn 'model or MODEL'", "model or MODEL" not in tsrc)

# ---------- 8. secret sạch ----------
print("[8] secret")
import re  # noqa: E402

pat = re.compile(r"sk-[A-Za-z0-9_\-]{12,}")
secret_files = (
    "apps/run_v5.bat",
    "apps/run_tele_cmd.bat",
    "run_v5.bat",
    "config.json",
    "engines/llm.py",
    ".claude/settings.local.json",
)
for f in secret_files:
    p = ROOT / f
    if not p.is_file():
        check(f"{f}: tồn tại", False)
        continue
    check(f"{f}: không còn key", not pat.search(p.read_text(encoding="utf-8")))
tb = (ROOT / "apps" / "run_tele_cmd.bat").read_text(encoding="utf-8")
check("apps/run_tele_cmd.bat giữ AGENT_MODEL + fallback openclaw",
      'set "AGENT_MODEL=' in tb and 'set "AGENT_FALLBACK_MODEL=openclaw"' in tb)
rv = (ROOT / "apps" / "run_v5.bat").read_text(encoding="utf-8")
check("apps/run_v5.bat PORT 8805 + PinelineV5_tmp",
      "8805" in rv and "PinelineV5_tmp" in rv)
# redact helper lives next to V2 tmp tooling (shared)
_redact_roots = (Path(r"D:\PinelineV5_tmp"), Path(r"D:\PinelineV2_tmp"))
for _rd in _redact_roots:
    if (_rd / "redact.py").is_file():
        sys.path.insert(0, str(_rd))
        break
from redact import redact  # noqa: E402

sample = ('{"LLM_API_KEY": "abc123", "X": "sk-0123456789abcdef", "keep": "sk-local"}\n'
          'set "TELEGRAM_BOT_TOKEN=1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"\n'
          'GOCSPX-abcdefgh ya29.a0Abc 1//0abcdefghijklmnopqrstuvwxyz')
red, _ = redact(sample)
check("redact che key/secret/token", all(s not in red for s in
      ("abc123", "sk-0123456789abcdef", "AAAAAAAAAAAAAAAA", "GOCSPX-abc", "ya29.a0", "1//0abc")))
check("redact giữ sk-local", "sk-local" in red)

print(f"\nTỔNG: {ok} PASS, {fail} FAIL")
sys.exit(1 if fail else 0)
