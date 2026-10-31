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
    assert config.load() == {"name": "Jay", "plugins": ["comfyui"]}
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
