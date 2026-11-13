"""
Sidecar: Telegram lệnh + HTTP mỏng (cổng AUTOPILOT_PORT của instance; V2 cũ: :8802)
KHÔNG restart Pineline server — chỉ gọi API server của instance (PINELINE_BASE).

Chạy:  python agents/agent_tele_cmd.py --instance v6     (V6: run_sidecar_v6.bat)
  hoặc  python agent_tele_cmd.py                          (kiểu V5: env PINELINE_BASE/AUTOPILOT_PORT)

V6 F4: chạy theo instance → Telegram chỉ khi cờ telegram; KHÔNG tự chạy vòng autopilot/feeder/runwatch/
film_db/config-live/auto-publish (server chạy ở services/ khi cờ feeder). Không có instance (kiểu V5) → như cũ.
Cổng mặc định khi thiếu env = cổng instance (core.instance), không bao giờ 8801/8802.

Telegram (user trong TELEGRAM_ALLOWED_USERS):
  /help /status
  /on  /off     — bật/tắt Auto-pilot
  /heal         — watch-once ngay
  /tick         — 1 vòng autopilot (queue+heal) kể cả khi off
  /stop         — dừng hàng chờ queue
  /agent <text> — chat agent (mutate)
  /running      — job đang chạy (stage, %, ETA, treo?)
  /feed [...]   — tự thêm job khi hết (yêu cầu bằng lời → spec)
  /lib [...]    — thư viện chưa làm + nút Thêm/Ưu tiên/Bỏ qua/Ghép
  /restartserver
  autopilot on|off  (plain text cũng được)

HTTP local (UI):
  GET  http://127.0.0.1:8802/status
  POST http://127.0.0.1:8802/on
  POST http://127.0.0.1:8802/off
  POST http://127.0.0.1:8802/tick
  POST http://127.0.0.1:8802/cmd  {"text":"..."}
  GET  http://127.0.0.1:8802/hg/watch?sid=<id>&ep=1   — trang xem thử tập miễn phí Hongguo
  GET  http://127.0.0.1:8802/hg/preview?sid=<id|tên|số BXH>&ep=1
"""
from __future__ import annotations
import sys as _sync_sys  # sync_from_v2: bootstrap — chạy trực tiếp vẫn import được core.*/engines.*
from pathlib import Path as _SyncP
_SYNC_ROOT = str(_SyncP(__file__).resolve().parents[1])
if _SYNC_ROOT not in _sync_sys.path:
    _sync_sys.path.insert(0, _SYNC_ROOT)

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import json
import os
import queue
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.agent_autopilot import (  # noqa: E402
    agent_chat,
    agent_confirm,
    format_status,
    hard_stop,
    load_state,
    set_enabled,
    status_brief,
    sync_pending_to_batch,
    tick_once,
)
from agents.agent_watch import telegram_config, telegram_send  # noqa: E402
import agents.agent_feeder as feeder  # noqa: E402
import core.batch_config as batch_config  # noqa: E402
import core.film_db as film_db  # noqa: E402
import agents.agent_hongguo as hg_agent  # noqa: E402

from core.instance import server_base as _server_base, sidecar_port as _sidecar_port  # noqa: E402
import services.feeder as svc_feeder  # noqa: E402

# V6 F4: cổng/URL theo instance (env do core.instance.apply_env đặt); main() --instance cập nhật lại.
BASE = _server_base()
CTRL_HOST = os.getenv("AUTOPILOT_HOST") or "127.0.0.1"
CTRL_PORT = _sidecar_port()
TICK_SEC = max(30, int(os.getenv("AUTOPILOT_TICK_SEC") or "90"))
POLL_SEC = max(1, int(os.getenv("TELEGRAM_POLL_SEC") or "2"))

_USER_HISTORIES: dict[str, list[dict]] = {}
_USER_HISTORY_LAST_TS: dict[str, float] = {}
_HISTORY_TTL_SEC = 30 * 60  # 30 phút không chat sẽ tự reset context
_MAX_HISTORY_TURNS = 10     # Lưu tối đa 10 lượt hỏi-đáp gần nhất
_HISTORY_FILE = ROOT / "data" / "tele_chat_history.json"


def _load_history_from_disk() -> None:
    global _USER_HISTORIES, _USER_HISTORY_LAST_TS
    if _HISTORY_FILE.is_file():
        try:
            data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
            _USER_HISTORIES = data.get("histories") or {}
            _USER_HISTORY_LAST_TS = data.get("last_ts") or {}
        except Exception:
            _USER_HISTORIES = {}
            _USER_HISTORY_LAST_TS = {}


def _save_history_to_disk() -> None:
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"histories": _USER_HISTORIES, "last_ts": _USER_HISTORY_LAST_TS}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(_HISTORY_FILE)
    except Exception:
        pass


_load_history_from_disk()


def get_user_history(uid: str) -> list[dict]:
    if not uid:
        return []
    now = time.time()
    last = _USER_HISTORY_LAST_TS.get(uid, 0)
    if now - last > _HISTORY_TTL_SEC:
        _USER_HISTORIES[uid] = []
    _USER_HISTORY_LAST_TS[uid] = now
    return list(_USER_HISTORIES.setdefault(uid, []))


def add_user_history(uid: str, role: str, content: str) -> None:
    if not uid or not content:
        return
    now = time.time()
    hist = _USER_HISTORIES.setdefault(uid, [])
    # Bỏ các tin nhắn quá ngắn hoặc báo lỗi kỹ thuật
    hist.append({"role": role, "content": str(content)[:2000]})
    if len(hist) > _MAX_HISTORY_TURNS * 2:
        _USER_HISTORIES[uid] = hist[-_MAX_HISTORY_TURNS * 2:]
    _USER_HISTORY_LAST_TS[uid] = now
    _save_history_to_disk()


def clear_user_history(uid: str) -> None:
    if uid in _USER_HISTORIES:
        _USER_HISTORIES[uid] = []
    _USER_HISTORY_LAST_TS[uid] = 0
    _save_history_to_disk()


def _telegram_allowed() -> bool:
    """V6 F4: instance tắt telegram (PINELINE_FLAG_TELEGRAM=0) → sidecar KHÔNG gửi/poll Telegram.
    Không có biến (kiểu V5) → cho phép như cũ."""
    v = os.getenv("PINELINE_FLAG_TELEGRAM")
    return v is None or v.strip().lower() in ("1", "true", "yes", "on")


def _ensure_agent_telegram() -> None:
    # telegram_send trong agent_watch cần AGENT_TELEGRAM=1 — chỉ khi instance cho phép telegram
    if not _telegram_allowed():
        os.environ["AGENT_TELEGRAM"] = "0"
        return
    if (os.getenv("AGENT_TELEGRAM") or "").strip() not in ("1", "true", "yes"):
        os.environ["AGENT_TELEGRAM"] = "1"


def tg_send(text: str, *, force: bool = True) -> bool:
    if not _telegram_allowed():
        return False
    _ensure_agent_telegram()
    return telegram_send(text, force=force, dedupe_key=None if force else text[:120])


def tg_api(method: str, payload: dict | None = None) -> dict:
    import requests as _rq
    token, _ = telegram_config()
    if not token:
        return {"ok": False, "error": "no TELEGRAM_BOT_TOKEN"}
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        # long-poll getUpdates cần timeout HTTP > timeout Telegram
        to = 40 if method == "getUpdates" else 20
        r = _rq.post(url, data=payload or {}, timeout=to)
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def allowed_uids() -> set[str]:
    _, uids = telegram_config()
    return set(uids)


def tg_send_video(chat_id: str, path: Path, caption: str, buttons: list | None = None,
                  meta: dict | None = None) -> dict:
    import requests as _rq
    token, _ = telegram_config()
    if not token:
        return {"ok": False, "error": "no TELEGRAM_BOT_TOKEN"}
    data = {"chat_id": chat_id, "caption": caption[:1000], "supports_streaming": "true"}
    for k in ("width", "height", "duration"):
        if meta and meta.get(k):
            data[k] = str(int(float(meta[k])))
    if buttons:
        data["reply_markup"] = _inline_kb(buttons)
    try:
        with path.open("rb") as fh:
            r = _rq.post(f"https://api.telegram.org/bot{token}/sendVideo", data=data,
                         files={"video": (path.name, fh, "video/mp4")}, timeout=180)
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


_PV_DIR = Path(os.getenv("TEMP") or os.getenv("TMP") or str(ROOT / "work")) / "hg_preview"


def send_preview(uid: str, ref: str, ep: int = 1) -> None:
    """Gửi tập xem thử (miễn phí) vào chat: video + nút chuyển tập + nút làm A-Z."""
    def _msg(t: str, buttons: list | None = None) -> None:
        p = {"chat_id": uid, "text": t[:3500], "disable_web_page_preview": "1"}
        if buttons:
            p["reply_markup"] = _inline_kb(buttons)
        tg_api("sendMessage", p)

    info = hg_agent.preview_info(ref, ep=ep)
    if not info.get("ok"):
        _msg(f"👁 {info.get('title') or ref}: {info.get('error')}")
        return
    sid, cur = str(info["series_id"]), int(info.get("ep") or ep)
    free, total = int(info.get("free") or 1), info.get("episode_cnt") or "?"
    title = info.get("title") or sid
    try:
        import engines.title_mt as title_mt
        tv = (title_mt.translate_titles([{"series_id": sid, "title": title}], workers=1).get("items") or [{}])[0]
        if tv.get("title_vi") and tv["title_vi"] != title:
            title = f"{tv['title_vi']} ({title})"
    except Exception:  # noqa: BLE001
        pass
    dur = int(float(info.get("duration_sec") or 0))
    caption = f"👁 {title}\nTập {cur}/{total} · xem thử miễn phí {free} tập · {dur // 60}:{dur % 60:02d}"
    ep_row = [(("• " if i == cur else "") + f"Tập {i}", f"hg:pv:{sid}:{i}") for i in range(1, free + 1)]
    buttons = [ep_row, [("▶ Làm A-Z", f"hg:auto:{sid}"), ("📚 Các mùa", f"hg:series:{sid}")]]
    path = None
    try:
        path = hg_agent.download_preview(info["url"], _PV_DIR / f"{sid}_{cur}.mp4")
    except Exception as e:  # noqa: BLE001
        print(f"[tele] preview download lỗi: {e}", flush=True)
    if path:
        r = tg_send_video(uid, path, caption, buttons,
                          {"width": info.get("width"), "height": info.get("height"), "duration": dur})
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if r.get("ok"):
            return
        print(f"[tele] sendVideo lỗi: {r.get('description') or r.get('error')}", flush=True)
    _msg(f"{caption}\n\nKhông gửi được video vào chat — mở link (hết hạn sau ~1 giờ):\n{info['url']}", buttons)


def send_covers(uid: str, page: int = 1) -> None:
    """Album ảnh bìa (tối đa 10) của trang Leaderboard/Catalog đang xem — giống lưới poster của app."""
    import io
    import requests as _rq

    token, _ = telegram_config()
    covers = hg_agent.page_covers(page)
    files, media = {}, []
    for i, (url, cap) in enumerate(covers[:10]):
        try:
            r = _rq.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            r.raise_for_status()
            raw = r.content
            try:
                from PIL import Image
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                im.thumbnail((720, 720))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=85)
                raw = buf.getvalue()
            except Exception:  # noqa: BLE001
                pass
            files[f"p{i}"] = (f"p{i}.jpg", raw, "image/jpeg")
            media.append({"type": "photo", "media": f"attach://p{i}", "caption": cap[:200]})
        except Exception as e:  # noqa: BLE001
            print(f"[tele] cover lỗi {url[:60]}: {e}", flush=True)
    if not media:
        tg_api("sendMessage", {"chat_id": uid, "text": "Không tải được ảnh bìa trang này."})
        return
    try:
        r = _rq.post(f"https://api.telegram.org/bot{token}/sendMediaGroup",
                     data={"chat_id": uid, "media": json.dumps(media, ensure_ascii=False)},
                     files=files, timeout=120).json()
        if not r.get("ok"):
            tg_api("sendMessage", {"chat_id": uid, "text": f"Gửi ảnh bìa lỗi: {r.get('description')}"})
    except Exception as e:  # noqa: BLE001
        tg_api("sendMessage", {"chat_id": uid, "text": f"Gửi ảnh bìa lỗi: {e}"})


