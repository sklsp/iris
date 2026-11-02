"""Office files for the user: Word .docx from a title and simple markdown, PowerPoint .pptx from slides,
and spreadsheet .xlsx from named worksheets of rows."""

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

    p = files.resolve_target(path, ".docx")
    doc = Document()  # built first: if this fails, the old file stays where it is
    doc.add_heading(title, 0)
    n = _add_content(doc, content)
    if p.exists():
        from iris.tools import trash_file  # lazy: tools imports this module

        trash_file(str(p))
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

    p = files.resolve_target(path, ".pptx")
    prs = Presentation()  # built first: if this fails, the old file stays where it is
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
    if p.exists():
        from iris.tools import trash_file  # lazy: tools imports this module

        trash_file(str(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(p))
    return f"made {p} ({len(slides)} slides)"


MAX_ROWS = 5000
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value):
    """Numbers and booleans stay as they are; everything else is text. Text never becomes a formula, because the
    content can come from a web page (an "=HYPERLINK(...)" or DDE trick must stay a plain string)."""
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)


def make_spreadsheet(path: str, sheets: list) -> str:
    """Make a spreadsheet for the user from named worksheets of rows.

    Each entry becomes one worksheet; its first row is the header (bold on light gray) and the columns get a width
    that fits their text. At most 5000 rows per sheet (more is an error, nothing written). A bare name goes to
    ~/Documents/Iris; ".xlsx" is added when the name has no suffix, and a path ending in .csv writes the first sheet
    as CSV without the office extra. Text that starts like a formula stays text. When the file already exists it is
    replaced: the old one goes to Iris's trash first.
    """
    for s in sheets:
        if len(s.get("rows", [])) > MAX_ROWS:
            return f"error: at most {MAX_ROWS} rows per sheet, got {len(s['rows'])}"
    p = files.resolve_target(path, ".xlsx")
    if p.suffix.lower() == ".csv":
        return _write_csv(p, sheets)
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return "error: install the office extra: pip install iris[office]"

    wb = Workbook()  # built first: if this fails, the old file stays where it is
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="D9D9D9")
    for i, s in enumerate(sheets):
        name = str(s.get("name") or f"Sheet{i + 1}")[:31]  # Excel's limit; an empty name is not allowed
        ws = wb.active if i == 0 else wb.create_sheet(name)
        ws.title = name
        widths: dict[int, int] = {}
        for r, row in enumerate(s.get("rows", []), 1):
            for c, value in enumerate(row, 1):
                cell = ws.cell(row=r, column=c, value=_cell(value))
                if isinstance(cell.value, str) and cell.value.startswith(_FORMULA_START):
                    cell.data_type = "s"  # openpyxl would store it as a formula
                widths[c] = max(widths.get(c, 0), len(str(value)))
                if r == 1:
                    cell.font = header_font
                    cell.fill = header_fill
        for c, w in widths.items():
            ws.column_dimensions[get_column_letter(c)].width = min(max(w + 2, 8), 60)
    if p.exists():
        from iris.tools import trash_file  # lazy: tools imports this module

        trash_file(str(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(p))
    return f"made {p} ({len(sheets)} sheets)"


def _write_csv(p, sheets: list) -> str:
    """The first sheet as CSV; text that starts like a formula gets a leading apostrophe so Excel shows it as text."""
    import csv

    rows = sheets[0].get("rows", []) if sheets else []
    if p.exists():
        from iris.tools import trash_file  # lazy: tools imports this module

        trash_file(str(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for row in rows:
            w.writerow(["'" + v if isinstance(v, str) and v.startswith(_FORMULA_START) else v for v in row])
    return f"made {p} ({len(rows)} rows)"
