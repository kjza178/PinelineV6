"""
NMT ZH→VI (HachimiMT) — DỊCH CHÍNH; LLM chỉ rà soát (run_review).

Config:
  MT_ZH_VI=1                 bật NMT
  MT_ZH_VI_ROLE=primary|hint primary=ghi VI từ NMT (mặc định); hint=chỉ gợi ý LLM dịch
  MT_ZH_VI_MODEL=auto|repo   auto = modern→base, xianxia→QT; hoặc ép 1 repo
  MT_ZH_VI_MODEL_BASE=ngocdang83/HachimiMT-60-zh-vi
  MT_ZH_VI_MODEL_QT=ngocdang83/HachimiMT-60-QT
  MT_ZH_VI_DEVICE=cpu|cuda|auto
  MT_ZH_VI_LOCAL=…           ép 1 thư mục (bỏ qua auto)
  MT_ZH_VI_ONLY=1            alias cũ của ROLE=primary (giữ tương thích)
"""
from __future__ import annotations

try:  # machine-config 11-11: key phụ thuộc máy (cổng/thư mục) — V5/V6 core/, V4 phẳng
    from core import machine as _machine
except ImportError:  # pragma: no cover
    import machine as _machine  # type: ignore
import os
import threading
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
_REPO_BASE = "ngocdang83/HachimiMT-60-zh-vi"
_REPO_QT = "ngocdang83/HachimiMT-60-QT"
_DEFAULT_REPO = "auto"
_LOCAL_HINTS_QT = [
    _machine.mt_model_dir() / "ngocdang83__HachimiMT-60-QT",
]
_LOCAL_HINTS_BASE = [
    _machine.mt_model_dir() / "ngocdang83__HachimiMT-60-zh-vi",
    _machine.mt_model_dir() / "robebo116__60-zh-vi",
    _machine.mt_model_dir() / "60-zh-vi",
    ROOT / "models" / "mt" / "60-zh-vi",
]

_lock = threading.Lock()
_translator = None
_tokenizer = None
_loaded_from: str | None = None
_loaded_repo: str | None = None
_active_repo: str | None = None


def _cfg(key: str, default: str | None = None) -> str | None:
    try:
        from core.config import get
        return get(key, default)
    except Exception:  # noqa: BLE001
        return os.getenv(key, default)


def enabled() -> bool:
    v = (_cfg("MT_ZH_VI") or "0").strip().lower()
    return v in ("1", "true", "yes", "on", "draft")


def primary_mode() -> bool:
    """True = NMT ghi VI chính; False = chỉ gắn mt_vi cho LLM dịch (hint)."""
    role = (_cfg("MT_ZH_VI_ROLE") or "").strip().lower()
    flag = (_cfg("MT_ZH_VI") or "").strip().lower()
    if flag in ("draft", "hint") or role in ("hint", "draft", "suggest"):
        return False
    if role in ("primary", "main", "nmt"):
        return True
    only = (_cfg("MT_ZH_VI_ONLY") or "").strip().lower()
    if only in ("1", "true", "yes", "on"):
        return True
    if only in ("0", "false", "no", "off"):
        return False
    # mặc định (2026-11-09): NMT dịch chính, LLM rà soát
    return True


def draft_mode() -> bool:
    """Ngược primary_mode (tên cũ: gắn nháp, không ghi VI)."""
    return not primary_mode()


def device() -> str:
    d = (_cfg("MT_ZH_VI_DEVICE") or "cpu").strip().lower()
    return d if d in ("cpu", "cuda", "auto") else "cpu"


