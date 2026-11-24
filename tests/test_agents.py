"""Tests for the read-only agent manager, against temporary git repos (no real repos touched)."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from iris import agents
from iris.agents import DEFAULT_REPOS, Config, RepoStatus, _age, agents_status, discover, load_config, repo_status
from iris.tools import SAFE, TOOLS


@pytest.fixture(autouse=True)
def _queue_in_tmp(tmp_path, monkeypatch):
    """agents_status also reads the prompt queue; keep these tests away from the real one."""
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "queue.json"))


def git(repo: Path, *args: str, when: str | None = None) -> None:
    """Run git in a temp repo. `when` backdates the commit so the age is deterministic."""
    env = dict(os.environ)  # keep PATH and HOME so git behaves the same as on the real machine
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def make_repo(root: Path, name: str, *, branch: str = "main", minutes_ago: float = 0) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "-b", branch)
    git(repo, "config", "user.email", "iris@test.local")
    git(repo, "config", "user.name", "Iris test")
    (repo / "a.txt").write_text("hello", encoding="utf-8")
    git(repo, "add", "a.txt")
    # "<epoch> +0000" is unambiguous; a bare local timestamp would shift with the machine's timezone
    when = f"@{int(time.time() - minutes_ago * 60)} +0000" if minutes_ago else None
    git(repo, "commit", "-m", "first commit", when=when)
    return repo


def test_repo_status_reports_branch_commit_and_clean_tree(tmp_path):
    repo = make_repo(tmp_path, "Acme Shop", branch="feature/render", minutes_ago=12)
    s = repo_status(repo)
    assert s.exists and s.branch == "feature/render"
    assert s.last_commit == "first commit"
    assert s.commit_age is not None and 700 <= s.commit_age <= 800  # about 12 minutes
    assert s.uncommitted == 0 and not s.blocked and not s.stuck


def test_uncommitted_files_are_counted(tmp_path):
    repo = make_repo(tmp_path, "feedbot")
    (repo / "b.txt").write_text("new", encoding="utf-8")
    (repo / "a.txt").write_text("changed", encoding="utf-8")
    assert repo_status(repo).uncommitted == 2


def test_repo_status_reads_utf8_subjects_and_file_names(tmp_path):
    """git output is decoded as UTF-8, so a non-ASCII subject and file name survive the locale code page."""
    repo = tmp_path / "cafe"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "iris@test.local")
    git(repo, "config", "user.name", "Iris test")
    (repo / "a.txt").write_text("hello", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-m", "fix café ☕")
    (repo / "notities-é.txt").write_text("new", encoding="utf-8")
    s = repo_status(repo)
    assert s.exists and s.last_commit == "fix café ☕"  # the subject comes back exactly as committed
    assert s.uncommitted == 1  # the accented file name is counted, not garbled


def test_stuck_needs_both_uncommitted_changes_and_an_old_commit(tmp_path):
    fresh = make_repo(tmp_path, "fresh", minutes_ago=5)
    (fresh / "b.txt").write_text("new", encoding="utf-8")
    assert repo_status(fresh).stuck is False  # recent commit, so someone is working on it

    old = make_repo(tmp_path, "Beacon", minutes_ago=120)
    (old / "b.txt").write_text("new", encoding="utf-8")
    s = repo_status(old)
    assert s.stuck is True
    assert "stuck for 2 hours" in s.spoken()

    clean = make_repo(tmp_path, "clean", minutes_ago=120)
    assert repo_status(clean).stuck is False  # committed 2 hours ago but nothing uncommitted


def test_blocked_md_is_reported(tmp_path):
    repo = make_repo(tmp_path, "blocked")
    (repo / "BLOCKED.md").write_text("stuck on the API key\n", encoding="utf-8")
    s = repo_status(repo)
    assert s.blocked is True and "BLOCKED.md exists" in s.spoken()


def test_missing_repo_reports_an_error_instead_of_raising(tmp_path):
    s = repo_status(tmp_path / "nope")
    assert s.exists is False and "not a git repo" in s.spoken()


def test_git_raises_the_last_stderr_line_when_a_command_fails(tmp_path, monkeypatch):
    """A failing git command raises with git's own last stderr line; no real git runs here."""
    repo = tmp_path / "broken"
    (repo / ".git").mkdir(parents=True)

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "fatal: not a git repository\nthe last line\n"

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _Proc())
    with pytest.raises(RuntimeError) as excinfo:
        agents._git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    assert str(excinfo.value) == "the last line"


