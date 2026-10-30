"""Tests for the timer module; a fake clock and a fake scheduler fire timers by hand, no real threads."""

from collections.abc import Callable

import pytest

from iris.timers import MAX_SECONDS, Timers


class _Handle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeStart:
    """Records (seconds, fn) so a test can fire a timer by hand; returns a handle it can cancel."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, Callable[[], None], _Handle]] = []

    def __call__(self, seconds: float, fn: Callable[[], None]) -> _Handle:
        handle = _Handle()
        self.calls.append((seconds, fn, handle))
        return handle


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_timers():
    announced: list[str] = []
    start = FakeStart()
    clock = FakeClock()
    timers = Timers(announce=announced.append, now=clock, start=start)
    return timers, start, clock, announced


def test_add_returns_id_label_and_seconds():
    timers, start, _, _ = make_timers()
    assert timers.add(60, "pasta") == {"id": 1, "label": "pasta", "seconds": 60}
    assert timers.add(30) == {"id": 2, "label": "", "seconds": 30}
    # the scheduler was asked for each timer's seconds and a fire function
    assert [c[0] for c in start.calls] == [60.0, 30.0]


def test_list_is_sorted_by_due_time_with_left_seconds_rounded_up():
    timers, _, clock, _ = make_timers()
    timers.add(120, "two minutes")        # due at 120
    timers.add(60, "one minute")          # due at 60
    timers.add(90.4, "ninety point four")  # due at 90.4
    clock.advance(30)                     # now = 30
    listing = timers.list()
    assert [t["id"] for t in listing] == [2, 3, 1]  # soonest first: 60, 90.4, 120
    assert listing[0] == {"id": 2, "label": "one minute", "left_seconds": 30}
    assert listing[1]["left_seconds"] == 61  # ceil(90.4 - 30) = ceil(60.4)
    assert listing[2] == {"id": 1, "label": "two minutes", "left_seconds": 90}


def test_left_seconds_is_never_negative():
    timers, _, clock, _ = make_timers()
    timers.add(60, "pasta")
    clock.advance(61)  # past due but not fired: the fake scheduler never fires on its own
    [t] = timers.list()
    assert t["left_seconds"] == 0


def test_fire_announces_and_removes_the_timer():
    timers, start, _, announced = make_timers()
    timers.add(60, "pasta")
    start.calls[0][1]()  # fire by hand
    assert announced == ["Timer pasta is done."]
    assert timers.list() == []


def test_fire_with_an_empty_label_announces_timer_done():
    timers, start, _, announced = make_timers()
    timers.add(60)
    start.calls[0][1]()
    assert announced == ["Timer done."]


def test_firing_twice_only_announces_once():
    timers, start, _, announced = make_timers()
    timers.add(60, "pasta")
    fn = start.calls[0][1]
    fn()
    fn()  # already removed: a second fire is a no-op
    assert announced == ["Timer pasta is done."]


def test_cancel_before_fire_never_announces():
    timers, start, _, announced = make_timers()
    timer_id = timers.add(60, "pasta")["id"]
    handle = start.calls[0][2]
    assert timers.cancel(timer_id) is True
    assert handle.cancelled is True  # the scheduled thread is cancelled too
    start.calls[0][1]()  # even if it fires late, nothing happens
    assert announced == []
    assert timers.list() == []


def test_cancel_of_an_unknown_id_is_false():
    timers, _, _, _ = make_timers()
    assert timers.cancel(99) is False


def test_cancel_all_cancels_everything_and_returns_the_count():
    timers, start, _, announced = make_timers()
    for label in ("a", "b", "c"):
        timers.add(60, label)
    assert timers.cancel_all() == 3
    assert timers.list() == []
    for fn in (c[1] for c in start.calls):
        fn()  # late fires are all no-ops
    assert announced == []


def test_add_refuses_zero_and_negative_seconds():
    timers, _, _, _ = make_timers()
    with pytest.raises(ValueError, match="greater than zero"):
        timers.add(0)
    with pytest.raises(ValueError, match="greater than zero"):
        timers.add(-5, "pasta")


def test_add_refuses_more_than_24_hours_but_allows_exactly_24():
    timers, _, _, _ = make_timers()
    with pytest.raises(ValueError, match="at most 86400"):
        timers.add(MAX_SECONDS + 1)
    assert timers.add(MAX_SECONDS)["id"] == 1  # exactly one day is allowed


def test_fire_with_a_message_announces_exactly_that_message():
    timers, start, _, announced = make_timers()
    timers.add(60, "pasta", "De pasta is klaar.")
    start.calls[0][1]()  # fire by hand
    assert announced == ["De pasta is klaar."]


def test_fire_without_a_message_keeps_the_english_default():
    timers, start, _, announced = make_timers()
    timers.add(60, "pasta")
    start.calls[0][1]()
    assert announced == ["Timer pasta is done."]


def test_add_refuses_a_message_longer_than_200_characters_but_allows_exactly_200():
    timers, _, _, _ = make_timers()
    with pytest.raises(ValueError, match="message too long"):
        timers.add(60, "pasta", "x" * 201)
    assert timers.add(60, "pasta", "y" * 200)["id"] == 1  # exactly 200 is allowed