def _edit_or_send(uid: str, cq: dict, text: str, buttons: list) -> None:
    mid = (cq.get("message") or {}).get("message_id")
    payload = {"chat_id": uid, "text": text[:3500], "disable_web_page_preview": "1"}
    if buttons:
        payload["reply_markup"] = _inline_kb(buttons)
    if mid:
        r = tg_api("editMessageText", {**payload, "message_id": str(mid)})
        if r.get("ok") or "not modified" in str(r.get("description") or ""):
            return
    tg_api("sendMessage", payload)


def spawn_preview(uid: str, ref: str, ep: int = 1) -> None:
    threading.Thread(target=send_preview, args=(uid, ref, ep), name=f"hg-pv-{ref}", daemon=True).start()

_SORT_WORDS = {"popular": "popular", "phổ biến": "popular", "pho bien": "popular", "hot": "popular",
               "eps": "eps", "nhiều tập": "eps", "nhieu tap": "eps", "newest": "newest", "mới": "newest", "new": "newest"}
_STATUS_WORDS = {"completed": "completed", "hoàn thành": "completed", "full": "completed", "xong": "completed",
                 "ongoing": "ongoing", "đang ra": "ongoing", "dang ra": "ongoing"}


def _parse_catalog_args(text: str) -> dict:
    """'/hg catalog Urban popular completed' → genre / sort / status; phần còn lại = từ khoá."""
    rest = (text or "").strip()
    out: dict = {"genre": "", "sort": "newest", "status": "", "q": ""}
    low = rest.lower()
    for words, key in ((_SORT_WORDS, "sort"), (_STATUS_WORDS, "status")):
        for w in sorted(words, key=len, reverse=True):
            if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low):
                out[key] = words[w]
                rest = re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", " ", rest, flags=re.I)
                low = rest.lower()
                break
    rest = re.sub(r"\b\d+\b", " ", rest).strip()
    if rest:
        try:
            genres = hg_agent.hg.genres(25)
        except Exception:  # noqa: BLE001
            genres = []
        alias = {"anime": "Animated", "hoạt hình": "Animated", "hoat hinh": "Animated", "animated": "Animated"}
        g = alias.get(rest.lower()) or next((x for x in genres if x.lower() == rest.lower()), "")
        if g:
            out["genre"] = g
        else:
            out["q"] = rest
    return out


def hg_tele_command(raw: str, uid: str = "") -> tuple[str, list]:
    rest = raw.split(maxsplit=1)[1].strip() if " " in raw else ""
    if not rest or rest in ("help", "?"):
        return (
            "🇨🇳 Lệnh Hongguo Downloader:\n"
            "/hg rank [ai | live-action | animated] — Leaderboard 100 phim như app (vd: /hg rank animated)\n"
            "/hg catalog [thể loại] [popular | eps] [completed | ongoing] — Catalog ~28k phim (vd: /hg catalog Urban popular)\n"
            "   ↳ mỗi phim có 👁 xem thử ngay cạnh ▶ làm, bấm tab/lọc/trang như app\n"
            "/hg genres — Xem 22 thể loại phim trong Catalog\n"
            "/hg search <tên> — Tìm phim theo tên kèm dịch Tiếng Việt\n"
            "/hg series <tên | số 1,2.. | ID> — Xem & làm trọn bộ các mùa (SS1, SS2..)\n"
            "/hg xem <tên | số 1,2.. | ID> [tập] — Xem thử tập miễn phí (gửi video vào chat)\n"
            "/hg auto <tên | số 1,2.. | top 1 | lib> — Tự làm trọn gói A-Z\n"
            "/hg status — Tiến độ tải Hongguo\n"
            "/hg lib — Kho phim D:\\Lib\n"
            "/hg merge <tên thư mục> — Gộp video 1080p\n"
            "/hg clean <tên thư mục> — Xóa tập mảnh lẻ\n"
            "Hoặc gõ tự nhiên: '/hg tìm cho tao 10 movie animated'!",
            []
        )

    # Chuẩn hoá nếu người dùng gõ hg_rank, hg_auto...
    if rest.startswith("hg_"):
        rest = rest.replace("hg_", "", 1)

    parts = rest.split(maxsplit=1)
    sub = parts[0].lower()
    sub_arg = parts[1].strip() if len(parts) > 1 else ""

    def _notify(t: str):
        if uid:
            tg_api("sendMessage", {"chat_id": uid, "text": t[:3500], "disable_web_page_preview": "1"})
        else:
            tg_send(t, force=True)

    def _spawn_auto(target_str: str):
        res = json.loads(hg_agent.start_auto_flow_bg(None, {"query_or_id": target_str, "auto_run": True},
                                                     notify=_notify))
        if res.get("already_running"):
            _notify(f"⏸ '{target_str}' đang chạy Auto Flow ({res.get('running_min')} phút) — không chạy thêm.")
        elif not res.get("ok"):
            _notify(f"❌ Auto Flow lỗi: {res.get('message') or res.get('error')}")

    # Hỗ trợ /hg 1, /hg 2 làm shortcut cho /hg auto 1, /hg auto 2
    if sub.isdigit() and len(sub) <= 5:
        _spawn_auto(sub)
        return (
            f"⏳ Đã nhận lệnh làm phim #{sub} trọn gói A-Z.\n"
            "Tiến trình đang chạy ngầm: Tải -> Gộp 1080p -> Xóa rác -> Tạo Job -> Xếp hàng chạy pipeline.\n"
            "Tiến độ từng bước sẽ được thông báo ngay bên dưới!",
            []
        )

    if sub in ("xem", "preview", "pv", "xemthu", "xem_thu", "trailer", "watch"):
        if not sub_arg:
            return "Dùng: /hg xem <tên phim / số thứ tự BXH / series_id> [tập] (vd: /hg xem 3, /hg xem 3 tập 2)", []
        m_ep = re.search(r"(?:\s+(?:tập|tap|ep|e)\s*|\s+)(\d{1,2})$", sub_arg, re.I)
        ref, ep = sub_arg, 1
        if m_ep and sub_arg[:m_ep.start()].strip():
            ref, ep = sub_arg[:m_ep.start()].strip(), int(m_ep.group(1))
        if not uid:
            info = hg_agent.preview_info(ref, ep=ep)
            if not info.get("ok"):
                return f"👁 {info.get('error')}", []
            return f"👁 {info.get('title')} · tập {info.get('ep')}/{info.get('episode_cnt')}\n{info.get('watch_url')}", []
        spawn_preview(uid, ref, ep)
        return f"⏳ Đang lấy tập {ep} xem thử của '{ref}'…", []

    low_all = (sub + " " + sub_arg).lower()

    # Nhận diện thể loại được yêu cầu trong câu (hoạt hình, live-action, AI, v.v.)
    req_cat = "all"
    req_genre = ""
    if any(w in low_all for w in ("anime", "anim", "hoạt hình", "hoathinh", "comic")):
        req_cat = "comic"
        req_genre = "Animated"
    elif any(w in low_all for w in ("live-action", "live action", "human", "người đóng", "nguoi dong", "người thật")) \
            or "live" in low_all.split():
        req_cat = "human"
        req_genre = "Live-action"
    elif "ai" in low_all.split():
        req_cat = "ai"
        req_genre = "AI"

    m_num = re.search(r"\d+", low_all)
    limit = int(m_num.group()) if m_num else (100 if "100" in low_all else (50 if "50" in low_all else 20))
    limit = min(100, max(1, limit))

    if sub in ("genres", "genre", "theloai", "thể_loại"):
        d = json.loads(hg_agent.tool_hg_genres())
        genres = d.get("genres") or []
        lines = ["🏷 Danh sách thể loại Hongguo Catalog (22 thể loại):"]
        for i, g in enumerate(genres, 1):
            lines.append(f"{i}. {g}")
        lines.append("\nDùng: /hg catalog <thể loại> (vd: /hg catalog Animated, /hg catalog Urban)")
        return "\n".join(lines), []

    if sub in ("catalog", "cat", "danhmuc", "danh_mục", "khampha", "khám_phá"):
        try:
            hg_agent.load_catalog(api_page=1, **_parse_catalog_args(sub_arg))
        except Exception as e:  # noqa: BLE001
            return f"Lỗi lấy Catalog: {e}", []
        return hg_agent.render_view(1)

    # Nếu người dùng gõ câu tự nhiên yêu cầu tìm/lấy danh sách phim theo thể loại
    # VD: "tìm cho tao 10 movie animated", "top 10 anime", "10 phim hoạt hình"
    is_category_query = (
        req_genre != "" and (
            sub in ("rank", "top", "bxh", "hot", "catalog", "cat")
            or any(w in low_all for w in ("tìm", "tim", "search", "cho tao", "list", "danh sách", "movie", "phim"))
        )
    )

    if is_category_query or sub in ("rank", "top", "bxh", "hot", "leaderboard", "lb"):
        try:
            hg_agent.load_leaderboard(req_cat)
        except Exception as e:  # noqa: BLE001
            return f"Lỗi lấy Leaderboard: {e}", []
        return hg_agent.render_view(1)

    if sub in ("search", "find", "tim", "tìm"):
        if not sub_arg:
            return "Dùng: /hg search <tên phim>", []
        d = json.loads(hg_agent.tool_hg_search(sub_arg, limit=8))
        if not d.get("ok"):
            return f"Lỗi tìm kiếm: {d.get('error')}", []
        res = d.get("results") or []
        if not res:
            return f"Không tìm thấy phim nào khớp với '{sub_arg}'.", []
        lines = [f"🔎 Tìm thấy {len(res)} phim cho '{sub_arg}':"]
        buttons = []
        for i, r in enumerate(res, 1):
            t_vi = r.get("title_vi") or ""
            t_zh = r.get("title") or ""
            disp = f"{t_vi} ({t_zh})" if t_vi and t_vi != t_zh else t_zh
            lines.append(f"{i}. {disp} ({r.get('episodes') or '?'} tập, điểm {r.get('score')})")
            buttons.append([(f"👁 Xem #{i}", f"hg:pv:{r['series_id']}:1"),
                            (f"▶ Làm #{i}", f"hg:auto:{r['series_id']}")])
        return "\n".join(lines), buttons

    if sub in ("series", "seasons", "season", "phan", "phần", "full", "he_liet", "franchise"):
        if not sub_arg:
            return "Dùng: /hg series <tên phim / số thứ tự / series_id>", []
        d = json.loads(hg_agent.tool_hg_series(sub_arg))
        if not d.get("ok"):
            return f"❌ {d.get('message') or d.get('error')}", []
        seasons = d.get("seasons") or []
        target_title = d.get("target_title") or sub_arg
        total_seasons = d.get("total_seasons", len(seasons))
        total_eps = d.get("total_episodes", 0)
        lines = [
            f"🎬 Trọn bộ Series: {target_title}",
            f"📊 Gồm {total_seasons} phần · Tổng {total_eps} tập:\n",
        ]
        buttons = []
        all_sids = []
        for i, s in enumerate(seasons, 1):
            t_vi = s.get("title_vi") or ""
            t_zh = s.get("title") or ""
            disp = f"{t_vi} ({t_zh})" if t_vi and t_vi != t_zh else t_zh
            eps_str = f"{s.get('episodes') or '?'} tập"
            date_str = f" · {s.get('date')}" if s.get('date') else ""
            score_str = f" · ⭐ {s.get('score')}" if s.get('score') else ""
            lines.append(f"{i}. {disp}\n   👉 {eps_str}{date_str}{score_str} (ID: {s.get('series_id')})")
            all_sids.append(str(s.get("series_id")))

        if len(seasons) > 1:
            buttons.append([("🚀 Làm trọn bộ (All Phần)", f"hg:series_all:{','.join(all_sids)}")])
        for i, s in enumerate(seasons, 1):
            buttons.append([(f"👁 Xem Phần {i}", f"hg:pv:{s['series_id']}:1"),
                            (f"▶ Làm Phần {i}", f"hg:auto:{s['series_id']}")])
        return "\n".join(lines), buttons

    if sub in ("status", "st", "tiendo", "tiến_độ"):
        d = json.loads(hg_agent.tool_hg_status())
        if not d.get("ok"):
            return f"Hongguo status: {d.get('message') or d.get('error')}", []
        lines = [
            f"📥 Hongguo Status: {'Đang tải' if d.get('running') else 'Nghỉ'}",
            f"Tốc độ: {d.get('speed_rate')}",
            f"Tập xong: {d.get('episodes_done')}/{d.get('episodes_need')}",
        ]
        for s in (d.get("queue_series") or [])[:5]:
            lines.append(f"- {s.get('title')}: {s.get('status')} ({s.get('progress') or ''})")
        return "\n".join(lines), []

    if sub in ("auto", "flow", "az", "run"):
        if not sub_arg:
            return "Dùng: /hg auto <tên phim / số thứ tự / 'top 1' / 'lib'>", []
        _spawn_auto(sub_arg)
        return (
            f"⏳ Đã nhận lệnh làm phim '{sub_arg}' trọn gói A-Z.\n"
            "Tiến trình đang chạy ngầm: Tải -> Gộp 1080p -> Xóa rác -> Tạo Job -> Xếp hàng chạy pipeline.\n"
            "Tiến độ từng bước sẽ được thông báo ngay bên dưới!",
            []
        )

    if sub in ("lib", "library", "thuvien", "thư_viện"):
        d = json.loads(hg_agent.tool_hg_library())
        if not d.get("ok"):
            return f"Lỗi đọc thư viện: {d.get('error')}", []
        series = d.get("series") or []
        lines = [f"📚 Thư viện D:\\Lib ({len(series)} bộ):"]
        for s in series[:10]:
            merged_tag = "✅ Đã gộp" if s.get("merged") else "⏳ Chưa gộp"
            lines.append(f"• {s.get('folder_name')} ({merged_tag}, {s.get('fragments')} mảnh)")
        return "\n".join(lines), []

    if sub in ("merge", "gop", "ghép"):
        if not sub_arg:
            return "Dùng: /hg merge <tên thư mục phim>", []
        d = json.loads(hg_agent.tool_hg_merge(sub_arg))
        return d.get("message") or str(d), []

    if sub in ("clean", "xoa", "dọn"):
        if not sub_arg:
            return "Dùng: /hg clean <tên thư mục phim>", []
        d = json.loads(hg_agent.tool_hg_clean(sub_arg))
        return f"Đã dọn dẹp thư mục {d.get('folder')}: xóa {d.get('removed_count')} tập mảnh.", []

    # Chat fallback
    d = _agent_ask(rest, uid)
    return (d.get("reply") or d.get("error") or "(trống)")[:3500], []


