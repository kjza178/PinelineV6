# -*- coding: utf-8 -*-
"""Worker omnivoice-vietnamese (chạy trong D:/OmniVoiceStudio-v2/.venv). Đọc job json:
   {out_dir, ref, text, device, speed, model, lines:[[i,"vi"],...]}
-> mỗi câu ghi seg_XXXX.wav. In "prog <done>/<total>" cho orchestrator.
"""
import json, sys
from pathlib import Path

import torch
import torchaudio
from omnivoice.models.omnivoice import OmniVoice


def main(job_path: str) -> None:
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    out_dir = Path(job["out_dir"]); out_dir.mkdir(parents=True, exist_ok=True)
    ref = job["ref"]; ref_text = job.get("text", "")
    device = job.get("device", "cuda")
    # speed do orchestrator xử lý bằng atempo (đồng nhất 2 engine) -> worker đọc tốc độ gốc
    num_step = int(job.get("num_step", 32))         # nhiều bước hơn = ổn định, ít glitch
    guidance = float(job.get("guidance_scale", 2.0))
    model_id = job.get("model", "splendor1811/omnivoice-vietnamese")
    lines = job["lines"]
    total = len(lines)

    print(f"loading omnivoice device={device} num_step={num_step} guidance={guidance}", flush=True)
    model = OmniVoice.from_pretrained(model_id, device_map=device, dtype=torch.float16)
    sr = int(model.sampling_rate)

    done = 0
    for i, vi in lines:
        try:
            audios = model.generate(text=vi, language="Vietnamese", ref_audio=ref,
                                    ref_text=ref_text, num_step=num_step,
                                    guidance_scale=guidance)
            a = audios[0]
            a = a.cpu() if hasattr(a, "cpu") else a
            torchaudio.save(str(out_dir / f"seg_{i:04d}.wav"), a, sr)
        except Exception as e:  # noqa: BLE001
            print(f"ERR {i}: {repr(e)[:160]}", flush=True)
        done += 1
        print(f"prog {done}/{total}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
