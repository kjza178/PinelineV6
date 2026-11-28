#!/usr/bin/env python3
"""Liệt kê mọi URL mà từng giao diện (Studio, trang Phim, Hongguo 2, desktop /v6/) gọi và route server phục vụ —
nguồn cho docs/design/2026-11-15-ui-map.md. Chạy: python scripts/ui_map.py (từ gốc repo). Chỉ đọc."""
import re, json, sys, pathlib
root = pathlib.Path('.')
files = {'Studio (index.html)': 'index.html', 'Trang Phim (films_ui.html)': 'films_ui.html',
         'Hongguo 2 (hongguo2.html)': 'hongguo2.html', 'Desktop /v6/ (desktop/ui/app.js)': 'desktop/ui/app.js'}
# routes defined
routes = {}
for f in list(root.glob('http_routes/*.py')) + [root/'server.py', root/'app/main.py']:
    t = f.read_text(encoding='utf-8', errors='replace')
    for m in re.finditer(r'@(?:router|app|r)\.(get|post|put|delete|api_route)\(\s*(?:V6_PREFIX\s*\+\s*)?["\']([^"\']+)["\']', t):
        path = m.group(2)
        if 'V6_PREFIX' in t[m.start():m.start()+60] and not path.startswith('/api/v6'):
            path = '/api/v6' + path
        routes.setdefault(path, set()).add(f.name)
def norm(u):
    u = re.sub(r'\$\{[^}]*\}', '{x}', u)
    u = re.sub(r"'\s*\+\s*[^+]*\+\s*'", '{x}', u)
    u = u.split('?')[0]
    return u
def match(u):
    for r, fs in routes.items():
        pat = '^' + re.sub(r'\\\{[^}]*\\\}', '[^/]+', re.escape(r)) + '$'
        uu = re.sub(r'\{x\}', 'X', u)
        if re.match(pat, uu) or r == u:
            return r, sorted(fs)
    # prefix match
    for r, fs in routes.items():
        if u.startswith(r.rstrip('/')+'/') and '{' in r:
            return r, sorted(fs)
    return None, []
out = {}
for name, fn in files.items():
    t = (root/fn).read_text(encoding='utf-8', errors='replace')
    urls = set()
    for m in re.finditer(r"""(?:fetch|api|EventSource|_hgFetch|hgFetch|j|getJSON|postJSON)\(\s*(?:"[A-Z]+",\s*)?[`'"]((?:https?://127\.0\.0\.1:\d+)?/[^`'"\s]*)""", t):
        urls.add(norm(m.group(1)))
    for m in re.finditer(r"""[`'"](https?://127\.0\.0\.1:(\d+)[^`'"\s]*)""", t):
        urls.add(norm(m.group(1)))
    rows = []
    for u in sorted(urls):
        host = re.match(r'https?://127\.0\.0\.1:(\d+)', u)
        path = re.sub(r'https?://127\.0\.0\.1:\d+', '', u) or '/'
        r, fs = match(path)
        rows.append((u, r, fs))
    out[name] = rows
for name, rows in out.items():
    print(f"\n### {name}: {len(rows)} URL")
    for u, r, fs in rows:
        print(f"  {'OK ' if r else '?? '} {u:60s} {('→ '+','.join(fs)) if r else ''}")