def test_git_never_takes_the_index_lock(tmp_path, monkeypatch):
    """A read-only poll must not take the index lock; every git call carries --no-optional-locks."""
    repo = tmp_path / "locked"
    (repo / ".git").mkdir(parents=True)

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    argvs = []

    def spy(*args, **kwargs):
        argvs.append(args[0])
        return _Proc()

    monkeypatch.setattr(subprocess, "run", spy)
    repo_status(repo)  # rev-parse, log and status: three git calls
    assert len(argvs) == 3
    for argv in argvs:
        assert argv[:2] == ["git", "--no-optional-locks"]
        assert argv[2:4] == ["-C", str(repo)]


def test_repo_status_reports_git_failure_as_an_error(tmp_path, monkeypatch):
    repo = tmp_path / "broken"
    (repo / ".git").mkdir(parents=True)

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "fatal: not a git repository\nthe last line\n"

    def broken(*args, **kwargs):
        return _Proc()

    monkeypatch.setattr(subprocess, "run", broken)
    s = repo_status(repo)
    assert s.exists is False and s.error == "git failed: the last line"


def test_spoken_answer_matches_the_documented_style(tmp_path):
    repo = make_repo(tmp_path, "Acme Shop", minutes_ago=12)
    old = make_repo(tmp_path, "Beacon", minutes_ago=120)
    (old / "b.txt").write_text("new", encoding="utf-8")
    out = agents_status(str(write_config(tmp_path, repos=[str(repo), str(old)], worktrees=[])))
    assert "Acme Shop: last commit 12 minutes ago." in out
    assert "Beacon: stuck for 2 hours with 1 uncommitted." in out


def test_spoken_says_no_commits_yet_for_a_fresh_repo():
    s = RepoStatus(name="Fresh", path="/tmp/fresh")
    assert s.spoken() == "Fresh: no commits yet."


def test_agents_status_appends_the_queue_summary(tmp_path):
    from iris.prompt_queue import add

    repo = make_repo(tmp_path, "Acme Shop", minutes_ago=12)
    cfg = str(write_config(tmp_path, repos=[str(repo)], worktrees=[]))
    add("leadscan", "Run de tests.", now="2026-10-14 09:00")
    out = agents_status(cfg)
    assert out.endswith("Queue: 1 prompt waiting (leadscan 1).")


def test_agents_status_reports_a_broken_queue_file(tmp_path):
    repo = make_repo(tmp_path, "Acme Shop", minutes_ago=12)
    cfg = str(write_config(tmp_path, repos=[str(repo)], worktrees=[]))
    (tmp_path / "queue.json").write_text("{oops", encoding="utf-8")
    out = agents_status(cfg)
    assert out.endswith("Queue: the queue file is broken.")


def test_agents_status_says_so_when_nothing_is_configured(tmp_path):
    out = agents_status(str(write_config(tmp_path, repos=[], worktrees=[])))
    assert out == "No agent repos are configured (iris/agents.toml)."


def write_config(tmp_path: Path, repos: list[str], worktrees: list[str], stuck: int = 60) -> Path:
    cfg = tmp_path / "agents.toml"
    lines = [f"repos = {repos!r}".replace("'", '"'), f"worktrees = {worktrees!r}".replace("'", '"'),
             f"stuck_minutes = {stuck}"]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg


