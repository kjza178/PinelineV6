"""Cài đặt AI chạy KHÔNG qua nhà cung cấp API: AI trên máy (llama-server, Ollama) + Aurora.

2026-11-16: trước đây các giá trị này gắn cứng hoặc chỉ đặt được bằng biến môi trường (Ollama :11434 chỉ đọc env
LOCAL_LLM_BASE_URL; số lô song song dùng chung SIMPLE_WORKERS với Aurora; trần output = SIMPLE_MAX_TOKENS; thinking /
num_ctx chỉ env LLM_ENABLE_THINKING / LLM_NUM_CTX; địa chỉ llama-server / Aurora có khoá nhưng không có ô nhập).
Giờ: một cửa đọc / ghi (Cài đặt → AI dịch, /api/v6/ai/local), lưu pineline.config.json; nơi dùng đọc LÚC GỌI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Opt:
    name: str
    key: str
    kind: str            # url | int | bool
    default: object
    lo: int = 0
    hi: int = 0


OPTS: dict[str, Opt] = {o.name: o for o in (
    Opt("llama_url", "LLAMA_BASE_URL", "url", "http://127.0.0.1:8083/v1"),
    Opt("ollama_url", "OLLAMA_BASE_URL", "url", "http://127.0.0.1:11434/v1"),
    Opt("concurrency", "LOCAL_CONCURRENCY", "int", 8, 1, 64),
    Opt("max_tokens", "LOCAL_MAX_TOKENS", "int", 8192, 256, 131072),
    Opt("thinking", "LLM_ENABLE_THINKING", "bool", False),
    Opt("num_ctx", "LLM_NUM_CTX", "int", 8192, 2048, 262144),
    Opt("aurora_url", "AURORA_BASE_URL", "url", ""),
)}

_TRUE = ("1", "true", "yes", "on")


def _raw(o: Opt) -> str:
    from core.config import get
    if o.name == "ollama_url":       # tương thích: env cũ LOCAL_LLM_BASE_URL
        import os
        return str(get(o.key, "") or os.getenv("LOCAL_LLM_BASE_URL") or "").strip()
    return str(get(o.key, "") or "").strip()


def value(name: str):
    o = OPTS[name]
    raw = _raw(o)
    if o.kind == "bool":
        return raw.lower() in _TRUE if raw else bool(o.default)
    if o.kind == "int":
        try:
            return max(o.lo, min(o.hi, int(float(raw)))) if raw else int(o.default)
        except ValueError:
            print(f"[local_ai] {o.key}={raw!r} không phải số → {o.default}", flush=True)
            return int(o.default)
    if not raw:       # chưa đặt → giá trị đang chạy thật (llama: LLM_BASE_URL nếu localhost; Aurora: CHATGPT_BASE_URL / :3377)
        from core import machine
        if name == "llama_url":
            return machine.llama_base_url().rstrip("/")
        if name == "aurora_url":
            return machine.aurora_base_url().rstrip("/")
    return (raw or str(o.default)).rstrip("/")


def is_local_model(model: str | None) -> bool:
    """llama-server (`local`, `llama`, `llama/<tên>`, `auto`) hoặc Ollama (`local/<tên>`)."""
    m = (model or "").strip().lower()
    return m in ("local", "llama", "auto", "default") or m.startswith(("llama/", "local/"))


def snapshot(probe: bool = True) -> dict:
    out = {n: {"key": o.key, "value": value(n), "default": o.default, "set": bool(_raw(o)),
               **({"min": o.lo, "max": o.hi} if o.kind == "int" else {})} for n, o in OPTS.items()}
    if probe:
        try:
            from engines.llm import get_llama_models, get_ollama_models
            out["status"] = {"llama": get_llama_models(value("llama_url")), "ollama": get_ollama_models(value("ollama_url"))}
        except Exception as e:  # noqa: BLE001
            print(f"[local_ai] dò model local lỗi: {e}", flush=True)
            out["status"] = {"llama": [], "ollama": [], "error": str(e)}
    return out


def validate(patch: dict) -> dict:
    """{tên: giá trị | "" (= về mặc định)} → {khoá config: giá trị | None}. Sai → ValueError (không ghi gì)."""
    if not isinstance(patch, dict):
        raise ValueError("local phải là object")
    upd, errs = {}, []
    for n, v in patch.items():
        o = OPTS.get(n)
        if o is None:
            errs.append(f"mục lạ: {n}")
            continue
        s = "" if v is None else str(v).strip()
        if o.kind == "bool":
            upd[o.key] = "1" if (v is True or s.lower() in _TRUE) else None
        elif not s:
            upd[o.key] = None
        elif o.kind == "url":
            if not re.match(r"^https?://[^\s/]+", s):
                errs.append(f"{o.key}: địa chỉ phải bắt đầu bằng http:// hoặc https://")
            else:
                s = s.rstrip("/")
                upd[o.key] = None if s == str(o.default).rstrip("/") else s
        else:
            if not s.isdigit() or not (o.lo <= int(s) <= o.hi):
                errs.append(f"{o.key}: phải là số từ {o.lo} đến {o.hi}")
            else:
                upd[o.key] = None if int(s) == int(o.default) else s
    if errs:
        raise ValueError("; ".join(errs))
    return upd


def apply(patch: dict) -> dict:
    upd = validate(patch)
    if upd:
        from core.config import save_config
        save_config(upd)
    return {"changed": sorted(upd), "local": snapshot()}
