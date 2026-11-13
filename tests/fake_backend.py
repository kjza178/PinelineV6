"""Backend giả (stdlib) cho tests/test_verify_all.py — mô phỏng route V6 mà verify_all bước d gọi.

python tests/fake_backend.py <port> [bad]   — `bad`: trang /ui3 còn :8802 (để test bắt FAIL).
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
BAD = len(sys.argv) > 2 and sys.argv[2] == "bad"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        pass

    def _send(self, code: int, body: str, ctype: str = "application/json") -> None:
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):  # noqa: N802
        p = self.path
        if p == "/api/health":
            return self._send(200, json.dumps({"ok": True, "instance": "test", "port": PORT, "jobs": 0}))
        if p == "/api/v6/jobs":
            return self._send(200, json.dumps({"jobs": []}))
        if p == "/api/jobs":
            return self._send(200, json.dumps([]))
        if p == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'event: snapshot\ndata: {"jobs": []}\n\n')
            self.wfile.flush()
            time.sleep(5)
            return None
        if p in ("/v6/", "/"):
            return self._send(200, "<html>ok</html>", "text/html")
        if p == "/ui3":
            return self._send(200, "<html>" + ("http://127.0.0.1:8802/config" if BAD else "/api/x") + "</html>",
                              "text/html")
        return self._send(404, "{}")


time.sleep(1.5)   # mô phỏng khởi động chậm
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
