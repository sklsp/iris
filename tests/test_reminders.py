"""The wait a reminder counts down is right across a daylight saving change.

iris.reminders stores due times as local wall time; the seconds it waits are computed from the
timezone-aware versions of both ends, so a spring or autumn shift in between is counted."""

from datetime import datetime

import pytest

from iris import reminders


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


def _now_before_spring():
    return datetime(2026, 3, 28, 12, 0)


# --- _seconds_until ---------------------------------------------------------------


def test_seconds_until_same_day_one_hour_apart_is_3600():
    now = datetime(2026, 10, 31, 9, 5)
    due = datetime(2026, 10, 31, 10, 5)
    assert reminders._seconds_until(due, now) == 3600.0


def test_seconds_until_counts_a_daylight_saving_change():
    # the last Sunday of March 2026: Europe/Amsterdam jumps from 01:00 to 03:00 on 2026-03-29, so a
    # wall-clock day across it is only 23 hours long; on a UTC machine the offsets match and only
    # the first assert runs
    now = datetime(2026, 3, 28, 12, 0)
    due = datetime(2026, 3, 29, 12, 0)
    assert reminders._seconds_until(due, now) == (due.astimezone() - now.astimezone()).total_seconds()
    if now.astimezone().utcoffset() != due.astimezone().utcoffset():
        shift = (due.astimezone().utcoffset() - now.astimezone().utcoffset()).total_seconds()
        assert reminders._seconds_until(due, now) == 86400.0 - shift


# --- reminder_set ------------------------------------------------------------------


def test_reminder_set_passes_the_seconds_until_value_to_the_schedule(reminders_file, monkeypatch):
    said = []
    sched = _Sched()
    reminders.start_reminders(said.append, now=_now_before_spring, schedule=sched)
    monkeypatch.setattr(reminders, "_wall_now", _now_before_spring)
    assert reminders.reminder_set("2026-03-29 12:00", "spring") == "reminder set for 2026-03-29 12:00 (id 1)"
    now = datetime(2026, 3, 28, 12, 0)
    due = datetime(2026, 3, 29, 12, 0)
    assert len(sched.calls) == 1 and sched.calls[0][0] == reminders._seconds_until(due, now)
