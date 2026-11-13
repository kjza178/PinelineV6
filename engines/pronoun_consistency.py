"""
Đồng nhất xưng hô không cần diarization (A–C + vá câu đỏ).

A) analyze_full_zh: quét TOÀN BỘ ZH theo lô + memory → glossary.pronouns + lock
B) inject lock/ledger vào mỗi lô dịch
C) hard-lock post-pass + QA nhảy ngôi (anh–em ↔ ông–tôi…) trong cửa sổ
D) vá câu đỏ bằng GPT (tùy chọn, tái dùng agent_pronoun_pass)
"""
from __future__ import annotations

from state import job_state as js  # V6 F1: mọi ghi manifest qua state
import json
import os
import re
import time
from pathlib import Path

from engines.llm import chat, parse_json, is_local, unload_local

STAGE_KEY = "pronoun_analyze"
DEFAULT_CHUNK = 220          # lô lớn hơn → ít round-trip Aurora
DEFAULT_MAX_CHUNKS = 10      # trần lô (mode fast)
DEFAULT_MIN_CHUNKS = 3       # tối thiểu trước early-stop
JUMP_WINDOW = 6

# Thanh ngữ ngôi 1 / ngôi 2 hay nhảy trong short drama đô thị
_SELF_FORMS = ("tôi", "tao", "anh", "em", "ta", "mình", "ông", "tớ")
_CALL_FORMS = ("anh", "em", "cậu", "mày", "ngươi", "chị", "bạn", "ông", "bà", "cô")


def _cfg(key: str, default: str | None = None) -> str | None:
    try:
        from core.config import get as _g
        return _g(key, default)
    except Exception:
        return os.getenv(key) or default


