"""config.toml and the plugin registry: what the user sets decides the name in the prompt and the tools offered."""

from iris import config, registry
from iris.agent import Agent, Journal, system_prompt
from iris.tools import TOOLS


def test_missing_broken_and_wrong_typed_config_fall_back_to_defaults(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("IRIS_CONFIG", str(path))
    assert config.load() == config.DEFAULTS
    path.write_text("name = [", encoding="utf-8")
    assert config.load() == config.DEFAULTS
    path.write_text('name = 3\nplugins = "comfyui"', encoding="utf-8")
    assert config.load() == config.DEFAULTS


def test_config_sets_name_and_plugins(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('name = " Jay "\nplugins = ["comfyui", 7]\n', encoding="utf-8")
    monkeypatch.setenv("IRIS_CONFIG", str(path))
    assert config.load() == {"name": "Jay", "plugins": ["comfyui"], "builder": [], "folders": []}
    assert "Jay's Windows PC" in system_prompt()
    assert "Today is Friday 31 October 2026." in system_prompt(today="Friday 31 October 2026")


def test_plugins_are_off_until_enabled():
    core = registry.active_tools({"plugins": []})
    plugin_tools = {n for names in registry.PLUGINS.values() for n in names}
    assert plugin_tools <= set(TOOLS)  # every plugin tool is a real, tier-locked tool
    assert not plugin_tools & set(core)
    assert "calculate" in core and "click" in core
    with_comfy = registry.active_tools({"plugins": ["comfyui", "no_such_plugin"]})
    assert {"comfy_status", "comfy_queue"} <= set(with_comfy) and "followups" not in with_comfy


class _LLM:
    def __init__(self):
        self.seen = []

    def chat(self, messages, schemas):
        from iris.llm import Reply

        self.seen.append({s["function"]["name"] for s in schemas})
        return Reply("ok", [], {"role": "assistant", "content": "ok"})


def test_agent_asks_for_its_tools_on_every_request(tmp_path):
    offered = [{"a": TOOLS["calculate"]}]
    llm = _LLM()
    agent = Agent(llm, lambda q: True, Journal(tmp_path / "j.jsonl"), lambda: offered[-1])
    agent.handle("one")
    offered.append({"a": TOOLS["calculate"], "b": TOOLS["timer_list"]})
    agent.handle("two")
    assert llm.seen == [{"calculate"}, {"calculate", "timer_list"}]


def test_doctor_reports_what_to_fix(tmp_path, monkeypatch):
    from iris import doctor

    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "config.toml"))
    (tmp_path / "config.toml").write_text('plugins = ["comfyui", "nope"]', encoding="utf-8")
    monkeypatch.setenv("IRIS_MODEL", "qwen-x")
    payload = {"reachable": True, "models": [{"id": "qwen-x"}]}
    lines = dict((line.split(" ")[0], ok) for ok, line in doctor.checks(payload))
    assert lines["model"] is True and lines["plugins"] is False
    down = [line for ok, line in doctor.checks({"reachable": False}) if not ok]
    assert any("LM Studio not reachable" in line for line in down)


def test_doctor_checks_the_skill_builder(tmp_path, monkeypatch):
    from iris import doctor

    monkeypatch.delenv("IRIS_CLINE_CLI", raising=False)
    (tmp_path / "config.toml").write_text('builder = ["no-such-agent-xyz", "{prompt}"]', encoding="utf-8")
    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "config.toml"))
    lines = [line for ok, line in doctor.checks({"reachable": False}) if line.startswith("skill builder")]
    assert lines == ["skill builder no-such-agent-xyz not found: fix builder in config.toml"]
    (tmp_path / "config.toml").write_text(f'cline_cli = "{(tmp_path / "config.toml").as_posix()}"', encoding="utf-8")
    ok_lines = [ok for ok, line in doctor.checks({"reachable": False}) if line.startswith("skill builder Cline")]
    assert ok_lines == [True]


