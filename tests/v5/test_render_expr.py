"""M3: biểu thức che hardsub theo thời gian không vượt giới hạn ffmpeg (~100 tầng if / 100 hạng tử +)."""
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import engines.render as render  # noqa: E402


def _eval(expr: str, t: float) -> float:
    py = expr.replace("\\,", ",").replace("if(", "IF(")
    return eval(py, {"IF": lambda c, a, b: a if c else b, "lt": lambda x, y: x < y,  # noqa: S307
                     "between": lambda x, a, b: float(a <= x <= b), "t": t})


def _ref(ends, vals, t):
    for e, v in zip(ends, vals):
        if t < round(e, 2):
            return v
    return vals[-1]


def _depth(expr: str) -> int:
    d = best = 0
    for ch in expr:
        d += ch == "("
        d -= ch == ")"
        best = max(best, d)
    return best


@pytest.mark.parametrize("n", [1, 2, 3, 64, 65, 200, 1000])
def test_pw_expr_matches_linear_semantics(n):
    rnd = random.Random(n)
    ends = sorted(rnd.uniform(0, 5000) for _ in range(n - 1))
    vals = [rnd.randrange(0, 1080, 2) for _ in range(n)]
    expr = render._pw_expr(ends, vals)
    probes = [-1.0, 1e9] + [e + d for e in ends[:300] for d in (-0.01, 0.0, 0.01)]
    assert all(_eval(expr, t) == _ref(ends, vals, t) for t in probes)
    assert _depth(expr) <= 64 + 2 if n <= 64 else _depth(expr) <= 2 * n.bit_length() + 2


@pytest.mark.parametrize("n", [1, 64, 65, 300])
def test_sum_expr_same_value_and_shallow(n):
    terms = [f"between(t,{k * 10:.2f},{k * 10 + 5:.2f})" for k in range(n)]
    expr = render._sum_expr(terms)
    for t in (2.0, 7.0, 10.0 * (n - 1) + 1, -3.0):
        assert _eval(expr, t) == sum(_eval(x, t) for x in terms)
    assert expr == "+".join(terms) if n <= 64 else _depth(expr) <= n.bit_length() + 2


def _alt_boxes(n: int):
    r1, r2, t = [], [], 0.0
    for k in range(n):
        (r1 if k % 2 == 0 else r2).append((round(t, 2), round(t + 0.01, 2)))
        t += 0.02
    return [(100, 900, 1700, 80, r1, 0.0), (100, 960, 1700, 70, r2, 0.0)]


def test_filters_stay_under_ffmpeg_limits():
    bx = _alt_boxes(300)
    for fg in (render._stretch_track_filter(bx, 1920, 1080), render._hardsub_track_filter_dyn(bx, 1920, 1080, 24)):
        for q in re.findall(r"'([^']*)'", fg):
            assert _depth(q) <= 60


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="không có ffmpeg")
@pytest.mark.parametrize("kind", ["stretch", "dyn"])
def test_ffmpeg_accepts_300_segments(kind):
    bx = _alt_boxes(300)
    chain = (render._stretch_track_filter(bx, 1920, 1080) if kind == "stretch"
             else render._hardsub_track_filter_dyn(bx, 1920, 1080, 24))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(f"[0:v]{chain}[v]")
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=1",
                            render.filter_script_opt(), f.name, "-map", "[v]", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    finally:
        Path(f.name).unlink(missing_ok=True)
    assert r.returncode == 0, r.stderr[-800:]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="không có ffmpeg")
def test_voice_bgm_runs_past_last_dub_line(tmp_path, monkeypatch):
    """M20: dub ngắn hơn video → nhạc thư viện vẫn chạy tới hết video (-t), không dừng ở câu thoại cuối."""
    def gen(name, src):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, name], cwd=tmp_path, check=True)
    gen("dub.wav", "sine=f=440:d=2")
    gen("bgm.wav", "sine=f=220:d=1")
    monkeypatch.setattr(render, "pick_bgm", lambda job_key=None: tmp_path / "bgm.wav")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=6"]
    af, used = render._build_audio_filter(cmd, tmp_path, tmp_path / "dub.wav", tmp_path / "nv.wav",
                                          tmp_path / "voc.wav", "voice_bgm", 0.2, 0.28)
    assert used.startswith("voice_bgm")
    cmd += ["-filter_complex", af, "-map", "[aout]", "-t", "6", "out.wav"]
    subprocess.run(cmd, cwd=tmp_path, check=True, timeout=120)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                "out.wav"], cwd=tmp_path, capture_output=True, text=True).stdout)
    assert dur == pytest.approx(6, abs=0.1)
    r = subprocess.run(["ffmpeg", "-ss", "4", "-i", "out.wav", "-af", "volumedetect", "-f", "null", "-"],
                       cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace")
    mean = float(re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr).group(1))
    assert mean > -50


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="không có ffmpeg")
def test_filter_script_option_matches_installed_ffmpeg(tmp_path, monkeypatch):
    """fix 2026-11-14: ffmpeg 8 bỏ `-filter_complex_script`; render phải tự chọn cú pháp mà ffmpeg đang cài nhận."""
    monkeypatch.setattr(render, "_FILTER_SCRIPT_OPT", None)
    opt = render.filter_script_opt()
    assert opt in ("-filter_complex_script", "-/filter_complex")
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=32x32:d=0.1"]
    script = render._attach_filter_complex(cmd, "[0:v]null[v]", tmp_path)
    assert cmd[-2:] == [opt, script.name]
    r = subprocess.run(cmd + ["-map", "[v]", "-f", "null", "-"], cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    assert render.filter_script_opt() == opt          # kết quả dò được giữ lại (không dò lại mỗi lần render)
