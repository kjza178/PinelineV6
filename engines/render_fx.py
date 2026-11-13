"""Helpers: uniqueness filters + library BGM paths (copyright-safe audio)."""
from __future__ import annotations

import os
import random
from pathlib import Path

from core.config import get as cfg

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
LUT_DIR = ASSETS / "luts"
OVERLAY_DIR = ASSETS / "overlays"
FRAMES_DIR = OVERLAY_DIR / "frames"
BGM_DIR = ASSETS / "bgm"

_BGM_EXT = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus"}
_FRAME_EXT = {".png", ".webp"}


def _esc_movie(path: Path) -> str:
    """Escape path for ffmpeg movie= / lut3d= file=."""
    s = str(path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return s


def resolve_lut_file() -> Path | None:
    env = (os.getenv("RENDER_LUT_FILE") or cfg("RENDER_LUT_FILE", "") or "").strip()
    if env:
        p = Path(env)
        return p if p.is_file() else None
    preferred = LUT_DIR / "soft_contrast.cube"
    if preferred.is_file():
        return preferred
    if LUT_DIR.is_dir():
        cubes = sorted(LUT_DIR.glob("*.cube"))
        return cubes[0] if cubes else None
    return None


def resolve_grain_file() -> Path | None:
    env = (os.getenv("RENDER_GRAIN_FILE") or cfg("RENDER_GRAIN_FILE", "") or "").strip()
    if env:
        p = Path(env)
        return p if p.is_file() else None
    p = OVERLAY_DIR / "grain.png"
    return p if p.is_file() else None


def list_bgm_files() -> list[Path]:
    env = (os.getenv("RENDER_BGM_FILE") or cfg("RENDER_BGM_FILE", "") or "").strip()
    if env:
        p = Path(env)
        return [p] if p.is_file() else []
    if not BGM_DIR.is_dir():
        return []
    return sorted(
        f for f in BGM_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in _BGM_EXT
    )


def pick_bgm(job_key: str | None = None) -> Path | None:
    files = list_bgm_files()
    if not files:
        return None
    if len(files) == 1:
        return files[0]
    if job_key:
        rng = random.Random(job_key)
        return files[rng.randrange(len(files))]
    return random.choice(files)


def default_audio_mode() -> str:
    """voice_bgm nếu có thư viện BGM; không thì voice_only (không nhạc gốc)."""
    return "voice_bgm" if list_bgm_files() else "voice_only"


def clamp_zoom(zoom: float | str | None) -> float:
    if zoom is None:
        return 1.12
    if isinstance(zoom, str):
        z_str = zoom.strip().lower()
        if z_str in ("off", "none", "0", "false", "no", "1", "1.0", "1.00"):
            return 1.0
        try:
            zoom = float(z_str)
        except ValueError:
            return 1.12
    z = float(zoom)
    if z <= 1.01:
        return 1.0
    allowed = (1.0, 1.10, 1.12, 1.15)
    return min(allowed, key=lambda a: abs(a - z))


def normalize_grain(level: str | None) -> str:
    g = (level or "weak").strip().lower()
    if g in ("0", "no", "false", "none", "off"):
        return "off"
    if g in ("med", "medium", "2"):
        return "medium"
    if g in ("strong", "hi", "high", "3"):
        return "strong"
    return "weak"


def _eq_fallback() -> str:
    c = cfg("RENDER_EQ_CONTRAST", "1.05") or "1.05"
    b = cfg("RENDER_EQ_BRIGHTNESS", "0.02") or "0.02"
    s = cfg("RENDER_EQ_SATURATION", "1.08") or "1.08"
    return f"eq=contrast={c}:brightness={b}:saturation={s}"


def build_motion_zoompan(W: int, H: int, zoom: float, fps: float) -> str:
    """Ken Burns nhẹ + Drift: scale cố định theo zoom + crop sin(t).

    Tránh dấu phẩy trong expression (Windows/filtergraph dễ vỡ dù đã escape).
    """
    z0 = max(1.01, float(zoom))
    sw = int(W * z0) // 2 * 2
    sh = int(H * z0) // 2 * 2
    amp = max(4, int(min(W, H) * 0.008))
    amp_y = max(3, amp - 2)
    # Ken Burns cực nhẹ: thêm ~2% scale cố định trên base zoom (đủ đổi pixel)
    sw2 = int(sw * 1.02) // 2 * 2
    sh2 = int(sh * 1.02) // 2 * 2
    return (
        f"scale={sw2}:{sh2},"
        f"crop={W}:{H}:(iw-ow)/2+sin(t*0.35)*{amp}:(ih-oh)/2+sin(t*0.27)*{amp_y}"
    )


def build_static_zoom(W: int, H: int, zoom: float) -> str | None:
    if not zoom or zoom <= 1.01:
        return None
    sw, sh = int(W * zoom) // 2 * 2, int(H * zoom) // 2 * 2
    return f"scale={sw}:{sh},crop={W}:{H}"


def lut3d_filter_arg(lut: Path, work_dir: Path | None = None) -> str:
    """Đường dẫn LUT an toàn cho filtergraph Windows.

    Drive letter ``Z:`` trong ``lut3d=file=...`` bị ffmpeg parse thành option
    (lỗi ``Undefined constant ... /Pineline/assets/...``) → NVENC fail → fallback
    CPU. Cách ổn định: copy LUT vào work_dir, chỉ dùng basename
    ``_render_lut.cube`` — ffmpeg phải chạy với ``cwd=work_dir``.
    """
    src = Path(lut)
    if work_dir is not None:
        dst = Path(work_dir) / "_render_lut.cube"
        try:
            if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                dst.write_bytes(src.read_bytes())
            return "lut3d=file=_render_lut.cube"
        except OSError as e:
            print(f"[render_fx] copy LUT lỗi ({e}) → thử escape tuyệt đối", flush=True)
    # fallback: escape — vẫn có thể fail trên Windows drive letter
    return f"lut3d=file='{_esc_movie(src)}'"


def _read_cube(path: Path) -> tuple[int, list[tuple[float, float, float]]]:
    n, rows = 0, []
    for ln in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.upper().startswith("LUT_3D_SIZE"):
            n = int(s.split()[1])
            continue
        if s[0].isalpha():
            continue
        v = s.split()
        if len(v) >= 3:
            rows.append((float(v[0]), float(v[1]), float(v[2])))
    return n, rows


def separable_lutrgb(lut: Path, tol: float = 1e-4) -> str | None:
    """LUT 3D mà mỗi kênh ra chỉ phụ thuộc kênh vào cùng tên → lutrgb 1D tương đương.

    lutrgb = bảng tra 256 giá trị/kênh, nhanh hơn lut3d (tetrahedral) ~40% toàn graph,
    kết quả trùng bit với lut3d (nội suy tuyến tính giữa các nút như lut3d).
    """
    try:
        n, rows = _read_cube(lut)
    except (OSError, ValueError):
        return None
    if n < 2 or len(rows) != n ** 3:
        return None

    def at(r: int, g: int, b: int) -> tuple[float, float, float]:
        return rows[r + g * n + b * n * n]

    for b in range(n):
        for g in range(n):
            for r in range(n):
                o = at(r, g, b)
                if (abs(o[0] - at(r, 0, 0)[0]) > tol or abs(o[1] - at(0, g, 0)[1]) > tol
                        or abs(o[2] - at(0, 0, b)[2]) > tol):
                    return None
    curves = ([at(i, 0, 0)[0] for i in range(n)], [at(0, i, 0)[1] for i in range(n)],
              [at(0, 0, i)[2] for i in range(n)])
    k = n - 1

    def expr(c: list[float]) -> str:
        e = f"{c[k]:.6f}"
        for i in range(k - 1, -1, -1):
            seg = f"({c[i]:.6f}+({c[i + 1]:.6f}-{c[i]:.6f})*(X-{i / k:.6f})*{k})"
            e = f"if(lt(X\\,{(i + 1) / k:.6f})\\,{seg}\\,{e})"
        return "'clip(maxval*" + e.replace("X", "(val/maxval)") + "\\,0\\,maxval)'"

    return f"lutrgb=r={expr(curves[0])}:g={expr(curves[1])}:b={expr(curves[2])}"


def append_lut_filters(parts: list[str], enabled: bool,
                       work_dir: Path | None = None) -> str:
    """Thêm LUT hoặc eq. Trả nhãn dùng thực tế."""
    if not enabled:
        return "lut=off"
    lut = resolve_lut_file()
    if lut:
        sep = separable_lutrgb(lut)
        if sep:
            parts.append(sep)
            return f"lutrgb:{lut.name}"
        parts.append(lut3d_filter_arg(lut, work_dir))
        return f"lut3d:{lut.name}"
    parts.append(_eq_fallback())
    return "eq_fallback"


def grain_noise_alls(level: str) -> float:
    g = normalize_grain(level)
    if g == "strong":
        return 1.5
    if g == "medium":
        return 1.0
    return 0.5


def append_inline_noise_grain(parts: list[str], level: str) -> str:
    """Film grain qua noise= (ổn định). PNG trong assets dùng khi blend input riêng."""
    g = normalize_grain(level)
    if g == "off":
        return "grain=off"
    allf = grain_noise_alls(g)
    parts.append(f"noise=alls={allf}:allf=t")
    png = resolve_grain_file()
    tag = "png+noise" if png else "noise"
    return f"grain={g}({tag})"


def grain_blend_fragment(vlabel: str, out_label: str, level: str, W: int, H: int
                         ) -> tuple[str | None, str]:
    """PNG blend tắt mặc định (movie loop dễ treo). Giữ API cho phase sau."""
    return None, append_inline_noise_grain([], level)


def bgm_volume() -> float:
    try:
        return float(cfg("RENDER_BGM_VOL", "0.14") or os.getenv("RENDER_BGM_VOL") or "0.14")
    except ValueError:
        return 0.14


def dub_volume() -> float:
    """Âm lượng giọng Việt khi mix (VieNeu đã chuẩn hoá ~0.9 → boost vừa phải 1.25 tránh vỡ tiếng)."""
    try:
        v = float(cfg("RENDER_DUB_VOL", "1.25") or os.getenv("RENDER_DUB_VOL") or "1.25")
    except ValueError:
        v = 1.25
    return max(0.5, min(3.0, v))


def list_frame_files() -> list[Path]:
    """PNG/WebP khung trong assets/overlays/frames (+ overlays gốc)."""
    env = (os.getenv("RENDER_FRAME_FILE") or cfg("RENDER_FRAME_FILE", "") or "").strip()
    if env:
        p = Path(env)
        return [p] if p.is_file() else []
    found: list[Path] = []
    for root in (FRAMES_DIR, OVERLAY_DIR):
        if not root.is_dir():
            continue
        for f in sorted(root.iterdir()):
            if not f.is_file() or f.suffix.lower() not in _FRAME_EXT:
                continue
            if f.name.lower() == "grain.png":
                continue
            if f.name.startswith("sample_") and root == FRAMES_DIR:
                # giữ sample nhưng xếp sau file user
                continue
            found.append(f)
    # sample_* cuối danh sách
    if FRAMES_DIR.is_dir():
        for f in sorted(FRAMES_DIR.glob("sample_*.png")):
            if f not in found:
                found.append(f)
    # unique by resolve
    seen: set[str] = set()
    out: list[Path] = []
    for f in found:
        k = str(f.resolve())
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def pick_frame(choice: str | None = None, job_key: str | None = None) -> Path | None:
    """choice: off | auto | tên file | đường dẫn."""
    key = (choice if choice is not None else (
        os.getenv("RENDER_FRAME") or cfg("RENDER_FRAME", "off") or "off"
    )).strip()
    if not key or key.lower() in ("0", "off", "none", "false", "no"):
        return None
    files = list_frame_files()
    if not files:
        return None
    if key.lower() in ("auto", "1", "true", "yes", "on"):
        if len(files) == 1:
            return files[0]
        rng = random.Random(job_key or "frame")
        return files[rng.randrange(len(files))]
    # exact name / path
    p = Path(key)
    if p.is_file():
        return p
    for f in files:
        if f.name == key or f.stem == key:
            return f
    # partial match
    kl = key.lower()
    for f in files:
        if kl in f.name.lower():
            return f
    return None


def prepare_frame_file(frame: Path, work_dir: Path) -> str:
    """Copy khung vào job → basename cho movie= (cwd=work_dir)."""
    dst = Path(work_dir) / "_render_frame.png"
    src = Path(frame)
    try:
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime or dst.stat().st_size != src.stat().st_size:
            # luôn PNG: nếu webp thì Pillow convert; png copy bytes
            if src.suffix.lower() == ".png":
                dst.write_bytes(src.read_bytes())
            else:
                from PIL import Image
                Image.open(src).convert("RGBA").save(dst)
    except OSError as e:
        print(f"[render_fx] copy frame lỗi ({e})", flush=True)
        return str(src)
    return dst.name


def brand_text_value(override: str | None = None) -> str:
    t = (override if override is not None else (
        os.getenv("RENDER_BRAND_TEXT") or cfg("RENDER_BRAND_TEXT", "") or ""
    )).strip()
    return t


def normalize_brand_mode(mode: str | None = None, enabled: bool | None = None) -> str:
    """off | drift | corners | drift_seed."""
    raw = (mode if mode is not None else (
        os.getenv("RENDER_BRAND_MODE") or cfg("RENDER_BRAND_MODE", "") or ""
    )).strip().lower()
    if not raw:
        if enabled is False:
            return "off"
        if enabled is True or brand_enabled(None):
            raw = "drift"
        else:
            return "off"
    aliases = {
        "0": "off", "off": "off", "none": "off", "false": "off", "no": "off",
        "1": "drift", "on": "drift", "true": "drift", "yes": "drift",
        "a": "drift", "sin": "drift", "drift": "drift",
        "b": "corners", "corner": "corners", "corners": "corners", "jump": "corners",
        "c": "drift_seed", "seed": "drift_seed", "random": "drift_seed",
        "drift_seed": "drift_seed", "drift-seed": "drift_seed",
    }
    return aliases.get(raw, "drift" if raw else "off")


def brand_enabled(flag: bool | None = None) -> bool:
    if flag is not None:
        return bool(flag)
    v = (os.getenv("RENDER_BRAND") or cfg("RENDER_BRAND", "0") or "0").strip().lower()
    return v not in ("0", "off", "none", "false", "no", "")


def brand_xy_expr(mode: str, job_key: str | None = None) -> tuple[str, str]:
    """Biểu thức ffmpeg x/y cho chữ/logo brand."""
    m = normalize_brand_mode(mode)
    if m == "corners":
        # Đổi góc mỗi 40s: TL TR BL BR
        x = (
            "if(eq(mod(floor(t/40),4),0),w*0.06,"
            "if(eq(mod(floor(t/40),4),1),w*0.70,"
            "if(eq(mod(floor(t/40),4),2),w*0.06,w*0.70)))"
        )
        y = (
            "if(eq(mod(floor(t/40),4),0),h*0.05,"
            "if(eq(mod(floor(t/40),4),1),h*0.05,"
            "if(eq(mod(floor(t/40),4),2),h*0.55,h*0.55)))"
        )
        return x, y
    if m == "drift_seed":
        rng = random.Random(job_key or "brand")
        fx = 0.12 + rng.random() * 0.10
        fy = 0.09 + rng.random() * 0.10
        px = rng.random() * 6.28
        py = rng.random() * 6.28
        return (
            f"w*0.08+(w*0.55)*(0.5+0.5*sin(t*{fx:.3f}+{px:.3f}))",
            f"h*0.06+(h*0.48)*(0.5+0.5*sin(t*{fy:.3f}+{py:.3f}))",
        )
    # drift (mặc định)
    return (
        "w*0.08+(w*0.55)*(0.5+0.5*sin(t*0.17))",
        "h*0.06+(h*0.48)*(0.5+0.5*sin(t*0.13+1.7))",
    )


def list_brand_logo_files() -> list[Path]:
    roots = [
        OVERLAY_DIR / "logos",
        OVERLAY_DIR / "brand",
        OVERLAY_DIR,
    ]
    env = (os.getenv("RENDER_BRAND_LOGO_FILE") or cfg("RENDER_BRAND_LOGO_FILE", "") or "").strip()
    if env:
        p = Path(env)
        return [p] if p.is_file() else []
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(root.iterdir()):
            if not f.is_file() or f.suffix.lower() not in _FRAME_EXT:
                continue
            if f.name.lower() in ("grain.png",) or f.name.startswith("_"):
                continue
            # overlays gốc: chỉ nhận tên gợi logo
            if root == OVERLAY_DIR:
                nl = f.name.lower()
                if not any(k in nl for k in ("logo", "brand", "channel", "avatar", "icon")):
                    continue
            k = str(f.resolve())
            if k not in seen:
                seen.add(k)
                out.append(f)
    return out


def pick_brand_logo(choice: str | None = None, job_key: str | None = None) -> Path | None:
    key = (choice if choice is not None else (
        os.getenv("RENDER_BRAND_LOGO") or cfg("RENDER_BRAND_LOGO", "off") or "off"
    )).strip()
    if not key or key.lower() in ("0", "off", "none", "false", "no"):
        return None
    files = list_brand_logo_files()
    if key.lower() in ("auto", "1", "true", "yes", "on"):
        if not files:
            return None
        if len(files) == 1:
            return files[0]
        return files[random.Random(job_key or "logo").randrange(len(files))]
    p = Path(key)
    if p.is_file():
        return p
    for f in files:
        if f.name == key or f.stem == key or key.lower() in f.name.lower():
            return f
    # absolute/relative path still
    if p.suffix.lower() in _FRAME_EXT and p.exists():
        return p
    return None


def prepare_brand_logo(logo: Path, work_dir: Path) -> str:
    dst = Path(work_dir) / "_render_brand_logo.png"
    src = Path(logo)
    try:
        if src.suffix.lower() == ".png":
            if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                dst.write_bytes(src.read_bytes())
        else:
            from PIL import Image
            Image.open(src).convert("RGBA").save(dst)
    except OSError as e:
        print(f"[render_fx] copy brand logo lỗi ({e})", flush=True)
        return str(src)
    return dst.name


def brand_drawtext_filter(text: str, H: int, fontfile: str,
                          x_expr: str, y_expr: str) -> str:
    """Chữ kênh với x/y expression đã chọn."""
    if not text:
        return ""
    try:
        op = float(os.getenv("RENDER_BRAND_OPACITY") or cfg("RENDER_BRAND_OPACITY", "0.72") or "0.72")
    except ValueError:
        op = 0.72
    op = max(0.25, min(op, 1.0))
    fs = max(20, int(H * 0.030))
    esc = (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )
    return (
        f"drawtext=fontfile='{fontfile}':text='{esc}':fontsize={fs}:"
        f"fontcolor=white@{op:.2f}:borderw=3:bordercolor=black@0.85:"
        f"x='{x_expr}':y='{y_expr}'"
    )


def apply_brand_overlay(body: str, *, W: int, H: int, fontfile: str,
                        work_dir: Path | None,
                        mode: str | None = None,
                        enabled: bool | None = None,
                        text: str | None = None,
                        logo: str | None = None,
                        job_key: str | None = None,
                        ) -> tuple[str, dict]:
    """Ghép brand (chữ ± logo) vào filter chain. Trả (body, meta)."""
    meta = {"brand": "off", "brand_mode": "off", "brand_logo": "off"}
    m = normalize_brand_mode(mode, enabled)
    if m == "off":
        return body, meta
    btxt = brand_text_value(text)
    logo_p = pick_brand_logo(logo, job_key=job_key)
    if not btxt and not logo_p:
        print("[render] brand bật nhưng thiếu text/logo → bỏ", flush=True)
        return body, meta
    x, y = brand_xy_expr(m, job_key=job_key)
    meta["brand_mode"] = m
    if logo_p and work_dir is not None:
        S = max(36, int(H * 0.055)) // 2 * 2
        lname = prepare_brand_logo(logo_p, work_dir)
        body = (
            f"{body}[vb0];movie={lname},format=rgba,scale={S}:{S}[blg];"
            f"[vb0][blg]overlay=x='{x}':y='{y}'"
        )
        meta["brand_logo"] = logo_p.name
        if btxt:
            xt = f"({x})+{S}+8"
            body = f"{body},{brand_drawtext_filter(btxt, H, fontfile, xt, y)}"
            meta["brand"] = btxt[:40]
    elif btxt:
        body = f"{body},{brand_drawtext_filter(btxt, H, fontfile, x, y)}"
        meta["brand"] = btxt[:40]
    print(f"[render] brand mode={m} text={meta.get('brand')!r} logo={meta.get('brand_logo')}",
          flush=True)
    return body, meta
