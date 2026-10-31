"""Office files for the user: Word .docx from a title and simple markdown, PowerPoint .pptx from slides."""

import re
from datetime import date

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


def make_presentation(path: str, title: str, slides: list) -> str:
    """Make a PowerPoint .pptx for the user from a title and slide entries.

    The first slide is a title slide with the given title and today's date as subtitle; each entry becomes a
    "Title and Content" slide with its bullets, and its notes go on the notes page. At most 40 slides and 12
    bullets per slide (more is an error, nothing written). A bare name goes to ~/Documents/Iris; ".pptx" is
    added when missing. When the file already exists it is replaced: the old one goes to Iris's trash first.
    """
    try:
        from pptx import Presentation
    except ImportError:
        return "error: install the office extra: pip install iris[office]"

    if len(slides) > 40:
        return f"error: at most 40 slides, got {len(slides)}"
    for s in slides:
        if len(s.get("bullets", [])) > 12:
            return "error: at most 12 bullets per slide"

    p = files.resolve_target(path)
    if not p.suffix:
        p = p.with_suffix(".pptx")
    if p.exists():
        from iris.tools import trash_file  # lazy: tools imports this module

        trash_file(str(p))
    prs = Presentation()
    first = prs.slides.add_slide(prs.slide_layouts[0])
    first.shapes.title.text = title
    first.placeholders[1].text = date.today().isoformat()
    for s in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = str(s.get("title", ""))
        tf = slide.placeholders[1].text_frame
        for i, b in enumerate(s.get("bullets", [])):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.text = str(b)
        notes = s.get("notes")
        if notes:
            slide.notes_slide.notes_text_frame.text = str(notes)
    p.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(p))
    return f"made {p} ({len(slides)} slides)"
