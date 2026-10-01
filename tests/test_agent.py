import json

from hermes.agent import MAX_STEPS, Agent, Journal
from hermes.llm import Reply, ToolCall
from hermes.tools import CONFIRM, SAFE, TOOLS, Tool, _click_tier
from hermes.voice import is_yes


class ScriptedLLM:
    """Plays back replies in order and records what the model was shown."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def chat(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


def call(name, **args):
    c = ToolCall(f"id-{name}", name, args)
    return Reply("", [c], {"role": "assistant", "content": "", "tool_calls": [
        {"id": c.id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})


def answer(text):
    return Reply(text, [], {"role": "assistant", "content": text})


def fake_tools(ran):
    def rec(name):
        return lambda **kw: ran.append((name, kw)) or f"{name} done"
    return {
        "status": Tool("status", "", {}, [], SAFE, rec("status")),
        "click": Tool("click", "", {}, [], CONFIRM, rec("click"), _click_tier),
    }


def agent(tmp_path, llm, ran, confirm=lambda q: True):
    return Agent(llm, confirm, Journal(tmp_path / "j.jsonl"), fake_tools(ran))


def journal(tmp_path):
    return [json.loads(l) for l in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()]


def test_safe_tool_runs_without_asking_and_result_is_marked_untrusted(tmp_path):
    ran, asked = [], []
    llm = ScriptedLLM(call("status"), answer("All fine."))
    a = agent(tmp_path, llm, ran, confirm=lambda q: asked.append(q) or True)
    assert a.handle("how is my pc") == "All fine."
    assert ran == [("status", {})] and asked == []
    tool_msg = llm.seen[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["content"].startswith("<untrusted_data>")


def test_confirm_tool_declined_is_not_run(tmp_path):
    ran = []
    llm = ScriptedLLM(call("click", window="Notepad", control="Bold"), answer("Okay, I left it."))
    agent(tmp_path, llm, ran, confirm=lambda q: False).handle("make it bold")
    assert ran == []
    assert [e.get("outcome") for e in journal(tmp_path) if e["event"] == "tool"] == ["declined"]


def test_never_words_are_refused_even_when_jay_says_yes(tmp_path):
    ran = []
    for control in ("Send", "Verzenden", "Betalen", "Delete"):
        llm = ScriptedLLM(call("click", window="Mail", control=control), answer("Do that yourself."))
        agent(tmp_path, llm, ran, confirm=lambda q: True).handle(f"press {control}")
    assert ran == []
    assert {e["outcome"] for e in journal(tmp_path) if e["event"] == "tool"} == {"refused"}


def test_confirmed_click_runs(tmp_path):
    ran = []
    llm = ScriptedLLM(call("click", window="Notepad", control="Bold"), answer("Done."))
    agent(tmp_path, llm, ran).handle("bold")
    assert ran == [("click", {"window": "Notepad", "control": "Bold"})]


def test_loop_stops_after_max_steps(tmp_path):
    ran = []
    llm = ScriptedLLM(*[call("status") for _ in range(MAX_STEPS)])
    assert "too many steps" in agent(tmp_path, llm, ran).handle("loop")
    assert len(ran) == MAX_STEPS


def test_unknown_tool_goes_back_to_the_model(tmp_path):
    llm = ScriptedLLM(call("format_disk"), answer("I can't do that."))
    agent(tmp_path, llm, []).handle("format c")
    assert "unknown tool" in llm.seen[1][-1]["content"]


def test_real_tool_tiers():
    assert TOOLS["read_window"].tier_of({"title": "x"}) == SAFE
    assert TOOLS["click"].tier_of({"window": "Chrome", "control": "Afrekenen"}) == "never"
    assert TOOLS["type_text"].tier_of({"window": "Chrome", "text": "x", "into": "Wachtwoord"}) == "never"
    assert TOOLS["type_text"].tier_of({"window": "Notepad", "text": "hi"}) == CONFIRM


def test_is_yes():
    assert is_yes("Yes please") and is_yes("ja, doe maar") and is_yes("okay")
    assert not is_yes("no") and not is_yes("nee, niet doen") and not is_yes("yes... no, stop")
