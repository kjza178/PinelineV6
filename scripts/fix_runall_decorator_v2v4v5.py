"""Sửa decorator /run-all bị gắn nhầm vào _req_audio (lỗi do fix B6 ngày 11-11) ở V2/V4/V5.
Chỉ sửa file server.py (có backup); server đang chạy KHÔNG bị ảnh hưởng tới lần restart sau.
Chạy:  python Z:\\PinelineV6\\scripts\\fix_runall_decorator_v2v4v5.py
"""
import ast
import shutil
from pathlib import Path

BAD = '@app.post("/api/jobs/{job_id}/run-all")\ndef _req_audio('
TGT = '\ndef run_all(job_id: str, req: "RunAllReq"):'
for tree in (r"Z:\PinelineV2", r"Z:\PinelineV4", r"Z:\PinelineV5"):
    p = Path(tree) / "server.py"
    raw = p.read_bytes()
    crlf = b"\r\n" in raw
    s = raw.decode("utf-8").replace("\r\n", "\n")
    if s.count(BAD) != 1:
        print(f"[bỏ qua] {p}: không thấy lỗi (có thể đã sửa)")
        continue
    bak = Path(tree) / ".backup_edit_20261111_runall_decorator"
    bak.mkdir(exist_ok=True)
    shutil.copy2(p, bak / "server.py")
    s = s.replace(BAD, "def _req_audio(", 1)
    assert s.count(TGT) == 1, p
    s = s.replace(TGT, '\n@app.post("/api/jobs/{job_id}/run-all")' + TGT, 1)
    ast.parse(s)
    if crlf:
        s = s.replace("\n", "\r\n")
    p.write_bytes(s.encode("utf-8"))
    print(f"[đã sửa] {p} (backup {bak})")
# 2) Test test_review_fail_not_rv giả lỗi bằng "boom (không phải quota)" → chữ "quota" làm code tưởng
#    hết hạn mức → gọi preset_switcher.switch_preset THẬT → restart llama-server :8083 (dùng chung).
OLD_MSG = 'raise ValueError("boom (không phải quota)")'
NEW_MSG = 'raise ValueError("boom (lỗi thường, không phải hết hạn mức)")'
for tree in (r"Z:\PinelineV2", r"Z:\PinelineV4", r"Z:\PinelineV5"):
    p = Path(tree) / "tests" / "test_review_fail_not_rv.py"
    if not p.is_file():
        continue
    raw = p.read_bytes()
    s = raw.decode("utf-8")
    if OLD_MSG not in s:
        print(f"[bỏ qua] {p}: đã sửa")
        continue
    bak = Path(tree) / ".backup_edit_20261111_runall_decorator" / "tests"
    bak.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, bak / p.name)
    p.write_bytes(s.replace(OLD_MSG, NEW_MSG).encode("utf-8"))
    print(f"[đã sửa] {p} (test không còn kích hoạt đổi preset llama thật)")
print("Xong. V2 nhận bản sửa server.py ở lần restart tới (khi hàng chờ rảnh).")
