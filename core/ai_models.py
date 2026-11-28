"""core/ai_models.py — MỘT nơi khai báo: chức năng nào của Pineline dùng AI, model lấy từ khoá cấu hình nào, đang dùng
model gì thật, và đổi ra sao. Màn hình «Cài đặt AI» trên desktop (GET/POST /api/v6/ai) đọc/ghi qua module này.

Model đang dùng (`effective`) được tính bằng CHÍNH hàm mà code chạy job gọi (không chép lại chuỗi dự phòng) →
màn hình luôn nói đúng thứ sẽ chạy. Ghi = sửa khoá trong pineline.config.json (core.config.save_config) → có hiệu
lực ở lần gọi kế, không cần restart.

Tiền tố model: deepseek/… · alibaba/… (qwen/…) · chatgpt/… aurora/… (Aurora) · local (llama :8083) · llama/<tên> ·
local/<tên> (Ollama) · <tên tùy chỉnh>/… (nhà cung cấp tùy chỉnh: engines/providers/custom.py — OpenAI, Gemini,
OpenRouter…). V6 không dùng 9router (ollama/…-cloud).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable

MODEL_RE = re.compile(r"^(deepseek|alibaba|qwen|chatgpt|aurora|llama|local)/[\w.:\-]+$|^(local|llama|auto)$", re.I)
AURORA_PREFIXES = ("chatgpt/", "aurora/")


def _get(key: str) -> str:
    from core.config import get
    return str(get(key) or "").strip()


# ───────────── tính model đang dùng thật (gọi đúng hàm của code chạy job) ─────────────
def _eff_review() -> str:
    from engines.translate_simple import cfg
    return cfg.review_model(None) or ""


def _eff_plan() -> str:
    import engines.translate as T
    return T.plan_model()


def _eff_name() -> str:
    from core.config import pass_llm_model
    return pass_llm_model("name") or ""


def _eff_pronoun() -> str:
    from core.config import pass_llm_model
    return pass_llm_model("pronoun") or ""


def _eff_pair_fill() -> str:
    from engines.translate_simple import cfg
    return cfg.pair_fill_model(None) or ""


def _eff_translate() -> str:
    import engines.translate as T
    return T._tmodel() or _get("LLM_MODEL") or "local"


def _eff_legacy_review() -> str:
    import engines.translate as T
    return T._rmodel() or _get("LLM_MODEL") or "local"


def _eff_hang_fix() -> str:
    from agents.agent_hang_fix import resolve_model
    return resolve_model(None)


def _eff_agent() -> str:
    from core.config import agent_llm
    return agent_llm()["model"]


def _eff_agent_fallback() -> str:
    from core.config import agent_llm
    return agent_llm()["fallback"]


def _eff_pronoun_analyze() -> str:
    from engines.pronoun_consistency import analyze_model
    return analyze_model()


@dataclass(frozen=True)
class AIFunction:
    id: str
    label: str
    where: str                       # dùng ở đâu (người đọc)
    key: str                         # khoá cấu hình chỉnh được (rỗng = cố định trong code)
    effective: Callable[[], str]
    fallback: str = ""               # để trống thì lấy gì (người đọc)
    needs: str = ""                  # "aurora_file" | "aurora_image" | "tools" | ""
    group: str = "Dịch"
    enabled_key: str = ""            # khoá bật/tắt chức năng (nếu có) — chỉ để hiển thị
    fixed: str = ""                  # model cứng trong code (không chỉnh được)
    extra: dict = field(default_factory=dict)
    legacy: bool = False             # KHÔNG dùng trong luồng V6 (luồng cũ / thử nghiệm đang tắt) — UI gấp lại


FUNCTIONS: tuple[AIFunction, ...] = (
    AIFunction("review", "Soát & dịch chính", "Luồng dịch V6: AI dịch thẳng từ câu Trung theo plan (không NMT), "
               "DeepSeek/Alibaba thấy cả phim / chương, lô tự tính theo «Song song»; vá câu bị cờ "
               "(engines/translate_simple). Tốn token nhất.", "SIMPLE_MODEL", _eff_review,
               "REVIEW_MODEL → TRANSLATE_MODEL", group="Luồng dịch V6"),
    AIFunction("review_fallback", "Soát & dịch — model dự phòng", "Khi model Soát & dịch chính hỏng hẳn (mất mạng, "
               "hết tiền / quota, key sai, lỗi máy chủ kéo dài — sau khi đã thử lại) thì lô đó dịch bằng model này; "
               "cũng dùng khi lập bảng nhân vật lỗi và khi vá xong vẫn sót chữ Hán / câu trống. "
               "Nên chọn nhà cung cấp KHÁC model chính (vd chính DeepSeek → dự phòng Alibaba / AI trên máy).",
               "SIMPLE_FALLBACK_MODEL", lambda: _get("SIMPLE_FALLBACK_MODEL"), "(không có)", group="Luồng dịch V6"),
    AIFunction("plan", "Lập bảng nhân vật / xưng hô", "Đầu bước dịch, 1 lần / tập: nhân vật, cặp xưng hô, thuật ngữ, cảnh "
               "(engines/translate.build_plan).", "PLAN_MODEL", _eff_plan, "LLM_MODEL",
               extra={"mode_key": "PLAN_MODE"}, group="Luồng dịch V6"),
    AIFunction("translate", "Dịch phụ (chữ trên hình, tiêu đề)", "Dịch chữ OCR trên hình, tiêu đề phim, Context Studio "
               "(và luồng dịch cũ legacy). Câu ngắn, ít — model nhỏ / local được.", "TRANSLATE_MODEL", _eff_translate,
               "LLM_MODEL", group="Dịch phụ"),
    AIFunction("name", "Pass sửa tên (V2)", "Chỉ luồng cũ legacy: Agent «rà soát» chạy pass tên trước khi soát lại. "
               "Luồng V6 không chạy (soát lại V6 tự xử lý tên theo plan).",
               "NAME_MODEL", _eff_name, "CHATGPT_MODEL → REVIEW_MODEL → LLM_MODEL", legacy=True),
    AIFunction("pronoun", "Pass xưng hô (V2)", "Chỉ luồng cũ legacy: Agent «rà soát» chạy pass xưng hô. Luồng V6 không "
               "chạy (xưng hô do plan + bộ kiểm + vá).",
               "PRONOUN_MODEL", _eff_pronoun, "CHATGPT_MODEL → REVIEW_MODEL → LLM_MODEL", legacy=True),
    AIFunction("pair_fill", "Bổ sung cặp xưng hô thiếu (thử nghiệm)", "Luồng V6 — thử nghiệm ĐANG TẮT "
               "(SIMPLE_PAIR_FILL=1 để bật): sau lượt soát đầu hỏi thêm cặp xưng hô còn thiếu trong plan.",
               "SIMPLE_PAIR_FILL_MODEL", _eff_pair_fill, "model Soát & dịch chính", enabled_key="SIMPLE_PAIR_FILL",
               legacy=True),
    AIFunction("legacy_review", "Soát (luồng cũ)", "Chỉ khi instance dùng translate_pipeline=legacy, và QA gate cũ.",
               "REVIEW_MODEL", _eff_legacy_review, "TRANSLATE_MODEL", legacy=True),
    AIFunction("hang_fix", "Sửa job treo (QA)", "Agent sửa câu khiến job kẹt sau dịch (agents/agent_hang_fix).",
               "HANG_FIX_MODEL", _eff_hang_fix, "PRONOUN_MODEL → chatgpt/gpt-5-6-mini", group="Agent"),
    AIFunction("agent", "AI Agent (chat + Second Brain)", "Nút «Agent» trên desktop, Telegram, Second Brain tự xử lý. "
               "Cần model hỗ trợ gọi công cụ.", "AGENT_MODEL", _eff_agent, "LLM_MODEL", needs="tools", group="Agent"),
    AIFunction("agent_fallback", "AI Agent — model dự phòng", "Thử khi model Agent chính lỗi (timeout, 429, 5xx).",
               "AGENT_FALLBACK_MODEL", _eff_agent_fallback, "(không có)", needs="tools", group="Agent"),
    AIFunction("pronoun_analyze", "Phân tích xưng hô cả phim (thử nghiệm)", "Đang tắt. Gửi CẢ file phụ đề lên để dựng bảng xưng hô — chỉ khi "
               "bật SIMPLE_PRONOUN_ANALYZE=1. Chế độ file (mặc định) upload phụ đề → chỉ Aurora; "
               "PRONOUN_ANALYZE_MODE=full/fast quét theo lô → model nào cũng được.", "PRONOUN_ANALYZE_MODEL",
               _eff_pronoun_analyze, "PRONOUN_MODEL", needs="aurora_file", enabled_key="SIMPLE_PRONOUN_ANALYZE",
               legacy=True),
    AIFunction("meta", "Tiêu đề / mô tả YouTube", "Meta Studio khi đăng YouTube (agents/meta_studio).", "",
               lambda: "chatgpt/gpt-5-6", fixed="chatgpt/gpt-5-6 (Aurora, cố định trong code)", group="Đăng YouTube"),
    AIFunction("thumb", "Ảnh thumbnail", "Tạo ảnh bìa khi đăng YouTube (Aurora /v1/images).", "",
               lambda: "aurora/gpt-image-2", needs="aurora_image", fixed="gpt-image-2 (Aurora, cố định trong code)",
               group="Đăng YouTube"),
)
BY_ID = {f.id: f for f in FUNCTIONS}

# Bộ cài sẵn: id chức năng → model. Chức năng không có trong bộ thì giữ nguyên.
# DeepSeek / Alibaba: KHÔNG ghi cứng tên model (nhà cung cấp đổi / khai tử tên — vd deepseek-chat ngừng 24/07/2026).
# Lúc áp, hỏi GET /models bằng key đã lưu rồi chọn bản «flash» (providers.pick_model) cho mọi chức năng.
_PRESET_FUNCS = ("review", "plan", "name", "pronoun", "pair_fill", "translate", "legacy_review", "hang_fix", "agent")
PRESETS: dict[str, dict] = {
    # Khuyến nghị 2026-11-15: DeepSeek làm việc nặng / chặn hàng chờ; Aurora plan (đọc cả tập qua file, tới khi xong
    # A7) + Agent dự phòng (khác nhà cung cấp); local cho việc nhỏ không tốn tiền (dịch chữ trên hình, sửa job treo).
    "recommended": {"label": "Khuyến nghị (DeepSeek + Aurora + local)", "plan_mode": "file", "provider": "deepseek",
                    "provider_funcs": ("review", "agent"),
                    "models": {"plan": "chatgpt/gpt-5-6-mini", "agent_fallback": "chatgpt/gpt-5-6-mini",
                               "translate": "local", "hang_fix": "local", "pair_fill": ""}},
    "deepseek": {"label": "Tất cả DeepSeek", "plan_mode": "sample", "provider": "deepseek"},
    "alibaba": {"label": "Tất cả Alibaba (Qwen)", "plan_mode": "sample", "provider": "alibaba"},
    "aurora": {"label": "Aurora (ChatGPT)", "plan_mode": "file",
               "models": {"review": "chatgpt/gpt-6", "plan": "chatgpt/gpt-5-6-mini", "name": "chatgpt/gpt-5-6-mini",
                          "pronoun": "chatgpt/gpt-5-6-mini", "pair_fill": "chatgpt/gpt-5-6-mini",
                          "translate": "chatgpt/gpt-5-6-mini", "legacy_review": "chatgpt/gpt-6",
                          "hang_fix": "chatgpt/gpt-5-6-mini", "agent": "chatgpt/gpt-5-6-mini"}},
    "local": {"label": "Chỉ AI trên máy (llama-server)", "plan_mode": "sample",
              "models": {k: "local" for k in _PRESET_FUNCS}},
}


def _provider(name: str):
    from engines import providers
    hit = providers.resolve(f"{(name or '').strip()}/x")
    if hit is None:
        raise ValueError(f"không có nhà cung cấp «{name}»")
    return hit[0]


def fetch_models(name: str, *, api_key: str | None = None, base_url: str | None = None) -> dict:
    """Danh sách model THẬT của nhà cung cấp (GET /models bằng key đã lưu, hoặc key/URL vừa gõ chưa lưu).
    → {provider, models: ["deepseek/<id>"…], suggested, fetched_at}. Lỗi mạng/key → ValueError (thông báo rõ)."""
    from engines import providers
    from engines.llm import LLMUnavailable
    p = _provider(name)
    b = (base_url or "").strip().rstrip("/")
    if b and not re.match(r"^https?://", b):
        raise ValueError(f"{p.label}: địa chỉ phải bắt đầu bằng http:// hoặc https://")
    try:
        ids = providers.list_models(p, api_key=api_key, base_url=b or None)
    except LLMUnavailable as e:
        raise ValueError(str(e)) from e
    pick = providers.pick_model(ids)
    return {"provider": p.name, "label": p.label, "models": [f"{p.name}/{m}" for m in ids],
            "suggested": f"{p.name}/{pick}" if pick else "", "fetched_at": time.time()}


def _aurora_reachable(base: str | None) -> bool:
    """2026-11-21: Aurora có ĐANG TRẢ LỜI không (GET /models, 1.5 s). «configured» chỉ là có địa chỉ — config cũ luôn có
    CHATGPT_BASE_URL=:3377 dù máy không chạy Aurora → UI tưởng có Aurora, giao plan cho Aurora → plan luôn lỗi."""
    if not base:
        return False
    try:
        import requests
        r = requests.get(str(base).rstrip("/") + "/models", timeout=1.5)
        return r.status_code < 500
    except Exception as e:  # noqa: BLE001
        print(f"[ai_models] Aurora {base} không trả lời: {type(e).__name__}", flush=True)
        return False


def _preset_models(pr: dict, model: str | None = None) -> dict:
    """model: tên người dùng tự chọn cho bộ của nhà cung cấp (vd «openrouter/…») — bỏ qua bước tự chọn bản «flash»
    (không cần GET /models, dùng được với nhà không có /models)."""
    if "provider" not in pr:
        return dict(pr["models"])
    m = str(model or "").strip()
    if m:
        from engines import providers
        hit = providers.resolve(m)
        if hit is None or hit[0].name != pr["provider"] or not hit[1]:
            raise ValueError(f"model «{m}» không thuộc nhà cung cấp «{pr['provider']}»")
        res = {"suggested": m}
    else:
        res = fetch_models(pr["provider"])                 # ValueError nếu chưa có key / lỗi mạng
    if not res["suggested"]:
        raise ValueError(f"{res['label']}: không tìm thấy model dịch được trong danh sách ({len(res['models'])} model)"
                         " — chọn tay ở từng chức năng")
    out = {k: res["suggested"] for k in pr.get("provider_funcs", _PRESET_FUNCS)}
    out.update(pr.get("models") or {})
    return out


def provider_of(model: str) -> str:
    """Tên nhà cung cấp hiển thị của một model."""
    m = (model or "").strip().lower()
    if not m:
        return ""
    if m.startswith(AURORA_PREFIXES):
        return "Aurora"
    from engines import providers
    hit = providers.resolve(m)
    if hit:
        return hit[0].label
    if m.startswith("local/"):
        return "Ollama (local)"
    if m.startswith("ollama/") or "-cloud" in m:
        return "9router (không còn dùng)"
    return "AI trên máy (llama)"


def check_model(fid: str, model: str) -> str:
    """Lý do model KHÔNG dùng được cho chức năng (chuỗi rỗng = hợp lệ)."""
    f = BY_ID.get(fid)
    m = (model or "").strip()
    if f is None:
        return f"không có chức năng «{fid}»"
    if f.fixed:
        return "chức năng này dùng model cố định trong code, chưa đổi được"
    if not m:
        return ""                                         # để trống = dùng chuỗi dự phòng
    if m.lower().startswith("ollama/") or "-cloud" in m.lower():
        return "V6 không dùng 9router (ollama/…-cloud) — chọn deepseek/…, alibaba/…, chatgpt/… hoặc local"
    from engines import providers
    hit = providers.resolve(m)
    if hit and not hit[1]:
        return f"thiếu tên model sau «{hit[0].name}/» — bấm «Lấy danh sách model» ở dòng {hit[0].label}"
    if hit:                                   # tên model của nhà cung cấp có thể chứa «/» (OpenRouter: hang/model)
        if not re.match(r"^[\w.:@\-]+(/[\w.:@\-]+)*$", hit[1]):
            return "tên model không hợp lệ"
        if _needs(f) == "aurora_file":
            return "chức năng này cần upload file — chỉ Aurora (chatgpt/… aurora/…) làm được"
        return ""
    if "/" in m and m.split("/", 1)[0].lower() not in ("chatgpt", "aurora", "llama", "local"):
        return f"không có nhà cung cấp «{m.split('/', 1)[0]}» — thêm ở Cài đặt → AI → Nhà cung cấp tùy chỉnh"
    if not MODEL_RE.match(m) and not re.match(r"^[\w.:\-]+$", m):
        return "tên model không hợp lệ"
    if _needs(f) == "aurora_file" and not m.lower().startswith(AURORA_PREFIXES):
        return "chức năng này cần upload file — chỉ Aurora (chatgpt/… aurora/…) làm được"
    return ""


def _needs(f: AIFunction) -> str:
    """Yêu cầu THỰC của chức năng lúc này. Phân tích xưng hô chỉ cần Aurora ở chế độ «file» (upload phụ đề);
    PRONOUN_ANALYZE_MODE=full / fast quét theo lô bằng lời gọi thường → model nào cũng được."""
    if f.id == "pronoun_analyze":
        try:
            from engines.pronoun_consistency import analyze_mode
            return "aurora_file" if analyze_mode() == "file" else ""
        except Exception as e:  # noqa: BLE001
            print(f"[ai_models] đọc PRONOUN_ANALYZE_MODE lỗi: {e}", flush=True)
    return f.needs


def _key_hint(v: str) -> str:
    return f"****{v[-4:]}" if len(v) >= 8 else ("****" if v else "")


def snapshot() -> dict:
    """Toàn bộ trạng thái cho màn hình Cài đặt AI. Key KHÔNG trả nguyên văn (chỉ 4 ký tự cuối)."""
    from engines import providers
    import engines.translate as T
    provs = []
    for p in providers.all_providers():
        k = p.key()
        provs.append({"name": p.name, "label": p.label, "base_url": p.base(), "default_base": p.default_base,
                      "base_key": p.base_keys[0], "key_key": p.key_keys[0], "has_key": bool(k),
                      "max_tokens": p.out_cap(), "max_tokens_default": p.max_tokens_cap,
                      "max_tokens_key": f"{p.env_prefix()}_MAX_TOKENS", "custom": p.custom,
                      "concurrency": p.concurrency(), "concurrency_default": p.default_concurrency,
                      "concurrency_key": f"{p.env_prefix()}_CONCURRENCY",
                      "key_hint": _key_hint(k), "models": [f"{p.name}/{m}" for m in providers.cached_models(p)[1]],
                      "models_fetched_at": providers.cached_models(p)[0] or None})
    try:
        from engines.llm import get_llama_models, get_ollama_models
        llama, ollama = get_llama_models(), get_ollama_models()
    except Exception as e:  # noqa: BLE001
        print(f"[ai_models] liệt kê model local lỗi: {e}", flush=True)
        llama, ollama = [], []
    aurora_base = _get("AURORA_BASE_URL") or _get("CHATGPT_BASE_URL")
    aurora_up = _aurora_reachable(aurora_base)
    from core import local_ai
    local_cfg = local_ai.snapshot(probe=False)
    funcs = []
    for f in FUNCTIONS:
        try:
            eff = f.effective() or ""
        except Exception as e:  # noqa: BLE001
            eff = ""
            print(f"[ai_models] tính model «{f.id}» lỗi: {e}", flush=True)
        set_val = _get(f.key) if f.key else ""
        row = {"id": f.id, "label": f.label, "where": f.where, "group": f.group, "key": f.key,
               "value": set_val, "effective": eff, "provider": provider_of(eff),
               "source": (f.key if set_val else (f"dự phòng: {f.fallback}" if f.fallback else "mặc định")),
               "needs": _needs(f), "fixed": f.fixed, "legacy": f.legacy, "problem": check_model(f.id, eff) if (eff and not f.fixed) else ""}
        if f.enabled_key:
            row["enabled"] = _get(f.enabled_key).lower() in ("1", "true", "yes", "on")
            row["enabled_key"] = f.enabled_key
        funcs.append(row)
    return {
        "providers": provs,
        "aurora": {"base_url": aurora_base or local_cfg["aurora_url"]["value"], "configured": bool(aurora_base),
                   "reachable": aurora_up},
        "local": {"llama": llama, "ollama": [f"local/{m}" for m in ollama], "settings": local_cfg},
        "functions": funcs,
        "plan_mode": {"value": _get("PLAN_MODE") or "file", "effective": T.plan_mode_effective()},
        "presets": [{"id": k, "label": v["label"]} for k, v in PRESETS.items()]
                   + [{"id": f"custom:{p.name}", "label": f"Tất cả {p.label}"} for p in providers.all_providers() if p.custom],
        "custom_templates": _custom_templates(),
    }


def _custom_templates() -> list[dict]:
    from engines.providers import custom
    return [dict(t) for t in custom.TEMPLATES]


def _preset(preset: str) -> dict | None:
    """Bộ cài sẵn theo id; «custom:<tên>» = mọi chức năng dùng nhà cung cấp tùy chỉnh đó (model «flash»/đầu danh sách)."""
    if preset.startswith("custom:"):
        name = preset.split(":", 1)[1]
        p = _provider(name)                       # ValueError nếu không có
        return {"label": f"Tất cả {p.label}", "plan_mode": "sample", "provider": p.name}
    return PRESETS.get(preset)


def save_custom_provider(spec: dict, *, api_key: str | None = None, editing: str | None = None) -> dict:
    """Thêm / sửa nhà cung cấp tùy chỉnh. editing = tên cũ khi sửa (đổi tên → chuyển key sang tên mới).
    Lỗi → ValueError, không ghi gì."""
    from core.config import save_config
    from engines.providers import custom
    cur = custom.specs()
    if editing and not any(x["name"] == editing for x in cur):
        raise ValueError(f"không có nhà cung cấp tùy chỉnh «{editing}»")
    item = custom.validate(spec or {}, existing=cur, editing=editing)
    if editing and editing != item["name"] and _users_of(editing):
        raise ValueError(f"đang có chức năng dùng «{editing}/…» ({', '.join(_users_of(editing))}) — đổi model các "
                         "chức năng đó trước khi đổi tên")
    out = [x for x in cur if x["name"] != (editing or item["name"])] + [item]
    updates: dict = {custom.CFG_KEY: custom.dump(out)}
    pre_new = custom.env_prefix(item["name"])
    if editing and editing != item["name"]:
        old = _provider(editing)
        if not str(api_key or "").strip() and old.key():
            updates[f"{pre_new}_API_KEY"] = old.key()
        for suf in ("API_KEY", "BASE_URL", "MAX_TOKENS", "CONCURRENCY"):
            updates[f"{custom.env_prefix(editing)}_{suf}"] = None
    if str(api_key or "").strip():
        updates[f"{pre_new}_API_KEY"] = str(api_key).strip()
    updates[f"{pre_new}_BASE_URL"] = None             # địa chỉ nằm trong danh sách — bỏ bản ghi đè cũ
    save_config(updates)
    return {"changed": sorted(updates), "provider": item["name"], **snapshot()}


def _users_of(name: str) -> list[str]:
    pre = f"{name}/"
    return [f.label for f in FUNCTIONS if f.key and _get(f.key).lower().startswith(pre)]


def remove_custom_provider(name: str) -> dict:
    """Xoá nhà cung cấp tùy chỉnh + key của nó. Còn chức năng dùng → ValueError (nói rõ chức năng nào)."""
    from core.config import save_config
    from engines.providers import custom
    n = str(name or "").strip().lower()
    cur = custom.specs()
    if not any(x["name"] == n for x in cur):
        raise ValueError(f"không có nhà cung cấp tùy chỉnh «{n}»")
    users = _users_of(n)
    if users:
        raise ValueError(f"đang có chức năng dùng «{n}/…»: {', '.join(users)} — đổi model các chức năng đó trước")
    updates: dict = {custom.CFG_KEY: custom.dump([x for x in cur if x["name"] != n]) if len(cur) > 1 else None}
    for suf in ("API_KEY", "BASE_URL", "MAX_TOKENS", "CONCURRENCY"):
        updates[f"{custom.env_prefix(n)}_{suf}"] = None
    save_config(updates)
    return {"changed": sorted(updates), **snapshot()}


def apply(models: dict | None = None, *, plan_mode: str | None = None, providers_cfg: dict | None = None,
          preset: str | None = None, preset_model: str | None = None) -> dict:
    """Ghi lựa chọn vào pineline.config.json. models: {id chức năng: model | "" (= dùng dự phòng)}.
    providers_cfg: {tên: {"api_key": "...", "base_url": "..."}} — api_key rỗng/thiếu = giữ nguyên key cũ;
    "clear_key": True = xoá key. Lỗi → ValueError (không ghi gì)."""
    from core.config import save_config
    from engines import providers
    models = dict(models or {})
    if preset:
        pr = _preset(str(preset))
        if pr is None:
            raise ValueError(f"không có bộ cài sẵn «{preset}»")
        models = {**_preset_models(pr, preset_model), **models}
        plan_mode = plan_mode or pr["plan_mode"]
    updates: dict = {}
    errors = []
    for fid, m in models.items():
        f = BY_ID.get(fid)
        if f is None or not f.key:
            errors.append(f"{fid}: {check_model(fid, '') or 'không chỉnh được'}")
            continue
        m = str(m or "").strip()
        why = check_model(fid, m)
        if why:
            errors.append(f"{f.label}: {why}")
            continue
        updates[f.key] = m or None
    if plan_mode is not None:
        pm = str(plan_mode).strip().lower()
        if pm not in ("file", "sample"):
            errors.append("PLAN_MODE: chọn file hoặc sample")
        else:
            updates["PLAN_MODE"] = pm
    for name, pc in (providers_cfg or {}).items():
        try:
            p = _provider(name)
        except ValueError as e:
            errors.append(str(e))
            continue
        pc = pc or {}
        if pc.get("clear_key"):
            updates[p.key_keys[0]] = None
        elif str(pc.get("api_key") or "").strip():
            updates[p.key_keys[0]] = str(pc["api_key"]).strip()
        if "base_url" in pc:
            b = str(pc.get("base_url") or "").strip().rstrip("/")
            if b and not re.match(r"^https?://", b):
                errors.append(f"{p.label}: địa chỉ phải bắt đầu bằng http:// hoặc https://")
            else:
                updates[p.base_keys[0]] = None if (not b or b == p.default_base.rstrip("/")) else b
        if "max_tokens" in pc:
            raw = str(pc.get("max_tokens") or "").strip()
            mk = f"{p.env_prefix()}_MAX_TOKENS"
            if not raw:
                updates[mk] = None
            elif not raw.isdigit() or not (256 <= int(raw) <= 1_000_000):
                errors.append(f"{p.label}: trần output phải là số từ 256 đến 1000000")
            else:
                updates[mk] = None if int(raw) == p.max_tokens_cap else raw
        if "concurrency" in pc:
            raw = str(pc.get("concurrency") or "").strip()
            ck = f"{p.env_prefix()}_CONCURRENCY"
            if not raw:
                updates[ck] = None
            elif not raw.isdigit() or not (1 <= int(raw) <= 512):
                errors.append(f"{p.label}: số request song song phải từ 1 đến 512")
            else:
                updates[ck] = None if int(raw) == p.default_concurrency else raw
    if errors:
        raise ValueError("; ".join(errors))
    if updates:
        save_config(updates)
    return {"changed": sorted(updates), **snapshot()}


def test_model(model: str, *, timeout: int = 40) -> dict:
    """Gửi 1 câu ngắn tới model → {ok, ms, reply | error}. Không thử lại (báo nhanh)."""
    m = (model or "").strip()
    if not m:
        return {"ok": False, "error": "chưa chọn model"}
    t0 = time.time()
    try:
        from engines.llm import chat
        out = chat([{"role": "user", "content": "Trả lời đúng một chữ: OK"}], model=m, temperature=0,
                   max_tokens=16, retries=1, timeout=timeout)
        return {"ok": bool((out or "").strip()), "ms": int((time.time() - t0) * 1000),
                "reply": (out or "").strip()[:80], "provider": provider_of(m),
                **({} if (out or "").strip() else {"error": "model trả rỗng"})}
    except Exception as e:  # noqa: BLE001 — trả lý do cho người dùng
        return {"ok": False, "ms": int((time.time() - t0) * 1000), "error": str(e)[:400], "provider": provider_of(m)}