def test_worktree_repos_are_discovered_under_the_hash_directories(tmp_path):
    root = tmp_path / "worktrees"
    make_repo(root / "09f4f", "Acme Shop")
    make_repo(root / "d1ac6", "Beacon")
    make_repo(root / "934d0", "Acme Shop")  # same project, second worktree
    (root / "empty").mkdir()  # a worktree dir with no repo in it: skipped, no crash

    found = dict(discover(Config(repos=[], worktrees=[str(root)])))
    assert set(found) == {"Acme Shop [09f4f]", "Beacon [d1ac6]", "Acme Shop [934d0]"}
    assert found["Beacon [d1ac6]"].is_dir() and (found["Beacon [d1ac6]"] / ".git").exists()

    out = agents_status(str(write_config(tmp_path, repos=[], worktrees=[str(root)])))
    assert "Acme Shop [09f4f]: last commit just now." in out
    assert "Beacon [d1ac6]: last commit just now." in out
    assert "Acme Shop [934d0]" in out  # same project, both worktrees listed separately


def test_discover_skips_worktree_roots_that_are_not_folders(tmp_path):
    found = discover(Config(repos=[], worktrees=[str(tmp_path / "missing-root")]))
    assert found == []


def test_discover_skips_files_inside_a_worktree_root(tmp_path):
    root = tmp_path / "worktrees"
    root.mkdir()
    (root / "stray.txt").write_text("x", encoding="utf-8")  # a file where a hash dir should be: skipped, no crash
    found = discover(Config(repos=[], worktrees=[str(root)]))
    assert found == []


def test_stuck_summary_line_counts_the_stuck_repos(tmp_path):
    root = tmp_path / "worktrees"
    ok = make_repo(root / "a", "fresh")
    (ok / "b.txt").write_text("x", encoding="utf-8")  # recent commit, so not stuck
    for name in ("b", "c"):
        old = make_repo(root / name, f"old-{name}", minutes_ago=120)
        (old / "b.txt").write_text("x", encoding="utf-8")
    out = agents_status(str(write_config(tmp_path, repos=[], worktrees=[str(root)])))
    assert out.endswith("2 stuck.")


def test_config_defaults_and_overrides(tmp_path):
    assert load_config(tmp_path / "missing.toml").repos == DEFAULT_REPOS  # missing file falls back to built-in defaults
    cfg = write_config(tmp_path, repos=["W:/Iris"], worktrees=[], stuck=15)
    loaded = load_config(cfg)
    assert loaded.repos == ["W:/Iris"] and loaded.worktrees == [] and loaded.stuck_minutes == 15
    (tmp_path / "agents.toml").write_text("repos = [ broken", encoding="utf-8")  # broken file
    assert load_config(tmp_path / "agents.toml").stuck_minutes == 60  # defaults, not a crash


def test_shipped_config_is_an_empty_example():
    shipped = Path(__file__).resolve().parents[1] / "iris" / "agents.toml"
    cfg = load_config(shipped)
    assert cfg.repos == [] and cfg.stuck_minutes == 60
    assert cfg.nightshift_command == [] and cfg.nightshift_runs == []  # the glance is off out of the box


def test_repos_come_from_the_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("IRIS_REPOS", "X:/alpha; X:/beta")
    assert load_config(tmp_path / "missing.toml").repos == ["X:/alpha", "X:/beta"]


def test_repos_default_to_the_built_in_list_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("IRIS_REPOS", raising=False)
    assert load_config(tmp_path / "missing.toml").repos == DEFAULT_REPOS


def test_agents_status_is_safe_and_never_writes(tmp_path):
    assert TOOLS["agents_status"].tier_of({}) == SAFE
    repo = make_repo(tmp_path, "iris")
    before = sorted(p.name for p in repo.iterdir())
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    repo_status(repo)
    assert sorted(p.name for p in repo.iterdir()) == before  # nothing added to the repo
    after = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    assert after == head  # and no commit was made


def test_age_is_spoken_in_readable_units():
    assert _age(5) == "5 seconds" and _age(1) == "1 second"
    assert _age(12 * 60) == "12 minutes" and _age(2 * 3600) == "2 hours"
    assert _age(3 * 86400) == "3 days" and _age(2 * 86400 * 365) == "2 years"
    assert _age(None) == "a while"


