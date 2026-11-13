"""
AV sync diagnose + heal — phát hiện lệch sub/audio / audio cụt sau mux.

Các class chính:
  audio_truncated  — tiếng chết giữa phim (duck_bgm -shortest cũ)
  elastic_drift    — assemble_fit drift lớn vs hình
  burn_mismatch    — burn sub không theo tts_fit
  duration_gap     — |dur_v - dur_a| lớn (container)
  ok
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import wave
from array import array
from pathlib import Path
from typing import Any, Callable


def _ffprobe_dur(path: Path, stream: str | None = None) -> float | None:
    if not path.is_file():
        return None
    cmd = ["ffprobe", "-v", "error"]
    if stream == "a":
        cmd += ["-select_streams", "a:0", "-show_entries", "stream=duration"]
    elif stream == "v":
        cmd += ["-select_streams", "v:0", "-show_entries", "stream=duration"]
    else:
        cmd += ["-show_entries", "format=duration"]
    cmd += ["-of", "csv=p=0", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return float((r.stdout or "").strip().split("\n")[0])
    except Exception:  # noqa: BLE001
        return None


def _rms_at(video: Path, t: float, dur: float = 1.5) -> float | None:
    if not video.is_file() or t < 0:
        return None
    out = Path(tempfile.gettempdir()) / f"pineline_av_{video.stem}_{int(t)}.wav"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(t), "-t", str(dur),
                "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(out),
            ],
            capture_output=True, timeout=120,
        )
        if not out.is_file() or out.stat().st_size < 500:
            return None
        with wave.open(str(out), "rb") as wf:
            data = wf.readframes(wf.getnframes())
            a = array("h")
            a.frombytes(data)
            if not a:
                return 0.0
            return (sum(x * x for x in a) / len(a)) ** 0.5
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass


def _tts_fit_stats(work: Path) -> dict:
    p = work / "tts_fit.json"
    if not p.is_file():
        return {"ok": False, "error": "no tts_fit.json"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    rows = data if isinstance(data, list) else (data.get("segments") or [])
    drifts = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = r.get("drift")
        if d is None and r.get("start") is not None and r.get("orig_start") is not None:
            try:
                d = float(r["start"]) - float(r["orig_start"])
            except (TypeError, ValueError):
                d = None
        if d is not None:
            drifts.append(abs(float(d)))
    return {
        "ok": True,
        "n": len(rows),
        "max_drift": max(drifts) if drifts else 0.0,
        "n_drift_gt_0_5": sum(1 for x in drifts if x > 0.5),
        "n_drift_gt_2": sum(1 for x in drifts if x > 2),
        "n_drift_gt_10": sum(1 for x in drifts if x > 10),
    }


def _mux_meta(work: Path) -> dict:
    try:
        m = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
        st = (m.get("stages") or {}).get("mux") or {}
        return {
            "status": st.get("status"),
            "audio_mode": st.get("audio_mode") or "",
            "cur_text": st.get("cur_text"),
            "progress": st.get("progress"),
            "dub_file": st.get("dub_file"),
        }
    except Exception:  # noqa: BLE001
        return {}


def diagnose_av_sync(work_root: Path, job_id: str) -> dict:
    work = Path(work_root) / job_id
    if not (work / "manifest.json").is_file():
        return {"ok": False, "error": f"job {job_id} không tồn tại"}

    out = work / "output.mp4"
    source = work / "source.mp4"
    try:
        vp = ((json.loads((work / "manifest.json").read_text(encoding="utf-8")).get("stages") or {})
              .get("download") or {}).get("video_path")
        if vp and Path(vp).is_file():
            source = Path(vp)
        from engines.source_preflight import resolve_source_video   # agentK 2026-11-12: nguồn chuẩn hoá nếu có
        source = resolve_source_video(work) or source
    except Exception:  # noqa: BLE001
        pass
    dub_fit = work / "dub_fit.wav"
    burn = work / "sub.vi.burn.srt"
    soft = work / "sub.vi.srt"

    dur_src = _ffprobe_dur(source)
    dur_out = _ffprobe_dur(out)
    dur_v = _ffprobe_dur(out, "v")
    dur_a = _ffprobe_dur(out, "a")
    dur_dub = _ffprobe_dur(dub_fit)
    fit = _tts_fit_stats(work)
    mux = _mux_meta(work)

    issues: list[dict] = []
    actions: list[dict] = []
    classes: list[str] = []

    if mux.get("status") == "running":
        return {
            "ok": True,
            "job_id": job_id,
            "class": "mux_running",
            "pass": False,
            "issues": [{"code": "mux_running", "detail": mux.get("cur_text") or "mux đang encode"}],
            "actions": [{"action": "wait_mux", "why": "đợi mux xong rồi av_sync_check lại"}],
            "mux": mux,
            "durations": {
                "source": dur_src, "output": dur_out, "video": dur_v,
                "audio": dur_a, "dub_fit": dur_dub,
            },
            "fit": fit,
        }

    if not out.is_file():
        issues.append({"code": "no_output", "detail": "chưa có output.mp4"})
        actions.append({"action": "mux_force", "why": "render lại"})
        return {
            "ok": True, "job_id": job_id, "class": "no_output", "pass": False,
            "issues": issues, "actions": actions, "mux": mux, "fit": fit,
        }

    try:
        from engines.media_ts import MARK, audio_ts_drift
        src_drift = audio_ts_drift(source) if source.is_file() else 0.0
    except Exception:  # noqa: BLE001
        src_drift, MARK = 0.0, ""
    if abs(src_drift) >= 0.3 and MARK and not (work / MARK).is_file():
        issues.append({
            "code": "audio_ts_drift",
            "detail": (f"nguồn ghép nhiều tập dư {src_drift:+.1f}s tiếng so với hình — "
                       "ASR/sub/giọng trễ dần về cuối phim"),
        })
        classes.append("audio_ts_drift")
        actions.append({
            "action": "mux_force",
            "why": "render tự dời mốc job về trục hình (media_ts) rồi ghép lại tiếng",
            "invalidate_mixed": True,
        })

    try:
        from engines.media_ts import MARK, audio_ts_drift
        ts_drift = audio_ts_drift(source) if source.is_file() else 0.0
    except Exception:  # noqa: BLE001
        MARK, ts_drift = "", 0.0
    if abs(ts_drift) >= 0.3 and not (work / MARK).is_file():
        issues.append({
            "code": "audio_ts_drift",
            "detail": (f"nguồn ghép nhiều tập: tiếng dư {ts_drift:+.1f}s so với hình, lệch tăng dần "
                       "→ sub/giọng/tiếng nền trễ dần về cuối phim"),
        })
        classes.append("audio_ts_drift")
        actions.append({
            "action": "mux_force",
            "why": "render lại: tự dời mốc job về trục hình (media_ts) rồi ghép dub_fit + trộn lại",
            "invalidate_mixed": True,
        })

    gap_va = None
    if dur_v is not None and dur_a is not None:
        gap_va = abs(dur_v - dur_a)
        if gap_va > 2.0:
            issues.append({
                "code": "duration_gap",
                "detail": f"|v-a|={gap_va:.1f}s (v={dur_v:.1f} a={dur_a:.1f})",
            })
            classes.append("duration_gap")
            actions.append({
                "action": "mux_force",
                "why": "remux — A/V duration lệch (thường sidechain cũ / audio cụt)",
                "invalidate_mixed": True,
            })

    # Sample RMS: sớm vs giữa vs muộn (bắt audio chết dù container pad silence)
    timeline = dur_src or dur_out or dur_v or 0
    samples = {}
    silent_after = None
    if timeline > 120:
        points = [60.0, timeline * 0.15, timeline * 0.35, timeline * 0.55, timeline * 0.8]
        # điểm quanh 31' (1850) nếu phim dài — pattern duck_bgm cũ
        if timeline > 2000:
            points.append(1900.0)
        for t in points:
            if t >= timeline - 5:
                continue
            samples[round(t, 1)] = _rms_at(out, float(t))
        # tìm mốc đầu tiên RMS~0 sau khi đã có tiếng
        had_sound = False
        for t in sorted(samples):
            rms = samples[t]
            if rms is None:
                continue
            if rms > 50:
                had_sound = True
            elif had_sound and rms is not None and rms < 5:
                silent_after = t
                break
        if silent_after is not None:
            issues.append({
                "code": "audio_dead_mid",
                "detail": (
                    f"RMS≈0 từ ~{silent_after:.0f}s — pattern duck_bgm truncate "
                    f"(BGM loop + -shortest). dub_fit vẫn dài={dur_dub}"
                ),
            })
            classes.append("audio_truncated")
            actions.append({
                "action": "mux_force",
                "why": (
                    "remux duck/duck_bgm two-pass "
                    "(pass1 mixed_audio.wav → pass2 map audio; không sidechain+encode cùng lúc)"
                ),
                "invalidate_mixed": True,
            })

    if fit.get("ok") and float(fit.get("max_drift") or 0) > 10:
        issues.append({
            "code": "elastic_drift",
            "detail": f"max_drift={fit['max_drift']:.1f}s · n>10s={fit.get('n_drift_gt_10')}",
        })
        classes.append("elastic_drift")
        actions.append({
            "action": "assemble_fit_compress",
            "why": "assemble_fit max_speed=1.3 rồi mux lại",
            "max_speed": 1.3,
        })
    elif fit.get("ok") and float(fit.get("max_drift") or 0) > 2:
        issues.append({
            "code": "mild_drift",
            "detail": f"max_drift={fit['max_drift']:.1f}s (nhẹ)",
        })

    # burn vs soft: nếu burn trùng soft timing đầu/cuối trong khi drift lớn → nghi burn sai
    if burn.is_file() and soft.is_file() and fit.get("ok"):
        # chỉ cảnh báo nếu drift lớn và burn tồn tại — render đúng dùng tts_fit
        if "elastic_drift" in classes:
            actions.append({
                "action": "mux_force",
                "why": "đảm bảo burn lại từ tts_fit sau assemble",
            })

    if not classes and not issues:
        classes.append("ok")
        issues.append({"code": "ok", "detail": "A/V + RMS + drift trong ngưỡng"})

    # Dedup actions
    seen = set()
    uniq = []
    for a in actions:
        k = a["action"]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(a)

    primary = classes[0] if classes else "unknown"
    return {
        "ok": True,
        "job_id": job_id,
        "class": primary,
        "classes": classes,
        "pass": primary == "ok" and not [i for i in issues if i.get("code") != "ok"],
        "issues": issues,
        "actions": uniq,
        "mux": mux,
        "durations": {
            "source": dur_src,
            "output": dur_out,
            "video": dur_v,
            "audio": dur_a,
            "dub_fit": dur_dub,
            "gap_va": gap_va,
        },
        "rms_samples": samples,
        "silent_after_sec": silent_after,
        "fit": fit,
        "hint": _hint(primary),
    }


def _hint(cls: str) -> str:
    return {
        "ok": "Không cần heal AV.",
        "audio_truncated": (
            "Remux force two-pass (invalidate mixed_audio + duck/duck_bgm) — không TTS lại."
        ),
        "duration_gap": "Remux force two-pass; kiểm tra lại |v-a|<2s + RMS muộn.",
        "elastic_drift": "Tăng max_speed assemble (1.3) hoặc TTS speed rồi assemble+mux.",
        "audio_ts_drift": "Render lại: render tự dời mốc asr/tts/sub về trục hình (không ASR/TTS lại).",
        "mux_running": "Đợi mux xong.",
        "no_output": "Chạy mux.",
    }.get(cls, "Xem issues/actions.")


def _invalidate_mixed(work: Path) -> list[str]:
    removed: list[str] = []
    for name in ("mixed_audio.wav", "mixed_audio.partial.wav", "output.partial.mp4"):
        p = work / name
        if not p.is_file():
            continue
        try:
            p.unlink()
            removed.append(name)
        except OSError as e:
            removed.append(f"{name}:err:{e}")
    return removed


def _quick_gap(work: Path) -> dict:
    out = work / "output.mp4"
    mux = _mux_meta(work)
    if mux.get("status") == "running":
        return {
            "job_id": work.name, "class": "mux_running", "pass": False,
            "gap_va": None, "mux": mux,
        }
    if not out.is_file():
        return {
            "job_id": work.name, "class": "no_output", "pass": False,
            "gap_va": None, "mux": mux,
        }
    dur_v = _ffprobe_dur(out, "v")
    dur_a = _ffprobe_dur(out, "a")
    gap = abs(dur_v - dur_a) if dur_v is not None and dur_a is not None else None
    if gap is not None and gap > 2.0:
        return {
            "job_id": work.name, "class": "duration_gap", "pass": False,
            "gap_va": round(gap, 1), "video": dur_v, "audio": dur_a, "mux": mux,
            "action": "mux_force",
        }
    return {
        "job_id": work.name, "class": "ok", "pass": True,
        "gap_va": round(gap, 1) if gap is not None else None,
        "video": dur_v, "audio": dur_a, "mux": mux,
    }


def scan_av_sync(
    work_root: Path,
    job_ids: list[str] | None = None,
    *,
    group_id: str | None = None,
    deep: bool = False,
) -> dict:
    """Quét nhiều job — mặc định nhanh (|v-a|). deep=True = diagnose đầy đủ (RMS)."""
    root = Path(work_root)
    ids: list[str] = []
    if job_ids:
        ids = [str(x).strip() for x in job_ids if str(x).strip()]
    elif group_id:
        gid = str(group_id).strip()
        for d in sorted(root.iterdir() if root.is_dir() else []):
            if not (d / "manifest.json").is_file():
                continue
            try:
                m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
                if (m.get("request") or {}).get("group_id") == gid:
                    ids.append(d.name)
            except Exception:  # noqa: BLE001
                continue
    else:
        for d in sorted(root.iterdir() if root.is_dir() else []):
            if (d / "manifest.json").is_file() and (d / "output.mp4").is_file():
                ids.append(d.name)

    rows = []
    broken = []
    running = []
    for jid in ids:
        work = root / jid
        if not (work / "manifest.json").is_file():
            rows.append({"job_id": jid, "class": "missing", "pass": False})
            broken.append(jid)
            continue
        if deep:
            diag = diagnose_av_sync(root, jid)
            row = {
                "job_id": jid,
                "class": diag.get("class"),
                "pass": bool(diag.get("pass")),
                "gap_va": (diag.get("durations") or {}).get("gap_va"),
                "silent_after_sec": diag.get("silent_after_sec"),
                "actions": [a.get("action") for a in (diag.get("actions") or [])],
            }
        else:
            row = _quick_gap(work)
        rows.append(row)
        if row.get("class") == "mux_running":
            running.append(jid)
        elif not row.get("pass"):
            broken.append(jid)

    return {
        "ok": True,
        "deep": deep,
        "n": len(rows),
        "n_broken": len(broken),
        "n_running": len(running),
        "broken": broken,
        "running": running,
        "jobs": rows,
        "hint": (
            "broken → av_sync_heal_batch apply=true (GPU tuần tự). "
            "Đang mux thì đợi rồi scan lại."
        ),
    }


def heal_av_sync(
    work_root: Path,
    job_id: str,
    *,
    apply: bool = False,
    hooks: dict[str, Callable[..., Any]] | None = None,
) -> dict:
    diag = diagnose_av_sync(work_root, job_id)
    if not diag.get("ok"):
        return diag
    if diag.get("class") == "mux_running":
        return {**diag, "applied": False, "note": "mux đang chạy — không heal chồng"}
    if diag.get("pass"):
        return {**diag, "applied": False, "note": "đã OK"}

    plan = list(diag.get("actions") or [])
    if not apply:
        return {"ok": True, "applied": False, "job_id": job_id, "plan": plan, "diagnose": diag}

    hooks = hooks or {}
    results = []
    work = Path(work_root) / job_id
    for step in plan:
        act = step["action"]
        try:
            if act == "wait_mux":
                results.append({"action": act, "result": {"skipped": "wait"}})
            elif act == "assemble_fit_compress":
                fn = hooks.get("assemble_fit")
                ms = float(step.get("max_speed") or 1.3)
                results.append({
                    "action": act,
                    "result": fn(job_id, ms) if fn else {"skipped": "no assemble_fit hook"},
                })
            elif act == "mux_force":
                if step.get("invalidate_mixed"):
                    removed = _invalidate_mixed(work)
                    results.append({"action": "invalidate_mixed", "result": {"removed": removed}})
                fn = hooks.get("mux_force")
                mode = None
                am = (diag.get("mux") or {}).get("audio_mode") or ""
                if "duck_bgm" in am:
                    mode = "duck_bgm"
                elif "voice_bgm" in am:
                    mode = "voice_bgm"
                elif "voice_only" in am:
                    mode = "voice_only"
                elif "duck" in am:
                    mode = "duck"
                results.append({
                    "action": act,
                    "result": fn(job_id, mode) if fn else {"skipped": "no mux_force hook"},
                })
                break
            else:
                results.append({"action": act, "result": {"skipped": "unknown"}})
        except Exception as e:  # noqa: BLE001
            results.append({"action": act, "result": {"error": str(e)}})

    return {
        "ok": True,
        "applied": True,
        "job_id": job_id,
        "results": results,
        "diagnose": diag,
        "next": "đợi mux xong → av_sync_check lại; pass nếu RMS cuối phim > 0 và |v-a|<2s",
    }


def heal_av_sync_batch(
    work_root: Path,
    job_ids: list[str] | None = None,
    *,
    group_id: str | None = None,
    apply: bool = False,
    hooks: dict[str, Callable[..., Any]] | None = None,
    deep: bool = False,
) -> dict:
    """Scan rồi heal job broken đầu tiên (GPU 1 job). Gọi lại sau khi mux xong."""
    scan = scan_av_sync(work_root, job_ids, group_id=group_id, deep=deep)
    if not apply:
        return {
            "ok": True,
            "applied": False,
            "scan": scan,
            "plan": [{"job_id": jid, "action": "mux_force"} for jid in (scan.get("broken") or [])],
        }
    if scan.get("running"):
        return {
            "ok": True,
            "applied": False,
            "note": f"đang mux: {scan['running']} — đợi xong rồi heal_batch lại",
            "scan": scan,
        }
    broken = list(scan.get("broken") or [])
    if not broken:
        return {"ok": True, "applied": False, "note": "không còn job AV broken", "scan": scan}
    target = broken[0]
    one = heal_av_sync(work_root, target, apply=True, hooks=hooks)
    return {
        "ok": True,
        "applied": True,
        "healed_now": target,
        "remaining": broken[1:],
        "result": one,
        "scan": scan,
        "next": (
            f"đợi mux {target} xong → heal_batch apply lại ({len(broken) - 1} còn lại)"
            if len(broken) > 1
            else f"đợi mux {target} xong → scan lại để verify"
        ),
    }
