"""Autopilot: notice when a run is done, so Iris can ask Jay before starting the next one.

A run is the newest PROGRESS-*.md in a repo root (by modification time). It counts as done when
every checklist line is ticked or BLOCKED, the tree is clean and the last commit has been quiet
for a few minutes. Nothing here starts anything; it only reports what git says. Read only.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from iris import prompt_queue
from iris.agents import _age, _git
from iris.tools import _escape_keys, _window


def new_task_command() -> str:
    """The command palette entry that opens a Cline task; IRIS_CLINE_NEW_TASK overrides it."""
    return os.environ.get("IRIS_CLINE_NEW_TASK", "Cline: New Task")


def vscode_title() -> str:
    """The VS Code window title to focus before typing; IRIS_VSCODE_TITLE overrides it."""
    return os.environ.get("IRIS_VSCODE_TITLE", "Visual Studio Code")


def cline_keys(text: str) -> list[str]:
    """The exact key strings that open a Cline task and type `text` into it, in order.

    A newline would send the prompt early, so only one line is allowed."""
    if "\n" in text or "\r" in text:
        raise ValueError("one line only")
    return ["^+p", _escape_keys(new_task_command()), "{ENTER}", _escape_keys(text), "{ENTER}"]


def paste_into_cline(text: str, window=None, sleep: Callable[[float], None] = time.sleep) -> str:
    """Open a Cline task in VS Code and type `text` into it. `window` is injectable for tests."""
    if window is None:
        window = _window(vscode_title())
    window.set_focus()
    keys = cline_keys(text)
    command_enter = keys.index("{ENTER}")  # the {ENTER} right after the command, not the one that sends it
    for i, k in enumerate(keys):
        window.type_keys(k, with_spaces=True)
        sleep(2 if i == command_enter else 0.3)  # after the command's {ENTER} the new task must open
    return f"started a Cline task with: {text[:60]}"


def cline_start(agent: str, repo: str = "") -> str:
    """Open a new Cline task and type this agent's next queued prompt into it. The tool layer asks Jay first."""
    if repo:
        done, reason = run_done(Path(repo))
        if not done:
            return f"error: the run in {repo} is not done: {reason}"
    waiting = prompt_queue.pending(agent)
    if not waiting:
        return f"error: no prompts waiting for {agent.strip()}"
    try:
        result = paste_into_cline(waiting[0]["text"])
    except LookupError:
        return "error: VS Code is not open"  # nothing was typed; the queue stays untouched
    prompt_queue.take(agent)  # only after the paste succeeded, so a failure leaves the queue unchanged
    return result



def _newest_progress(repo: Path) -> Path | None:
    """The newest PROGRESS-*.md in the repo root by modification time, or None when there is none."""
    files = [p for p in repo.glob("PROGRESS-*.md") if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _open_items(text: str) -> tuple[int, int]:
    """(open, total) over the checklist lines; a ticked line or one with BLOCKED counts as done."""
    open_ = 0
    total = 0
    for line in text.splitlines():
        if not line.startswith("- ["):
            continue
        total += 1
        if not (line.startswith("- [x]") or "BLOCKED" in line):
            open_ += 1
    return open_, total


def run_done(repo: Path | str, quiet_minutes: int = 3, now: float | None = None) -> tuple[bool, str]:
    """Is the newest PROGRESS-*.md run done? Returns (done, one short sentence saying why)."""
    path = Path(repo)
    progress = _newest_progress(path)
    if progress is None:
        return False, "no PROGRESS file"
    open_, total = _open_items(progress.read_text(encoding="utf-8"))
    if open_:
        return False, f"{open_} of {total} items open in {progress.name}"
    porcelain = _git(path, "status", "--porcelain")
    uncommitted = len([line for line in porcelain.splitlines() if line.strip()])
    if uncommitted:
        return False, f"{uncommitted} uncommitted files"
    try:
        ts = float(_git(path, "log", "-1", "--format=%ct").strip())
    except (RuntimeError, ValueError):
        return False, "no commits yet"
    age = max(0.0, (now if now is not None else time.time()) - ts)
    if age < quiet_minutes * 60:
        return False, f"last commit {_age(age)} ago"
    return True, f"{progress.name} done: {total} items"
