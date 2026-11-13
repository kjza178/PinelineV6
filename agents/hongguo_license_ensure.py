"""Ensure Hongguo 2.x helper is license-proved (same process as hongguo_rpc).

Uses DPAPI key at %%LOCALAPPDATA%%\\Pineline\\hg_typed_key.dpapi and the WinUI
Hongguo.Licensing stack (HgLicActivate host). Challenge/prove go through a
localhost HTTP bridge into this process's hongguo_rpc helper singleton.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import agents.hongguo_rpc as rpc

_KEY_PATH = Path(os.environ.get("LOCALAPPDATA", "")) / "Pineline" / "hg_typed_key.dpapi"
_KEY_ENTROPY = b"Pineline/hongguo-key/v1"
_LOCAL = Path(os.environ.get("LOCALAPPDATA", ""))


def _resolve_activate_exe() -> Path:
    env = (os.environ.get("HG_LIC_ACTIVATE_EXE") or "").strip()
    if env:
        return Path(env)
    candidates = [
        _LOCAL / "Pineline" / "tools" / "HgLicActivate" / "HgLicActivate.exe",
        Path(r"D:\PinelineV5_tmp\HgLicActivate_patch\HgLicActivate.exe"),
        Path(r"D:\PinelineV2_tmp\HgLicActivate_patch\HgLicActivate.exe"),
        Path(os.environ.get("TEMP", "")) / "HgLicActivate_patch" / "HgLicActivate.exe",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


_ACTIVATE_EXE = _resolve_activate_exe()

_lock = threading.Lock()
_last_ok = 0.0


def _unprotect_key() -> str | None:
    if not _KEY_PATH.is_file():
        return None
    try:
        import clr  # type: ignore  # noqa: F401
    except Exception:
        pass
    # Prefer PowerShell ProtectedData (always present on this machine)
    ps = f"""
