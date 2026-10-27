"""The safety rules of Iris, as tests: tiers are enforced in code, not in the prompt."""

import json
from pathlib import Path

import pytest

from iris import tools
from iris.agent import Agent, Journal
from iris.llm import Reply, ToolCall

ALL = sorted(tools.TOOLS.values(), key=lambda t: t.name)


@pytest.mark.parametrize("tool", ALL, ids=[t.name for t in ALL])
def test_every_tool_has_a_known_tier(tool):
    assert tool.tier in (tools.SAFE, tools.CONFIRM, tools.NEVER)


@pytest.mark.parametrize("tool", ALL, ids=[t.name for t in ALL])
def test_schema_description_starts_with_its_tier(tool):
    description = tool.schema()["function"]["description"]
    assert description.startswith(f"[{tool.tier}]")


@pytest.mark.parametrize("tool", ALL, ids=[t.name for t in ALL])
def test_required_params_are_all_in_params(tool):
    missing = set(tool.required) - set(tool.params)
    assert not missing, f"{tool.name}: required {sorted(missing)} are not in its params"


# --- argument escalation: the tier can rise with the arguments -----------------
# Expected tiers below were checked against the real functions. "Poster" and "Payment" escalate because
# _NEVER_WORDS matches word starts without a trailing boundary; tools.py documents that over-refusal as
# an accepted tradeoff, so these rows assert the real result on purpose.

CLICK_TIER = [
    ({"control": "OK"}, tools.CONFIRM),
    ({"control": "Send"}, tools.NEVER),
    ({"control": "Verzenden"}, tools.NEVER),
    ({"control": "Betalen"}, tools.NEVER),
    ({"control": "wachtwoord"}, tools.NEVER),
    ({"control": "sEnD"}, tools.NEVER),  # mixed case still matches
    ({"control": "Poster"}, tools.NEVER),  # over-refusal: "post" at a word start, documented in tools.py
    ({"control": "Reorder"}, tools.CONFIRM),  # "order" only inside another word: no escalation
]


@pytest.mark.parametrize("args, expected", CLICK_TIER, ids=[str(a["control"]) for a, _ in CLICK_TIER])
def test_click_tier(args, expected):
    assert tools._click_tier(args) == expected


TYPE_TIER = [
    ({"into": "Subject"}, tools.CONFIRM),
    ({"into": "Password"}, tools.NEVER),
    ({"into": "wachtwoord"}, tools.NEVER),
    ({"into": "Bestelnummer"}, tools.NEVER),  # Dutch: an order-number field is still order-related
    ({"into": "Amount"}, tools.CONFIRM),
    ({"into": "Payment"}, tools.NEVER),  # over-refusal: "pay" at a word start, documented in tools.py
    ({"into": "Reorder"}, tools.CONFIRM),  # "order" only inside another word: no escalation
]


@pytest.mark.parametrize("args, expected", TYPE_TIER, ids=[str(a["into"]) for a, _ in TYPE_TIER])
def test_type_tier(args, expected):
    assert tools._type_tier(args) == expected


PRESS_KEYS_TIER = [
    ({"keys": "^s", "window": "Outlook"}, tools.CONFIRM),  # no Enter: saving is fine even in Outlook
    ({"keys": "{TAB}", "window": "Gmail"}, tools.CONFIRM),
    ({"keys": "{ENTER}", "window": "Notepad"}, tools.CONFIRM),  # Enter outside mail/chat sends nothing
    ({"keys": "{ENTER}", "window": "Outlook"}, tools.NEVER),
    ({"keys": "{enter}", "window": "Gmail"}, tools.NEVER),  # mixed case still matches
    ({"keys": "{ENTER}", "window": "WhatsApp"}, tools.NEVER),
    ({"keys": "{ENTER}", "window": "Discord"}, tools.NEVER),
    ({"keys": "{ENTER}", "window": "Teams"}, tools.NEVER),
    ({"keys": "{ENTER}", "window": "Slack"}, tools.NEVER),
    ({"keys": "{ENTER}{BACKSPACE}", "window": "Outlook"}, tools.NEVER),  # Enter anywhere in the string counts
]


@pytest.mark.parametrize("args, expected", PRESS_KEYS_TIER,
                         ids=[f"{a['keys']}@{a['window']}" for a, _ in PRESS_KEYS_TIER])
