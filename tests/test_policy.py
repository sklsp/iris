"""The escalation policy lives in its own protected module, not in the file a self-built branch edits."""

import pytest

from iris import policy, tools

MOVED_NAMES = [
    "SAFE", "CONFIRM", "NEVER", "_NEVER_WORDS", "_MAIL_CHAT", "_SYSTEM_DIRS",
    "_trash_dir", "_trash_roots", "_under", "_unload_tier", "_click_tier",
    "_type_tier", "_open_url_tier", "_press_keys_tier", "_trash_tier",
]

TIERED = sorted((t for t in tools.TOOLS.values() if t.tier_for), key=lambda t: t.name)


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_every_moved_name_exists_in_policy(name):
    assert hasattr(policy, name), f"{name} is missing from iris.policy"


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_tools_reexports_the_same_objects_from_policy(name):
    assert getattr(tools, name) is getattr(policy, name), f"iris.tools.{name} is not the policy object"


@pytest.mark.parametrize("tool", TIERED, ids=[t.name for t in TIERED])
def test_every_tier_for_lives_in_the_protected_policy_module(tool):
    assert tool.tier_for.__module__ == "iris.policy"


# The behaviour of the rules, kept in this protected file so a self-built branch cannot weaken the checks
# along with the rules (tests/test_tools.py is not protected).
@pytest.mark.parametrize("fn, args", [
    (policy._click_tier, {"window": "Afrekenen - Google Chrome", "control": "Nu betalen"}),
    (policy._click_tier, {"window": "Gmail", "control": "Verzenden"}),
    (policy._type_tier, {"window": "Inloggen", "into": "Wachtwoord"}),
    (policy._press_keys_tier, {"window": "Inbox - Outlook", "keys": "{ENTER}"}),
    (policy._open_url_tier, {"url": "javascript:alert(1)"}),
    (policy._open_url_tier, {"url": "file:///C:/Windows/System32/cmd.exe"}),
    (policy._trash_tier, {"path": "C:/Windows/System32"}),
])
def test_dangerous_actions_stay_never(fn, args):
    assert fn(args) == policy.NEVER


@pytest.mark.parametrize("fn, args", [
    (policy._click_tier, {"window": "Kladblok", "control": "Opslaan"}),
    (policy._press_keys_tier, {"window": "Kladblok", "keys": "^s"}),
])
def test_ordinary_actions_still_ask(fn, args):
    assert fn(args) == policy.CONFIRM


def test_https_stays_safe():
    assert policy._open_url_tier({"url": "https://example.com"}) == policy.SAFE
