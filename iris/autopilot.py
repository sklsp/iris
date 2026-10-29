"""Autopilot: notice when a run is done, so Iris can ask Jay before starting the next one.

A run is the newest PROGRESS-*.md in a repo root (by modification time). It counts as done when
every checklist line is ticked or BLOCKED, the tree is clean and the last commit has been quiet
for a few minutes. run_done only reads; cline_start starts a session through the Cline CLI and is asked for first.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path

from iris import prompt_queue
from iris.agent import Journal
from iris.agents import _age, _git
from iris.voice import is_yes


def cline_cli() -> str:
    """The Cline CLI executable that starts a session in the background hub; IRIS_CLINE_CLI overrides it."""
    return os.environ.get("IRIS_CLINE_CLI", "W:/Cline/cline-cli.exe")


def cline_provider() -> str:
    """The provider passed with -P to the Cline CLI; IRIS_CLINE_PROVIDER overrides it."""
    return os.environ.get("IRIS_CLINE_PROVIDER", "lmstudio")


def cline_model() -> str:
    """The model passed with -m to the Cline CLI; IRIS_CLINE_MODEL overrides it."""
    return os.environ.get("IRIS_CLINE_MODEL", "qwen3.8-27b")


def cline_cwd() -> str:
    """The working directory a Cline session starts in by default; IRIS_CLINE_CWD overrides it."""
    return os.environ.get("IRIS_CLINE_CWD", "W:/Iris")


def cline_command(text: str, cwd: str) -> list[str]:
    """The argv that starts a Cline CLI session with `text` in `cwd`.

    A multi-line text is allowed; it stays one argv item. Empty or whitespace-only text raises ValueError."""
    if not text.strip():
        raise ValueError("empty prompt")
    return [cline_cli(), "-z", "-c", cwd, "-P", cline_provider(), "-m", cline_model(), text]


def cline_sessions(run=subprocess.run) -> list[dict]:
    """The last ten Cline sessions as dicts, from `history --json`; [] when they cannot be read.

    Non-dict items in the JSON are skipped; a missing CLI, a timeout, bad JSON or a non-list result all give []."""
    try:
        result = run(
            [cline_cli(), "history", "--json", "--limit", "10"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _started_at_epoch(value: object) -> float | None:
    """The epoch seconds of an ISO timestamp (a trailing Z means UTC); None when it cannot be parsed."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _find_started(text: str, t0: float, sessions: list[dict]) -> str | None:
    """The session id of a `cline-cli-zen` entry started at or after t0 - 5 whose prompt contains text, else None."""
    needle = text.strip()[:60]
    for session in sessions:
        if session.get("source") != "cline-cli-zen":
            continue
        started_at = _started_at_epoch(session.get("startedAt"))
        if started_at is None or started_at < t0 - 5:
            continue
        prompt = session.get("prompt")
        if not isinstance(prompt, str) or needle not in prompt:
            continue
        return session["sessionId"]
    return None


