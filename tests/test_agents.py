"""Tests for the read-only agent manager, against temporary git repos (no real repos touched)."""

import os
import subprocess
import time
from pathlib import Path

import pytest

from iris.agents import DEFAULT_REPOS, Config, _age, agents_status, discover, load_config, repo_status
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
    assert 700 <= s.commit_age <= 800  # about 12 minutes
    assert s.uncommitted == 0 and not s.blocked and not s.stuck


def test_uncommitted_files_are_counted(tmp_path):
    repo = make_repo(tmp_path, "feedbot")
    (repo / "b.txt").write_text("new", encoding="utf-8")
    (repo / "a.txt").write_text("changed", encoding="utf-8")
    assert repo_status(repo).uncommitted == 2


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


def test_spoken_answer_matches_the_documented_style(tmp_path):
    repo = make_repo(tmp_path, "Acme Shop", minutes_ago=12)
    old = make_repo(tmp_path, "Beacon", minutes_ago=120)
    (old / "b.txt").write_text("new", encoding="utf-8")
    out = agents_status(str(write_config(tmp_path, repos=[str(repo), str(old)], worktrees=[])))
    assert "Acme Shop: last commit 12 minutes ago." in out
    assert "Beacon: stuck for 2 hours with 1 uncommitted." in out


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
