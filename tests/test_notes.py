"""Notes in ~/.iris/notes.md (IRIS_NOTES overrides): add, read newest first, remove with confirmation."""

from datetime import datetime

import pytest

from iris import notes
from iris.agent import _describe
from iris.tools import CONFIRM, SAFE, TOOLS


@pytest.fixture(autouse=True)
def notes_file(tmp_path, monkeypatch):
    p = tmp_path / "notes.md"
    monkeypatch.setenv("IRIS_NOTES", str(p))
    return p


class _Clock(datetime):
    @staticmethod
    def now(tz=None):
        return datetime(2026, 10, 31, 9, 5)


# --- note_add ------------------------------------------------------------------

def test_note_add_appends_a_timestamped_line(notes_file, monkeypatch):
    monkeypatch.setattr(notes, "datetime", _Clock)
    assert notes.note_add("buy milk") == "added a note"
    assert notes_file.read_text(encoding="utf-8") == "- 2026-10-31 09:05 buy milk\n"


def test_note_add_turns_newlines_into_spaces(notes_file, monkeypatch):
    monkeypatch.setattr(notes, "datetime", _Clock)
    notes.note_add("a\nb\rc")
    assert notes_file.read_text(encoding="utf-8") == "- 2026-10-31 09:05 a b c\n"


def test_note_add_rejects_more_than_2000_characters(notes_file):
    result = notes.note_add("x" * 2001)
    assert result.startswith("error:") and "2000" in result
    assert not notes_file.exists()


def test_note_add_accepts_exactly_2000_characters(notes_file, monkeypatch):
    monkeypatch.setattr(notes, "datetime", _Clock)
    assert notes.note_add("x" * 2000) == "added a note"
    text = notes_file.read_text(encoding="utf-8").strip().split(" ", 3)[3]
    assert len(text) == 2000


def test_note_add_empty_is_an_error(notes_file):
    assert notes.note_add("").startswith("error:")
    assert notes.note_add(" \n ").startswith("error:")
    assert not notes_file.exists()


# --- notes_read ----------------------------------------------------------------

def test_notes_read_returns_newest_first(notes_file):
    notes_file.write_text(
        "- 2026-10-31 09:01 first\n"
        "- 2026-10-31 09:03 third\n"
        "- 2026-10-31 09:02 second\n", encoding="utf-8")
    assert notes.notes_read() == (
        "- 2026-10-31 09:03 third\n"
        "- 2026-10-31 09:02 second\n"
        "- 2026-10-31 09:01 first")


def test_notes_read_caps_at_last(notes_file):
    notes_file.write_text("".join(f"- 2026-10-31 09:0{i} note {i}\n" for i in range(1, 6)), encoding="utf-8")
    assert notes.notes_read(last=2) == "- 2026-10-31 09:05 note 5\n- 2026-10-31 09:04 note 4"


def test_notes_read_filters_by_query_case_insensitively(notes_file):
    notes_file.write_text(
        "- 2026-10-31 09:01 buy milk\n"
        "- 2026-10-31 09:02 call mom\n", encoding="utf-8")
    assert notes.notes_read(query="MILK") == "- 2026-10-31 09:01 buy milk"


def test_notes_read_without_a_file_says_no_notes_yet(notes_file):
    assert notes.notes_read() == "no notes yet"


def test_notes_read_query_without_matches(notes_file):
    notes_file.write_text("- 2026-10-31 09:01 buy milk\n", encoding="utf-8")
    assert notes.notes_read(query="xyz") == 'no notes containing "xyz"'


def test_notes_read_last_below_one_is_an_error(notes_file):
    assert notes.notes_read(last=0).startswith("error:")


# --- note_remove ---------------------------------------------------------------

def test_note_remove_removes_the_single_match(notes_file):
    notes_file.write_text(
        "- 2026-10-31 09:01 buy milk\n"
        "- 2026-10-31 09:02 call mom\n", encoding="utf-8")
    assert notes.note_remove("milk") == "removed a note (2026-10-31 09:01)"
    assert notes_file.read_text(encoding="utf-8") == "- 2026-10-31 09:02 call mom\n"


def test_note_remove_removing_the_last_note_leaves_an_empty_file(notes_file):
    notes_file.write_text("- 2026-10-31 09:01 buy milk\n", encoding="utf-8")
    assert notes.note_remove("milk").startswith("removed a note")
    assert notes_file.read_text(encoding="utf-8") == ""


def test_note_remove_none_matching_is_an_error(notes_file):
    notes_file.write_text("- 2026-10-31 09:01 buy milk\n", encoding="utf-8")
    result = notes.note_remove("xyz")
    assert result.startswith("error:") and 'no notes containing "xyz"' in result
    assert notes_file.read_text(encoding="utf-8") == "- 2026-10-31 09:01 buy milk\n"


def test_note_remove_several_matches_lists_them_and_keeps_the_file(notes_file):
    notes_file.write_text(
        "- 2026-10-31 09:01 buy milk\n"
        "- 2026-10-31 09:02 drink milk tea\n", encoding="utf-8")
    result = notes.note_remove("milk")
    assert result.startswith("error:") and "2 notes" in result
    assert "buy milk" in result and "drink milk tea" in result
    assert notes_file.read_text(encoding="utf-8").count("\n") == 2


# --- tiers, describe, paths ----------------------------------------------------

def test_tiers_pin_safe_and_confirm():
    assert TOOLS["note_add"].tier_of({"text": "x"}) is SAFE
    assert TOOLS["notes_read"].tier_of({}) is SAFE
    assert TOOLS["note_remove"].tier_of({"query": "milk"}) is CONFIRM


def test_describe_shows_the_single_matched_note(notes_file):
    notes_file.write_text("- 2026-10-31 09:01 buy milk for the picnic\n", encoding="utf-8")
    assert _describe("note_remove", {"query": "milk"}) == "Remove the note 'buy milk for the picnic'?"


def test_describe_truncates_long_notes_to_60_chars(notes_file):
    notes_file.write_text(f"- 2026-10-31 09:01 {'a' * 70}\n", encoding="utf-8")
    assert _describe("note_remove", {"query": "aaa"}) == f"Remove the note '{'a' * 60}'?"


def test_describe_falls_back_to_the_query_when_several_match(notes_file):
    notes_file.write_text(
        "- 2026-10-31 09:01 buy milk\n"
        "- 2026-10-31 09:02 drink milk tea\n", encoding="utf-8")
    assert _describe("note_remove", {"query": "milk"}) == "Remove the note matching milk?"


def test_default_path_is_under_home(monkeypatch, tmp_path):
    monkeypatch.delenv("IRIS_NOTES", raising=False)
    monkeypatch.setattr(notes.Path, "home", staticmethod(lambda: tmp_path))
    assert notes._path() == tmp_path / ".iris" / "notes.md"

