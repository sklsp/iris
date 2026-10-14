"""The prompt queue for the agent manager.

Prompts for an agent wait in a JSON file; Iris lists them and hands the next one out only after Jay says yes.
Iris never pastes by itself: pasting stays type_text, which always asks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def queue_path() -> Path:
    return Path(os.environ.get("IRIS_QUEUE") or (Path.home() / ".iris" / "queue.json"))


def load(path=None) -> dict:
    p = Path(path) if path else queue_path()
    if not p.exists():
        return {"next_id": 1, "prompts": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"the queue file is broken: {p}") from e
    prompts = data.get("prompts") if isinstance(data, dict) else None
    next_id = data.get("next_id") if isinstance(data, dict) else None
    # Never silently start over: a broken file would lose the prompts waiting in it.
    if not isinstance(prompts, list) or not isinstance(next_id, int):
        raise ValueError(f"the queue file is broken: {p}")
    return data


def save(data: dict, path=None) -> None:
    p = Path(path) if path else queue_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
