"""Tests for run_done, against temporary git repos with fixed commit dates (no real repos touched)."""

import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris.autopilot import (
    cline_cli,
    cline_command,
    cline_cwd,
    cline_model,
    cline_provider,
    cline_sessions,
    cline_start,
    run_done,
    run_progress,
    start_in_hub,
    watch,
)

FIXED = 1_700_000_000  # a fixed epoch; every age in these tests is computed from it, never the clock


def git(repo: Path, *args: str, when: int | None = None) -> None:
    """Run git in a temp repo. `when` fixes the commit date so ages are deterministic."""
    env = dict(os.environ)  # keep PATH and HOME so git behaves the same as on the real machine
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{when} +0000"
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def checklist(open_at: tuple[int, ...] = (), total: int = 15) -> list[str]:
    """total checklist lines, with the ones at open_at left unticked."""
    return [f"- [{' ' if i in open_at else 'x'}] A{i}. item {i}" for i in range(1, total + 1)]


def make_repo(root: Path, name: str, lines: list[str] | None = None, *, when: int = FIXED) -> Path:
    """A temp git repo with one commit at `when`; the PROGRESS file is committed too."""
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "iris@test.local")
    git(repo, "config", "user.name", "Iris test")
    (repo / "a.txt").write_text("hello", encoding="utf-8")
    if lines is not None:
        (repo / "PROGRESS-iris6.md").write_text("# Iris run\n" + "\n".join(lines) + "\n", encoding="utf-8")
        git(repo, "add", "PROGRESS-iris6.md")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-m", "first commit", when=when)
    return repo


def test_no_progress_file(tmp_path):
    repo = make_repo(tmp_path, "alpha", None)
    assert run_done(repo, now=FIXED + 3600) == (False, "no PROGRESS file")


def test_open_items_are_reported_with_their_count(tmp_path):
    repo = make_repo(tmp_path, "beta", checklist(open_at=(4, 9, 12)))
    assert run_done(repo, now=FIXED + 3600) == (False, "3 of 15 items open in PROGRESS-iris6.md")


def test_uncommitted_files_block_a_done_run(tmp_path):
    repo = make_repo(tmp_path, "gamma", checklist())
    (repo / "b.txt").write_text("new", encoding="utf-8")
    (repo / "c.txt").write_text("more", encoding="utf-8")
    assert run_done(repo, now=FIXED + 3600) == (False, "2 uncommitted files")


def test_a_fresh_commit_is_not_quiet_yet(tmp_path):
    repo = make_repo(tmp_path, "delta", checklist())
    # 90 seconds old: under the 3 minute quiet period, and _age speaks it as '1 minute'
    assert run_done(repo, now=FIXED + 90) == (False, "last commit 1 minute ago")


def test_all_ticked_clean_and_quiet_is_done(tmp_path):
    repo = make_repo(tmp_path, "epsilon", checklist())
    # exactly quiet_minutes old counts as done ('at least')
    assert run_done(repo, now=FIXED + 3 * 60) == (True, "PROGRESS-iris6.md done: 15 items")


def test_blocked_lines_count_as_done(tmp_path):
    lines = checklist()
    lines[3] = "- [ ] A4. item 4 (BLOCKED)"
    repo = make_repo(tmp_path, "zeta", lines)
    assert run_done(repo, now=FIXED + 300) == (True, "PROGRESS-iris6.md done: 15 items")


def test_the_newest_progress_file_wins(tmp_path):
    old_lines = checklist()
    new_lines = checklist(open_at=(2, 4), total=5)
    repo = make_repo(tmp_path, "eta", old_lines)
    (repo / "PROGRESS-iris7.md").write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    git(repo, "add", "PROGRESS-iris7.md")
    git(repo, "commit", "-m", "second progress file", when=FIXED)
    os.utime(repo / "PROGRESS-iris6.md", (FIXED - 1000, FIXED - 1000))
    os.utime(repo / "PROGRESS-iris7.md", (FIXED - 500, FIXED - 500))
    # the older file is fully ticked; the newer one still has open items and it wins
    assert run_done(repo, now=FIXED + 3600) == (False, "2 of 5 items open in PROGRESS-iris7.md")