class _StatusProc:
    """A stand-in for subprocess.run's result; the fake runner hands one of these back."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _status_json() -> str:
    """The same JSON nightshift status --json prints (see _status_payload in the nightshift cli)."""
    return json.dumps({
        "name": "qwen92",
        "total": 2,
        "done": [1],
        "blocked": [],
        "next_item": 2,
        "next_title": "dashboard: show it on the agents view",
        "current_minutes": 7,
        "gate": "pass",
        "commits": [{"hash": "abc1234", "subject": "Q1: agents: read the active nightshift run"}],
        "events": [],
    })


def make_run_dir(root: Path, name: str, *, minutes_ago: float = 0) -> Path:
    """A nightshift run dir: a folder with a run.toml and .gate/supervise.jsonl.

    `minutes_ago` backdates the supervise file so 'most recently touched' is deterministic.
    """
    d = root / name
    (d / ".gate").mkdir(parents=True)
    (d / "run.toml").write_text("[run]\nname = \"test\"\n", encoding="utf-8")
    gate_file = d / ".gate" / "supervise.jsonl"
    gate_file.write_text("{}\n", encoding="utf-8")
    stamp = time.time() - minutes_ago * 60
    os.utime(gate_file, (stamp, stamp))
    return d


def test_nightshift_run_is_off_by_default(tmp_path):
    calls: list[list[str]] = []

    def fake(*args, **kwargs):
        calls.append(args[0])
        return _StatusProc(stdout=_status_json())

    assert agents.nightshift_run(Config(nightshift_command=[], nightshift_runs=[str(tmp_path)]), run=fake) is None
    assert calls == []  # the command never runs when the feature is off


def test_config_reads_the_nightshift_keys(tmp_path):
    loaded = load_config(write_config(tmp_path, repos=[], worktrees=[]))  # no nightshift keys at all
    assert loaded.nightshift_command == [] and loaded.nightshift_runs == []
    p = tmp_path / "ns.toml"
    p.write_text('nightshift_command = ["W:/ns/python.exe", "-m", "nightshift"]\n'
                 'nightshift_runs = ["W:/Opus-notes"]\n', encoding="utf-8")
    loaded = load_config(p)
    assert loaded.nightshift_command == ["W:/ns/python.exe", "-m", "nightshift"]
    assert loaded.nightshift_runs == ["W:/Opus-notes"]


def test_nightshift_run_picks_the_most_recently_touched_run(tmp_path):
    make_run_dir(tmp_path, "old-run", minutes_ago=90)
    new = make_run_dir(tmp_path, "new-run")  # touched just now
    argvs: list[list[str]] = []

    def fake(*args, **kwargs):
        argvs.append(args[0])
        return _StatusProc(stdout=_status_json())

    out = agents.nightshift_run(Config(nightshift_command=["ns"], nightshift_runs=[str(tmp_path)]), run=fake)
    assert out is not None and "error" not in out
    assert out["dir"] == str(new)  # the newer supervise.jsonl wins, not the folder name
    assert argvs[0] == ["ns", "status", "--json", str(new)]


def test_nightshift_run_maps_the_status_fields(tmp_path):
    run_dir = make_run_dir(tmp_path, "qwen92")

    def fake(*args, **kwargs):
        return _StatusProc(stdout=_status_json())

    out = agents.nightshift_run(Config(nightshift_command=["ns"], nightshift_runs=[str(tmp_path)]), run=fake)
    assert out == {
        "name": "qwen92",
        "done": 1,
        "total": 2,
        "blocked": 0,
        "next_item": 2,
        "next_title": "dashboard: show it on the agents view",
        "current_minutes": 7,
        "gate": "pass",
        "dir": str(run_dir),
    }


def test_nightshift_run_counts_done_and_blocked_items(tmp_path):
    make_run_dir(tmp_path, "qwen92")
    payload = json.dumps({"name": "done-run", "total": 5, "done": [1, 2], "blocked": [3, 4],
                          "next_item": None, "next_title": None, "current_minutes": None, "gate": "fail"})

    def fake(*args, **kwargs):
        return _StatusProc(stdout=payload)

    out = agents.nightshift_run(Config(nightshift_command=["ns"], nightshift_runs=[str(tmp_path)]), run=fake)
    assert out is not None and "error" not in out
    assert out["done"] == 2 and out["blocked"] == 2  # counts, not the raw lists
    assert out["next_item"] is None and out["next_title"] is None and out["current_minutes"] is None
    assert out["gate"] == "fail"


def test_nightshift_run_argv_is_exactly_command_plus_status_json_and_dir(tmp_path):
    run_dir = make_run_dir(tmp_path, "qwen92")
    seen: dict[str, object] = {}

    def fake(*args, **kwargs):
        seen["argv"] = args[0]
        seen.update(kwargs)
        return _StatusProc(stdout=_status_json())

    agents.nightshift_run(Config(nightshift_command=["W:/ns/python.exe", "-m", "nightshift"],
                                 nightshift_runs=[str(tmp_path)]), run=fake)
    assert seen["argv"] == ["W:/ns/python.exe", "-m", "nightshift", "status", "--json", str(run_dir)]
    assert seen["capture_output"] is True and seen["text"] is True
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"
    assert seen["timeout"] == 20


def test_nightshift_run_reports_when_no_runs_exist(tmp_path):
    (tmp_path / "not-a-run").mkdir()  # a folder without run.toml is not a run dir
    bare = make_run_dir(tmp_path, "no-gate")
    (bare / ".gate" / "supervise.jsonl").unlink()  # a run dir nobody has touched yet: skipped
    calls: list[list[str]] = []

    def fake(*args, **kwargs):
        calls.append(args[0])
        return _StatusProc(stdout=_status_json())

    runs = [str(tmp_path), str(tmp_path / "missing")]  # a missing root is skipped too
    out = agents.nightshift_run(Config(nightshift_command=["ns"], nightshift_runs=runs), run=fake)
    assert isinstance(out, dict) and set(out) == {"error"}
    assert "no nightshift runs" in str(out["error"])
    assert calls == []  # nothing to ask, so the command never runs


def test_nightshift_run_reports_a_non_zero_exit(tmp_path):
    make_run_dir(tmp_path, "qwen92")

    def fake(*args, **kwargs):
        return _StatusProc(returncode=1, stderr="boom: first line\nthe last line\n")

    out = agents.nightshift_run(Config(nightshift_command=["ns"], nightshift_runs=[str(tmp_path)]), run=fake)
    assert out == {"error": "the last line"}


def test_nightshift_run_reports_bad_json(tmp_path):
    make_run_dir(tmp_path, "qwen92")

    def fake(*args, **kwargs):
        return _StatusProc(stdout="{oops")

    out = agents.nightshift_run(Config(nightshift_command=["ns"], nightshift_runs=[str(tmp_path)]), run=fake)
    assert isinstance(out, dict) and set(out) == {"error"}
    assert "JSON" in str(out["error"])


def test_nightshift_run_reports_a_timeout(tmp_path):
    make_run_dir(tmp_path, "qwen92")

    def fake(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ns", timeout=20)

    out = agents.nightshift_run(Config(nightshift_command=["ns"], nightshift_runs=[str(tmp_path)]), run=fake)
    assert isinstance(out, dict) and set(out) == {"error"}
    assert "timed out" in str(out["error"])


def test_nightshift_run_reports_a_missing_command(tmp_path):
    make_run_dir(tmp_path, "qwen92")

    def fake(*args, **kwargs):
        raise FileNotFoundError("ns not found")

    out = agents.nightshift_run(Config(nightshift_command=["ns"], nightshift_runs=[str(tmp_path)]), run=fake)
    assert isinstance(out, dict) and set(out) == {"error"}
    assert "failed" in str(out["error"])
