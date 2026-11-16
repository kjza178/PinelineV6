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
            "registry": str(ct.VOICES_FILE)}


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