def test_a_repo_without_commits_is_reported_not_done(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    repo = make_repo(tmp_path, "theta", checklist())

    def fake_git(target: Path, *args: str) -> str:
        if args and args[0] == "log":
            raise RuntimeError("fatal: your current branch 'main' does not have any commits yet")
        return ""  # a clean status, so only the missing commit shows up

    monkeypatch.setattr(autopilot, "_git", fake_git)
    assert run_done(repo, now=FIXED + 3600) == (False, "no commits yet")


def test_run_progress_of_a_half_done_file(tmp_path):
    repo = make_repo(tmp_path, "alpha", checklist(open_at=(4, 9)))
    assert run_progress(repo) == {
        "repo": str(repo),
        "file": "PROGRESS-iris6.md",
        "done_items": 13,
        "total": 15,
        "done": False,
        "reason": "2 of 15 items open in PROGRESS-iris6.md",
    }


def test_run_progress_of_a_done_file(tmp_path):
    # the commit is at FIXED (years ago), so a fully ticked clean tree is past the quiet period
    repo = make_repo(tmp_path, "beta", checklist())
    assert run_progress(repo) == {
        "repo": str(repo),
        "file": "PROGRESS-iris6.md",
        "done_items": 15,
        "total": 15,
        "done": True,
        "reason": "PROGRESS-iris6.md done: 15 items",
    }


def test_run_progress_without_a_file(tmp_path):
    repo = make_repo(tmp_path, "gamma", None)
    assert run_progress(repo) == {
        "repo": str(repo),
        "file": None,
        "done_items": 0,
        "total": 0,
        "done": False,
        "reason": "no PROGRESS file",
    }


def _clear_cli_env(monkeypatch) -> None:
    for var in ("IRIS_CLINE_CLI", "IRIS_CLINE_PROVIDER", "IRIS_CLINE_MODEL", "IRIS_CLINE_CWD"):
        monkeypatch.delenv(var, raising=False)


def test_cline_accessors_default_and_read_the_env_when_called(monkeypatch):
    _clear_cli_env(monkeypatch)
    assert cline_cli() == "W:/Cline/cline-cli.exe"
    assert cline_provider() == "lmstudio"
    assert cline_model() == "qwen3.8-27b"
    assert cline_cwd() == "W:/Iris"
    monkeypatch.setenv("IRIS_CLINE_CLI", "D:/tools/cline.exe")
    monkeypatch.setenv("IRIS_CLINE_PROVIDER", "openrouter")
    monkeypatch.setenv("IRIS_CLINE_MODEL", "gpt-5.2")
    monkeypatch.setenv("IRIS_CLINE_CWD", "W:/Other")
    assert cline_cli() == "D:/tools/cline.exe"  # read when called, not at import
    assert cline_provider() == "openrouter"
    assert cline_model() == "gpt-5.2"
    assert cline_cwd() == "W:/Other"


def test_cline_command_is_the_exact_argv(monkeypatch):
    _clear_cli_env(monkeypatch)
    assert cline_command("fix the bug", "W:/Iris") == [
        "W:/Cline/cline-cli.exe",
        "-z",
        "-c",
        "W:/Iris",
        "-P",
        "lmstudio",
        "-m",
        "qwen3.8-27b",
        "fix the bug",
    ]


def test_cline_command_uses_the_env(monkeypatch):
    monkeypatch.setenv("IRIS_CLINE_CLI", "D:/tools/cline.exe")
    monkeypatch.setenv("IRIS_CLINE_PROVIDER", "openrouter")
    monkeypatch.setenv("IRIS_CLINE_MODEL", "gpt-5.2")
    assert cline_command("go", "W:/Other") == [
        "D:/tools/cline.exe",
        "-z",
        "-c",
        "W:/Other",
        "-P",
        "openrouter",
        "-m",
        "gpt-5.2",
        "go",
    ]


def test_cline_command_rejects_empty_or_blank_text(monkeypatch):
    _clear_cli_env(monkeypatch)
    for text in ("", "   ", "\n\t  "):
        with pytest.raises(ValueError) as exc:
            cline_command(text, "W:/Iris")
        assert str(exc.value) == "empty prompt"


def test_cline_command_keeps_a_multiline_text_as_one_item(monkeypatch):
    _clear_cli_env(monkeypatch)
    text = "first line\nsecond line"
    argv = cline_command(text, "W:/Iris")
    assert len(argv) == 9
    assert argv[-1] == text


def test_cline_sessions_returns_the_parsed_list_and_skips_non_dicts(monkeypatch):
    _clear_cli_env(monkeypatch)

    def fake_run(argv, **kwargs):
        return SimpleNamespace(
            stdout='[{"sessionId": "s1", "source": "cline-cli-zen"}, {"sessionId": "s2"}, "not a dict", 42]'
        )

    out = cline_sessions(run=fake_run)
    assert out == [{"sessionId": "s1", "source": "cline-cli-zen"}, {"sessionId": "s2"}]


def test_cline_sessions_bad_json_gives_empty(monkeypatch):
    _clear_cli_env(monkeypatch)

    def fake_run(argv, **kwargs):
        return SimpleNamespace(stdout="this is not json")

    assert cline_sessions(run=fake_run) == []


def test_cline_sessions_a_dict_instead_of_a_list_gives_empty(monkeypatch):
    _clear_cli_env(monkeypatch)

    def fake_run(argv, **kwargs):
        return SimpleNamespace(stdout='{"sessionId": "s1"}')

    assert cline_sessions(run=fake_run) == []


def test_cline_sessions_missing_cli_gives_empty(monkeypatch):
    _clear_cli_env(monkeypatch)

    def fake_run(argv, **kwargs):
        raise FileNotFoundError("no such file")

    assert cline_sessions(run=fake_run) == []


def test_cline_sessions_timeout_gives_empty(monkeypatch):
    _clear_cli_env(monkeypatch)

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=30)

    assert cline_sessions(run=fake_run) == []


