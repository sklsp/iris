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
