"""The branches of iris/tools.py that tests/test_tools.py does not reach: comfy_queue against a broken workflow
file, cline_start handing off to the autopilot, _wall_now itself and capability_check on a blocked skill. No real
ComfyUI, Cline session or network."""

import sys
import types
from datetime import datetime

from iris import skills, tools


def test_comfy_queue_says_could_not_read_a_broken_workflow_file(tmp_path, monkeypatch):
    folder = tmp_path / "workflows"
    folder.mkdir()
    (folder / "bad.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("IRIS_COMFY_WORKFLOWS", str(folder))
    assert tools.comfy_queue("bad") == "error: could not read bad.json"


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