def test_cline_sessions_passes_the_exact_argv_and_kwargs(monkeypatch):
    _clear_cli_env(monkeypatch)
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout="[]")

    cline_sessions(run=fake_run)
    argv, kwargs = calls[0]
    assert argv == ["W:/Cline/cline-cli.exe", "history", "--json", "--limit", "10"]
    assert kwargs == {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "timeout": 30,
        "stdin": subprocess.DEVNULL,
        "check": False,
    }


HUB_NOW = 1_750_000_000.0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")


def test_start_in_hub_success_with_timeout_text_on_stderr(monkeypatch):
    _clear_cli_env(monkeypatch)
    sessions = [
        {"sessionId": "abc", "source": "cline-cli-zen", "startedAt": _iso(HUB_NOW + 1), "prompt": "fix the bug"}
    ]

    def fake_run(argv, **kwargs):
        return SimpleNamespace(stdout="", stderr="hub timed out after 120s\n", returncode=0)

    out = start_in_hub("fix the bug", "W:/Iris", run=fake_run, sessions=lambda: sessions, now=lambda: HUB_NOW)
    assert out == "started Cline session abc in W:/Iris"


def test_start_in_hub_success_after_timeout(monkeypatch):
    _clear_cli_env(monkeypatch)
    sessions = [
        {"sessionId": "abc", "source": "cline-cli-zen", "startedAt": _iso(HUB_NOW + 1), "prompt": "fix the bug"}
    ]

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=120)

    out = start_in_hub("fix the bug", "W:/Iris", run=fake_run, sessions=lambda: sessions, now=lambda: HUB_NOW)
    assert out == "started Cline session abc in W:/Iris"


def test_start_in_hub_ignores_a_session_started_before_the_window(monkeypatch):
    _clear_cli_env(monkeypatch)
    # started 6 seconds before t0: outside the 5-second grace window
    sessions = [
        {"sessionId": "old", "source": "cline-cli-zen", "startedAt": _iso(HUB_NOW - 6), "prompt": "fix the bug"}
    ]

    def fake_run(argv, **kwargs):
        return SimpleNamespace(stdout="", stderr="hub timed out after 120s\n", returncode=0)

    with pytest.raises(RuntimeError) as exc:
        start_in_hub("fix the bug", "W:/Iris", run=fake_run, sessions=lambda: sessions, now=lambda: HUB_NOW)
    assert str(exc.value) == "Cline did not start a session: hub timed out after 120s"


