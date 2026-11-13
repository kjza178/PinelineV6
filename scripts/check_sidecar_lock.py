import os, subprocess, sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# V6: chạy trên CÂY CỦA CHÍNH NÓ (trước cứng Z:\PinelineV2 → V6 kiểm nhầm code V2)
ROOT = str(Path(__file__).resolve().parents[1])
os.environ["AUTOPILOT_PORT"] = "18902"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import agents.agent_tele_cmd as t

ok = True
a = t._ExclusiveHTTPServer(("127.0.0.1", 18902), BaseHTTPRequestHandler)
try:
    t._ExclusiveHTTPServer(("127.0.0.1", 18902), BaseHTTPRequestHandler)
    print("FAIL bind trùng exclusive"); ok = False
except OSError:
    print("PASS bind lần 2 bị từ chối")
a.server_close()
old = ThreadingHTTPServer(("127.0.0.1", 18902), BaseHTTPRequestHandler)
try:
    t._ExclusiveHTTPServer(("127.0.0.1", 18902), BaseHTTPRequestHandler)
    print("FAIL bind đè lên server kiểu cũ (reuseaddr)"); ok = False
except OSError:
    print("PASS không bind đè được server kiểu cũ")
old.server_close()
l1 = t._single_instance_lock()
code = ("import os,sys; os.environ['AUTOPILOT_PORT']='18902'; sys.path.insert(0, %r); "
        "import agents.agent_tele_cmd as t; sys.exit(0 if t._single_instance_lock() is None else 3)") % ROOT
r = subprocess.run([sys.executable, "-c", code], cwd=ROOT)
print("PASS khoá bản 2" if (l1 and r.returncode == 0) else f"FAIL khoá bản 2 rc={r.returncode}")
ok = ok and bool(l1) and r.returncode == 0
sys.exit(0 if ok else 1)
