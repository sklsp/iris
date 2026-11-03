"""Notes in ~/.iris/notes.md (IRIS_NOTES overrides): add, read newest first, remove with confirmation.

Reminders in ~/.iris/reminders.json (IRIS_REMINDERS overrides): set, list, cancel, survive a restart."""

import json
from datetime import datetime

import pytest

from iris import notes, reminders
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


# --- find_notes ------------------------------------------------------------------

def test_find_notes_with_a_blank_query_is_empty(notes_file):
    notes_file.write_text("- 2026-10-31 09:01 buy milk\n", encoding="utf-8")
    assert notes.find_notes("   ") == []


# --- note_remove ---------------------------------------------------------------

def test_note_remove_with_a_blank_query_is_an_error(notes_file):
    assert notes.note_remove("") == "error: say which note to remove"
    assert notes.note_remove("   ") == "error: say which note to remove"


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


# --- reminders -----------------------------------------------------------------


@pytest.fixture()
def reminders_file(tmp_path, monkeypatch):
    p = tmp_path / "reminders.json"
    monkeypatch.setenv("IRIS_REMINDERS", str(p))
    reminders._handles.clear()  # module state from an earlier test must not leak in
    reminders._announce = None
    reminders._schedule = reminders._start_thread
    return p


class _Sched:
    """A stand-in for the daemon threading.Timer that records what was scheduled instead of waiting."""

    def __init__(self):
        self.calls = []  # (seconds, fn) in the order they were scheduled

    def __call__(self, seconds, fn):
        self.calls.append((seconds, fn))
        return object()


def _now_905():
    return datetime(2026, 10, 31, 9, 5)


# --- reminder_set ---------------------------------------------------------------


def test_reminder_set_writes_a_pending_row(reminders_file, monkeypatch):
    monkeypatch.setattr(reminders, "_wall_now", _now_905)
    assert reminders.reminder_set("09:30", "buy milk") == "reminder set for 2026-10-31 09:30 (id 1)"
    assert json.loads(reminders_file.read_text(encoding="utf-8")) == [
        {"id": 1, "due": "2026-10-31T09:30", "text": "buy milk"}]


def test_reminder_set_clock_time_already_past_means_tomorrow(reminders_file, monkeypatch):
    monkeypatch.setattr(reminders, "_wall_now", _now_905)
    assert reminders.reminder_set("08:00", "late") == "reminder set for 2026-11-01 08:00 (id 1)"


def test_reminder_set_explicit_date_time(reminders_file, monkeypatch):
    monkeypatch.setattr(reminders, "_wall_now", _now_905)
    assert reminders.reminder_set("2026-11-01 08:00", "call the dentist") == (
        "reminder set for 2026-11-01 08:00 (id 1)")


def test_reminder_set_exactly_366_days_ahead_is_allowed(reminders_file, monkeypatch):
    monkeypatch.setattr(reminders, "_wall_now", _now_905)
    assert reminders.reminder_set("2027-11-01 09:05", "a year") == "reminder set for 2027-11-01 09:05 (id 1)"


def test_reminder_set_more_than_366_days_ahead_is_an_error(reminders_file, monkeypatch):
    monkeypatch.setattr(reminders, "_wall_now", _now_905)
    result = reminders.reminder_set("2027-11-02 09:05", "too far")
    assert result.startswith("error:") and "366" in result
    assert not reminders_file.exists()


def test_reminder_set_past_time_is_an_error(reminders_file, monkeypatch):
    monkeypatch.setattr(reminders, "_wall_now", _now_905)
    assert reminders.reminder_set("2026-10-31 08:00", "yesterday").startswith("error:")


def test_reminder_set_bad_when_is_an_error(reminders_file, monkeypatch):
    monkeypatch.setattr(reminders, "_wall_now", _now_905)
    assert reminders.reminder_set("banana", "x").startswith("error:")
    assert reminders.reminder_set("2026-13-40 09:00", "x").startswith("error:")


def test_reminder_set_empty_text_is_an_error(reminders_file):
    assert reminders.reminder_set("09:30", "   ").startswith("error:")
    assert not reminders_file.exists()


def test_reminder_set_turns_newlines_into_spaces_and_caps_at_200_chars(reminders_file, monkeypatch):
    monkeypatch.setattr(reminders, "_wall_now", _now_905)
    assert reminders.reminder_set("09:30", "a\nb") == "reminder set for 2026-10-31 09:30 (id 1)"
    result = reminders.reminder_set("09:40", "x" * 201)
    assert result.startswith("error:") and "200" in result


def test_reminder_set_ids_increase(reminders_file, monkeypatch):
    monkeypatch.setattr(reminders, "_wall_now", _now_905)
    reminders.reminder_set("09:30", "first")
    assert reminders.reminder_set("09:40", "second").endswith("(id 2)")


# --- reminder_list ---------------------------------------------------------------


def test_reminder_list_without_a_file_says_no_reminders(reminders_file):
    assert reminders.reminder_list() == "no reminders set"


