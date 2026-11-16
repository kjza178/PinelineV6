"""Danh sách giọng clone cho Cài đặt → Giọng đọc (registry voices/f5_voices.json + file mẫu wav).

2026-11-16: máy mới không có cây V2 → git mang registry (tên giọng) nhưng không mang wav → giọng «biến mất» không rõ lý
do. Ở đây báo rõ giọng nào dùng được / thiếu file mẫu, và xoá được mục hỏng. Thêm giọng mới: POST /api/clone-voice.
"""
from __future__ import annotations

import json


def list_voices() -> dict:
    import engines.clone_tts as ct
    out = []
    for name, v in ct._load_registry().items():
        ref = ct._resolve_ref((v or {}).get("ref", ""))
        ok = bool(ref and ref.exists())
        out.append({"name": name, "ref": str((v or {}).get("ref") or ""), "ok": ok,
                    "has_text": bool(str((v or {}).get("text") or "").strip()),
                    "source": "VieNeu (giọng có sẵn)" if (v or {}).get("source", "").startswith("vieneu") else "clone"})
    out.sort(key=lambda x: (not x["ok"], x["name"].lower()))
    return {"items": out, "ok": sum(x["ok"] for x in out), "missing": sum(not x["ok"] for x in out),
            "orphans": orphans(), "registry": str(ct.VOICES_FILE)}


def remove(name: str) -> bool:
    """Xoá MỤC trong registry (file mẫu nằm trong voices/clone_refs cũng xoá; file ở chỗ khác giữ nguyên)."""
    import engines.clone_tts as ct
    data = ct._load_registry()
    v = data.pop(name, None)
    if v is None:
        return False
    ref = ct._resolve_ref(v.get("ref", ""))
    tmp = ct.VOICES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(ct.VOICES_FILE)
    clone_dir = (ct._PROJ / "voices" / "clone_refs").resolve()
    try:
        if ref and ref.is_file() and ref.resolve().parent == clone_dir:
            ref.unlink()
    except OSError as e:
        print(f"[voices] xoá file mẫu {ref} lỗi: {e}", flush=True)
    print(f"[voices] đã xoá giọng {name}", flush=True)
    return True


AUDIO_EXT = (".wav", ".mp3", ".m4a", ".flac", ".ogg")
_REF_DIRS = ("clone_refs", "f5_refs", "vieneu_bac")


def orphans() -> list[dict]:
    """File ghi âm nằm trong voices/{clone_refs,f5_refs,vieneu_bac} nhưng CHƯA có tên trong registry
    (vd chép tay V7.wav từ máy cũ) — giao diện cho «Thêm» một lần."""
    import engines.clone_tts as ct
    used = set()
    for v in ct._load_registry().values():
        r = ct._resolve_ref((v or {}).get("ref", ""))
        if r:
            used.add(str(r.resolve()).lower())
    out = []
    for sub in _REF_DIRS:
        d = ct._PROJ / "voices" / sub
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in AUDIO_EXT and str(f.resolve()).lower() not in used:
                out.append({"file": f"voices/{sub}/{f.name}", "name": f.stem, "size": f.stat().st_size})
    return out


def register(file: str, name: str, text: str = "") -> dict:
    """Thêm file mồ côi (phải nằm trong orphans()) vào registry — KHÔNG chép / đổi file."""
    import engines.clone_tts as ct
    name = (name or "").strip()
    if not name:
        raise ValueError("thiếu tên giọng")
    if file not in {o["file"] for o in orphans()}:
        raise ValueError("file không nằm trong thư mục giọng hoặc đã có trong danh sách")
    data = ct._load_registry()
    if name in data:
        raise ValueError(f"đã có giọng tên «{name}»")
    data[name] = {"ref": file, "text": (text or "").strip()}
    tmp = ct.VOICES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(ct.VOICES_FILE)
    print(f"[voices] đã thêm giọng {name} ← {file}", flush=True)
    return {"name": name, "ref": file}