def test_start_in_hub_ignores_a_session_with_another_prompt(monkeypatch):
    _clear_cli_env(monkeypatch)
    sessions = [
        {"sessionId": "abc", "source": "cline-cli-zen", "startedAt": _iso(HUB_NOW + 1), "prompt": "some other prompt"}
    ]

    def fake_run(argv, **kwargs):
        return SimpleNamespace(stdout="", stderr="hub timed out after 120s\n", returncode=0)

    with pytest.raises(RuntimeError) as exc:
        start_in_hub("fix the bug", "W:/Iris", run=fake_run, sessions=lambda: sessions, now=lambda: HUB_NOW)
    assert str(exc.value) == "Cline did not start a session: hub timed out after 120s"


def test_start_in_hub_ignores_a_session_from_another_source(monkeypatch):
    _clear_cli_env(monkeypatch)
    sessions = [
        {"sessionId": "abc", "source": "vscode", "startedAt": _iso(HUB_NOW + 1), "prompt": "fix the bug"}
    ]

    def fake_run(argv, **kwargs):
        return SimpleNamespace(stdout="", stderr="hub timed out after 120s\n", returncode=0)

    with pytest.raises(RuntimeError) as exc:
        start_in_hub("fix the bug", "W:/Iris", run=fake_run, sessions=lambda: sessions, now=lambda: HUB_NOW)
    assert str(exc.value) == "Cline did not start a session: hub timed out after 120s"


def test_start_in_hub_missing_cli_raises_lookup_error(monkeypatch):
    _clear_cli_env(monkeypatch)

    def fake_run(argv, **kwargs):
        raise FileNotFoundError("no such file")

    with pytest.raises(LookupError) as exc:
        start_in_hub("fix the bug", "W:/Iris", run=fake_run, sessions=lambda: [], now=lambda: HUB_NOW)
    assert str(exc.value) == "Cline CLI not found at W:/Cline/cline-cli.exe"


def test_start_in_hub_error_carries_the_last_stderr_line(monkeypatch):
    _clear_cli_env(monkeypatch)

    def fake_run(argv, **kwargs):
        return SimpleNamespace(stdout="", stderr="first line\nsecond line\nhub timed out after 120s\n", returncode=1)

    with pytest.raises(RuntimeError) as exc:
        start_in_hub("fix the bug", "W:/Iris", run=fake_run, sessions=lambda: [], now=lambda: HUB_NOW)
    assert str(exc.value) == "Cline did not start a session: hub timed out after 120s"


def test_start_in_hub_error_says_no_output_when_stderr_is_empty(monkeypatch):
    _clear_cli_env(monkeypatch)

    def fake_run(argv, **kwargs):
        return SimpleNamespace(stdout="", stderr="   \n", returncode=1)

    with pytest.raises(RuntimeError) as exc:
        start_in_hub("fix the bug", "W:/Iris", run=fake_run, sessions=lambda: [], now=lambda: HUB_NOW)
    assert str(exc.value) == "Cline did not start a session: no output"


def test_start_in_hub_defaults_to_cline_sessions_and_the_clock(monkeypatch):
    _clear_cli_env(monkeypatch)
    started_at = datetime.fromtimestamp(time.time() + 1, tz=UTC).isoformat().replace("+00:00", "Z")
    history = (
        f'[{{"sessionId": "abc", "source": "cline-cli-zen", '
        f'"startedAt": "{started_at}", "prompt": "fix the bug"}}]'
    )

    def fake_run(argv, **kwargs):
        if argv[1] == "history":  # cline_sessions' call
            return SimpleNamespace(stdout=history)
        assert argv == [
            "W:/Cline/cline-cli.exe", "-z", "-c", "W:/Iris", "-P", "lmstudio", "-m", "qwen3.8-27b", "fix the bug"
        ]
        return SimpleNamespace(stdout="", stderr="hub timed out after 120s\n", returncode=0)

    out = start_in_hub("fix the bug", "W:/Iris", run=fake_run)
    assert out == "started Cline session abc in W:/Iris"


def _queue_file(
    tmp_path: Path, texts: tuple[str, ...] = ("first prompt", "second prompt"), agent: str = "cline"
) -> Path:
    """A queue file under tmp_path with these prompts waiting for `agent`, oldest first."""
    import iris.prompt_queue as pq

    path = tmp_path / "queue.json"
    for text in texts:
        pq.add(agent, text, path=path)
    return path


