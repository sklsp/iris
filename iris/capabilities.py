"""Safety rails for Iris's self-extension: what a self-built branch may never touch or change.

A built branch must never weaken an existing rule or touch the protected files. guard_problems
checks a proposed set of changed paths against PROTECTED and the old tier lock against the new one;
an empty result means the branch is acceptable (Jay still has to say OK before anything merges).

save_plan stores the plan for a skill Iris does not have yet: Jay asks, Iris writes the plan files,
and a later run can build it. The plans live in capabilities_dir().
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

PROTECTED = (
    "iris/agent.py",
    "iris/capabilities.py",
    "iris/eval.py",
    "tests/test_tier_lock.py",
    "tests/test_capabilities.py",
    ".github/",
)


def guard_problems(changed: list[str], old_lock: dict, new_lock: dict) -> list[str]:
    """One problem string per violation; an empty list means the branch is acceptable.

    A changed path that is or starts with a PROTECTED entry (an entry ending in "/" is a folder
    prefix) is refused; paths are compared with forward slashes. Every name in old_lock whose entry
    differs in new_lock or is missing is refused as well; new names in new_lock are fine.
    """
    problems = []
    for path in changed:
        p = path.replace("\\", "/")
        if any(p == entry or p.startswith(entry) for entry in PROTECTED):
            problems.append(f"touches protected path {p}")
    for name, entry in old_lock.items():
        if new_lock.get(name) != entry:
            problems.append(f"changes the tier of {name}")
    return problems


PLAN_TEMPLATE = """# Plan: {tool_name}

- Tier: {tier}
- Status: {status}
- Created: {created}

## Request

{request}

## Plan

{plan}
"""

# The fixed rules every plan carries, so the run that builds it knows the rails. Built from PROTECTED so the two
# can never drift apart; no braces in here, it is appended to PLAN_TEMPLATE before .format().
RULES = "\n".join([
    "## Rules",
    "",
    "- Add the tool to iris/tools.py and TOOLS with exactly the tier above, and add its entry to iris/tier_lock.json.",
    "- Never change the tier, the escalation or the entry of an existing tool.",
    "- Tests go in tests/ and never touch the network, the desktop, LM Studio or files outside tmp_path.",
    "- Append at least one eval task with fixtures to eval/tasks.toml.",
    "- The gates must pass: ruff check iris tests, mypy, pytest with coverage.",
    "- Never touch these protected paths:",
    *[f"  - {p}" for p in PROTECTED],
    "- One commit per step. No em dashes or en dashes.",
    "",
])
PLAN_TEMPLATE += "\n" + RULES


def capabilities_dir() -> Path:
    env = os.environ.get("IRIS_CAPABILITIES")
    if env:
        return Path(env)
    return Path.home() / ".iris" / "capabilities"


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_plan(request: str, tool_name: str, tier: str, plan: str) -> Path:
    """Save the plan for a new skill as <tool_name>.md and <tool_name>.json.

    Raises ValueError when the name, tier or texts are not acceptable or a plan already exists.
    """
    from iris.tools import TOOLS  # local on purpose: tools.py imports this module

    if not re.fullmatch(r"[a-z][a-z0-9_]{2,39}", tool_name):
        raise ValueError(f"invalid tool name {tool_name!r}")
    if tool_name in TOOLS:
        raise ValueError(f"{tool_name} already exists")
    if tier not in ("safe", "confirm"):
        raise ValueError("tier must be 'safe' or 'confirm'")
    if not request:
        raise ValueError("request is empty")
    if not plan:
        raise ValueError("plan is empty")
    if len(plan) > 4000:
        raise ValueError("plan is too long (max 4000 chars)")

    directory = capabilities_dir()
    md_path = directory / f"{tool_name}.md"
    json_path = directory / f"{tool_name}.json"
    if md_path.exists() or json_path.exists():
        raise ValueError(f"a plan for {tool_name} exists")

    created = datetime.now().isoformat(timespec="seconds")
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(md_path, PLAN_TEMPLATE.format(tool_name=tool_name, tier=tier, status="planned",
                                                created=created, request=request, plan=plan))
    meta = {"tool_name": tool_name, "tier": tier, "request": request, "status": "planned",
            "created": created}
    _atomic_write(json_path, json.dumps(meta, ensure_ascii=False) + "\n")
    return md_path


def list_plans() -> str:
    """One line per saved plan from the json files; 'No plans yet.' when there are none."""
    directory = capabilities_dir()
    if not directory.is_dir():
        return "No plans yet."
    lines = []
    for meta_file in sorted(directory.glob("*.json")):
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        asked = str(data.get("request", ""))[:60]
        lines.append(f"{data['tool_name']} ({data['tier']}): {data['status']}, asked: {asked}")
    return "\n".join(lines) if lines else "No plans yet."

