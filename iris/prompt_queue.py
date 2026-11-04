"""The prompt queue for the agent manager.

Prompts for an agent wait in a JSON file; Iris lists them and hands the next one out only after the user says yes.
Iris never pastes by itself: pasting stays type_text, which always asks.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def queue_path() -> Path:
    """Where the queue lives, or ~/.iris/queue.json when IRIS_QUEUE is not set."""
    return Path(os.environ.get("IRIS_QUEUE") or (Path.home() / ".iris" / "queue.json"))


def load(path: Path | None = None) -> dict:
    """Read the saved queue; no file means an empty one, but a broken file raises so nothing waiting in it is lost."""
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


def save(data: dict, path: Path | None = None) -> None:
    """Write the queue atomically through a temp file so a crash never leaves a half-written queue.json behind."""
    p = Path(path) if path else queue_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def add(agent: str, text: str, now: str = "", path: Path | None = None) -> int:
    """Put a prompt at the end of the queue and return its id."""
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


def pending(agent: str = "", path: Path | None = None) -> list[dict]:
    """The prompts still waiting, optionally only those for one agent."""
    prompts = load(path)["prompts"]
    if agent:
        a = agent.strip().lower()
        prompts = [p for p in prompts if str(p.get("agent", "")).strip().lower() == a]
    return prompts


def take(agent: str, path: Path | None = None) -> dict:
    """Remove and return the oldest prompt waiting for an agent, so Iris never hands one out twice."""
    data = load(path)
    a = agent.strip().lower()
    for i, p in enumerate(data["prompts"]):
        if str(p.get("agent", "")).strip().lower() == a:
            del data["prompts"][i]
            save(data, path)
            return p
    raise LookupError(f"no prompts waiting for {agent.strip()}")


def remove(prompt_id: int, path: Path | None = None) -> dict:
    """Drop one prompt by id and return it."""
    data = load(path)
    for i, p in enumerate(data["prompts"]):
        if p.get("id") == prompt_id:
            del data["prompts"][i]
            save(data, path)
            return p
    raise LookupError(f"no prompt with id {prompt_id}")


def move(prompt_id: int, step: int, path: Path | None = None) -> None:
    """Swap a prompt with its neighbor to reorder the queue; step is -1 or 1."""
    if step not in (-1, 1):
        raise ValueError("step must be -1 or 1")
    data = load(path)
    for i, p in enumerate(data["prompts"]):
        if p.get("id") == prompt_id:
            j = i + step
            if 0 <= j < len(data["prompts"]):
                data["prompts"][i], data["prompts"][j] = data["prompts"][j], data["prompts"][i]
                save(data, path)
            return
    raise LookupError(f"no prompt with id {prompt_id}")


def listing(agent: str = "", path: Path | None = None) -> str:
    """The waiting prompts as text for the user, with long ones cut off at 80 characters."""
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


def summary(path: Path | None = None) -> str:
    """A short count of what is waiting per agent, empty when the queue is empty."""
    prompts = load(path)["prompts"]
    if not prompts:
        return ""
    counts: dict[str, int] = {}
    for p in prompts:
        a = str(p.get("agent", "")).strip()
        counts[a] = counts.get(a, 0) + 1
    parts = ", ".join(f"{a} {n}" for a, n in counts.items())
    if len(prompts) == 1:
        return f"Queue: 1 prompt waiting ({parts})."
    return f"Queue: {len(prompts)} prompts waiting ({parts})."
