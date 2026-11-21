"""Hộp thoại chọn NHIỀU file video (Windows / tkinter) — server gọi khi bấm «Chọn file» trong hộp Thêm job.
Chạy: python pick_files_dialog.py [initial_dir]  →  stdout: mỗi đường dẫn 1 dòng (UTF-8). Rỗng = huỷ."""
from __future__ import annotations

import sys


def main() -> int:
    import tkinter as tk
    from tkinter import filedialog
    init = (sys.argv[1] if len(sys.argv) > 1 else "").strip().strip('"') or None
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)          # hiện lên trên app desktop
    paths = filedialog.askopenfilenames(
        parent=root, initialdir=init, title="Chọn video để tạo job",
        filetypes=[("Video", "*.mp4 *.mkv *.mov *.webm *.flv *.ts *.avi *.m4v"), ("Tất cả", "*.*")])
    root.destroy()
    sys.stdout.reconfigure(encoding="utf-8")
    for p in paths or ():
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