def test_folders_are_read_from_the_config(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('folders = ["W:/", "D:/Projects"]\n', encoding="utf-8")
    monkeypatch.setenv("IRIS_CONFIG", str(path))
    assert config.load()["folders"] == ["W:/", "D:/Projects"]


def test_folders_ignore_wrong_types(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("IRIS_CONFIG", str(path))
    path.write_text('folders = "W:/"\n', encoding="utf-8")  # a string is not a list
    assert config.load()["folders"] == []
    path.write_text('folders = [7, "D:/p"]\n', encoding="utf-8")  # non-strings are dropped
    assert config.load()["folders"] == ["D:/p"]


def test_folders_lists_home_first_then_configured_without_duplicates(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() on Windows
    proj = tmp_path / "proj"
    path = tmp_path / "config.toml"
    path.write_text(f'folders = ["{proj.as_posix()}", "{home.as_posix()}"]\n', encoding="utf-8")
    monkeypatch.setenv("IRIS_CONFIG", str(path))
    assert config.folders() == [home, proj]  # home first; the duplicate of home is dropped



def test_version_is_0_4_0(capsys, monkeypatch):
    import sys

    import pytest

    from iris import __main__ as imain

    monkeypatch.setattr(sys, "argv", ["iris", "--version"])
    with pytest.raises(SystemExit):
        imain.main()
    assert capsys.readouterr().out.strip() == "iris 0.5.0"


def test_doctor_marks_optional_misses_and_exits_on_required_ones(capsys, monkeypatch):
    from iris import doctor

    monkeypatch.setattr(doctor, "checks", lambda: [(True, "Python 3.12"), (False, "voice extras missing"),
                                                   (False, "skill builder Cline (cline) not found")])
    assert doctor.main() == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["ok  Python 3.12", "--  voice extras missing", "--  skill builder Cline (cline) not found"]
    monkeypatch.setattr(doctor, "checks", lambda: [(False, "LM Studio not reachable")])
    assert doctor.main() == 1
    assert capsys.readouterr().out.strip() == "FIX LM Studio not reachable"


def test_doctor_says_whether_the_model_reads_images(tmp_path, monkeypatch):
    from iris import doctor

    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("IRIS_MODEL", "qwen-x")

    def vision(kind, known=True):
        payload = {"reachable": True, "states_known": known, "models": [{"id": "qwen-x", "type": kind}]}
        return [(ok, line) for ok, line in doctor.checks(payload) if line.startswith("vision")]

    assert vision("vlm") == [(True, "vision: qwen-x reads images (look_at_screen, click_on_screen)")]
    assert vision("llm")[0][0] is False and "pick a vision model" in vision("llm")[0][1]
    assert vision(None, known=False) == []  # an old LM Studio without types: say nothing rather than guess


def test_init_writes_a_starter_config_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "iris" / "config.toml"))
    answers = iter(['Zoë "Z"', "Utrecht"])
    assert config.init_main(lambda q: next(answers)) == 0
    assert config.load()["name"] == 'Zoë "Z"' and config.get("city") == "Utrecht"
    assert "# plugins" in config.config_path().read_text(encoding="utf-8")
    assert config.init_main(lambda q: "x") == 1 and "exists already" in capsys.readouterr().out
    config.config_path().unlink()
    assert config.init_main(lambda q: "") == 0
    assert config.load()["name"] == "the user" and config.get("city") == ""


def test_doctor_reads_the_speech_engine_from_config_and_names_missing_voices(tmp_path, monkeypatch):
    import importlib.util

    from iris import doctor
    from iris import voice as v

    monkeypatch.delenv("IRIS_TTS", raising=False)
    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "config.toml"))
    (tmp_path / "config.toml").write_text('tts = "piper"', encoding="utf-8")
    monkeypatch.setattr(v, "PIPER_DIR", tmp_path)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: object())  # every package "installed"
    lines = [(ok, line) for ok, line in doctor.checks({"reachable": False}) if line.startswith("speech")]
    assert lines and lines[0][0] is False and "download_voices nl_NL-ronnie-medium en_US-joe-medium" in lines[0][1]
