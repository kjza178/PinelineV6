"""
meta_studio.py — Module sinh Metadata YouTube (Title / Description / Tags / AI Image Prompt)
và dựng Thumbnail 16:9 chuẩn YouTube từ poster dọc + text giật gân.
"""
from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
import io
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

ROOT = Path(__file__).resolve().parents[1]
FONTS_DIR = Path("C:/Windows/Fonts")
DEFAULT_FONT = str(FONTS_DIR / "arialbd.ttf") if (FONTS_DIR / "arialbd.ttf").exists() else str(FONTS_DIR / "arial.ttf")
SEGOE_FONT = str(FONTS_DIR / "segoeuib.ttf") if (FONTS_DIR / "segoeuib.ttf").exists() else DEFAULT_FONT

# Thư mục lưu thumbnail đã tạo
THUMB_DIR = ROOT / "data" / "thumbnails"
THUMB_DIR.mkdir(parents=True, exist_ok=True)

# Thư mục lưu Metadata dùng chung cho cả bộ phim (Series)
SERIES_META_DIR = ROOT / "data" / "series_meta"
SERIES_META_DIR.mkdir(parents=True, exist_ok=True)


def to_series_slug(text: str) -> str:
    """Chuyển tên series sang slug không dấu (vd: 'Sinh Tồn Sa Mạc' -> 'sinh_ton_sa_mac')."""
    if not text:
        return ""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", str(text))
    ascii_text = "".join([c for c in nfkd if not unicodedata.combining(c)])
    ascii_text = ascii_text.replace("đ", "d").replace("Đ", "D")
    clean = re.sub(r"[^\w\s]", "", ascii_text).strip().lower()
    return re.sub(r"[\s_]+", "_", clean)[:50]


def clean_series_name(name: str, fallback: str = "") -> str:
    """Làm sạch tên series, loại bỏ toàn bộ chữ Hán, số phần P1/P2 thừa, và ký tự thừa."""
    if not name:
        return fallback.strip()
    s = str(name).strip()

    # 1. Bỏ tiền tố/hậu tố Phần X, P1, Mùa X, S1...
    s = re.sub(r"^(?:phần|phan|mùa|mua|season|s|tập|p)\s*\d+\s*[|:\-–—]\s*", "", s, flags=re.I)
    s = re.sub(r"\(?\s*(?:phần|phan|mùa|mua|season|s|tập|p)\s*\d+\s*\)?", "", s, flags=re.I)

    # 2. Nếu có dấu phân cách : | - tách và lấy phần tên chính
    parts = re.split(r"[:|–—]", s)
    cand = parts[0].strip(" -_()[]") if parts else s.strip(" -_()[]")

    # 3. Kiểm tra chữ Hán
    if re.search(r"[\u4e00-\u9fff]", cand):
        # Thử bỏ phần chữ Hán nếu có cả tiếng Việt lẫn chữ Hán
        no_zh = re.sub(r"[\u4e00-\u9fff\s,，:：\-–—]+", " ", cand).strip()
        if len(no_zh) >= 3 and not re.search(r"[\u4e00-\u9fff]", no_zh):
            return no_zh.strip(" -_()[]")
        # Nếu toàn bộ là chữ Hán, thử xem fallback có tiếng Việt không
        if fallback and not re.search(r"[\u4e00-\u9fff]", fallback):
            return clean_series_name(fallback)
    else:
        return cand.strip(" -_()[]")

    return cand.strip(" -_()[]")


_CLICHE_HOOKS = frozenset({
    "NGHỊCH THIÊN", "ĐỒ THẦN", "THỨC TỈNH", "TUYỆT THẾ", "THỨC TỈNH TUYỆT THẾ",
    "LẬT NGƯỢC THẾ CỜ", "THÂN PHẬN BÍ ẨN", "MỘT CHIÊU ÁP ĐẢO", "PHẾ VẬT THỨC TỈNH",
    "NGHỊCH THIÊN ĐỒ THẦN", "MỘT KIẾM TRẢM THẦN",
})


def hooks_from_series_title(title_vi: str, title_zh: str = "") -> list[str]:
    """Tách hook 2 dòng từ tên phim thật — không bịa cụm sáo rỗng.

    VD: «Võ Đạo Cộng Điểm: Lão Lục Thời Loạn Thành Thánh»
        → [\"VÕ ĐẠO CỘNG ĐIỂM\", \"LÃO LỤC THÀNH THÁNH\"].
    """
    # Giữ nguyên dấu : để tách 2 vế — KHÔNG qua clean_series_name (hàm đó cắt mất vế sau)
    raw = (title_vi or "").strip()
    raw = re.sub(r"^(?:phần|phan|mùa|mua|season|s|tập|p)\s*\d+\s*[|:\-–—]\s*", "", raw, flags=re.I)
    raw = re.sub(r"[\u4e00-\u9fff]+", " ", raw).strip()  # bỏ chữ Hán lẫn
    raw = re.sub(r"\s+", " ", raw).strip(" -_()[]")
    if not raw and title_zh:
        raw = re.sub(r"\s+", " ", str(title_zh).strip())
    if not raw:
        return ["TRỌN BỘ", "THUYẾT MINH"]

    parts = [p.strip(" -_()[]") for p in re.split(r"[:：|–—]", raw) if p.strip(" -_()[]")]
    if len(parts) >= 2:
        a, b = parts[0], parts[1]
        bw = b.split()
        if len(bw) > 4:
            # Ưu tiên cụm đầu + cụm cuối (Lão Lục … Thành Thánh)
            b = " ".join(bw[:2] + bw[-2:])
        aw = a.split()
        if len(aw) > 5:
            a = " ".join(aw[:5])
        return [a.upper(), b.upper()]

    words = raw.split()
    if len(words) >= 4:
        mid = max(2, len(words) // 2)
        return [" ".join(words[:mid]).upper(), " ".join(words[mid:mid + 4]).upper()]
    if len(words) >= 2:
        return [words[0].upper(), " ".join(words[1:]).upper()]
    return [raw.upper()[:28], "THUYẾT MINH"]


def _hook_anchored_to_title(hook: str, title_vi: str) -> bool:
    """≥75% token (≥3 ký tự) của hook phải nằm trong tên phim — chặn bịa «ẨN NHẪN»."""
    title_u = re.sub(r"\s+", " ", (title_vi or "").upper())
    tokens = [t for t in re.findall(r"[0-9A-ZÀ-Ỹ]+", hook.upper()) if len(t) >= 3]
    if not tokens or not title_u:
        return False
    hits = sum(1 for t in tokens if t in title_u)
    return (hits / len(tokens)) >= 0.75


def sanitize_hook_texts(hooks: list | None, title_vi: str = "", title_zh: str = "") -> list[str]:
    """Loại hook sáo / trống / không neo tên phim; fallback tách từ tên phim."""
    derived = hooks_from_series_title(title_vi, title_zh)
    cleaned: list[str] = []
    for h in (hooks or []):
        s = re.sub(r"\s+", " ", str(h or "").strip()).upper()
        if not s or s in _CLICHE_HOOKS:
            continue
        if title_vi and not _hook_anchored_to_title(s, title_vi):
            continue
        cleaned.append(s[:32])
    if len(cleaned) >= 2:
        return cleaned[:2]
    if len(cleaned) == 1:
        return [cleaned[0], derived[1] if derived[1] != cleaned[0] else derived[0]]
    return derived[:2]


_BADGE_ALLOWED = ("NEW", "HOT")
_BADGE_BANNED = frozenset({
    "TRỌN BỘ THUYẾT MINH", "TRỌN BỘ", "THUYẾT MINH", "FULL", "FULL EPISODE",
    "TRON BO THUYET MINH",
})


def pick_badge_text(raw: str | None = "", *, job_id: str = "", title_vi: str = "") -> str:
    """Badge góc trên: chỉ NEW hoặc HOT — không dùng «TRỌN BỘ THUYẾT MINH»."""
    s = re.sub(r"\s+", " ", str(raw or "").strip()).upper()
    if s in _BADGE_ALLOWED:
        return s
    if s in _BADGE_BANNED or "THUYẾT MINH" in s or "THUYET MINH" in s or "TRỌN BỘ" in s:
        s = ""
    if s in ("NEW!", "HOT!", "NEW ", "HOT "):
        return s.strip("! ")
    # xen kẽ ổn định theo job/title để không luôn NEW
    seed = (job_id or title_vi or "x").encode("utf-8", errors="ignore")
    return _BADGE_ALLOWED[sum(seed) % 2]


def vi_to_image_latin(text: str) -> str:
    """Bỏ dấu tiếng Việt → LATIN HOA để model ảnh ít vỡ chữ (₫/ơ/ệ hay bị gen sai).

    Metadata/title vẫn giữ dấu; chỉ chuỗi nung vào ảnh dùng bản này.
    """
    import unicodedata
    s = str(text or "").strip()
    if not s:
        return ""
    s = s.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", s)
    ascii_s = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_s = re.sub(r"[^0-9A-Za-z\s\-]", " ", ascii_s)
    ascii_s = re.sub(r"\s+", " ", ascii_s).strip().upper()
    return ascii_s[:40]


def build_master_image_prompt(
    *,
    title_vi: str = "",
    title_zh: str = "",
    story_moment: str = "",
    visual_dna: str = "",
    palette: str = "",
    layout: str = "",
    emotional_trigger: str = "",
    hook_texts: list | None = None,
    badge_text: str = "NEW",
    sub_text: str = "",
    font_material: str = "",
) -> str:
    """Prompt ảnh AI: scene + chữ 3D LATIN (không dấu) — giảm gen sai chữ Việt có dấu.

    Badge NEW/HOT. Hook/sub đã sanitize; bản nung ảnh = vi_to_image_latin(...).
    """
    title = clean_series_name(title_vi or "", fallback=title_vi) or title_zh or "donghua"
    hooks = sanitize_hook_texts(hook_texts, title_vi=title_vi or title, title_zh=title_zh)
    badge = pick_badge_text(badge_text, title_vi=title_vi)
    sub = (sub_text or title).strip()[:48]
    moment = (story_moment or emotional_trigger or "decisive confrontation from the plot").strip()
    dna = (visual_dna or "main character faithful to reference image").strip()
    colors = (palette or "palette matching the film's real setting, not generic fire-gold").strip()
    lay = (layout or "character on the right third, bold 3D text on the left").strip()
    mat = (font_material or "premium 3D metallic type matching the chosen color system").strip()
    line1 = vi_to_image_latin(hooks[0])
    line2 = vi_to_image_latin(hooks[1])
    sub_img = vi_to_image_latin(sub)
    # Spell-out giúp model ít đổi chữ: V O   D A O
    def _spell(s: str) -> str:
        return " · ".join(list(s.replace(" ", "  ")))

    return (
        "REFERENCE IMAGE MANDATE: Strictly preserve the exact facial features, hairstyle, hair color, "
        "gender, outfit design, and 3D/2D rendering style of the main character from the attached "
        "reference image(s).\n\n"
        f"Create a high-CTR 16:9 horizontal YouTube thumbnail for the donghua «{title}».\n\n"
        f"STORY MOMENT (must match real plot, not a generic trope):\n{moment}\n\n"
        f"CHARACTER & POSE:\n{dna}\n"
        "Diverse pose matching THIS film — do NOT default to open palm shooting fire at camera.\n\n"
        f"ENVIRONMENT & COLOR PALETTE:\n{colors}\n{lay}\n\n"
        "INTEGRATED 3D LATIN TYPOGRAPHY (LEFT / TOP-LEFT — NO VIETNAMESE DIACRITICS):\n"
        "Image models corrupt Vietnamese tone marks (ă â ê ô ơ ư ạ ệ…). "
        "Therefore ALL on-image text MUST be ASCII uppercase Latin ONLY.\n"
        f"- Top badge ribbon EXACTLY: \"{badge}\" "
        "(NEW or HOT only — NEVER 'TRỌN BỘ THUYẾT MINH')\n"
        f"- Main 3D Headline EXACTLY two lines (copy character-perfect, do not invent letters):\n"
        f"  1) \"{line1}\"\n"
        f"  2) \"{line2}\"\n"
        f"  Letter check line1: {_spell(line1)}\n"
        f"  Letter check line2: {_spell(line2)}\n"
        f"  Material: {mat}. Sharp, high-contrast, readable on mobile.\n"
        f"- Bottom subtitle banner EXACTLY: \"{sub_img}\"\n"
        "Do NOT add extra words. Do NOT restore diacritics. Do NOT write Chinese characters.\n"
        "Avoid cluttered HUD/stat bars unless the plot is literally a system/RPG panel moment.\n\n"
        "QUALITY: 16:9 widescreen, Unreal Engine 5 / premium Chinese 3D donghua key art, "
        "high contrast, rich depth of field, clear focal hierarchy."
    )


def finalize_thumb_meta_fields(
    data: dict, title_vi: str = "", title_zh: str = "", job_id: str = "",
) -> dict:
    """Sanitize hook + badge NEW/HOT; dựng lại master_image_prompt (AI vẽ chữ, không Pillow)."""
    if not isinstance(data, dict):
        return data
    hooks = sanitize_hook_texts(data.get("hook_texts"), title_vi, title_zh)
    data["hook_texts"] = hooks
    opts = []
    for o in (data.get("thumb_text_options") or []):
        s = re.sub(r"\s+", " ", str(o or "").strip()).upper()
        if s and s not in _CLICHE_HOOKS:
            opts.append(s[:40])
    if len(opts) < 2:
        opts = [f"{hooks[0]} {hooks[1]}".strip(), hooks[0], hooks[1]]
    data["thumb_text_options"] = opts[:4]

    sub = clean_series_name(str(data.get("sub_text") or title_vi or ""), fallback=title_vi or "")
    data["sub_text"] = (sub or hooks[0])[:48]
    data["badge_text"] = pick_badge_text(
        data.get("badge_text"), job_id=job_id, title_vi=title_vi or sub,
    )

    concept = data.get("thumbnail_concept") if isinstance(data.get("thumbnail_concept"), dict) else {}
    typo = data.get("typography_direction") if isinstance(data.get("typography_direction"), dict) else {}
    data["master_image_prompt"] = build_master_image_prompt(
        title_vi=title_vi or sub,
        title_zh=title_zh,
        story_moment=str(concept.get("story_moment") or ""),
        visual_dna=str(concept.get("visual_dna") or ""),
        palette=str(
            concept.get("selected_concept")
            or typo.get("color")
            or concept.get("visual_conflict")
            or ""
        ),
        layout=str(concept.get("layout") or typo.get("layout") or ""),
        emotional_trigger=str(concept.get("emotional_trigger") or ""),
        hook_texts=hooks,
        badge_text=data["badge_text"],
        sub_text=data["sub_text"],
        font_material=str(typo.get("effects") or typo.get("font_style") or ""),
    )
    data["ai_image_prompt"] = data["master_image_prompt"]
    if typo is not None:
        typo = dict(typo)
        typo["text_exact"] = hooks
        data["typography_direction"] = typo
    return data


def get_series_key(job_id: str) -> tuple[str, str]:
    """Trả về (series_key, series_display_name).
    series_key: group_id nếu có, hoặc slug tên bộ.
    series_display_name: Tên tiếng Việt sạch (không bao giờ chứa chữ Hán nếu có tên tiếng Việt).
    """
    from core.job_queue import get_job_info
    info = get_job_info(job_id)
    gid = info.get("group_id")
    gtitle = (info.get("group_title") or "").strip()
    series = (info.get("series") or "").strip()
    title = (info.get("title") or "").strip()

    # Tra cứu trước trong cache series_meta nếu gid đã có tên tiếng Việt chuẩn
    stored_name = ""
    if gid:
        sm = get_series_meta(str(gid).strip())
        if sm and sm.get("series_name") and not re.search(r"[\u4e00-\u9fff]", sm["series_name"]):
            stored_name = sm["series_name"].strip()

    # Tìm tên hiển thị tiếng Việt ưu tiên: stored_name -> series -> title -> gtitle
    chosen_name = ""
    for cand in [stored_name, series, title]:
        if cand and not re.search(r"[\u4e00-\u9fff]", cand):
            c = clean_series_name(cand)
            if c:
                chosen_name = c
                break

    if not chosen_name:
        chosen_name = clean_series_name(series or gtitle or title or job_id, fallback=job_id)

    if gid:
        return str(gid).strip(), chosen_name.strip()

    slug = to_series_slug(chosen_name) or f"series_{job_id[:8]}"
    return slug, chosen_name.strip()


def get_series_meta(series_key: str, series_name: str = "") -> dict | None:
    """Lấy metadata đã lưu của series (hỗ trợ tra cứu chéo qua group_id hoặc tên series)."""
    if not series_key and not series_name:
        return None

    # 1. Tra cứu theo series_key
    if series_key:
        f = SERIES_META_DIR / f"{series_key}.json"
        if f.is_file():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass

    # 2. Tra cứu theo slug tên series
    name_to_check = series_name or series_key
    slug = to_series_slug(name_to_check)
    if slug and slug != series_key:
        f_slug = SERIES_META_DIR / f"{slug}.json"
        if f_slug.is_file():
            try:
                return json.loads(f_slug.read_text(encoding="utf-8"))
            except Exception:
                pass

    # 3. Quét tất cả file json trong SERIES_META_DIR để tìm khớp tên
    if SERIES_META_DIR.is_dir():
        for jf in SERIES_META_DIR.glob("*.json"):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                stored_name = data.get("series_name") or ""
                stored_key = data.get("series_key") or ""
                if series_key and (stored_key == series_key or jf.stem == series_key):
                    return data
                if slug and to_series_slug(stored_name) == slug:
                    return data
            except Exception:
                continue
    return None


def save_series_meta(series_key: str, data: dict, series_name: str = "") -> None:
    """Lưu metadata dùng chung cho cả series (lưu cả group_id và slug tên bộ)."""
    if not series_key or not isinstance(data, dict):
        return
    SERIES_META_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)

    f = SERIES_META_DIR / f"{series_key}.json"
    f.write_text(payload, encoding="utf-8")

    sname = series_name or data.get("series_name") or ""
    slug = to_series_slug(sname)
    if slug and slug != series_key:
        f_slug = SERIES_META_DIR / f"{slug}.json"
        f_slug.write_text(payload, encoding="utf-8")


