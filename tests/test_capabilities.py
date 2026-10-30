"""The guard, and the plan files for skills Iris does not have yet."""

import json
import re
from pathlib import Path

import pytest

from iris import tools
from iris.agent import _describe
from iris.capabilities import PLAN_TEMPLATE, capabilities_dir, guard_problems, list_plans, save_plan


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
