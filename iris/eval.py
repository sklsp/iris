"""Measures how well the model behind Iris picks tools, with dry-run tools."""
import tomllib
from dataclasses import dataclass, field

from iris.tools import TOOLS


@dataclass
class Task:
    id: str
    request: str
    expect: list[str] = field(default_factory=list)
    forbid: list[str] = field(default_factory=list)
    confirm: str = "no"
    outcomes: dict[str, str] = field(default_factory=dict)
    fixtures: dict[str, str] = field(default_factory=dict)
    max_calls: int = 6


def load_tasks(path) -> list[Task]:
    """Read a TOML file of [[task]] tables and validate every task."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    tasks = []
    seen = set()
    for t in data.get("task", []):
        tid, req = t.get("id"), t.get("request")
        if not tid or not req:
            raise ValueError("task without id or request")
        if tid in seen:
            raise ValueError(f"duplicate task id: {tid}")
        seen.add(tid)
        confirm = t.get("confirm", "no")
        if confirm not in ("yes", "no"):
            raise ValueError(f"confirm must be yes or no in task {tid}")
        expect, forbid = list(t.get("expect", [])), list(t.get("forbid", []))
        outcomes, fixtures = dict(t.get("outcomes", {})), dict(t.get("fixtures", {}))
        for name in [*expect, *forbid, *outcomes, *fixtures]:
            if name not in TOOLS:
                raise ValueError(f"unknown tool in task {tid}: {name}")
        for value in outcomes.values():
            if value not in ("ok", "refused", "declined", "error"):
                raise ValueError(f"unknown outcome in task {tid}: {value}")
        tasks.append(Task(id=tid, request=req, expect=expect, forbid=forbid, confirm=confirm,
                          outcomes=outcomes, fixtures=fixtures, max_calls=int(t.get("max_calls", 6))))
    return tasks
