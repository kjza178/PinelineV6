"""Download FireRedASR2S models into FIRERED_MODELS (mặc định: vendor/FireRedASR2S/pretrained_models — portable)."""
from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download

BASE = Path(os.getenv("FIRERED_MODELS") or Path(__file__).resolve().parents[1] / "vendor" / "FireRedASR2S" / "pretrained_models")
MODELS = [
    ("FireRedTeam/FireRedASR2-AED", "FireRedASR2-AED"),
    ("FireRedTeam/FireRedVAD", "FireRedVAD"),
    ("FireRedTeam/FireRedLID", "FireRedLID"),
    ("FireRedTeam/FireRedPunc", "FireRedPunc"),
]


def main() -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    for repo, name in MODELS:
        dest = BASE / name
        print(f"=== {repo} -> {dest}", flush=True)
        path = snapshot_download(repo_id=repo, local_dir=str(dest))
        print(f"OK {name}: {path}", flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
