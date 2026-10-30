"""The guard: what a self-built branch may never touch or change."""

from iris.capabilities import guard_problems


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
