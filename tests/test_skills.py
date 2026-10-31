"""Skills Iris builds for herself: plan, build, check, approve, and run only approved, unchanged code out of process."""

import json
import subprocess
import threading

import pytest

from iris import registry, skills, tools

SKILL_TOML = 'description = "Says hello to someone."\nrequired = ["who"]\n[params.who]\ntype = "string"\n'
SKILL_PY = "def run(who):\n    return f'hello {who}'\n"
TEST_PY = "import skill\n\n\ndef test_hello():\n    assert skill.run(who='Mark') == 'hello Mark'\n"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_SKILLS", str(tmp_path / "skills"))
    monkeypatch.setenv("IRIS_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "config.toml"))
    return tmp_path


def _planned(name="say_hello", tier="safe"):
    return skills.save_plan("Zeg hallo tegen Mark", name, tier, "A run(who) that greets.", set(tools.TOOLS))


def _built(name="say_hello", tier="safe", py=SKILL_PY, test=TEST_PY):
    folder = _planned(name, tier)
    skills.start_build(name, start=lambda prompt, cwd: "started fake")
    (folder / "skill.toml").write_text(SKILL_TOML, encoding="utf-8")
    (folder / "skill.py").write_text(py, encoding="utf-8")
    (folder / "test_skill.py").write_text(test, encoding="utf-8")
    (folder / "DONE").write_text("", encoding="utf-8")
    return folder


def test_save_plan_writes_the_plan_and_refuses_bad_input():
    folder = _planned()
    assert "Tier: safe" in (folder / "plan.md").read_text(encoding="utf-8")
    assert skills.meta("say_hello")["status"] == "planned"
    for args, why in [(("x", "Bad-Name", "safe", "p"), "invalid skill name"),
                      (("x", "calculate", "safe", "p"), "already exists"),
                      (("x", "new_one", "never", "p"), "tier must be"),
                      (("x", "new_one", "safe", " "), "may not be empty"),
                      (("x", "new_one", "safe", "p" * 4001), "too long"),
                      (("x", "say_hello", "safe", "p"), "exists")]:
        with pytest.raises(ValueError, match=why):
            skills.save_plan(*args, set(tools.TOOLS))


def test_start_build_hands_the_plan_to_the_builder_and_clears_an_old_attempt():
    folder = _planned()
    (folder / "skill.py").write_text("old", encoding="utf-8")
    seen = []
    assert skills.start_build("say_hello", start=lambda p, cwd: seen.append((p, cwd)) or "started x") == "started x"
    assert str(folder / "plan.md") in seen[0][0] and seen[0][1] == str(folder)
    assert not (folder / "skill.py").exists()
    assert skills.meta("say_hello")["status"] == "building"
    with pytest.raises(ValueError, match="is building"):
        skills.start_build("say_hello", start=lambda p, cwd: "again")


def test_a_configured_builder_command_gets_the_prompt(home, monkeypatch):
    (home / "config.toml").write_text('builder = ["claude", "-p", "{prompt}"]', encoding="utf-8")
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: started.append((argv, kw["cwd"])))
    _planned()
    assert skills.start_build("say_hello") == "started claude"
    argv, cwd = started[0]
    assert argv[:2] == ["claude", "-p"] and "plan.md" in argv[2] and cwd == skills.skills_dir() / "say_hello"


def test_check_runs_the_skills_own_tests_and_marks_it_ready():
    _built()
    data = skills.check("say_hello")
    assert data["status"] == "ready", data["problems"]
    assert "1 passed" in (skills.skills_dir() / "say_hello" / "check.txt").read_text(encoding="utf-8")


def test_check_blocks_failing_tests_missing_files_and_code_that_reaches_into_iris():
    _built(test=TEST_PY.replace("hello Mark", "bye"))
    data = skills.check("say_hello")
    assert data["status"] == "blocked" and data["problems"][0].startswith("tests failed")

    folder = _built("reach_in", py="from iris import policy\n\ndef run(who):\n    return who\n")
    (folder / "test_skill.py").unlink()
    problems = skills.check("reach_in")["problems"]
    assert "skill.py imports Iris itself" in problems and "test_skill.py is missing" in problems

    folder = _built("no_spec")
    (folder / "skill.toml").write_text('description = ""\nrequired = ["x"]', encoding="utf-8")
    problems = skills.check("no_spec")["problems"]
    assert problems == ["skill.toml has no description", "skill.toml required must list parameter names"]


def test_check_needs_a_build():
    _planned()
    with pytest.raises(ValueError, match="has not been built"):
        skills.check("say_hello")


def test_check_reports_a_test_timeout():
    _built()

    def slow(*a, **kw):
        raise subprocess.TimeoutExpired("pytest", 300)

    assert skills.check("say_hello", run=slow)["problems"] == ["tests timed out"]


