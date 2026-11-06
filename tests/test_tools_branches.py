"""The branches of iris/tools.py that tests/test_tools.py does not reach: comfy_queue against a broken workflow
file, cline_start handing off to the autopilot, _wall_now itself, capability_check on a blocked skill and click's
fallback when invoke is or is not supported. No real ComfyUI, Cline session, network or desktop."""

import sys
import types
from datetime import datetime

from iris import skills, tools
from tests.test_tools_behaviour import FakeControl, FakeDesktop, FakeWindow, _install_desktop


def test_comfy_queue_says_could_not_read_a_broken_workflow_file(tmp_path, monkeypatch):
    folder = tmp_path / "workflows"
    folder.mkdir()
    (folder / "bad.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("IRIS_COMFY_WORKFLOWS", str(folder))
    assert tools.comfy_queue("bad") == "error: bad.json is not valid JSON; export it again from ComfyUI in API format"


def test_cline_start_hands_the_agent_and_repo_to_autopilot(monkeypatch):
    calls = []

    def fake_start(agent, repo=""):
        calls.append((agent, repo))
        return "started cline for coder"

    mod = types.ModuleType("iris.autopilot")
    mod.cline_start = fake_start
    monkeypatch.setitem(sys.modules, "iris.autopilot", mod)
    assert tools.cline_start("coder", "W:/repos/x") == "started cline for coder"
    assert calls == [("coder", "W:/repos/x")]


def test_wall_now_returns_the_current_datetime():
    now = tools._wall_now()
    assert isinstance(now, datetime)
    assert abs((datetime.now() - now).total_seconds()) < 5


def test_capability_check_reports_a_blocked_skill_with_its_problems(tmp_path):
    folder = skills.skills_dir() / "bad_skill"
    folder.mkdir(parents=True)
    skills._save_meta("bad_skill", {"name": "bad_skill", "tier": "safe", "status": "building"})
    out = tools.capability_check("bad_skill")
    assert out.startswith("bad_skill is blocked: ")
    assert "skill.py is missing; test_skill.py is missing" in out


def test_click_falls_back_to_a_real_click_when_invoke_is_not_supported(monkeypatch):
    stubborn = FakeControl(text="Go", kind="Button")

    def invoke():
        stubborn.invoke_calls += 1
        raise NotImplementedError("Invoke pattern not supported")

    monkeypatch.setattr(stubborn, "invoke", invoke)
    _install_desktop(monkeypatch, FakeDesktop([FakeWindow("Notepad", [stubborn])]))

    assert tools.click("notepad", "go") == "clicked Button 'Go'"
    assert stubborn.invoke_calls == 1 and stubborn.click_input_calls == 1


def test_click_falls_back_on_pywinauto_s_own_missing_pattern_error(monkeypatch):
    class NoPatternInterfaceError(Exception):  # pywinauto.uia_defines raises it with no message at all
        pass

    plain = FakeControl(text="Go", kind="Button")

    def invoke():
        raise NoPatternInterfaceError()

    monkeypatch.setattr(plain, "invoke", invoke)
    _install_desktop(monkeypatch, FakeDesktop([FakeWindow("Notepad", [plain])]))

    assert tools.click("notepad", "go") == "clicked Button 'Go'"
    assert plain.click_input_calls == 1


def test_click_does_not_click_again_when_invoke_acted_and_then_failed(monkeypatch):
    flaky = FakeControl(text="OK", kind="Button")

    def invoke():
        flaky.invoke_calls += 1
        raise RuntimeError("timeout")

    monkeypatch.setattr(flaky, "invoke", invoke)
    _install_desktop(monkeypatch, FakeDesktop([FakeWindow("Notepad", [flaky])]))

    out = tools.click("notepad", "ok")
    assert out.startswith("clicked")
    assert flaky.invoke_calls == 1 and flaky.click_input_calls == 0
