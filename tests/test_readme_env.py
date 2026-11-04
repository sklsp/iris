"""Every environment variable Iris reads must be in the README, so the table can never drift from the code."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

# These names pass data to a child process; the user never sets them, so they stay out of the README.
INTERNAL = {"IRIS_CLIP", "IRIS_SHOT_PATH", "IRIS_SHOT_RECT", "IRIS_STOP_EVENT", "IRIS_TOKEN"}


def _env_names():
    """Every IRIS_* name that appears in iris/ source."""
    names = set()
    for path in (ROOT / "iris").rglob("*.py"):
        names.update(re.findall(r"IRIS_[A-Z0-9_]*[A-Z0-9]", path.read_text(encoding="utf-8")))
    return names


def test_every_env_variable_is_in_the_readme():
    readme = README.read_text(encoding="utf-8")
    missing = sorted(name for name in _env_names() - INTERNAL if name not in readme)
    assert not missing, "README.md is missing these environment variables: " + ", ".join(missing)


def test_internal_names_are_exactly_the_child_process_ones():
    assert INTERNAL == {"IRIS_CLIP", "IRIS_SHOT_PATH", "IRIS_SHOT_RECT", "IRIS_STOP_EVENT", "IRIS_TOKEN"}
    source = "".join(path.read_text(encoding="utf-8") for path in (ROOT / "iris").rglob("*.py"))
    stale = sorted(name for name in INTERNAL if name not in source)
    assert not stale, "these internal names no longer appear in iris/: " + ", ".join(stale)
