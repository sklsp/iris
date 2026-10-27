"""The README's risk tier table must match tools.TOOLS exactly, so it can never drift from the code."""

from pathlib import Path

from iris import tools

README = Path(__file__).resolve().parent.parent / "README.md"


def _risk_tier_rows():
    text = README.read_text(encoding="utf-8")
    section = text.split("## Risk tiers", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] == "Tool" or set(cells[0]) <= {"-"}:
            continue
        rows.append((cells[0], cells[1]))
    return rows


def test_readme_risk_tier_table_matches_tools():
    assert _risk_tier_rows() == [(t.name, t.tier) for t in tools.TOOLS.values()]
