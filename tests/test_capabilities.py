"""The guard, and the plan files for skills Iris does not have yet."""

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris import tools
from iris.agent import _describe
from iris.capabilities import (
    PLAN_TEMPLATE,
    branch_for,
    build_prompt,
    capabilities_dir,
    check_build,
    guard_problems,
    list_plans,
    prepare_build,
    save_plan,
    worktree_for,
)


def _lock(tier="confirm", escalates=True):
    return {"click": {"tier": tier, "escalates": escalates}}


def test_clean_change_is_acceptable():
    old = _lock()
    new = dict(old)
    new["new_tool"] = {"tier": "safe", "escalates": False}  # a new lock entry is fine
    changed = ["iris/tools.py", "tests/test_tools.py", "eval/tasks.toml", "iris/tier_lock.json"]
    assert guard_problems(changed, old, new) == []


def test_agent_py_is_protected():
    assert guard_problems(["iris/agent.py"], {}, {}) == ["touches protected path iris/agent.py"]


def test_github_file_is_protected():
    assert guard_problems([".github/workflows/ci.yml"], {}, {}) == [
        "touches protected path .github/workflows/ci.yml"]


def test_backslash_path_is_normalized_before_comparison():
    assert guard_problems(["iris\\agent.py"], {}, {}) == ["touches protected path iris/agent.py"]


def test_changed_tier_is_a_problem():
    old = _lock()
    new = {"click": {"tier": "safe", "escalates": True}}
    assert guard_problems([], old, new) == ["changes the tier of click"]


def test_removed_entry_is_a_problem():
    old = _lock()
    assert guard_problems([], old, {}) == ["changes the tier of click"]


def test_escalates_flip_is_a_problem():
    old = _lock(escalates=True)
    new = {"click": {"tier": "confirm", "escalates": False}}
    assert guard_problems([], old, new) == ["changes the tier of click"]


# --- save_plan: the plan files for a skill Iris does not have yet -----------------

def _caps(tmp_path, monkeypatch):
    caps = tmp_path / "caps"
    monkeypatch.setenv("IRIS_CAPABILITIES", str(caps))
    return caps


def test_save_plan_writes_md_and_json(tmp_path, monkeypatch):
    caps = _caps(tmp_path, monkeypatch)
    md = save_plan("I want Iris to check the printer ink level", "printer_ink", "safe",
                   "A tool that reads the ink level from the printer status window.")
    assert md == caps / "printer_ink.md"
    meta = json.loads((caps / "printer_ink.json").read_text(encoding="utf-8"))
    assert meta["tool_name"] == "printer_ink"
    assert meta["tier"] == "safe"
    assert meta["request"] == "I want Iris to check the printer ink level"
    assert meta["status"] == "planned"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", meta["created"])


def test_save_plan_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    caps = _caps(tmp_path, monkeypatch)
    save_plan("a request", "new_tool", "safe", "a plan")
    assert sorted(p.name for p in caps.iterdir()) == ["new_tool.json", "new_tool.md"]


def test_save_plan_refuses_to_overwrite_an_existing_plan(tmp_path, monkeypatch):
    _caps(tmp_path, monkeypatch)
    save_plan("first ask", "printer_ink", "safe", "a plan")
    with pytest.raises(ValueError, match="a plan for printer_ink exists"):
        save_plan("second ask", "printer_ink", "safe", "another plan")


VALIDATION_CASES = [
    ("a request", "NewTool", "safe", "a plan", "invalid tool name 'NewTool'"),
    ("a request", "ab", "safe", "a plan", "invalid tool name 'ab'"),
    ("a request", "1tool", "safe", "a plan", "invalid tool name '1tool'"),
    ("a request", "click", "safe", "a plan", "click already exists"),
    ("a request", "new_tool", "never", "a plan", "tier must be 'safe' or 'confirm'"),
    ("", "new_tool", "safe", "a plan", "request is empty"),
    ("a request", "new_tool", "safe", "", "plan is empty"),
    ("a request", "new_tool", "safe", "x" * 4001, "plan is too long (max 4000 chars)"),
]