def get_series_thumbnail(series_key: str, series_name: str = "") -> Path | None:
    """Lấy đường dẫn ảnh thumbnail dùng chung của series."""
    if not series_key and not series_name:
        return None

    # 1. Check trực tiếp theo series_key
    if series_key:
        for ext in (".jpg", ".png", ".jpeg"):
            cand = THUMB_DIR / f"series_{series_key}{ext}"
            if cand.is_file() and cand.stat().st_size > 1000:
                return cand

    # 2. Check theo slug tên series
    name_to_check = series_name or series_key
    slug = to_series_slug(name_to_check)
    if slug and slug != series_key:
        for ext in (".jpg", ".png", ".jpeg"):
            cand = THUMB_DIR / f"series_{slug}{ext}"
            if cand.is_file() and cand.stat().st_size > 1000:
                return cand

    # 3. Check qua metadata JSON đã lưu
    sm = get_series_meta(series_key, series_name)
    if sm:
        if sm.get("thumb_filename"):
            cand = THUMB_DIR / sm["thumb_filename"]
            if cand.is_file() and cand.stat().st_size > 1000:
                return cand
        stored_key = sm.get("series_key")
        if stored_key:
            for ext in (".jpg", ".png", ".jpeg"):
                cand = THUMB_DIR / f"series_{stored_key}{ext}"
                if cand.is_file() and cand.stat().st_size > 1000:
                    return cand
        if sm.get("thumb_path") and Path(sm["thumb_path"]).is_file():
            return Path(sm["thumb_path"])
    return None


def save_series_thumbnail(series_key: str, src_path_or_bytes, series_name: str = "") -> Path:
    """Lưu ảnh thumbnail mẫu của cả series thành series_{series_key}.jpg (và bản alias slug)."""
    target = THUMB_DIR / f"series_{series_key}.jpg"
    if isinstance(src_path_or_bytes, (str, Path)):
        import shutil
        shutil.copyfile(str(src_path_or_bytes), str(target))
    elif isinstance(src_path_or_bytes, (bytes, bytearray)):
        target.write_bytes(src_path_or_bytes)

    slug = to_series_slug(series_name)
    if slug and slug != series_key and target.is_file():
        import shutil
        slug_target = THUMB_DIR / f"series_{slug}.jpg"
        shutil.copyfile(str(target), str(slug_target))

    return target


def _build_fallback_part_meta(clean_series: str, season: int, episode_info: str = "", base_tags: list = None) -> dict:
    """Tạo bộ Title & Description fallback đầy đủ 4 khối chuẩn YouTube SEO khi không kết nối được LLM."""
    hook = "Bị Coi Thường, Ngày Trở Về Khiến Tất Cả Khiếp Sợ!"
    title = f"Phần {season} | {clean_series} - {hook}"

    desc_p1 = (
        f"Giữa những hiểm nguy trùng trùng và nghịch cảnh ngặt nghèo, cơ hội sống sót vô cùng quý giá! "
        f"Liệu bước tiến mới sẽ giúp nhân vật chính vượt qua những thử thách nào trong phần {season}?\n\n"
        f"{clean_series} (Phần {season}) tiếp tục câu chuyện đầy hấp dẫn và hiểm nguy trong thế giới huyền huyễn. "
        f"Nhân vật chính phải tìm cách thích nghi với môi trường khắc nghiệt, tận dụng năng lực đặc biệt và đối mặt với những kẻ thù hùng mạnh.\n\n"
        f"Từ sức mạnh mới được khai phá đến những bước ngoặt bất ngờ, hành trình hứa hẹn nhiều tình tiết kịch tính và gay cấn. "
        f"Cùng theo dõi để khám phá cách nhân vật chính từng bước nghịch thiên cải mệnh, khuynh đảo toàn bộ cục diện!"
    )
    desc_p2 = (
        f"🎬 Tên phim: {clean_series} (Phần {season})\n"
        f"🎙️ Nội dung: Phim thuyết minh tiếng Việt.\n"
        f"👍 Nếu yêu thích thể loại manhua, donghua, sinh tồn và huyền huyễn, hãy nhấn Like, đăng ký kênh và bật chuông thông báo để không bỏ lỡ những video tiếp theo!\n"
        f"💬 Theo bạn, năng lực của nhân vật chính ở phần này đã đủ sức áp đảo đối thủ chưa? Hãy chia sẻ ý kiến ở phần bình luận!"
    )
    desc_p3 = (
        "Lưu ý: Video có thể sử dụng công cụ AI hỗ trợ một số công đoạn biên tập, hình ảnh hoặc hậu kỳ. "
        "Nội dung được biên soạn nhằm mục đích kể lại, bình luận và giải trí. "
        "Các nhân vật, hình ảnh và tác phẩm gốc thuộc quyền của chủ sở hữu tương ứng. "
        "Video không nhằm thay thế tác phẩm gốc."
    )

    import unicodedata
    def _to_tag(t):
        nfkd = unicodedata.normalize("NFKD", str(t))
        clean = "".join([c for c in nfkd if not unicodedata.combining(c)]).replace("đ", "d").replace("Đ", "D")
        return "#" + "".join([w.capitalize() for w in re.sub(r"[^\w\s]", "", clean).split()])

    series_tag = _to_tag(clean_series)
    hashtags = [
        series_tag,
        f"{series_tag}ThuyetMinh",
        "#ManhuaTiengViet",
        "#KeLaiManhua",
        "#Donghua",
        "#TuTien",
        "#HuyenHuyen",
        "#PhimThuyetMinh",
        "#Manhua",
        "#SinhTon",
    ]
    full_desc = f"{desc_p1}\n\n{desc_p2}\n\n{desc_p3}\n\n{' '.join(hashtags)}"

    tags = base_tags or [
        clean_series,
        f"{clean_series} Phần {season}",
        f"{clean_series} Thuyết Minh",
        "Manhua Tiếng Việt",
        "Donghua 3D",
        "Phim Thuyết Minh",
        "Kể Lại Manhua",
        "Tu Tiên",
        "Huyền Huyễn",
        "Mạt Thế Sinh Tồn",
    ]

    return {
        "title": title,
        "description": full_desc,
        "tags": tags,
    }



