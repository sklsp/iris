"""Notes in ~/.iris/notes.md (IRIS_NOTES overrides): one line per note, '- YYYY-MM-DD HH:MM <text>'."""

import os
import re
from datetime import datetime
from pathlib import Path

LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (.+)$")


def _path() -> Path:
    env = os.environ.get("IRIS_NOTES", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".iris" / "notes.md"


def _read(path: Path | None = None) -> list[tuple[datetime, str]]:
    p = path or _path()
    if not p.exists():
        return []
    notes: list[tuple[datetime, str]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if m:
            notes.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M"), m.group(2)))
    return notes


def find_notes(query: str) -> list[str]:
    """The texts of all notes containing query (case-insensitive), oldest first."""
    q = query.strip().lower()
    if not q:
        return []
    return [text for _, text in _read() if q in text.lower()]


def note_add(text: str) -> str:
    """Add one line to the user's notes; newlines become spaces, at most 2000 characters."""
    flat = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    flat = flat.strip()
    if not flat:
        return "error: say what the note says"
    if len(flat) > 2000:
        return f"error: notes are at most 2000 characters (this one is {len(flat)})"
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with p.open("a", encoding="utf-8") as f:
        f.write(f"- {stamp} {flat}\n")
    return "added a note"


def notes_read(query: str = "", last: int = 20) -> str:
    """The user's notes, newest first: the last ones, or the ones containing query."""
    if last < 1:
        return "error: last must be at least 1"
    q = query.strip().lower()
    notes = [n for n in _read() if not q or q in n[1].lower()]
    notes.sort(key=lambda n: n[0], reverse=True)
    picked = notes[:last]
    if not picked:
        return f'no notes containing "{query}"' if q else "no notes yet"
    return "\n".join(f"- {dt:%Y-%m-%d %H:%M} {text}" for dt, text in picked)


def note_remove(query: str) -> str:
    """Remove the single note that contains query; an error when none or several match."""
    q = query.strip().lower()
    if not q:
        return "error: say which note to remove"
    p = _path()
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    hits: list[tuple[datetime, str]] = []
    first_idx = -1
    for i, line in enumerate(lines):
        m = LINE.match(line)
        if m and q in m.group(2).lower():
            hits.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M"), m.group(2)))
            if first_idx < 0:
                first_idx = i
    if not hits:
        return f'error: no notes containing "{query}"'
    if len(hits) > 1:
        shown = "\n".join(f"- {dt:%Y-%m-%d %H:%M} {text[:80]}" for dt, text in hits)
        return f'error: {len(hits)} notes contain "{query}"; say which one:\n{shown}'
    del lines[first_idx]
    tmp = p.with_name(p.name + ".tmp")  # write aside, then swap: a crash mid-write never loses the other notes
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp, p)
    dt, _ = hits[0]
    return f"removed a note ({dt:%Y-%m-%d %H:%M})"
