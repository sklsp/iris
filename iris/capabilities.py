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
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

PROTECTED = (
    "iris/agent.py",
    "iris/capabilities.py",
    "iris/eval.py",
    "iris/policy.py",
    "tests/test_tier_lock.py",
    "tests/test_capabilities.py",
    "tests/test_policy.py",
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


def worktree_for(tool_name: str) -> Path:
    """Where the self-build for tool_name works: IRIS_SELF_WORKTREES, else Iris-self next to the repo."""
    env = os.environ.get("IRIS_SELF_WORKTREES")
    if env:
        return Path(env) / tool_name
    repo = Path(__file__).resolve().parent.parent  # the parent of the iris package
    return repo.parent / "Iris-self" / tool_name


def branch_for(tool_name: str) -> str:
    return f"self/{tool_name}"


def prepare_build(
    tool_name: str,
    repo: Path,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    """Start the self-build for a planned skill in its own worktree and branch.

    The plan must exist with status "planned" (else ValueError), and the worktree folder must not
    exist yet. Runs `git -C <repo> worktree add <worktree> -b self/<tool_name> main`; a non-zero
    return raises RuntimeError with the last stderr line. Copies the plan .md into the worktree as
    BUILD-<tool_name>.md and marks the plan json "building". Never touches the repo's own working
    tree or current branch.
    """
    directory = capabilities_dir()
    md_path = directory / f"{tool_name}.md"
    json_path = directory / f"{tool_name}.json"
    meta = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else None
    if meta is None or meta.get("status") != "planned":
        raise ValueError(f"no planned skill {tool_name}")

    worktree = worktree_for(tool_name)
    if worktree.exists():
        raise ValueError(f"a worktree for {tool_name} already exists")

    argv = ["git", "-C", str(repo), "worktree", "add", str(worktree), "-b", branch_for(tool_name), "main"]
    result = run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        raise RuntimeError(lines[-1] if lines else "git worktree add failed")

    worktree.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(md_path, worktree / f"BUILD-{tool_name}.md")
    meta["status"] = "building"
    meta["worktree"] = str(worktree)
    meta["branch"] = branch_for(tool_name)
    _atomic_write(json_path, json.dumps(meta, ensure_ascii=False) + "\n")
    return worktree


def build_prompt(tool_name: str, worktree: Path) -> str:
    """The text Cline gets for a self-build of tool_name in its own worktree."""
    return (f"Read {worktree}/BUILD-{tool_name}.md and follow it. "
            f"You work in {worktree} on branch self/{tool_name}, made for you; never switch or create branches, "
            f"never push. Write progress to {worktree}/PROGRESS-{tool_name}.md. "
            f"Do NOT call attempt_completion until every step is done.")


GATES = (
    ("ruff", ["-m", "ruff", "check", "iris", "tests"]),
    ("mypy", ["-m", "mypy"]),
    ("pytest", ["-m", "pytest", "-q", "-p", "no:cacheprovider"]),
)


def _git(run: Callable[..., subprocess.CompletedProcess[str]], argv: list[str]) -> str:
    """Run one git command and return its stdout; a non-zero exit raises RuntimeError with the last stderr line."""
    result = run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        raise RuntimeError(lines[-1] if lines else "git failed")
    return result.stdout


def _venv_python(where: Path) -> Path | None:
    """The .venv python of a folder (Windows and POSIX layout), or None when it has none."""
    for parts in ((".venv", "Scripts", "python.exe"), (".venv", "bin", "python")):
        candidate = where.joinpath(*parts)
        if candidate.exists():
            return candidate
    return None


def check_build(
    tool_name: str,
    repo: Path,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict:
    """Guard and gate a self-build branch; store the outcome in the plan json.

    The plan must be "building", "ready" or "blocked" (else ValueError). Changed paths come from
    `git -C <repo> diff --name-only main...self/<tool_name>`; guard_problems compares them with
    PROTECTED and the tier lock on both sides of the branch, plus "no commits yet" when nothing
    changed. Only a clean guard runs the three gates in the worktree (its own .venv python if it
    has one, else the repo's), each with timeout=900; a failing gate adds "<gate> failed: <last
    output line>". The json gets "status" ("ready" or "blocked"), "problems" and "checked"; the
    returned dict is that meta plus "changed".
    """
    directory = capabilities_dir()
    json_path = directory / f"{tool_name}.json"
    meta = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else None
    if meta is None or meta.get("status") not in ("building", "ready", "blocked"):
        raise ValueError(f"no build to check for {tool_name}")

    worktree = Path(meta["worktree"])
    branch = branch_for(tool_name)
    diff_out = _git(run, ["git", "-C", str(repo), "diff", "--name-only", f"main...{branch}"])
    changed = [line.strip() for line in diff_out.splitlines() if line.strip()]
    old_lock = json.loads(_git(run, ["git", "-C", str(repo), "show", "main:iris/tier_lock.json"]))
    new_lock = json.loads(_git(run, ["git", "-C", str(repo), "show", f"{branch}:iris/tier_lock.json"]))

    problems = guard_problems(changed, old_lock, new_lock)
    if not changed:
        problems.append("no commits yet")

    if not problems:
        python = _venv_python(worktree) or _venv_python(repo)
        if python is None:
            raise RuntimeError(f"no .venv python in {worktree} or {repo}")
        for gate, args in GATES:
            result = run([str(python), *args], cwd=str(worktree), capture_output=True, text=True, timeout=900)
            if result.returncode != 0:
                output = (result.stdout + "\n" + result.stderr).strip()
                lines = [line for line in output.splitlines() if line.strip()]
                problems.append(f"{gate} failed: {lines[-1] if lines else ''}")

    meta["status"] = "ready" if not problems else "blocked"
    meta["problems"] = problems
    meta["checked"] = datetime.now().isoformat(timespec="seconds")
    _atomic_write(json_path, json.dumps(meta, ensure_ascii=False) + "\n")
    meta["changed"] = changed
    return meta


def set_plan_field(tool_name: str, field: str, value: str) -> None:
    """Set one field on a saved plan's json (the session id after a start, or status back to planned)."""
    path = capabilities_dir() / f"{tool_name}.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta[field] = value
    _atomic_write(path, json.dumps(meta, ensure_ascii=False) + "\n")

