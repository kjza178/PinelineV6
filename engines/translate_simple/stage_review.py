"""Bước 3: REVIEW cloud theo lô — MỘT lần gọi/lô làm hết: sửa nghĩa, áp xưng hô theo cặp plan,
tên/thuật ngữ, thành ngữ, dịch câu trống. Trả mỗi câu {i, vi, spk, to, conf}.

- Lô SIMPLE_BATCH câu (mặc định 30) + ngữ cảnh ±SIMPLE_CTX câu CHỈ ĐỌC.
- Khối plan thu gọn theo lô (plan_view.block_for) + gợi ý thành ngữ (lexicon.hints_for).
- Parse CHẶT (học từ translate._parse_review_fixes C1/R1): rỗng / cụt / fence mở / thiếu "lines" /
  JSON hỏng = LÔ LỖI. Câu không được trả hợp lệ → KHÔNG đánh rv (để cổng park/thử lại).
- Song song có giới hạn (SIMPLE_WORKERS, mặc định 8). Mọi gọi cloud qua
  cloud_retry.call_with_cloud_retry (một chính sách retry). KHÔNG fallback local / switch_preset.
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import cfg, lexicon
from .plan_view import PlanView
from .script_store import ScriptStore, is_manual


class BadOutput(ValueError):
    """Phản hồi rỗng / cụt / sai schema — lô coi là LỖI."""


SYSTEM_PROMPT = (
    "Bạn là biên tập viên lồng tiếng phim Trung → Việt. Với MỖI câu trong DANH SÁCH, trả bản tiếng Việt CUỐI "
    "CÙNG để đọc lồng tiếng.\n"
    "Mỗi câu có: i; zh = câu gốc (ASR, có thể nghe nhầm chữ đồng âm); nhap = bản nháp máy NMT (có thể sai nghĩa, "
    "sai xưng hô; rỗng = chưa có); sec = thời lượng (giây); dich=true = câu CHƯA có bản dịch, phải dịch mới từ zh; "
    "khoa=true = câu người đã sửa tay → trả y nguyên nhap.\n"
    "Làm trong MỘT lượt, theo thứ tự ưu tiên:\n"
    "1. NGHĨA: sửa chỗ nháp sai nghĩa so với zh (đảo chủ ngữ, hiểu ngược, dịch từng chữ vô nghĩa). Nháp đã đúng "
    "nghĩa và tự nhiên → GIỮ, không đổi chữ cho «hay hơn».\n"
    "2. XƯNG HÔ: xác định người nói (spk) và người nghe (to) từ ngữ cảnh, cảnh và nhân vật có mặt. Nếu cặp "
    "spk→to có trong bảng XƯNG HÔ BẮT BUỘC → câu dùng ĐÚNG tự xưng / cách gọi của cặp đó (sửa cả khi nháp dùng "
    "đại từ khác). Không có cặp → theo thể loại + quan hệ, giữ nhất quán với các câu trước của cùng cặp. Độc "
    "thoại / nói với đám đông → to=\"*\". Không đoán được → spk=\"?\" và conf thấp.\n"
    "3. TÊN / THUẬT NGỮ: tên nhân vật và thuật ngữ đúng chữ VI trong bảng; không phiên âm khác, không dịch nghĩa tên.\n"
    "4. THÀNH NGỮ / LÓNG: dịch đúng Ý tiếng Việt thông dụng (xem GỢI Ý); CẤM dịch từng chữ, CẤM dạng bị cấm.\n"
    "5. VĂN PHONG: thoại tự nhiên, ngắn gọn như phim lồng tiếng Việt, đúng thể loại; ~5 âm tiết/giây theo sec; "
    "số viết bằng chữ số Ả Rập (万=10 000, 亿=100 triệu); không thêm tình tiết, không giải thích.\n"
    "CẶP MỚI: nếu người nói và người nghe đều là nhân vật CÓ TÊN trong bảng NHÂN VẬT mà cặp spk→to CHƯA có "
    "trong XƯNG HÔ BẮT BUỘC → vẫn dịch theo mục 2, và ghi cặp đó vào \"new_pairs\" (tự xưng / cách gọi bạn đã "
    "dùng, i = câu đầu tiên của cặp; tối đa 5; không lặp cặp đã có trong bảng). Không có cặp mới → bỏ khoá này.\n"
    "Trả về DUY NHẤT một JSON object:\n"
    "{\"lines\":[{\"i\":<int>,\"vi\":\"<bản cuối>\",\"spk\":\"<tên VI người nói | ?>\",\"to\":\"<tên VI người "
    "nghe | * | ?>\",\"conf\":<0..1 = độ chắc về spk/to>}],"
    "\"new_pairs\":[{\"who\":\"<tên VI>\",\"to\":\"<tên VI>\",\"self\":\"<tự xưng>\",\"call\":\"<gọi>\",\"i\":<int>}]}\n"
    "Đủ MỌI i trong DANH SÁCH (kể cả câu giữ nguyên), đúng thứ tự, không thêm i ngoài DANH SÁCH, không trả câu "
    "NGỮ CẢNH. Không markdown, không chú thích."
)

REPAIR_PROMPT = (
    "Bạn là biên tập viên lồng tiếng phim Trung → Việt. Các câu trong DANH SÁCH đã được BỘ KIỂM đánh dấu lỗi "
    "(trường loi). Sửa ĐÚNG lỗi đó, giữ phần đã đúng:\n"
    "- pronoun: đại từ lệch cặp xưng hô của plan → dùng đúng tự xưng / cách gọi của cặp spk→to (nếu thật ra "
    "người nói/nghe khác → sửa spk/to cho đúng và dùng cặp tương ứng).\n"
    "- name: thiếu/sai tên nhân vật → dùng đúng tên VI trong bảng; nếu câu cố ý gọi bằng danh xưng (học trưởng, "
    "sư phụ…) hoặc đại từ thay tên là đúng thì giữ và đặt \"ok\":true.\n"
    "- empty: câu chưa có bản dịch → dịch mới từ zh.\n"
    "- cjk: còn chữ Hán → dịch hết sang tiếng Việt.\n"
    "Trả về DUY NHẤT JSON: {\"lines\":[{\"i\":<int>,\"vi\":\"…\",\"spk\":\"…\",\"to\":\"…\",\"conf\":<0..1>,"
    "\"ok\":<true nếu xác nhận câu hiện tại đúng>}]} — đủ mọi i trong DANH SÁCH, không markdown."
)


# ---------------------------------------------------------------------------- parse
def _decode(text: str | None):
    """Bóc JSON từ phản hồi. Ném BadOutput khi rỗng / cụt / fence mở / JSON hỏng."""
    t = (text or "").strip()
    if "<think>" in t and "</think>" in t:
        t = re.sub(r"<think>[\s\S]*?</think>", "", t).strip()
    if not t:
        raise BadOutput("phản hồi rỗng")
    if "```" in t:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t)
        if not m:
            raise BadOutput("fence mở không đóng (cụt)")
        t = m.group(1).strip()
    data = None
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        try:
            data = json.loads(re.sub(r",\s*([\}\]])", r"\1", t))
        except json.JSONDecodeError:
            dec = json.JSONDecoder()
            for mm in re.finditer(r"\{", t):
                try:
                    val, _ = dec.raw_decode(t, mm.start())
                except json.JSONDecodeError:
                    continue
                if isinstance(val, dict) and isinstance(val.get("lines"), list):
                    data = val
                    break
            if data is None:
                raise BadOutput(f"JSON hỏng/cụt (len={len(t)}) · cuối={t[-60:]!r}")
    return data


def parse_lines(text: str | None, want: set[int]) -> dict[int, dict]:
    """Parse CHẶT. Trả {i: {vi, spk, to, conf, ok}} chỉ cho i ∈ want có vi chuỗi khác rỗng.
    Ném BadOutput khi rỗng / cụt / không phải JSON trọn / thiếu "lines" (list).
    i trả trùng với vi KHÁC nhau → bỏ i đó (không rv, lượt thử lại làm lại) — review 2026-11-12."""
    data = _decode(text)
    if isinstance(data, list):
        lines = data
    elif isinstance(data, dict) and isinstance(data.get("lines"), list):
        lines = data["lines"]
    else:
        raise BadOutput("thiếu khoá \"lines\" (list)")
    out: dict[int, dict] = {}
    conflict: set[int] = set()
    for it in lines:
        if not isinstance(it, dict) or "i" not in it:
            continue
        try:
            ii = int(it["i"])
        except (TypeError, ValueError):
            continue
        if ii not in want:
            continue
        vi = it.get("vi")
        if not isinstance(vi, str) or not vi.strip():
            continue
        try:
            conf = max(0.0, min(1.0, float(it.get("conf"))))
        except (TypeError, ValueError):
            conf = None   # review 2026-11-12: thiếu/hỏng conf ≠ «không chắc» → bộ kiểm vẫn kiểm (validate)
        if ii in out:
            if out[ii]["vi"] != vi.strip():
                conflict.add(ii)
            continue
        out[ii] = {"vi": vi.strip(), "spk": str(it.get("spk") or "?").strip(),
                   "to": str(it.get("to") or "?").strip(), "conf": conf, "ok": bool(it.get("ok"))}
    for ii in conflict:
        out.pop(ii, None)
    return out


def parse_new_pairs(text: str | None) -> list[dict]:
    """Khoá tuỳ chọn "new_pairs" (đề xuất cặp xưng hô chưa có trong plan). Lỏng: hỏng → []."""
    try:
        data = _decode(text)
    except BadOutput:
        return []
    arr = data.get("new_pairs") if isinstance(data, dict) else None
    out = []
    for it in (arr if isinstance(arr, list) else [])[:10]:
        if not isinstance(it, dict):
            continue
        rec = {k: str(it.get(k) or "").strip()[:40] for k in ("who", "to", "self", "call")}
        try:
            rec["i"] = int(it.get("i"))
        except (TypeError, ValueError):
            rec["i"] = None
        if rec["who"] and rec["to"] and (rec["self"] or rec["call"]):
            out.append(rec)
    return out


# ---------------------------------------------------------------------------- prompt
def _ctx_line(r: dict, key: str) -> str:
    return f"  {r.get('i')} | {r.get('zh') or ''} | {(r.get(key) or '').strip()}"


def build_prompt(rows: list[dict], before: list[dict], after: list[dict], pv: PlanView, *,
                 repair_flags: dict[int, list[dict]] | None = None) -> tuple[str, str]:
    """(system, user) cho 1 lô. repair_flags → prompt vá (REPAIR_PROMPT + trường loi)."""
    parts = [pv.block_for(rows, before + after)]
    hints = lexicon.hints_for([r.get("zh") or "" for r in rows], pv.plan)
    if hints:
        parts.append("GỢI Ý THÀNH NGỮ / LÓNG (có trong lô):\n" + "\n".join(lexicon.hint_lines(hints)))
    if before:
        parts.append("NGỮ CẢNH TRƯỚC (chỉ đọc · i | zh | vi):\n" + "\n".join(_ctx_line(r, "vi") for r in before))
    payload = []
    for r in rows:
        item = {"i": int(r["i"]), "zh": r.get("zh") or "", "nhap": (r.get("vi") or "").strip(),
                "sec": round(float(r.get("end") or 0) - float(r.get("start") or 0), 1)}
        if not item["nhap"]:
            item["dich"] = True
        if is_manual(r):
            item["khoa"] = True
        if repair_flags is not None:
            item["loi"] = [f"{f['code']}: {f['why']}" for f in repair_flags.get(int(r["i"]), [])]
            if r.get("s_spk"):
                item["spk"] = r.get("s_spk")
                item["to"] = r.get("s_to")
        payload.append(item)
    parts.append("DANH SÁCH (JSON):\n" + json.dumps(payload, ensure_ascii=False))
    if after:
        parts.append("NGỮ CẢNH SAU (chỉ đọc · i | zh | nháp):\n" + "\n".join(_ctx_line(r, "vi") for r in after))
    return (REPAIR_PROMPT if repair_flags is not None else SYSTEM_PROMPT), "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------- gọi LLM
def call_llm(system: str, user: str, want: set[int], *, model: str | None, stop_check=None,
             label: str = "[simple]", pair_sink: list | None = None) -> dict[int, dict]:
    """1 lần gọi (qua cloud_retry) + parse chặt. Ném lỗi khi lô hỏng.
    pair_sink: list nhận đề xuất "new_pairs" của lần gọi THÀNH CÔNG (không ảnh hưởng rv)."""
    import engines.cloud_retry as cloud_retry
    from engines.llm import chat

    def _once():
        out = chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                   model=model, temperature=cfg.temperature(), json_mode=True,
                   max_tokens=cfg.max_tokens(), timeout=cfg.timeout())
        res = parse_lines(out, want)
        if pair_sink is not None:
            pair_sink.extend(parse_new_pairs(out))
        return res

    return cloud_retry.call_with_cloud_retry(
        _once, model=model, label=label, what=f"lô {len(want)} câu", stop_check=stop_check,
        cfg_get=cfg.get,
    )


def make_jobs(rows: list[dict], todo_idx: list[int], batch: int, ctx: int) -> list[dict]:
    """Chia các vị trí cần làm (todo_idx, theo thứ tự) thành lô; ngữ cảnh ±ctx lấy theo vị trí trong rows."""
    jobs = []
    for k in range(0, len(todo_idx), batch):
        pos = todo_idx[k:k + batch]
        p0, p1 = pos[0], pos[-1]
        in_lot = set(pos)
        before = [rows[j] for j in range(max(0, p0 - ctx), p0) if j not in in_lot]
        after = [rows[j] for j in range(p1 + 1, min(len(rows), p1 + 1 + ctx)) if j not in in_lot]
        jobs.append({"k": k, "pos": pos, "before": before, "after": after})
    return jobs


def apply_result(store: ScriptStore, rows: list[dict], job: dict, res: dict[int, dict], step: str,
                 done_pos: set | None = None) -> int:
    """Ghi kết quả 1 lô: câu trả hợp lệ → vi + s_spk/s_to/s_conf + rv=1. Câu thiếu → không rv.
    done_pos (R1.1): nhận vị trí các câu ĐƯỢC SOÁT trong lượt gọi này (khác `rv`, vốn còn từ lượt trước)."""
    n_ok = 0
    for p in job["pos"]:
        r = rows[p]
        ii = int(r["i"])
        got = res.get(ii)
        if got is None:
            store.note(step, ii, "LLM không trả câu này — giữ chưa rv")
            continue
        if not is_manual(r):
            store.set_vi(r, got["vi"], step, f"spk={got['spk']} to={got['to']} conf={got['conf']}")
            r.pop("fill_pending", None)
        r["s_spk"], r["s_to"] = got["spk"], got["to"]
        r["s_conf"] = None if got["conf"] is None else round(got["conf"], 3)
        if got.get("ok"):
            r["s_name_ok"] = 1
        else:
            r.pop("s_name_ok", None)   # vi mới → kiểm tên lại (không mang xác nhận cũ)
        r["rv"] = 1
        r.pop("unreviewed_accepted", None)
        if done_pos is not None:
            done_pos.add(p)
        n_ok += 1
    return n_ok


def run(store: ScriptStore, pv: PlanView, *, model: str | None, stop_check=None, progress=None,
        rereview: bool = False, pair_sink: list | None = None, batch: int | None = None) -> dict:
    """Review mọi câu chưa rv (rereview=True: mọi câu). Lô lỗi → thử lại 1 lượt lô nhỏ (½), tuần tự.
    Câu rác (junk=1, fix 2026-11-13) không bao giờ gửi. batch: cỡ lô (mặc định SIMPLE_BATCH).
    Trả thống kê {n_todo, n_ok, n_fail_lots, n_left}.
    R1.1 (fix 2026-11-14): n_ok / n_left / lượt thử lại tính theo câu ĐƯỢC SOÁT TRONG LƯỢT NÀY, không theo cờ `rv`.
    Trước đây «Soát lại» (rereview) mà mọi lô lỗi vẫn ra n_ok = tất cả (rv=1 còn từ lượt trước) → báo xong giả,
    không thử lại, pipeline tưởng cloud còn sống."""
    rows = store.rows
    todo_idx = [p for p, r in enumerate(rows) if (rereview or not r.get("rv")) and not r.get("junk")]
    stats = {"n_todo": len(todo_idx), "n_ok": 0, "n_fail_lots": 0, "n_left": 0, "lots": 0}
    if not todo_idx:
        return stats
    batch, ctx, workers = (int(batch) if batch else cfg.batch()), cfg.ctx(), cfg.workers()
    lock = threading.Lock()
    done_pos: set[int] = set()      # vị trí câu đã được soát trong lượt gọi này

    def _do(job: dict, step: str) -> int:
        if stop_check and stop_check():
            raise RuntimeError("stopped by user")
        lot = [rows[p] for p in job["pos"]]
        want = {int(r["i"]) for r in lot}
        system, user = build_prompt(lot, job["before"], job["after"], pv)
        try:
            res = call_llm(system, user, want, model=model, stop_check=stop_check, label="[simple-review]",
                           pair_sink=pair_sink)
        except Exception as e:  # noqa: BLE001
            if "stopped by user" in str(e).lower():
                raise
            with lock:
                for r in lot:
                    store.note(step, r.get("i"), f"lô lỗi: {str(e)[:160]}")
            print(f"[simple-review] lô {job['k']} lỗi ({type(e).__name__}: {str(e)[:160]}) — giữ chưa rv",
                  flush=True)
            return -1
        with lock:
            n = apply_result(store, rows, job, res, step, done_pos)
            store.write()
        return n

    jobs = make_jobs(rows, todo_idx, batch, ctx)
    stats["lots"] = len(jobs)
    print(f"[simple-review] model={model} · {len(todo_idx)} câu · {len(jobs)} lô × ≤{batch} · ctx ±{ctx} · "
          f"workers={min(workers, len(jobs))}", flush=True)
    failed: list[dict] = []
    done_n = 0
    if workers <= 1 or len(jobs) <= 1:
        for j in jobs:
            n = _do(j, "review")
            if n < 0:
                failed.append(j)
            done_n += 1
            if progress:
                progress(f"review lô {done_n}/{len(jobs)}")
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(jobs)), thread_name_prefix="simple-rv") as ex:
            futs = {ex.submit(_do, j, "review"): j for j in jobs}
            for f in as_completed(futs):
                n = f.result()
                if n < 0:
                    failed.append(futs[f])
                done_n += 1
                if progress:
                    progress(f"review lô {done_n}/{len(jobs)}")
    # lượt thử lại: câu còn chưa rv (lô lỗi hoặc LLM bỏ sót) — lô ½, tuần tự
    stats["n_fail_lots"] = len(failed)
    left_idx = [p for p in todo_idx if p not in done_pos]
    if left_idx:
        small = max(4, batch // 2)
        retry = make_jobs(rows, left_idx, small, ctx)
        print(f"[simple-review] thử lại {len(left_idx)} câu chưa soát được ({len(retry)} lô × ≤{small}, tuần tự)",
              flush=True)
        for j in retry:
            _do(j, "review-retry")
    stats["n_left"] = sum(1 for p in todo_idx if p not in done_pos)
    stats["n_ok"] = len(todo_idx) - stats["n_left"]
    return stats
