"""Reminders that survive a restart.

Pending reminders are a JSON list in ~/.iris/reminders.json (IRIS_REMINDERS moves the file):
[{"id": 1, "due": "2026-11-01T09:30", "text": "..."}]. While Iris is running, each future reminder
waits on a daemon threading.Timer; one that was due while Iris was off is announced at once as
"Missed reminder: <text>". A fired or cancelled reminder leaves the file. The clock and the
scheduler are injected so tests can fire reminders by hand instead of waiting on real threads.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

MAX_DAYS_AHEAD = 366  # a reminder is at most a year away
MAX_TEXT = 200  # the same cap as a timer message: it will be spoken out loud

_CLOCK_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _path() -> Path:
    env = os.environ.get("IRIS_REMINDERS", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".iris" / "reminders.json"


def _wall_now() -> datetime:
    """The wall clock; a module function so tests can monkeypatch it."""
    return datetime.now()


def _start_thread(seconds: float, fn: Callable[[], None]) -> threading.Timer:
    """The default scheduler: a daemon thread that calls `fn` after `seconds`."""
    timer = threading.Timer(max(0.0, seconds), fn)
    timer.daemon = True
    timer.start()
    return timer


def _cancel_handle(handle: object) -> None:
    """Cancel the scheduled handle if it can be cancelled (a test fake may not be)."""
    cancel = getattr(handle, "cancel", None)
    if callable(cancel):
        cancel()


_lock = threading.Lock()
_handles: dict[int, object] = {}  # reminder id -> scheduled handle; empty until start_reminders runs
_announce: Callable[[str], object] | None = None
_schedule: Callable[[float, Callable[[], None]], object] = _start_thread


def _read(path: Path | None = None) -> list[dict]:
    """The pending reminders; a missing or unreadable file is an empty list, never fatal."""
    p = path or _path()
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not (isinstance(row, dict) and isinstance(row.get("id"), int)
                and isinstance(row.get("due"), str) and isinstance(row.get("text"), str)):
            continue
        try:
            datetime.fromisoformat(row["due"])
        except ValueError:
            continue  # a reminder whose time cannot be read is left alone, never fatal
        out.append(row)
    return out


def _write(path: Path | None, rows: list[dict]) -> None:
    p = path or _path()
    p.parent.mkdir(parents=True, exist_ok=True)  # a fresh PC has no ~/.iris yet
    tmp = p.with_name(p.name + ".tmp")  # write aside, then swap: a crash mid-write never empties the list
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _parse_when(when: str, now: datetime) -> datetime | None:
    """'HH:MM' means today (tomorrow when it already passed); 'YYYY-MM-DD HH:MM' is taken as given."""
    s = when.strip()
    m = _CLOCK_RE.fullmatch(s)
    if m is not None:
        target = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _fire(rid: int, text: str) -> None:
    """Runs on the timer thread when a reminder is due; announces it and removes it from the file."""
    with _lock:
        _handles.pop(rid, None)
        rows = _read()
        kept = [r for r in rows if r["id"] != rid]
        if len(kept) == len(rows):
            return  # cancelled before it fired: never announces
        _write(None, kept)
    announce = _announce
    if announce is not None:
        announce(text)


def _fire_later(rid: int, text: str) -> Callable[[], None]:
    """A zero-argument callable for the timer thread; binds this row's id and text when created."""
    return lambda: _fire(rid, text)


def reminder_set(when: str, text: str) -> str:
    """Remember `text` for a time; Iris says it out loud when the time comes.

    `when` is 'HH:MM' (today, or tomorrow when already past) or 'YYYY-MM-DD HH:MM'; more than 366 days
    ahead is an error. The reminder survives a restart and, while Iris is running, starts waiting at once."""
    flat = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
    if not flat:
        return "error: say what to remind about"
    if len(flat) > MAX_TEXT:
        return f"error: reminders are at most {MAX_TEXT} characters (this one is {len(flat)})"
    now = _wall_now()
    due = _parse_when(when, now)
    if due is None:
        return "error: when must look like 15:00 or 2026-11-01 09:30"
    if due <= now:
        return "error: that time has already passed"
    if due > now + timedelta(days=MAX_DAYS_AHEAD):
        return f"error: reminders are at most {MAX_DAYS_AHEAD} days ahead"
    with _lock:
        rows = _read()
        new_id = max((r["id"] for r in rows), default=0) + 1
        rows.append({"id": new_id, "due": due.strftime("%Y-%m-%dT%H:%M"), "text": flat})
        _write(None, rows)
        if _announce is not None:
            _handles[new_id] = _schedule((due - now).total_seconds(), _fire_later(new_id, flat))
    return f"reminder set for {due:%Y-%m-%d %H:%M} (id {new_id})"


def reminder_list() -> str:
    """The pending reminders with their times and ids, soonest first."""
    rows = _read()
    if not rows:
        return "no reminders set"
    parsed = sorted(((datetime.fromisoformat(r["due"]), r) for r in rows), key=lambda t: (t[0], t[1]["id"]))
    return "\n".join(f"- {due:%Y-%m-%d %H:%M} {r['text']} (id {r['id']})" for due, r in parsed)


def reminder_cancel(rid: int) -> str:
    """Cancel one pending reminder by its id; it stops waiting and leaves the file."""
    with _lock:
        handle = _handles.pop(rid, None)
        rows = _read()
        kept = [r for r in rows if r["id"] != rid]
        if len(kept) == len(rows):
            return f"error: no reminder with id {rid}"
        removed = next(r for r in rows if r["id"] == rid)
        _write(None, kept)
    _cancel_handle(handle)
    due = datetime.fromisoformat(removed["due"])
    return f"cancelled reminder ({due:%Y-%m-%d %H:%M})"


def start_reminders(announce: Callable[[str], object], now: Callable[[], datetime] | None = None,
                    schedule: Callable[[float, Callable[[], None]], object] | None = None) -> None:
    """Schedule every pending reminder; called once when Iris starts.

    Future reminders wait on a daemon threading.Timer (the injected `schedule` stands in for it in
    tests); one that was due while Iris was off is announced at once as "Missed reminder: <text>" and
    leaves the file."""
    global _announce, _schedule
    clock = now or _wall_now
    with _lock:
        _announce = announce
        _schedule = schedule or _start_thread
        rows = _read()
        kept: list[dict] = []
        for row in rows:
            due = datetime.fromisoformat(row["due"])  # _read only keeps rows whose time parses
            t = clock()
            if due <= t:
                announce(f"Missed reminder: {row['text']}")
            else:
                rid = row["id"]
                _handles[rid] = _schedule((due - t).total_seconds(), _fire_later(rid, row["text"]))
                kept.append(row)
        if len(kept) != len(rows):
            _write(None, kept)

