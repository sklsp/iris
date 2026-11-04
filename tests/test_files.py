"""Tests for iris.files: finding, listing, reading, writing, moving and copying files, plus their tiers."""

import os
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris import files, tools


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


# --- find_files ------------------------------------------------------------------

def test_find_files_matches_files_and_folders_case_insensitively(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / "Report 2026.txt").write_text("x", encoding="utf-8")
    (home / "Reports").mkdir()
    (home / "other.txt").write_text("y", encoding="utf-8")
    out = files.find_files("report")
    assert str(home / "Report 2026.txt") in out.splitlines()
    assert str(home / "Reports") in out.splitlines()
    assert "other.txt" not in out


def test_find_files_searches_the_configured_folders(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    (proj / "deep").mkdir(parents=True)
    (proj / "deep" / "budget.xlsx").write_bytes(b"x")
    _cfg(tmp_path, monkeypatch, f'folders = ["{proj.as_posix()}"]\n')
    assert str(proj / "deep" / "budget.xlsx") in files.find_files("budget").splitlines()


def test_find_files_skips_hidden_dirs_skip_listed_dirs_and_the_trash(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".hidden").mkdir()
    (home / ".hidden" / "report.txt").write_text("x", encoding="utf-8")
    (home / "node_modules").mkdir()
    (home / "node_modules" / "report.txt").write_text("x", encoding="utf-8")
    trash = home / "trash"
    trash.mkdir()
    (trash / "report.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("IRIS_TRASH_DIR", str(trash))
    assert files.find_files("report") == "No files named report found."


def test_find_files_says_so_when_nothing_matches(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    assert files.find_files("zzz") == "No files named zzz found."


def test_find_files_searches_only_the_given_folder(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    (home / "report.txt").write_text("x", encoding="utf-8")
    (other / "report.txt").write_text("y", encoding="utf-8")
    out = files.find_files("report", folder=str(other))
    assert str(other / "report.txt") in out.splitlines()
    assert str(home / "report.txt") not in out


def test_find_files_errors_on_a_missing_folder(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    assert files.find_files("x", folder=str(tmp_path / "nope")).startswith("error: no such folder")


def test_find_files_missing_folder_error_carries_the_same_hint_as_the_other_file_tools(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    missing = str(tmp_path / "nope")
    hint = ("; it does not exist. Look for it once with find_files, then tell the user what you found "
            "instead of retrying.")
    assert files.find_files("x", folder=missing) == f"error: no such folder: {missing}" + hint


def test_find_files_stops_after_30_hits_and_lists_newest_first(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    for i in range(40):
        (home / f"f{i:02d}.txt").write_text(str(i), encoding="utf-8")
    now = time.time()
    for i in range(5):  # the first five files are the newest, in this order; wide offsets beat clock quantization
        os.utime(home / f"f{i:02d}.txt", (now + 50 + i * 10, now + 50 + i * 10))
    lines = files.find_files("f").splitlines()
    assert len(lines) == 31
    assert [Path(line).name for line in lines[:5]] == ["f04.txt", "f03.txt", "f02.txt", "f01.txt", "f00.txt"]
    assert lines[-1] == "[stopped after 30 hits]"


def test_find_files_stops_after_scanning_20000_entries(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)

    def walk(root):
        yield (str(root), [], [f"n{i:05d}.dat" for i in range(20_000)])

    monkeypatch.setattr(files.os, "walk", walk)
    assert files.find_files("zzz") == "No files named zzz found.\n[stopped after scanning 20000 entries]"


def test_find_files_skips_configured_folders_that_are_not_directories(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    work = tmp_path / "work"
    (work / "deep").mkdir(parents=True)
    (work / "deep" / "budget.xlsx").write_bytes(b"x")
    stray = tmp_path / "stray.txt"  # a file listed as a folder must be skipped, not crash the search
    stray.write_text("x", encoding="utf-8")
    _cfg(tmp_path, monkeypatch, f'folders = ["{stray.as_posix()}", "{work.as_posix()}"]\n')
    assert str(work / "deep" / "budget.xlsx") in files.find_files("budget").splitlines()


def test_in_trash_is_false_when_resolving_fails(tmp_path, monkeypatch):
    def broken(self):
        raise OSError("access denied")

    monkeypatch.setattr(Path, "resolve", broken)
    assert files._in_trash(tmp_path / "x") is False


def test_mtime_is_zero_when_stat_fails(tmp_path):
    p = tmp_path / "gone.txt"
    p.write_text("x", encoding="utf-8")
    p.unlink()  # stat now raises FileNotFoundError, an OSError
    assert files._mtime(p) == 0.0


# --- list_folder -----------------------------------------------------------------

def test_list_folder_lists_folders_first_then_files_with_kb(tmp_path):
    d = tmp_path / "d"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_bytes(b"x" * 3000)
    assert files.list_folder(str(d)) == "sub/\na.txt 2 KB"


def test_list_folder_reports_an_empty_folder(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert files.list_folder(str(d)) == "(empty)"


def test_list_folder_errors_on_a_missing_folder(tmp_path):
    assert files.list_folder(str(tmp_path / "nope")).startswith("error: no such folder")


def test_list_folder_caps_at_100_lines_and_says_so(tmp_path):
    d = tmp_path / "many"
    d.mkdir()
    for i in range(105):
        (d / f"f{i:03d}.txt").write_text("x", encoding="utf-8")
    lines = files.list_folder(str(d)).splitlines()
    assert len(lines) == 101
    assert lines[-1] == "... and 5 more"


# --- read_file -------------------------------------------------------------------

def test_read_file_returns_the_text(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hello iris", encoding="utf-8")
    assert files.read_file(str(f)) == "hello iris"


def test_read_file_truncates_at_20000_characters(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("a" * 25_000, encoding="utf-8")
    out = files.read_file(str(f))
    assert len(out) - len("... (truncated)") == 20_000 and out.endswith("... (truncated)")


def test_read_file_refuses_binary_files(tmp_path):
    f = tmp_path / "img.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\xff\xfe" * 10)
    assert files.read_file(str(f)) == "error: cannot read this kind of file"


def test_read_file_errors_on_a_missing_file(tmp_path):
    assert files.read_file(str(tmp_path / "nope.txt")).startswith("error: no such file")


def test_read_file_reads_docx_paragraphs_on_separate_lines(tmp_path):
    p = tmp_path / "doc.docx"
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:p><w:r><w:t>First line</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Second line</w:t></w:r></w:p></w:document>')
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", xml)
    assert files.read_file(str(p)) == "First line\nSecond line"


def test_read_file_reads_pptx_slide_by_slide(tmp_path):
    p = tmp_path / "deck.pptx"

    def slide(text):
        return (f'<sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                f"<p><r><a:t>{text}</a:t></r></p></sld>")

    with zipfile.ZipFile(p, "w") as z:
        z.writestr("ppt/slides/slide1.xml", slide("One"))
        z.writestr("ppt/slides/slide2.xml", slide("Two"))
    assert files.read_file(str(p)) == "Slide 1: One\nSlide 2: Two"


def _make_pdf(path, text="hello pdf world"):
    """A minimal one-page PDF with a single line of Helvetica text."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            (b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/"
             b"Resources<</Font<</F1 5 0 R>>>>>>"),
            b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream",
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer<</Size 6/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF".encode()
    path.write_bytes(bytes(out))


def test_read_file_reads_pdf_with_pypdf(tmp_path):
    import importlib.util

    p = tmp_path / "doc.pdf"
    _make_pdf(p)
    if importlib.util.find_spec("pypdf") is None:  # not installed in this venv
        assert files.read_file(str(p)) == "error: reading PDF files needs pypdf: pip install pypdf in Iris's Python"
    else:
        assert files.read_file(str(p)) == "hello pdf world"


def test_read_file_reports_a_reader_failure(tmp_path, monkeypatch):
    p = tmp_path / "doc.pdf"
    _make_pdf(p)

    def broken(path):
        raise ValueError("boom")

    monkeypatch.setattr(files, "_pdf_text", broken)
    assert files.read_file(str(p)) == "error: could not read doc.pdf: boom"


def test_pdf_text_joins_the_pages_of_a_reader(tmp_path, monkeypatch):
    """The reader lines run with a stand-in pypdf, so they are covered even when pypdf is missing."""

    class _Page:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class _Reader:
        def __init__(self, path):
            self.pages = [_Page("first page"), _Page(None)]  # a blank page extracts to None

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=_Reader))
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 not really")
    assert files._pdf_text(p) == "first page\n"


# --- tiers -----------------------------------------------------------------------

def test_find_files_and_list_folder_are_safe():
    assert tools.TOOLS["find_files"].tier == tools.SAFE
    assert tools.TOOLS["list_folder"].tier == tools.SAFE
    assert tools.TOOLS["read_file"].tier == tools.SAFE  # the base tier; _read_tier escalates


@pytest.mark.parametrize("name", [
    "password.txt", "Wachtwoord.md", "my-secret.json", "token.log", "credentials.csv",
    "apikey.ini", "api_key.toml", "private notes.txt", "server.key", "cert.pem",
    "store.pfx", "vault.kdbx", ".env", "id_rsa", "id_ed25519", "id_ed25519.pub",
    "Passwörter.txt", "kennwort.docx", "mots de passe.txt", "mot_de_passe.csv", "contraseñas.xlsx",
    "contrasenas.txt", "geheim.md",
])
def test_read_file_never_reads_secret_looking_files(name):
    assert tools.TOOLS["read_file"].tier_of({"path": f"C:/Users/jay/{name}"}) == tools.NEVER


@pytest.mark.parametrize("name", ["notes.txt", "report.docx", "slides.pptx", "paper.pdf", ".env.bak"])
def test_read_file_is_safe_for_everything_else(name):
    assert tools.TOOLS["read_file"].tier_of({"path": f"C:/Users/jay/{name}"}) == tools.SAFE


# --- write_file ------------------------------------------------------------------


def test_write_file_writes_utf8_text_to_the_iris_folder(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    out = files.write_file("notes.txt", "héllo")
    p = home / "Documents" / "Iris" / "notes.txt"
    assert p.read_text(encoding="utf-8") == "héllo"
    assert out == f"wrote 5 characters to {p}"


def test_write_file_refuses_to_overwrite_without_saying_so(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("old", encoding="utf-8")
    assert files.write_file(str(p), "new") == f"error: {p} exists; say overwrite to replace it"
    assert p.read_text(encoding="utf-8") == "old"


def test_write_file_never_replaces_a_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_TRASH_DIR", str(tmp_path / "trash"))
    folder = tmp_path / "Projects"
    (folder / "keep").mkdir(parents=True)
    out = files.write_file(str(folder), "x", overwrite=True)
    assert out.startswith(f"error: {folder} is a folder; give a file name inside it")
    assert (folder / "keep").is_dir() and not (tmp_path / "trash").exists()


def test_write_file_overwrite_puts_the_old_file_in_the_trash(tmp_path, monkeypatch):
    trash = tmp_path / "trash"
    monkeypatch.setenv("IRIS_TRASH_DIR", str(trash))
    p = tmp_path / "a.txt"
    p.write_text("old", encoding="utf-8")
    out = files.write_file(str(p), "new", overwrite=True)
    assert out == f"wrote 3 characters to {p}"
    assert p.read_text(encoding="utf-8") == "new"
    trashed = [x for x in trash.iterdir() if not x.name.endswith(".json")]
    assert len(trashed) == 1
    assert trashed[0].read_text(encoding="utf-8") == "old"
    assert any(x.name.endswith(".json") for x in trash.iterdir())  # the same mechanism as trash_file


# --- make_folder -------------------------------------------------------------------


def test_make_folder_creates_missing_parents(tmp_path):
    target = tmp_path / "a" / "b"
    out = files.make_folder(str(target))
    assert target.is_dir()
    assert out == f"made folder {target}"


def test_make_folder_refuses_an_existing_path(tmp_path):
    p = tmp_path / "x"
    p.mkdir()
    assert files.make_folder(str(p)) == f"error: {p} already exists"


# --- move_file and copy_file ---------------------------------------------------------


def test_move_file_moves_a_file_and_creates_missing_parents(tmp_path):
    s = tmp_path / "a.txt"
    s.write_text("x", encoding="utf-8")
    d = tmp_path / "sub" / "a.txt"
    out = files.move_file(str(s), str(d))
    assert not s.exists()
    assert d.read_text(encoding="utf-8") == "x"
    assert out == f"moved {s} to {d}"


def test_move_file_into_an_existing_folder(tmp_path):
    s = tmp_path / "a.txt"
    s.write_text("x", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    files.move_file(str(s), str(dest))
    assert (dest / "a.txt").read_text(encoding="utf-8") == "x"


def test_move_file_never_overwrites(tmp_path):
    s = tmp_path / "a.txt"
    s.write_text("new", encoding="utf-8")
    d = tmp_path / "b.txt"
    d.write_text("old", encoding="utf-8")
    assert files.move_file(str(s), str(d)) == f"error: {d} already exists; never overwrites"
    assert s.exists() and d.read_text(encoding="utf-8") == "old"


def test_move_file_says_so_when_the_source_is_missing(tmp_path):
    out = files.move_file(str(tmp_path / "nope.txt"), str(tmp_path / "d"))
    assert out.startswith("error: no such file or folder:")


def test_move_file_error_names_the_missing_source_in_full(tmp_path):
    out = files.move_file(str(tmp_path / "nope.txt"), str(tmp_path / "d"))
    assert out == f"error: no such file or folder: {tmp_path / 'nope.txt'}"


def test_copy_file_copies_a_folder_with_its_contents(tmp_path):
    s = tmp_path / "proj"
    (s / "deep").mkdir(parents=True)
    (s / "a.txt").write_text("1", encoding="utf-8")
    (s / "deep" / "b.txt").write_text("2", encoding="utf-8")
    out = files.copy_file(str(s), str(tmp_path / "copy"))
    assert s.exists()  # the source stays
    assert (tmp_path / "copy" / "a.txt").read_text(encoding="utf-8") == "1"
    assert (tmp_path / "copy" / "deep" / "b.txt").read_text(encoding="utf-8") == "2"
    assert out.startswith("copied ")


def test_copy_file_never_overwrites(tmp_path):
    s = tmp_path / "a.txt"
    s.write_text("new", encoding="utf-8")
    d = tmp_path / "b.txt"
    d.write_text("old", encoding="utf-8")
    assert files.copy_file(str(s), str(d)) == f"error: {d} already exists; never overwrites"
    assert s.exists() and d.read_text(encoding="utf-8") == "old"


def test_copy_file_says_so_when_the_source_is_missing(tmp_path):
    out = files.copy_file(str(tmp_path / "nope.txt"), str(tmp_path / "d"))
    assert out.startswith("error: no such file or folder:")


def test_copy_file_error_names_the_missing_source_in_full(tmp_path):
    out = files.copy_file(str(tmp_path / "nope.txt"), str(tmp_path / "d"))
    assert out == f"error: no such file or folder: {tmp_path / 'nope.txt'}"


# --- tiers for the new tools ----------------------------------------------------------


def test_new_tools_have_their_base_tiers():
    assert tools.TOOLS["write_file"].tier == tools.CONFIRM
    assert tools.TOOLS["make_folder"].tier == tools.SAFE
    assert tools.TOOLS["move_file"].tier == tools.CONFIRM
    assert tools.TOOLS["copy_file"].tier == tools.CONFIRM


def test_write_tier_confirms_inside_folders_and_never_outside(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    _cfg(tmp_path, monkeypatch, f'folders = ["{work.as_posix()}"]\n')
    assert tools.TOOLS["write_file"].tier_of({"path": str(work / "a.txt")}) == tools.CONFIRM
    assert tools.TOOLS["write_file"].tier_of({"path": str(outside / "b.txt")}) == tools.NEVER


def test_make_folder_tier_is_safe_inside_and_never_outside(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    _cfg(tmp_path, monkeypatch, f'folders = ["{work.as_posix()}"]\n')
    assert tools.TOOLS["make_folder"].tier_of({"path": str(work / "new")}) == tools.SAFE
    assert tools.TOOLS["make_folder"].tier_of({"path": str(outside / "new")}) == tools.NEVER


def test_move_tier_never_when_the_destination_or_source_is_outside(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    _cfg(tmp_path, monkeypatch, f'folders = ["{work.as_posix()}"]\n')
    assert tools.TOOLS["move_file"].tier_of(
        {"source": str(work / "a"), "destination": str(outside / "b")}) == tools.NEVER
    assert tools.TOOLS["move_file"].tier_of(
        {"source": str(outside / "a"), "destination": str(work / "b")}) == tools.NEVER
    assert tools.TOOLS["move_file"].tier_of(
        {"source": str(work / "a"), "destination": str(work / "b")}) == tools.CONFIRM


def test_copy_tier_checks_only_the_destination(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    _cfg(tmp_path, monkeypatch, f'folders = ["{work.as_posix()}"]\n')
    assert tools.TOOLS["copy_file"].tier_of(
        {"source": str(outside / "a"), "destination": str(work / "b")}) == tools.CONFIRM
    assert tools.TOOLS["copy_file"].tier_of(
        {"source": str(work / "a"), "destination": str(outside / "b")}) == tools.NEVER


def test_write_tier_never_in_system_dirs_even_when_configured(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    sysroot = os.environ.get("SystemRoot", "C:/Windows")
    _cfg(tmp_path, monkeypatch, f'folders = ["{sysroot}"]\n')
    assert tools.TOOLS["write_file"].tier_of({"path": str(Path(sysroot) / "system32" / "x.txt")}) == tools.NEVER


def test_write_tier_never_in_the_trash(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    trash = tmp_path / "trash"
    monkeypatch.setenv("IRIS_TRASH_DIR", str(trash))
    _cfg(tmp_path, monkeypatch, f'folders = ["{tmp_path.as_posix()}"]\n')
    assert tools.TOOLS["write_file"].tier_of({"path": str(trash / "x.txt")}) == tools.NEVER


def test_describe_names_the_new_file_actions():
    from iris import agent

    assert agent._describe("write_file", {"path": "a.txt"}) == "Write a.txt?"
    assert (agent._describe("write_file", {"path": "a.txt", "overwrite": True})
            == "Write a.txt? It replaces the old file; the old one goes to the trash.")
    assert agent._describe("move_file", {"source": "a", "destination": "b"}) == "Move a to b?"
    assert agent._describe("copy_file", {"source": "a", "destination": "b"}) == "Copy a to b?"



# --- review fixes: secret names with "key", the write tier of a bare name -----------------------

def test_key_as_a_word_in_a_file_name_is_a_secret():
    t = tools.TOOLS["read_file"]
    for name in ("google_key.txt", "api-key.txt", "KEY.txt", "ssh keys.md", "C:/x/telefoon_wachtwoord.txt"):
        assert t.tier_of({"path": name}) == tools.NEVER, name
    for name in ("keyboard.txt", "monkey.txt", "turkey-recipe.md", "notes.txt"):
        assert t.tier_of({"path": name}) == tools.SAFE, name


def test_write_tier_of_a_bare_name_looks_in_the_iris_documents_folder(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir("C:/Windows")  # the cwd must not matter
    assert tools.TOOLS["write_file"].tier_of({"path": "notes.txt"}) == tools.CONFIRM


def test_move_and_copy_into_a_new_folder_never_rename_the_file(tmp_path):
    from iris.files import copy_file, move_file

    src = tmp_path / "facturen-2026-118.docx"
    src.write_text("x", encoding="utf-8")
    assert move_file(str(src), str(tmp_path / "Archief")).endswith(str(tmp_path / "Archief" / src.name))
    assert (tmp_path / "Archief" / src.name).exists() and not src.exists()
    budget = tmp_path / "budget.xlsx"
    budget.write_text("b", encoding="utf-8")
    copy_file(str(budget), str(tmp_path / "Backups"))
    assert (tmp_path / "Backups" / "budget.xlsx").read_text(encoding="utf-8") == "b"
    copy_file(str(budget), str(tmp_path / "Kopie") + "/")  # a trailing slash means a folder
    assert (tmp_path / "Kopie" / "budget.xlsx").exists()
    copy_file(str(budget), str(tmp_path / "budget-oud.xlsx"))  # a name with an extension is a new name
    assert (tmp_path / "budget-oud.xlsx").exists()
    folder = tmp_path / "fotos"
    folder.mkdir()
    move_file(str(folder), str(tmp_path / "fotos-2026"))  # a folder without extension is still a rename
    assert (tmp_path / "fotos-2026").is_dir() and not (tmp_path / "fotos-2026" / "fotos").exists()


def test_no_file_tool_may_change_iris_own_code_or_state(tmp_path, monkeypatch):
    import iris

    home = _home(tmp_path, monkeypatch)
    package = Path(iris.__file__).resolve().parent  # only named, never written: tiers do not need the file
    _cfg(tmp_path, monkeypatch, f'folders = ["{package.parent.as_posix()}"]')  # even when the code's folder is listed
    monkeypatch.setenv("IRIS_SKILLS", str(home / ".iris" / "skills"))
    own = [home / ".iris" / "config.toml", home / ".iris" / "skills" / "approved.json",
           home / ".iris" / "skills" / "hue" / "skill.py", package / "tier_lock.json", package / "policy.py",
           tmp_path / "config.toml"]
    for p in own:
        assert tools.TOOLS["write_file"].tier_of({"path": str(p), "overwrite": True}) == tools.NEVER, p
        assert tools.TOOLS["trash_file"].tier_of({"path": str(p)}) == tools.NEVER, p
        assert tools.TOOLS["copy_file"].tier_of({"source": "a", "destination": str(p)}) == tools.NEVER, p
        assert tools.TOOLS["move_file"].tier_of({"source": str(p), "destination": str(home / "x")}) == tools.NEVER
    assert tools.TOOLS["write_file"].tier_of({"path": str(home / "Documents" / "a.txt")}) == tools.CONFIRM


@pytest.mark.parametrize("dest", ["A/Archive", "A/backup/", "A"])
def test_a_folder_is_never_copied_or_moved_into_itself(tmp_path, dest):
    (tmp_path / "A" / "Archive").mkdir(parents=True)
    (tmp_path / "A" / "f.txt").write_text("x", encoding="utf-8")
    for fn in (files.copy_file, files.move_file):
        out = fn(str(tmp_path / "A"), str(tmp_path / dest))
        assert out.startswith("error: ") and "inside" in out, out
    assert sorted(p.name for p in (tmp_path / "A").iterdir()) == ["Archive", "f.txt"]
    assert list((tmp_path / "A" / "Archive").iterdir()) == []