def get_film_story_info(
    job_id: str = "",
    title_zh: str = "",
    title_vi: str = "",
    summary: str = "",
    episode_info: str = "",
) -> dict:
    """Trích xuất thông tin Tên gốc tiếng Trung, Tên tiếng Việt và Tóm tắt Cốt truyện chuẩn (Baike / Intro / Desc)
    cho phim. TUYỆT ĐỐI KHÔNG đọc file phụ đề .srt để tránh làm nhiễu tiêu đề YouTube.
    """
    clean_zh = str(title_zh or "").strip()
    clean_vi = str(title_vi or "").strip()
    clean_summary = str(summary or "").strip()
    series_id = ""
    cover_url = ""
    tags: list[str] = []

    # 1. Nếu có job_id: Đọc manifest.json và .series.json trong thư mục nguồn
    if job_id:
        try:
            work_dir = ROOT / "work" / str(job_id).strip()
            mf_file = work_dir / "manifest.json"
            if mf_file.is_file():
                mdata = json.loads(mf_file.read_text(encoding="utf-8"))
                req = mdata.get("request") or {}
                if not clean_zh:
                    orig_t = str(req.get("title") or "").strip()
                    orig_t = re.sub(r"\s*\((?:merged|sub|raw)\).*", "", orig_t).strip()
                    if orig_t:
                        clean_zh = orig_t
                if not clean_vi:
                    clean_vi = str(mdata.get("title_vi") or "").strip()
                if not clean_summary:
                    clean_summary = str(req.get("summary") or req.get("desc") or req.get("intro") or "").strip()

                # Kiểm tra thư mục nguồn xem có .series.json không
                orig_p = str(req.get("orig_path") or "")
                if orig_p:
                    src_dir = Path(orig_p).parent
                    series_json = src_dir / ".series.json"
                    if series_json.is_file():
                        try:
                            s_data = json.loads(series_json.read_text(encoding="utf-8"))
                            series_id = str(s_data.get("series_id") or "").strip()
                            if not clean_zh and s_data.get("title"):
                                clean_zh = str(s_data.get("title")).strip()
                        except Exception:
                            pass
        except Exception as e_mf:
            print(f"[meta_studio] get_film_story_info manifest error: {e_mf}", flush=True)

    # 2. Tra cứu film_db nếu còn thiếu thông tin
    if (not clean_zh or not series_id) and job_id:
        try:
            import core.film_db as film_db
            rows = film_db.query(q=job_id, limit=2)
            row = next((r for r in rows if r.get("job_id") == job_id), None)
            if row:
                if not clean_zh:
                    clean_zh = str(row.get("title_zh") or row.get("title") or "").strip()
                if not clean_vi:
                    clean_vi = str(row.get("title_vi") or "").strip()
                if not series_id:
                    series_id = str(row.get("hg_series_id") or "").strip()
        except Exception:
            pass

    # 3. Tự động tra cứu Baike (DuanjuBaike / Hongguo) để lấy Cốt truyện chuẩn (Intro / Desc / Bản gốc)
    if not clean_summary or len(clean_summary) < 40:
        try:
            import agents.hongguo_bridge as hg
            b_info = None
            if series_id:
                try:
                    b_info = hg.baike_info(series_id, timeout=8)
                except Exception:
                    pass
            if (not b_info or not b_info.get("intro")) and clean_zh:
                # Tìm kiếm theo tên gốc tiếng Trung
                q_clean = re.sub(r"第[0-9一二三四五六七八九十]+季|\(merged\)|（.*）|\(.*\)|\s+", " ", clean_zh).strip()
                if q_clean:
                    b_list = hg.baike_search(q_clean, timeout=8)
                    if b_list and b_list[0].get("series_id"):
                        try:
                            b_info = hg.baike_info(b_list[0]["series_id"], timeout=8)
                        except Exception:
                            pass
            if b_info:
                if b_info.get("intro") and not clean_summary:
                    clean_summary = b_info["intro"]
                if b_info.get("cover"):
                    cover_url = b_info["cover"]
                if b_info.get("tags"):
                    tags = b_info["tags"]
        except Exception as e_bk:
            print(f"[meta_studio] get_film_story_info baike lookup warning: {e_bk}", flush=True)

    # 4. Fallback cuối cùng nếu vẫn chưa có tóm tắt: lấy từ episode_info
    if not clean_summary and episode_info and len(episode_info) > 20:
        clean_summary = episode_info

    return {
        "title_zh": clean_zh,
        "title_vi": clean_vi,
        "story": clean_summary,
        "series_id": series_id,
        "cover_url": cover_url,
        "tags": tags,
    }


def generate_youtube_titles(
    series_name: str,
    season: int = 1,
    episode_info: str = "",
    summary: str = "",
    job_id: str = "",
    tone: str = "",
    count: int = 5,
    title_zh: str = "",
) -> dict[str, Any]:
    """Gọi LLM sinh danh sách 5 Tiêu Đề YouTube kịch tính theo phong cách Hán Việt chuẩn,
    bám sát 100% Tên gốc tiếng Trung và Tóm tắt cốt truyện / Baike của bộ phim.
    TUYỆT ĐỐI KHÔNG đọc file phụ đề .srt để tránh làm loãng và rối tiêu đề.
    """
    clean_name = clean_series_name(series_name, fallback="Phim Hay")
    detected_season = season
    is_tron_bo = bool(re.search(r"trọn\s*bộ|gộp\s*\d+\s*(?:phần|mùa)|full|hoàn\s*tất", f"{series_name} {episode_info}", re.I))

    # Lấy thông tin Tên gốc + Tóm tắt cốt truyện chuẩn từ Baike / Manifest (KHÔNG đọc .srt)
    story_info = get_film_story_info(
        job_id=job_id,
        title_zh=title_zh,
        title_vi=series_name,
        summary=summary,
        episode_info=episode_info,
    )
    zh_name = story_info.get("title_zh") or title_zh or ""
    tom_tat = story_info.get("story") or summary or episode_info or clean_name

    if zh_name and clean_name and zh_name != clean_name:
        ten_goc = f"{zh_name} (Tên tạm dịch: {clean_name})"
    elif zh_name:
        ten_goc = zh_name
    else:
        ten_goc = clean_name

    phan_label = "Trọn Bộ" if is_tron_bo else (f"Phần {detected_season}" if detected_season and detected_season > 0 else "Phần 1")

    prompt = f"""Bạn là biên tập tiêu đề YouTube kênh Donghua / Manhua thuyết minh tiếng Việt.
Mục tiêu: người lướt feed đọc xong BIẾT VIDEO ĐANG NÓI GÌ (premise + hệ thống + hướng đi của «Ta»), rồi muốn bấm.

# DỮ LIỆU PHIM (NGUỒN SỰ THẬT — BÁM SÁT, KHÔNG BỊA PHIM KHÁC)
- Tên gốc ZH: {zh_name or clean_name}
- Tên VI: {clean_name}
- Nhãn phần: {phan_label}
- Tóm tắt Baike / giới thiệu:
\"\"\"
{tom_tat}
\"\"\"

# CÔNG THỨC BẮT BUỘC (mọi tiêu đề)
`{phan_label} | {{PREMISE}}: {{HOOK}}, {{TA_PAYOFF}}`

Trong đó:
- **PREMISE** = thân phận / tình thế mở đầu (vd Xuyên Không Thành Nô Lệ, Bị Ép Làm Phế Vật…). Lấy từ tóm tắt — KHÔNG nhét tên phim khô, KHÔNG nhét tên nhân vật.
- **HOOK** = biến cố / hệ thống lõi (Thức Tỉnh Hệ Thống / Cộng Điểm Võ Đạo / … đúng cơ chế trong nguồn).
- **TA_PAYOFF** = bắt đầu bằng «Ta …» — hướng đi người xem theo dõi (Ta một đường thành thánh / Ta cộng điểm quyền đao đến khi cả giới võ khiếp…).

# QUY TẮC
1. Chỉ dùng thân phận / hệ thống / biến cố CÓ TRONG tóm tắt hoặc tên phim. CẤM copy khuôn phim khác.
2. **CẤM TÊN NHÂN VẬT** trong mọi tiêu đề (vd Hứa Hoa, Lão Lục, Trương Tam…). Dùng «Ta» / thân phận (nô lệ, phế vật…) thay thế. Tên hệ thống / kỹ năng được phép.
3. {count} tiêu đề = {count} biến thể CÙNG công thức nhưng PAYOFF / HOOK khác nhau (không paraphrase nguyên câu).
4. 60–95 ký tự. Chữ hoa tự nhiên kiểu Hán Việt tiêu đề.
5. Bắt đầu bằng `{phan_label} | `.
6. Chỉ tiếng Việt — không Hán, không pinyin, không emoji, không **.
7. CẤM cụm sáo rỗng: «nghịch thiên cải mệnh», «siêu phẩm», «đỉnh cao trả thù».
8. CẤM kiểu chỉ «Tên phim: …» mà thiếu premise + «Ta …».

# VÍ DỤ ĐÚNG (học nhịp — ĐỔI nội dung theo phim hiện tại)
- «Phần 1 | Xuyên Không Thành Nô Lệ: Thức Tỉnh Hệ Thống Cộng Điểm, Ta Một Đường Thành Thánh»
- «Phần 1 | Xuyên Không Thành Nô Lệ: Thức Tỉnh Hệ Thống, Ta Cộng Điểm Quyền Đao Cho Đến Khi Cả Giới Võ Khiếp»
- «Phần 1 | Nô Lệ Khai Khoáng: Cộng Điểm Võ Đạo, Ta Từ Đáy Trở Thành Thánh»

# VÍ DỤ SAI (đừng làm)
- «… Hứa Hoa …» / «… Lão Lục …» ← CẤM tên nhân vật
- «Phần 1 | Võ Đạo Cộng Điểm: … Lão Lục Thành Thánh» ← thiếu «Ta» + có tên NV
- «Xuyên Không Thành Nô Lệ, Hứa Hoa Cộng Điểm…» ← thiếu nhãn phần + tên NV

# ĐẦU RA
Chỉ JSON hợp lệ:
{{"series_name_refined":"<tên VI ngắn đẹp>","titles":["...","...","...","...","..."],"best":"<1 trong titles>","reason":"<1 câu: premise + Ta payoff có trong best>"}}"""

    try:
        content = _call_chatgpt([{"role": "user", "content": prompt}], model="gpt-5-6")
        cleaned_json = (content or "").strip()
        cleaned_json = re.sub(r'</?escape>', '', cleaned_json).strip()
        m_json = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned_json, re.DOTALL)
        if m_json:
            cleaned_json = m_json.group(1)
        elif not (cleaned_json.startswith("{") and cleaned_json.endswith("}")):
            m_brace = re.search(r'(\{.*\})', cleaned_json, re.DOTALL)
            if m_brace:
                cleaned_json = m_brace.group(1)

        parsed = None
        try:
            parsed = json.loads(cleaned_json)
        except Exception:
            pass

        titles = []
        best = ""
        reason = ""
        if isinstance(parsed, dict):
            raw_titles = parsed.get("titles") or []
            if isinstance(raw_titles, list):
                for t in raw_titles:
                    t_clean = re.sub(r'[\u4e00-\u9fff]+', '', str(t)).strip('"` \t')
                    t_clean = re.sub(r'\*\*', '', t_clean).strip()
                    if t_clean and len(t_clean) >= 10:
                        titles.append(t_clean[:95].strip())
            best = str(parsed.get("best") or "").strip()
            reason = str(parsed.get("reason") or "").strip()

        if not titles:
            for l in (content or "").splitlines():
                line = l.strip()
                if not line or line.startswith("#") or line.startswith("{") or line.startswith("}"):
                    continue
                cleaned = re.sub(r"^\d+[\.\)\-–—]\s*", "", line).strip()
                cleaned = re.sub(r"\*\*", "", cleaned).strip('"` \t')
                cleaned = re.sub(r'[\u4e00-\u9fff]+', '', cleaned).strip()
                if cleaned and len(cleaned) >= 10:
                    titles.append(cleaned[:95].strip())

        if titles:
            best_title = best if (best and best in titles) else titles[0]
            return {
                "ok": True,
                "titles": titles[:count],
                "best_title": best_title,
                "best": best_title,
                "reason": reason,
            }
    except Exception as e:
        print(f"[meta_studio] generate_youtube_titles error: {e}", flush=True)

    sec_tag = phan_label
    # Fallback theo công thức Premise : Hook, Ta Payoff
    hook_bits = hooks_from_series_title(clean_name, zh_name)
    h1 = hook_bits[0].title()
    h2 = hook_bits[1].title()
    premise = h1 if h1 else clean_name
    fallbacks = [
        f"{sec_tag} | {premise}: {h2}, Ta Một Đường Lật Ngược Cục Diện",
        f"{sec_tag} | {premise}: Thức Tỉnh {h2}, Ta Đi Đến Cùng",
        f"{sec_tag} | {clean_name}: {h1}, Ta Từ Đáy Trở Thành {h2}",
        f"{sec_tag} | {premise}: {h1} Mở Đầu, Ta Không Còn Là Kẻ Bị Coi Thường",
        f"{sec_tag} | {clean_name}: {h2}, Ta Giữ Thế Chủ Động Đến Cuối",
    ]
    return {
        "ok": True,
        "titles": fallbacks[:count],
        "best_title": fallbacks[0],
        "best": fallbacks[0],
        "reason": "Tiêu đề fallback Hán Việt chuẩn",
        "fallback": True,
    }