def _send_confirm_buttons(uid: str, confirms: list[dict]) -> None:
    for c in confirms or []:
        cid = str(c.get("id") or "")
        if not cid:
            continue
        tg_api("sendMessage", {
            "chat_id": uid,
            "text": f"⚠️ Agent muốn chạy:\n{str(c.get('summary') or c.get('tool'))[:900]}\n\n"
                    "Bấm ✅ để chạy, ❌ để huỷ (hết hạn sau 15 phút).",
            "reply_markup": _inline_kb([[("✅ Chạy", f"agc:{cid}:y"), ("❌ Huỷ", f"agc:{cid}:n")]]),
            "disable_web_page_preview": "1",
        })


def _agent_ask(msg: str, uid: str = "") -> dict:
    """Chat agent; có uid (Telegram) thì tool phá huỷ đỗ lại và gửi nút ✅/❌."""
    hist = get_user_history(uid) if uid else []
    # fix 2026-11-12 agentI2: tin người gõ trên Telegram (uid đã qua allowed_uids) → header user → tool
    # run_stage/dịch/tts/mux tính là CHẠY TAY (gỡ HOLD + 1 tin). Không uid (gọi nội bộ) → không gắn.
    d = agent_chat(BASE, msg, history=hist, allow_mutate=True, require_confirm=bool(uid),
                   caller="user" if uid else None, source="telegram" if uid else None)
    if uid and d.get("reply"):
        add_user_history(uid, "user", msg)
        add_user_history(uid, "assistant", d["reply"])
    if uid and d.get("confirms"):
        _send_confirm_buttons(uid, d["confirms"])
    return d


def _confirm_callback(uid: str, cq: dict, data: str) -> None:
    _, cid, yn = (data.split(":") + ["", ""])[:3]
    d = agent_confirm(BASE, cid, yn == "y", caller="user", source="telegram")   # agentI2: người bấm ✅
    if not d.get("ok"):
        text = f"❌ {d.get('error') or 'Xác nhận lỗi'}"
    elif d.get("cancelled"):
        text = f"🚫 Đã huỷ: {d.get('summary')}"
    else:
        res = d.get("result")
        body = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, indent=1)
        text = f"✅ Đã chạy: {d.get('summary')}\n\n{str(body)[:2500]}"
    _edit_or_send(uid, cq, text[:3500], [])


_PLAIN_MUTATE = frozenset({
    "feed", "feeder", "reset", "stop", "stopqueue", "restartserver", "set", "heal", "tick", "on", "off",
})
_FEED_READ = frozenset({"", "status", "show", "preview", "xem"})
_HG_MUTATE = frozenset({"auto", "flow", "az", "run", "merge", "gop", "ghép", "ghep", "clean", "xoa", "xoá", "dọn", "don"})
_AUTOPILOT_WORDS = frozenset({"autopilot", "auto-pilot", "auto pilot"})


def _slash_hint(text: str) -> tuple[str | None, str]:
    """Chữ thường (không '/') mà đổi trạng thái → ("hint", gợi ý) nếu ngắn kiểu lệnh hoặc có tiền tố
    hg/lib, ("agent", "") nếu là câu dài (agent hiểu, tool phá huỷ vẫn phải bấm ✅). (None, "") = đi tiếp."""
    raw = (text or "").strip()
    if not raw or raw.startswith("/"):
        return None, ""
    low = re.sub(r"\s+", " ", raw.lower())
    words = low.split(" ")
    first = words[0]
    second = words[1] if len(words) > 1 else ""
    short = len(words) <= 3 and "?" not in raw

    for ap_word in _AUTOPILOT_WORDS:
        if low == ap_word:
            return None, ""
        if low.startswith(ap_word + " ") and low[len(ap_word) + 1:] in ("on", "off", "bật", "tắt", "bat", "tat"):
            on = low[len(ap_word) + 1:] in ("on", "bật", "bat")
            return "hint", f"Bật/tắt Auto-pilot cần dấu /: gõ {'/on' if on else '/off'}"

    mutating = prefixed = False
    if first in _PLAIN_MUTATE:
        mutating = not (first in ("feed", "feeder") and " ".join(words[1:]) in _FEED_READ)
    elif first in ("hg", "hongguo") or first.startswith("hg_"):
        sub = first[3:] if first.startswith("hg_") else second
        mutating = prefixed = sub in _HG_MUTATE or (sub.isdigit() and len(sub) <= 5)
    elif first in ("lib", "thuvien", "library") and second:
        mutating = prefixed = bool(feeder.lib_verb(second))
    if not mutating:
        return None, ""
    if short or prefixed:
        return "hint", f"Lệnh thay đổi trạng thái cần dấu /: gõ /{raw}"
    return "agent", ""


def handle_command(text: str, uid: str = "") -> str:
    raw = (text or "").strip()
    if not raw:
        return "Lệnh trống. /help"
    kind, hint = _slash_hint(raw)
    if kind == "hint":
        return hint
    if kind == "agent":
        return _agent_reply(raw, uid)
    low = raw.lower().strip()
    parts = low.split(maxsplit=1)
    if low.startswith("/"):
        cmd = parts[0].split("@", 1)[0]
        arg = parts[1] if len(parts) > 1 else ""
    else:
        cmd = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/help", "help", "?"):
        return (
            "🤖 Pineline Auto-pilot & Agent Bot\n"
            "/on /off — Bật/tắt Auto-pilot\n"
            "/status — Queue + tiến độ tổng quát\n"
            "/running — Job đang chạy: stage, %, thời gian, cảnh báo treo\n"
            "/stop — Dừng hàng chờ & tắt Auto-pilot\n"
            "/heal — Tự sửa lỗi job treo/thất bại an toàn\n"
            "/tick — Chạy 1 vòng queue + heal ngay\n"
            "/history — Xem bộ nhớ ngữ cảnh hội thoại gần nhất\n"
            "/new — Làm mới phiên chat (xóa bộ nhớ ngữ cảnh)\n"
            "/hg — Lệnh Hongguo (rank / search / series / auto / status / lib / merge / clean)\n"
            "/lib — Thư viện chưa làm (gợi ý + nút Thêm / Ưu tiên / Ghép)\n"
            "/films — Thống kê database phim đã làm\n"
            "/film <job_id> — Chi tiết + lịch sử 1 phim\n"
            "/feed — Tự động chọn và thêm job theo tiêu chí\n"
            "/config /set /reset — Cấu hình batch xử lý video\n"
            "/agent <câu hỏi> — AI Agent điều khiển toàn bộ pipeline\n"
            "💡 Mẹo: Bạn có thể chat tự nhiên trực tiếp (vd: 'Xem top 5 phim hot trên hongguo', 'Gộp bộ số 2')"
        )

    if cmd in ("/new", "new", "/newchat", "newchat", "/clear", "clear", "/clearchat", "clearchat"):
        if uid:
            clear_user_history(uid)
        return "🔄 Đã làm mới phiên hội thoại (xóa bộ nhớ ngữ cảnh). Bạn có thể bắt đầu chủ đề mới!"

    if cmd in ("/history", "history", "/chat", "chat"):
        hist = get_user_history(uid) if uid else []
        if not hist:
            return "📭 Bộ nhớ chat hiện đang trống."
        lines = [f"🧠 Bộ nhớ ngữ cảnh gần đây ({len(hist)} tin nhắn):"]
        for i, m in enumerate(hist[-6:], 1):
            role = "👤 Bạn" if m.get("role") == "user" else "🤖 Bot"
            content = (m.get("content") or "").strip()
            if len(content) > 120:
                content = content[:120] + "..."
            lines.append(f"{i}. {role}: {content}")
        lines.append("\nGõ /new để xóa bộ nhớ khi muốn bắt đầu chủ đề mới.")
        return "\n".join(lines)

    if cmd in ("/hg", "hg", "/hongguo", "hongguo") or cmd.startswith("hg_"):
        hg_in = raw if raw.startswith("/") else "/hg " + (raw[3:] if raw.lower().startswith("hg ") else raw)
        return hg_tele_command(hg_in, uid=uid)[0]

    if cmd in ("/lib", "lib", "/thuvien", "/library"):
        rest = raw.split(maxsplit=1)[1].strip() if " " in raw else ""
        return svc_feeder.lib_command(rest, BASE)[0]

    if cmd in ("/films", "films", "/phim"):
        rest = raw.split(maxsplit=1)[1].strip() if " " in raw else ""
        if not rest:
            return film_db.format_stats() + "\n\nMới nhất:\n" + film_db.format_rows(film_db.query(limit=10)) \
                + f"\n\nXem bảng đầy đủ: http://127.0.0.1:{CTRL_PORT}/films/ui"
        if rest.lower() in ("done", "failed", "running", "pending", "uploaded", "not_uploaded"):
            rows = film_db.query(status=rest.lower(), limit=40)
        else:
            rows = film_db.query(q=rest, limit=40, order="series")
        return f"{len(rows)} phim:\n" + film_db.format_rows(rows)

    if cmd in ("/film", "film"):
        rest = raw.split(maxsplit=1)[1].strip() if " " in raw else ""
        if not rest:
            return "Dùng: /film <job_id>"
        return film_db.format_detail(rest)

    if cmd in ("/delfilm", "delfilm", "/delfilms", "delfilms", "/xoaphim", "xoaphim"):
        rest = raw.split(maxsplit=1)[1].strip() if " " in raw else ""
        if not rest:
            return "Cách dùng: /delfilm <job_id> [work] (thêm 'work' nếu muốn xoá cả thư mục trên ổ đĩa)"
        parts = rest.split()
        jid = parts[0]
        del_work = len(parts) > 1 and parts[1].lower() in ("work", "all", "true", "1")
        res = film_db.delete_film(jid, delete_work=del_work)
        if res.get("deleted"):
            msg = f"Đã xoá film {jid} khỏi database."
            if res.get("work_deleted"):
                msg += " (Đã xoá cả thư mục work)."
            return msg
        return f"Không tìm thấy job {jid} trong database."

    if cmd in ("/running", "running", "/jobs"):
        return svc_feeder.running_text(BASE)

    if cmd in ("/feed", "feed", "/feeder"):
        rest = raw.split(maxsplit=1)[1].strip() if " " in raw else ""
        return svc_feeder.feed_command(rest, BASE)   # V6 F4: chung với server (services/feeder.py)

    if cmd in ("/restartserver", "restartserver"):
        import subprocess
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen([sys.executable, str(ROOT / "restart_server.py")], cwd=str(ROOT), creationflags=flags)
        return f"Đang restart server {BASE} — hàng chờ + Auto-pilot được giữ, báo lại khi xong."

    if cmd in ("/status", "status"):
        return format_status(status_brief(BASE))

    if cmd in ("/config", "config"):
        m = batch_config.meta()
        return (
            "Config batch (Auto-pilot + Agent dùng chung)\n"
            + batch_config.describe()
            + f"\n— sửa lúc {m.get('updated_at') or 'mặc định'} bởi {m.get('updated_by') or '-'}"
            + "\nĐổi: /set audio_mode=voice_bgm orig_vol=0.3 · /reset"
        )

    if cmd in ("/set", "set"):
        rest = raw.split(maxsplit=1)[1] if " " in raw else ""
        if not rest:
            return "Dùng: /set key=value ... (xem /config). Ví dụ: /set audio_mode=voice_bgm orig_vol=0.6"
        try:
            r = batch_config.update(batch_config.parse_kv(rest), by="telegram")
        except ValueError as e:
            return f"Không đổi được: {e}"
        ch = r["changed"]
        if not ch:
            return "Không có gì thay đổi."
        return "Đã lưu config batch:\n" + "\n".join(f"{k} = {v}" for k, v in ch.items()) + \
            "\n(job chưa chạy trong hàng chờ tự theo config mới trong ~5s; job đang chạy giữ config cũ)"

    if cmd in ("/reset", "reset"):
        batch_config.reset(by="telegram")
        return "Config batch về mặc định:\n" + batch_config.describe()

    if cmd in ("/on", "/autopilot") and (arg in ("", "on", "1", "true") or cmd == "/on"):
        if cmd == "/autopilot" and arg in ("off", "0", "false"):
            set_enabled(False)
            return "Auto-pilot OFF"
        set_enabled(True)
        r = tick_once(BASE, force=True)
        return "Auto-pilot ON\n" + format_status(status_brief(BASE)) + f"\nTick: {json.dumps(r.get('actions'), ensure_ascii=False)[:400]}"

    if low in _AUTOPILOT_WORDS:
        on = bool(load_state().get("enabled"))
        return f"Auto-pilot {'ON' if on else 'OFF'} (đổi: /on · /off)\n" + format_status(status_brief(BASE))

    if cmd in ("/off",) or (cmd == "/autopilot" and arg in ("off", "0", "false")):
        set_enabled(False)
        return "Auto-pilot OFF\n" + format_status(status_brief(BASE))

    if cmd in ("/heal", "heal"):
        import requests as _rq
        d = _rq.post(BASE + "/api/agent/watch-once", json={}, timeout=180).json()
        return f"heal: skipped={d.get('skipped')} healed={d.get('n_healed')} raw={d.get('n_raw_problems') or d.get('n_problems')} err={d.get('error')}"

    if cmd in ("/tick", "tick"):
        r = tick_once(BASE, force=True)
        return "Tick xong\n" + format_status(status_brief(BASE)) + f"\n{json.dumps(r.get('actions'), ensure_ascii=False)[:500]}"

    if cmd in ("/stop", "/stopqueue", "stop"):
        # Tắt Auto-pilot trước — nếu không nó sẽ xếp lại job sau vài chục giây
        set_enabled(False)
        d = hard_stop(BASE)
        return (
            "STOP ALL + Auto-pilot OFF\n"
            + format_status(status_brief(BASE))
            + f"\nstopped_jobs={d.get('stopped_jobs')}"
        )

    if cmd in ("/agent", "agent") or low.startswith("/agent "):
        msg = raw.split(maxsplit=1)[1].strip() if " " in raw else ""
        if not msg:
            return "Dùng: /agent <câu hỏi/lệnh>"
        return _agent_reply(msg, uid)

    # Chat tự nhiên: chuyển thẳng cho AI Agent (có đầy đủ mọi tools Hongguo + Pipeline)
    return _agent_reply(raw, uid)


