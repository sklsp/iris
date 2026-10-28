"""Tests for run_done, against temporary git repos with fixed commit dates (no real repos touched)."""

import os
import subprocess
from pathlib import Path

import pytest

from iris.autopilot import cline_keys, cline_start, new_task_command, paste_into_cline, run_done, vscode_title

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


def test_commands_default_and_read_the_env_when_called(monkeypatch):
    monkeypatch.delenv("IRIS_CLINE_NEW_TASK", raising=False)
    monkeypatch.delenv("IRIS_VSCODE_TITLE", raising=False)
    assert new_task_command() == "Cline: New Task"
    assert vscode_title() == "Visual Studio Code"
    monkeypatch.setenv("IRIS_CLINE_NEW_TASK", "Cline: New Cline Task")
    monkeypatch.setenv("IRIS_VSCODE_TITLE", "Code - Workspaces")
    assert new_task_command() == "Cline: New Cline Task"  # read when called, not at import
    assert vscode_title() == "Code - Workspaces"


def test_cline_keys_is_the_exact_sequence(monkeypatch):
    monkeypatch.delenv("IRIS_CLINE_NEW_TASK", raising=False)
    text = "add 2+3 (with {braces})"
    assert cline_keys(text) == [
        "^+p",
        "Cline: New Task",
        "{ENTER}",
        "add 2{+}3 {(}with {{}braces{}}{)}",
        "{ENTER}",
    ]


def test_cline_keys_escapes_the_command_too(monkeypatch):
    monkeypatch.setenv("IRIS_CLINE_NEW_TASK", "Cline: New Task (beta)")
    assert cline_keys("x") == ["^+p", "Cline: New Task {(}beta{)}", "{ENTER}", "x", "{ENTER}"]


def test_cline_keys_rejects_a_newline(monkeypatch):
    monkeypatch.delenv("IRIS_CLINE_NEW_TASK", raising=False)
    with pytest.raises(ValueError) as exc:
        cline_keys("first\nsecond")
    assert str(exc.value) == "one line only"


class FakeWindow:
    """Records set_focus and every type_keys call; no real window is ever touched."""

    def __init__(self) -> None:
        self.focus_calls = 0
        self.keys: list[tuple[str, dict]] = []

    def set_focus(self) -> None:
        self.focus_calls += 1

    def type_keys(self, keys: str, **kwargs) -> None:
        self.keys.append((keys, kwargs))


def test_paste_into_cline_types_the_exact_sequence(monkeypatch):
    monkeypatch.delenv("IRIS_CLINE_NEW_TASK", raising=False)
    window = FakeWindow()
    sleeps: list[float] = []
    text = "add 2+3 (with {braces})"
    out = paste_into_cline(text, window=window, sleep=sleeps.append)
    assert window.focus_calls == 1
    assert [k for k, _ in window.keys] == [
        "^+p",
        "Cline: New Task",
        "{ENTER}",
        "add 2{+}3 {(}with {{}braces{}}{)}",
        "{ENTER}",
    ]
    assert all(kwargs == {"with_spaces": True} for _, kwargs in window.keys)
    # two seconds after the command's {ENTER}, a short pause after every other step
    assert sleeps == [0.3, 0.3, 2, 0.3, 0.3]
    assert out == "started a Cline task with: add 2+3 (with {braces})"


def test_paste_into_cline_truncates_the_reported_text(monkeypatch):
    monkeypatch.delenv("IRIS_CLINE_NEW_TASK", raising=False)
    window = FakeWindow()
    text = "x" * 80
    out = paste_into_cline(text, window=window, sleep=lambda s: None)
    assert out == "started a Cline task with: " + "x" * 60


def test_paste_into_cline_without_vs_code_raises(monkeypatch):
    import iris.autopilot as autopilot

    monkeypatch.delenv("IRIS_VSCODE_TITLE", raising=False)

    def no_window(title: str):
        raise LookupError(f"no window whose title contains {title!r}")

    monkeypatch.setattr(autopilot, "_window", no_window)
    with pytest.raises(LookupError, match="Visual Studio Code"):
        paste_into_cline("hello")


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


def test_cline_start_pastes_the_oldest_prompt_and_shrinks_the_queue(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    path = _queue_file(tmp_path)
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    typed: list[str] = []

    def fake_paste(text: str) -> str:
        typed.append(text)
        return f"started a Cline task with: {text[:60]}"

    monkeypatch.setattr(autopilot, "paste_into_cline", fake_paste)
    assert cline_start("cline") == "started a Cline task with: first prompt"
    assert typed == ["first prompt"]  # the oldest waiting prompt was pasted
    assert _waiting(path) == ["second prompt"]  # the queue shrank by exactly one


def test_cline_start_leaves_the_queue_when_the_paste_fails(tmp_path, monkeypatch):
    import iris.autopilot as autopilot

    path = _queue_file(tmp_path)
    monkeypatch.setenv("IRIS_QUEUE", str(path))

    def broken(text: str) -> str:
        raise LookupError("no window whose title contains 'Visual Studio Code'")

    monkeypatch.setattr(autopilot, "paste_into_cline", broken)
    assert cline_start("cline") == "error: VS Code is not open"
    assert _waiting(path) == ["first prompt", "second prompt"]  # unchanged


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


