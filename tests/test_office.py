"""Tests for iris.office: making Word documents, with a real python-docx on tmp_path only."""

import sys

from docx import Document

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
