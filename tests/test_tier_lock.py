"""The tier lock: iris/tier_lock.json pins the tier and escalation of every tool in TOOLS.

A self-built branch must never weaken a safety rule of an existing tool. Adding a tool means adding
a lock line; changing an existing line is visible in the diff (and refused by the guard, item 2).
"""

import json
from pathlib import Path

import pytest

from iris import tools

LOCK = Path(__file__).resolve().parents[1] / "iris" / "tier_lock.json"


def _lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(tools.TOOLS), ids=sorted(tools.TOOLS))
def test_every_tool_is_locked_with_its_tier_and_escalation(name):
    lock = _lock()
    assert name in lock, f"{name} is not in iris/tier_lock.json: add a line for it"
    tool = tools.TOOLS[name]
    entry = lock[name]
    assert entry["tier"] == tool.tier, (
        f"{name}: the lock says tier {entry['tier']!r} but TOOLS has {tool.tier!r}; "
        "a built branch may not weaken an existing rule")
    assert entry["escalates"] is bool(tool.tier_for), (
        f"{name}: the lock says escalates={entry['escalates']} but tier_for is "
        f"{'set' if tool.tier_for else 'not set'}; a built branch may not weaken an existing rule")


@pytest.mark.parametrize("name", sorted(_lock()), ids=sorted(_lock()))
def test_every_lock_entry_is_a_tool_in_tools(name):
    assert name in tools.TOOLS, f"{name} is in iris/tier_lock.json but no longer in TOOLS"
