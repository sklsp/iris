"""The safety rules of Iris, as tests: tiers are enforced in code, not in the prompt."""

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