def _agent_reply(msg: str, uid: str = "") -> str:
    try:
        d = _agent_ask(msg, uid)
    except Exception as e:  # noqa: BLE001
        return f"Lỗi gọi Agent: {e}"
    if d.get("reply"):
        return d["reply"][:3500]
    if d.get("error"):
        return f"Agent lỗi: {d['error']}"
    return "Không hiểu. Gõ /help hoặc chat câu hỏi/lệnh tự nhiên."


def _fetch_poster_bytes(name: str) -> bytes | None:
    name = (name or "").strip()
    if not name:
        return None
    import urllib.request
    import urllib.parse
    # 1. Thử gọi /api/hg/poster của server Pineline 8801
    try:
        url = f"{BASE}/api/hg/poster?name={urllib.parse.quote(name)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Pineline"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = r.read()
            if data and len(data) > 100:
                return data
    except Exception:
        pass
    # 2. Thử gọi Hongguo Downloader 8000
    try:
        import agents.hongguo_bridge as hg
        data, _ = hg.fetch_poster(name, hg.DEFAULT_BASE)
        if data and len(data) > 100:
            return data
    except Exception:
        pass
    # 3. Thử resolve file local
    try:
        import agents.hongguo_bridge as hg
        p = hg.resolve_poster_path(name)
        if p and p.is_file():
            return p.read_bytes()
    except Exception:
        pass
    return None


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class CtrlHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003
        print(f"[autopilot-http] {self.address_string()} {fmt % args}", flush=True)

    @staticmethod
    def _is_local(v: str) -> bool:
        from urllib.parse import urlsplit
        try:
            return (urlsplit(v if "//" in v else "//" + v).hostname or "").lower() in _LOCAL_HOSTS
        except ValueError:
            return False

    def _blocked(self) -> bool:
        """UI :8801 gọi sang đây cross-origin → chỉ cho origin localhost; trang web lạ
        (Origin/Referer khác máy) hoặc Host lạ (DNS rebinding) → 403."""
        path = self.path.split("?", 1)[0]
        # Cho phép các endpoint OAuth callback từ Google / Canva chuyển hướng về
        if path in ("/test-channel/callback", "/canva/callback"):
            return False
        host = self.headers.get("Host") or ""
        origin = self.headers.get("Origin")
        referer = self.headers.get("Referer")
        bad = (host and not self._is_local(host)) or (origin is not None and not self._is_local(origin)) \
            or (origin is None and referer and not self._is_local(referer))
        if bad:
            body = b'{"ok": false, "error": "forbidden"}'
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        return bool(bad)

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin and self._is_local(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802
        if self._blocked():
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if self._blocked():
            return
        path = self.path.split("?", 1)[0]
        if path in ("/", "/status"):
            st = load_state()
            brief = status_brief(BASE)
            self._json(200, {"ok": True, "state": st, "brief": brief, "text": format_status(brief)})
            return
        if path == "/config":
            self._json(200, {"ok": True, "opts": batch_config.load(), **batch_config.meta(),
                             "text": batch_config.describe()})
            return
        if path in ("/films", "/films.csv"):
            try:
                film_db.sync_uploads()
            except Exception:
                pass
            from urllib.parse import parse_qs, urlsplit
            qs = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            rows = film_db.query(q=qs.get("q", ""), status=qs.get("status", ""),
                                 limit=int(qs.get("limit") or 5000), order=qs.get("order", "recent"),
                                 work_exists=qs.get("work_exists"),
                                 block_status=qs.get("block_status", ""))
            if path == "/films.csv":
                body = ("\ufeff" + film_db.to_csv(rows)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", "attachment; filename=films.csv")
                self._cors()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(200, {"ok": True, "stats": film_db.stats(), "films": rows})
            return
        # Mergeable / merge-parts trước catch-all /films/{id}
        try:
            import agents.films_merge_api as _fma
            hit = _fma.dispatch("GET", path)
            if hit is not None:
                code, payload = hit
                self._json(code, payload)
                return
        except Exception as e:  # noqa: BLE001
            if path in ("/films/mergeable",):
                self._json(500, {"ok": False, "error": f"films_merge: {e}"})
                return
        if path.startswith("/films/") and path != "/films/ui":
            d = film_db.get(path.rsplit("/", 1)[1])
            self._json(200 if d else 404, {"ok": bool(d), "film": d})
            return
        if path == "/library":
            try:
                import importlib
                # Không reload hongguo_bridge — reload làm orphan MainKt (stdio handle mất).
                global feeder
                feeder = importlib.reload(feeder)
                self._json(200, feeder.library_view(BASE))
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": str(e)})
            return
        if path == "/library/accounts":
            try:
                import tools.lib_check as lib_check
                self._json(200, {"ok": True, "accounts": lib_check.list_yt_accounts()})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e), "accounts": []})
            return
        if path in ("/test-channel/status", "/test-channel/recheck-health", "/test-channel/reset-quota"):
            import agents.test_channel as test_channel
            import importlib
            test_channel = importlib.reload(test_channel)
            if path == "/test-channel/reset-quota":
                from urllib.parse import parse_qs, urlsplit
                qs = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                pid = qs.get("project_id")
                self._json(200, test_channel.reset_exhausted_projects(pid))
                return
            if path == "/test-channel/recheck-health":
                test_channel.verify_all_projects_health(force=True)
            self._json(200, test_channel.get_status())
            return
        if path == "/test-channel/auth-url":
            import agents.test_channel as test_channel
            import importlib
            test_channel = importlib.reload(test_channel)
            from urllib.parse import parse_qs, urlsplit
            qs = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            host = self.headers.get("Host") or f"127.0.0.1:{CTRL_PORT}"
            redirect_uri = qs.get("redirect_uri") or f"http://{host}/test-channel/callback"
            project_id = qs.get("project_id")
            try:
                auth_res = test_channel.get_auth_url(redirect_uri, project_id=project_id)
                self._json(200, {**auth_res, "redirect_uri": redirect_uri})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/test-channel/callback":
            import agents.test_channel as test_channel
            import importlib
            test_channel = importlib.reload(test_channel)
            from urllib.parse import parse_qs, urlsplit
            qs = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            code = qs.get("code") or ""
            state = qs.get("state") or ""
            host = self.headers.get("Host") or f"127.0.0.1:{CTRL_PORT}"
            redirect_uri = f"http://{host}/test-channel/callback"
            html = ""
            if not code:
                html = "<h3>⚠️ Không tìm thấy authorization code từ Google.</h3>"
            else:
                try:
                    res = test_channel.finish_oauth(code, redirect_uri, project_id=state)
                    ch_name = res.get("channel_title") or "Kênh Test"
                    html = f"""
                    <!DOCTYPE html><html><head><meta charset="utf-8"><title>Kết nối thành công</title>
                    <style>body{{background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:90vh;margin:0}}
                    .box{{background:#161b22;padding:30px;border-radius:12px;border:1px solid #30363d;text-align:center;max-width:460px}}
                    h2{{color:#238636;margin-top:0}}
                    a{{color:#58a6ff;text-decoration:none}}
                    .b{{display:inline-block;padding:10px 20px;background:#238636;color:#fff;border-radius:6px;font-weight:600;margin-top:15px}}</style></head>
                    <body><div class="box">
                      <h2>🎉 Kết nối Kênh Phụ Test Thành Công!</h2>
                      <p>Kênh đã liên kết: <b>{ch_name}</b> ({res.get('email')})</p>
                      <p style="color:#8b949e;font-size:13px">Từ nay Pineline sẽ tự động upload clip mẫu 90s lên kênh này để kiểm tra bản quyền.</p>
                      <a href="/films/ui" class="b">Quay lại Giao diện Pineline</a>
                      <script>setTimeout(function(){{ if(window.opener){{ window.close(); }} else {{ window.location.href='/films/ui'; }} }}, 3000);</script>
                    </div></body></html>
                    """
                except Exception as e:
                    html = f"<h3>❌ Lỗi kết nối Google OAuth: {e}</h3><br><a href='/films/ui'>Quay lại</a>"
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/api/models", "/api/llm/models"):
            import engines.llm as llm
            self._json(200, llm.list_available_models())
            return
        if path == "/api/hg/poster":
            # Proxy poster → :8801 / Hongguo / local (Films UI trên :8802)
            from urllib.parse import parse_qs, urlsplit
            qs = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            name = (qs.get("name") or "").strip()
            data = _fetch_poster_bytes(name) if name else None
            if not data:
                self._json(404, {"ok": False, "error": "poster not found"})
                return
            ctype = "image/jpeg"
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                ctype = "image/png"
            elif data[:4] == b"RIFF":
                ctype = "image/webp"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/test-channel/batch-progress":
            import agents.test_channel as test_channel
            self._json(200, test_channel.get_batch_progress())
            return
        if path in ("/hg/watch", "/hg/preview"):
            from urllib.parse import parse_qs, urlsplit
            qs = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            ref = qs.get("sid") or qs.get("q") or ""
            ep = int(qs.get("ep") or 1) if str(qs.get("ep") or "1").isdigit() else 1
            try:
                if path == "/hg/preview":
                    self._json(200, hg_agent.preview_info(ref, ep=ep))
                    return
                body = hg_agent.render_watch_page(ref, ep).encode("utf-8")
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/hongguo", "/hongguo2", "/v5-map"):
            # UI Hongguo / map chạy trên Pineline :8801 — redirect (tránh JSON not found)
            loc = f"{BASE}{path}"
            body = (
                f'<!doctype html><meta charset="utf-8">'
                f'<meta http-equiv="refresh" content="0;url={loc}">'
                f'<p>Chuyển tới <a href="{loc}">{loc}</a></p>'
            ).encode("utf-8")
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/films/ui", "/library/ui"):
            ui = ROOT / "films_ui.html"
            html = ui.read_text(encoding="utf-8") if ui.is_file() else FILMS_UI
            try:   # review2: :8801/:8802 cứng (V2) → server/sidecar của instance này
                from core.instance import localize_html
                html = localize_html(html, server=BASE, sidecar="")
            except Exception:  # noqa: BLE001
                pass
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/meta/baike", "/api/meta/baike"):
            from urllib.parse import parse_qs, urlsplit
            qs = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            q = (qs.get("q") or qs.get("title") or "").strip()
            if not q:
                self._json(400, {"ok": False, "error": "thiếu tên phim"})
                return
            try:
                import agents.hongguo_bridge as hg
                info = hg.baike_deep(title=q)
                intro_vi = ""
                try:
                    import engines.title_mt as tmt
                    if info.get("intro"):
                        tr = tmt.translate_titles([{"series_id": "intro", "title": info["intro"][:300]}])
                        if tr.get("items"):
                            intro_vi = tr["items"][0].get("title_vi", "")
                except Exception:
                    pass
                self._json(200, {
                    "ok": True,
                    "title": info.get("title") or q,
                    "tags": info.get("tags") or [],
                    "intro": info.get("intro") or "",
                    "intro_vi": intro_vi,
                    "actors": info.get("actors") or [],
                    "roles": info.get("roles") or [],
                })
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path in ("/canva/status", "/api/canva/status"):
            import agents.canva_client as canva_client
            cfg = canva_client.load_config()
            self._json(200, {
                "ok": True,
                "configured": bool(cfg.get("client_id") and cfg.get("client_secret")),
                "connected": canva_client.is_connected(),
                "client_id": cfg.get("client_id", "")[:6] + "..." if cfg.get("client_id") else "",
            })
            return
        if path in ("/canva/auth-url", "/api/canva/auth-url"):
            import agents.canva_client as canva_client
            import importlib
            canva_client = importlib.reload(canva_client)
            try:
                url, state = canva_client.generate_auth_url()
                self._json(200, {"ok": True, "url": url, "state": state})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
            return
        if path in ("/canva/callback", "/api/canva/callback"):
            from urllib.parse import parse_qs, urlsplit
            qs = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            code = qs.get("code") or ""
            state = qs.get("state") or ""
            err = qs.get("error") or ""
            if err:
                msg = f"Canva từ chối kết nối: {err}"
            elif not code:
                msg = "Thiếu authorization code từ Canva"
            else:
                try:
                    import agents.canva_client as canva_client
                    canva_client.exchange_code(code, state)
                    msg = "SUCCESS"
                except Exception as e:
                    msg = f"Lỗi đổi token: {e}"
            html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Canva Connect</title>
