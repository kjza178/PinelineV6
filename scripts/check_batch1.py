"""Test đợt 1 (chặn mất dữ liệu) — chỉ dùng thư mục tạm, không đụng job/queue/thư viện thật."""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_TMP_BASE = Path(r"D:\PinelineV5_tmp")
_TMP_BASE.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="b1_", dir=str(_TMP_BASE)))
FAILS: list[str] = []
AGENTS = ROOT / "agents"


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def extract(path: Path, names: list[str], g: dict) -> dict:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    mod = ast.Module(body=nodes, type_ignores=[])
    exec(compile(mod, str(path), "exec"), g)
    return g


def ffmk(path: Path, dur: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=d={dur}:s=160x120:r=10",
                    "-f", "lavfi", "-i", f"sine=d={dur}", "-shortest", "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", str(path)], check=True)


def touch(p: Path, t: float) -> None:
    os.utime(p, (t, t))


# ---------------- S1: _stage_complete_on_disk ----------------
def test_s1() -> None:
    g = {"json": json, "os": os, "Path": Path, "_find_video": lambda wd: None}
    extract(ROOT / "server.py", ["_path_key", "_json_rows", "_stage_complete_on_disk",
                                 "_stage_has_artifact"], g)
    f = g["_stage_complete_on_disk"]
    wd = TMP / "job_s1"
    wd.mkdir()
    rows = [{"i": i, "start": i, "end": i + 1, "zh": "x", "vi": "câu"} for i in range(5)]
    (wd / "script.json").write_text(json.dumps(rows), encoding="utf-8")
    check("S1 translate: chỉ script.json (checkpoint) → chưa xong", not f(wd, "translate"))
    (wd / "script.display.json").write_text(json.dumps(rows), encoding="utf-8")
    check("S1 translate: có script.display.json mới → xong", f(wd, "translate"))
    touch(wd / "script.display.json", time.time() - 600)
    check("S1 translate: script.json mới hơn display (resume ghi dở) → chưa xong", not f(wd, "translate"))

    tts = [{"i": i, "start": i, "end": i + 1, "audio": f"seg_{i:04d}.wav"} for i in range(3)]
    (wd / "tts.json").write_text(json.dumps(tts), encoding="utf-8")
    check("S1 tts: salvage 3/5 → chưa xong", not f(wd, "tts"))
    tts = [{"i": i, "start": i, "end": i + 1, "audio": f"seg_{i:04d}.wav"} for i in range(5)]
    (wd / "tts.json").write_text(json.dumps(tts), encoding="utf-8")
    check("S1 tts: đủ 5/5 → xong", f(wd, "tts"))

    out = wd / "output.mp4"
    out.write_bytes(b"\0" * 1_100_000)
    touch(out, time.time() - 3600)
    check("S1 mux: output.mp4 cũ hơn tts.json → chưa xong", not f(wd, "mux"))
    touch(out, time.time() + 5)
    check("S1 mux: output.mp4 mới hơn tts.json → xong", f(wd, "mux"))

    pk = g["_path_key"]
    probe = "pathkey_probe"
    slash_a = pk(str(ROOT / "work" / probe).replace("/", "\\"))
    slash_b = pk(str(ROOT / "work" / probe).replace("\\", "/"))
    check("S3 _path_key: chuẩn hoá slash/case", slash_a == slash_b and slash_a.endswith("\\pathkey_probe"),
          f"{slash_a!r} vs {slash_b!r}")
    work_z, work_d = ROOT / "work", Path(r"D:\PinelineV5_work")
    try:
        same = work_z.exists() and work_d.exists() and Path(os.path.realpath(work_z)).samefile(work_d)
    except OSError:
        same = False
    if same:
        jobs = [d for d in work_z.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
        j = jobs[0].name if jobs else probe
        a, b = pk(str(work_z / j)), pk(str(work_d / j))
        check("S3 _path_key: Z:\\…\\work\\<job> == D:\\PinelineV5_work\\<job>", a == b, f"{a} vs {b}")
    else:
        check("S3 _path_key: V5 work chưa junction Z↔D (bỏ qua dual-drive)", True)

    src = (ROOT / "server.py").read_text(encoding="utf-8")
    i = src.find("    m = Manifest(work_dir)\n    stages = m.data.get(\"stages\", {})")
    check("S1 read_job không còn gọi _heal_stages_from_disk", i > 0)


# ---------------- S6: job_queue giữ opts khi đang chạy ----------------
def test_s6() -> None:
    sys.modules.setdefault("pytest", types.ModuleType("pytest"))
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        import core.job_queue as jq
        jq._reset_for_tests()
        jq.WORK_ROOT = TMP / "work"
        (jq.WORK_ROOT / "jobA").mkdir(parents=True)
        (jq.WORK_ROOT / "jobA" / "manifest.json").write_text('{"stages":{}}', encoding="utf-8")
        seen = {}

        def runner(job_id, opts):
            persisted = json.loads((jq.WORK_ROOT / "_job_queue.json").read_text(encoding="utf-8"))
            seen["opts"] = opts
            seen["persist"] = persisted
            return "done", ""
        jq.set_runner(runner)
        jq.queue_add(["jobA"], opts={"brand": "SageReview", "voice": "v2"}, skip_done=False)
        for _ in range(50):
            if seen and not jq.queue_state()["worker"]:
                break
            time.sleep(0.1)
        check("S6 runner nhận đúng opts", (seen.get("opts") or {}).get("brand") == "SageReview")
        check("S6 file hàng đợi giữ opts của job ĐANG chạy (restart không mất)",
              (seen.get("persist", {}).get("opts_by_job", {}).get("jobA") or {}).get("brand") == "SageReview",
              json.dumps(seen.get("persist"), ensure_ascii=False)[:300])
        final = json.loads((jq.WORK_ROOT / "_job_queue.json").read_text(encoding="utf-8"))
        check("S6 xong job → dọn opts_by_job", "jobA" not in (final.get("opts_by_job") or {}))
        assert str(jq.WORK_ROOT).startswith(str(TMP))
    finally:
        os.chdir(cwd)


# ---------------- H1/H2: cleanup_fragments ----------------
def test_hongguo() -> None:
    import agents.hongguo_bridge as hg
    lib = TMP / "Lib"
    show = lib / "Show"
    show.mkdir(parents=True)
    ffmk(show / "第001集.mp4", 2)
    ffmk(show / "第002集.mp4", 2)
    ffmk(show / "trailer.mp4", 1)
    lst = TMP / "concat.txt"
    lst.write_text(f"file '{(show / '第001集.mp4').as_posix()}'\nfile '{(show / '第002集.mp4').as_posix()}'\n",
                   encoding="utf-8")
    merged = show / "Show(merged).mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(merged)], check=True)
    now = time.time()
    for p in (show / "第001集.mp4", show / "第002集.mp4", show / "trailer.mp4"):
        touch(p, now - 300)
    touch(merged, now - 100)

    # merged quá ngắn → không dọn
    short = show / "Show(merged).mp4"
    bak = TMP / "full_merged.mp4"
    shutil.copy2(short, bak)
    ffmk(short, 1)
    touch(short, now - 100)
    r = hg.cleanup_fragments(show)
    check("H1 merged ngắn hơn 98% tổng tập → KHÔNG dọn", not r.get("ok") and r.get("removed") == 0, str(r))
    check("H1 tập vẫn còn sau lần từ chối", (show / "第001集.mp4").exists())
    shutil.copy2(bak, short)
    touch(short, now - 100)

    # tập mới hơn merged → stale, remerge
    touch(show / "第002集.mp4", now + 600)
    chk = hg.merged_covers_fragments(show)
    check("H2 có tập mới hơn merged → stale", chk.get("stale") is True and not chk.get("ok"), str(chk))
    check("H2 tập lẻ đủ nội dung → remerge=True", chk.get("remerge") is True, str(chk))
    r = hg.cleanup_fragments(show)
    check("H2 stale → cleanup không dọn", not r.get("ok") and (show / "第002集.mp4").exists(), str(r))
    touch(show / "第002集.mp4", now - 300)

    # đủ → dọn vào thùng rác, giữ trailer
    r = hg.cleanup_fragments(show)
    check("H1 merged đủ → dọn 2 tập", r.get("ok") and r.get("removed") == 2, str(r))
    check("H1 trailer.mp4 (không đúng mẫu tên tập) được giữ", (show / "trailer.mp4").exists())
    check("H1 merged được giữ", merged.exists())
    trash = Path(r.get("trash") or "")
    check("H1 tập nằm trong thùng rác ngoài thư viện",
          trash.is_dir() and (trash / "第001集.mp4").exists() and str(trash).startswith(str(TMP / "Lib_trash")),
          str(trash))

    # purge sau 7 ngày
    old = now - 8 * 86400
    touch(trash, old)
    n = hg.purge_trash(show)
    check("H1 purge_trash xoá lô > 7 ngày", n == 1 and not trash.exists(), f"n={n}")


# ---------------- A3: _pick_lib_item ----------------
def test_pick() -> None:
    g = {}
    extract(AGENTS / "agent_hongguo.py", ["_pick_lib_item"], g)
    pick = g["_pick_lib_item"]
    items = [
        {"name": "从炼气到称霸，我只靠两块灵石逆袭", "series_id": "7667575008222252094"},
        {"name": "从炼气到称霸，我只靠两块灵石逆袭第三季", "series_id": "7676386740709362750"},
        {"name": "末世2023", "series_id": "7000000000000000001"},
    ]
    it, amb = pick(items, "从炼气到称霸，我只靠两块灵石逆袭")
    check("A3 tên trùng khớp chọn bộ gốc (không phải 第三季)", it and it["series_id"] == "7667575008222252094")
    it, amb = pick(items, "从炼气到称霸")
    check("A3 chuỗi con khớp 2 bộ → hỏi lại", it is None and amb and len(amb) == 2)
    it, amb = pick(items, "7676386740709362750")
    check("A3 series_id chọn đúng 第三季", it and it["name"].endswith("第三季"))
    it, amb = pick(items, "第三季")
    check("A3 chuỗi con duy nhất → chọn", it and it["name"].endswith("第三季"))
    src = (AGENTS / "agent_hongguo.py").read_text(encoding="utf-8")
    check("A3 số trần không có danh sách → trả select_rank", '"stage": "select_rank"' in src)
    check("A4 hết giờ tải → download_timeout", src.count('"stage": "download_timeout"') == 2)
    check("A4 thiếu tập → download_incomplete", '"stage": "download_incomplete"' in src)


def test_feeder_sids() -> None:
    g = {}
    extract(AGENTS / "agent_feeder.py", ["_downloading_sids"], g)
    f = g["_downloading_sids"]
    st = {"series": [{"sid": "111", "status": "downloading"}, {"sid": "222", "status": "done"}]}
    check("feeder đọc khoá sid, bỏ bộ đã tải xong", f(st) == {"111"}, str(f(st)))


# ---------------- M2: media_ts commit an toàn ----------------
def test_m2() -> None:
    import engines.media_ts as mt
    wd = TMP / "job_m2"
    wd.mkdir()
    (wd / "script.json").write_text("OLD", encoding="utf-8")
    (wd / "script.json.audio_ts.tmp").write_text("NEW", encoding="utf-8")
    (wd / "audio.wav").write_bytes(b"OLDWAV")
    (wd / "audio.ts.wav").write_bytes(b"NEWWAV")
    (wd / mt.MARK_PENDING).write_text(json.dumps({"drift": 1.0, "changed": ["script.json"]}), encoding="utf-8")
    r = mt.ensure_job_audio_ts(wd, wd / "nope.mp4")
    check("M2 có pending → commit nốt (không tính lại)", r.get("status") == "fixed", str(r))
    check("M2 script.json = bản mới", (wd / "script.json").read_text(encoding="utf-8") == "NEW")
    check("M2 audio.wav gốc vào backup", (wd / "_pre_audio_ts" / "audio.wav").read_bytes() == b"OLDWAV")
    check("M2 audio.wav = bản mới", (wd / "audio.wav").read_bytes() == b"NEWWAV")
    check("M2 MARK có, pending hết", (wd / mt.MARK).exists() and not (wd / mt.MARK_PENDING).exists())

    # crash giữa commit: wav đã vào bak, audio.wav mất, tmp còn
    wd2 = TMP / "job_m2b"
    (wd2 / "_pre_audio_ts").mkdir(parents=True)
    (wd2 / "_pre_audio_ts" / "audio.wav").write_bytes(b"ORIG")
    (wd2 / "script.json").write_text("NEW", encoding="utf-8")
    (wd2 / "audio.ts.wav").write_bytes(b"NEWWAV")
    (wd2 / mt.MARK_PENDING).write_text(json.dumps({"drift": 1.0, "changed": ["script.json"]}), encoding="utf-8")
    mt.ensure_job_audio_ts(wd2, wd2 / "nope.mp4")
    check("M2 resume giữa commit: audio.wav = mới", (wd2 / "audio.wav").read_bytes() == b"NEWWAV")
    check("M2 resume: backup gốc KHÔNG bị đè", (wd2 / "_pre_audio_ts" / "audio.wav").read_bytes() == b"ORIG")
    check("M2 resume: script.json không bị đổi lần 2", (wd2 / "script.json").read_text(encoding="utf-8") == "NEW")

    p = wd2 / "x.json"
    p.write_text("A", encoding="utf-8")
    mt._backup_once(p, wd2 / "_pre_audio_ts")
    p.write_text("B", encoding="utf-8")
    mt._backup_once(p, wd2 / "_pre_audio_ts")
    check("M2 _backup_once không đè", (wd2 / "_pre_audio_ts" / "x.json").read_text(encoding="utf-8") == "A")


# ---------------- M5: cắt runaway theo dur/ratio ----------------
def test_m5() -> None:
    import numpy as np
    import soundfile as sf
    import engines.assemble as asm
    wd = TMP / "job_m5"
    (wd / "tts").mkdir(parents=True)
    sr = 24000

    def tone(p: Path, sec: float) -> None:
        t = np.arange(int(sec * sr)) / sr
        sf.write(str(p), (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr)
    tone(wd / "tts" / "a.wav", 3.0)
    tone(wd / "tts" / "b.wav", 20.0)
    tone(wd / "tts" / "c.wav", 0.5)
    clips = [
        {"i": 0, "start": 0.0, "end": 1.0, "dur": 3.0, "vi": "một hai ba bốn năm sáu bảy", "audio": str(wd / "tts" / "a.wav")},
        {"i": 1, "start": 1.0, "end": 3.0, "dur": 20.0, "vi": "ừ", "audio": str(wd / "tts" / "b.wav")},
        {"i": 2, "start": 4.0, "end": 5.0, "dur": 0.5, "vi": "hết", "audio": str(wd / "tts" / "c.wav")},
    ]
    (wd / "tts.json").write_text(json.dumps(clips, ensure_ascii=False), encoding="utf-8")
    os.environ.pop("ASSEMBLE_FIT_MODE", None)
    plan = asm.compute_fit_placements(clips, max_speed=1.0)
    asm.assemble_fit(wd, sr=sr, max_speed=1.0)
    real = json.loads((wd / "tts_fit.json").read_text(encoding="utf-8"))
    p1, r1 = plan[1], real[1]
    check("M5 câu runaway có nén (ratio>1) — đúng kịch bản lỗi", p1["ratio"] > 1.001 and p1["truncated"], str(p1))
    check("M5 audio thật khớp kế hoạch (dur/ratio)", abs(r1["dur"] - p1["dur"]) < 0.05,
          f"plan {p1['dur']} vs real {r1['dur']}")


if __name__ == "__main__":
    for t in (test_s1, test_s6, test_hongguo, test_pick, test_feeder_sids, test_m2, test_m5):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{t.__name__} chạy không lỗi", False, repr(e))
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else 'FAILS'} {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