@pytest.mark.parametrize("req, tool_name, tier, plan, expected", VALIDATION_CASES)
def test_save_plan_rejects_invalid_input(tmp_path, monkeypatch, req, tool_name, tier, plan, expected):
    _caps(tmp_path, monkeypatch)
    with pytest.raises(ValueError) as excinfo:
        save_plan(req, tool_name, tier, plan)
    assert str(excinfo.value) == expected


def test_save_plan_accepts_boundary_lengths(tmp_path, monkeypatch):
    caps = _caps(tmp_path, monkeypatch)
    name = "a" * 40  # the longest allowed name
    save_plan("a request", name, "confirm", "x" * 4000)  # the longest allowed plan
    assert (caps / f"{name}.md").exists()


# --- PLAN_TEMPLATE: what a plan file must contain ---------------------------------

def test_plan_md_contains_every_required_field(tmp_path, monkeypatch):
    _caps(tmp_path, monkeypatch)
    request = "I want Iris to check the printer ink level"
    plan = "Read the ink level from the printer status window."
    md = save_plan(request, "printer_ink", "confirm", plan).read_text(encoding="utf-8")
    assert "# Plan: printer_ink" in md
    assert "- Tier: confirm" in md
    assert "- Status: planned" in md
    assert request in md  # Jay's request, verbatim
    assert plan in md


def test_plan_md_has_the_created_timestamp(tmp_path, monkeypatch):
    _caps(tmp_path, monkeypatch)
    md = save_plan("a request", "new_tool", "safe", "a plan").read_text(encoding="utf-8")
    created = re.search(r"- Created: (\S+)", md).group(1)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", created)


def test_plan_template_placeholders_are_pinned():
    fields = set(re.findall(r"\{(\w+)\}", PLAN_TEMPLATE))
    assert fields == {"tool_name", "tier", "status", "created", "request", "plan"}


# --- capability_list: one line per saved plan ------------------------------------