def test_only_approved_unchanged_skills_are_offered_and_they_run_out_of_process():
    folder = _built()
    with pytest.raises(ValueError, match="only a ready skill"):
        skills.approve("say_hello")
    skills.check("say_hello")
    assert "say_hello" not in registry.active_tools()
    assert skills.approve("say_hello") == "approved say_hello"
    tool = registry.active_tools()["say_hello"]
    assert tool.tier == "safe" and tool.required == ["who"] and "built herself" in tool.description
    assert tool.fn(who="Mark") == "hello Mark"  # a real separate python process
    assert "approved" in skills.listing()

    (folder / "skill.py").write_text(SKILL_PY.replace("hello", "HACKED"), encoding="utf-8")
    assert "say_hello" not in registry.active_tools()  # changed after approval: never loaded again


def test_the_tier_is_the_planned_one_and_changing_it_voids_the_approval():
    folder = _built(tier="confirm")
    skills.check("say_hello")
    skills.approve("say_hello")
    assert registry.active_tools()["say_hello"].tier == "confirm"
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    meta["tier"] = "safe"
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert "say_hello" not in registry.active_tools()


def test_a_skill_named_like_a_core_tool_is_never_offered(home):
    _built()
    skills.check("say_hello")
    skills.approve("say_hello")
    assert skills.approved_tools({"say_hello"}) == []


def test_run_skill_reports_errors_and_timeouts():
    _built(py="def run(who):\n    raise RuntimeError('boom')\n")
    assert skills.run_skill("say_hello", {"who": "x"}).startswith("error: say_hello failed: RuntimeError: boom")

    def slow(*a, **kw):
        raise subprocess.TimeoutExpired("python", 120)

    assert skills.run_skill("say_hello", {}, run=slow) == "error: say_hello took longer than 120 seconds"
    big = subprocess.CompletedProcess([], 0, "x" * 30000, "")
    assert skills.run_skill("say_hello", {}, run=lambda *a, **kw: big).endswith("... (truncated)")


def test_remove_moves_the_skill_to_the_trash_and_drops_the_approval(home):
    _built()
    skills.check("say_hello")
    skills.approve("say_hello")
    assert skills.remove("say_hello") == "removed say_hello; it is in Iris's trash"
    assert not (skills.skills_dir() / "say_hello").exists()
    assert any("skill_say_hello" in p.name for p in (home / "trash").iterdir())
    assert "say_hello" not in json.loads((skills.skills_dir() / "approved.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="no skill named"):
        skills.remove("say_hello")


def test_the_watcher_checks_finished_builds_and_says_how_it_went():
    _built("good_one")
    _built("bad_one")
    _planned("not_built")
    said = []
    stop = threading.Event()

    def fake_check(name):
        stop.set()  # one round is enough
        status = "ready" if name == "good_one" else "blocked"
        skills._save_meta(name, {**skills.meta(name), "status": status})
        return {"status": status, "problems": [] if status == "ready" else ["tests failed: 1 failed"]}

    assert skills.finished_builds() == ["bad_one", "good_one"]
    skills.watch(said.append, interval=0, stop=stop, check_fn=fake_check).join(5)
    assert said == ["The new skill bad_one is blocked: tests failed: 1 failed",
                    "The new skill good_one is built and its tests pass. Review it on the dashboard, "
                    "or ask me to approve good_one."]
    assert skills.finished_builds() == []


def test_request_capability_plans_and_starts_the_build_in_one_yes(monkeypatch):
    monkeypatch.setattr(skills, "start_build", lambda name: "started fake")
    out = tools.request_capability("Zeg hallo", "say_hello", "safe", "A run(who).")
    assert out == ("Building the new skill say_hello. I will say when it is ready; "
                   "I only use it after the user approves it.")
    assert tools.request_capability("x", "calculate", "safe", "p") == "error: calculate already exists"

    def broken(name):
        raise LookupError("cline-cli.exe not found")

    monkeypatch.setattr(skills, "start_build", broken)
    assert tools.request_capability("Zeg dag", "say_bye", "safe", "p") == (
        "Saved the plan for say_bye, but the builder did not start: cline-cli.exe not found. Ask to build it later.")


def test_the_capability_tools_report_in_plain_words(monkeypatch):
    _built()
    assert tools.capability_check("say_hello") == (
        "say_hello is ready: its tests pass. The user can review it on the dashboard and approve it.")
    assert tools.capability_approve("say_hello") == "approved say_hello; I can use it from the next request on."
    assert tools.capability_build("say_hello") == "error: say_hello is ready, not planned or blocked"
    assert tools.capability_check("nope_x") == "error: no skill named nope_x"
    assert tools.capability_approve("nope_x") == "error: no skill named nope_x"
    assert tools.capability_remove("say_hello") == "removed say_hello; it is in Iris's trash"
    assert tools.capability_remove("say_hello") == "error: no skill named say_hello"
    assert tools.capability_list() == "No skills yet."
    _planned("other_one")
    monkeypatch.setattr(skills, "start_build", lambda name: "started")
    assert tools.capability_build("other_one") == "Building other_one. I will say when it is ready."
