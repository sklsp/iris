"""The prompt queue for the agent manager.

Prompts for an agent wait in a JSON file; Iris lists them and hands the next one out only after Jay says yes.
Iris never pastes by itself: pasting stays type_text, which always asks.
"""

from __future__ import annotations

import json
import os
import time
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


def add(agent: str, text: str, now: str = "", path=None) -> int:
    agent = agent.strip()
    text = text.strip()
    if not agent or not text:
        raise ValueError("agent and text are required")
    data = load(path)
    prompt_id = data["next_id"]
    data["prompts"].append(
        {"id": prompt_id, "agent": agent, "text": text, "added": now or time.strftime("%Y-%m-%d %H:%M")}
    )
    data["next_id"] += 1
    save(data, path)
    return prompt_id


def pending(agent: str = "", path=None) -> list[dict]:
    prompts = load(path)["prompts"]
    if agent:
        a = agent.strip().lower()
        prompts = [p for p in prompts if str(p.get("agent", "")).strip().lower() == a]
    return prompts


def take(agent: str, path=None) -> dict:
    data = load(path)
    a = agent.strip().lower()
    for i, p in enumerate(data["prompts"]):
        if str(p.get("agent", "")).strip().lower() == a:
            del data["prompts"][i]
            save(data, path)
            return p
    raise LookupError(f"no prompts waiting for {agent.strip()}")


def listing(agent: str = "", path=None) -> str:
    prompts = pending(agent, path)
    if not prompts:
        return f"No prompts waiting for {agent.strip()}." if agent else "The queue is empty."
    n = len(prompts)
    lines = ["1 prompt waiting:" if n == 1 else f"{n} prompts waiting:"]
    for p in prompts:
        text = str(p.get("text", ""))
        if len(text) > 80:
            text = text[:77] + "..."
        lines.append(f'{p["id"]}. {p.get("agent", "")}: {text}')
    return "\n".join(lines)
