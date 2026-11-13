"""core/instance.py — Cấu hình 1 instance Pineline (thay cho việc giữ 3 cây V2/V4/V5).

Một codebase, nhiều instance: mỗi instance là 1 file `instances/<tên>.json`
(cổng, thư mục work, TEMP, router bật, desktop, agent). Server, Tauri và script ops đọc
cùng file nên không còn hằng số cổng/đường dẫn rải trong code.

Chọn instance: tham số `--instance`, hoặc env `PINELINE_INSTANCE`, mặc định `v6`.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTANCES_DIR = ROOT / "instances"


@dataclass(frozen=True)
class Instance:
    name: str
    port: int
    work_root: Path
    temp: Path
    host: str = "127.0.0.1"
    routers: tuple[str, ...] = ()
    desktop: bool = False
    agent_brain: bool = False
    agent_watch: bool = False
    telegram: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def sidecar_port(self) -> int:
        return int(self.extra.get("sidecar_port") or self.port + 10)

    def apply_env(self) -> None:
        """Đẩy giá trị instance vào env cho module cũ + tiến trình con (engine, render worker)."""
        os.environ["PINELINE_INSTANCE"] = self.name
        os.environ["PINELINE_PORT"] = str(self.port)
        os.environ["PINELINE_BASE"] = self.base_url
        os.environ["PORT"] = str(self.port)
        os.environ["TEMP"] = os.environ["TMP"] = str(self.temp)
        # Code V5 dùng ROOT/"work" (junction → work_root) hoặc WORK_DIR; cả hai trỏ cùng chỗ.
        os.environ["WORK_DIR"] = os.environ["PINELINE_WORK_ROOT"] = str(self.work_root)
        # Sidecar riêng mỗi instance: không bao giờ gọi nhầm sidecar :8802 của V2.
        sc = self.sidecar_port
        os.environ["AUTOPILOT_PORT"] = str(sc)
        os.environ["AUTOPILOT_URL"] = f"http://{self.host}:{sc}"
        os.environ.setdefault("AGENT_BRAIN", "1" if self.agent_brain else "0")
        os.environ.setdefault("AGENT_WATCH", "1" if self.agent_watch else "0")
        # V6 F3: cờ instance cho các khởi động nền của server.py (legacy) — server đọc qua
        # server._instance_flag(). Ghi ĐÈ (không setdefault): config.json có AGENT_BRAIN=1 kiểu
        # "file thắng env" nên chỉ env AGENT_* KHÔNG đủ để tắt; cờ này là công tắc cứng.
        for name, on in self.flags().items():
            os.environ[f"PINELINE_FLAG_{name.upper()}"] = "1" if on else "0"
        if not self.telegram:
            os.environ["AGENT_TELEGRAM"] = "0"      # agents.agent_watch.telegram_send đọc env này
        if not self.agent_brain:
            os.environ["AGENT_BRAIN"] = "0"
        if not self.agent_watch:
            os.environ["AGENT_WATCH"] = "0"
        os.environ["PINELINE_ROUTERS"] = ",".join(self.routers)
        # F6 port 11-12: chọn luồng dịch theo instance (chỉ khi instance ghi rõ — không rò env khi thiếu khoá).
        if "translate_pipeline" in self.extra:
            os.environ["PINELINE_TRANSLATE_PIPELINE"] = self.translate_pipeline
        # port Q6 11-13: preflight/chuẩn hoá video nguồn — chỉ khi instance ghi rõ khoá (mặc định trong
        # engines/source_preflight: VIDEO_PREFLIGHT=1, VIDEO_NORMALIZE=1). Env VIDEO_* đặt tay vẫn thắng.
        for k in ("video_preflight", "video_normalize"):
            if k in self.extra:
                os.environ[f"PINELINE_FLAG_{k.upper()}"] = "1" if bool(self.extra.get(k)) else "0"

    def flags(self) -> dict[str, bool]:
        """Công tắc dịch vụ nền. auto_publish / hg_merge_resume / title_translate / queue_autostart /
        render_subprocess / feeder nằm trong `extra` (mặc định TẮT)."""
        return {
            "agent_brain": self.agent_brain,
            "agent_watch": self.agent_watch,
            "telegram": self.telegram,
            "auto_publish": bool(self.extra.get("auto_publish", False)),
            "hg_merge_resume": bool(self.extra.get("hg_merge_resume", False)),
            # review1: dịch tiêu đề gọi LLM (:8083 dùng chung V2) — mặc định TẮT ngoài prod.
            "title_translate": bool(self.extra.get("title_translate", False)),
            # review1: khởi động mà _job_queue.json còn job → tự chạy (GPU). Mặc định TẮT ngoài prod.
            "queue_autostart": bool(self.extra.get("queue_autostart", False)),
            # F5: render/mux chạy workers/render_worker.py (tiến trình con, không importlib.reload).
            "render_subprocess": bool(self.extra.get("render_subprocess", False)),
            # F4: vòng nền autopilot/feeder/runwatch/film_db/config-live chạy TRONG server (services/).
            # Sidecar chạy theo instance thì không tự chạy các vòng này nữa (chỉ Telegram + HTTP mỏng).
            "feeder": bool(self.extra.get("feeder", False)),
            # 11-11: đổi preset llama = restart :8083 dùng chung V2 → chỉ prod được làm.
            "preset_switch": bool(self.extra.get("preset_switch", False)),
            # F6 port 11-12: Demucs tách giọng TRƯỚC ASR (ASR trên vocals.wav, lỗi → fallback audio gốc).
            # MẶC ĐỊNH BẬT (khác các cờ trên). engines/asr/asr.asr_use_demucs đọc PINELINE_FLAG_ASR_USE_DEMUCS
            # (env ASR_USE_DEMUCS đặt tay vẫn thắng). Rollback: "asr_use_demucs": false.
            "asr_use_demucs": bool(self.extra.get("asr_use_demucs", True)),
        }

    @property
    def translate_pipeline(self) -> str:
        """F6 port 11-12: luồng dịch — "simple" (mặc định, engines/translate_simple) | "legacy" (rollback:
        run_translate + run_review). apply_env → PINELINE_TRANSLATE_PIPELINE (đọc ở translate_simple.cfg)."""
        v = str(self.extra.get("translate_pipeline") or "simple").strip().lower()
        return v if v in ("simple", "legacy") else "simple"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["work_root"], d["temp"] = str(self.work_root), str(self.temp)
        return d


def load(name: str | None = None, *, directory: Path | None = None) -> Instance:
    name = (name or os.getenv("PINELINE_INSTANCE") or "v6").strip()
    path = (directory or INSTANCES_DIR) / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Không có instance '{name}' ({path})")
    raw = json.loads(path.read_text(encoding="utf-8"))
    known = {"name", "port", "work_root", "temp", "host", "routers", "desktop", "agent_brain",
             "agent_watch", "telegram"}
    return Instance(
        name=raw.get("name", name),
        port=int(raw["port"]),
        work_root=Path(raw["work_root"]),
        temp=Path(raw.get("temp") or os.getenv("TEMP") or "."),
        host=raw.get("host", "127.0.0.1"),
        routers=tuple(raw.get("routers") or ()),
        desktop=bool(raw.get("desktop", False)),
        agent_brain=bool(raw.get("agent_brain", False)),
        agent_watch=bool(raw.get("agent_watch", False)),
        telegram=bool(raw.get("telegram", False)),
        extra={k: v for k, v in raw.items() if k not in known},
    )


# ───────────── F4: cổng/URL cho code chạy ngoài app (agents, sidecar, tools) ─────────────
# Thứ tự: env do apply_env đặt (PINELINE_BASE / PINELINE_PORT / AUTOPILOT_*) → instance mặc định
# (PINELINE_INSTANCE hoặc v6). KHÔNG BAO GIỜ rơi về 8801/8802 (V2 production).
DEFAULT_INSTANCE = "v6"


def _default_instance() -> Instance | None:
    try:
        return load(os.getenv("PINELINE_INSTANCE") or DEFAULT_INSTANCE)
    except Exception:  # noqa: BLE001
        return None


def server_port() -> int:
    for k in ("PINELINE_PORT",):
        v = (os.getenv(k) or "").strip()
        if v.isdigit():
            return int(v)
    inst = _default_instance()
    if inst is None:
        raise RuntimeError("không xác định được cổng Pineline (thiếu PINELINE_PORT và instances/v6.json)")
    return inst.port


def server_base() -> str:
    """URL server Pineline của instance hiện tại (thay mọi URL cổng V2 cứng làm mặc định)."""
    b = (os.getenv("PINELINE_BASE") or "").strip().rstrip("/")
    if b:
        return b
    return f"http://127.0.0.1:{server_port()}"


def sidecar_port() -> int:
    v = (os.getenv("AUTOPILOT_PORT") or "").strip()
    if v.isdigit():
        return int(v)
    inst = _default_instance()
    if inst is None:
        return server_port() + 10
    return inst.sidecar_port


def sidecar_url() -> str:
    u = (os.getenv("AUTOPILOT_URL") or "").strip().rstrip("/")
    return u or f"http://127.0.0.1:{sidecar_port()}"


# ───────────── review2 (F4/F5): nhận diện tiến trình + cổng trong HTML theo instance ─────────────
# Cổng của tiến trình KHÔNG thuộc V6 (V2 production :8801/sidecar :8802, Hongguo :8000, llama :8083).
# Script ops (restart_server, migrate_work) từ chối nhắm vào các cổng này trừ khi chủ động cho phép.
FOREIGN_PORTS = frozenset({8000, 8083, 8801, 8802})


def _arg_after(toks: list[str], flag: str) -> str | None:
    for i, t in enumerate(toks):
        if t == flag and i + 1 < len(toks):
            return toks[i + 1]
        if t.startswith(flag + "="):
            return t.split("=", 1)[1]
    return None


def server_cmd_matches(cmdline, *, port: int, instance: str | None = None) -> bool:
    """cmdline (list) có phải backend Pineline của ĐÚNG instance/cổng này không.

    Nhận: `python [-X utf8] -m app --instance <tên> [--port N]` (run_v6.bat, Tauri) và
    `python server.py --port N` (kiểu V5). So TOKEN (không so chuỗi con: "--port 8806" ≠ "--port 88061"),
    nên không bao giờ khớp server của cây khác chạy cổng khác (V2 :8801)."""
    toks = [str(x) for x in (cmdline or [])]
    low = [t.lower() for t in toks]
    is_app = any(low[i] == "-m" and i + 1 < len(low) and low[i + 1] == "app" for i in range(len(low)))
    is_srv = any(Path(t.replace("\\", "/")).name == "server.py" for t in low)
    if not (is_app or is_srv):
        return False
    p = _arg_after(toks, "--port")
    if p is not None:
        return p.strip() == str(port)
    if is_app:
        name = _arg_after(toks, "--instance") or "v6"
        if instance is not None and name != instance:
            return False
        try:
            return load(name).port == int(port)
        except Exception:  # noqa: BLE001
            return False
    return False


_HTML_HOSTS = ("http://127.0.0.1:", "http://localhost:")
_V2_SERVER, _V2_SIDECAR = 8801, 8802     # cổng V2 production còn cứng trong HTML đồng bộ từ V2


def localize_html(text: str, *, server: str | None = None, sidecar: str | None = None) -> str:
    """UI HTML cũ (index/index3/hongguo/films_ui) còn URL cứng V2 `:8801` (server) / `:8802` (sidecar).
    Phục vụ trong V6 mà không đổi → UI V6 bấm Auto-pilot/Lưu config/Dừng sẽ gọi SIDECAR V2 production.
    Đổi lúc phục vụ: `:8802` → sidecar của instance; `:8801` → `server` ("" = cùng origin, đường tương đối).
    Không sửa file HTML gốc (đồng bộ từ V2)."""
    if not text:
        return text
    sc = (sidecar if sidecar is not None else sidecar_url()).rstrip("/")
    sv = (server or "").rstrip("/")
    for h in _HTML_HOSTS:
        text = text.replace(f"{h}{_V2_SIDECAR}", sc).replace(f"{h}{_V2_SERVER}", sv)
    # Nhãn/thông báo còn ghi ":8802"/":8801" (vd «Sidecar :8802 tắt») → cổng thật của instance,
    # để người dùng không bị dẫn tới tiến trình V2 (verify_all 11-11: /ui3 còn ":8802" dạng chữ).
    sc_port = sc.rsplit(":", 1)[-1] if sc.rsplit(":", 1)[-1].isdigit() else str(sidecar_port())
    text = text.replace(f":{_V2_SIDECAR}", f":{sc_port}")
    text = text.replace(f":{_V2_SERVER}", f":{server_port()}")
    return text
