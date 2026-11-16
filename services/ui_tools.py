"""Công cụ cho giao diện web (trang Phim / Studio) — trước đây CHỈ có trong sidecar (agents/agent_tele_cmd.py, :8802).

2026-11-15: V6 phục vụ trang Phim + Studio từ server :8806 và tắt sidecar → các nút Tra Baike, Tạo ảnh AI, Lưu / nạp
cấu hình batch, Dừng test kênh gọi API không tồn tại (404). Logic dời về đây; route mỏng ở app/routes/ui_compat.py;
sidecar (nếu bật) gọi lại cùng hàm.
"""
from __future__ import annotations

import io
import time


def baike(q: str) -> dict:
    """Tra thông tin phim (Baidu Baike qua hongguo_bridge.baike_deep) + dịch giới thiệu sang tiếng Việt."""
    q = (q or "").strip()
    if not q:
        raise ValueError("thiếu tên phim")
    import agents.hongguo_bridge as hg
    info = hg.baike_deep(title=q)
    intro_vi = ""
    if info.get("intro"):
        try:
            import engines.title_mt as tmt
            tr = tmt.translate_titles([{"series_id": "intro", "title": info["intro"][:300]}])
            if tr.get("items"):
                intro_vi = tr["items"][0].get("title_vi", "")
        except Exception as e:  # noqa: BLE001
            print(f"[ui_tools] dịch giới thiệu Baike lỗi: {e}", flush=True)
    return {"ok": True, "title": info.get("title") or q, "tags": info.get("tags") or [],
            "intro": info.get("intro") or "", "intro_vi": intro_vi,
            "actors": info.get("actors") or [], "roles": info.get("roles") or []}


def ai_image(body: dict) -> dict:
    """Sinh ảnh thumbnail bằng AI (agents/meta_studio.generate_ai_image), cắt 16:9 1280×720 cho YouTube.
    Ném RuntimeError khi không sinh được."""
    import agents.meta_studio as meta_studio
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Thiếu prompt")
    job_id = str(body.get("job_id") or f"gen_{int(time.time())}").strip()
    img = meta_studio.generate_ai_image(prompt, timeout=300, job_id=job_id,
                                        poster_name=str(body.get("poster_name") or "").strip())
    if not img:
        raise RuntimeError("Không sinh được ảnh (Aurora lỗi / hết thời gian)")
    raw_name = f"thumb_{job_id}_ai_raw.png"
    (meta_studio.THUMB_DIR / raw_name).write_bytes(img)
    jpg_name = f"thumb_{job_id}.jpg"
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(img)).convert("RGB")
        tw, th = 1280, 720
        ratio, target = im.width / max(im.height, 1), tw / th
        if ratio > target:
            nw = int(im.height * target)
            im = im.crop(((im.width - nw) // 2, 0, (im.width + nw) // 2, im.height))
        elif ratio < target:
            nh = int(im.width / target)
            im = im.crop((0, (im.height - nh) // 2, im.width, (im.height + nh) // 2))
        im.resize((tw, th), Image.Resampling.LANCZOS).save(meta_studio.THUMB_DIR / jpg_name, format="JPEG",
                                                            quality=95, optimize=True)
    except Exception as e:  # noqa: BLE001
        print(f"[ui_tools] cắt ảnh AI lỗi: {e} → ghi nguyên ảnh", flush=True)
        (meta_studio.THUMB_DIR / jpg_name).write_bytes(img)
    return {"ok": True, "filename": jpg_name, "raw_ai_filename": raw_name,
            "url": f"/meta/thumbnail?file={jpg_name}", "raw_url": f"/meta/thumbnail?file={raw_name}"}


def batch_config_get() -> dict:
    import core.batch_config as bc
    return {"ok": True, "opts": bc.load(), **bc.meta(), "text": bc.describe()}


def batch_config_set(body: dict, by: str = "ui") -> dict:
    """Body {opts: {...}} hoặc {khoá: giá trị}; khoá lạ bị bỏ. ValueError khi giá trị sai."""
    import core.batch_config as bc
    patch = body.get("opts") if isinstance(body.get("opts"), dict) else body
    patch = {k: v for k, v in (patch or {}).items() if k in bc.DEFAULTS}
    r = bc.update(patch, by=str(body.get("by") or by))
    return {"ok": True, **r, "text": bc.describe(r["opts"])}


def test_channel_batch_stop() -> dict:
    # cùng module với routes_films (/test-channel/batch-start) — agents.test_channel là bản sao có BATCH_STATE riêng
    import tools.test_channel as test_channel
    return test_channel.stop_batch_test()


# Trang được phép mở bằng trình duyệt ngoài từ app desktop (WebView2 của app chặn cửa sổ mới / target=_blank)
_EXTERNAL_HOSTS = ("accounts.google.com", "console.developers.google.com", "console.cloud.google.com",
                   "www.youtube.com", "youtube.com", "youtu.be", "studio.youtube.com")


def open_external(url: str) -> dict:
    """Mở `url` bằng trình duyệt mặc định của máy chạy server (= máy chạy app desktop). Chỉ trang Google / YouTube và
    trang của chính server (localhost) — không mở địa chỉ tuỳ ý. ValueError khi không được phép."""
    import os
    import webbrowser
    from urllib.parse import urlsplit
    u = urlsplit(str(url or "").strip())
    host = (u.hostname or "").lower()
    ok = (u.scheme == "https" and host in _EXTERNAL_HOSTS) or (u.scheme in ("http", "https") and host in ("127.0.0.1", "localhost"))
    if not ok:
        raise ValueError(f"không mở địa chỉ này bằng trình duyệt ngoài: {host or url}")
    try:
        if os.name == "nt":
            os.startfile(u.geturl())          # noqa: S606 — URL đã lọc host ở trên
        elif not webbrowser.open(u.geturl()):
            raise RuntimeError("không có trình duyệt")
    except Exception as e:  # noqa: BLE001
        print(f"[ui_tools] mở trình duyệt ngoài lỗi: {e}", flush=True)
        return {"ok": False, "error": f"Không mở được trình duyệt: {e}", "url": u.geturl()}
    return {"ok": True}