Add-Type -AssemblyName System.Security
$raw = [IO.File]::ReadAllBytes('{_KEY_PATH}')
$ent = [Text.Encoding]::UTF8.GetBytes('Pineline/hongguo-key/v1')
$dec = [Security.Cryptography.ProtectedData]::Unprotect($raw, $ent, 'CurrentUser')
[Console]::Out.Write([Text.Encoding]::UTF8.GetString($dec))
"""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True,
            timeout=15,
        )
        key = (out or "").strip()
        return key or None
    except Exception:
        return None


def license_ready() -> bool:
    st = rpc.license_status()
    return str(st.get("state") or "") in ("licensed", "grace")


def has_stored_key() -> bool:
    return _KEY_PATH.is_file() and _KEY_PATH.stat().st_size > 0


def activate_host_ok() -> bool:
    return _resolve_activate_exe().is_file()


def status_public() -> dict[str, Any]:
    """Safe status for UI — no secrets."""
    try:
        st = rpc.license_status()
    except Exception as e:  # noqa: BLE001
        st = {"state": "unknown", "error": str(e)[:80]}
    return {
        "ok": True,
        "state": str(st.get("state") or "none"),
        "until": st.get("until"),
        "has_key": has_stored_key(),
        "activate_host_ok": activate_host_ok(),
        "licensed": str(st.get("state") or "") in ("licensed", "grace"),
    }


def store_key(typed_key: str) -> dict[str, Any]:
    key = (typed_key or "").strip()
    if not key or len(key) < 8:
        return {"ok": False, "error": "invalid_key"}
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write via PowerShell DPAPI — never log key
    tmp = _KEY_PATH.with_suffix(".tmp.txt")
    try:
        tmp.write_text(key, encoding="utf-8")
        tpath = str(tmp).replace("'", "''")
        kpath = str(_KEY_PATH).replace("'", "''")
        ps = (
            "Add-Type -AssemblyName System.Security; "
            f"$plain = [IO.File]::ReadAllText('{tpath}'); "
            "$bytes = [Text.Encoding]::UTF8.GetBytes($plain); "
            "$ent = [Text.Encoding]::UTF8.GetBytes('Pineline/hongguo-key/v1'); "
            "$prot = [Security.Cryptography.ProtectedData]::Protect($bytes, $ent, 'CurrentUser'); "
            f"[IO.File]::WriteAllBytes('{kpath}', $prot)"
        )
        subprocess.check_call(
            ["powershell", "-NoProfile", "-Command", ps],
            timeout=20,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "has_key": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}
    finally:
        try:
            if tmp.is_file():
                tmp.write_text("", encoding="utf-8")
                tmp.unlink(missing_ok=True)
        except OSError:
            pass


def clear_key() -> dict[str, Any]:
    try:
        if _KEY_PATH.is_file():
            _KEY_PATH.unlink()
        return {"ok": True, "has_key": False}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120], "has_key": has_stored_key()}


def _handle_rpc(msg: dict[str, Any]) -> dict[str, Any]:
    cmd = str(msg.get("cmd") or "")
    try:
        if cmd == "status":
            return {"ok": True, "result": rpc.license_status()}
        if cmd == "clear":
            try:
                rpc.call("license.clear", {}, timeout=8)
            except Exception:
                pass
            return {"ok": True}
        if cmd == "challenge":
            purpose = str(msg.get("purpose") or "licence")
            params: dict[str, Any] = {"purpose": purpose}
            if msg.get("seriesId"):
                params["seriesId"] = str(msg["seriesId"])
            return {"ok": True, "result": rpc.call("license.challenge", params, timeout=10)}
        if cmd == "prove":
            params = dict(msg.get("params") or {})
            r = rpc.call("license.prove", params, timeout=15)
            return {"ok": True, "result": r if r is not None else {}}
        return {"ok": False, "error": f"unknown cmd {cmd}"}
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(e)[:200],
            "code": getattr(e, "code", None),
            "data": getattr(e, "data", None),
        }


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            msg = {}
        body = json.dumps(_handle_rpc(msg), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def ensure_licensed(*, force: bool = False) -> dict[str, Any]:
    """Prove into the current hongguo_rpc helper. Returns status dict."""
    global _last_ok
    with _lock:
        if not force and license_ready():
            return {"ok": True, "already": True, "status": rpc.license_status()}

        key = _unprotect_key()
        if not key:
            return {
                "ok": False,
                "error": "missing_key",
                "hint": f"DPAPI key not found at {_KEY_PATH}",
                "status": rpc.license_status(),
            }
        if not _ACTIVATE_EXE.is_file():
            return {
                "ok": False,
                "error": "missing_activate_host",
                "hint": str(_ACTIVATE_EXE),
                "status": rpc.license_status(),
            }

        httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        env = os.environ.copy()
        env["HG_TYPED_KEY"] = key
        env["HG_PROOF_HTTP"] = f"http://127.0.0.1:{port}/"
        try:
            proc = subprocess.run(
                [str(_ACTIVATE_EXE)],
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(_ACTIVATE_EXE.parent),
            )
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            # wipe key from env copy
            env.pop("HG_TYPED_KEY", None)
        finally:
            httpd.shutdown()

        st = rpc.license_status()
        ok = str(st.get("state") or "") in ("licensed", "grace")
        if ok:
            import time as _t

            _last_ok = _t.time()
        return {
            "ok": ok,
            "status": st,
            "activate_exit": proc.returncode,
            "activate_log_tail": "\n".join(
                [
                    ln
                    for ln in out.splitlines()
                    if not any(x in ln.lower() for x in ("hg-", "sig=", "body=", "token"))
                ][-20:]
            ),
        }


def ensure_or_raise() -> None:
    r = ensure_licensed()
    if not r.get("ok"):
        # Không dump activate_log / dict đầy đủ vào message (UI alert nổ dài)
        err = str(r.get("error") or "")
        if not err:
            st = (r.get("status") or {}) if isinstance(r.get("status"), dict) else {}
            err = str(st.get("state") or "prove_failed")
            log = str(r.get("activate_log_tail") or "")
            if "too_many_activations" in log:
                err = "too_many_activations"
        raise rpc.HgRpcError(
            f"Hongguo license prove failed: {err}",
            code=-32010,
            data={
                "reason": "need_license",
                **{
                    k: v
                    for k, v in r.items()
                    if k not in ("activate_log_tail", "status")
                },
            },
        )
