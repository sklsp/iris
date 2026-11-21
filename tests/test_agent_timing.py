import json
from typing import Any, cast

from iris.agent import Agent, Journal
from iris.llm import LMStudio, Reply, ToolCall
from iris.tools import SAFE, Tool


class FakeClock:
    """Steps by a fixed amount on every read; 0.125 is an exact binary fraction, so the ms values are literal."""

    def __init__(self, step=0.125):
        self.step = step
        self.now = 0.0

    def __call__(self) -> float:
        t = self.now
        self.now += self.step
        return t


class ScriptedLLM:
    """Plays back replies in order; an exception entry is raised instead of returned."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, messages, tools):
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def call(name, **args):
    c = ToolCall(f"id-{name}", name, args)
    return Reply("", [c], {"role": "assistant", "content": "", "tool_calls": [
        {"id": c.id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})


def calls(*names):
    """A reply that asks for several tools at once; the raw messages are shaped like the server's."""
    cs = [ToolCall(f"id-{n}", n, {}) for n in names]
    return Reply("", cs, {"role": "assistant", "content": "", "tool_calls": [
        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}} for c in cs]})


def answer(text):
    return Reply(text, [], {"role": "assistant", "content": text})


def fake_tools(ran=None):
    ran = [] if ran is None else ran

    def rec(name):
        return lambda **kw: ran.append((name, kw)) or f"{name} done"
    return {"status": Tool("status", "", {}, [], SAFE, rec("status")),
            "click": Tool("click", "", {}, [], SAFE, rec("click"))}


def agent(tmp_path, llm, clock, ran=None):
    return Agent(llm, lambda q: True, Journal(tmp_path / "j.jsonl"), fake_tools(ran), clock=clock)


def timing_entries(tmp_path):
    """Every event="timing" line of the journal, without its timestamp."""
    entries = []
    for line in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["event"] == "timing":
            entries.append({k: v for k, v in entry.items() if k != "t"})
    return entries


def journal_events(tmp_path):
    """Every line of the journal, in order, without its timestamp."""
    return [{k: v for k, v in json.loads(line).items() if k != "t"}
            for line in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()]


def test_a_plain_answer_times_the_model_call_only(tmp_path):
    a = agent(tmp_path, ScriptedLLM(answer("All fine.")), FakeClock())
    assert a.handle("how is my pc") == "All fine."
    assert timing_entries(tmp_path) == [{"event": "timing", "ms": 375, "llm_ms": 125, "llm_calls": 1,
                                         "tools_ms": 0, "tool_calls": 0, "slowest_tool": None,
                                         "slowest_tool_ms": 0, "model": None}]


def test_one_tool_call_times_the_tool_and_names_it_slowest(tmp_path):
    ran: list[tuple[str, dict[str, Any]]] = []
    a = agent(tmp_path, ScriptedLLM(call("status"), answer("Done.")), FakeClock(), ran=ran)
    assert a.handle("check the status") == "Done."
    assert ran == [("status", {})]  # the tool really ran once, so its time is real
    assert timing_entries(tmp_path) == [{"event": "timing", "ms": 875, "llm_ms": 250, "llm_calls": 2,
                                         "tools_ms": 125, "tool_calls": 1, "slowest_tool": "status",
                                         "slowest_tool_ms": 125, "model": None}]


def test_a_model_error_still_writes_the_timing_entry(tmp_path):
    a = agent(tmp_path, ScriptedLLM(OSError("connection refused")), FakeClock())
    assert a.handle("how is my pc") == "I can't reach the model in LM Studio right now. Try again in a moment."
    assert timing_entries(tmp_path) == [{"event": "timing", "ms": 375, "llm_ms": 125, "llm_calls": 1,
                                         "tools_ms": 0, "tool_calls": 0, "slowest_tool": None,
                                         "slowest_tool_ms": 0, "model": None}]


def test_a_cancelled_request_still_writes_the_timing_entry(tmp_path):
    box: dict[str, Agent] = {}

    class CancellingLLM:
        def chat(self, messages, tools):
            box["agent"].cancel.set()  # the overlay's cancel button, pressed while the model was thinking
            return answer("too late.")

    llm = cast(LMStudio, CancellingLLM())
    a = Agent(llm, lambda q: True, Journal(tmp_path / "j.jsonl"), fake_tools(), clock=FakeClock())
    box["agent"] = a
    assert a.handle("do the thing") == ""
    assert timing_entries(tmp_path) == [{"event": "timing", "ms": 375, "llm_ms": 125, "llm_calls": 1,
                                         "tools_ms": 0, "tool_calls": 0, "slowest_tool": None,
                                         "slowest_tool_ms": 0, "model": None}]


def test_two_tool_calls_write_one_entry_each_before_the_request_timing(tmp_path):
    ran: list[tuple[str, dict[str, Any]]] = []
    a = agent(tmp_path, ScriptedLLM(calls("status", "click"), answer("Done.")), FakeClock(), ran=ran)
    assert a.handle("do both") == "Done."
    assert ran == [("status", {}), ("click", {})]  # both really ran, so their times are real
    events = journal_events(tmp_path)
    assert [e for e in events if e["event"] == "tool_timing"] == [
        {"event": "tool_timing", "tool": "status", "ms": 125},
        {"event": "tool_timing", "tool": "click", "ms": 125}]
    assert events[-1]["event"] == "timing"  # the request's entry comes after both tool entries


def test_a_cancelled_tool_call_writes_no_entry(tmp_path):
    a = agent(tmp_path, ScriptedLLM(calls("status", "click")), FakeClock())
    a.tools["status"] = Tool("status", "", {}, [], SAFE, lambda: a.cancel.set() or "fine")  # clicked meanwhile
    assert a.handle("do both") == ""
    events = journal_events(tmp_path)
    assert [e for e in events if e["event"] == "tool_timing"] == [{"event": "tool_timing", "tool": "status", "ms": 125}]


def test_the_timing_entry_names_the_model(tmp_path):
    class ModelLLM(ScriptedLLM):
        def _model(self) -> str:
            return "qwen-test"

    a = agent(tmp_path, ModelLLM(answer("All fine.")), FakeClock())
    assert a.handle("how is my pc") == "All fine."
    assert timing_entries(tmp_path) == [{"event": "timing", "ms": 375, "llm_ms": 125, "llm_calls": 1,
                                         "tools_ms": 0, "tool_calls": 0, "slowest_tool": None,
                                         "slowest_tool_ms": 0, "model": "qwen-test"}]


def test_the_timing_entry_has_no_model_when_the_llm_does_not_say(tmp_path):
    a = agent(tmp_path, ScriptedLLM(answer("All fine.")), FakeClock())
    assert a.handle("how is my pc") == "All fine."
    entries = timing_entries(tmp_path)
    assert len(entries) == 1 and entries[0]["model"] is None


def test_a_broken_model_reader_gives_none_and_the_request_still_answers(tmp_path):
    class BrokenModelLLM(ScriptedLLM):
        def _model(self) -> str:
            raise RuntimeError("no model")

    a = agent(tmp_path, BrokenModelLLM(answer("All fine.")), FakeClock())
    assert a.handle("how is my pc") == "All fine."
    entries = timing_entries(tmp_path)
    assert len(entries) == 1 and entries[0]["model"] is None
