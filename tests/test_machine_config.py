"""2026-11-11 machine-config: key phụ thuộc máy (core/machine.py), preset_switcher, doctor --offline.

Không gọi mạng, không chạy PowerShell/llama: mọi thứ monkeypatch.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from core import machine  # noqa: E402


@pytest.fixture
def cfg(monkeypatch):
    """Giả lập config.get bằng dict (bỏ qua env/pineline.config.json thật của máy)."""
    data: dict[str, str] = {}

    def fake_get(key, default=None):
        v = data.get(key)
        return v if v not in (None, "") else default

    monkeypatch.setattr(machine._config, "get", fake_get)
    return data


# ── mặc định = giá trị cứng cũ ────────────────────────────────────────────────
def test_defaults_keep_old_values(cfg):
    assert machine.llama_base_url() == "http://127.0.0.1:8083/v1"
    assert machine.llama_root_url() == "http://127.0.0.1:8083"
    assert machine.llama_host_port() == ("127.0.0.1", 8083)
    assert machine.presets_dir() == Path(r"E:\models\presets")
    assert machine.model_start_script() == Path(r"E:\models\presets") / "start-model.ps1"
    cmd = machine.model_start_cmd("gemma4-12b")
    assert cmd[:5] == ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(machine.model_start_script())]
    assert cmd[-1] == "gemma4-12b"
    assert machine.hongguo_base_url() == "http://127.0.0.1:8000"
    assert machine.mt_model_dir() == Path(r"E:\models\mt")
    assert machine.library_dir() == Path(r"D:\Lib")
    assert machine.yt_uploader_dir() == Path(r"Z:\yt_uploader")
    assert machine.cloud_llm_base_url() == "http://192.168.31.247:20128/v1"
    assert machine.aurora_base_urls() == [
        "http://127.0.0.1:3377/v1", "http://192.168.31.247:5005/v1", "http://192.168.31.244:5005/v1"]
    assert machine.flag("AUTO_SWITCH_PRESETS")


def test_in_library_matches_old_rule(cfg):
    assert machine.in_library(r"D:\Lib\Phim A\ep1.mp4")
    assert machine.in_library("D:/Lib/Phim A/ep1.mp4")
    assert not machine.in_library(r"E:\Other\ep1.mp4")
    cfg["LIBRARY_DIR"] = r"F:\Kho"
    assert machine.in_library("F:/Kho/x.mp4") and not machine.in_library(r"D:\Lib\x.mp4")


# ── llama host/port suy từ LLM_BASE_URL ─────────────────────────────────────
def test_llama_from_local_llm_base_url(cfg):
    cfg["LLM_BASE_URL"] = "http://localhost:9090/v1"
    assert machine.llama_base_url() == "http://localhost:9090/v1"
    assert machine.llama_host_port() == ("localhost", 9090)


def test_llama_ignores_remote_llm_base_url(cfg):
    """LLM_BASE_URL trỏ 9router/LAN → không phải llama local → giữ :8083 cũ."""
    cfg["LLM_BASE_URL"] = "http://192.168.31.247:20128/v1"
    assert machine.llama_host_port() == ("127.0.0.1", 8083)


def test_llama_base_url_explicit_wins_and_adds_v1(cfg):
    cfg["LLM_BASE_URL"] = "http://127.0.0.1:9090/v1"
    cfg["LLAMA_BASE_URL"] = "http://10.0.0.5:8183"
    assert machine.llama_base_url() == "http://10.0.0.5:8183/v1"
    assert machine.llama_host_port() == ("10.0.0.5", 8183)


# ── presets: bỏ qua khi thiếu / tắt ──────────────────────────────────────────
def test_preset_switch_reasons(cfg, tmp_path):
    cfg["MODEL_PRESETS_DIR"] = str(tmp_path / "khong_co")
    assert "MODEL_PRESETS_DIR" in machine.preset_switch_unavailable_reason()
    d = tmp_path / "presets"
    d.mkdir()
    cfg["MODEL_PRESETS_DIR"] = str(d)
    assert "MODEL_START_SCRIPT" in machine.preset_switch_unavailable_reason()
    (d / "start-model.ps1").write_text("#", encoding="utf-8")
    assert machine.preset_switch_unavailable_reason() == ""
    cfg["AUTO_SWITCH_PRESETS"] = "0"
    assert machine.preset_switch_unavailable_reason() == "AUTO_SWITCH_PRESETS=0"


def test_custom_start_cmd_template(cfg, tmp_path):
    cfg["MODEL_PRESETS_DIR"] = str(tmp_path / "khong_co")
    cfg["LLAMA_BASE_URL"] = "http://127.0.0.1:8090/v1"
    cfg["MODEL_START_CMD"] = "bash /opt/llama/switch.sh {preset} --port {port}"
    assert machine.preset_switch_unavailable_reason() == ""  # lệnh tuỳ ý không cần thư mục presets
    assert machine.model_start_cmd("qwen") == ["bash", "/opt/llama/switch.sh", "qwen", "--port", "8090"]


def test_aurora_fallback_off_and_hint(cfg):
    cfg["AURORA_FALLBACK_URLS"] = "off"
    cfg["CHATGPT_BASE_URL"] = "http://10.0.0.9:3377/v1"
    assert machine.aurora_base_urls() == ["http://10.0.0.9:3377/v1", "http://127.0.0.1:3377/v1"]
    cfg["PLAN_MODEL"] = "chatgpt/gpt-5-6-mini"
    hint = machine.aurora_missing_hint()
    assert "PLAN_MODEL" in hint and "Aurora" in hint


# ── preset_switcher dùng machine, bỏ qua không lỗi ───────────────────────────
@pytest.fixture
def ps(monkeypatch, cfg):
    pytest.importorskip("requests")
    import tools.preset_switcher as ps_mod

    def no_popen(*a, **k):  # đổi preset thật = restart llama dùng chung → cấm trong test
        raise AssertionError("không được gọi subprocess.Popen")

    monkeypatch.setattr(ps_mod.subprocess, "Popen", no_popen)
    return ps_mod


def test_preset_switcher_base_from_config(ps, cfg):
    cfg["LLM_BASE_URL"] = "http://127.0.0.1:9191/v1"
    assert ps._base() == "http://127.0.0.1:9191"


@pytest.mark.parametrize("why_cfg", ["missing_dir", "auto_off"])
def test_switch_skipped_uses_running_model(ps, cfg, monkeypatch, tmp_path, why_cfg):
    if why_cfg == "missing_dir":
        cfg["MODEL_PRESETS_DIR"] = str(tmp_path / "khong_co")
    else:
        cfg["AUTO_SWITCH_PRESETS"] = "0"
    calls = []

    def ready(target_preset=None, ping_inference=True):
        calls.append(target_preset)
        return True

    monkeypatch.setattr(ps, "is_model_ready", ready)
    assert ps._switch_preset_unlocked("gemma4-12b", max_wait_sec=1) is True
    assert calls == [None]  # chỉ hỏi "đang có model chạy không", không đòi đúng preset

    monkeypatch.setattr(ps, "is_model_ready", lambda **k: False)
    assert ps._switch_preset_unlocked("gemma4-12b", max_wait_sec=1) is False


def test_switch_preset_still_blocked_in_pytest(ps):
    """Khoá an toàn cũ (không restart llama dùng chung khi test) vẫn còn."""
    assert ps.switch_preset("gemma4-12b", max_wait_sec=1) is False


# ── example config + doctor ─────────────────────────────────────────────────
def test_example_config_lists_machine_keys():
    d = json.loads((ROOT / "config" / "pineline.config.example.json").read_text(encoding="utf-8"))
    for k in machine.MACHINE_KEYS:
        assert k in d, k
        assert k in d["_machine_keys_help"], k
    assert "sk-" not in json.dumps(d)  # không chứa key thật


def test_doctor_offline_runs():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_env.py"), "--offline", "--no-torch", "--show"],
                       cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode in (0, 1), r.stderr
    assert "Tổng:" in r.stdout and "Traceback" not in r.stderr
    assert "LLAMA_BASE_URL(effective)" in r.stdout
    # không in API key
    for name in ("pineline.config.json", "config.json"):
        p = ROOT / name
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            for k, v in data.items():
                if any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET")) and isinstance(v, str) and len(v) > 8:
                    assert v not in r.stdout, k