def _dispatch(text: str, cwd: str, run=subprocess.run) -> str:
    """Run the Cline CLI command once and return its stderr, "" on timeout; FileNotFoundError raises LookupError."""
    try:
        result = run(
            cline_command(text, cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        raise LookupError(f"Cline CLI not found at {cline_cli()}") from None
    except subprocess.TimeoutExpired:
        return ""  # the session may still have started; polling decides
    return result.stderr or ""


def _poll(text: str, t0: float, sessions, sleep=time.sleep, polls: int = 5) -> str | None:
    """Check the history up to `polls` times, 3 seconds apart; the matching session id, else None."""
    for attempt in range(polls):
        if attempt > 0:
            sleep(3)
        sid = _find_started(text, t0, sessions())
        if sid is not None:
            return sid
    return None


def start_in_hub(
    text: str, cwd: str, run=subprocess.run, sessions=None, now=None, sleep=time.sleep, polls: int = 5
) -> str:
    """Start a Cline session in the background hub and prove it started from the history.

    A timeout of the CLI call is not an error by itself (the session may still have started). FileNotFoundError
    raises LookupError at once; when the first dispatch's polls find nothing, the same command runs once more and
    the history is polled again with the original t0, so a late first session still counts. Only then does it raise
    RuntimeError "Cline did not start a session after two tries: <detail>", where detail is the last non-empty
    stderr line of the second call, or "no output"."""
    if sessions is None:
        sessions = partial(cline_sessions, run=run)
    if now is None:
        now = time.time
    t0 = now()
    stderr = _dispatch(text, cwd, run=run)
    sid = _poll(text, t0, sessions, sleep=sleep, polls=polls)
    if sid is None:
        stderr = _dispatch(text, cwd, run=run)  # the first dispatch may have been lost; one retry before deciding
        sid = _poll(text, t0, sessions, sleep=sleep, polls=polls)
    if sid is not None:
        return f"started Cline session {sid} in {cwd}"
    detail = next((line.strip() for line in reversed(stderr.splitlines()) if line.strip()), "no output")
    raise RuntimeError(f"Cline did not start a session after two tries: {detail}")


def cline_start(agent: str, repo: str = "") -> str:
    """Start this agent's next queued prompt as a Cline session in the background hub. The tool layer asks Jay first."""
    if repo:
        done, reason = run_done(Path(repo))
        if not done:
            return f"error: the run in {repo} is not done: {reason}"
    waiting = prompt_queue.pending(agent)
    if not waiting:
        return f"error: no prompts waiting for {agent.strip()}"
    cwd = repo or cline_cwd()
    try:
        result = start_in_hub(waiting[0]["text"], cwd)
    except (LookupError, RuntimeError) as exc:
        return f"error: {exc}"  # the session did not start; the queue stays untouched
    prompt_queue.take(agent)  # only after the hub started the session, so a failure leaves the queue unchanged
    return result


def watch(
    repo: Path | str,
    agent: str,
    confirm: Callable[[str], bool],
    every: float = 60,
    sleep: Callable[[float], None] = time.sleep,
    start: Callable[[str, str], str] | None = None,
    journal: Journal | None = None,
    max_checks: int | None = None,
) -> str:
    """Wait until the run in `repo` is done, then ask Jay once before starting the agent's next prompt.

    It returns after the first start or the first answer, so it never starts twice. The start callable receives
    (agent, str(repo)) so the new session runs in the repo that just finished. When journal is given, each outcome
    is logged as an "autopilot" event with repo, agent, answer and result. max_checks (tests only) stops the loop
    before any question is asked."""
    if start is None:
        start = cline_start
    checks = 0
    while True:
        checks += 1
        done, reason = run_done(repo)
        if done:
            break
        if max_checks is not None and checks >= max_checks:
            return f"still running after {max_checks} checks"
        sleep(every)
    waiting = prompt_queue.pending(agent)
    if not waiting:
        result = f"nothing queued for {agent}"
        if journal is not None:
            journal.log(event="autopilot", source="cli", repo=str(repo), agent=agent, answer=None, result=result)
        return result
    answer = confirm(f"{repo} is done ({reason}). Start the next {agent} prompt?")
    if not answer:
        result = "Jay said no"
        if journal is not None:
            journal.log(event="autopilot", source="cli", repo=str(repo), agent=agent, answer=False, result=result)
        return result
    result = start(agent, str(repo))  # the new session runs in the repo that just finished
    if journal is not None:
        journal.log(event="autopilot", source="cli", repo=str(repo), agent=agent, answer=True, result=result)
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


def run_progress(repo: Path | str) -> dict:
    """The progress of the newest PROGRESS-*.md run in `repo`: file name, item counts and whether it is done."""
    path = Path(repo)
    progress = _newest_progress(path)
    if progress is None:
        return {
            "repo": str(repo),
            "file": None,
            "done_items": 0,
            "total": 0,
            "done": False,
            "reason": "no PROGRESS file",
        }
    open_, total = _open_items(progress.read_text(encoding="utf-8"))
    done, reason = run_done(path)
    return {
        "repo": str(repo),
        "file": progress.name,
        "done_items": total - open_,
        "total": total,
        "done": done,
        "reason": reason,
    }


def autopilot_main(argv: list[str]) -> int:
    """The CLI behind `python -m iris autopilot`: wait for the run in --repo to finish, ask Jay once, start."""
    ap = argparse.ArgumentParser(prog="iris autopilot", description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--agent", required=True)
    ap.add_argument("--every", type=float, default=60.0)
    a = ap.parse_args(argv)

    def confirm(question: str) -> bool:
        return is_yes(input(f"Iris: {question} (yes/no) "))

    journal = Journal(Path(os.environ.get("IRIS_JOURNAL") or (Path.home() / ".iris" / "journal.jsonl")))
    result = watch(a.repo, a.agent, confirm, every=a.every, journal=journal)
    print(result)
    return 0
