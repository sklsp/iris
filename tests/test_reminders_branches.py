"""Branch tests for the parts of iris/reminders.py that test_notes.py does not reach:
the wall clock, the default thread scheduler, _cancel_handle, and _read dropping bad rows."""

import json
import threading
from datetime import datetime
from pathlib import Path

from iris import reminders


def test_wall_now_returns_the_real_clock():
    first = reminders._wall_now()
    second = reminders._wall_now()
    assert isinstance(first, datetime) and isinstance(second, datetime)
    assert abs((second - first).total_seconds()) <= 5.0  # two real clock reads are close together


def test_start_thread_runs_the_callback_on_a_daemon_timer():
    fired = threading.Event()
    timer = reminders._start_thread(0.0, fired.set)
    assert isinstance(timer, threading.Timer)
    assert timer.daemon is True
    assert fired.wait(timeout=2.0)  # the callback ran on its own thread within 2 s


class _Cancellable:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def test_cancel_handle_calls_cancel_when_it_is_callable():
    handle = _Cancellable()
    reminders._cancel_handle(handle)
    assert handle.cancelled is True


def test_cancel_handle_ignores_an_object_without_cancel():
    reminders._cancel_handle(object())  # a plain object has no cancel: nothing may happen
    reminders._cancel_handle(None)      # reminder_cancel passes None when nothing was scheduled


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_read_of_unreadable_json_is_an_empty_list(tmp_path):
    p = tmp_path / "reminders.json"
    _write(p, "{not json at all")
    assert reminders._read(p) == []


def test_read_when_the_rows_are_not_a_list_is_an_empty_list(tmp_path):
    p = tmp_path / "reminders.json"
    _write(p, json.dumps({"id": 1, "due": "2026-11-01T09:30", "text": "one"}))
    assert reminders._read(p) == []


def test_read_drops_rows_with_a_missing_or_wrong_typed_field(tmp_path):
    p = tmp_path / "reminders.json"
    _write(p, json.dumps([
        {"id": 1, "due": "2026-11-01T09:30"},                    # no text field at all
        {"id": "two", "due": "2026-11-01T09:30", "text": "x"},   # id is not an int
        {"id": 3, "due": 45, "text": "y"},                       # due is not a str
        "not a dict at all",                                     # rows must be dicts
    ]))
    assert reminders._read(p) == []


def test_read_drops_a_row_whose_due_is_not_an_iso_time(tmp_path):
    p = tmp_path / "reminders.json"
    _write(p, json.dumps([{"id": 1, "due": "banana", "text": "x"}]))
    assert reminders._read(p) == []


def test_read_keeps_only_the_good_rows(tmp_path):
    p = tmp_path / "reminders.json"
    good = {"id": 7, "due": "2026-11-01T09:30", "text": "buy milk"}
    _write(p, json.dumps([{"id": 1, "due": "banana", "text": "x"}, good]))
    assert reminders._read(p) == [good]
