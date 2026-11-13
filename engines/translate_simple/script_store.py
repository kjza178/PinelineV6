"""Một cửa đọc/ghi script.json + trace.jsonl cho luồng simple.

- Ghi atomic (fileio.atomic_write_json), giữ MỌI trường của hàng (kể cả trường lạ do module khác thêm).
- KHÔNG BAO GIỜ đổi `vi` của câu `manual` (người sửa tay) — set_vi trả False.
- Mỗi lần đổi vi ghi 1 dòng trace.jsonl: {ts, step, i, old, new, why}.
- review 2026-11-12: script.json hỏng (không parse được) → chép sang script.corrupt.<ts>.json TRƯỚC khi dựng
  lại từ ASR (không mất câu manual trong im lặng); trace.jsonl quá SIMPLE_TRACE_MAX_MB → xoay sang
  trace.1.jsonl (giữ tối đa 2 file).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

SCRIPT = "script.json"
TRACE = "trace.jsonl"
_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")
_SEG_DROP = frozenset({"text", "mt_vi", "words", "tokens", "src_i"})


def has_cjk(s: str | None) -> bool:
    return bool(_CJK_RE.search(s or ""))


def is_manual(r: dict | None) -> bool:
    return bool(isinstance(r, dict) and r.get("manual"))


def _clean_zh(zh: str) -> str:
    try:
        from engines.translate import clean_zh_spam
        return clean_zh_spam(zh) or zh
    except Exception:  # noqa: BLE001
        return zh


class ScriptStore:
    def __init__(self, work_dir: str | Path):
        self.work_dir = Path(work_dir)
        self.path = self.work_dir / SCRIPT
        self.trace_path = self.work_dir / TRACE
        self.rows: list[dict] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ đọc/dựng
    def load(self) -> list[dict]:
        rows: list = []
        if self.path.exists():
            try:
                rows = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                rows = []
                bak = self.work_dir / f"script.corrupt.{time.strftime('%Y%m%d_%H%M%S')}.json"
                try:
                    shutil.copyfile(self.path, bak)
                    print(f"[simple] script.json hỏng ({e}) → giữ bản {bak.name}, dựng lại từ ASR", flush=True)
                    self._trace("load", None, None, None, f"script.json hỏng → {bak.name}")
                except OSError as e2:
                    raise RuntimeError(f"script.json hỏng và không sao lưu được: {e2}") from e
        self.rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        return self.rows

    def ensure_from_asr(self, segments: list[dict], *, force: bool = False) -> list[dict]:
        """Bảo đảm mỗi câu ASR có 1 hàng. Giữ nguyên hàng cũ (mọi trường) theo i.

        Script post-split (dài hơn ASR > 5 hoặc có src_i) → giữ nguyên, không remap ASR.
        force=True → câu không manual: xoá vi/rv/flag cũ (dịch lại), câu manual giữ nguyên."""
        with self._lock:
            old = self.load()
            post_split = bool(old) and (
                len(old) > len(segments) + 5 or any(r.get("src_i") is not None for r in old)
            )
            if post_split:
                rows = old
            else:
                by_i = {}
                for r in old:
                    try:
                        by_i[int(r.get("i"))] = r
                    except (TypeError, ValueError):
                        continue
                rows = []
                seen = set()
                for s in segments:
                    try:
                        ii = int(s.get("i"))
                    except (TypeError, ValueError):
                        continue
                    seen.add(ii)
                    if ii in by_i:
                        rows.append(by_i[ii])
                        continue
                    zh0 = (s.get("text") or s.get("zh") or "").strip()
                    row = {"i": ii, "start": s.get("start", 0), "end": s.get("end", 0),
                           "zh": _clean_zh(zh0), "vi": ""}
                    for k, v in s.items():
                        if k not in row and k not in _SEG_DROP:
                            row[k] = v
                    rows.append(row)
                # hàng cũ không có trong ASR: giữ (không mất dữ liệu), xếp theo i
                extra = [r for ii, r in by_i.items() if ii not in seen]
                if extra:
                    rows = sorted(rows + extra, key=lambda r: int(r.get("i", 0)))
            if force:
                for r in rows:
                    if is_manual(r):
                        continue
                    if (r.get("vi") or "").strip():
                        self._trace("force", r.get("i"), r.get("vi"), "", "force: dịch lại")
                    r["vi"] = ""
                    for k in ("rv", "mt", "s_spk", "s_to", "s_conf", "s_flags", "s_name_ok", "fill_pending"):
                        r.pop(k, None)
            self.rows = rows
            self.write()
            return rows

    def by_i(self) -> dict[int, dict]:
        out = {}
        for r in self.rows:
            try:
                out[int(r.get("i"))] = r
            except (TypeError, ValueError):
                continue
        return out

    # ------------------------------------------------------------------ ghi
    def set_vi(self, row: dict, new: str, step: str, why: str = "") -> bool:
        """Đổi vi (trace). Câu manual → không đổi, trả False."""
        if is_manual(row):
            return False
        new = (new or "").strip()
        old = (row.get("vi") or "").strip()
        if new == old:
            return False
        with self._lock:
            row["vi"] = new
            self._trace(step, row.get("i"), old, new, why)
        return True

    def note(self, step: str, i, why: str) -> None:
        """Trace không đổi vi (vd cờ kiểm, lô lỗi)."""
        with self._lock:
            self._trace(step, i, None, None, why)

    def _trace(self, step: str, i, old, new, why: str) -> None:
        rec = {"ts": round(time.time(), 3), "step": step, "i": i}
        if old is not None or new is not None:
            rec["old"] = old
            rec["new"] = new
        if why:
            rec["why"] = why
        try:
            self.work_dir.mkdir(parents=True, exist_ok=True)
            with open(self.trace_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def rotate_trace(self, max_bytes: int) -> bool:
        """trace.jsonl > max_bytes → đổi tên thành trace.1.jsonl (đè bản .1 cũ). Trả True khi đã xoay."""
        try:
            if max_bytes > 0 and self.trace_path.stat().st_size > max_bytes:
                os.replace(self.trace_path, self.work_dir / "trace.1.jsonl")
                return True
        except OSError:
            pass
        return False

    def write(self) -> None:
        from core.fileio import atomic_write_json
        with self._lock:
            atomic_write_json(self.path, self.rows, fsync=False)

    # V6: tên gốc V2 là save(); đổi sang write() để ratchet F1 («không .save() ngoài state/» — luật cho
    # Manifest) không nhầm script.json với manifest. Giữ alias cho code/test V2.
    save = write

    # ------------------------------------------------------------------ thống kê
    def n_missing(self) -> int:
        return sum(1 for r in self.rows if not (r.get("vi") or "").strip())

    def n_unreviewed(self) -> int:
        return sum(1 for r in self.rows if not r.get("rv"))
