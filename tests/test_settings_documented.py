"""Every setting on the Settings tab must be named in the README's Configuration section, so a new
setting cannot ship until it is documented there."""

import re
from pathlib import Path

from iris import settings

README = Path(__file__).resolve().parent.parent / "README.md"


def _configuration_section():
    """The README text between `## Configuration` and the next line that starts with `## `."""
    lines = README.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## Configuration"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## "))
    return "\n".join(lines[start:end])


def test_every_setting_is_in_the_readme_configuration_section():
    section = _configuration_section()
    missing = [f["key"] for f in settings.FIELDS if not re.search(rf"\b{re.escape(f['key'])}\b", section)]
    assert not missing, f"settings missing from the README's Configuration section: {sorted(missing)}"