def analyze_enabled() -> bool:
    v = (_cfg("PRONOUN_ANALYZE", "1") or "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def analyze_model() -> str:
    m = (_cfg("PRONOUN_ANALYZE_MODEL") or "").strip()
    if m:
        return m
    try:
        from core.config import pass_llm_model
        return pass_llm_model("pronoun") or "chatgpt/gpt-5-6-mini"
    except Exception:
        return "chatgpt/gpt-5-6-mini"


def chunk_size() -> int:
    try:
        return max(40, int(_cfg("PRONOUN_ANALYZE_CHUNK", str(DEFAULT_CHUNK)) or DEFAULT_CHUNK))
    except ValueError:
        return DEFAULT_CHUNK


def analyze_mode() -> str:
    """file = upload SRT ZH 1 lần (Aurora /v1/files). fast = mẫu+lô. full = quét hết."""
    return (_cfg("PRONOUN_ANALYZE_MODE", "file") or "file").strip().lower()


def max_chunks() -> int:
    try:
        return max(3, int(_cfg("PRONOUN_ANALYZE_MAX_CHUNKS", str(DEFAULT_MAX_CHUNKS)) or DEFAULT_MAX_CHUNKS))
    except ValueError:
        return DEFAULT_MAX_CHUNKS


def min_chunks_before_stop() -> int:
    try:
        return max(2, int(_cfg("PRONOUN_ANALYZE_MIN_CHUNKS", str(DEFAULT_MIN_CHUNKS)) or DEFAULT_MIN_CHUNKS))
    except ValueError:
        return DEFAULT_MIN_CHUNKS


def early_stop_enabled() -> bool:
    v = (_cfg("PRONOUN_ANALYZE_EARLY_STOP", "1") or "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def is_pronouns_locked(plan: dict | None) -> bool:
    if not isinstance(plan, dict):
        return False
    lock = plan.get("style_lock") if isinstance(plan.get("style_lock"), dict) else {}
    if lock.get("pronouns_locked") or lock.get("pronoun_lock"):
        return True
    return bool(plan.get("pronouns_locked"))


def mark_locked(plan: dict, source: str = "analyze_full_zh") -> dict:
    plan = dict(plan or {})
    lock = dict(plan.get("style_lock") or {}) if isinstance(plan.get("style_lock"), dict) else {}
    lock["pronouns_locked"] = True
    lock["pronoun_lock_source"] = source
    lock["pronoun_lock_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # default đô thị nếu chưa có mode
    if not lock.get("mode"):
        blob = " ".join(str(plan.get(k) or "") for k in ("genre", "tone", "content_type")).lower()
        if any(k in blob for k in ("đô thị", "do thi", "hiện đại", "urban", "modern", "mixed", "dị năng")):
            lock["mode"] = "mixed"
    plan["style_lock"] = lock
    plan["pronouns_locked"] = True
    return plan


def export_zh_lines(segments_or_rows: list[dict]) -> list[dict]:
    """Chuẩn hoá [{i, zh}] từ asr segments hoặc script rows."""
    out = []
    for idx, s in enumerate(segments_or_rows or []):
        if not isinstance(s, dict):
            continue
        zh = (s.get("zh") or s.get("text") or "").strip()
        if not zh:
            continue
        i = s.get("i", idx)
        try:
            i = int(i)
        except (TypeError, ValueError):
            i = idx
        out.append({"i": i, "zh": zh})
    return out


def write_zh_export(work_dir: Path, lines: list[dict]) -> Path:
    work_dir = Path(work_dir)
    p = work_dir / "zh_full_for_pronoun.txt"
    body = "\n".join(f"{x['i']}|{x['zh']}" for x in lines)
    p.write_text(body, encoding="utf-8")
    # plain list (upload / debug)
    (work_dir / "sub.zh.analyze.txt").write_text(body, encoding="utf-8")
    return p


def write_zh_srt(
    work_dir: Path,
    lines: list[dict],
    segments: list[dict] | None = None,
    *,
    annotate_i: bool = False,
) -> Path:
    """Xuất sub.zh.srt để Aurora upload (API.md: POST /v1/files).

    annotate_i=True → tiền tố `[i=N]` trong text (plan glossary dùng range theo i thật).
    """
    work_dir = Path(work_dir)
    by_i = {}
    for s in segments or []:
        if isinstance(s, dict) and "i" in s:
            try:
                by_i[int(s["i"])] = s
            except (TypeError, ValueError):
                pass
    blocks = []
    for n, x in enumerate(lines, 1):
        try:
            i = int(x["i"])
        except (TypeError, ValueError, KeyError):
            continue
        seg = by_i.get(i) or {}
        try:
            st = float(seg.get("start") or (n - 1) * 2.0)
            en = float(seg.get("end") or st + 1.8)
        except (TypeError, ValueError):
            st, en = (n - 1) * 2.0, (n - 1) * 2.0 + 1.8

        def ts(sec: float) -> str:
            if sec < 0:
                sec = 0.0
            h = int(sec // 3600)
            m = int((sec % 3600) // 60)
            s = int(sec % 60)
            ms = int(round((sec - int(sec)) * 1000))
            if ms >= 1000:
                s += 1
                ms = 0
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        zh = (x.get("zh") or "").replace("\n", " ").strip()
        if annotate_i:
            zh = f"[i={i}] {zh}"
        blocks.append(f"{n}\n{ts(st)} --> {ts(en)}\n{zh}\n")
    srt_path = work_dir / "sub.zh.srt"
    srt_path.write_text("\n".join(blocks), encoding="utf-8")
    return srt_path


def _apply_analyze_delta_to_plan(plan: dict, data: dict, source: str) -> dict:
    chars = _merge_chars(plan.get("characters") or [], data.get("characters") or data.get("characters_delta") or [])
    prs = _merge_pronouns(plan.get("pronouns") or [], data.get("pronouns") or data.get("pronouns_delta") or [])
    plan["characters"] = chars
    plan["pronouns"] = prs
    if data.get("pronoun_notes"):
        plan["pronoun_notes"] = data["pronoun_notes"]
    if data.get("genre") and not plan.get("genre"):
        plan["genre"] = data["genre"]
    if data.get("tone") and not plan.get("tone"):
        plan["tone"] = data["tone"]
    plan = mark_locked(plan, source=source)
    lock = plan["style_lock"]
    if data.get("default_register"):
        lock["default_register"] = data["default_register"]
    plan["style_lock"] = lock
    return plan


def analyze_via_srt_file(
    work_dir: Path,
    plan: dict,
    lines: list[dict],
    segments: list[dict] | None = None,
    model: str | None = None,
) -> dict:
    """Upload sub.zh.srt → GPT đọc 1 lần → glossary pronouns (Aurora file QA)."""
    from engines.llm import aurora_upload_file, chatgpt_chat_with_file, parse_json

    srt_path = write_zh_srt(work_dir, lines, segments=segments)
    print(f"[pronoun-analyze] upload SRT {srt_path.name} ({len(lines)} câu)…", flush=True)
    up = aurora_upload_file(srt_path, purpose="assistants")
    file_id = up["id"]
    genre_hint = " / ".join(
        str(plan.get(k) or "") for k in ("genre", "tone", "content_type") if plan.get(k)
    ) or "unknown"
    seed = {
        "characters": [
            {"zh": c.get("zh"), "vi": c.get("vi"), "gender": c.get("gender"), "role": c.get("role")}
            for c in (plan.get("characters") or [])[:30]
            if isinstance(c, dict)
        ],
        "pronouns": [
            {"who": p.get("who"), "to": p.get("to"), "self": p.get("self"), "call": p.get("call")}
            for p in (plan.get("pronouns") or [])[:30]
            if isinstance(p, dict)
        ],
    }
    prompt = (
        "Bạn là chuyên gia xưng hô tiếng Việt cho phim Trung (short drama / đô thị / tu tiên).\n"
        f"File đính kèm là phụ đề ZH (SRT), genre_hint={genre_hint}.\n"
        "ĐỌC TOÀN BỘ file, lập HỒ SƠ XƯNG HÔ BẤT BIẾN cho cả phim.\n"
        "Đô thị/mixed: CẤM mặc định Ta/Ngươi.\n"
        "Cùng một quan hệ A↔B chỉ 1 cặp self/call ổn định xuyên phim.\n"
        f"Seed (có thể thiếu/sai, hãy bổ sung/sửa):\n{json.dumps(seed, ensure_ascii=False)}\n"
        "CHỈ trả JSON:\n"
        "{\n"
        '  "genre": "...",\n'
        '  "tone": "...",\n'
        '  "default_register": "anh-em|toi-cau|toi-anh|ancient-ta-nguoi|mixed",\n'
        '  "pronoun_notes": "1-3 câu",\n'
        '  "characters": [{"zh":"","vi":"","gender":"","role":""}],\n'
        '  "pronouns": [{"who":"tên VI","to":"tên VI|chung","self":"anh","call":"em","note":""}]\n'
        "}\n"
    )
    use_model = model or analyze_model()
    out = chatgpt_chat_with_file(
        file_id,
        prompt,
        model=use_model,
        temperature=0.1,
        max_tokens=8192,
        json_mode=True,
        timeout=420,
        base=up.get("base"),
        account_id=up.get("account_id") or None,
    )
    data = parse_json(out) if out else {}
    if isinstance(data, dict) and data.get("error") and not (data.get("pronouns") or data.get("pronouns_delta")):
        raise RuntimeError(f"file-analyze: GPT không đọc file — {data.get('error')}")
    if not data or not (data.get("pronouns") or data.get("pronouns_delta")):
        raise RuntimeError("file-analyze: JSON rỗng / thiếu pronouns")
    plan2 = _apply_analyze_delta_to_plan(dict(plan), data, source=f"srt_upload:{use_model}")
    (work_dir / "glossary.json").write_text(
        json.dumps(plan2, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    meta = {
        "ok": True,
        "mode": "file",
        "file_id": file_id,
        "srt": str(srt_path),
        "n_lines": len(lines),
        "n_pronouns": len(plan2.get("pronouns") or []),
        "n_characters": len(plan2.get("characters") or []),
        "model": use_model,
        "locked": True,
    }
    (work_dir / "pronoun_lock.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[pronoun-analyze] FILE LOCK · {meta['n_pronouns']} cặp · {meta['n_characters']} NV",
        flush=True,
    )
    return meta


def select_analyze_lines(lines: list[dict], mode: str | None = None) -> list[dict]:
    """fast: phủ đầu/giữa/cuối + câu có hô ngữ — ít lô hơn full scan."""
    mode = (mode or analyze_mode()).lower()
    if mode in ("full", "all", "slow") or len(lines) <= chunk_size() * 2:
        return lines
    n = len(lines)
    cs = chunk_size()
    # ngân sách ~ max_chunks * chunk (có overlapping nhẹ)
    budget = max_chunks() * cs
    if n <= budget:
        return lines

    picked: dict[int, dict] = {}

    def add_slice(start: int, end: int) -> None:
        for x in lines[max(0, start) : min(n, end)]:
            picked[int(x["i"])] = x

    # đầu / 3 điểm giữa / cuối
    add_slice(0, cs)
    for frac in (0.25, 0.5, 0.75):
        mid = int(n * frac)
        add_slice(mid - cs // 2, mid + cs // 2)
    add_slice(n - cs, n)

    # ưu tiên câu có tín hiệu xưng hô ZH
    kw = ("我", "你", "您", "他", "她", "哥", "姐", "弟", "妹", "爸", "妈", "师父", "公子", "姑娘", "大人")
    extras = [x for x in lines if any(k in x["zh"] for k in kw)]
    step = max(1, len(extras) // max(80, budget // 4))
    for x in extras[::step]:
        if len(picked) >= budget:
            break
        picked[int(x["i"])] = x

    out = [picked[k] for k in sorted(picked)]
    print(
        f"[pronoun-analyze] mode=fast: lấy {len(out)}/{n} câu (phủ đầu–giữa–cuối + hô ngữ)",
        flush=True,
    )
    return out


def _slim_memory(memory: dict) -> dict:
    """Giảm token gửi Aurora mỗi lô."""
    chars = []
    for c in (memory.get("characters") or [])[:25]:
        if isinstance(c, dict):
            chars.append({
                "zh": c.get("zh"), "vi": c.get("vi"),
                "gender": c.get("gender"), "role": c.get("role"),
            })
    prs = []
    for p in (memory.get("pronouns") or [])[:35]:
        if isinstance(p, dict):
            prs.append({
                "who": p.get("who"), "to": p.get("to"),
                "self": p.get("self"), "call": p.get("call"),
            })
    return {
        "characters": chars,
        "pronouns": prs,
        "default_register": memory.get("default_register") or "",
        "pronoun_notes": (memory.get("pronoun_notes") or "")[:240],
    }


def _merge_pronouns(base: list, incoming: list) -> list:
    out, seen = [], set()

    def key(pr: dict) -> str:
        return f"{(pr.get('who') or '').strip().lower()}->{(pr.get('to') or '').strip().lower()}"

    for src in (incoming or [], base or []):
        for pr in src:
            if not isinstance(pr, dict):
                continue
            who = (pr.get("who") or "").strip()
            if not who:
                continue
            k = key(pr)
            if k in seen:
                # incoming đã thêm trước → bỏ bản cũ
                continue
            seen.add(k)
            out.append({
                "who": who,
                "to": (pr.get("to") or "chung").strip() or "chung",
                "self": (pr.get("self") or "").strip(),
                "call": (pr.get("call") or "").strip(),
                "note": (pr.get("note") or "").strip(),
            })
    return out


def _merge_chars(base: list, incoming: list) -> list:
    out, seen = [], set()
    for src in (incoming or [], base or []):
        for c in src:
            if not isinstance(c, dict):
                continue
            zh = (c.get("zh") or "").strip()
            vi = (c.get("vi") or "").strip()
            if not zh and not vi:
                continue
            k = zh or vi
            if k in seen:
                continue
            seen.add(k)
            out.append(c)
    return out


def _analyze_chunk(
    chunk: list[dict],
    memory: dict,
    model: str,
    genre_hint: str,
) -> dict:
    sys = (
        "Bạn là chuyên gia xưng hô tiếng Việt cho phim Trung (short drama / đô thị / tu tiên). "
        "Đọc đoạn thoại ZH (có thể lỗi ASR), CẬP NHẬT bộ nhớ xưng hô. "
        "Ưu tiên ổn định: cùng quan hệ A↔B chỉ 1 cặp self/call. "
        "Đô thị/mixed: CẤM mặc định Ta/Ngươi; dùng tôi/anh/em/cậu/mày… "
        "CHỈ trả JSON."
    )
    user = {
        "genre_hint": genre_hint,
        "memory_so_far": _slim_memory(memory if isinstance(memory, dict) else {}),
        "zh_lines": chunk,
        "return_schema": {
            "characters_delta": [{"zh": "", "vi": "", "gender": "", "role": ""}],
            "pronouns_delta": [
                {"who": "tên VI", "to": "tên VI|chung", "self": "tôi", "call": "anh", "note": ""}
            ],
            "default_register": "anh-em | toi-cau | toi-anh | ancient-ta-nguoi | mixed",
            "pronoun_notes": "1-3 câu quy ước toàn phim",
            "conflicts": ["mô tả nếu memory cũ mâu thuẫn với đoạn này — chọn 1 phía"],
        },
    }
    out = chat(
        [{"role": "system", "content": sys}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
        model=model, temperature=0.1, json_mode=True, max_tokens=2500, timeout=90, retries=2,
    )
    try:
        return parse_json(out) if out else {}
    except Exception:
        return {}


def analyze_full_zh(
    work_dir: str | Path,
    plan: dict | None = None,
    segments: list[dict] | None = None,
    model: str | None = None,
    force: bool = False,
) -> dict:
    """A) Quét toàn bộ ZH theo lô + memory → cập nhật plan + lock pronouns."""
    work_dir = Path(work_dir)
    plan_path = work_dir / "glossary.json"
    if plan is None and plan_path.is_file():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan = dict(plan or {})

    if is_pronouns_locked(plan) and not force:
        return {"ok": True, "skipped": True, "reason": "already_locked", "n_pronouns": len(plan.get("pronouns") or [])}

    if segments is None:
        asr = work_dir / "asr.json"
        if asr.is_file():
            segments = json.loads(asr.read_text(encoding="utf-8"))
        elif (work_dir / "script.json").is_file():
            segments = json.loads((work_dir / "script.json").read_text(encoding="utf-8"))
        else:
            return {"ok": False, "error": "không có asr.json/script.json"}

    all_lines = export_zh_lines(segments)
    if not all_lines:
        return {"ok": False, "error": "ZH rỗng"}
    write_zh_export(work_dir, all_lines)
    mode = analyze_mode()
    use_model = model or analyze_model()

    # Ưu tiên: upload SRT 1 lần (đúng ý user / Aurora API.md)
    if mode in ("file", "upload", "srt", "oneshot", "auto"):
        try:
            return analyze_via_srt_file(
                work_dir, plan, all_lines, segments=segments, model=use_model,
            )
        except Exception as e_file:  # noqa: BLE001
            print(
                f"[pronoun-analyze] upload/file thất bại ({e_file}) → fallback mode=fast",
                flush=True,
            )
            mode = "fast"

    lines = select_analyze_lines(all_lines, mode=mode)
    genre_hint = " / ".join(
        str(plan.get(k) or "") for k in ("genre", "tone", "content_type") if plan.get(k)
    ) or "unknown"
    memory = {
        "characters": list(plan.get("characters") or [])[:40],
        "pronouns": list(plan.get("pronouns") or [])[:40],
        "default_register": (plan.get("style_lock") or {}).get("default_register")
        if isinstance(plan.get("style_lock"), dict) else "",
        "pronoun_notes": plan.get("pronoun_notes") or "",
    }

    cs = chunk_size()
    n_chunks = (len(lines) + cs - 1) // cs
    print(
        f"[pronoun-analyze] mode={mode} · {len(lines)}/{len(all_lines)} câu · "
        f"{n_chunks} lô ×{cs} · model={use_model}",
        flush=True,
    )
    conflicts: list[str] = []
    try:
        from core.manifest import Manifest
        m = Manifest(work_dir)
    except Exception:
        m = None

    stable = 0
    prev_n_pr = len(memory.get("pronouns") or [])
    early = False
    ci = -1
    for ci in range(n_chunks):
        if (work_dir / ".stop").exists():
            return {"ok": False, "error": "stopped", "partial": True}
        chunk = lines[ci * cs : (ci + 1) * cs]
        try:
            delta = _analyze_chunk(chunk, memory, use_model, genre_hint)
        except Exception as e:  # noqa: BLE001
            print(f"[pronoun-analyze] lô {ci + 1}/{n_chunks} lỗi: {e}", flush=True)
            delta = {}
        if delta:
            memory["characters"] = _merge_chars(
                memory.get("characters") or [], delta.get("characters_delta") or []
            )[:80]
            memory["pronouns"] = _merge_pronouns(
                memory.get("pronouns") or [], delta.get("pronouns_delta") or []
            )[:80]
            if delta.get("default_register"):
                memory["default_register"] = delta["default_register"]
            if delta.get("pronoun_notes"):
                memory["pronoun_notes"] = delta["pronoun_notes"]
            for c in delta.get("conflicts") or []:
                if c:
                    conflicts.append(str(c)[:200])
        n_pr = len(memory.get("pronouns") or [])
        if n_pr == prev_n_pr and n_pr >= 12:
            stable += 1
        else:
            stable = 0
        prev_n_pr = n_pr
        if m is not None:
            try:
                st = m.data.setdefault("stages", {}).setdefault("translate", {})
                st["cur_text"] = f"Analyze xưng hô ZH {ci + 1}/{n_chunks}"
                st["updated_at"] = time.time()
                js.persist(m)
            except Exception:
                pass
        print(f"  ...analyze {ci + 1}/{n_chunks} · pronouns={n_pr}", flush=True)
        # Early-stop: đủ cặp + 2 lô không thêm cặp mới
        if (
            early_stop_enabled()
            and mode not in ("full", "all", "slow")
            and (ci + 1) >= min_chunks_before_stop()
            and stable >= 2
            and n_pr >= 15
        ):
            early = True
            print(
                f"[pronoun-analyze] early-stop tại lô {ci + 1}/{n_chunks} "
                f"(pronouns ổn định={n_pr})",
                flush=True,
            )
            break

    plan["characters"] = _merge_chars(plan.get("characters") or [], memory.get("characters") or [])
    plan["pronouns"] = _merge_pronouns([], memory.get("pronouns") or [])  # memory đã merge đủ
    if memory.get("pronoun_notes"):
        plan["pronoun_notes"] = memory["pronoun_notes"]
    plan = mark_locked(plan, source=f"analyze_full_zh:{use_model}")
    lock = plan["style_lock"]
    if memory.get("default_register"):
        lock["default_register"] = memory["default_register"]
    plan["style_lock"] = lock

    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {
        "ok": True,
        "n_lines": len(all_lines),
        "n_lines_analyzed": len(lines),
        "n_chunks": n_chunks,
        "n_chunks_done": (ci + 1) if n_chunks else 0,
        "early_stop": early,
        "mode": mode,
        "n_pronouns": len(plan.get("pronouns") or []),
        "n_characters": len(plan.get("characters") or []),
        "model": use_model,
        "conflicts": conflicts[:20],
        "locked": True,
    }
    (work_dir / "pronoun_lock.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if is_local(use_model):
        unload_local(use_model)
    print(
        f"[pronoun-analyze] LOCK · {meta['n_pronouns']} cặp · {meta['n_characters']} NV",
        flush=True,
    )
    return meta


def lock_banner(plan: dict | None) -> str:
    """Block inject vào plan_block / batch khi đã lock."""
    if not is_pronouns_locked(plan):
        return ""
    pairs = []
    for pr in (plan or {}).get("pronouns") or []:
        if isinstance(pr, dict) and pr.get("who"):
            pairs.append(
                f"{pr.get('who')}→{pr.get('to') or '?'}: "
                f"xưng «{pr.get('self') or '?'}», gọi «{pr.get('call') or '?'}»"
            )
    lock = (plan or {}).get("style_lock") if isinstance((plan or {}).get("style_lock"), dict) else {}
    reg = lock.get("default_register") or ""
    lines = [
        "⚠️ XƯNG HÔ ĐÃ KHÓA (pronouns_locked=true) — CẤM đổi cặp self/call giữa các câu.",
        "CẤM nhảy anh–em ↔ ông–tôi / cậu–tôi / tôi–anh trong cùng quan hệ.",
    ]
    if reg:
        lines.append(f"Thanh ngữ mặc định toàn phim: {reg}")
    if pairs:
        lines.append("Ma trận khóa: " + "; ".join(pairs[:16]))
    if (plan or {}).get("pronoun_notes"):
        lines.append(f"Ghi chú: {plan.get('pronoun_notes')}")
    return "\n".join(lines) + "\n"


def review_lock_rules(plan: dict | None) -> str:
    """Prompt review xưng hô — linh hoạt theo cảnh (đô thị↔tu tiên)."""
    lock = (plan or {}).get("style_lock") if isinstance((plan or {}).get("style_lock"), dict) else {}
    mode = str(lock.get("mode") or "").lower()
    flex = mode in ("mixed", "", "unknown") or not is_pronouns_locked(plan)
    if flex:
        return (
            "3. XƯNG HÔ: GIỮ nguyên đại từ trong vi_nhap nếu đã khớp ZH. "
            "CẤM đổi Lão tử/ta/ngươi → tôi/cậu chỉ vì 'hay hơn'. "
            "Chỉ sửa khi đảo ngôi TRONG CÙNG câu so với ZH "
            "('Đến lượt ngươi' ≠ 'Đến lượt ta'). Không ép một thanh ngữ cả phim.\n"
        )
    return (
        "3. Xưng hô theo ma trận pronouns: ưu tiên giữ self/call đã chốt trong cùng quan hệ/"
        "cùng cảnh. Được nới khi phân cảnh chuyển genre rõ. "
        "Chỉ sửa đại từ khi vi_nhap đảo ngôi rõ so với ZH trong cùng câu.\n"
    )


def fix_preserves_locked_register(vi_nhap: str, vi_fix: str) -> bool:
    """True nếu bản sửa không đổi thanh ngữ chính so với nháp (khi đang lock)."""
    a, b = _forms_in(vi_nhap), _forms_in(vi_fix)
    if not a and not b:
        return True
    # thanh ngữ “nhạy” — nếu nháp có anh/em mà fix đưa ông/ngươi/ta đầu câu → reject
    soft = {"anh", "em", "cậu", "mày", "tôi", "tao"}
    harsh = {"ông", "ngươi", "ta"}
    if (a & soft) and (b & harsh) and not (a & harsh):
        return False
    if (a & {"ông"}) and (b & {"anh", "em"}) and "ông" not in b:
        # cho phép nếu đang sửa lệch — nhưng khi lock thường giữ ông nếu nháp ông
        return False
    # mất hết đại từ nháp có → nghi đổi mạnh
    if a and not (a & b) and len(a) >= 1 and len(b) >= 1:
        return False
    return True


def filter_review_fixes_for_lock(
    fixes: dict[int, str],
    rows: list[dict],
    plan: dict | None,
) -> dict[int, str]:
    """Lọc fix phá xưng hô — bỏ qua khi mixed/linh hoạt (đô thị↔tu tiên)."""
    if not fixes:
        return fixes
    lock = (plan or {}).get("style_lock") if isinstance((plan or {}).get("style_lock"), dict) else {}
    mode = str(lock.get("mode") or "").lower()
    # mixed / không lock cứng → cho phép đổi register theo cảnh
    if mode in ("mixed", "", "unknown") or not is_pronouns_locked(plan):
        return fixes
    by_i = {r.get("i"): r for r in rows if isinstance(r, dict) and "i" in r}
    out = {}
    dropped = 0
    for i, vi in fixes.items():
        r = by_i.get(i) or {}
        nhap = (r.get("vi") or "").strip()
        if not nhap or fix_preserves_locked_register(nhap, vi):
            out[i] = vi
        else:
            dropped += 1
    if dropped:
        print(f"[review] pronouns_locked: bỏ {dropped} fix đổi xưng hô", flush=True)
    return out


def ledger_from_prev(prev_ctx: list[dict] | None) -> str:
    """B) Ledger từ vài câu VI trước — nhắc model giữ thanh ngữ đang dùng."""
    if not prev_ctx:
        return ""
    observed = []
    for c in prev_ctx[-6:]:
        vi = (c.get("vi") or "").strip()
        if not vi:
            continue
        low = vi.lower()
        hits = []
        for form in ("anh", "em", "cậu", "mày", "tôi", "tao", "ông", "ngươi", "ta"):
            if re.search(rf"\b{re.escape(form)}\b", low):
                hits.append(form)
        if hits:
            observed.append(f"{'/'.join(hits[:3])} ← «{vi[:48]}»")
    if not observed:
        return ""
    return (
        "LEDGER XƯNG HÔ (câu liền trước — GIỮ CÙNG thanh ngữ, cấm nhảy ổng/tôi nếu đang anh/em):\n"
        + "\n".join(f"  - {x}" for x in observed)
        + "\n\n"
    )


def _forms_in(vi: str) -> set[str]:
    low = (vi or "").lower()
    found = set()
    for form in set(_SELF_FORMS + _CALL_FORMS):
        if re.search(rf"\b{re.escape(form)}\b", low):
            # bỏ «ông ta/cô ta/chúng ta»
            if form == "ta" and re.search(r"\b(ông|cô|chúng|chúng ta|bọn)\s+ta\b", low):
                continue
            found.add(form)
    return found


def find_pronoun_jumps(rows: list[dict], window: int = JUMP_WINDOW) -> list[dict]:
    """C) QA: trong cửa sổ gần nhau, vừa có {anh,em} vừa có {ông} ngôi hô / tôi lệch kiểu."""
    issues = []
    n = len(rows or [])
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict) or r.get("manual"):
            continue
        vi = (r.get("vi") or "").strip()
        if not vi:
            continue
        forms = _forms_in(vi)
        if not forms:
            continue
        # thu thập forms cửa sổ
        wforms: set[str] = set()
        for j in range(max(0, i - window), min(n, i + window + 1)):
            rr = rows[j]
            if isinstance(rr, dict):
                wforms |= _forms_in(rr.get("vi") or "")
        # xung đột thanh ngữ điển hình
        conflict = False
        reason = ""
        if {"anh", "em"} & wforms and "ông" in forms and not re.search(r"\bông\s+ta\b", vi.lower()):
            conflict = True
            reason = "cửa sổ đang anh/em nhưng câu này dùng «ông»"
        if {"anh", "em"} & wforms and {"ngươi", "ta"} & forms:
            # ta đầu câu
            if re.search(r"(?:^|[.!?…]\s*)Ta\b", vi) or "ngươi" in vi.lower():
                conflict = True
                reason = reason or "cửa sổ anh/em nhưng câu còn Ta/Ngươi"
        if "cậu" in wforms and "ông" in forms and "ông ta" not in vi.lower():
            conflict = True
            reason = reason or "cửa sổ cậu/* nhưng câu dùng ông"
        if conflict:
            issues.append({
                "i": r.get("i"),
                "zh": (r.get("zh") or "").strip(),
                "vi": vi,
                "reasons": [reason],
            })
    return issues


def ensure_analyze_before_translate(
    work_dir: str | Path,
    plan: dict,
    segments: list[dict] | None = None,
    force: bool = False,
) -> tuple[dict, dict]:
    """Gọi từ translate.run_translate sau khi có plan."""
    work_dir = Path(work_dir)
    if not analyze_enabled():
        return plan, {"ok": True, "skipped": True, "reason": "PRONOUN_ANALYZE off"}
    if is_pronouns_locked(plan) and not force:
        return plan, {"ok": True, "skipped": True, "reason": "already_locked"}
    meta = analyze_full_zh(work_dir, plan=plan, segments=segments, force=force)
    # reload plan
    gp = work_dir / "glossary.json"
    if gp.is_file():
        try:
            plan = json.loads(gp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return plan, meta


def run_post_translate_consistency(
    work_dir: str | Path,
    rows: list[dict],
    plan: dict | None,
    fix_with_gpt: bool = True,
) -> dict:
    """C+D sau dịch/polish: post-pass + QA jumps + GPT vá đỏ."""
    from engines.translate import apply_pronoun_postpass

    work_dir = Path(work_dir)
    plan = plan or {}
    n_det = apply_pronoun_postpass(rows, plan)
    jumps = find_pronoun_jumps(rows)
    n_gpt = 0
    sample = []
    if fix_with_gpt and jumps:
        try:
            from agents.agent_pronoun_pass import _fix_batch
            from engines.translate import _plan_block_for_qa, ensure_hard_terms
            plan2 = ensure_hard_terms(plan)
            # lock_banner already inside _plan_block_for_qa — do not append twice
            block = _plan_block_for_qa(plan2)
            pos = {r["i"]: k for k, r in enumerate(rows) if isinstance(r, dict) and "i" in r}
            for k in range(0, len(jumps), 20):
                chunk = jumps[k : k + 20]
                try:
                    fixes = _fix_batch(chunk, block, analyze_model())
                except Exception as e:  # noqa: BLE001
                    print(f"[pronoun-qa] GPT vá lỗi: {e}", flush=True)
                    fixes = {}
                for i, vi in fixes.items():
                    if i in pos and vi and vi != rows[pos[i]].get("vi"):
                        if len(sample) < 10:
                            sample.append({"i": i, "before": rows[pos[i]].get("vi"), "after": vi})
                        rows[pos[i]]["vi"] = vi
                        rows[pos[i]]["pr"] = 1
                        n_gpt += 1
            apply_pronoun_postpass(rows, plan)
            jumps = find_pronoun_jumps(rows)
        except Exception as e:  # noqa: BLE001
            print(f"[pronoun-qa] bỏ GPT vá: {e}", flush=True)

    report = {
        "ok": True,
        "n_det": n_det,
        "n_jumps_left": len(jumps),
        "n_gpt_fixed": n_gpt,
        "sample": sample,
        "jumps": jumps[:30],
    }
    try:
        (work_dir / "pronoun_qa.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    if n_det or n_gpt or jumps:
        print(
            f"[pronoun-qa] det={n_det} · gpt_fix={n_gpt} · jumps_còn={len(jumps)}",
            flush=True,
        )
    return report
