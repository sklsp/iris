"""The confirm questions of the tools the main agent tests never call, and the credential check of important mode."""
import json

from iris import agent as agent_mod
from iris import browser as browser_mod
from iris.agent import Agent, Journal
from iris.llm import Reply, ToolCall
from iris.tools import CONFIRM, NEVER, Tool


class ScriptedLLM:
    """Plays back replies in order."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, messages, tools):
        return self.replies.pop(0)


def call(tool, **args):
    c = ToolCall(f"id-{tool}", tool, args)
    return Reply("", [c], {"role": "assistant", "content": "", "tool_calls": [
        {"id": c.id, "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]})


def answer(text):
    return Reply(text, [], {"role": "assistant", "content": text})


def ask(tmp_path, name, args, tier=CONFIRM):
    """One request in which the model calls the tool once; returns (the questions asked, what ran)."""
    ran = []
    tools = {name: Tool(name, "", {}, [], tier, lambda **kw: ran.append((name, kw)) or f"{name} done")}
    llm = ScriptedLLM(call(name, **args), answer("Done."))
    asked = []
    Agent(llm, lambda q: asked.append(q) or True, Journal(tmp_path / "j.jsonl"), tools).handle(f"do {name}")
    return asked, ran


def test_browser_type_question_names_the_item_and_site(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_mod, "item", lambda index: {"type": "text", "name": "Search box"})
    monkeypatch.setattr(browser_mod, "host", lambda index: "example.com")
    asked, ran = ask(tmp_path, "browser_type", {"index": 2, "text": "hello world"})
    assert asked == ["Type 'hello world' into 'Search box' on example.com?"
                     " A yes covers every click and keystroke in example.com for this request."]
    assert ran == [("browser_type", {"index": 2, "text": "hello world"})]


def test_type_text_question_names_the_target(tmp_path):
    asked, _ = ask(tmp_path, "type_text", {"into": "Notepad", "text": "hello world"})
    assert asked == ["Type 'hello world' into Notepad?"
                     " A yes covers every click and keystroke in the screen for this request."]


def test_make_document_question(tmp_path):
    asked, _ = ask(tmp_path, "make_document", {"path": "C:/tmp/report.docx"})
    assert asked == ["Make C:/tmp/report.docx as a Word document?"]


def test_make_presentation_question(tmp_path):
    asked, _ = ask(tmp_path, "make_presentation", {"path": "C:/tmp/talk.pptx"})
    assert asked == ["Make C:/tmp/talk.pptx as a PowerPoint presentation?"]


def test_make_spreadsheet_question(tmp_path):
    asked, _ = ask(tmp_path, "make_spreadsheet", {"path": "C:/tmp/numbers.xlsx"})
    assert asked == ["Make C:/tmp/numbers.xlsx as a spreadsheet?"]


def test_open_app_question_warns_it_can_change_anything(tmp_path):
    asked, _ = ask(tmp_path, "open_app", {"name": "Calculator"})
    assert asked == ["Run the program Calculator? It can change anything on this PC."]


def test_capability_build_question(tmp_path):
    asked, _ = ask(tmp_path, "capability_build", {"tool_name": "horror_films"})
    assert asked == ["Build the skill 'horror_films' again with the coding agent?"]


def test_capability_check_question(tmp_path):
    asked, _ = ask(tmp_path, "capability_check", {"tool_name": "horror_films"})
    assert asked == ["Check the new skill 'horror_films'? This runs its tests."]


def test_capability_approve_question(tmp_path):
    asked, _ = ask(tmp_path, "capability_approve", {"tool_name": "horror_films"})
    assert asked == ["Approve the skill 'horror_films'? From then on I can run its code on this PC."
                     " Review it on the dashboard first."]


def test_important_mode_checks_browser_fields_for_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_CONFIRM", "important")
    monkeypatch.setattr(browser_mod, "item", lambda index: {"type": "text", "name": "Search box"})
    monkeypatch.setattr(browser_mod, "host", lambda index: "example.com")
    asked, ran = ask(tmp_path, "browser_type", {"index": 2, "text": "hello"}, tier=NEVER)
    assert asked == ["Type 'hello' into 'Search box' on example.com?"
                     " This is a sensitive action: say yes only if you are sure."]
    assert ran == [("browser_type", {"index": 2, "text": "hello"})]


def test_important_mode_never_types_into_a_password_field(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_CONFIRM", "important")
    monkeypatch.setattr(browser_mod, "item", lambda index: {"type": "password", "name": "Wachtwoord"})
    monkeypatch.setattr(browser_mod, "host", lambda index: "example.com")
    asked, ran = ask(tmp_path, "browser_type", {"index": 2, "text": "hunter2"}, tier=NEVER)
    assert asked == []
    assert ran == []


def test_important_mode_never_types_into_a_login_window(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_CONFIRM", "important")
    asked, ran = ask(tmp_path, "type_text", {"into": "Sign in - Bank", "text": "hunter2"}, tier=NEVER)
    assert asked == []
    assert ran == []


class _BrokenBody(Exception):
    """An HTTP error whose body can not be read."""

    def read(self):
        raise OSError("the body stream broke")


def test_llm_error_keeps_the_status_when_the_body_can_not_be_read():
    # the server's reason is a bonus: when reading it fails, the status in repr(exc) still goes to the journal
    exc = _BrokenBody("502 Bad Gateway")
    out = agent_mod._llm_error(exc)
    assert out == repr(exc)


class _FailingRetryLLM:
    """Answers the last call with a tool call and no words; the retry without tools fails."""

    def __init__(self, first):
        self.first = first

    def chat(self, messages, schemas):
        if self.first is not None:
            reply, self.first = self.first, None
            return reply
        raise OSError("the model server went away")


def test_out_of_steps_survives_a_failing_retry_without_tools(tmp_path):
    # the last call answers with a tool call and no words; when the retry without tools fails, nothing is raised,
    # the journal gets an llm_error event, and Iris has nothing extra to say
    tools = {"calculate": Tool("calculate", "", {}, [], CONFIRM, lambda **kw: "2")}
    journal = Journal(tmp_path / "j.jsonl")
    agent_obj = Agent(_FailingRetryLLM(call("calculate", expression="1+1")), lambda q: True, journal, tools)
    out = agent_obj._out_of_steps([t.schema() for t in tools.values()])
    assert isinstance(out, str) and out == ""
    events = [json.loads(line) for line in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()]
    errors = [e for e in events if e.get("event") == "llm_error"]
    assert len(errors) == 1 and "OSError" in errors[0]["error"]
