"""Timers that Iris announces out loud when they run out.

A small, dependency-free module. `Timers` keeps the running timers (id 1, 2, 3..., label and due time)
and calls an announce callback with a spoken line when one fires. The clock and the scheduler are
injected so tests can fire timers by hand instead of waiting on real threads.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

MAX_SECONDS = 24 * 60 * 60  # a timer runs at most one day


def _start_thread(seconds: float, fn: Callable[[], None]) -> threading.Timer:
    """The default scheduler: a daemon thread that calls `fn` after `seconds`."""
    timer = threading.Timer(seconds, fn)
    timer.daemon = True
    timer.start()
    return timer


def _cancel_handle(handle: object) -> None:
    """Cancel the scheduled handle if it can be cancelled (a test fake may not be)."""
    cancel = getattr(handle, "cancel", None)
    if callable(cancel):
        cancel()


@dataclass
class _Timer:
    label: str
    due: float  # monotonic time when the timer is due
    handle: object  # what start() returned; cancelled on cancel/cancel_all
    message: str = ""  # what Iris says out loud when it fires; empty keeps the English default


class Timers:
    """The running timers. A lock guards the dict because fire runs on a timer thread."""

    def __init__(
        self,
        announce: Callable[[str], object],
        now: Callable[[], float] = time.monotonic,
        start: Callable[[float, Callable[[], None]], object] = _start_thread,
    ) -> None:
        self._announce = announce
        self._now = now
        self._start = start
        self._timers: dict[int, _Timer] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def add(self, seconds: float, label: str = "", message: str = "") -> dict:
        """Start a timer for `seconds` (0 < seconds <= 24 hours); returns its id, label and seconds.

        With a non-empty `message`, Iris says that sentence out loud when the timer fires instead of
        the English default; longer than 200 characters is refused."""
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds <= 0:  # NaN passes both range checks below
            raise ValueError("seconds must be greater than zero")
        if seconds > MAX_SECONDS:
            raise ValueError(f"seconds must be at most {MAX_SECONDS} (24 hours)")
        if len(message) > 200:
            raise ValueError("message too long")
        with self._lock:
            timer_id = self._next_id
            self._next_id += 1
            due = self._now() + seconds
            handle = self._start(seconds, lambda: self._fire(timer_id))
            self._timers[timer_id] = _Timer(label=label, due=due, handle=handle, message=message)
        return {"id": timer_id, "label": label, "seconds": seconds}

    def cancel(self, timer_id: int) -> bool:
        """Cancel one timer by id; True if it was running, False if there is no such timer."""
        with self._lock:
            entry = self._timers.pop(timer_id, None)
        if entry is None:
            return False
        _cancel_handle(entry.handle)
        return True

    def cancel_all(self) -> int:
        """Cancel every running timer and return how many there were."""
        with self._lock:
            entries = list(self._timers.values())
            self._timers.clear()
        for entry in entries:
            _cancel_handle(entry.handle)
        return len(entries)

    def list(self) -> list[dict]:
        """The running timers as id, label and left_seconds (rounded up, never negative), soonest first."""
        with self._lock:
            now = self._now()
            rows = [(t.due, timer_id, t.label) for timer_id, t in self._timers.items()]
        rows.sort(key=lambda row: row[0])  # by due time; left_seconds is the same order
        return [
            {"id": timer_id, "label": label, "left_seconds": max(0, math.ceil(due - now))}
            for due, timer_id, label in rows
        ]

    def _fire(self, timer_id: int) -> None:
        """Runs on the timer thread when a timer is due; announces only if it was not cancelled."""
        with self._lock:
            entry = self._timers.pop(timer_id, None)
        if entry is None:
            return  # cancelled before it fired: never announces
        if entry.message:
            text = entry.message  # the user's own sentence, in their language
        elif entry.label:
            text = f"Timer {entry.label} is done."
        else:
            text = "Timer done."
        self._announce(text)
