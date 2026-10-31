import json
import urllib.error

import pytest

from iris.agent import MAX_HISTORY_CHARS, MAX_STEPS, Agent, Journal, _describe, system_prompt
from iris.llm import Reply, ToolCall
from iris.tools import CONFIRM, SAFE, TOOLS, Tool, _click_tier
from iris.voice import is_yes


class ScriptedLLM:
    """Plays back replies in order and records what the model was shown."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def chat(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


class RaisingLLM:
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
    return [json.loads(line) for line in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()]


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


def test_returned_error_text_is_journaled_as_an_error(tmp_path):
    ran = []
    tools = {
        "boom": Tool("boom", "", {}, [], SAFE, lambda **kw: ran.append(("boom", kw)) or "error: x"),
        "count": Tool("count", "", {}, [], SAFE, lambda **kw: "errors: 3"),
        "caps": Tool("caps", "", {}, [], SAFE, lambda **kw: "Error: x"),
        "mid": Tool("mid", "", {}, [], SAFE, lambda **kw: "ok, no error: here"),
    }
    llm = ScriptedLLM(call("boom"), call("count"), call("caps"), call("mid"), answer("done"))
    a = Agent(llm, lambda q: True, Journal(tmp_path / "j.jsonl"), tools)
    assert a.handle("go") == "done"
    entries = [e for e in journal(tmp_path) if e["event"] == "tool"]
    assert [(e["outcome"], e.get("error_kind")) for e in entries] == [
        ("error", "tool"), ("ok", None), ("ok", None), ("ok", None)]
    assert entries[0]["error"] == "error: x" and "result" not in entries[0]
    # the model still receives the exact text, wrapped as untrusted data
    contents = [llm.seen[i + 1][-1]["content"] for i in range(4)]
    assert contents == [f"<untrusted_data>\n{t}\n</untrusted_data>"
                        for t in ("error: x", "errors: 3", "Error: x", "ok, no error: here")]


def test_real_tool_tiers():
    assert TOOLS["read_window"].tier_of({"title": "x"}) == SAFE
    assert TOOLS["click"].tier_of({"window": "Chrome", "control": "Afrekenen"}) == "never"
    assert TOOLS["type_text"].tier_of({"window": "Chrome", "text": "x", "into": "Wachtwoord"}) == "never"
    assert TOOLS["type_text"].tier_of({"window": "Notepad", "text": "hi"}) == CONFIRM


def test_is_yes():
    assert is_yes("Yes please") and is_yes("ja, doe maar") and is_yes("okay")
    assert not is_yes("no") and not is_yes("nee, niet doen") and not is_yes("yes... no, stop")


def test_describe_asks_clearer_questions_for_trash_and_keys():
    assert _describe("trash_file", {"path": "W:/tmp/oud.txt"}) == "Move W:/tmp/oud.txt to the trash?"
    keys_desc = _describe("press_keys", {"window": "Naamloos - Kladblok", "keys": "^s"})
    assert keys_desc == "Press ^s in Naamloos - Kladblok?"
    assert _describe("scroll", {"window": "Chrome"}) == "Run scroll with {'window': 'Chrome'}?"


def test_describe_asks_for_cline_start_without_the_prompt_text():
    desc = _describe("cline_start", {"agent": "leadscan"})
    assert desc == "Start a new Cline session for leadscan in W:/Iris with the next queued prompt?"
    with_repo = _describe("cline_start", {"agent": "leadscan", "repo": "acme"})
    assert with_repo == "Start a new Cline session for leadscan in acme with the next queued prompt?"


def test_journal_creates_its_folder(tmp_path):
    path = tmp_path / "fresh" / "journal.jsonl"
    Journal(path).log(event="request", text="hoi")
    assert json.loads(path.read_text(encoding="utf-8"))["text"] == "hoi"


def test_history_is_bounded_to_max_chars(tmp_path):
    ran = []
    big = Tool("big", "", {}, [], SAFE, lambda **kw: ran.append(("big", kw)) or "x" * 5000)
    replies = []
    for i in range(30):
        replies += [call("big"), answer(f"turn {i} done")]
    llm = ScriptedLLM(*replies)
    a = Agent(llm, lambda q: True, Journal(tmp_path / "j.jsonl"), {"big": big})
    for i in range(30):
        assert a.handle(f"do thing {i}") == f"turn {i} done"
        size = sum(len(json.dumps(m, ensure_ascii=False)) for m in a.messages)
        users = [m for m in a.messages if m["role"] == "user"]
        assert size <= MAX_HISTORY_CHARS or len(users) == 1
    assert len(ran) == 30
    assert a.messages[0] == {"role": "system", "content": system_prompt()}
    assert a.messages[1]["role"] == "user"


def test_short_session_keeps_every_turn(tmp_path):
    ran = []
    llm = ScriptedLLM(call("status"), answer("one."), call("status"), answer("two."),
                      call("status"), answer("three."))
    a = agent(tmp_path, llm, ran)
    assert a.handle("first") == "one."
    assert a.handle("second") == "two."
    assert a.handle("third") == "three."
    users = [m for m in a.messages if m["role"] == "user"]
    assert [m["content"] for m in users] == ["first", "second", "third"]


def test_one_huge_request_is_kept_and_answered(tmp_path):
    ran = []
    llm = ScriptedLLM(answer("Got it."))
    a = agent(tmp_path, llm, ran)
    huge = "y" * 100_000
    assert a.handle(huge) == "Got it."
    assert any(m["role"] == "user" and m["content"] == huge for m in a.messages)


def test_llm_error_ends_the_turn_not_iris(tmp_path):
    ran = []
    llm = RaisingLLM(urllib.error.URLError("connection refused"))
    a = agent(tmp_path, llm, ran)
    before = [dict(m) for m in a.messages]
    assert a.handle("hello") == "I can't reach the model in LM Studio right now. Try again in a moment."
    assert a.messages == before
    errors = [e for e in journal(tmp_path) if e["event"] == "llm_error"]
    assert len(errors) == 1 and "URLError" in errors[0]["error"]


def test_llm_error_keeps_earlier_turns_and_the_next_turn_works(tmp_path):
    ran = []
    llm = RaisingLLM(answer("one."), TimeoutError(), answer("three."))
    a = agent(tmp_path, llm, ran)
    assert a.handle("first") == "one."
    before = [dict(m) for m in a.messages]
    assert a.handle("second") == "I can't reach the model in LM Studio right now. Try again in a moment."
    assert a.messages == before
    users = [m["content"] for m in a.messages if m["role"] == "user"]
    assert users == ["first"]
    assert a.handle("third") == "three."


def test_llm_error_after_a_tool_call_removes_the_whole_turn(tmp_path):
    ran = []
    http_400 = urllib.error.HTTPError("http://127.0.0.1:1234", 400, "context full", None, None)
    llm = RaisingLLM(call("status"), http_400)
    a = agent(tmp_path, llm, ran)
    before = [dict(m) for m in a.messages]
    assert a.handle("check") == "I can't reach the model in LM Studio right now. Try again in a moment."
    assert a.messages == before
    assert len(ran) == 1


def test_runtime_error_from_chat_is_not_caught(tmp_path):
    llm = RaisingLLM(RuntimeError("boom"))
    a = agent(tmp_path, llm, [])
    with pytest.raises(RuntimeError):
        a.handle("hello")


def test_a_model_that_ran_out_of_tokens_gets_its_own_answer(tmp_path):
    from iris.agent import Agent, Journal

    class LLM:
        def chat(self, messages, tools):
            raise ValueError("the model ran out of tokens while thinking")

    answer = Agent(LLM(), lambda q: True, Journal(tmp_path / "j.jsonl"), {}).handle("Maak een PowerPoint")
    assert answer == "That took me too long to think through. Try asking it in a shorter way."
