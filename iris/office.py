"""Word documents for the user: a .docx from a title and simple markdown."""

import re

from iris import files


def _add_rich(paragraph, text: str) -> None:
    """Add text to a paragraph; **bold** parts become bold runs."""
    for part in re.split(r"(\*\*.+?\*\*)", text):
        if not part:
            continue
        if len(part) > 4 and part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        else:
            paragraph.add_run(part)


def _add_content(doc, content: str) -> int:
    """Add the simple-markdown content; returns how many paragraphs it made.

    "# ", "## " and "### " become headings 1 to 3, "- " or "* " bullets, a number with ". " a numbered item,
    consecutive plain lines one paragraph, a blank line starts a new one.
    """
    n = 0
    buf: list[str] = []

    def flush() -> None:
        nonlocal n
        if buf:
            _add_rich(doc.add_paragraph(), " ".join(buf))
            buf.clear()
            n += 1

    for line in content.splitlines():
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("### "):
            flush()
            doc.add_heading(s[4:], 3)
            n += 1
        elif s.startswith("## "):
            flush()
            doc.add_heading(s[3:], 2)
            n += 1
        elif s.startswith("# "):
            flush()
            doc.add_heading(s[2:], 1)
            n += 1
        elif s.startswith("- ") or s.startswith("* "):
            flush()
            _add_rich(doc.add_paragraph(style="List Bullet"), s[2:])
            n += 1
        elif re.match(r"^\d+\. ", s):
            flush()
            _add_rich(doc.add_paragraph(style="List Number"), s.split(". ", 1)[1])
            n += 1
        else:
            buf.append(s)
    flush()
    return n


def make_document(path: str, title: str, content: str) -> str:
    """Make a Word .docx for the user from a title and simple markdown.

    A bare name goes to ~/Documents/Iris; ".docx" is added when missing. The title becomes the Title heading;
    "# ", "## " and "### " become headings 1 to 3, "- " or "* " bullets, "1. " numbered items, a blank line
    starts a new paragraph and **bold** becomes bold text. When the file already exists it is replaced: the old
    one goes to Iris's trash first.
    """
    try:
        from docx import Document
    except ImportError:
        return "error: install the office extra: pip install iris[office]"

    p = files.resolve_target(path)
    if not p.suffix:
        p = p.with_suffix(".docx")
    if p.exists():
        from iris.tools import trash_file  # lazy: tools imports this module

        trash_file(str(p))
    doc = Document()
    doc.add_heading(title, 0)
    n = _add_content(doc, content)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(p))
    return f"made {p} ({n} paragraphs)"