def generate_title_only(series_name: str, season: int = 1, episode_info: str = "", summary: str = "", job_id: str = "", title_zh: str = "") -> str:
    """Gọi LLM sinh 1 Tiêu Đề YouTube kịch tính, bám sát nội dung thực tế của tập."""
    res = generate_youtube_titles(series_name=series_name, season=season, episode_info=episode_info, summary=summary, job_id=job_id, count=1, title_zh=title_zh)
    return res.get("best_title") or (res.get("titles", [""])[0])


def generate_part_metadata(
    series_name: str,
    season: int = 1,
    episode_info: str = "",
    shared_meta: dict | None = None,
    job_id: str = "",
    summary: str = "",
    title_zh: str = "",
) -> dict:
    """Sinh TITLE và DESCRIPTION chuẩn SEO cho từng Phần của Series khi dùng chung Thumbnail:
    - Title: Bám sát tình tiết thực tế từ kịch bản/summary của tập, danh xưng Hán Việt chuẩn, không dịch nôm na ngô nghê.
    - Description: 3 đoạn tóm tắt cốt truyện kịch tính của riêng tập đó + Emojis CTA + Disclaimer AI + 10 Hashtags.
    - Tags: Bộ tags SEO của series.
    - Model: GPT-5.6 (model mới nhất).
    """
    clean_name = clean_series_name(series_name, fallback="Phim Hay")
    ep_info = (episode_info or "").strip()
    if re.search(r"[\u4e00-\u9fff]", ep_info):
        no_zh_ep = re.sub(r"[\u4e00-\u9fff]", "", ep_info).strip(" -:|")
        if len(no_zh_ep) >= 3:
            ep_info = no_zh_ep

    # Lấy thông tin Tên gốc + Tóm tắt cốt truyện chuẩn từ Baike / Manifest (không đọc .srt làm rối)
    story_info = get_film_story_info(
        job_id=job_id,
        title_zh=title_zh,
        title_vi=clean_name,
        summary=summary,
        episode_info=ep_info,
    )
    actual_summary = story_info.get("story") or summary or ep_info or clean_name
    if (not actual_summary or len(actual_summary) < 30) and job_id:
        actual_summary = extract_job_summary(job_id) or clean_name

    base_tags = []
    if shared_meta and isinstance(shared_meta.get("tags"), list):
        base_tags = [t for t in shared_meta["tags"] if not re.search(r"[\u4e00-\u9fff]", str(t))]
    if story_info.get("tags"):
        for t in story_info["tags"]:
            if t and t not in base_tags and not re.search(r"[\u4e00-\u9fff]", t):
                base_tags.append(t)

    # Ưu tiên lấy Title chất lượng cao từ Master Title Engine (chuẩn Hán Việt, không dịch nôm na)
    master_title_res = generate_youtube_titles(
        series_name=clean_name,
        season=season,
        episode_info=ep_info,
        summary=actual_summary,
        job_id=job_id,
        count=1,
        title_zh=story_info.get("title_zh") or title_zh,
    )
    master_title = master_title_res.get("best_title") or (master_title_res.get("titles", [""])[0] if master_title_res.get("titles") else "")

    prompt = f"""Bạn là Creative Director và YouTube SEO chuyên nghiệp cho kênh kể lại phim hoạt hình/manhua Trung Quốc.
Bộ phim: «{clean_name}»
Tập cần làm: Phần {season}
Thông tin tập phim: {ep_info or clean_name}

KỊCH BẢN / TÓM TẮT DIỄN BIẾN THỰC TẾ CỦA TẬP NÀY:
\"\"\"
{actual_summary}
\"\"\"

HÃY TẠO BỘ METADATA YOUTUBE (TITLE & DESCRIPTION) CHUẨN SEO CHO PHẦN {season}.

QUY TẮC BẮT BUỘC:

1. NGUYÊN TẮC HÁN VIỆT SANG TRỌNG & TỰ NHIÊN:
- BẮT BUỘC dùng danh xưng, thân phận, tông môn Hán Việt kinh điển của phim/truyện Trung: «Thiên Đạo Chi Nữ», «Tu Tiên Giới», «Thiên Giới», «Tông Môn», «Tiên Tôn», «Quậy Tung»...
- TUYỆT ĐỐI CẤM dịch ngô nghê theo nghĩa đen (ví dụ CẤM dịch 'Con Gái Cưng Của Thiên Đạo', BẮT BUỘC dùng 'Thiên Đạo Chi Nữ').
- Tuyệt đối KHÔNG chứa chữ Hán hay pinyin trong kết quả.

2. TIÊU ĐỀ (TITLE) BÁM SÁT CỐT TRUYỆN THẬT — KHÔNG ĐẶT RẬP KHUÔN:
- Format: Phần {season} | {clean_name} - [Biến cố gay cấn / Cú twist / Tình huống oái oăm từ kịch bản trên]
- Dùng dấu " - " ngăn cách tên phim và câu hook (CẤM dùng dấu hai chấm ":").
- Tuyệt đối CẤM câu sáo rỗng vô thưởng vô phạt: "Nghịch thiên cải mệnh", "Thức tỉnh năng lực kỳ bí", "Đảo ngược cục diện", "Đồ thần", "Hắn lật ngược tất cả", "Bá đạo vô song".
- Dưới 85 ký tự. Không dùng ngoặc kép ngoài cùng.

3. MÔ TẢ (DESCRIPTION):
Bắt buộc có đầy đủ 4 phần theo cấu trúc chuẩn:
- Phần 1 (Cốt truyện 3 đoạn bám sát diễn biến tập này):
  + Đoạn 1: Mở đầu bằng mâu thuẫn hoặc hiểm nguy cụ thể của Phần {season} dựa theo kịch bản trên.
  + Đoạn 2: {clean_name} (Phần {season}) tiếp tục diễn biến... nêu chi tiết bước đi, xung đột hoặc thử thách mà nhân vật chính phải đối mặt.
  + Đoạn 3: Cao trào kịch tính, bước ngoặt bất ngờ và lời kêu gọi theo dõi diễn biến tiếp theo.
- Phần 2 (Thông tin & Kêu gọi tương tác):
  🎬 Tên phim: {clean_name} (Phần {season})
  🎙️ Nội dung: Phim thuyết minh tiếng Việt.
  👍 Nếu yêu thích thể loại manhua, donghua và huyền huyễn, hãy nhấn Like, đăng ký kênh và bật chuông thông báo để không bỏ lỡ những tập tiếp theo!
  💬 [1 câu hỏi tranh luận hấp dẫn dựa trên tình tiết hoặc quyết định của nhân vật trong tập này]? Hãy chia sẻ ý kiến ở phần bình luận!
- Phần 3 (Lưu ý AI & Bản quyền):
  Lưu ý: Video có thể sử dụng công cụ AI hỗ trợ một số công đoạn biên tập, hình ảnh hoặc hậu kỳ. Nội dung được biên soạn nhằm mục đích kể lại, bình luận và giải trí. Các nhân vật, hình ảnh và tác phẩm gốc thuộc quyền của chủ sở hữu tương ứng. Video không nhằm thay thế tác phẩm gốc.
- Phần 4 (Hashtags):
  Đủ 10 hashtags chuẩn SEO viết liền có dấu thăng (#) bắt đầu bằng tên phim và các từ khóa liên quan.

ĐẦU RA BẮT BUỘC JSON HỢP LỆ (KHÔNG KÈM VĂN BẢN NGOÀI KHỐI JSON):
{{"title": "Phần {season} | {clean_name} - ...", "description": "...", "tags": ["tag1", "tag2"]}}"""

    try:
        reply = _call_chatgpt([{"role": "user", "content": prompt}], model="gpt-5-6")
        data = _clean_json_response(reply)
        if data and data.get("title") and data.get("description"):
            raw_title = master_title or str(data["title"]).strip('"` \t')
            raw_title = re.sub(r"[\u4e00-\u9fff]+", "", raw_title).strip()
            raw_title = re.sub(r"\*\*", "", raw_title).strip()
            raw_title = re.sub(r"\s{2,}", " ", raw_title)
            if not re.match(r"^(?:Phần|Tập|Mùa|Trọn\s*Bộ)\s*\d*\s*[|:\-–—]", raw_title, re.I):
                raw_title = f"Phần {season} | {clean_name} - {raw_title.lstrip(' -:')}"
            else:
                raw_title = re.sub(r"^((?:Phần|Tập|Mùa|Trọn\s*Bộ)\s*\d*\s*\|\s*[^:\-–—]+):\s*", r"\1 - ", raw_title)

            raw_desc = str(data["description"]).replace("\\n", "\n").strip()
            llm_tags = [str(t).strip() for t in data.get("tags", []) if str(t).strip()]
            combined_tags = []
            for t in (llm_tags + base_tags):
                if t and t not in combined_tags and not re.search(r"[\u4e00-\u9fff]", t):
                    combined_tags.append(t)

            return {
                "title": raw_title[:100].strip(),
                "description": raw_desc,
                "tags": combined_tags or base_tags or [clean_name, "Phim Thuyết Minh", "Manhua Tiếng Việt"],
            }
    except Exception as e:
        print(f"[meta_studio] LLM generate_part_metadata error: {e}, using rich fallback", flush=True)

    return _build_fallback_part_meta(clean_name, season=season, episode_info=actual_summary, base_tags=base_tags)


