"""
preset_switcher.py — Tự động chuyển đổi Preset Model trên llama-server.
Mặc định (máy gốc): llama :8083, presets tại E:\\models\\presets, script start-model.ps1 (PowerShell).

2026-11-11 machine-config: host/cổng/thư mục/lệnh lấy từ `machine` (config):
  LLAMA_BASE_URL (trống = LLM_BASE_URL nếu localhost, không thì :8083), MODEL_PRESETS_DIR,
  MODEL_START_SCRIPT, MODEL_START_CMD, AUTO_SWITCH_PRESETS.
  Máy không có presets / AUTO_SWITCH_PRESETS=0 → KHÔNG đổi preset, KHÔNG lỗi: dùng model đang chạy.

Chức năng:
- Xác định model đang chạy thực tế trên llama-server (qua /v1/models hoặc active.json).
- Tự động chuyển đổi model trước Lượt 1 (Dịch tốc độ cao) và Lượt 2 (Rà soát chuẩn xác).
- ĐẶC BIỆT: Chờ đợi kiên nhẫn (tối đa 180s) cho đến khi AI local nạp VRAM xong,
  vượt qua trạng thái "loading model", và test ping inference 1 token thành công 100%
  rồi mới cho phép pipeline tiếp tục chạy dịch.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
import requests

try:  # V5/V6 package; V4 phẳng
    from core import machine as _m
except ImportError:  # pragma: no cover
    import machine as _m  # type: ignore

# Giữ tên cũ (giá trị lúc import) cho code/test cũ; hàm bên dưới đọc LẠI lúc gọi qua _m.
PRESET_DIR = _m.presets_dir()
ACTIVE_JSON = _m.presets_active_json()
MODELS_JSON = _m.presets_models_json()
START_SCRIPT = _m.model_start_script()
LLAMA_HOST, LLAMA_PORT = _m.llama_host_port()
BASE_URL = _m.llama_root_url()


def _base() -> str:
    return _m.llama_root_url()


def get_active_preset() -> str | None:
    """Trả về ID hoặc alias của model đang thực sự hoạt động trên llama-server."""
    # 1. Ưu tiên kiểm tra trực tiếp từ server đang chạy trong RAM
    try:
        r = requests.get(f"{_base()}/v1/models", headers={"Authorization": "Bearer sk-local"}, timeout=1.5)
        if r.status_code == 200:
            d = r.json()
            data_list = d.get("data") or d.get("models") or []
            if data_list and isinstance(data_list, list):
                mid = data_list[0].get("id") or data_list[0].get("name")
                if mid:
                    return str(mid).strip()
    except Exception:
        pass

    # 2. Đọc từ file active.json
    active_json = _m.presets_active_json()
    if active_json.is_file():
        try:
            data = json.loads(active_json.read_text(encoding="utf-8"))
            return str(data.get("id") or data.get("alias") or "").strip() or None
        except Exception:
            pass
    return None


def list_available_presets() -> list[dict]:
    """Trả về danh sách tất cả các preset có trong models.json."""
    models_json = _m.presets_models_json()
    if not models_json.is_file():
        return []
    try:
        data = json.loads(models_json.read_text(encoding="utf-8"))
        models = data.get("models") or []
        out = []
        for m in models:
            if isinstance(m, dict) and m.get("id"):
                out.append({
                    "id": m["id"],
                    "alias": m.get("alias") or m["id"],
                    "path": m.get("path") or "",
                })
        return out
    except Exception:
        return []


def is_model_ready(target_preset: str | None = None, ping_inference: bool = True) -> bool:
    """Kiểm tra toàn diện xem llama-server đã khởi động xong và sẵn sàng nhận prompt chưa.
    
    Các tiêu chí:
    1. /health trả về HTTP 200 và status == 'ok' (nếu là 'loading model' -> chưa xong).
    2. Nếu có target_preset: kiểm tra xem model đang nạp có đúng là target_preset không.
    3. Gửi test inference mini 1 token: server phải trả về kết quả thành công mà không lỗi.
    """
    # 1. Kiểm tra /health
    try:
        r = requests.get(f"{_base()}/health", timeout=2.0)
        if r.status_code != 200:
            return False
        hdata = r.json()
        if hdata.get("status") != "ok":
            return False
    except Exception:
        return False

    # 2. Kiểm tra đúng model mong muốn
    if target_preset:
        tgt_clean = target_preset.lower().strip()
        active = get_active_preset()
        if not active or tgt_clean not in active.lower():
            # Nếu tên model chưa khớp (có thể server cũ chưa tắt hoặc mới đang nạp dở)
            return False

    # 3. Test ping inference cực ngắn (1 token) để chắc chắn server không bị nghẽn hay out-of-memory
    if ping_inference:
        try:
            payload = {
                "model": target_preset or "local",
                "messages": [{"role": "user", "content": "1"}],
                "max_tokens": 1,
                "stream": False,
            }
            r = requests.post(
                f"{_base()}/v1/chat/completions",
                headers={"Authorization": "Bearer sk-local", "Content-Type": "application/json"},
                json=payload,
                timeout=5.0,
            )
            if r.status_code != 200:
                return False
        except Exception:
            return False

    return True


# fix 2026-11-11 B3: nhiều thread REVIEW∥ + Brain cùng gọi → chỉ 1 lần restart llama-server tại một thời điểm
_SWITCH_LOCK = threading.Lock()


def switch_allowed() -> tuple[bool, str]:
    """V6 (11-11): đổi preset = KHỞI ĐỘNG LẠI llama-server (mặc định :8083) dùng chung với V2 production.
    Cấm khi đang chạy test (pytest) và khi instance tắt cờ preset_switch (v6/test). Chạy trần
    (không có env instance, kiểu V2) thì vẫn cho phép như cũ."""
    if "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
        return False, "đang chạy test"
    v = os.getenv("PINELINE_FLAG_PRESET_SWITCH")
    if v is not None and v.strip().lower() not in ("1", "true", "yes", "on"):
        return False, "instance tắt preset_switch"
    return True, ""


def switch_preset(preset_id: str, max_wait_sec: int = 180) -> bool:
    """Bản có khoá của _switch_preset_unlocked (xem docstring bên dưới)."""
    ok, why = switch_allowed()
    if not ok:
        print(f"[preset_switcher] ⛔ KHÔNG đổi preset '{preset_id}' ({why}) — llama {_base()} dùng chung", flush=True)
        return False
    if not _SWITCH_LOCK.acquire(timeout=max(30, int(max_wait_sec) + 60)):
        print(f"[preset_switcher] ⏳ Đang có lượt switch khác quá lâu — bỏ qua '{preset_id}'", flush=True)
        return False
    try:
        return _switch_preset_unlocked(preset_id, max_wait_sec)
    finally:
        _SWITCH_LOCK.release()


def _use_running_model(preset_id: str, why: str) -> bool:
    """Không đổi preset được (KHÔNG coi là lỗi): True nếu llama-server đang chạy sẵn sàng."""
    if is_model_ready(target_preset=None, ping_inference=True):
        print(f"[preset_switcher] ⏭ Bỏ qua đổi preset '{preset_id}' ({why}) — dùng model đang chạy trên {_base()}", flush=True)
        return True
    print(f"[preset_switcher] ❌ Không đổi preset '{preset_id}' ({why}) và llama-server {_base()} chưa sẵn sàng. "
          "Bật llama-server / sửa LLAMA_BASE_URL, hoặc đổi model sang LLM khác.", flush=True)
    return False


def _switch_preset_unlocked(preset_id: str, max_wait_sec: int = 180) -> bool:
    """Chuyển đổi llama-server sang preset mong muốn và CHỜ ĐỢI cho đến khi AI mở lại hoàn toàn.
    
    Args:
        preset_id: ID của preset (vd: 'gemma4-12b-qat-mtp-fast', 'gemma4-26b-qat-uncen-mtp')
        max_wait_sec: Thời gian tối đa kiên nhẫn chờ server khởi động và nạp VRAM (mặc định 180s = 3 phút).
        
    Returns:
        True nếu chuyển thành công và AI local đã OPEN hoàn toàn, sẵn sàng dịch.
        False nếu hết thời gian chờ hoặc gặp lỗi.
    """
    preset_id = (preset_id or "").strip()
    if not preset_id:
        return False

    # machine-config: máy không có presets / AUTO_SWITCH_PRESETS=0 → bỏ qua, dùng model đang chạy
    why = _m.preset_switch_unavailable_reason()
    if why:
        return _use_running_model(preset_id, why)

    # Kiểm tra nếu preset mong muốn đã đang chạy sẵn và hoạt động trơn tru
    if is_model_ready(target_preset=preset_id, ping_inference=True):
        print(f"[preset_switcher] ✅ Model '{preset_id}' đã đang mở sẵn sàng trên {_base()}.", flush=True)
        return True


    current = get_active_preset() or "chưa rõ"
    print(f"\n[preset_switcher] 🔄 Đang kích hoạt chuyển AI Local: '{current}' ➔ '{preset_id}'...", flush=True)

    cmd = _m.model_start_cmd(preset_id)
    pdir = _m.presets_dir()

    try:
        # Chạy lệnh đổi preset (mặc định script PowerShell) (chạy async không chặn để Python chủ động loop thăm dò)
        subprocess.Popen(
            cmd,
            cwd=str(pdir) if pdir.is_dir() else None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[preset_switcher] ❌ Lỗi khi gọi script khởi động model: {e}", flush=True)
        return False

    # Chờ đợi kiên nhẫn: server cũ tắt -> server mới bật -> nạp VRAM -> /health ok -> test ping ok
    t0 = time.time()
    # Chờ 3s đầu tiên để tiến trình cũ bị kill và giải phóng port
    time.sleep(3.0)

    print(f"[preset_switcher] ⏳ Đang chờ AI Local '{preset_id}' khởi động và nạp vào VRAM (tối đa {max_wait_sec}s)...", flush=True)

    last_log_time = t0
    while time.time() - t0 < max_wait_sec:
        time.sleep(2.5)
        elapsed = time.time() - t0

        # Kiểm tra xem AI local đã hoàn toàn mở và sẵn sàng nhận prompt chưa
        if is_model_ready(target_preset=preset_id, ping_inference=True):
            print(f"\n[preset_switcher] 🚀 AI Local '{preset_id}' ĐÃ MỞ THÀNH CÔNG VÀ SẴN SÀNG! (Mất {elapsed:.1f}s)", flush=True)
            print("[preset_switcher] Bắt đầu chạy tiến trình dịch...\n", flush=True)
            return True

        # Log tiến trình mỗi 8 giây để người dùng nắm được trạng thái
        if time.time() - last_log_time >= 8.0:
            last_log_time = time.time()
            # Xem server đã phản hồi ở mức nào
            state_desc = "đang khởi tạo tiến trình"
            try:
                r = requests.get(f"{_base()}/health", timeout=1.0)
                if r.status_code == 200:
                    status_text = r.json().get("status", "")
                    if status_text == "ok":
                        state_desc = "đang nạp trọng số model vào VRAM"
                    else:
                        state_desc = f"trạng thái: {status_text}"
                else:
                    state_desc = f"HTTP {r.status_code} (đang nạp GGUF)"
            except Exception:
                state_desc = "đang giải phóng VRAM / mở port"
            print(f"  [chờ AI Local] {elapsed:.0f}s / {max_wait_sec}s — {state_desc}...", flush=True)

    print(f"\n[preset_switcher] ❌ Quá thời gian chờ ({max_wait_sec}s) cho model '{preset_id}'!", flush=True)
    return False