def test_reminder_list_is_soonest_first_with_time_and_id(reminders_file):
    reminders_file.write_text(json.dumps([
        {"id": 1, "due": "2026-11-01T09:30", "text": "buy milk"},
        {"id": 2, "due": "2026-10-31T18:00", "text": "call mom"}]), encoding="utf-8")
    assert reminders.reminder_list() == (
        "- 2026-10-31 18:00 call mom (id 2)\n"
        "- 2026-11-01 09:30 buy milk (id 1)")


# --- reminder_cancel ---------------------------------------------------------------


def test_reminder_cancel_removes_the_row(reminders_file):
    reminders_file.write_text(json.dumps([
        {"id": 1, "due": "2026-10-31T18:00", "text": "call mom"},
        {"id": 2, "due": "2026-11-01T09:30", "text": "buy milk"}]), encoding="utf-8")
    assert reminders.reminder_cancel(1) == "cancelled reminder (2026-10-31 18:00)"
    assert json.loads(reminders_file.read_text(encoding="utf-8")) == [
        {"id": 2, "due": "2026-11-01T09:30", "text": "buy milk"}]


def test_reminder_cancel_unknown_id_is_an_error_and_keeps_the_file(reminders_file):
    reminders_file.write_text(json.dumps([{"id": 1, "due": "2026-10-31T18:00", "text": "call mom"}]), encoding="utf-8")
    result = reminders.reminder_cancel(9)
    assert result.startswith("error:") and "9" in result
    assert json.loads(reminders_file.read_text(encoding="utf-8")) == [
        {"id": 1, "due": "2026-10-31T18:00", "text": "call mom"}]


# --- start_reminders ---------------------------------------------------------------


def test_start_reminders_schedules_future_and_announces_missed(reminders_file):
    reminders_file.write_text(json.dumps([
        {"id": 1, "due": "2026-10-31T08:00", "text": "old thing"},
        {"id": 2, "due": "2026-10-31T10:00", "text": "later thing"}]), encoding="utf-8")
    said = []
    sched = _Sched()
    reminders.start_reminders(said.append, now=_now_905, schedule=sched)
    assert said == ["Missed reminder: old thing"]  # due while Iris was off: announced at once
    assert len(sched.calls) == 1 and sched.calls[0][0] == 3300.0  # 9:05 to 10:00 is 55 minutes
    assert json.loads(reminders_file.read_text(encoding="utf-8")) == [
        {"id": 2, "due": "2026-10-31T10:00", "text": "later thing"}]
    sched.calls[0][1]()  # fire it by hand instead of waiting on the thread
    assert said[-1] == "later thing"
    assert json.loads(reminders_file.read_text(encoding="utf-8")) == []


def test_start_reminders_with_nothing_pending_does_nothing(reminders_file):
    said = []
    sched = _Sched()
    reminders.start_reminders(said.append, now=_now_905, schedule=sched)
    assert said == [] and sched.calls == []


def test_cancelled_reminder_never_announces(reminders_file):
    reminders_file.write_text(json.dumps([{"id": 1, "due": "2026-10-31T10:00", "text": "buy milk"}]), encoding="utf-8")
    said = []
    sched = _Sched()
    reminders.start_reminders(said.append, now=_now_905, schedule=sched)
    assert reminders.reminder_cancel(1).startswith("cancelled")
    sched.calls[0][1]()  # the timer thread still runs after a cancel: it must stay silent
    assert said == []


def test_reminder_set_while_running_schedules_at_once(reminders_file, monkeypatch):
    said = []
    sched = _Sched()
    reminders.start_reminders(said.append, now=_now_905, schedule=sched)
    monkeypatch.setattr(reminders, "_wall_now", _now_905)
    assert reminders.reminder_set("10:00", "buy milk") == "reminder set for 2026-10-31 10:00 (id 1)"
    assert len(sched.calls) == 1 and sched.calls[0][0] == 3300.0  # 9:05 to 10:00 is 55 minutes


# --- tiers, paths ------------------------------------------------------------------


def test_reminder_tiers_pin_safe():
    assert TOOLS["reminder_set"].tier_of({"when": "09:30", "text": "x"}) is SAFE
    assert TOOLS["reminder_list"].tier_of({}) is SAFE
    assert TOOLS["reminder_cancel"].tier_of({"id": 1}) is SAFE


def test_reminder_default_path_is_under_home(reminders_file, monkeypatch, tmp_path):
    monkeypatch.delenv("IRIS_REMINDERS", raising=False)
    monkeypatch.setattr(reminders.Path, "home", staticmethod(lambda: tmp_path))
    assert reminders._path() == tmp_path / ".iris" / "reminders.json"



def test_rewrites_never_leave_a_temp_file_behind(tmp_path, monkeypatch):
    from iris import notes, reminders

    monkeypatch.setenv("IRIS_NOTES", str(tmp_path / "notes.md"))
    monkeypatch.setenv("IRIS_REMINDERS", str(tmp_path / "reminders.json"))
    notes.note_add("tandarts donderdag")
    notes.note_add("loodgieter vrijdag")
    assert notes.note_remove("tandarts").startswith("removed")
    reminders._write(None, [{"id": 1, "due": "2026-11-01T09:30", "text": "bellen"}])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.md", "reminders.json"]
    assert "loodgieter" in (tmp_path / "notes.md").read_text(encoding="utf-8")
    assert reminders._read()[0]["text"] == "bellen"