<style>body{{font-family:system-ui;background:#181818;color:#eee;text-align:center;padding:50px}}
.box{{background:#222;border:1px solid #333;border-radius:8px;padding:30px;max-width:460px;margin:auto}}
button{{background:#7d2ae8;color:#fff;border:none;padding:10px 20px;border-radius:6px;cursor:pointer;font-weight:600}}</style>
</head><body><div class="box">
<h2>{'🎉 Kết nối Canva thành công!' if msg == 'SUCCESS' else '❌ Lỗi kết nối'}</h2>
<p>{'Bạn có thể đóng cửa sổ này và quay lại Pineline.' if msg == 'SUCCESS' else msg}</p>
<button onclick="window.close()">Đóng cửa sổ này</button>
</div><script>if('{msg}'==='SUCCESS'){{setTimeout(()=>window.close(), 2500);}}</script></body></html>"""
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/meta/thumbnail", "/api/meta/thumbnail"):
            from urllib.parse import parse_qs, urlsplit
            qs = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            fn = qs.get("file") or ""
            p = (ROOT / "data" / "thumbnails" / fn).resolve()
            if p.is_file() and p.parent == (ROOT / "data" / "thumbnails").resolve():
                body = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(404, {"error": "not found"})
            return
        if path in ("/meta/series-info", "/api/meta/series-info"):
            import agents.meta_studio as meta_studio
            from urllib.parse import parse_qs, urlsplit
            qs = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            job_id = qs.get("job_id") or ""
            series_key, series_name = meta_studio.get_series_key(job_id)
            shared_meta = meta_studio.get_series_meta(series_key, series_name=series_name)
            shared_thumb = meta_studio.get_series_thumbnail(series_key, series_name=series_name)
            self._json(200, {
                "ok": True,
                "has_shared": bool(shared_meta and shared_thumb and shared_thumb.is_file()),
                "series_key": series_key,
                "series_name": series_name,
                "shared_meta": shared_meta,
                "thumb_url": f"/meta/thumbnail?file={shared_thumb.name}" if (shared_thumb and shared_thumb.is_file()) else None
            })
            return
        if path in ("/api/auto-publish/config", "/auto-publish/config"):
            import agents.auto_publisher as auto_publisher
            self._json(200, auto_publisher.load_config())
            return
        if path in ("/api/auto-publish/state", "/auto-publish/state"):
            import agents.auto_publisher as auto_publisher
            self._json(200, auto_publisher.get_auto_state())
            return
        if path == "/api/hg/library/watchlist":
            try:
                from urllib.parse import parse_qs, urlsplit
                qs = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                include_deleted = str(qs.get("include_deleted", "1")).lower() not in ("0", "false", "no")
                rows = film_db.list_hg_watchlist(include_deleted=include_deleted)
                self._json(200, {
                    "ok": True,
                    "n": len(rows),
                    "items": rows,
                    "on_disk": sum(1 for r in rows if int(r.get("on_disk") or 0) == 1),
                    "deleted": sum(1 for r in rows if int(r.get("on_disk") or 0) == 0),
                })
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return
        # Settings + Uploader API (Films UI)
        try:
            import agents.uploader_settings_api as _usa
            hit = _usa.dispatch("GET", path)
            if hit is not None:
                code, payload = hit
                self._json(code, payload)
                return
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": f"uploader_settings: {e}"})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        if self._blocked():
            return
        path = self.path.split("?", 1)[0]
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            body = {}

        if path == "/stop":
            set_enabled(False)
            r = hard_stop(BASE)
            tg_send("STOP ALL + Auto-pilot OFF (từ UI/API)", force=True)
            self._json(200, {"ok": True, "enabled": False, "stop": r, "text": format_status(status_brief(BASE))})
            return
        if path == "/on":
            set_enabled(True)
            r = tick_once(BASE, force=True)
            tg_send("Auto-pilot ON (từ UI)", force=True)
            self._json(200, {"ok": True, "enabled": True, "tick": r, "text": format_status(status_brief(BASE))})
            return
        if path == "/off":
            set_enabled(False)
            tg_send("Auto-pilot OFF (từ UI)", force=True)
            self._json(200, {"ok": True, "enabled": False, "text": format_status(status_brief(BASE))})
            return
        if path == "/tick":
            r = tick_once(BASE, force=True)
            self._json(200, {"ok": True, "tick": r, "text": format_status(status_brief(BASE))})
            return
        if path == "/config":
            patch = body.get("opts") if isinstance(body.get("opts"), dict) else body
            patch = {k: v for k, v in patch.items() if k in batch_config.DEFAULTS}
            try:
                r = batch_config.update(patch, by=str(body.get("by") or "ui"))
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
                return
            if r["changed"]:
                tg_send("Config batch đổi (UI): " + ", ".join(f"{k}={v}" for k, v in r["changed"].items())[:500])
            self._json(200, {"ok": True, **r, "text": batch_config.describe(r["opts"])})
            return
        if path == "/config/reset":
            self._json(200, {"ok": True, "opts": batch_config.reset(by="ui")})
            return
        if path == "/library/action":
            try:
                import importlib
                global feeder
                feeder = importlib.reload(feeder)
                r = feeder.library_action(BASE, str(body.get("action") or ""), body.get("names"), body.get("cores"))
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "error": str(e)}
            if r.get("job_ids"):
                tg_send(f"Thêm tay từ tab thư viện: {len(r['job_ids'])} job\n" + "\n".join(r.get("titles") or [])[:1500])
            self._json(200, r)
            return
        if path == "/api/hg/library/seasons/discover":
            try:
                import importlib
                import agents.hongguo_bridge as hg
                hg = importlib.reload(hg)
                try:
                    max_cores = int(body.get("max_cores") or 16)
                except (TypeError, ValueError):
                    max_cores = 16
                expand = bool(body.get("expand") or False)
                self._json(200, hg.discover_new_seasons(max_cores=max_cores, expand=expand))
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/api/hg/translate-titles":
            # Dịch thô Trung→Việt cho Films UI Mùa mới (local title_mt, cùng :8801)
            try:
                import importlib
                import engines.title_mt as tmt
                tmt = importlib.reload(tmt)
                items = list(body.get("items") or [])
                self._json(200, tmt.translate_titles(items))
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/api/hg/submit":
            try:
                import requests as _rq
                r = _rq.post(BASE + "/api/hg/submit", json=body, timeout=120)
                try:
                    data = r.json()
                except Exception:  # noqa: BLE001
                    data = {"ok": False, "error": r.text[:300]}
                self._json(r.status_code if r.status_code < 500 else 200, data)
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/library/cut-sample":
            import tools.lib_check as lib_check
            import importlib
            lib_check = importlib.reload(lib_check)
            name = str(body.get("name") or "").strip()
            title_vi = str(body.get("title_vi") or "").strip()
            start_sec = int(body.get("start_sec") or 60)
            dur_sec = int(body.get("dur_sec") or 90)
            r = lib_check.cut_sample(name, start_sec=start_sec, dur_sec=dur_sec, title_vi=title_vi)
            self._json(200 if r.get("ok") else 400, r)
            return
        if path == "/library/enqueue-test":
            import tools.lib_check as lib_check
            import importlib
            lib_check = importlib.reload(lib_check)
            name = str(body.get("name") or "").strip()
            title_vi = str(body.get("title_vi") or "").strip()
            sample_path = str(body.get("sample_path") or "").strip()
            privacy = str(body.get("privacy") or "unlisted").strip()
            r = lib_check.add_to_yt_queue(name, sample_path=sample_path, privacy=privacy, title_vi=title_vi)
            self._json(200 if r.get("ok") else 400, r)
            return
        if path == "/library/test-url":
            import tools.lib_check as lib_check
            import importlib
            lib_check = importlib.reload(lib_check)
            name = str(body.get("name") or "").strip()
            url = str(body.get("url") or body.get("video_id") or "").strip()
            r = lib_check.test_library_by_url(name, url)
            self._json(200 if r.get("ok") else 400, r)
            return
        if path == "/library/clear-samples":
            import tools.lib_check as lib_check
            import importlib
            lib_check = importlib.reload(lib_check)
            older = float(body.get("older_than_hours") or 0)
            r = lib_check.clear_samples(older_than_hours=older)
            self._json(200, r)
            return
        if path == "/library/scan-queue":
            import tools.lib_check as lib_check
            import importlib
            lib_check = importlib.reload(lib_check)
            check_live = bool(body.get("check_live", True))
            r = lib_check.scan_all_lib_from_yt_queue(check_live=check_live)
            self._json(200 if r.get("ok") else 400, r)
            return
        if path == "/library/set-block-status":
            name = str(body.get("name") or "").strip()
            status = str(body.get("status") or "unknown").strip()
            reason = str(body.get("reason") or "").strip()
            rec = film_db.upsert_library_block(name=name, status=status, reason=reason)
            self._json(200, {"ok": True, "record": rec})
            return
        if path == "/library/batch-test":
            import tools.lib_check as lib_check
            import importlib
            lib_check = importlib.reload(lib_check)
            names = body.get("names")
            limit = int(body.get("limit") or 50)
            privacy = str(body.get("privacy") or "unlisted")
            account = str(body.get("account") or "default")
            r = lib_check.batch_enqueue_untested_lib(names=names, limit=limit, privacy=privacy, account=account)
            self._json(200 if r.get("ok") else 400, r)
            return
        if path == "/test-channel/finish-auth":
            import agents.test_channel as test_channel
            code = str(body.get("code") or "").strip()
            host = self.headers.get("Host") or f"127.0.0.1:{CTRL_PORT}"
            redirect_uri = str(body.get("redirect_uri") or f"http://{host}/test-channel/callback")
            try:
                res = test_channel.finish_oauth(code, redirect_uri)
                self._json(200, res)
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/test-channel/disconnect":
            import agents.test_channel as test_channel
            ok = test_channel.disconnect()
            self._json(200, {"ok": ok})
            return
        if path == "/test-channel/test-one":
            import tools.lib_check as lib_check
            import agents.test_channel as test_channel
            import importlib
            lib_check = importlib.reload(lib_check)
            test_channel = importlib.reload(test_channel)
            name = str(body.get("name") or "").strip()
            title_vi = str(body.get("title_vi") or "").strip()
            auto_delete = bool(body.get("auto_delete", False))
            project_id = str(body.get("project_id") or "").strip() or None
            try:
                res = test_channel.run_full_test_for_season(
                    name, auto_delete=auto_delete, title_vi=title_vi, project_id=project_id
                )
                self._json(200 if res.get("ok") else 400, res)
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/test-channel/batch-start":
            import tools.lib_check as lib_check
            import agents.test_channel as test_channel
            import importlib
            lib_check = importlib.reload(lib_check)
            test_channel = importlib.reload(test_channel)
            names = body.get("names")
            title_vi_map = body.get("title_vi_map")
            limit = int(body.get("limit") or 500)
            auto_delete = bool(body.get("auto_delete", False))
            res = test_channel.start_batch_test(names=names, limit=limit, auto_delete=auto_delete, title_vi_map=title_vi_map)
            self._json(200 if res.get("ok") else 400, res)
            return
        if path == "/test-channel/batch-stop":
            # Không reload — giữ cùng BATCH_STATE với worker đang chạy
            import agents.test_channel as test_channel
            if not hasattr(test_channel, "stop_batch_test"):
                # process chưa nạp code mới → đánh dấu cancel trực tiếp nếu có
                st = getattr(test_channel, "BATCH_STATE", None)
                if isinstance(st, dict) and st.get("running"):
                    st["cancel"] = True
                    self._json(200, {"ok": True, "stopped": True, "message": "Đã gửi lệnh dừng (legacy)."})
                else:
                    self._json(200, {"ok": True, "stopped": False, "message": "Không có tiến trình / chưa nạp stop."})
                return
            self._json(200, test_channel.stop_batch_test())
            return
        if path == "/test-channel/recheck-one":
            import tools.lib_check as lib_check
            import agents.test_channel as test_channel
            import importlib
            lib_check = importlib.reload(lib_check)
            test_channel = importlib.reload(test_channel)
            name = str(body.get("name") or "").strip()
            res = test_channel.recheck_single(name)
            self._json(200 if res.get("ok") else 400, res)
            return
        if path == "/test-channel/recheck-pending":
            import agents.test_channel as test_channel
            import importlib
            test_channel = importlib.reload(test_channel)
            force = bool(body.get("force", False))
            res = test_channel.check_and_process_pending_rechecks(force=force)
            self._json(200, {"ok": True, "count": len(res), "results": res})
            return
        if path == "/cmd":
            text = str(body.get("text") or body.get("message") or "").strip()
            reply = handle_command(text)
            self._json(200, {"ok": True, "reply": reply})
            return
        if path == "/films/delete":
            job_ids = body.get("job_ids")
            if not job_ids and body.get("job_id"):
                job_ids = [body.get("job_id")]
            delete_work = bool(body.get("delete_work", False))
            if job_ids:
                try:
                    feeder._post(BASE, "/api/queue/jobs/remove", {"job_ids": job_ids}, timeout=10)
                except Exception:
                    pass
            r = film_db.delete_films(job_ids or [], delete_work=delete_work)
            self._json(200, r)
            return
        if path == "/films/purge-missing":
            only_pf = bool(body.get("only_pending_failed", False))
            r = film_db.purge_missing(only_pending_failed=only_pf)
            self._json(200, r)
            return
        if path == "/films/sync":
            r = film_db.sync()
            self._json(200, {"ok": True, "result": r, "stats": film_db.stats()})
            return
        if path == "/films/combine":
            try:
                import importlib
                feeder_mod = importlib.reload(feeder)
                job_ids = body.get("job_ids") or []
                custom_title_vi = body.get("title_vi")
                auto_publish = bool(body.get("auto_publish", False))
                allow_oversized = bool(
                    body.get("allow_oversized")
                    or body.get("allow_huge")
                    or body.get("confirm_huge")
                )
                r = feeder_mod.combine_rendered_jobs(
                    BASE,
                    job_ids=job_ids,
                    custom_title_vi=custom_title_vi,
                    auto_publish=auto_publish,
                    # explicit_order / preserve_order: client đã ↑↓ — không sort lại theo Phần
                    preserve_order=bool(
                        body.get("preserve_order")
                        or body.get("explicit_order")
                        or False
                    ),
                    allow_oversized=allow_oversized,
                )
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "error": str(e)}
            self._json(200 if r.get("ok") else 400, r)
            return
        if path == "/films/check-block":
            import engines.check_block as check_block
            import importlib
            check_block = importlib.reload(check_block)
            job_ids = body.get("job_ids")
            if not job_ids and body.get("job_id"):
                job_ids = [body.get("job_id")]
            if not job_ids:
                self._json(400, {"ok": False, "error": "Thiếu job_id"})
                return
            results = []
            for jid in job_ids:
                r = check_block.check_film_by_job(jid)
                results.append(r)
            self._json(200, {"ok": True, "results": results, "stats": film_db.stats()})
            return
        if path == "/films/check-block-all":
            import engines.check_block as check_block
            import importlib
            check_block = importlib.reload(check_block)
            rows = film_db.query(status="uploaded", limit=200)
            results = []
            for row in rows:
                if row.get("yt_url"):
                    r = check_block.check_film_by_job(row["job_id"])
                    results.append(r)
            self._json(200, {"ok": True, "count": len(results), "results": results, "stats": film_db.stats()})
            return
        if path in ("/meta/generate", "/api/meta/generate"):
            import agents.meta_studio as meta_studio
            import importlib
            meta_studio = importlib.reload(meta_studio)
            r = meta_studio.generate_metadata(
                title_vi=str(body.get("title_vi") or ""),
                title_zh=str(body.get("title_zh") or ""),
                info_text=str(body.get("info_text") or ""),
                prompt_guide=str(body.get("prompt_guide") or ""),
                tone=str(body.get("tone") or "giật gân, kịch tính"),
                job_id=str(body.get("job_id") or ""),
            )
            self._json(200 if r.get("ok") else 500, r)
            return
        if path in ("/meta/generate-title", "/api/meta/generate-title"):
            import agents.meta_studio as meta_studio
            import importlib
            meta_studio = importlib.reload(meta_studio)
            job_id = str(body.get("job_id") or "").strip()
            series_name = str(body.get("series_name") or body.get("title_vi") or body.get("title") or "").strip()
            title_zh = str(body.get("title_zh") or body.get("title") or "").strip()
            season = int(body.get("season") or 1)
            info_text = str(body.get("info_text") or "").strip()
            summary = str(body.get("summary") or "").strip()
            tone = str(body.get("tone") or "").strip()
            count = int(body.get("count") or 5)
            r = meta_studio.generate_youtube_titles(
                series_name=series_name,
                season=season,
                episode_info=info_text,
                summary=summary,
                job_id=job_id,
                tone=tone,
                count=count,
                title_zh=title_zh,
            )
            self._json(200 if r.get("ok") else 500, r)
            return
        if path in ("/meta/thumbnail", "/api/meta/thumbnail"):
            import agents.meta_studio as meta_studio
            import importlib
            meta_studio = importlib.reload(meta_studio)
            try:
                poster_src = body.get("poster_url") or body.get("poster_path")
                if not poster_src and body.get("poster_name"):
                    poster_src = _fetch_poster_bytes(str(body["poster_name"]))
                if not poster_src:
                    self._json(400, {"ok": False, "error": "Thiếu nguồn ảnh poster hoặc không tìm thấy poster"})
                    return
                bg_src = body.get("bg_frame_path")
                if not bg_src and body.get("bg_filename"):
                    bg_p = meta_studio.THUMB_DIR / str(body["bg_filename"])
                    if bg_p.is_file():
                        bg_src = bg_p
                include_p = bool(body.get("include_poster", True))
                out = meta_studio.create_thumbnail(
                    poster_source=poster_src,
                    hook_text_1=str(body.get("hook_text_1") or ""),
                    hook_text_2=str(body.get("hook_text_2") or ""),
                    badge_text=str(body.get("badge_text") or "TRỌN BỘ THUYẾT MINH"),
                    sub_text=str(body.get("sub_text") or ""),
                    bg_frame_source=bg_src,
                    style=str(body.get("style") or "fire_gold"),
                    out_filename=str(body.get("out_filename") or ""),
                    include_poster=include_p,
                )
                self._json(200, {"ok": True, "path": str(out), "filename": out.name, "url": f"/meta/thumbnail?file={out.name}"})
                return
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
        if path in ("/canva/config", "/api/canva/config"):
            import agents.canva_client as canva_client
            cid = str(body.get("client_id") or "").strip()
            csec = str(body.get("client_secret") or "").strip()
            if not cid or not csec:
                self._json(400, {"ok": False, "error": "Thiếu client_id hoặc client_secret"})
                return
            canva_client.save_config(cid, csec)
            self._json(200, {"ok": True, "configured": True})
            return
        if path in ("/canva/create", "/api/canva/create"):
            import agents.canva_client as canva_client
            import agents.meta_studio as meta_studio
            import importlib
            canva_client = importlib.reload(canva_client)
            meta_studio = importlib.reload(meta_studio)
            title = str(body.get("title") or "Thumbnail YouTube").strip()
            poster_name = body.get("poster_name") or ""
            thumb_filename = body.get("thumb_filename") or ""

            thumb_bytes = None
            # 1. Kiểm tra nếu đã có file thumbnail render sẵn
            if thumb_filename:
                thumb_path = meta_studio.THUMB_DIR / thumb_filename
                if thumb_path.is_file():
                    try:
                        thumb_bytes = thumb_path.read_bytes()
                    except Exception:
                        pass

            # 2. Lấy poster_bytes
            poster_bytes = None
            if poster_name:
                poster_bytes = _fetch_poster_bytes(poster_name)

            # 3. Nếu chưa có thumb_bytes nhưng có poster_bytes, tự động dựng thumbnail 16:9 luôn!
            if not thumb_bytes and poster_bytes:
                try:
                    h1 = str(body.get("hook_text_1") or "NGHỊCH THIÊN").strip()
                    h2 = str(body.get("hook_text_2") or "ĐỒ THẦN").strip()
                    b_badge = str(body.get("badge_text") or "TRỌN BỘ THUYẾT MINH").strip()
                    s_sub = str(body.get("sub_text") or title).strip()
                    st = str(body.get("style") or "fire_gold").strip()
                    out_p = meta_studio.create_thumbnail(
                        poster_source=poster_bytes,
                        hook_text_1=h1,
                        hook_text_2=h2,
                        badge_text=b_badge,
                        sub_text=s_sub,
                        style=st,
                        out_filename=thumb_filename or "thumb_auto.jpg",
                    )
                    if out_p and out_p.is_file():
                        thumb_bytes = out_p.read_bytes()
                except Exception as e_render:
                    print(f"[Canva] Auto render thumb warning: {e_render}")

            try:
                res = canva_client.create_youtube_thumbnail(
                    title=title,
                    poster_source=poster_bytes,
                    thumb_image_source=thumb_bytes,
                )
                self._json(200, res)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path in ("/api/auto-publish/config", "/auto-publish/config"):
            import agents.auto_publisher as auto_publisher
            cfg = auto_publisher.save_config(body)
            self._json(200, {"ok": True, "config": cfg})
            return
        if path in ("/api/auto-publish/trigger", "/auto-publish/trigger"):
            import agents.auto_publisher as auto_publisher
            job_id = body.get("job_id")
            if not job_id:
                self._json(400, {"ok": False, "error": "Thiếu job_id"})
                return
            privacy = body.get("privacy")
            sched = body.get("schedule_hours")
            force = bool(body.get("force", False))
            allow_oversized = bool(
                body.get("allow_oversized")
                or body.get("allow_huge")
                or body.get("confirm_huge")
            )
            busy_now, active_owner = auto_publisher.is_any_job_uploading()
            if busy_now and active_owner != job_id:
                res = auto_publisher.enqueue_publish(
                    job_id, privacy=privacy, schedule_hours=sched, force=force, allow_oversized=allow_oversized
                )
            else:
                res = auto_publisher.publish_job(
                    job_id, privacy=privacy, schedule_hours=sched, force=force, allow_oversized=allow_oversized
                )
            # 400 khi từ chối oversized (UI hiện confirm), không 500
            code = 200 if res.get("ok") else (400 if res.get("refused_oversized") else 500)
            self._json(code, res)
            return
        if path in ("/api/auto-publish/scan", "/auto-publish/scan"):
            import agents.auto_publisher as auto_publisher
            res = auto_publisher.scan_and_publish_completed_jobs()
            if isinstance(res, dict):
                self._json(200, res)
            else:
                self._json(200, {"ok": True, "scanned": res})
            return
        if path in ("/meta/ai-image", "/api/meta/ai-image"):
            import agents.meta_studio as meta_studio
            import importlib
            meta_studio = importlib.reload(meta_studio)
            prompt = str(body.get("prompt") or "").strip()
            job_id = str(body.get("job_id") or f"gen_{int(time.time())}").strip()
            hook_1 = str(body.get("hook_1") or body.get("hook_text_1") or "").strip()
            hook_2 = str(body.get("hook_2") or body.get("hook_text_2") or "").strip()
            badge = str(body.get("badge") or body.get("badge_text") or "TRỌN BỘ THUYẾT MINH").strip()
            sub = str(body.get("sub") or body.get("sub_text") or "").strip()
            style = str(body.get("style") or "fire_gold").strip()
            poster_name = str(body.get("poster_name") or "").strip()

            if not prompt:
                self._json(400, {"ok": False, "error": "Thiếu prompt"})
                return
            img_bytes = meta_studio.generate_ai_image(
                prompt,
                timeout=300,
                job_id=job_id,
                poster_name=poster_name,
            )
            if not img_bytes:
                self._json(500, {"ok": False, "error": "Không sinh được ảnh (timeout/lỗi GPT local :3377 hoặc Aurora)"})
                return

            # 1. Lưu bản Raw AI chất lượng gốc
            raw_ai_name = f"thumb_{job_id}_ai_raw.png"
            raw_p = meta_studio.THUMB_DIR / raw_ai_name
            raw_p.write_bytes(img_bytes)

            # 2. Xử lý chuẩn 16:9 (1280x720) cho YouTube Master Thumbnail
            # Aurora DALL-E 3 đã vẽ trực tiếp chữ thư pháp 3D lửa và hiệu ứng hoàng tráng.
            # TUYỆT ĐỐI KHÔNG dùng Pillow phủ đen 58% hay đè font văn phòng lên làm nát artwork AI.
            jpg_name = f"thumb_{job_id}.jpg"
            try:
                from PIL import Image
                import io
                im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                tw, th = 1280, 720
                ratio = im.width / max(im.height, 1)
                target_ratio = tw / th
                if ratio > target_ratio:
                    new_w = int(im.height * target_ratio)
                    im = im.crop(((im.width - new_w) // 2, 0, (im.width + new_w) // 2, im.height))
                elif ratio < target_ratio:
                    new_h = int(im.width / target_ratio)
                    im = im.crop((0, (im.height - new_h) // 2, im.width, (im.height + new_h) // 2))
                im = im.resize((tw, th), Image.Resampling.LANCZOS)
                im.save(meta_studio.THUMB_DIR / jpg_name, format="JPEG", quality=95, optimize=True)
            except Exception as e_proc:
                print(f"[meta] Lỗi resize ảnh AI: {e_proc}, ghi trực tiếp bytes", flush=True)
                (meta_studio.THUMB_DIR / jpg_name).write_bytes(img_bytes)

            self._json(200, {
                "ok": True,
                "filename": jpg_name,
                "raw_ai_filename": raw_ai_name,
                "url": f"/meta/thumbnail?file={jpg_name}",
                "raw_url": f"/meta/thumbnail?file={raw_ai_name}",
            })
            return
        # Merge parts (job đã làm / split)
        try:
            import agents.films_merge_api as _fma
            hit = _fma.dispatch("POST", path, body if isinstance(body, dict) else {})
            if hit is not None:
                code, payload = hit
                self._json(code, payload)
                return
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": f"films_merge: {e}"})
            return
        # Settings + Uploader API (Films UI)
        try:
            import agents.uploader_settings_api as _usa
            hit = _usa.dispatch("POST", path, body if isinstance(body, dict) else {})
            if hit is not None:
                code, payload = hit
                self._json(code, payload)
                return
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": f"uploader_settings: {e}"})
            return
        self._json(404, {"ok": False, "error": "not found"})


def autopilot_loop(stop: threading.Event) -> None:
    from services.autopilot import autopilot_loop as _loop
    _loop(stop, base=BASE, notify=lambda t: tg_send(t, force=False), tick_sec=TICK_SEC)


FILMDB_SEC = max(30, int(os.getenv("FILMDB_SEC") or "120"))


def filmdb_loop(stop: threading.Event) -> None:
    from services.film_db_sync import loop as _loop
    _loop(stop, base=BASE, sec=FILMDB_SEC)


FILMS_UI = """<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Phim đã làm</title>
<style>
body{font:13px system-ui,sans-serif;margin:16px;background:#111;color:#ddd}
h2{margin:0 0 8px}#st{color:#9ab;margin-bottom:10px;white-space:pre-line}
input,select,button{background:#222;color:#ddd;border:1px solid #444;padding:5px 8px;border-radius:4px}
table{border-collapse:collapse;width:100%;margin-top:10px}th,td{border-bottom:1px solid #2a2a2a;padding:5px 6px;text-align:left;vertical-align:top}
th{position:sticky;top:0;background:#1b1b1b;cursor:pointer}tr:hover{background:#1a1f26}
.done{color:#6c6}.failed{color:#e66}.running{color:#6af}.pending{color:#aaa}a{color:#7bf}.gone{opacity:.55}
small{color:#888}
</style></head><body>
<h2>Database phim đã làm</h2><div id="st">…</div>
<input id="f-q" placeholder="Tìm tên / bộ / job_id" size="30">
<select id="f-status"><option value="">Mọi trạng thái</option><option>done</option><option>running</option>
<option>pending</option><option>failed</option><option value="uploaded">đã upload</option>
<option value="not_uploaded">xong, chưa upload</option></select>
<select id="f-order"><option value="recent">Mới nhất</option><option value="series">Theo bộ / mùa</option></select>
<button onclick="load()">Lọc</button> <a id="f-csv" href="/films.csv">Tải CSV</a>
<table><thead><tr><th>Trạng thái</th><th>Tên Việt / gốc</th><th>Bộ · mùa</th><th>Dài</th><th>Tạo</th><th>Xong</th>
<th>YouTube</th><th>Job</th></tr></thead><tbody id="f-tb"></tbody></table>
<script>
const $=id=>document.getElementById(id);
const q=$('f-q'),fstatus=$('f-status'),order=$('f-order'),csv=$('f-csv'),tb=$('f-tb'),st=$('st');
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const dur=s=>{if(!s)return'';const m=Math.round(s/60);return m>=60?Math.floor(m/60)+'h'+String(m%60).padStart(2,'0'):m+'m'};
async function load(){
  const p=new URLSearchParams({q:q.value,status:fstatus.value,order:order.value});
  csv.href='/films.csv?'+p;
  let d;
  try{ d=await (await fetch('/films?'+p)).json(); }
  catch(e){ st.textContent='Không đọc được dữ liệu: '+e; return; }
  const s=d.stats,b=s.by_status||{};
  st.textContent=`${s.total} job · ${s.n_series} bộ · xong ${b.done||0} (${s.done_hours} giờ phim) · chạy ${b.running||0} · chờ ${b.pending||0} · lỗi ${b.failed||0} · đã upload ${s.uploaded} · 7 ngày qua xong ${s.done_7d}`;
  tb.innerHTML=d.films.map(f=>`<tr class="${f.work_exists?'':'gone'}"><td class="${f.status}">${f.status}<br><small>${esc(f.stage)}</small></td>
  <td>${esc(f.title_vi)}<br><small>${esc(f.title)}</small></td><td>${esc(f.series)}${f.season?' · S'+f.season:''}</td>
  <td>${dur(f.duration_sec)}</td><td>${esc((f.created_at||'').slice(0,16).replace('T',' '))}</td>
  <td>${esc((f.finished_at||'').slice(0,16).replace('T',' '))}</td>
  <td>${f.yt_url?`<a href="${esc(f.yt_url)}" target="_blank">${esc(f.yt_url.replace('https://',''))}</a><br><small>${esc(f.yt_channel)}</small>`:''}</td>
  <td><small>${esc(f.job_id)}${f.work_exists?'':'<br>(đã xoá thư mục)'}</small></td></tr>`).join('')
    || '<tr><td colspan="8">Không có phim nào khớp bộ lọc.</td></tr>';
}
q.onkeydown=e=>{if(e.key==='Enter')load()};fstatus.onchange=load;order.onchange=load;load();
</script></body></html>"""

RUNWATCH_SEC = max(30, int(os.getenv("RUNWATCH_SEC") or "90"))


def runwatch_loop(stop: threading.Event) -> None:
    """Luôn theo dõi job chạy + báo xong/treo; chỉ tự xử lý treo khi Auto-pilot ON."""
    svc_feeder.runwatch_loop(stop, base=BASE, notify=lambda t: tg_send(t, force=True), tick_sec=RUNWATCH_SEC)


def feeder_loop(stop: threading.Event) -> None:
    svc_feeder.feeder_loop(stop, base=BASE, notify=lambda t: tg_send(t, force=True), tick_sec=TICK_SEC)


LIVE_CONFIG = (os.getenv("QUEUE_LIVE_CONFIG") or "1").strip() not in ("0", "false", "no")


def _sync_and_report(reason: str) -> dict:
    from services.autopilot import sync_and_report
    return sync_and_report(reason, base=BASE, notify=lambda t: tg_send(t, force=True))


def config_watch_loop(stop: threading.Event) -> None:
    if not LIVE_CONFIG:
        print("[config-live] tắt (QUEUE_LIVE_CONFIG=0)", flush=True)
        return
    from services.autopilot import config_watch_loop as _loop
    _loop(stop, base=BASE, notify=lambda t: tg_send(t, force=True))


def _inline_kb(rows: list) -> str:
    return json.dumps({"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]},
                      ensure_ascii=False)


def _on_callback(cq: dict, allow: set[str]) -> None:
    uid = str((cq.get("from") or {}).get("id") or "")
    data = str(cq.get("data") or "")
    if uid not in allow:
        tg_api("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": "Không có quyền"})
        return
    print(f"[tele] ⎆ {uid}: {data}", flush=True)
    tg_api("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": "Đang xử lý…"})
    try:
        if data.startswith("agc:"):
            threading.Thread(target=_confirm_callback, args=(uid, cq, data), name="agent-confirm",
                             daemon=True).start()
            return
        if data.startswith("lib:"):
            reply = feeder.lib_callback(BASE, data)
            tg_api("sendMessage", {"chat_id": uid, "text": reply[:3500], "disable_web_page_preview": "1"})
        elif data == "hg:noop":
            tg_api("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": "Trang hiện tại"})
            return
        elif data.startswith("hg:page:"):
            page_str = data.split(":")[2]
            page_num = int(page_str) if page_str.isdigit() else 1
            reply, buttons = hg_agent.render_rank_page(page=page_num, per_page=10)
            msg_obj = cq.get("message") or {}
            mid = msg_obj.get("message_id")
            if mid:
                tg_api("editMessageText", {
                    "chat_id": uid,
                    "message_id": str(mid),
                    "text": reply[:3500],
                    "reply_markup": _inline_kb(buttons),
                    "disable_web_page_preview": "1",
                })
            else:
                tg_api("sendMessage", {
                    "chat_id": uid,
                    "text": reply[:3500],
                    "reply_markup": _inline_kb(buttons),
                    "disable_web_page_preview": "1",
                })
            return
        elif data.startswith(("hgl:", "hgc:")):
            reply, buttons = hg_agent.view_callback(data)
            _edit_or_send(uid, cq, reply, buttons)
            return
        elif data.startswith("hgv:covers:"):
            n = data[len("hgv:covers:"):]
            threading.Thread(target=send_covers, args=(uid, int(n) if n.isdigit() else 1),
                             name="hg-covers", daemon=True).start()
        elif data.startswith("hg:pv:"):
            _, _, sid, ep_s = (data.split(":") + ["1"])[:4]
            spawn_preview(uid, sid, int(ep_s) if ep_s.isdigit() else 1)
        elif data.startswith("hg:series:"):
            reply, buttons = hg_tele_command(f"/hg series {data[len('hg:series:'):]}", uid=uid)
            payload = {"chat_id": uid, "text": reply[:3500], "disable_web_page_preview": "1"}
            if buttons:
                payload["reply_markup"] = _inline_kb(buttons)
            tg_api("sendMessage", payload)
        elif data.startswith("hg:auto:"):
            tg_api("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": "Đã nhận lệnh làm A-Z!"})
            param = data[len("hg:auto:"):].strip()
            def _send_tg(t: str):
                tg_api("sendMessage", {"chat_id": uid, "text": t[:3500], "disable_web_page_preview": "1"})
            _send_tg(f"⏳ Đã nhận lệnh làm phim #{param} trọn gói. Đang bắt đầu quy trình A-Z (Tải -> Gộp 1080p -> Xóa rác -> Tạo Job -> Xếp hàng chạy)...")
            res = json.loads(hg_agent.start_auto_flow_bg(None, {"query_or_id": param, "auto_run": True},
                                                         notify=_send_tg))
            if res.get("already_running"):
                _send_tg(f"⏸ '{param}' đang chạy Auto Flow ({res.get('running_min')} phút) — không chạy thêm.")
            elif not res.get("ok"):
                _send_tg(f"❌ Auto Flow: {res.get('message') or res.get('error')}")
        elif data.startswith("hg:series_all:"):
            tg_api("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": "Đã nhận lệnh làm trọn bộ các phần!"})
            sids_str = data[len("hg:series_all:"):].strip()
            sids = [x.strip() for x in sids_str.split(",") if x.strip()]
            def _send_tg(t: str):
                tg_api("sendMessage", {"chat_id": uid, "text": t[:3500], "disable_web_page_preview": "1"})
            _send_tg(f"⏳ Đã xếp lệnh làm trọn bộ {len(sids)} phần. Hệ thống sẽ tự động tải, gộp và xếp hàng chạy tuần tự từng phần...")
            def _run_all_series():
                for idx, sid in enumerate(sids, 1):
                    try:
                        _send_tg(f"🎬 Bắt đầu quy trình A-Z cho Phần {idx}/{len(sids)} (ID: {sid})...")
                        res_raw = hg_agent.tool_hg_auto_flow(sid, auto_run=True, notify_cb=_send_tg, mode="name")
                        res = json.loads(res_raw)
                        if not res.get("ok"):
                            _send_tg(f"⚠️ Phần {idx} (ID: {sid}) lỗi: {res.get('message') or res.get('error')}")
                    except Exception as e:
                        _send_tg(f"❌ Lỗi chạy Phần {idx} (ID: {sid}): {e}")
            threading.Thread(target=_run_all_series, name="hg-series-all", daemon=True).start()
        else:
            tg_api("sendMessage", {"chat_id": uid, "text": "Nút không hỗ trợ", "disable_web_page_preview": "1"})
    except Exception as e:  # noqa: BLE001
        tg_api("sendMessage", {"chat_id": uid, "text": f"Lỗi: {e}", "disable_web_page_preview": "1"})


def telegram_loop(stop: threading.Event) -> None:
    if not _telegram_allowed():
        print("[tele] Telegram OFF (instance telegram=false) — chỉ HTTP", flush=True)
        return
    token, uids = telegram_config()
    if not token:
        print(f"[tele] KHÔNG có TELEGRAM_BOT_TOKEN — chỉ dùng HTTP :{CTRL_PORT}", flush=True)
        return
    if not uids:
        print("[tele] TELEGRAM_ALLOWED_USERS trống — bỏ poll", flush=True)
        return
    allow = set(uids)
    offset = 0
    threading.Thread(target=_tg_worker, args=(stop,), name="tele-worker", daemon=True).start()
    print(f"[tele] polling bot · allow={allow}", flush=True)
    # bỏ updates cũ
    boot = tg_api("getUpdates", {"timeout": "0", "offset": "-1"})
    if boot.get("ok") and boot.get("result"):
        offset = int(boot["result"][-1]["update_id"]) + 1
    while not stop.is_set():
        try:
            data = tg_api(
                "getUpdates",
                {"timeout": "25", "offset": str(offset),
                 "allowed_updates": json.dumps(["message", "callback_query"])},
            )
            if not data.get("ok"):
                time.sleep(POLL_SEC)
                continue
            for upd in data.get("result") or []:
                offset = max(offset, int(upd["update_id"]) + 1)
                if upd.get("callback_query"):
                    _on_callback(upd["callback_query"], allow)
                    continue
                msg = upd.get("message") or {}
                chat = msg.get("chat") or {}
                frm = msg.get("from") or {}
                uid = str(frm.get("id") or chat.get("id") or "")
                text = (msg.get("text") or "").strip()
                if not text or uid not in allow:
                    continue
                print(f"[tele] ← {uid}: {text[:80]}", flush=True)
                if is_priority_cmd(text):
                    _handle_tg_text(uid, text)
                else:
                    _TG_WORK.put((uid, text))
        except Exception as e:  # noqa: BLE001
            print(f"[tele] poll lỗi: {e}", flush=True)
            time.sleep(3)


_PRIORITY_CMDS = frozenset({"/stop", "/stopqueue", "/off", "/status", "/running", "/jobs", "/help"})
_TG_WORK: "queue.Queue[tuple[str, str]]" = queue.Queue()


def is_priority_cmd(text: str) -> bool:
    """Lệnh chạy ngay trên luồng poll, không xếp sau lượt agent đang nghĩ (tới 180s)."""
    t = (text or "").strip().lower()
    return t.split(maxsplit=1)[0].split("@", 1)[0] in _PRIORITY_CMDS if t.startswith("/") else False


def _tg_worker(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            uid, text = _TG_WORK.get(timeout=1)
        except queue.Empty:
            continue
        try:
            _handle_tg_text(uid, text)
        except Exception as e:  # noqa: BLE001
            print(f"[tele] worker lỗi: {e}", flush=True)


def _handle_tg_text(uid: str, text: str) -> None:
    buttons: list = []
    try:
        kind, hint = _slash_hint(text)
        if kind:
            reply = hint if kind == "hint" else _agent_reply(text, uid)
        elif re.match(r"^/(lib|thuvien|library)(@\S+)?(\s|$)", text, re.I):
            reply, buttons = feeder.lib_command(BASE, text.split(maxsplit=1)[1] if " " in text else "")
            add_user_history(uid, "user", text)
            add_user_history(uid, "assistant", reply)
        elif re.match(r"^/(hg|hongguo)(@\S+)?(\s|$)", text, re.I) or re.match(r"^(hg_|hg\s)", text, re.I):
            hg_in = text if text.startswith("/") else "/hg " + (text[3:] if text.lower().startswith("hg ") else text)
            reply, buttons = hg_tele_command(hg_in, uid=uid)
            add_user_history(uid, "user", text)
            add_user_history(uid, "assistant", reply)
        else:
            reply = handle_command(text, uid=uid)
    except Exception as e:  # noqa: BLE001
        reply = f"Lỗi: {e}"
    payload = {"chat_id": uid, "text": reply[:3500], "disable_web_page_preview": "1"}
    if buttons:
        payload["reply_markup"] = _inline_kb(buttons)
    tg_api("sendMessage", payload)


class _ExclusiveHTTPServer(ThreadingHTTPServer):
    """Windows: SO_REUSEADDR (mặc định của HTTPServer) cho bản thứ 2 bind trùng cổng."""
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _single_instance_lock():
    """Khoá 1 bản / cổng; trả file handle (giữ sống tới khi thoát) hoặc None nếu đã có bản khác."""
    try:
        import msvcrt
    except ImportError:
        return True
    fh = open(Path(__file__).resolve().parents[1] / f".sidecar_{CTRL_PORT}.lock", "a+")
    fh.seek(0)
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        return None
    return fh


def _apply_instance(argv: list[str]) -> str | None:
    """V6 F4: `--instance <tên>` (hoặc env PINELINE_INSTANCE khi chưa có PINELINE_FLAG_*) → apply_env
    rồi cập nhật BASE/CTRL_PORT. Trả tên instance hoặc None (kiểu V5)."""
    global BASE, CTRL_PORT
    name = None
    if "--instance" in argv:
        i = argv.index("--instance")
        name = argv[i + 1] if i + 1 < len(argv) else None
    if not name and os.getenv("PINELINE_FLAG_TELEGRAM") is None:
        # review2: chạy trần `python agents/agent_tele_cmd.py` trong cây V6 (không --instance, không env
        # instance) trước đây = kiểu V5: poll Telegram CÙNG bot với sidecar V2 :8802 (getUpdates tranh lệnh)
        # + tự chạy vòng nền. Cây V6 luôn chạy theo instance: mặc định PINELINE_INSTANCE hoặc v6.
        from core.instance import DEFAULT_INSTANCE as _DEF_INST
        name = os.getenv("PINELINE_INSTANCE") or _DEF_INST
        print(f"[sidecar] không có --instance → dùng instance '{name}'", flush=True)
    if name:
        from core.instance import load as _load_instance
        _load_instance(name).apply_env()
    BASE = _server_base()
    CTRL_PORT = _sidecar_port()
    return name or os.getenv("PINELINE_INSTANCE")


def sidecar_owns_background() -> bool:
    """Sidecar còn tự chạy vòng nền (autopilot/feeder/runwatch/film_db/config-live/auto-publish)?

    V6 F4: chạy theo instance (có PINELINE_FLAG_FEEDER) → KHÔNG — server sở hữu (services/, cờ feeder).
    Kiểu V5 (không env instance) → có, như cũ."""
    return os.getenv("PINELINE_FLAG_FEEDER") is None


def main() -> None:
    _apply_instance(sys.argv[1:])
    lock = _single_instance_lock()
    if lock is None:
        print(f"[autopilot] đã có sidecar khác giữ cổng {CTRL_PORT} — thoát.", flush=True)
        raise SystemExit(1)
    httpd = None
    for i in range(15):
        try:
            httpd = _ExclusiveHTTPServer((CTRL_HOST, CTRL_PORT), CtrlHandler)
            break
        except OSError as e:
            if i == 14:
                print(f"[autopilot] không bind được {CTRL_HOST}:{CTRL_PORT}: {e} — thoát.", flush=True)
                raise SystemExit(1)
            time.sleep(1)
    _ensure_agent_telegram()
    stop = threading.Event()
    threading.Thread(target=telegram_loop, args=(stop,), name="telegram", daemon=True).start()
    if sidecar_owns_background():
        threading.Thread(target=autopilot_loop, args=(stop,), name="autopilot", daemon=True).start()
        threading.Thread(target=config_watch_loop, args=(stop,), name="config-live", daemon=True).start()
        threading.Thread(target=runwatch_loop, args=(stop,), name="runwatch", daemon=True).start()
        threading.Thread(target=feeder_loop, args=(stop,), name="feeder", daemon=True).start()
        threading.Thread(target=filmdb_loop, args=(stop,), name="film-db", daemon=True).start()
        try:
            import agents.auto_publisher as auto_publisher
            auto_publisher.start_worker()
        except Exception as e_start_pub:
            print(f"[auto-publish] Không khởi động được worker: {e_start_pub}", flush=True)
    else:
        print("[sidecar] theo instance: không chạy vòng nền (server sở hữu — cờ feeder)", flush=True)

    print(f"[autopilot] HTTP http://{CTRL_HOST}:{CTRL_PORT}  ·  Pineline {BASE}", flush=True)
    # Giữ state đã lưu — KHÔNG tự bật ON (tránh xếp lại job sau khi user STOP)
    st = load_state()
    brief = status_brief(BASE)
    msg = (
        f"Pineline sidecar ready · Auto-pilot={'ON' if st.get('enabled') else 'OFF'}\n"
        + format_status(brief)
        + "\nGõ /help · /stop = dừng hết + tắt autopilot"
    )
    print(msg, flush=True)
    tg_send(msg, force=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        stop.set()
        httpd.shutdown()


if __name__ == "__main__":
    main()
