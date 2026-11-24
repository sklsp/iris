"""The agent manager: what are my other agents up to? Read only, safe by construction.

Reads ~/.iris/agents.toml (else the shipped iris/agents.toml) for the repo list, then for each git repo
reports the branch, the last commit with its age, the number of uncommitted files, whether BLOCKED.md
exists, and a STUCK flag when there are uncommitted changes but nothing committed for over an hour.

Everything here only runs git commands that cannot write: rev-parse, log, status. No checkout, no
commit, no add.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_USER_TOML = Path.home() / ".iris" / "agents.toml"   # the user's own list wins over the shipped example
CONFIG = Path(os.environ.get("IRIS_AGENTS_TOML")
              or (_USER_TOML if _USER_TOML.exists() else Path(__file__).with_name("agents.toml")))
DEFAULT_REPOS: list[str] = []
DEFAULT_WORKTREES = [str(Path.home() / ".cline" / "worktrees")]
DEFAULT_STUCK_MINUTES = 60
GIT_TIMEOUT = 10  # seconds; a repo on a spinning disk must not hang the voice loop
NIGHTSHIFT_TIMEOUT = 20  # seconds; a hung status call must not hang the agents view either


def _default_repos() -> list[str]:
    """Repo paths from IRIS_REPOS (';' separated); the built-in defaults when unset."""
    raw = os.environ.get("IRIS_REPOS")
    if not raw:
        return list(DEFAULT_REPOS)
    return [s.strip() for s in raw.split(";") if s.strip()]


@dataclass
class RepoStatus:
    name: str
    path: str
    branch: str = "?"
    last_commit: str = ""       # subject of the most recent commit, empty when there is none
    commit_age: float | None = None  # seconds since that commit, None when there is no commit
    uncommitted: int = 0
    blocked: bool = False
    stuck: bool = False
    error: str = ""

    @property
    def exists(self) -> bool:
        """Whether git could be read for this repo; when it could not, `error` says why."""
        return not self.error

    def spoken(self) -> str:
        """One short sentence per repo, like 'Acme: last commit 12 minutes ago.'"""
        if not self.exists:
            return f"{self.name}: {self.error}."
        if self.stuck:
            parts = [f"stuck for {_age(self.commit_age)} with {self.uncommitted} uncommitted"]
        elif self.last_commit:
            ago = "just now" if (self.commit_age or 0) < 10 else f"{_age(self.commit_age)} ago"
            parts = [f"last commit {ago}"]
            if self.uncommitted:
                parts.append(f"{self.uncommitted} uncommitted")
        else:
            parts = ["no commits yet"]
        if self.blocked:
            parts.append("BLOCKED.md exists")
        return f"{self.name}: {', '.join(parts)}."


@dataclass
class Config:
    repos: list[str] = field(default_factory=_default_repos)
    worktrees: list[str] = field(default_factory=lambda: list(DEFAULT_WORKTREES))
    stuck_minutes: int = DEFAULT_STUCK_MINUTES
    nightshift_command: list[str] = field(default_factory=list)  # empty = the active-run glance is off
    nightshift_runs: list[str] = field(default_factory=list)     # folders that hold run dirs (a run.toml inside)


def load_config(path: Path | str | None = None) -> Config:
    """Read agents.toml; a missing or unreadable file falls back to the built-in defaults."""
    p = Path(path) if path else CONFIG
    try:
        with p.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return Config()
    cfg = Config()
    if isinstance(data.get("repos"), list):
        cfg.repos = [str(x) for x in data["repos"]]
    if isinstance(data.get("worktrees"), list):
        cfg.worktrees = [str(x) for x in data["worktrees"]]
    if isinstance(data.get("stuck_minutes"), (int, float)) and data["stuck_minutes"] > 0:
        cfg.stuck_minutes = int(data["stuck_minutes"])
    if isinstance(data.get("nightshift_command"), list):
        cfg.nightshift_command = [str(x) for x in data["nightshift_command"]]
    if isinstance(data.get("nightshift_runs"), list):
        cfg.nightshift_runs = [str(x) for x in data["nightshift_runs"]]
    return cfg


def discover(cfg: Config | None = None) -> list[tuple[str, Path]]:
    """The repos to report on: the explicit list, then every git repo inside the worktree roots.

    A Cline worktree is <root>/<hash>/<project>, so walk two levels deep. The project name is used,
    with the worktree hash in brackets so several worktrees of the same project stay tellable apart.
    """
    cfg = cfg or load_config()
    found: list[tuple[str, Path]] = []
    for raw in cfg.repos:
        p = Path(raw)
        found.append((p.name or raw, p))
    for root in cfg.worktrees:
        base = Path(root)
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            candidates = [child] if (child / ".git").exists() else list(child.iterdir())
            for repo in candidates:
                if repo.is_dir() and (repo / ".git").exists():
                    name = repo.name or repo.as_posix()
                    found.append((f"{name} [{child.name}]" if repo.parent != base else name, repo))
    return found


def _git(repo: Path, *args: str) -> str:
    """Run a read-only, lock-free git command in `repo`. Raises on failure so the caller can report it."""
    cmd = ["git", "--no-optional-locks", "-C", str(repo), *args]  # never takes the index lock
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=GIT_TIMEOUT, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "git failed").strip().splitlines()[-1])
    return proc.stdout


def repo_status(repo: Path | str, stuck_minutes: int = DEFAULT_STUCK_MINUTES, now: float | None = None) -> RepoStatus:
    """Everything Iris knows about one repo, from git only. Never writes."""
    path = Path(repo)
    status = RepoStatus(name=path.name or str(path), path=str(path))
    if not (path / ".git").exists():
        status.error = "not a git repo"
        return status
    try:
        status.branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD").strip() or "?"
        log = _git(path, "log", "-1", "--format=%ct%x1f%s").strip()
        if log:
            ts, subject = log.split("\x1f", 1)
            status.commit_age = max(0.0, (now if now is not None else time.time()) - float(ts))
            status.last_commit = subject
        porcelain = _git(path, "status", "--porcelain")
        status.uncommitted = len([line for line in porcelain.splitlines() if line.strip()])
        status.blocked = (path / "BLOCKED.md").exists()
        status.stuck = (status.uncommitted > 0 and status.commit_age is not None
                        and status.commit_age > stuck_minutes * 60)
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
        status.error = f"git failed: {exc}"
    return status


def _newest_run_dir(runs: list[str]) -> Path | None:
    """The most recently active nightshift run dir under the folders in `runs`.

    A run dir is a folder with a run.toml; activity means its .gate/supervise.jsonl exists, and the
    newest such file wins so several runs can share one notes folder.
    """
    best: tuple[float, Path] | None = None
    for root in runs:
        base = Path(root)
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            gate_file = child / ".gate" / "supervise.jsonl"
            if not (child.is_dir() and (child / "run.toml").is_file() and gate_file.is_file()):
                continue
            mtime = gate_file.stat().st_mtime
            if best is None or mtime > best[0]:
                best = (mtime, child)
    return best[1] if best else None


def nightshift_run(
    cfg: Config | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object] | None:
    """The active nightshift run as one dict for the agents view; None when the feature is off.

    Runs `<nightshift_command> status --json <dir>` on the most recently touched run dir and maps
    its JSON to the fields the view shows. Any failure (no runs, non-zero exit, bad JSON, timeout)
    comes back as {"error": "<one line>"} instead of raising: this is a read-only glance, and Iris
    never starts, stops or changes a run.
    """
    cfg = cfg or load_config()
    if not cfg.nightshift_command:
        return None
    run_dir = _newest_run_dir(cfg.nightshift_runs)
    if run_dir is None:
        return {"error": "no nightshift runs found"}
    cmd = [*cfg.nightshift_command, "status", "--json", str(run_dir)]
    try:
        proc = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=NIGHTSHIFT_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        return {"error": f"nightshift status timed out after {exc.timeout} seconds"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"nightshift status failed: {exc}"}
    if proc.returncode != 0:
        last = (proc.stderr or "nightshift status failed").strip().splitlines()[-1]
        return {"error": last}
    try:
        data = json.loads(proc.stdout)
        done = len(data["done"]) if isinstance(data.get("done"), list) else 0
        blocked = len(data["blocked"]) if isinstance(data.get("blocked"), list) else 0
        updated = data.get("updated")
        return {
            "name": str(data["name"]),
            "done": done,
            "total": int(data["total"]),
            "blocked": blocked,
            "next_item": data["next_item"],
            "next_title": data["next_title"],
            "current_minutes": data["current_minutes"],
            "gate": str(data["gate"]),
            # older nightshift versions print neither key: the line then reads stopped with no age
            "supervisor_running": bool(data.get("supervisor_running", False)),
            "updated": updated if isinstance(updated, str) else None,
            "dir": str(run_dir),
        }
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": f"bad nightshift status JSON: {exc}"}


def agents_status(config: Path | str | None = None) -> str:
    """Status of every watched agent repo: branch, last commit and age, uncommitted files, BLOCKED.md."""
    cfg = load_config(config)
    rows = []
    for name, repo in discover(cfg):
        s = repo_status(repo, cfg.stuck_minutes)
        s.name = name  # the discovered label (with the worktree hash) beats the bare folder name
        rows.append(s)
    if not rows:
        return "No agent repos are configured (iris/agents.toml)."
    stuck = [s for s in rows if s.stuck]
    out = "\n".join(s.spoken() for s in rows)
    if stuck:
        out = f"{out}\n{len(stuck)} stuck."
    from iris.prompt_queue import summary as _queue_summary

    try:
        queue_line = _queue_summary()  # only reading; the queue is never changed here
    except ValueError:
        queue_line = "Queue: the queue file is broken."
    if queue_line:
        out = f"{out}\n{queue_line}"
    return out


# (upper bound in seconds, seconds per unit, unit name)
_UNITS = ((90, 1, "second"), (5400, 60, "minute"), (172800, 3600, "hour"),
          (604800, 86400, "day"), (31536000, 604800, "week"))


def _age(seconds: float | None) -> str:
    """Seconds as something speakable: '12 minutes', '2 hours', '3 days'."""
    if seconds is None:
        return "a while"
    s = int(seconds)
    for limit, per, unit in _UNITS:
        if s < limit:
            n = max(s // per, 1)
            return f"{n} {unit}" if n == 1 else f"{n} {unit}s"
    return f"{max(s // 31536000, 1)} years"
