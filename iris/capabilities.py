"""Safety rails for Iris's self-extension: what a self-built branch may never touch or change.

A built branch must never weaken an existing rule or touch the protected files. guard_problems
checks a proposed set of changed paths against PROTECTED and the old tier lock against the new one;
an empty result means the branch is acceptable (Jay still has to say OK before anything merges).
"""

from __future__ import annotations

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