def test_capability_list_with_no_plans(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_CAPABILITIES", str(tmp_path / "caps"))  # the dir does not exist yet
    assert list_plans() == "No plans yet."


def test_capability_list_with_an_empty_dir(tmp_path, monkeypatch):
    caps = tmp_path / "caps"
    caps.mkdir()
    monkeypatch.setenv("IRIS_CAPABILITIES", str(caps))
    assert list_plans() == "No plans yet."


def test_capability_lists_the_saved_plans_one_line_each(tmp_path, monkeypatch):
    _caps(tmp_path, monkeypatch)
    save_plan("I want Iris to check the printer ink level", "printer_ink", "safe", "a plan")
    long_request = "please" + "y" * 70
    save_plan(long_request, "zeta_tool", "confirm", "another plan")
    assert list_plans() == (
        "printer_ink (safe): planned, asked: I want Iris to check the printer ink level\n"
        f"zeta_tool (confirm): planned, asked: {long_request[:60]}"
    )


def test_capabilities_dir_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("IRIS_CAPABILITIES", raising=False)
    assert capabilities_dir() == Path.home() / ".iris" / "capabilities"


# --- the tools as the agent sees them ---------------------------------------------

def test_request_capability_tool_saves_and_reports(tmp_path, monkeypatch):
    _caps(tmp_path, monkeypatch)
    out = tools.request_capability("a request", "new_tool", "safe", "a plan")
    assert out == "Saved the plan for new_tool. It is not built yet; Jay can ask to build it."


def test_request_capability_tool_reports_validation_errors(tmp_path, monkeypatch):
    _caps(tmp_path, monkeypatch)
    assert tools.request_capability("r", "click", "safe", "p") == "error: click already exists"
    assert tools.request_capability("r", "NewTool", "safe", "p") == "error: invalid tool name 'NewTool'"


def test_describe_request_capability():
    assert _describe("request_capability", {"tool_name": "printer_ink"}) == \
        "I can't do that yet. Save a plan to build a new skill 'printer_ink'?"


def test_a_saved_plan_carries_the_rules_and_every_protected_path(tmp_path, monkeypatch):
    from iris.capabilities import PROTECTED

    _caps(tmp_path, monkeypatch)
    md = save_plan("Maak een PowerPoint", "make_powerpoint", "confirm", "Use python-pptx.").read_text(encoding="utf-8")
    assert "## Rules" in md
    assert "Never change the tier, the escalation or the entry of an existing tool." in md
    for p in PROTECTED:
        assert f"  - {p}" in md
    assert md.index("## Plan") < md.index("## Rules")


# --- prepare_build: a build happens in its own worktree ---------------------------

class _FakeRun:
    def __init__(self, code=0, stderr=""):
        self.code = code
        self.stderr = stderr
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return SimpleNamespace(returncode=self.code, stdout="", stderr=self.stderr)


def _planned(tmp_path, monkeypatch, tool="printer_ink"):
    caps = _caps(tmp_path, monkeypatch)
    save_plan("a request", tool, "safe", "a plan")
    wt_root = tmp_path / "wt"
    monkeypatch.setenv("IRIS_SELF_WORKTREES", str(wt_root))
    return caps, wt_root


def test_worktree_for_uses_the_env_folder(monkeypatch):
    monkeypatch.setenv("IRIS_SELF_WORKTREES", "/tmp/wts")
    assert worktree_for("printer_ink") == Path("/tmp/wts/printer_ink")


def test_worktree_for_falls_back_next_to_the_repo(monkeypatch):
    monkeypatch.delenv("IRIS_SELF_WORKTREES", raising=False)
    repo = Path(__file__).resolve().parent.parent
    assert worktree_for("printer_ink") == repo.parent / "Iris-self" / "printer_ink"


def test_branch_for():
    assert branch_for("printer_ink") == "self/printer_ink"


def test_prepare_build_runs_the_exact_git_command(tmp_path, monkeypatch):
    _planned(tmp_path, monkeypatch)
    wt_root = tmp_path / "wt"
    repo = tmp_path / "repo"
    fake = _FakeRun()
    prepare_build("printer_ink", repo, run=fake)
    argv, kwargs = fake.calls[0]
    assert argv == ["git", "-C", str(repo), "worktree", "add", str(wt_root / "printer_ink"),
                    "-b", "self/printer_ink", "main"]
    assert kwargs == {"capture_output": True, "text": True, "check": False}


def test_prepare_build_copies_the_plan_and_updates_the_json(tmp_path, monkeypatch):
    caps, wt_root = _planned(tmp_path, monkeypatch)
    md = caps / "printer_ink.md"
    worktree = prepare_build("printer_ink", tmp_path / "repo", run=_FakeRun())
    assert worktree == wt_root / "printer_ink"
    built = worktree / "BUILD-printer_ink.md"
    assert built.read_text(encoding="utf-8") == md.read_text(encoding="utf-8")
    meta = json.loads((caps / "printer_ink.json").read_text(encoding="utf-8"))
    assert meta["status"] == "building"
    assert meta["worktree"] == str(worktree)
    assert meta["branch"] == "self/printer_ink"


def test_prepare_build_refuses_an_unknown_plan(tmp_path, monkeypatch):
    _planned(tmp_path, monkeypatch)
    fake = _FakeRun()
    with pytest.raises(ValueError, match="no planned skill ghost_tool"):
        prepare_build("ghost_tool", tmp_path / "repo", run=fake)
    assert fake.calls == []


def test_prepare_build_refuses_a_plan_that_is_already_building(tmp_path, monkeypatch):
    _planned(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    prepare_build("printer_ink", repo, run=_FakeRun())
    with pytest.raises(ValueError, match="no planned skill printer_ink"):
        prepare_build("printer_ink", repo, run=_FakeRun())


def test_prepare_build_refuses_an_existing_worktree_folder(tmp_path, monkeypatch):
    _planned(tmp_path, monkeypatch)
    (tmp_path / "wt" / "printer_ink").mkdir(parents=True)
    fake = _FakeRun()
    with pytest.raises(ValueError, match="a worktree for printer_ink already exists"):
        prepare_build("printer_ink", tmp_path / "repo", run=fake)
    assert fake.calls == []


def test_prepare_build_raises_on_git_failure(tmp_path, monkeypatch):
    caps, _ = _planned(tmp_path, monkeypatch)
    fake = _FakeRun(code=1, stderr="fatal: not a git repository\nerror: bad thing happened")
    with pytest.raises(RuntimeError, match="bad thing happened"):
        prepare_build("printer_ink", tmp_path / "repo", run=fake)
    meta = json.loads((caps / "printer_ink.json").read_text(encoding="utf-8"))
    assert meta["status"] == "planned"


def test_prepare_build_git_failure_with_empty_stderr(tmp_path, monkeypatch):
    _planned(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="git worktree add failed"):
        prepare_build("printer_ink", tmp_path / "repo", run=_FakeRun(code=1))


# --- capability_build: the confirm tool that starts Cline -------------------------

def test_build_prompt_is_the_exact_text_for_cline(tmp_path):
    wt = tmp_path / "wt" / "printer_ink"
    assert build_prompt("printer_ink", wt) == (
        f"Read {wt}/BUILD-printer_ink.md and follow it. You work in {wt} on branch self/printer_ink, made for you; "
        f"never switch or create branches, never push. Write progress to {wt}/PROGRESS-printer_ink.md. "
        f"Do NOT call attempt_completion until every step is done.")


def test_describe_capability_build():
    assert _describe("capability_build", {"tool_name": "printer_ink"}) == \
        "Build the new skill 'printer_ink' now? Cline works on it in a separate copy of Iris."


def _planned_and_patched(tmp_path, monkeypatch):
    caps = _caps(tmp_path, monkeypatch)
    save_plan("a request", "printer_ink", "safe", "a plan")
    wt_root = tmp_path / "wt"
    monkeypatch.setenv("IRIS_SELF_WORKTREES", str(wt_root))
    fake_wt = wt_root / "printer_ink"

    def fake_prepare(name, repo):  # the real prepare_build's json side effects, without git
        meta = json.loads((caps / f"{name}.json").read_text(encoding="utf-8"))
        meta["status"] = "building"
        meta["worktree"] = str(fake_wt)
        meta["branch"] = branch_for(name)
        (caps / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")
        return fake_wt

    monkeypatch.setattr(tools, "prepare_build", fake_prepare)
    return caps, fake_wt


def test_capability_build_starts_cline_and_stores_the_session(tmp_path, monkeypatch):
    caps, fake_wt = _planned_and_patched(tmp_path, monkeypatch)
    calls = []

    def start(prompt, cwd):
        calls.append((prompt, cwd))
        return f"started Cline session abc123 in {cwd}"

    monkeypatch.setattr("iris.autopilot.start_in_hub", start)
    result = tools.capability_build("printer_ink")
    assert result == (f"Building printer_ink in {fake_wt}. I will not use it until Jay reviews and merges it.")
    prompt, cwd = calls[0]
    assert prompt == build_prompt("printer_ink", fake_wt)
    assert cwd == str(fake_wt)
    meta = json.loads((caps / "printer_ink.json").read_text(encoding="utf-8"))
    assert meta["status"] == "building"
    assert meta["session"] == "abc123"


def test_capability_build_failed_start_goes_back_to_planned(tmp_path, monkeypatch):
    caps, fake_wt = _planned_and_patched(tmp_path, monkeypatch)

    def start(prompt, cwd):
        raise RuntimeError("Cline did not start a session after two tries: no output")

    monkeypatch.setattr("iris.autopilot.start_in_hub", start)
    result = tools.capability_build("printer_ink")
    assert result == ("error: Cline did not start a session after two tries: no output. "
                      "The plan is back to planned; nothing else was removed.")
    meta = json.loads((caps / "printer_ink.json").read_text(encoding="utf-8"))
    assert meta["status"] == "planned"
    assert meta["worktree"] == str(fake_wt)  # nothing else was removed


def test_capability_build_refuses_a_not_planned_skill(tmp_path, monkeypatch):
    _caps(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr("iris.autopilot.start_in_hub", lambda prompt, cwd: calls.append((prompt, cwd)))
    result = tools.capability_build("ghost_tool")
    assert result == "error: no planned skill ghost_tool"
    assert calls == []


class _GitRun:
    """Fake run for check_build: answers git diff/show and the three gates by argv."""

    def __init__(self, changed=(), old_lock=None, new_lock=None, gate_failures=None):
        self.changed = list(changed)
        self.old_lock = {} if old_lock is None else old_lock
        self.new_lock = {} if new_lock is None else new_lock
        self.gate_failures = dict(gate_failures or {})
        self.calls = []

    def __call__(self, argv, **kwargs):
        text = " ".join(str(a) for a in argv)
        self.calls.append((list(argv), kwargs))
        if "--name-only" in text:
            return subprocess.CompletedProcess(list(argv), 0, stdout="\n".join(self.changed), stderr="")
        if "main:iris/tier_lock.json" in text:
            return subprocess.CompletedProcess(list(argv), 0, stdout=json.dumps(self.old_lock), stderr="")
        if ":iris/tier_lock.json" in text:
            return subprocess.CompletedProcess(list(argv), 0, stdout=json.dumps(self.new_lock), stderr="")
        for gate, marker in (("ruff", "-m ruff"), ("mypy", "-m mypy"), ("pytest", "-m pytest")):
            if marker in text:
                if gate in self.gate_failures:
                    code, output = self.gate_failures[gate]
                    return subprocess.CompletedProcess(list(argv), code, stdout="", stderr=output)
                return subprocess.CompletedProcess(list(argv), 0, stdout="ok", stderr="")
        raise AssertionError(f"unexpected command: {text}")


def _building(tmp_path, monkeypatch, with_venv=True):
    """A plan for printer_ink in status building, with its worktree folder (and maybe a .venv python)."""
    monkeypatch.setenv("IRIS_CAPABILITIES", str(tmp_path / "caps"))
    caps = tmp_path / "caps"
    wt = tmp_path / "wt-printer_ink"
    if with_venv:
        py = wt / ".venv" / "Scripts" / "python.exe"
        py.parent.mkdir(parents=True)
        py.write_text("", encoding="utf-8")
    caps.mkdir()
    meta = {"tool_name": "printer_ink", "tier": "confirm", "status": "building",
            "created": "2026-10-31T00:00:00", "request": "r", "plan": "p",
            "worktree": str(wt), "branch": "self/printer_ink"}
    (caps / "printer_ink.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")
    return caps, wt


def _saved(caps):
    return json.loads((caps / "printer_ink.json").read_text(encoding="utf-8"))


def test_check_build_clean_ready(tmp_path, monkeypatch):
    caps, wt = _building(tmp_path, monkeypatch)
    run = _GitRun(changed=["iris/tools.py", "tests/test_tools.py"])
    meta = check_build("printer_ink", tmp_path / "repo", run=run)
    assert meta["status"] == "ready"
    assert meta["problems"] == []
    assert meta["changed"] == ["iris/tools.py", "tests/test_tools.py"]
    saved = _saved(caps)
    assert saved["status"] == "ready" and saved["problems"] == [] and "checked" in saved
    ruff = [argv for argv, _ in run.calls if "-m ruff" in " ".join(argv)][0]
    assert ruff[0] == str(wt / ".venv" / "Scripts" / "python.exe")


def test_check_build_runs_gates_in_the_worktree(tmp_path, monkeypatch):
    caps, wt = _building(tmp_path, monkeypatch)
    run = _GitRun(changed=["iris/tools.py"])
    check_build("printer_ink", tmp_path / "repo", run=run)
    for marker in ("-m ruff", "-m mypy", "-m pytest"):
        gate = [kwargs for argv, kwargs in run.calls if marker in " ".join(argv)]
        assert len(gate) == 1 and gate[0]["cwd"] == str(wt) and gate[0]["timeout"] == 900


def test_check_build_uses_repo_venv_when_worktree_has_none(tmp_path, monkeypatch):
    caps, wt = _building(tmp_path, monkeypatch, with_venv=False)
    repo = tmp_path / "repo"
    py = repo / ".venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    run = _GitRun(changed=["iris/tools.py"])
    check_build("printer_ink", repo, run=run)
    ruff = [argv for argv, _ in run.calls if "-m ruff" in " ".join(argv)][0]
    assert ruff[0] == str(py)


def test_check_build_protected_path(tmp_path, monkeypatch):
    caps, wt = _building(tmp_path, monkeypatch)
    run = _GitRun(changed=["iris/tools.py", "iris/agent.py"])
    meta = check_build("printer_ink", tmp_path / "repo", run=run)
    assert meta["status"] == "blocked"
    assert meta["problems"] == ["touches protected path iris/agent.py"]
    saved = _saved(caps)
    assert saved["status"] == "blocked" and saved["problems"] == ["touches protected path iris/agent.py"]
    assert not any("-m ruff" in " ".join(argv) for argv, _ in run.calls)


def test_check_build_changed_tier(tmp_path, monkeypatch):
    caps, wt = _building(tmp_path, monkeypatch)
    old = {"click": {"tier": "confirm", "escalates": True}}
    new = {"click": {"tier": "safe", "escalates": False}}
    run = _GitRun(changed=["iris/tools.py"], old_lock=old, new_lock=new)
    meta = check_build("printer_ink", tmp_path / "repo", run=run)
    assert meta["status"] == "blocked"
    assert meta["problems"] == ["changes the tier of click"]
    assert _saved(caps)["status"] == "blocked"


def test_check_build_no_commits(tmp_path, monkeypatch):
    caps, wt = _building(tmp_path, monkeypatch)
    run = _GitRun(changed=[])
    meta = check_build("printer_ink", tmp_path / "repo", run=run)
    assert meta["status"] == "blocked"
    assert meta["problems"] == ["no commits yet"]
    assert _saved(caps)["status"] == "blocked"
    assert not any("-m pytest" in " ".join(argv) for argv, _ in run.calls)


def test_check_build_failing_pytest(tmp_path, monkeypatch):
    caps, wt = _building(tmp_path, monkeypatch)
    run = _GitRun(changed=["iris/tools.py"], gate_failures={"pytest": (1, "3 failed in 2s")})
    meta = check_build("printer_ink", tmp_path / "repo", run=run)
    assert meta["status"] == "blocked"
    assert meta["problems"] == ["pytest failed: 3 failed in 2s"]
    assert _saved(caps)["status"] == "blocked"


def test_check_build_not_building(tmp_path, monkeypatch):
    caps, wt = _building(tmp_path, monkeypatch)
    (caps / "printer_ink.json").write_text(
        json.dumps({"tool_name": "printer_ink", "status": "planned"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no build to check"):
        check_build("printer_ink", tmp_path / "repo", run=_GitRun())


def test_capability_check_ready_message(tmp_path, monkeypatch):
    _building(tmp_path, monkeypatch)
    fake = {"status": "ready", "problems": [], "changed": ["iris/tools.py", "tests/test_tools.py"]}
    monkeypatch.setattr("iris.tools.check_build", lambda name, repo: fake)
    assert tools.capability_check("printer_ink") == (
        "printer_ink is ready: 2 files changed, gates green. Jay can review the branch self/printer_ink.")


def test_capability_check_blocked_message(tmp_path, monkeypatch):
    _building(tmp_path, monkeypatch)
    fake = {"status": "blocked", "problems": ["no commits yet"], "changed": []}
    monkeypatch.setattr("iris.tools.check_build", lambda name, repo: fake)
    assert tools.capability_check("printer_ink") == "printer_ink is blocked: no commits yet"


def test_capability_check_no_build(tmp_path, monkeypatch):
    _building(tmp_path, monkeypatch)

    def boom(name, repo):
        raise ValueError(f"no build to check for {name}")

    monkeypatch.setattr("iris.tools.check_build", boom)
    assert tools.capability_check("printer_ink") == "error: no build to check for printer_ink"