def _flavor_from_plan(plan: dict | None) -> str:
    """qt | base — chỉ QT khi xianxia/cổ THUẦN; mixed/đổi cảnh → base (linh hoạt)."""
    if not isinstance(plan, dict):
        return "base"
    lock = plan.get("style_lock") if isinstance(plan.get("style_lock"), dict) else {}
    mode = str(lock.get("mode") or "").lower()
    if mode in ("mixed", "modern"):
        return "base"
    if mode in ("xianxia", "ancient"):
        return "qt"
    blob = " ".join(
        str(plan.get(k) or "")
        for k in ("genre", "tone", "content_type", "pronoun_notes")
    ).lower()
    ancient_keys = (
        "tu tiên", "tu tien", "cổ trang", "co trang", "wuxia", "xianxia",
        "xuanhuan", "huyền ảo", "tiên hiệp", "tien hiep", "cổ đại", "co dai",
    )
    modern_keys = (
        "đô thị", "do thi", "hiện đại", "hien dai", "đời thường", "doi thuong",
        "vlog", "urban", "modern", "học đường", "hoc duong", "dị năng",
        "tinh cầu", "short drama", "short-drama",
    )
    has_ancient = any(k in blob for k in ancient_keys)
    has_modern = any(k in blob for k in modern_keys)
    if has_modern and not has_ancient:
        return "base"
    if has_ancient and not has_modern:
        return "qt"
    # mixed / chưa rõ → base (linh hoạt đô thị↔tu tiên)
    return "base"


def resolve_model_id(plan: dict | None = None) -> str:
    """Repo HF sẽ load. `auto` → base/QT theo genre; repo cụ thể → ép."""
    raw = (_cfg("MT_ZH_VI_MODEL") or _DEFAULT_REPO).strip()
    low = raw.lower()
    if low in ("", "auto", "genre", "by_genre"):
        flavor = _flavor_from_plan(plan)
        if flavor == "base":
            return (_cfg("MT_ZH_VI_MODEL_BASE") or _REPO_BASE).strip()
        return (_cfg("MT_ZH_VI_MODEL_QT") or _REPO_QT).strip()
    return raw


def model_id(plan: dict | None = None) -> str:
    """Repo đang chọn (có plan) hoặc config thô."""
    return resolve_model_id(plan) if plan is not None else (
        _active_repo or resolve_model_id(None)
    )


def _resolve_model_dir(repo: str) -> Path:
    custom = (_cfg("MT_ZH_VI_LOCAL") or "").strip()
    if custom and Path(custom).is_dir():
        return Path(custom)
    mid = repo.replace("/", "__")
    hinted = _machine.mt_model_dir() / mid
    if (hinted / "ct2-int8_float32").is_dir():
        return hinted
    hints = _LOCAL_HINTS_QT if "qt" in repo.lower() else _LOCAL_HINTS_BASE
    # nếu ép repo base nhưng folder QT-named — vẫn quét cả 2 list
    for p in list(hints) + _LOCAL_HINTS_BASE + _LOCAL_HINTS_QT:
        if (p / "ct2-int8_float32").is_dir():
            # chỉ nhận hint khớp họ model
            name = p.name.lower()
            want_qt = "qt" in repo.lower()
            is_qt = "qt" in name
            if want_qt == is_qt or (not want_qt and "60-zh-vi" in name and "qt" not in name):
                return p
            if not want_qt and "robebo" in name:
                return p
    from huggingface_hub import snapshot_download
    dest = _machine.mt_model_dir() / mid
    dest.parent.mkdir(parents=True, exist_ok=True)
    path = snapshot_download(
        repo,
        local_dir=str(dest),
        allow_patterns=[
            "config.json", "generation_config.json",
            "source.spm", "target.spm", "vocab.json", "tokenizer_config.json",
            "model.safetensors", "ct2-int8_float32/*", "README.md",
        ],
    )
    return Path(path)


