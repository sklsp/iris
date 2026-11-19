"""Every command must be named in `iris --help` (the __main__ docstring) and in the docs (README.md or docs/), so a new
command cannot ship until it is documented in both."""

import re
from pathlib import Path

import pytest

import iris.__main__ as imain
from tests.doc_text import doc_text

MAIN = Path(__file__).resolve().parent.parent / "iris" / "__main__.py"

EXPECTED_COMMANDS = {
    "eval", "eval-compare", "dashboard", "app", "init", "doctor", "config",
    "startup", "shortcut", "skill", "autopilot", "timings",
}


def _command_names():
    """The commands main() dispatches on: every `sys.argv[1] == "name"` in iris/__main__.py."""
    text = MAIN.read_text(encoding="utf-8")
    return set(re.findall(r'sys\.argv\[1\] == "([a-z-]+)"', text))


def test_the_command_set_is_exactly_the_expected_one():
    names = _command_names()
    assert names == EXPECTED_COMMANDS, f"commands in iris/__main__.py: {sorted(names)}"


@pytest.mark.parametrize("name", sorted(EXPECTED_COMMANDS))
def test_every_command_is_in_help_and_docs(name):
    help_text = imain.__doc__ or ""
    assert f"python -m iris {name} " in help_text, f"`iris --help` does not name the {name!r} command"
    text = doc_text()
    assert f"iris {name}" in text, f"the docs do not name the {name!r} command"
