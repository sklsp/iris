"""The safety rules of Iris, as tests: tiers are enforced in code, not in the prompt."""

from pathlib import Path

import pytest

from iris import tools

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

