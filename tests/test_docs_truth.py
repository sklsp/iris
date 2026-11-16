"""Every command and environment variable named in docs/ must exist, so the docs cannot drift from the code."""

import re
from pathlib import Path

import pytest

from tests.test_commands_documented import _command_names

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted((ROOT / "docs").rglob("*.md"))

# A word after `iris` is a command only when it starts with a letter: flags such as --voice are not commands.
WORD = r"([a-z][a-z0-9-]*)"


def _command_words(text):
    """Every `python -m iris <word>` and `iris <word>` the doc names in backticks or code blocks.

    Only a word that starts a backtick span, starts a code line, or follows `python -m ` counts: elsewhere an
    iris word is a path list (ruff check iris tests) or part of a longer name."""
    words = set()
    for span in re.findall(r"`([^`\n]+)`", text):
        _add(words, span)
    for block in re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S):
        for line in block.splitlines():
            _add(words, line.lstrip())
    return words


def _add(words, chunk):
    """The command word of one backtick span or code line, if it names a command."""
    match = re.match(r"iris\s+" + WORD, chunk)
    if match:
        words.add(match.group(1))
    match = re.search(r"python\s+-m\s+iris\s+" + WORD, chunk)
    if match:
        words.add(match.group(1))


def _env_names(text):
    """Every IRIS_* name the doc names."""
    return set(re.findall(r"IRIS_[A-Z0-9_]*[A-Z0-9]", text))


def _iris_source():
    """All of iris/ as one string: a variable named in the docs must appear somewhere in it."""
    return "".join(path.read_text(encoding="utf-8") for path in (ROOT / "iris").rglob("*.py"))


def test_the_docs_are_there():
    assert DOCS, "docs/ has no markdown files to check"


@pytest.mark.parametrize("doc", DOCS, ids=lambda doc: doc.name)
def test_every_command_it_names_is_a_real_command(doc):
    words = _command_words(doc.read_text(encoding="utf-8"))
    missing = sorted(word for word in words if word not in _command_names())
    assert not missing, f"{doc.name} names these commands that iris/__main__.py does not have: " + ", ".join(missing)


@pytest.mark.parametrize("doc", DOCS, ids=lambda doc: doc.name)
def test_every_environment_variable_it_names_is_in_the_code(doc):
    source = _iris_source()
    missing = sorted(name for name in _env_names(doc.read_text(encoding="utf-8")) if name not in source)
    assert not missing, f"{doc.name} names these environment variables missing from iris/: " + ", ".join(missing)