def _clean_json_response(raw: str) -> dict:
    """Bóc tách và tự động sửa lỗi JSON bị cắt cụt (truncated) từ phản hồi LLM."""
    if not raw or not isinstance(raw, str):
        return {}
    s = raw.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)(?:```|$)", s)
    if m:
        s = m.group(1).strip()
    start = s.find("{")
    if start == -1:
        return {}
    s = s[start:]

    # 1. Thử parse nguyên bản
    try:
        return json.loads(s)
    except Exception:
        pass

    # 2. Thử cắt ngoặc nhọn ngoài cùng nếu có markdown thừa
    end = s.rfind("}")
    if end != -1 and end > 0:
        try:
            return json.loads(s[:end + 1])
        except Exception:
            pass

    # 3. Tự động phục hồi JSON bị đứt đoạn (do LLM chạm giới hạn max_tokens)
    for cut_idx in range(len(s), max(0, len(s) - 800), -1):
        cand = s[:cut_idx].rstrip()
        if cand.endswith(","):
            cand = cand[:-1].rstrip()
        stack = []
        in_str = False
        escape = False
        for c in cand:
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c in ("{", "["):
                stack.append(c)
            elif c == "}" and stack and stack[-1] == "{":
                stack.pop()
            elif c == "]" and stack and stack[-1] == "[":
                stack.pop()

        if in_str:
            cand += '"'
        for open_c in reversed(stack):
            cand += "}" if open_c == "{" else "]"
        try:
            res = json.loads(cand)
            if isinstance(res, dict) and (res.get("titles") or res.get("hook_texts") or res.get("ai_image_prompt")):
                return res
        except Exception:
            continue

    return {}


def _cfg(key: str, default: str = "") -> str:
    try:
        from core.config import get as _cget
        v = _cget(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    except Exception:
        pass
    return (os.getenv(key) or default).strip()


def _get_chatgpt_token() -> str:
    """Đọc Access Token / API key ChatGPT: config → env → data/chatgpt_token.txt."""
    for k in ("CHATGPT_API_KEY", "CHATGPT_ACCESS_TOKEN"):
        v = _cfg(k)
        if v:
            return v
    token_file = ROOT / "data" / "chatgpt_token.txt"
    if token_file.exists():
        try:
            return token_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def _chatgpt_candidate_bases() -> list[str]:
    """Ưu tiên CHATGPT_BASE_URL trong pineline.config (local :3377), rồi Aurora LAN."""
    bases: list[str] = []
    for k in ("AURORA_BASE_URL", "CHATGPT_BASE_URL"):
        v = _cfg(k).rstrip("/")
        if v and v not in bases:
            bases.append(v)
    for u in _machine.aurora_base_urls():  # machine-config: :3377 + AURORA_FALLBACK_URLS
        if u not in bases:
            bases.append(u)
    return bases


def extract_job_summary(job_id: str, max_chars: int = 5500) -> str:
    """Trích xuất kịch bản thực tế từ phụ đề tiếng Việt (sub.vi.srt / script.json) trải đều 6 giai đoạn
    xuyên suốt bộ phim để AI nắm bắt trọn vẹn: Nhân vật chính, Bàn tay vàng, Biến cố, Xung đột & Cao trào.
    """
    if not job_id:
        return ""
    work_dir = ROOT / "work" / str(job_id).strip()
    if not work_dir.is_dir():
        return ""

    header_lines: list[str] = []
    mf_file = work_dir / "manifest.json"
    if mf_file.is_file():
        try:
            mdata = json.loads(mf_file.read_text(encoding="utf-8"))
            req = mdata.get("request") or {}
            orig_t = str(req.get("title") or "").strip()
            vi_t = str(mdata.get("title_vi") or "").strip()
            if orig_t:
                header_lines.append(f"[Tên gốc]: {orig_t}")
            if vi_t:
                header_lines.append(f"[Tên tiếng Việt]: {vi_t}")
            desc_cand = str(req.get("summary") or req.get("desc") or "").strip()
            if desc_cand:
                header_lines.append(f"[Giới thiệu]: {desc_cand}")
        except Exception:
            pass

    text_lines: list[str] = []
    # 1. Ưu tiên đọc từ sub.vi.srt / sub.vi.burn.srt / sub.vi.aligned.srt
    for srt_name in ("sub.vi.srt", "sub.vi.burn.srt", "sub.vi.aligned.srt"):
        srt_file = work_dir / srt_name
        if srt_file.is_file() and srt_file.stat().st_size > 100:
            raw_lines = srt_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line in raw_lines:
                l = line.strip()
                if not l or l.isdigit() or "-->" in l:
                    continue
                l_clean = re.sub(r"<[^>]+>", "", l).strip()
                if len(l_clean) < 3:
                    continue
                if text_lines and text_lines[-1] == l_clean:
                    continue
                text_lines.append(l_clean)
            if len(text_lines) >= 15:
                break

    # 2. Fallback sang script.json nếu chưa có file .srt
    if len(text_lines) < 15:
        sp = work_dir / "script.json"
        if sp.is_file():
            try:
                sdata = json.loads(sp.read_text(encoding="utf-8"))
                for seg in sdata.get("segments") or []:
                    txt = str(seg.get("text_vi") or seg.get("vi") or seg.get("text") or "").strip()
                    if len(txt) >= 3 and (not text_lines or text_lines[-1] != txt):
                        text_lines.append(txt)
            except Exception:
                pass

    if not text_lines:
        return "\n".join(header_lines)

    full_text = " ".join(text_lines)
    prefix = ("\n".join(header_lines) + "\n\n") if header_lines else ""
    budget = max(1200, max_chars - len(prefix))

    if len(full_text) <= budget:
        return f"{prefix}[Toàn bộ thoại kịch bản]:\n{full_text}".strip()

    # Chia bộ phim thành 6 giai đoạn chiến lược để AI thấy trọn vẹn cốt truyện từ đầu tới cuối
    n = len(text_lines)
    stages = [
        ("1. Mở đầu - Thiết lập nhân vật & Biến cố", 0.00, int(budget * 0.24)),
        ("2. Diễn biến đầu - Thân phận & Năng lực/Bàn tay vàng", 0.18, int(budget * 0.16)),
        ("3. Diễn biến giữa - Xung đột & Kẻ địch lộ diện", 0.38, int(budget * 0.16)),
        ("4. Bước ngoặt - Bộc lộ thực lực & Phản kích", 0.58, int(budget * 0.16)),
        ("5. Cao trào - Đụng độ đỉnh điểm", 0.78, int(budget * 0.14)),
        ("6. Kết tập - Kết quả & Dư âm", 0.92, int(budget * 0.14)),
    ]
    sections: list[str] = []
    for label, pos_ratio, char_lim in stages:
        start_idx = min(max(0, int(n * pos_ratio)), max(0, n - 5))
        chunk_words: list[str] = []
        cur_len = 0
        for line in text_lines[start_idx:]:
            if cur_len + len(line) + 1 > char_lim and chunk_words:
                break
            chunk_words.append(line)
            cur_len += len(line) + 1
        if chunk_words:
            sections.append(f"[{label}]: " + " ".join(chunk_words))

    return (prefix + "\n\n".join(sections)).strip()


def _normalize_ref_jpeg(raw_bytes: bytes, max_dim: int = 1024) -> bytes | None:
    """Chuẩn hoá ảnh tham chiếu (Poster hoặc Frame) về RGB JPEG <= 1024px để gửi lên Aurora /v1/images/edits."""
    if not raw_bytes or len(raw_bytes) < 100:
        return None
    try:
        im = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        w, h = im.size
        if w < 64 or h < 64:
            return None
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90, optimize=True)
        return buf.getvalue()
    except Exception:
        return None


def _extract_best_video_frame(video_path: Path) -> bytes | None:
    """Trích xuất khung hình sắc nét và giàu chi tiết nhất từ video làm ảnh tham chiếu nhân vật."""
    import subprocess
    if not video_path or not video_path.is_file():
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    dur = 0.0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=15, creationflags=flags,
        )
        dur = float((r.stdout or "").strip() or 0.0)
    except Exception:
        dur = 0.0

    # Chọn 3 mốc thời gian giàu nhân vật (tránh intro đen và credit cuối)
    timestamps = [max(5.0, dur * r) for r in (0.12, 0.28, 0.48)] if dur > 30 else [3.0, 8.0]
    best_jpeg: bytes | None = None
    best_score = -1.0

    for ts in timestamps:
        try:
            r = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{ts:.2f}", "-i", str(video_path),
                    "-frames:v", "1", "-q:v", "2", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
                ],
                capture_output=True, timeout=20, creationflags=flags,
            )
            if r.returncode == 0 and r.stdout and len(r.stdout) > 2000:
                norm = _normalize_ref_jpeg(r.stdout, max_dim=1024)
                # Khung hình có dung lượng JPEG lớn hơn ở cùng quality thường nhiều chi tiết & độ tương phản cao hơn (không bị tối đen/mờ)
                if norm and len(norm) > best_score:
                    best_score = float(len(norm))
                    best_jpeg = norm
        except Exception:
            continue
    return best_jpeg


def get_job_reference_images(job_id: str = "", poster_name: str = "") -> list[bytes]:
    """Lấy danh sách ảnh gốc tham chiếu (Poster gốc + Khung hình sắc nét trong phim) của Job
    để truyền vào Aurora /v1/images/edits giúp giữ đúng khuôn mặt, trang phục và art style nhân vật.
    """
    refs: list[bytes] = []
    work_dir = (ROOT / "work" / str(job_id).strip()) if job_id else None
    orig_path_str = ""

    if work_dir and (work_dir / "manifest.json").is_file():
        try:
            m = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
            req = m.get("request") or {}
            orig_path_str = str(req.get("orig_path") or "")
            if not poster_name and orig_path_str:
                poster_name = Path(orig_path_str).parent.name
            if not poster_name:
                poster_name = str(req.get("title") or "").replace(" (merged)", "").strip()
        except Exception:
            pass

    # 1. Tìm Poster gốc từ thư mục nguồn (D:\Lib hoặc D:\Lib_trash) hoặc API Hongguo
    poster_bytes: bytes | None = None
    if orig_path_str:
        src_folder = Path(orig_path_str).parent
        if src_folder.is_dir():
            for cname in ("poster.jpg", "poster.jpeg", "poster.png", "cover.jpg", "cover.jpeg", "cover.png", "thumb.jpg"):
                cp = src_folder / cname
                if cp.is_file() and cp.stat().st_size > 200:
                    try:
                        poster_bytes = _normalize_ref_jpeg(cp.read_bytes())
                        if poster_bytes:
                            break
                    except Exception:
                        pass

    if not poster_bytes and poster_name:
        try:
            import agents.hongguo_bridge as hg
            p_local = hg.resolve_poster_path(poster_name)
            if p_local and p_local.is_file():
                poster_bytes = _normalize_ref_jpeg(p_local.read_bytes())
        except Exception:
            pass
        if not poster_bytes:
            try:
                import urllib.parse
                from core.instance import server_base as _server_base  # V6 F4: không 8801
                url = f"{_server_base()}/api/hg/poster?name={urllib.parse.quote(poster_name)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Pineline"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    poster_bytes = _normalize_ref_jpeg(resp.read())
            except Exception:
                pass

    if poster_bytes:
        refs.append(poster_bytes)

    # 2. Trích xuất thêm 1 khung hình thực tế từ video (output.mp4 / video gốc) để AI thấy rõ nhân vật trong phim
    if work_dir and work_dir.is_dir():
        vid_candidates = [
            work_dir / "output.mp4",
            work_dir / "video.mp4",
            work_dir / "input.mp4",
        ]
        if orig_path_str:
            vid_candidates.append(Path(orig_path_str))
        for vp in vid_candidates:
            if vp.is_file():
                frame_bytes = _extract_best_video_frame(vp)
                if frame_bytes:
                    refs.append(frame_bytes)
                    break

    return refs[:2]


def _call_chatgpt(messages: list[dict], model: str = "gpt-5-6") -> str:
    """Gọi LLM qua Aurora / Chat2Api chạy trên Raspberry Pi 5.
    Ưu tiên model mới nhất (gpt-5-6 Luna / auto GPT-6) và tự động fallback.
    """
    import requests
    token = _get_chatgpt_token()
    candidate_urls = _chatgpt_candidate_bases()

    # Danh sách model theo thứ tự ưu tiên mới nhất
    target_models = []
    req_m = (model or "").strip()
    if req_m and req_m not in ("4o", "auto", "default"):
        target_models.append(req_m)
    # Model mới nhất trên hệ thống: gpt-5-6 Luna -> auto (GPT-6) -> gpt-5-6-mini
    for m in ("gpt-5-6", "auto", "gpt-5-6-mini"):
        if m not in target_models:
            target_models.append(m)

    last_err = None
    for chosen_model in target_models:
        data = {
            "model": chosen_model,
            "messages": messages,
            "stream": False,
        }
        for base_url in candidate_urls:
            url = f"{base_url}/chat/completions"
            # Thử 1: Nếu có token, thử với token
            if token:
                try:
                    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                    r = requests.post(url, headers=headers, json=data, timeout=60)
                    if r.status_code == 200:
                        res = r.json()
                        return res["choices"][0]["message"]["content"]
                except Exception as e:
                    last_err = e
            # Thử 2: Gọi trực tiếp không token (Aurora dùng pool tài khoản nội bộ trên Pi 5)
            try:
                headers = {"Content-Type": "application/json"}
                r = requests.post(url, headers=headers, json=data, timeout=60)
                if r.status_code == 200:
                    res = r.json()
                    return res["choices"][0]["message"]["content"]
                r.raise_for_status()
            except Exception as e:
                last_err = e
                continue

    raise ConnectionError(f"Không thể kết nối tới LLM trên Pi 5 ({last_err})")


def _decode_aurora_image_response(resp_json: dict, timeout: int = 60) -> bytes | None:
    """Giải mã ảnh từ phản hồi của Aurora (/v1/images/edits hoặc /v1/images/generations)."""
    import base64
    import requests
    data = (resp_json or {}).get("data") or []
    if not data or not isinstance(data, list):
        return None
    item = data[0] if isinstance(data[0], dict) else {}
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        r_img = requests.get(str(item["url"]), timeout=timeout)
        if r_img.status_code == 200 and len(r_img.content) > 1000:
            return r_img.content
    return None


def generate_ai_image(
    prompt: str,
    size: str = "1024x1024",
    timeout: int = 150,
    ref_images: list[bytes] | bytes | None = None,
    job_id: str = "",
    poster_name: str = "",
) -> bytes | None:
    """Gọi Aurora trên Pi 5 để sinh ảnh Thumbnail 16:9 chất lượng cao:
    - ƯU TIÊN SỐ 1: Đính kèm ảnh gốc tham chiếu (Poster + Frame trong phim) gửi lên `/v1/images/edits`
      (`model="gpt-image-2"`) để giữ chuẩn khuôn mặt, trang phục, khí chất nhân vật và Visual DNA của phim.
    - Dự phòng (Fallback): Nếu không có ảnh tham chiếu hoặc `/v1/images/edits` lỗi, tự động gọi `/v1/images/generations`.
    """
    import base64
    import requests

    token = _get_chatgpt_token()
    candidate_urls = _chatgpt_candidate_bases()
    try:
        cfg_t = int(_cfg("CHATGPT_TIMEOUT", "900") or 900)
    except Exception:
        cfg_t = 900
    timeout = max(int(timeout or 0), min(max(cfg_t, 180), 1800))

    # Chuẩn bị danh sách ảnh tham chiếu (Reference Images)
    ref_list: list[bytes] = []
    if isinstance(ref_images, bytes):
        norm = _normalize_ref_jpeg(ref_images)
        if norm:
            ref_list.append(norm)
    elif isinstance(ref_images, list):
        for b in ref_images:
            if isinstance(b, bytes):
                norm = _normalize_ref_jpeg(b)
                if norm:
                    ref_list.append(norm)

    if not ref_list and (job_id or poster_name):
        ref_list = get_job_reference_images(job_id=job_id, poster_name=poster_name)

    # Local :3377 cần Bearer sk-local; Aurora pool có thể không cần token.
    auth_headers_list: list[dict] = []
    if token:
        auth_headers_list.append({"Authorization": f"Bearer {token}"})
    auth_headers_list.append({})

    # 1. NẾU CÓ ẢNH THAM CHIẾU -> GỌI /v1/images/edits (Bám sát ngoại hình nhân vật & Visual DNA ảnh gốc)
    if ref_list:
        ref_prefix = (
            "REFERENCE IMAGE MANDATE: Strictly use the attached reference image(s) for the main character's "
            "exact facial features, hairstyle, hair color, outfit design, and 3D/2D rendering aesthetic. "
            "Keep the character's identity 100% faithful to the reference image while composing a dramatic "
            "16:9 horizontal YouTube thumbnail scene as instructed below:\n\n"
        )
        edit_prompt = prompt if "REFERENCE IMAGE" in prompt.upper() else (ref_prefix + prompt)

        for base_url in candidate_urls:
            edit_url = f"{base_url}/images/edits"
            for hdrs in auth_headers_list:
                # Cách 1A: multipart/form-data theo chuẩn Aurora API.md
                try:
                    files_payload = [
                        ("image", (f"ref_{idx}.jpg", img_b, "image/jpeg"))
                        for idx, img_b in enumerate(ref_list[:2])
                    ]
                    form_data = {
                        "model": "gpt-image-2",
                        "prompt": edit_prompt,
                        "n": "1",
                        "size": size,
                        "response_format": "b64_json",
                    }
                    print(f"[meta_studio] Gửi {len(files_payload)} ảnh gốc tham chiếu lên {edit_url} (multipart)...", flush=True)
                    r = requests.post(edit_url, headers=hdrs, data=form_data, files=files_payload, timeout=timeout)
                    if r.status_code == 200:
                        out_bytes = _decode_aurora_image_response(r.json(), timeout=timeout)
                        if out_bytes:
                            print(f"[meta_studio] ✅ Sinh ảnh từ Reference Image (/images/edits multipart) thành công ({len(out_bytes)//1024} KB)!", flush=True)
                            return out_bytes
                except Exception as e_mp:
                    print(f"[meta_studio] /images/edits multipart warning ({base_url}): {e_mp}", flush=True)

                # Cách 1B: application/json với mảng Base64 Data URL theo Aurora API.md
                try:
                    data_urls = [
                        f"data:image/jpeg;base64,{base64.b64encode(img_b).decode('ascii')}"
                        for img_b in ref_list[:2]
                    ]
                    json_payload = {
                        "model": "gpt-image-2",
                        "prompt": edit_prompt,
                        "n": 1,
                        "size": size,
                        "response_format": "b64_json",
                        "images": data_urls,
                    }
                    json_hdrs = {**hdrs, "Content-Type": "application/json"}
                    r = requests.post(edit_url, headers=json_hdrs, json=json_payload, timeout=timeout)
                    if r.status_code == 200:
                        out_bytes = _decode_aurora_image_response(r.json(), timeout=timeout)
                        if out_bytes:
                            print(f"[meta_studio] ✅ Sinh ảnh từ Reference Image (/images/edits json) thành công ({len(out_bytes)//1024} KB)!", flush=True)
                            return out_bytes
                except Exception as e_js:
                    print(f"[meta_studio] /images/edits json warning ({base_url}): {e_js}", flush=True)

    # 2. FALLBACK -> GỌI /v1/images/generations
    payload = {
        "model": "gpt-image-2",
        "prompt": prompt,
        "n": 1,
        "size": size,
        "response_format": "b64_json",
    }
    for base_url in candidate_urls:
        url = f"{base_url}/images/generations"
        for hdrs in auth_headers_list:
            try:
                r = requests.post(url, headers={**hdrs, "Content-Type": "application/json"}, json=payload, timeout=timeout)
                if r.status_code == 200:
                    out_bytes = _decode_aurora_image_response(r.json(), timeout=timeout)
                    if out_bytes:
                        return out_bytes
            except Exception:
                continue
    return None


def generate_metadata(
    title_vi: str = "",
    title_zh: str = "",
    info_text: str = "",
    prompt_guide: str = "",
    tone: str = "giật gân, kịch tính",
    job_id: str = "",
) -> dict:
    """Dùng ChatGPT (qua Pi 5 với GPT-5.6 Luna mới nhất) hoặc Local LLM đọc Tên gốc & Cốt truyện Baike chuẩn
    để sinh Title SEO chuẩn Hán Việt, Description, Tags, Hook Text và Master Image Prompt (đa dạng màu sắc & bố cục, bám sát ảnh gốc).
    TUYỆT ĐỐI KHÔNG dùng file .srt để sinh Title.
    """
    actual_info = (info_text or "").strip()
    story_info = get_film_story_info(
        job_id=job_id,
        title_zh=title_zh,
        title_vi=title_vi,
        summary=info_text,
    )
    zh_name = story_info.get("title_zh") or title_zh or ""
    clean_vi = story_info.get("title_vi") or title_vi or ""
    story_text = story_info.get("story") or ""

    if story_text:
        actual_info = f"[Tên gốc tiếng Trung]: {zh_name}\n[Tên tạm dịch]: {clean_vi}\n[Tóm tắt cốt truyện Baike/Bản gốc]:\n{story_text}".strip()
    elif job_id:
        # Fallback sang thoại srt chỉ khi hoàn toàn KHÔNG có tóm tắt nào
        extracted = extract_job_summary(job_id, max_chars=5500)
        if extracted:
            actual_info = f"[Tên gốc]: {zh_name or clean_vi}\n[Kịch bản thoại tóm lược]:\n{extracted}".strip()

    system_prompt = (
        """# MASTER PROMPT — SENIOR CREATIVE DIRECTOR + ART DIRECTOR + TYPOGRAPHER + YOUTUBE SEO STRATEGIST

## CHUYÊN THIẾT KẾ THUMBNAIL ĐA PHONG CÁCH & METADATA TỪ CỐT TRUYỆN GỐC (BAIKE) CHO KÊNH DONGHUA / MANHUA TIẾNG VIỆT

MỤC TIÊU TỐI THƯỢNG:
1. ĐỌC KỸ TÊN GỐC TIẾNG TRUNG & TÓM TẮT CỐT TRUYỆN GỐC (BAIKE) ĐƯỢC CUNG CẤP để hiểu chính xác nhân vật chính là ai, có bàn tay vàng/cổ bảo gì, gặp biến cố gì và màn nghịch thiên ra sao.
2. Tạo bộ TIÊU ĐỀ YOUTUBE & HOOK TEXT đặc sắc, chuẩn văn phong Hán Việt sang trọng, phản ánh ĐÚNG 100% cốt truyện thật của phim (TUYỆT ĐỐI CẤM văn xuôi kể lể dài dòng, cấm dùng cụm từ vô hồn).
3. Tạo MASTER IMAGE PROMPT cho `/v1/images/edits` (có đính kèm ảnh gốc Reference Image):
   - BẮT BUỘC giữ nguyên ngoại hình, khuôn mặt, kiểu tóc, trang phục và chất liệu 3D/2D của nhân vật từ ảnh gốc tham chiếu (Reference Image).
   - TUYỆT ĐỐI CẤM thumbnail "1 màu vàng lửa" giống hệt nhau — chọn bảng màu/bối cảnh/tư thế khớp THỂ LOẠI & BỐI CẢNH thật.
   - AI PHẢI nung chữ 3D Việt vào ảnh (typography tích hợp): badge + headline 2 dòng + banner tên phim.
   - Badge CHỈ được là `NEW` hoặc `HOT` — CẤM «TRỌN BỘ THUYẾT MINH» và mọi badge dài kiểu thuyết minh.

---

### PHẦN 1 — 6 HỆ BẢNG MÀU & PHONG CÁCH THỊ GIÁC ĐA DẠNG (CHỌN ĐÚNG HỆ THEO CỐT TRUYỆN, CẤM LẶP LẠI 1 MÀU VÀNG LỬA):
Tùy theo thể loại và bối cảnh cốt truyện, BẮT BUỘC chọn 1 trong 6 hệ màu & chất liệu chữ sau (hoặc phối hợp sáng tạo):
1. **HỆ TINH HÀ / BĂNG TUYẾT / KIẾM ĐẠO (Ice Cyan, Astral Blue & Silver Platinum)**:
   - Dùng cho: Tu tiên kiếm đạo, tinh không,thợ sửa chữa tinh tú, băng hệ, bí cảnh, không gian.
   - Bảng màu: Xanh băng giá (Ice Cyan), Xanh tinh vân (Deep Astral Blue), Trắng bạch kim (Silver White) & Tím ngân hà.
   - Chữ 3D: Đúc bạc bạch kim vát cạnh pha lê băng hoặc phát sáng xanh điện quang (Electric Cyan & Chrome), viền xanh đen sâu thẳm.
2. **HỆ MẠT THẾ / SINH TỒN / ZOMBIE / DỊ NĂNG (Bio-Toxic Green, Cold Steel Blue & Crimson Alert)**:
   - Dùng cho: Tận thế, tang thi, tích trữ vật tư, căn cứ sinh tồn, quái vật biến dị, quy tắc kinh dị.
   - Bảng màu: Xanh rêu độc (Toxic Emerald), Xám thép lạnh (Cold Gunmetal), Đỏ huyết nguyệt (Blood Moon Red).
   - Chữ 3D: Kim loại thép xước công nghiệp hoặc vàng cảnh báo sinh học viền đỏ/xanh neon, hiệu ứng tia lửa hàn hoặc sương mù mạt thế.
3. **HỆ LÔI ĐIỆN / TÍM HUYỀN HUYỄN / MA ĐẠO (Royal Amethyst Purple, Electric Violet & Pale Gold)**:
   - Dùng cho: Thiên Đạo, lôi kiếp, ma tôn, thức tỉnh huyết mạch cổ xưa, triệu hồi thần thú, phù văn.
   - Bảng màu: Tím tử vi (Amethyst Purple), Tím lôi điện (Electric Magenta/Violet) đối lập với sắc Vàng kim nhạt hoặc Trắng ngọc.
   - Chữ 3D: Đá cổ ngữ phát sáng tím-trắng hoặc kim loại bạch kim bọc tia sét tím.
4. **HỆ ĐÔ THỊ HÀO MÔN / TRỌNG SINH BÁO THÙ / THẦN HÀO (Emerald Luxury, Cyberpunk Neon hoặc Royal Gold & Obsidian)**:
   - Dùng cho: Đô thị, tổng tài, hào môn thế gia, trọng sinh làm giàu, vả mặt kẻ phản bội, giám bảo, thần y.
   - Bảng màu: Đen huyền thạch (Obsidian), Xanh ngọc lục bảo (Emerald) hoặc Ánh đèn đô thị (Neon Blue & Warm Gold).
   - Chữ 3D: Vàng Champagne sang trọng hoặc Bạch kim Chrome bóng gương sắc lạnh.
5. **HỆ THẬP NIÊN 80-90 / ĐIỀN VIÊN / SĂN BẮN / CỔ ĐẠI (Warm Sunset Amber, Jade Green & Ivory)**:
   - Dùng cho: Trọng sinh thập niên 80/90, đi săn nuôi cả nhà, làm ruộng, y thuật cổ đại, cung đấu.
   - Bảng màu: Nắng chiều hổ phách (Warm Amber), Xanh rừng già (Forest Green), Đỏ chu sa (Vermilion) & Trắng ngà.
6. **HỆ HỎA DIỄM / CHIẾN THẦN / LONG TỘC (Molten Crimson, Phoenix Orange & Imperial Gold)**:
   - CHỈ DÙNG KHI nhân vật trong kịch bản `.srt` thực sự tu luyện Hỏa hệ, Viêm thần, Long hỏa hoặc chiến trường rực lửa!

---

### PHẦN 2 — ĐA DẠNG BỐ CỤC & TƯ THẾ NHÂN VẬT (CẤM LẶP LẠI TƯ THẾ ĐƯA BÀN TAY RA TRƯỚC):
- **TUYỆT ĐỐI CẤM** lúc nào cũng vẽ "nhân vật nam đứng bên phải giơ bàn tay mở rộng bắn lửa vàng về phía camera"!
- Hãy chọn tư thế & góc máy khớp với **khoảnh khắc đắt giá nhất trong kịch bản `.srt`**:
  * **Góc Confrontation (Đối đầu)**: Nhân vật chính đứng nhìn nghiêng hoặc quay đầu lại với ánh mắt lạnh lùng sắc bén, phía sau/đối diện là kẻ thù khổng lồ hoặc đám người đang kinh hãi quỳ rạp.
  * **Góc Weapon / Artifact Reveal (Lộ Pháp Bảo / Kỹ Năng)**: Nhân vật cầm kiếm / búa thần / pháp bảo tinh tú / bảng hệ thống hologram hoặc triệu hồi thần thú uy nghi.
  * **Góc Awakening / Power Surge (Thức Tỉnh)**: Góc máy low-angle ngước lên, nhân vật lơ lửng giữa pháp trận/tinh không hoặc bước ra từ đống đổ nát với hào quang đặc trưng của hệ năng lực trong phim.
  * **Bám sát 100% Reference Image**: Giữ nguyên giới tính (nam/nữ/bé gái), khuôn mặt, kiểu tóc và trang phục từ ảnh gốc (Poster/Frame) được đính kèm!

---

### PHẦN 3 — QUY TẮC TIÊU ĐỀ YOUTUBE & HOOK TEXT (BÁM TÊN GỐC + BAIKE, KHÔNG BỊA):
1. **HOOK TEXT TRÊN THUMBNAIL (`hook_texts`)**:
   - Đúng 2 dòng, mỗi dòng 2–4 từ IN HOA.
   - BẮT BUỘC lấy từ **tên phim tiếng Việt/Trung đã cho** hoặc từ khóa cốt lõi trong tóm tắt Baike (vd tên nhân vật, hệ thống, biến cố).
   - CẤM paraphrase đổi nghĩa (vd «老六 / Lão Lục» KHÔNG được đổi thành «ẨN NHẪN»).
   - CẤM cụm sáo: NGHỊCH THIÊN, ĐỒ THẦN, THỨC TỈNH TUYỆT THẾ, PHẾ VẬT THỨC TỈNH, MỘT KIẾM TRẢM THẦN…
2. **TIÊU ĐỀ YOUTUBE (`titles` & `best_title`)**:
   - Công thức: `Phần N | {{PREMISE}}: {{HOOK}}, {{Ta … PAYOFF}}` — người xem phải hiểu video nói gì.
   - Ngôi «Ta»; **CẤM tên nhân vật** (dùng thân phận / hệ thống thay thế).

---

# YÊU CẦU ĐỊNH DẠNG ĐẦU RA BẮT BUỘC:
Trả về DUY NHẤT một khối JSON hợp lệ theo cấu trúc sau (không kèm văn bản ngoài JSON):
{
  "thumbnail_concept": {
    "selected_concept": "Tên concept + Hệ màu được chọn (vd: Astral Star-Forging — Ice Cyan & Nebula Violet)",
    "why": "Lý do chọn concept và bảng màu này dựa trên tình tiết thực tế trong file .srt",
    "concept_options": [
      "CONCEPT A: <mô tả>",
      "CONCEPT B: <mô tả>",
      "CONCEPT C: <mô tả>"
    ],
    "visual_dna": "Mô tả chi tiết ngoại hình nhân vật theo ảnh gốc + bảng màu chủ đạo (KHÔNG lặp lại 1 màu vàng lửa)",
    "story_moment": "Khoảnh khắc đắt giá nhất trích từ kịch bản .srt",
    "emotional_trigger": "Cảm xúc chính",
    "visual_conflict": "Xung đột thị giác chính",
    "click_question": "Câu hỏi kích thích tò mò",
    "layout": "Bố cục & góc máy cụ thể"
  },
  "typography_direction": {
    "font_style": "Kiểu chữ 3D phù hợp thể loại",
    "weight": "Extra Bold / Heavy",
    "layout": "Vị trí đặt chữ có khoảng trống âm (Negative Space)",
    "color": "Phối màu chữ 3D tương phản mạnh với nền (vd: Platinum Chrome & Ice Cyan / Emerald & Gold / Crimson & Silver...)",
    "effects": "Hiệu ứng vật liệu 3D",
    "graphic_integration": "Tương tác giữa chữ và bối cảnh/hiệu ứng",
    "text_exact": ["DÒNG 1 (2-3 TỪ IN HOA)", "DÒNG 2 (2-3 TỪ IN HOA)"]
  },
  "hook_texts": ["DÒNG 1 (2-3 TỪ)", "DÒNG 2 (2-3 TỪ)"],
  "thumb_text_options": [
    "PHƯƠNG ÁN HOOK 1",
    "PHƯƠNG ÁN HOOK 2",
    "PHƯƠNG ÁN HOOK 3",
    "PHƯƠNG ÁN HOOK 4"
  ],
  "badge_text": "NEW",
  "sub_text": "<Tên phim tiếng Việt ngắn — lấy từ tên đã cho, không bịa>",
  "master_image_prompt": "REFERENCE IMAGE MANDATE: ...\\n\\nSTORY MOMENT (English, from Baike/plot):\\n<...>\\n\\nCHARACTER & POSE: <faithful to reference>\\n\\nENVIRONMENT & COLOR PALETTE: <match chosen palette>\\n\\nINTEGRATED 3D VIETNAMESE TYPOGRAPHY (LEFT):\\n- Top badge ribbon EXACTLY: \\\"NEW\\\" or \\\"HOT\\\" (never TRỌN BỘ THUYẾT MINH)\\n- Main headline EXACTLY the two hook_texts lines (no paraphrasing names)\\n- Bottom banner: short series title\\n\\n16:9 UE5 donghua key art, high contrast.",
  "ai_image_prompt": "<Giống hệt master_image_prompt>",
  "titles": [
    "⭐ <Tiêu đề 1 xuất sắc nhất bám sát .srt (65-92 ký tự)>",
    "<Tiêu đề 2 góc tương phản bám sát .srt>",
    "<Tiêu đề 3 góc tên phim + biến cố .srt>",
    "<Tiêu đề 4 góc bàn tay vàng/năng lực .srt>",
    "<Tiêu đề 5 góc cao trào trả thù/lật kèo .srt>"
  ],
  "best_title": "<Tiêu đề xuất sắc nhất (không chứa dấu sao ⭐)>",
  "description": "<Đoạn 1: Mở đầu kịch tính từ tình tiết .srt>\\n\\n<Đoạn 2: Diễn biến & năng lực đặc biệt của nhân vật chính trong phim>\\n\\n<Đoạn 3: Cao trào và lời mời theo dõi>\\n\\n🎬 Tên phim: <Tên phim chuẩn>\\n🎙️ Nội dung: Phim thuyết minh tiếng Việt.\\n👍 Hãy nhấn Like, Đăng ký kênh và bật chuông thông báo để đón xem những siêu phẩm tiếp theo!\\n\\nLưu ý: Video có sử dụng công cụ AI hỗ trợ một số công đoạn biên tập và hậu kỳ nhằm mục đích kể lại và giải trí.\\n\\n<10 hashtags>",
  "tags": ["tag1", "tag2", "...", "25-30 tags chuẩn SEO"],
  "hashtags": ["#Donghua", "#ManhuaTiengViet", "..."],
  "pinned_comment": "<1 câu hỏi thú vị bám sát tình tiết trong phim để khán giả bình luận>",
  "creative_score": {
    "clickability": "9.5/10",
    "story_clarity": "9.5/10",
    "typography": "9/10",
    "visual_hierarchy": "9.5/10",
    "mobile_readability": "9.5/10"
  },
  "self_critique": "Điểm lưu ý về độ tương phản giữa chữ và nền",
  "revision_option": "Phương án tinh chỉnh"
}"""
    )

    user_content = (
        f"- Tên phim tiếng Việt (tạm dịch): {title_vi or 'Chưa rõ'}\n"
        f"- Tên gốc tiếng Trung: {title_zh or 'Chưa rõ'}\n"
        f"- Định hướng phong cách / Yêu cầu thêm: {prompt_guide or 'Bám sát ngoại hình ảnh gốc (Reference Image), phối màu đa dạng theo đúng thể loại phim, đặt tiêu đề từ kịch bản .srt'}\n"
        f"- Tone giọng: {tone}\n"
        f"- KỊCH BẢN & LỜI THOẠI THỰC TẾ TRÍCH XUẤT TỪ PHỤ ĐỀ (.SRT) CỦA PHIM:\n\"\"\"\n{actual_info or 'Phim hoạt hình thuyết minh kịch tính'}\n\"\"\"\n"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    # Ưu tiên 1: Aurora qua Raspberry Pi 5
    engine_used = "aurora_pi5"
    reply = ""
    try:
        reply = _call_chatgpt(messages, model="gpt-5-6")
    except Exception as e_gpt:
        # Fallback 2: Local LLM qua 9router / Ollama
        try:
            from engines.llm import chat
            engine_used = "local_llm"
            reply = chat(messages, temperature=0.6, max_tokens=3500)
        except Exception as e_local:
            return {"ok": False, "error": f"ChatGPT lỗi ({e_gpt}) & Local LLM lỗi ({e_local})"}

    data = _clean_json_response(reply)
    if not data or not isinstance(data, dict) or not data.get("titles"):
        safe_title = clean_series_name(title_vi or title_zh or "Siêu Phẩm Phim Mới", fallback="Siêu Phẩm Mới")
        fb_hooks = hooks_from_series_title(safe_title, title_zh)
        data = {
            "titles": [
                f"{safe_title} - Hành Trình Nghịch Chuyển Vận Mệnh Gây Chấn Động",
                f"Trọn Bộ | {safe_title} - Biến Cố Khiến Kẻ Thù Phải Kiêng Dè",
                f"{safe_title}: Thân Phận Thật Lộ Diện, Quét Ngang Đối Thủ",
                f"{safe_title} - Cao Trào Quyết Chiến Giữa Tuyệt Cảnh",
                f"{safe_title} | Màn Lật Kèo Đỉnh Cao Khiến Cả Giới Sững Sờ",
            ],
            "best_title": f"{safe_title} - Hành Trình Nghịch Chuyển Vận Mệnh Gây Chấn Động",
            "hook_texts": fb_hooks,
            "thumb_text_options": [f"{fb_hooks[0]} {fb_hooks[1]}", fb_hooks[0], fb_hooks[1], safe_title.upper()[:40]],
            "badge_text": "NEW",
            "sub_text": safe_title,
            "thumbnail_concept": {
                "story_moment": f"Key confrontation from «{safe_title}» plot",
                "visual_dna": "Protagonist faithful to reference poster",
                "selected_concept": "Palette matching film setting",
                "layout": "Character right, left negative space",
            },
            "master_image_prompt": "",
            "ai_image_prompt": "",
            "description": f"{safe_title} - Trọn bộ thuyết minh chất lượng cao.\n\n#Manhua #Donghua #PhimThuyetMinh #TuTien",
            "tags": [safe_title, "Phim Thuyết Minh", "Manhua Tiếng Việt", "Donghua 3D", "Tu Tiên", "Huyền Huyễn"],
            "hashtags": ["#Manhua", "#Donghua", "#PhimThuyetMinh", "#TuTien", "#HuyenHuyen"],
            "pinned_comment": "Theo bạn nhân vật chính trong bộ này sẽ xử lý kẻ thù tiếp theo thế nào? Bình luận bên dưới nhé!",
        }

    # Đồng bộ/làm giàu 5 tiêu đề từ Master Title Engine nếu cần
    try:
        t_res = generate_youtube_titles(
            series_name=data.get("sub_text") or title_vi or title_zh or "Phim Hay",
            season=1,
            episode_info="",
            summary=actual_info,
            job_id=job_id,
            count=5,
            title_zh=title_zh,
        )
        if t_res.get("ok") and t_res.get("titles"):
            data["titles"] = t_res["titles"]
            data["best_title"] = t_res.get("best_title") or t_res["titles"][0]
            if t_res.get("reason") and isinstance(data.get("thumbnail_concept"), dict):
                data["thumbnail_concept"].setdefault("story_moment", t_res["reason"])
    except Exception as e_titles:
        print(f"[meta_studio] generate_metadata sync titles error: {e_titles}", flush=True)

    data = finalize_thumb_meta_fields(
        data,
        title_vi=clean_vi or title_vi,
        title_zh=zh_name or title_zh,
        job_id=job_id or "",
    )
    return {"ok": True, "engine": engine_used, **data}


def create_thumbnail(
    poster_source: str | Path | bytes | Image.Image,
    hook_text_1: str = "",
    hook_text_2: str = "",
    badge_text: str = "NEW",
    sub_text: str = "",
    bg_frame_source: str | Path | bytes | Image.Image | None = None,
    style: str = "fire_gold",
    out_filename: str | None = None,
    include_poster: bool = True,
) -> Path:
    """Tạo ảnh Thumbnail 16:9 (1280x720) — legacy Pillow (auto-publish không dùng; AI typography chính).
    
    Args:
        poster_source: Đường dẫn file local, URL http(s), hoặc bytes/Image của poster dọc.
        hook_text_1: Dòng chữ lớn 1 (in hoa).
        hook_text_2: Dòng chữ lớn 2 (in hoa, tuỳ chọn).
        badge_text: Nhãn góc trên — chỉ NEW hoặc HOT.
        sub_text: Câu phụ bên dưới (ví dụ tên phim hoặc thể loại).
        bg_frame_source: File ảnh hoặc bytes ảnh AI 16:9 làm nền hoành tráng.
        style: fire_gold (vàng lửa), neon_cyan (xanh dạ quang), purple_magic (tím ma mị), clean_white.
        out_filename: Tên file lưu kết quả.
        include_poster: Có đặt thêm khung poster gốc ở bên phải hay không.
    """
    tw, th = 1280, 720

    # 1. Đọc Poster
    poster: Image.Image
    if isinstance(poster_source, Image.Image):
        poster = poster_source.convert("RGB")
    elif isinstance(poster_source, bytes):
        poster = Image.open(io.BytesIO(poster_source)).convert("RGB")
    elif str(poster_source).startswith(("http://", "https://")):
        req = urllib.request.Request(str(poster_source), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            poster = Image.open(io.BytesIO(resp.read())).convert("RGB")
    else:
        p = Path(poster_source)
        if not p.is_file():
            raise FileNotFoundError(f"Không tìm thấy poster: {poster_source}")
        poster = Image.open(p).convert("RGB")

    # 2. Tạo Background 16:9
    bg: Image.Image | None = None
    if bg_frame_source:
        try:
            if isinstance(bg_frame_source, Image.Image):
                bg = bg_frame_source.convert("RGB")
            elif isinstance(bg_frame_source, bytes):
                bg = Image.open(io.BytesIO(bg_frame_source)).convert("RGB")
            elif Path(str(bg_frame_source)).is_file():
                bg = Image.open(bg_frame_source).convert("RGB")
        except Exception:
            bg = None

    if bg:
        # Scale & crop về chuẩn 16:9
        bg_ratio = bg.width / max(bg.height, 1)
        target_ratio = tw / th
        if bg_ratio > target_ratio:
            new_w = int(bg.height * target_ratio)
            bg = bg.crop(((bg.width - new_w) // 2, 0, (bg.width + new_w) // 2, bg.height))
        else:
            new_h = int(bg.width / target_ratio)
            bg = bg.crop((0, (bg.height - new_h) // 2, bg.width, (bg.height + new_h) // 2))
        bg = bg.resize((tw, th), Image.Resampling.LANCZOS)
        # Giữ độ sáng và màu sắc rực rỡ của AI Artwork
        bg = ImageEnhance.Brightness(bg).enhance(0.9)
    else:
        # Fallback: Lấy poster phóng to, crop giữa 16:9 và Gaussian Blur
        bg = poster.copy()
        bg_ratio = bg.width / max(bg.height, 1)
        target_ratio = tw / th
        if bg_ratio > target_ratio:
            new_w = int(bg.height * target_ratio)
            bg = bg.crop(((bg.width - new_w) // 2, 0, (bg.width + new_w) // 2, bg.height))
        else:
            new_h = int(bg.width / target_ratio)
            bg = bg.crop((0, (bg.height - new_h) // 2, bg.width, (bg.height + new_h) // 2))
        bg = bg.resize((tw, th), Image.Resampling.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=28))
        bg = ImageEnhance.Brightness(bg).enhance(0.4)

    # Gradient tối phía bên trái (chỉ che ~55% bên trái để chữ nổi bật, bên phải giữ trọn ánh sáng ảnh AI)
    grad = Image.new("L", (tw, th))
    cover_w = int(tw * 0.58)
    for x in range(tw):
        alpha = int(215 * (1 - x / cover_w)) if x < cover_w else 0
        for y in range(th):
            grad.putpixel((x, y), max(0, min(255, alpha)))
    dark_overlay = Image.new("RGB", (tw, th), (5, 5, 12))
    bg.paste(dark_overlay, (0, 0), mask=grad)

    # 3. Đặt Poster sắc nét ở 1/3 bên phải nếu include_poster = True
    if include_poster:
        ph = 620
        pw = max(100, int(ph * (poster.width / max(poster.height, 1))))
        if pw > 480:
            pw = 480
            ph = int(pw * (poster.height / max(poster.width, 1)))

        p_resized = poster.resize((pw, ph), Image.Resampling.LANCZOS)

        # Tạo bóng đổ mềm mại cho poster
        shadow_margin = 25
        shadow = Image.new("RGBA", (pw + shadow_margin * 2, ph + shadow_margin * 2), (0, 0, 0, 0))
        s_draw = ImageDraw.Draw(shadow)
        s_draw.rectangle([shadow_margin, shadow_margin, pw + shadow_margin, ph + shadow_margin], fill=(0, 0, 0, 210))
        shadow = shadow.filter(ImageFilter.GaussianBlur(16))

        px = tw - pw - 45
        py = (th - ph) // 2
        bg.paste(shadow, (px - shadow_margin, py - shadow_margin), mask=shadow)
        bg.paste(p_resized, (px, py))

        p_border = ImageDraw.Draw(bg)
        p_border.rectangle([px, py, px + pw, py + ph], outline=(255, 255, 255, 80), width=1)

    # 4. Vẽ Typography bên trái
    draw = ImageDraw.Draw(bg)
    font_badge = ImageFont.truetype(DEFAULT_FONT, 28)
    font_hook = ImageFont.truetype(SEGOE_FONT, 66)
    font_sub = ImageFont.truetype(DEFAULT_FONT, 38)

    # Thiết lập phối màu theo style
    styles = {
        "fire_gold": {
            "badge_bg": (220, 30, 40),
            "badge_border": (255, 215, 0),
            "badge_text": (255, 255, 255),
            "hook_1": (255, 225, 30),
            "hook_2": (255, 255, 255),
            "sub": (90, 210, 255),
        },
        "neon_cyan": {
            "badge_bg": (10, 80, 180),
            "badge_border": (0, 230, 255),
            "badge_text": (255, 255, 255),
            "hook_1": (0, 240, 255),
            "hook_2": (255, 255, 255),
            "sub": (255, 215, 0),
        },
        "purple_magic": {
            "badge_bg": (120, 20, 160),
            "badge_border": (230, 100, 255),
            "badge_text": (255, 255, 255),
            "hook_1": (240, 110, 255),
            "hook_2": (255, 235, 120),
            "sub": (180, 220, 255),
        },
        "clean_white": {
            "badge_bg": (40, 40, 40),
            "badge_border": (255, 255, 255),
            "badge_text": (255, 255, 255),
            "hook_1": (255, 255, 255),
            "hook_2": (255, 215, 0),
            "sub": (200, 200, 200),
        },
    }
    palette = styles.get(style, styles["fire_gold"])

    # Badge góc trên
    badge_str = pick_badge_text(badge_text, title_vi=sub_text or "")
    if badge_str:
        bx, by = 65, 85
        bb = draw.textbbox((bx, by), badge_str, font=font_badge)
        pad = 12
        draw.rounded_rectangle(
            [bb[0] - pad, bb[1] - pad + 2, bb[2] + pad, bb[3] + pad - 2],
            radius=8,
            fill=palette["badge_bg"],
            outline=palette["badge_border"],
            width=2,
        )
        draw.text((bx, by), badge_str, font=font_badge, fill=palette["badge_text"])

    # Hook texts chính — trống thì tách từ sub_text / không dùng cụm sáo
    tx, ty = 65, 195
    line1 = (hook_text_1 or "").strip().upper()
    line2 = (hook_text_2 or "").strip().upper()
    if not line1 and not line2:
        derived = hooks_from_series_title(sub_text or "")
        line1, line2 = derived[0], derived[1]
    elif not line2 and line1 and " " in line1 and len(line1.split()) >= 4:
        ws = line1.split()
        mid = len(ws) // 2
        line1, line2 = " ".join(ws[:mid]), " ".join(ws[mid:])
    if line1 in _CLICHE_HOOKS:
        line1 = hooks_from_series_title(sub_text or "")[0]
    if line2 in _CLICHE_HOOKS:
        line2 = hooks_from_series_title(sub_text or "")[1]

    if line1:
        # Shadow giả lập 3D
        draw.text((tx + 3, ty + 3), line1, font=font_hook, fill=(0, 0, 0), stroke_width=6, stroke_fill=(0, 0, 0))
        draw.text((tx, ty), line1, font=font_hook, fill=palette["hook_1"], stroke_width=5, stroke_fill=(0, 0, 0))

    if line2:
        ty2 = ty + 85
        draw.text((tx + 3, ty2 + 3), line2, font=font_hook, fill=(0, 0, 0), stroke_width=6, stroke_fill=(0, 0, 0))
        draw.text((tx, ty2), line2, font=font_hook, fill=palette["hook_2"], stroke_width=5, stroke_fill=(0, 0, 0))

    # Subtext (Tên phim hoặc thể loại)
    sub_str = (sub_text or "").strip()
    if sub_str:
        sy = ty + (175 if line2 else 95)
        # Tự động cắt ngắn nếu quá dài
        if len(sub_str) > 35:
            sub_str = sub_str[:32] + "…"
        draw.text((tx + 2, sy + 2), sub_str, font=font_sub, fill=(0, 0, 0), stroke_width=4, stroke_fill=(0, 0, 0))
        draw.text((tx, sy), sub_str, font=font_sub, fill=palette["sub"], stroke_width=3, stroke_fill=(0, 0, 0))

    # 5. Lưu ảnh
    if not out_filename:
        import time
        out_filename = f"thumb_{int(time.time())}.jpg"
    out_path = THUMB_DIR / out_filename
    bg.save(out_path, format="JPEG", quality=92, optimize=True)
    return out_path