def test_press_keys_tier(args, expected):
    assert tools._press_keys_tier(args) == expected


TRASH_TIER = [
    (str(Path.home() / "notes.txt"), tools.CONFIRM),  # under the home folder: allowed
    ("W:/scratch.txt", tools.CONFIRM),  # under W:/: allowed
    (str(Path.home()), tools.NEVER),  # a root itself is never trashed
    ("W:/", tools.NEVER),
    ("C:/Windows/system32", tools.NEVER),  # outside the roots and a system dir
    ("C:/Program Files", tools.NEVER),
    (str(Path.home() / ".iris" / "trash"), tools.NEVER),  # the trash itself is never trashed
]


@pytest.mark.parametrize("path, expected", TRASH_TIER, ids=[p.replace("\\", "/") for p, _ in TRASH_TIER])
def test_trash_tier(path, expected, monkeypatch):
    monkeypatch.delenv("IRIS_TRASH_DIR", raising=False)  # keep the default trash dir deterministic
    assert tools._trash_tier({"path": path}) == expected


# --- a declined action never runs -------------------------------------------------
class _ScriptedLLM:
    """Plays back replies in order and records what the model was shown."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def chat(self, messages, schemas):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


def _confirm_call(**args):
    c = ToolCall("id-click", "click", args)
    return Reply("", [c], {"role": "assistant", "content": "", "tool_calls": [
        {"id": c.id, "type": "function", "function": {"name": "click", "arguments": json.dumps(args)}}]})


def _answer(text):
    return Reply(text, [], {"role": "assistant", "content": text})


def _agent(tmp_path, llm, ran, confirm):
    tool = tools.Tool("click", "", {}, [], tools.CONFIRM, lambda **kw: ran.append(kw) or "clicked")
    return Agent(llm, confirm, Journal(tmp_path / "j.jsonl"), {"click": tool})


def test_declined_confirm_tool_never_runs(tmp_path):
    ran = []
    llm = _ScriptedLLM(_confirm_call(window="Notepad", control="Bold"), _answer("Okay, I left it."))
    _agent(tmp_path, llm, ran, lambda q: False).handle("make it bold")
    assert ran == []  # the tool fn was never called
    tool_msg = llm.seen[1][-1]
    assert tool_msg["role"] == "tool" and "declined" in tool_msg["content"]
    entries = [json.loads(line) for line in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e.get("outcome") for e in entries if e["event"] == "tool"] == ["declined"]


def test_confirmed_confirm_tool_runs_exactly_once(tmp_path):
    ran = []
    llm = _ScriptedLLM(_confirm_call(window="Notepad", control="Bold"), _answer("Done."))
    _agent(tmp_path, llm, ran, lambda q: True).handle("make it bold")
    assert ran == [{"window": "Notepad", "control": "Bold"}]


# --- a never action is never even asked -------------------------------------------
def _assert_refused(tmp_path, llm, ran, asked):
    assert ran == [] and asked == []  # fn never called, confirm never called either
    tool_msg = llm.seen[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["content"].startswith("<untrusted_data>\nrefused")
    entries = [json.loads(line) for line in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e.get("outcome") for e in entries if e["event"] == "tool"] == ["refused"]


def test_never_tool_by_base_tier_is_refused_without_asking(tmp_path):
    ran, asked = [], []
    llm = _ScriptedLLM(_confirm_call(window="Mail", control="Send"), _answer("Do it yourself."))
    tool = tools.Tool("click", "", {}, [], tools.NEVER, lambda **kw: ran.append(kw) or "clicked")
    Agent(llm, lambda q: asked.append(q) or True, Journal(tmp_path / "j.jsonl"), {"click": tool}).handle("send it")
    _assert_refused(tmp_path, llm, ran, asked)


def test_never_tool_by_escalation_is_refused_without_asking(tmp_path):
    ran, asked = [], []
    llm = _ScriptedLLM(_confirm_call(window="Mail", control="Verzenden"), _answer("Do it yourself."))
    tool = tools.Tool("click", "", {}, [], tools.CONFIRM, lambda **kw: ran.append(kw) or "clicked",
                      tools._click_tier)  # CONFIRM base tier escalated to NEVER by the argument
    Agent(llm, lambda q: asked.append(q) or True, Journal(tmp_path / "j.jsonl"), {"click": tool}).handle("verzenden")
    _assert_refused(tmp_path, llm, ran, asked)



