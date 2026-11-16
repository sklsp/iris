"""The architecture document stays true to the code.

Every iris/<path>.py it names exists, every module directly in iris/ and iris/dashboard/ is named,
it shows one mermaid diagram of a voice turn, and it uses no em or en dashes (the house rule).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "ARCHITECTURE.md"


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_every_iris_path_it_names_exists():
    text = _text()
    for rel in set(re.findall(r"iris/([A-Za-z0-9_/]+\.py)", text)):
        assert (ROOT / "iris" / rel).is_file(), f"{rel} is named in the doc but does not exist"


def test_every_module_is_named():
    text = _text()
    for path in sorted((ROOT / "iris").glob("*.py")):
        if path.name == "__init__.py":
            continue  # the package itself, not a module with behaviour of its own
        assert f"iris/{path.name}" in text, f"{path.name} is never named in the doc"
    for path in sorted((ROOT / "iris" / "dashboard").glob("*.py")):
        assert f"iris/dashboard/{path.name}" in text, f"dashboard/{path.name} is never named in the doc"


def test_the_voice_turn_has_a_mermaid_diagram():
    assert "```mermaid" in _text()


def test_no_em_or_en_dash():
    text = _text()
    for name, ch in (("em dash", "\u2014"), ("en dash", "\u2013")):
        assert ch not in text, f"the doc contains a {name}"
