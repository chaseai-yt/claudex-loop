"""Checkpoint read/write utilities for the slot-machine recovery pattern."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_CHECKPOINT_DIR = _PROJECT_ROOT / "OUTPUTS" / "checkpoints"


def checkpoint_path(step: int) -> Path:
    return _CHECKPOINT_DIR / f"step{step}.json"


def write_checkpoint(step: int, data: Any) -> None:
    _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = checkpoint_path(step)
    payload = data if isinstance(data, (dict, list)) else data
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    elif isinstance(payload, list) and payload and hasattr(payload[0], "model_dump"):
        payload = [item.model_dump(mode="json") for item in payload]
    path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")


def read_checkpoint(step: int) -> Any:
    path = checkpoint_path(step)
    if not path.exists():
        raise FileNotFoundError(
            f"Checkpoint step{step}.json not found. "
            f"--reset-from step{step+1} requires a prior run that produced step{step}.json. "
            "Re-run from step 1 or use --keep-checkpoints on your next run."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def cleanup_checkpoints() -> None:
    for i in range(1, 5):
        p = checkpoint_path(i)
        if p.exists():
            p.unlink()
