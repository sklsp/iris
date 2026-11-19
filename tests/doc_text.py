"""The doc text the truth tests read: README.md plus every markdown file under docs/."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def doc_text() -> str:
    """README.md and every docs/*.md as one string, so a test sees text wherever it lives."""
    parts = [(ROOT / "README.md").read_text(encoding="utf-8")]
    parts.extend(path.read_text(encoding="utf-8") for path in sorted((ROOT / "docs").rglob("*.md")))
    return "\n".join(parts)
