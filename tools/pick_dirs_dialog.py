"""Hộp thoại chọn NHIỀU folder cùng lúc (Listbox multi-select).
Chạy: python pick_dirs_dialog.py [initial_dir]
Stdout: mỗi path 1 dòng (UTF-8). Rỗng = hủy.
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import re
import sys
from pathlib import Path

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov"}


def _nat_key(name: str):
    parts = re.split(r"(\d+)", name.lower())
    return [int(x) if x.isdigit() else x for x in parts]


def _count_videos(folder: Path, limit: int = 200) -> int:
    n = 0
    try:
        for p in folder.rglob("*"):
            try:
                if p.is_file() and p.suffix.lower() in VIDEO_EXT:
                    n += 1
                    if n >= limit:
                        return n
            except OSError:
                continue
    except OSError:
        return 0
    return n


def _list_children(root: Path) -> list[tuple[str, Path, int]]:
    rows: list[tuple[str, Path, int]] = []
    try:
        kids = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return []
    kids.sort(key=lambda p: _nat_key(p.name))
    for p in kids:
        rows.append((p.name, p, _count_videos(p)))
    return rows


def main() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    init = (sys.argv[1] if len(sys.argv) > 1 else "").strip().strip('"')
    if init:
        try:
            p0 = Path(init).expanduser()
            init = str(p0.resolve()) if p0.is_dir() else "D:\\Lib"
        except OSError:
            init = "D:\\Lib"
    else:
        init = "D:\\Lib"

    win = tk.Tk()
    win.title("Chọn nhiều folder — giữ Ctrl hoặc Shift")
    win.geometry("760x560")
    try:
        win.attributes("-topmost", True)
    except tk.TclError:
        pass
    win.lift()
    win.focus_force()

    init_label = ttk.Label(
        win,
        text="Chọn nhiều folder series trong 1 lần (Ctrl + click / Shift + click) → OK",
        font=("Segoe UI", 10),
        padding=(12, 8),
    )
    init_label.pack(fill=tk.X)

    root_var = tk.StringVar(value=init)
    filter_var = tk.BooleanVar(value=True)
    status_var = tk.StringVar(value="")
    # list of (display, full_path)
    items: list[tuple[str, str]] = []

    frm = ttk.Frame(win, padding=10)
    frm.pack(fill=tk.BOTH, expand=True)

    top = ttk.Frame(frm)
    top.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(top, text="Thư mục gốc:").pack(side=tk.LEFT)
    ent = ttk.Entry(top, textvariable=root_var)
    ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

    def change_root():
        p = filedialog.askdirectory(
            title="Chọn thư mục gốc (vd D:\\Lib)",
            initialdir=root_var.get() or "D:\\Lib",
            mustexist=True,
            parent=win,
        )
        if p:
            root_var.set(p)
            refresh()

    ttk.Button(top, text="Đổi…", command=change_root).pack(side=tk.LEFT)

    mid = ttk.Frame(frm)
    mid.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        mid,
        text="Giữ Ctrl / Shift để chọn nhiều folder · double-click = tick/bỏ",
    ).pack(anchor=tk.W)

    lb_frm = ttk.Frame(mid)
    lb_frm.pack(fill=tk.BOTH, expand=True, pady=6)
    scroll = ttk.Scrollbar(lb_frm)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    lb = tk.Listbox(
        lb_frm,
        selectmode=tk.EXTENDED,
        yscrollcommand=scroll.set,
        font=("Segoe UI", 11),
        activestyle="dotbox",
        exportselection=False,
    )
    lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.config(command=lb.yview)

    def refresh():
        nonlocal items
        lb.delete(0, tk.END)
        items = []
        root = Path(root_var.get().strip().strip('"'))
        if not root.is_dir():
            status_var.set(f"Không thấy: {root}")
            return
        rows = _list_children(root)
        only = filter_var.get()
        for name, path, nvid in rows:
            if only and nvid <= 0:
                continue
            label = f"{name}   ({nvid} video)"
            items.append((label, str(path)))
            lb.insert(tk.END, label)
        status_var.set(f"{len(items)} folder · chọn: {len(lb.curselection())}")

    def on_select(_evt=None):
        status_var.set(f"{len(items)} folder · chọn: {len(lb.curselection())}")

    lb.bind("<<ListboxSelect>>", on_select)

    def select_all():
        lb.select_set(0, tk.END)
        on_select()

    def clear_sel():
        lb.selection_clear(0, tk.END)
        on_select()

    bar = ttk.Frame(frm)
    bar.pack(fill=tk.X, pady=(4, 0))
    ttk.Checkbutton(
        bar, text="Chỉ folder có video", variable=filter_var, command=refresh
    ).pack(side=tk.LEFT)
    ttk.Button(bar, text="Chọn hết", command=select_all).pack(side=tk.LEFT, padx=4)
    ttk.Button(bar, text="Bỏ chọn", command=clear_sel).pack(side=tk.LEFT)
    ttk.Label(bar, textvariable=status_var).pack(side=tk.LEFT, padx=10)

    result: list[str] = []

    def ok():
        nonlocal result
        idxs = lb.curselection()
        if not idxs:
            messagebox.showinfo("Chưa chọn", "Hãy chọn ≥1 folder (Ctrl+click).", parent=win)
            return
        result = [items[i][1] for i in idxs]
        win.destroy()

    def cancel():
        nonlocal result
        result = []
        win.destroy()

    bot = ttk.Frame(frm)
    bot.pack(fill=tk.X, pady=(10, 0))
    ttk.Button(bot, text="Hủy", command=cancel).pack(side=tk.RIGHT)
    ttk.Button(bot, text="OK — dùng các folder đã chọn", command=ok).pack(
        side=tk.RIGHT, padx=8
    )

    win.protocol("WM_DELETE_WINDOW", cancel)
    refresh()
    win.mainloop()

    if result:
        sys.stdout.reconfigure(encoding="utf-8")
        for p in result:
            sys.stdout.write(p + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
