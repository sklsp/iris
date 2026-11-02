"""Tests for iris.office: making Word documents, PowerPoint presentations and spreadsheets, with real
python-docx, python-pptx and openpyxl on tmp_path only."""

import sys
from datetime import date

from docx import Document
from openpyxl import Workbook, load_workbook
from pptx import Presentation

from iris import office, tools


def _home(tmp_path, monkeypatch):
    """Point Path.home() at a tmp folder so the tests never touch the real home."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _cfg(tmp_path, monkeypatch, text):
    cfg = tmp_path / "config.toml"
    cfg.write_text(text, encoding="utf-8")
    monkeypatch.setenv("IRIS_CONFIG", str(cfg))


# --- make_document ---------------------------------------------------------------

def test_make_document_makes_a_docx_with_title_and_markdown(tmp_path):
    p = tmp_path / "doc.docx"
    content = ("# One\n## Two\n### Three\n- a bullet\n* another\n1. first\n2. second\n"
               "plain line one\nplain line two\n\nafter blank")
    assert office.make_document(str(p), "My Title", content) == f"made {p} (9 paragraphs)"

    paras = Document(str(p)).paragraphs
    assert (paras[0].text, paras[0].style.name) == ("My Title", "Title")
    assert [(q.text, q.style.name) for q in paras[1:4]] == [
        ("One", "Heading 1"), ("Two", "Heading 2"), ("Three", "Heading 3")]
    assert [(q.text, q.style.name) for q in paras[4:6]] == [("a bullet", "List Bullet"), ("another", "List Bullet")]
    assert [(q.text, q.style.name) for q in paras[6:8]] == [("first", "List Number"), ("second", "List Number")]
    assert (paras[8].text, paras[9].text) == ("plain line one plain line two", "after blank")


def test_make_document_bold_parts_become_bold_runs(tmp_path):
    p = tmp_path / "bold.docx"
    office.make_document(str(p), "T", "before **bold** after")

    para = Document(str(p)).paragraphs[1]
    assert [(r.text, bool(r.bold)) for r in para.runs] == [("before ", False), ("bold", True), (" after", False)]


def test_make_document_bare_name_goes_to_iris_folder_and_adds_docx(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    p = home / "Documents" / "Iris" / "report.docx"
    assert office.make_document("report", "T", "body") == f"made {p} (1 paragraphs)"
    assert p.exists()


def test_make_document_replaces_an_existing_file_and_trashes_the_old_one(tmp_path, monkeypatch):
    p = tmp_path / "old.docx"
    old = Document()
    old.add_heading("Old", 0)
    old.save(str(p))
    trash = tmp_path / "trash"
    monkeypatch.setenv("IRIS_TRASH_DIR", str(trash))

    assert office.make_document(str(p), "New", "body") == f"made {p} (1 paragraphs)"
    assert Document(str(p)).paragraphs[0].text == "New"
    trashed = [x for x in trash.iterdir() if not x.name.endswith(".json")]
    assert len(trashed) == 1 and trashed[0].name.endswith("old.docx")  # the same mechanism as trash_file
    assert any(x.name.endswith(".json") for x in trash.iterdir())


def test_make_document_without_python_docx_says_install_the_extra(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "docx", None)
    assert office.make_document(str(tmp_path / "x.docx"), "T", "b") == \
        "error: install the office extra: pip install iris[office]"


# --- make_presentation ---------------------------------------------------------------

def test_make_presentation_makes_a_title_slide_and_content_slides(tmp_path):
    p = tmp_path / "deck.pptx"
    slides = [{"title": "One", "bullets": ["a", "b"], "notes": "n1"}, {"title": "Two", "bullets": ["c"]}]
    assert office.make_presentation(str(p), "My Deck", slides) == f"made {p} (2 slides)"

    prs = Presentation(str(p))
    assert len(prs.slides) == 3
    title_slide, one, two = prs.slides
    assert title_slide.shapes.title.text == "My Deck"
    assert title_slide.placeholders[1].text == date.today().isoformat()
    assert one.shapes.title.text == "One"
    assert [q.text for q in one.placeholders[1].text_frame.paragraphs] == ["a", "b"]
    assert one.notes_slide.notes_text_frame.text == "n1"
    assert two.shapes.title.text == "Two"
    assert [q.text for q in two.placeholders[1].text_frame.paragraphs] == ["c"]


def test_make_presentation_bare_name_goes_to_iris_folder_and_adds_pptx(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    p = home / "Documents" / "Iris" / "deck.pptx"
    assert office.make_presentation("deck", "T", []) == f"made {p} (0 slides)"
    assert p.exists()


def test_make_presentation_more_than_40_slides_is_an_error(tmp_path):
    p = tmp_path / "big.pptx"
    slides = [{"title": str(i), "bullets": ["b"]} for i in range(41)]
    assert office.make_presentation(str(p), "T", slides) == "error: at most 40 slides, got 41"
    assert not p.exists()


def test_make_presentation_more_than_12_bullets_is_an_error(tmp_path):
    p = tmp_path / "many.pptx"
    slides = [{"title": "One", "bullets": [str(i) for i in range(13)]}]
    assert office.make_presentation(str(p), "T", slides) == "error: at most 12 bullets per slide"
    assert not p.exists()


def test_make_presentation_replaces_an_existing_file_and_trashes_the_old_one(tmp_path, monkeypatch):
    p = tmp_path / "old.pptx"
    old = Presentation()
    old.slides.add_slide(old.slide_layouts[0])
    old.save(str(p))
    trash = tmp_path / "trash"
    monkeypatch.setenv("IRIS_TRASH_DIR", str(trash))

    assert office.make_presentation(str(p), "New", []) == f"made {p} (0 slides)"
    assert Presentation(str(p)).slides[0].shapes.title.text == "New"
    trashed = [x for x in trash.iterdir() if not x.name.endswith(".json")]
    assert len(trashed) == 1 and trashed[0].name.endswith("old.pptx")


def test_make_presentation_without_python_pptx_says_install_the_extra(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pptx", None)
    assert office.make_presentation(str(tmp_path / "x.pptx"), "T", []) == \
        "error: install the office extra: pip install iris[office]"


# --- make_spreadsheet ----------------------------------------------------------------

def test_make_spreadsheet_makes_named_sheets_with_bold_gray_header(tmp_path):
    p = tmp_path / "data.xlsx"
    sheets = [{"name": "A", "rows": [["h1", "h2"], ["x", 1]]}, {"name": "B", "rows": [["only"]]}]
    assert office.make_spreadsheet(str(p), sheets) == f"made {p} (2 sheets)"

    wb = load_workbook(str(p))
    assert wb.sheetnames == ["A", "B"]
    a, b = wb["A"], wb["B"]
    assert [(c.value, c.font.bold) for c in a[1]] == [("h1", True), ("h2", True)]
    assert [c.fill.fgColor.rgb for c in a[1]] == ["00D9D9D9", "00D9D9D9"]
    assert [(c.value, bool(c.font.bold)) for c in a[2]] == [("x", False), (1, False)]
    assert b["A1"].value == "only" and b["A1"].font.bold is True


def test_make_spreadsheet_bare_name_goes_to_iris_folder_and_adds_xlsx(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    p = home / "Documents" / "Iris" / "data.xlsx"
    assert office.make_spreadsheet("data", [{"name": "S", "rows": []}]) == f"made {p} (1 sheets)"
    assert p.exists()


def test_make_spreadsheet_more_than_5000_rows_is_an_error(tmp_path):
    p = tmp_path / "big.xlsx"
    sheets = [{"name": "A", "rows": [[str(i)] for i in range(5001)]}]
    assert office.make_spreadsheet(str(p), sheets) == "error: at most 5000 rows per sheet, got 5001"
    assert not p.exists()


def test_make_spreadsheet_replaces_an_existing_file_and_trashes_the_old_one(tmp_path, monkeypatch):
    p = tmp_path / "old.xlsx"
    old = Workbook()
    old.active.title = "Old"
    old.save(str(p))
    trash = tmp_path / "trash"
    monkeypatch.setenv("IRIS_TRASH_DIR", str(trash))

    assert office.make_spreadsheet(str(p), [{"name": "New", "rows": []}]) == f"made {p} (1 sheets)"
    assert load_workbook(str(p)).sheetnames == ["New"]
    trashed = [x for x in trash.iterdir() if not x.name.endswith(".json")]
    assert len(trashed) == 1 and trashed[0].name.endswith("old.xlsx")


def test_make_spreadsheet_without_openpyxl_says_install_the_extra(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    assert office.make_spreadsheet(str(tmp_path / "x.xlsx"), []) == \
        "error: install the office extra: pip install iris[office]"


# --- _new_file_tier ---------------------------------------------------------------

def test_new_file_tier_is_safe_for_a_new_file_in_a_folder(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    _cfg(tmp_path, monkeypatch, f'folders = ["{work.as_posix()}"]\n')
    assert tools.TOOLS["make_document"].tier_of({"path": str(work / "new.docx")}) == tools.SAFE


def test_new_file_tier_is_confirm_when_the_file_already_exists(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / "old.docx").write_text("x", encoding="utf-8")
    _cfg(tmp_path, monkeypatch, f'folders = ["{work.as_posix()}"]\n')
    assert tools.TOOLS["make_document"].tier_of({"path": str(work / "old.docx")}) == tools.CONFIRM


def test_new_file_tier_resolves_bare_names_to_the_iris_folder(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    iris_dir = home / "Documents" / "Iris"
    iris_dir.mkdir(parents=True)
    (iris_dir / "report.docx").write_text("x", encoding="utf-8")
    assert tools.TOOLS["make_document"].tier_of({"path": "report.docx"}) == tools.CONFIRM


def test_new_file_tier_is_never_outside_home_and_folders(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    outside = tmp_path / "outside.docx"
    assert tools.TOOLS["make_document"].tier_of({"path": str(outside)}) == tools.NEVER


def test_new_file_tier_is_never_in_a_system_directory(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    assert tools.TOOLS["make_document"].tier_of({"path": "C:/Windows/report.docx"}) == tools.NEVER


# --- make_presentation tiers ---------------------------------------------------------------

def test_presentation_tier_is_safe_for_a_new_file_in_a_folder(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    _cfg(tmp_path, monkeypatch, f'folders = ["{work.as_posix()}"]\n')
    assert tools.TOOLS["make_presentation"].tier_of({"path": str(work / "new.pptx")}) == tools.SAFE


def test_presentation_tier_is_confirm_when_the_file_already_exists(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / "old.pptx").write_text("x", encoding="utf-8")
    _cfg(tmp_path, monkeypatch, f'folders = ["{work.as_posix()}"]\n')
    assert tools.TOOLS["make_presentation"].tier_of({"path": str(work / "old.pptx")}) == tools.CONFIRM


def test_presentation_tier_is_never_outside_home_and_folders(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    outside = tmp_path / "outside.pptx"
    assert tools.TOOLS["make_presentation"].tier_of({"path": str(outside)}) == tools.NEVER


def test_presentation_tier_is_never_in_a_system_directory(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    assert tools.TOOLS["make_presentation"].tier_of({"path": "C:/Windows/deck.pptx"}) == tools.NEVER


# --- make_spreadsheet tiers ---------------------------------------------------------------

def test_spreadsheet_tier_is_safe_for_a_new_file_in_a_folder(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    _cfg(tmp_path, monkeypatch, f'folders = ["{work.as_posix()}"]\n')
    assert tools.TOOLS["make_spreadsheet"].tier_of({"path": str(work / "new.xlsx")}) == tools.SAFE


def test_spreadsheet_tier_is_confirm_when_the_file_already_exists(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / "old.xlsx").write_text("x", encoding="utf-8")
    _cfg(tmp_path, monkeypatch, f'folders = ["{work.as_posix()}"]\n')
    assert tools.TOOLS["make_spreadsheet"].tier_of({"path": str(work / "old.xlsx")}) == tools.CONFIRM


def test_spreadsheet_tier_is_never_outside_home_and_folders(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    outside = tmp_path / "outside.xlsx"
    assert tools.TOOLS["make_spreadsheet"].tier_of({"path": str(outside)}) == tools.NEVER


def test_spreadsheet_tier_is_never_in_a_system_directory(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    assert tools.TOOLS["make_spreadsheet"].tier_of({"path": "C:/Windows/data.xlsx"}) == tools.NEVER


# --- review fixes: formulas stay text, csv, sheet names, suffix-aware tiers ---------------------

def test_text_that_looks_like_a_formula_stays_text(tmp_path):
    import openpyxl

    p = tmp_path / "t.xlsx"
    rows = [["Naam", "Link"], ["x", '=HYPERLINK("http://evil.example","klik")'], ["y", "+1+1"], ["z", 42]]
    office.make_spreadsheet(str(p), [{"name": "", "rows": rows}])
    ws = openpyxl.load_workbook(p).active
    assert ws.title == "Sheet1"
    assert (ws["B2"].data_type, ws["B2"].value) == ("s", '=HYPERLINK("http://evil.example","klik")')
    assert (ws["B3"].data_type, ws["B4"].value) == ("s", 42)
    assert ws.column_dimensions["B"].width == 42


def test_a_csv_path_writes_csv_and_escapes_formulas(tmp_path):
    p = tmp_path / "t.csv"
    assert office.make_spreadsheet(str(p), [{"name": "A", "rows": [["a", "b"], ["=1+1", 3]]}]) == f"made {p} (2 rows)"
    assert p.read_text(encoding="utf-8-sig").splitlines() == ["a,b", "'=1+1,3"]


def test_office_tiers_look_at_the_file_with_its_suffix(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    iris_dir = home / "Documents" / "Iris"
    iris_dir.mkdir(parents=True)
    (iris_dir / "report.docx").write_text("x", encoding="utf-8")
    assert tools.TOOLS["make_document"].tier_of({"path": "report"}) == tools.CONFIRM  # report means report.docx
    assert tools.TOOLS["make_presentation"].tier_of({"path": "report"}) == tools.SAFE  # report.pptx is new


def test_a_failed_build_leaves_the_old_document_in_place(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_TRASH_DIR", str(tmp_path / "trash"))
    old = tmp_path / "plan.docx"
    old.write_bytes(b"old")

    def boom(doc, content):
        raise RuntimeError("bad content")

    monkeypatch.setattr(office, "_add_content", boom)
    try:
        office.make_document(str(old), "Plan", "x")
        raise AssertionError("expected the build to fail")
    except RuntimeError:
        pass
    assert old.read_bytes() == b"old"  # untouched, not in the trash
