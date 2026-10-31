"""Tests for iris.files: find_files, list_folder and read_file, plus their tiers."""

import os
import time
import zipfile
from pathlib import Path

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
        assert files.read_file(str(p)) == "error: install pypdf to read PDF files"
    else:
        assert files.read_file(str(p)) == "hello pdf world"


# --- tiers -----------------------------------------------------------------------

def test_find_files_and_list_folder_are_safe():
    assert tools.TOOLS["find_files"].tier == tools.SAFE
    assert tools.TOOLS["list_folder"].tier == tools.SAFE
    assert tools.TOOLS["read_file"].tier == tools.SAFE  # the base tier; _read_tier escalates


@pytest.mark.parametrize("name", [
    "password.txt", "Wachtwoord.md", "my-secret.json", "token.log", "credentials.csv",
    "apikey.ini", "api_key.toml", "private notes.txt", "server.key", "cert.pem",
    "store.pfx", "vault.kdbx", ".env", "id_rsa", "id_ed25519", "id_ed25519.pub",
])
def test_read_file_never_reads_secret_looking_files(name):
    assert tools.TOOLS["read_file"].tier_of({"path": f"C:/Users/jay/{name}"}) == tools.NEVER


@pytest.mark.parametrize("name", ["notes.txt", "report.docx", "slides.pptx", "paper.pdf", ".env.bak"])
def test_read_file_is_safe_for_everything_else(name):
    assert tools.TOOLS["read_file"].tier_of({"path": f"C:/Users/jay/{name}"}) == tools.SAFE