def ensure_loaded(plan: dict | None = None, *, repo: str | None = None) -> str:
    """Load (hoặc đổi) model. Trả repo đang active."""
    global _translator, _tokenizer, _loaded_from, _loaded_repo, _active_repo
    want = (repo or resolve_model_id(plan)).strip()
    with _lock:
        if (
            _translator is not None
            and _tokenizer is not None
            and _loaded_repo == want
        ):
            _active_repo = want
            return want
        # đổi model → drop cũ
        _translator = None
        _tokenizer = None
        _loaded_from = None
        _loaded_repo = None

        root = _resolve_model_dir(want)
        ct2 = root / "ct2-int8_float32"
        if not ct2.is_dir():
            raise RuntimeError(f"MT: thiếu ct2-int8_float32 trong {root}")
        import ctranslate2
        from transformers import AutoTokenizer

        dev = device()
        if dev == "auto":
            try:
                import torch
                dev = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:  # noqa: BLE001
                dev = "cpu"
        compute = "int8_float32" if dev == "cpu" else "float16"
        print(f"[mt-zh-vi] load {want} ← {root} device={dev}", flush=True)
        _translator = ctranslate2.Translator(str(ct2), device=dev, compute_type=compute)
        tok_src = ct2 if (ct2 / "vocab.json").is_file() else root
        _tokenizer = AutoTokenizer.from_pretrained(str(tok_src))
        _loaded_from = str(root)
        _loaded_repo = want
        _active_repo = want
        return want


def unload() -> None:
    global _translator, _tokenizer, _loaded_from, _loaded_repo, _active_repo
    with _lock:
        _translator = None
        _tokenizer = None
        _loaded_from = None
        _loaded_repo = None
        _active_repo = None


def _decode_opts(repo: str | None = None) -> dict:
    mid = (repo or _active_repo or model_id()).lower()
    if "qt" in mid:
        return {"beam": 1, "no_repeat": 0, "rep": 1.0}
    return {
        "beam": int(_cfg("MT_ZH_VI_BEAM", "4") or 4),
        "no_repeat": 2,
        "rep": 1.2,
    }


def translate_many(
    texts: Iterable[str],
    *,
    max_tokens: int = 256,
    batch_size: int = 24,
    plan: dict | None = None,
    repo: str | None = None,
) -> list[str]:
    active = ensure_loaded(plan, repo=repo)
    assert _translator is not None and _tokenizer is not None
    src_list = [(t or "").strip() for t in texts]
    out = [""] * len(src_list)
    pending = [i for i, t in enumerate(src_list) if t]
    if not pending:
        return out
    opts = _decode_opts(active)
    max_tokens = int(_cfg("MT_ZH_VI_MAX_TOKENS", str(max_tokens)) or max_tokens)
    batch_size = int(_cfg("MT_ZH_VI_BATCH", str(batch_size)) or batch_size)
    for start in range(0, len(pending), batch_size):
        idxs = pending[start : start + batch_size]
        chunk = [src_list[i] for i in idxs]
        input_tokens = []
        for t in chunk:
            flat = " ".join(t.split())
            ids = _tokenizer(flat, truncation=True, max_length=256).input_ids
            pieces = [
                p for p in _tokenizer.convert_ids_to_tokens(ids)
                if p not in (_tokenizer.pad_token, None)
            ]
            input_tokens.append(pieces)
        kw = dict(
            beam_size=opts["beam"],
            max_decoding_length=max_tokens,
            repetition_penalty=opts["rep"],
        )
        if opts["no_repeat"] > 0:
            kw["no_repeat_ngram_size"] = opts["no_repeat"]
        results = _translator.translate_batch(input_tokens, **kw)
        for j, res in enumerate(results):
            hyp = res.hypotheses[0] if res.hypotheses else []
            try:
                ids = _tokenizer.convert_tokens_to_ids(hyp)
                text = _tokenizer.decode(ids, skip_special_tokens=True)
            except Exception:  # noqa: BLE001
                text = " ".join(hyp).replace("▁", " ").strip()
            out[idxs[j]] = (text or "").strip()
    return out


def draft_rows(
    rows: list[dict],
    *,
    text_key: str = "text",
    plan: dict | None = None,
) -> dict[int, str]:
    if not rows:
        return {}
    ids, zhs = [], []
    for r in rows:
        zh = (r.get(text_key) or r.get("zh") or "").strip()
        if not zh:
            continue
        ids.append(int(r["i"]))
        zhs.append(zh)
    if not zhs:
        return {}
    repo = resolve_model_id(plan)
    vis = translate_many(zhs, plan=plan, repo=repo)
    return {i: vi for i, vi in zip(ids, vis) if vi}