def _waiting(path: Path) -> list[str]:
    import iris.prompt_queue as pq

    return [p["text"] for p in pq.pending("", path)]


def test_cline_start_starts_the_oldest_prompt_in_the_hub_and_shrinks_the_queue(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    _clear_cli_env(monkeypatch)
    path = _queue_file(tmp_path)
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    started: list[tuple[str, str]] = []

    def fake_start(text: str, cwd: str) -> str:
        started.append((text, cwd))
        return f"started Cline session abc in {cwd}"

    monkeypatch.setattr(autopilot, "start_in_hub", fake_start)
    assert cline_start("cline") == "started Cline session abc in W:/Iris"
    assert started == [("first prompt", "W:/Iris")]  # the oldest waiting prompt was started in cline_cwd()
    assert _waiting(path) == ["second prompt"]  # the queue shrank by exactly one


def test_cline_start_leaves_the_queue_when_the_cli_is_missing(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    path = _queue_file(tmp_path)
    monkeypatch.setenv("IRIS_QUEUE", str(path))

    def broken(text: str, cwd: str) -> str:
        raise LookupError("Cline CLI not found at W:/Cline/cline-cli.exe")

    monkeypatch.setattr(autopilot, "start_in_hub", broken)
    assert cline_start("cline") == "error: Cline CLI not found at W:/Cline/cline-cli.exe"
    assert _waiting(path) == ["first prompt", "second prompt"]  # unchanged


def test_cline_start_leaves_the_queue_when_the_hub_fails(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    path = _queue_file(tmp_path)
    monkeypatch.setenv("IRIS_QUEUE", str(path))

    def broken(text: str, cwd: str) -> str:
        raise RuntimeError("Cline did not start a session: hub timed out after 120s")

    monkeypatch.setattr(autopilot, "start_in_hub", broken)
    assert cline_start("cline") == "error: Cline did not start a session: hub timed out after 120s"
    assert _waiting(path) == ["first prompt", "second prompt"]  # unchanged


def test_cline_start_falls_back_to_cline_cwd_for_the_hub(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    path = _queue_file(tmp_path)
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    monkeypatch.setattr(autopilot, "cline_cwd", lambda: "W:/Fallback")
    seen: list[str] = []

    def fake_start(text: str, cwd: str) -> str:
        seen.append(cwd)
        return f"started Cline session abc in {cwd}"

    monkeypatch.setattr(autopilot, "start_in_hub", fake_start)
    assert cline_start("cline") == "started Cline session abc in W:/Fallback"
    assert seen == ["W:/Fallback"]  # no repo given: the hub starts in cline_cwd()


def test_cline_start_refuses_when_the_repo_run_is_not_done(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    path = _queue_file(tmp_path)
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    repo = tmp_path / "repo"
    monkeypatch.setattr(autopilot, "run_done", lambda r: (False, "no commits yet"))
    assert cline_start("cline", repo=str(repo)) == f"error: the run in {repo} is not done: no commits yet"
    assert _waiting(path) == ["first prompt", "second prompt"]  # unchanged


def test_cline_start_with_nothing_waiting(tmp_path, monkeypatch):
    path = _queue_file(tmp_path, texts=())
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    assert cline_start("cline") == "error: no prompts waiting for cline"
    assert _waiting(path) == []


def test_cline_start_is_a_confirm_tool():
    from iris.tools import CONFIRM, TOOLS

    assert TOOLS["cline_start"].tier == CONFIRM


class FakeJournal:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def log(self, **entry) -> None:
        self.entries.append(entry)


def test_watch_sleeps_until_done_then_asks_once(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    path = _queue_file(tmp_path)
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    answers = iter(
        [
            (False, "2 of 15 items open in PROGRESS-iris6.md"),
            (False, "last commit 1 minute ago"),
            (True, "PROGRESS-iris6.md done: 15 items"),
        ]
    )
    monkeypatch.setattr(autopilot, "run_done", lambda repo: next(answers))
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    asked: list[str] = []

    def fake_confirm(question: str) -> bool:
        asked.append(question)
        return True

    started: list[tuple[str, str]] = []

    def fake_start(agent_name: str, repo: str) -> str:
        started.append((agent_name, repo))
        return "started the next prompt"

    out = watch("acme", "cline", fake_confirm, sleep=fake_sleep, start=fake_start)
    assert sleeps == [60, 60]  # not done twice: two waits before the third check finds it done
    assert asked == ["acme is done (PROGRESS-iris6.md done: 15 items). Start the next cline prompt?"]
    assert started == [("cline", "acme")]  # the new session runs in the repo that just finished
    assert out == "started the next prompt"


def test_watch_a_no_never_starts(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    path = _queue_file(tmp_path)
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    monkeypatch.setattr(autopilot, "run_done", lambda repo: (True, "PROGRESS-iris6.md done: 15 items"))
    asked: list[str] = []

    def fake_confirm(question: str) -> bool:
        asked.append(question)
        return False

    started: list[tuple[str, str]] = []

    def fake_start(agent_name: str, repo: str) -> str:
        started.append((agent_name, repo))
        return "should not happen"

    journal = FakeJournal()
    out = watch("acme", "cline", fake_confirm, sleep=lambda s: None, start=fake_start, journal=journal)
    assert out == "Jay said no"
    assert asked == ["acme is done (PROGRESS-iris6.md done: 15 items). Start the next cline prompt?"]
    assert started == []
    assert journal.entries == [
        {
            "event": "autopilot",
            "source": "cli",
            "repo": "acme",
            "agent": "cline",
            "answer": False,
            "result": "Jay said no",
        }
    ]


def test_watch_with_an_empty_queue_never_asks(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    path = _queue_file(tmp_path, texts=())
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    monkeypatch.setattr(autopilot, "run_done", lambda repo: (True, "PROGRESS-iris6.md done: 15 items"))
    asked: list[str] = []

    def fake_confirm(question: str) -> bool:
        asked.append(question)
        return True

    journal = FakeJournal()
    out = watch(
        "acme",
        "cline",
        fake_confirm,
        sleep=lambda s: None,
        start=lambda agent_name, repo: "nope",
        journal=journal,
    )
    assert out == "nothing queued for cline"
    assert asked == []  # nothing waiting means no question at all
    assert journal.entries == [
        {
            "event": "autopilot",
            "source": "cli",
            "repo": "acme",
            "agent": "cline",
            "answer": None,
            "result": "nothing queued for cline",
        }
    ]


def test_watch_max_checks_stops_the_loop(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    checks = 0

    def never_done(repo: object) -> tuple[bool, str]:
        nonlocal checks
        checks += 1
        return (False, "no PROGRESS file")

    monkeypatch.setattr(autopilot, "run_done", never_done)

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    out = watch("acme", "cline", lambda q: True, sleep=fake_sleep, start=lambda agent_name, repo: "nope", max_checks=2)
    assert out == "still running after 2 checks"
    assert checks == 2
    assert sleeps == [60]


def test_watch_logs_an_autopilot_event(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    path = _queue_file(tmp_path)
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    monkeypatch.setattr(autopilot, "run_done", lambda repo: (True, "PROGRESS-iris6.md done: 15 items"))
    journal = FakeJournal()

    seen: list[tuple[str, str]] = []

    def fake_start(agent_name: str, repo: str) -> str:
        seen.append((agent_name, repo))
        return "started the next prompt"

    out = watch("acme", "cline", lambda q: True, sleep=lambda s: None, start=fake_start, journal=journal)
    assert out == "started the next prompt"
    assert seen == [("cline", "acme")]  # started in the repo that just finished
    assert journal.entries == [
        {
            "event": "autopilot",
            "source": "cli",
            "repo": "acme",
            "agent": "cline",
            "answer": True,
            "result": "started the next prompt",
        }
    ]


def test_watch_defaults_to_cline_start(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    path = _queue_file(tmp_path)
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    monkeypatch.setattr(autopilot, "run_done", lambda repo: (True, "PROGRESS-iris6.md done: 15 items"))
    started: list[tuple[str, str]] = []

    def fake_cline_start(agent_name: str, repo: str) -> str:
        started.append((agent_name, repo))
        return "started via the default"

    monkeypatch.setattr(autopilot, "cline_start", fake_cline_start)
    out = watch("acme", "cline", lambda q: True, sleep=lambda s: None)  # no start given: cline_start is the default
    assert out == "started via the default"
    assert started == [("cline", "acme")]  # the default receives (agent, repo) too


