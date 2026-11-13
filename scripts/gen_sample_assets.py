#!/usr/bin/env python3
"""Tạo sample assets cho Pineline (grain / LUT / BGM).

Chạy:  python scripts/gen_sample_assets.py

- grain.png  : film grain loop (overlay)
- soft_contrast.cube : LUT nhẹ
- sample_ambient.mp3 : BGM procedural SAMPLE (không phải nhạc thương mại)

BGM mẫu chỉ để thử pipeline. Production: thay bằng track bạn có quyền dùng
(royalty-free / tự sáng tác / mua license) trong assets/bgm/.
"""
from __future__ import annotations

import math
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
LUTS = ASSETS / "luts"
OVERLAYS = ASSETS / "overlays"
BGM = ASSETS / "bgm"


def _write_grain(path: Path, size: int = 512) -> None:
    from PIL import Image
    rng = random.Random(42)
    im = Image.new("RGBA", (size, size))
    px = im.load()
    for y in range(size):
        for x in range(size):
            v = rng.randint(0, 255)
            # alpha thấp — blend overlay không đè chết màu
            a = 40 if abs(v - 128) > 18 else 18
            px[x, y] = (v, v, v, a)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    print(f"[ok] {path.relative_to(ROOT)} ({size}x{size})")


def _write_cube(path: Path, n: int = 17) -> None:
    """LUT soft contrast + hơi ấm (không phá da/anime)."""
    lines = [
        "# Pineline sample LUT — soft contrast / slight warm",
        "# TITLE soft_contrast",
        f"LUT_3D_SIZE {n}",
        "",
    ]
    for bi in range(n):
        for gi in range(n):
            for ri in range(n):
                r = ri / (n - 1)
                g = gi / (n - 1)
                b = bi / (n - 1)
                # contrast quanh 0.5
                def _c(x: float, amt: float = 1.06) -> float:
                    return max(0.0, min(1.0, (x - 0.5) * amt + 0.5 + 0.015))

                rr = _c(r) * 1.02 + 0.01  # warm
                gg = _c(g)
                bb = _c(b) * 0.98
                rr = max(0.0, min(1.0, rr))
                gg = max(0.0, min(1.0, gg))
                bb = max(0.0, min(1.0, bb))
                lines.append(f"{rr:.6f} {gg:.6f} {bb:.6f}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] {path.relative_to(ROOT)} (size {n})")


def _write_bgm(path: Path, dur: float = 60.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # pink noise nhẹ + sine thấp — ambient SAMPLE, không copy nhạc có bản quyền
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.025:d={dur}:sample_rate=44100",
        "-f", "lavfi", "-i", f"sine=frequency=110:duration={dur}:sample_rate=44100",
        "-f", "lavfi", "-i", f"sine=frequency=164.81:duration={dur}:sample_rate=44100",
        "-filter_complex",
        "[0:a]volume=0.7[n];"
        "[1:a]volume=0.08[a];"
        "[2:a]volume=0.05[b];"
        "[n][a][b]amix=inputs=3:duration=first:normalize=0,"
        "lowpass=f=2500,volume=0.45,"
        f"afade=t=in:d=2,afade=t=out:st={max(0.0, dur - 2):.1f}:d=2",
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-800:] if r.stderr else "ffmpeg fail", file=sys.stderr)
        raise SystemExit(f"ffmpeg BGM lỗi (code {r.returncode})")
    print(f"[ok] {path.relative_to(ROOT)} ({dur:.0f}s SAMPLE ambient)")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    _write_grain(OVERLAYS / "grain.png")
    _write_cube(LUTS / "soft_contrast.cube")
    _write_bgm(BGM / "sample_ambient.mp3")
    print("[done] sample assets sẵn sàng — thay BGM bằng track có quyền dùng trước khi đăng.")


if __name__ == "__main__":
    main()
