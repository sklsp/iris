"""The voice overlay: its event and state logic, the pipe from the voice loop, and the thin Tk part with a fake tkinter.
No window opens: Panel gets a MagicMock for tkinter and a recorder for user32."""

import io
import json
import math
import queue
import runpy
import subprocess
import sys
import threading
import time
import types
from unittest import mock

import numpy as np
import pytest

from iris import overlay, tools
from iris.overlay import (
    ALPHA,
    BG,
    CANCEL,
    COLOUR_FADE,
    COLOURS,
    EDGE_PIECES,
    HIDE_AFTER,
    INNER,
    KEY,
    OUTER,
    Overlay,
    Panel,
    Tint,
    View,
    Win32,
    blend,
    dots,
    edge,
    glow,
    parse,
    pulse,
    rounded_rect,
    split_ring,
    tail,
)
from iris.voice import FRAME, VoiceSession


class Recorder:
    """Stands in for Overlay in a VoiceSession: keeps what was sent."""

    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, event, text=""):
        self.sent.append((event, text) if text else event)

    def close(self):
        self.closed = True


class _Speaker:
    def __init__(self, finished=True):
        self.finished = finished
        self.said = []

    def speak(self, text, stop_event=None):
        self.said.append(text)
        return self.finished


class _Wake:
    def __init__(self, hit):
        self.hit = hit

    def detect(self, frame):
        return self.hit


# --- switching it on ---------------------------------------------------------------------------------------------


def test_overlay_is_off_by_default():
    assert overlay.enabled() is False


@pytest.mark.parametrize("value, on", [("1", True), ("on", True), ("0", False), ("off", False)])
def test_iris_overlay_switches_it(monkeypatch, value, on):
    monkeypatch.setenv("IRIS_OVERLAY", value)
    assert overlay.enabled() is on


@pytest.mark.parametrize("line, on", [("overlay = true", True), ('overlay = "on"', True),
                                      ("overlay = false", False), ("overlay = 1", False)])
def test_config_switches_it(tmp_path, monkeypatch, line, on):
    (tmp_path / "iris-config.toml").write_text(line + "\n", encoding="utf-8")
    assert overlay.enabled() is on


def test_the_environment_wins_over_the_config(tmp_path, monkeypatch):
    (tmp_path / "iris-config.toml").write_text("overlay = true\n", encoding="utf-8")
    monkeypatch.setenv("IRIS_OVERLAY", "0")
    assert overlay.enabled() is False


# --- text and events ---------------------------------------------------------------------------------------------


def test_tail_keeps_short_text_and_tidies_spaces():
    assert tail("  Het is   tien  over vier. ", 100) == "Het is tien over vier."
    assert tail("een\n\n  twee \n", 100) == "een\ntwee"


def test_tail_keeps_the_end_of_long_text():
    out = tail("a" * 50 + " the end", 20)
    assert out.startswith("…") and out.endswith("the end") and len(out) <= 20


def test_tail_keeps_the_last_lines():
    assert tail("1\n2\n3\n4\n5", 100, lines=2) == "…4\n5"


def test_tail_keeps_dutch_and_emoji():
    assert tail("Prima, ik zet een timer ⏰ voor café \U0001f600", 100) == \
        "Prima, ik zet een timer ⏰ voor café \U0001f600"


def test_parse_takes_known_events_only():
    assert parse('{"event": "heard", "text": "hallo"}') == {"event": "heard", "text": "hallo"}
    assert parse("not json") is None
    assert parse('["listening"]') is None
    assert parse('{"event": "explode"}') is None


def test_a_whole_turn_shows_prompt_then_answer_then_hides():
    v = View()
    assert not v.visible and v.lines() == ("", "")
    v.apply({"event": "listening"}, 0)
    assert v.visible and v.mode == "listening" and v.lines() == ("Listening...", "")
    v.apply({"event": "heard", "text": "Hoe laat is het?"}, 1)
    assert v.mode == "thinking" and v.lines() == ("You: Hoe laat is het?", "Thinking...")
    v.apply({"event": "thinking"}, 2)
    assert v.mode == "thinking"
    v.apply({"event": "answer", "text": "Het is tien over vier."}, 3)
    assert v.mode == "speaking" and v.lines() == ("You: Hoe laat is het?", "Het is tien over vier.")
    v.apply({"event": "idle"}, 5)
    assert v.mode == "done" and v.hide_at == 5 + HIDE_AFTER
    assert v.lines() == ("You: Hoe laat is het?", "Het is tien over vier.")  # the answer stays up a little
    v.tick(5 + HIDE_AFTER - 0.01)
    assert v.visible
    v.tick(5 + HIDE_AFTER)
    assert not v.visible and v.prompt == v.answer == ""


def test_it_fades_over_the_last_part_before_hiding():
    v = View()
    v.apply({"event": "answer", "text": "Klaar."}, 0)
    assert v.alpha(100) == ALPHA  # no idle yet: stays fully up however long Iris talks
    v.apply({"event": "idle"}, 0)
    assert v.alpha(1) == ALPHA
    assert 0 < v.alpha(HIDE_AFTER - 0.2) < ALPHA
    assert v.alpha(HIDE_AFTER + 1) == 0


def test_a_new_event_cancels_the_hide():
    v = View()
    v.apply({"event": "answer", "text": "Klaar."}, 0)
    v.apply({"event": "idle"}, 0)
    v.apply({"event": "listening"}, 1)  # a follow-up question
    v.tick(100)
    assert v.visible and v.mode == "listening" and v.answer == ""


def test_idle_while_hidden_stays_hidden():
    v = View()
    v.apply({"event": "idle"}, 0)
    assert not v.visible and v.hide_at is None


def test_nothing_heard_says_so():
    v = View()
    v.apply({"event": "heard", "text": ""}, 0)
    assert v.lines()[0] == "You: (nothing heard)"


def test_interrupted_listens_again_without_the_old_answer():
    v = View()
    v.apply({"event": "heard", "text": "vertel een verhaal"}, 0)
    v.apply({"event": "answer", "text": "Er was eens..."}, 1)
    v.apply({"event": "interrupted"}, 2)
    assert v.mode == "listening" and v.lines() == ("Listening...", "")


def test_an_answer_without_a_question_has_no_you_line():
    v = View()
    v.apply({"event": "answer", "text": "Your timer is done."}, 0)  # a timer or reminder speaking up
    assert v.lines() == ("", "Your timer is done.")


def test_unknown_events_change_nothing():
    v = View()
    v.apply({"event": "listening"}, 0)
    v.apply({"event": "explode"}, 1)
    assert v.mode == "listening"


@pytest.mark.parametrize("mode, n", [("listening", 1), ("thinking", 3), ("speaking", 3), ("done", 3)])
def test_the_indicator_has_a_shape_per_mode(mode, n):
    a, b = dots(mode, 0.0), dots(mode, 0.3)
    assert len(a) == n and all(r > 0 for _, _, r in a + b)
    if mode != "done":
        assert a != b  # it moves


# --- the pipe from the voice loop --------------------------------------------------------------------------------


def test_an_overlay_without_a_process_sends_nothing():
    o = Overlay()
    o.send("heard", "hallo")
    o.close()


def test_events_reach_the_process_as_ascii_json_lines():
    proc = types.SimpleNamespace(stdin=io.StringIO())
    o = Overlay(proc)  # type: ignore[arg-type]
    o.send("heard", "café \U0001f600")
    o.send("idle")
    deadline = time.monotonic() + 2
    while proc.stdin.getvalue().count("\n") < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    lines = proc.stdin.getvalue().splitlines()
    assert all(line.isascii() for line in lines)
    assert [json.loads(line) for line in lines] == [{"event": "heard", "text": "café \U0001f600"},
                                                   {"event": "idle", "text": ""}]


def test_a_dead_overlay_process_never_breaks_sending():
    proc = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.PIPE, text=True, encoding="ascii")
    proc.wait(timeout=30)
    o = Overlay(proc)
    for _ in range(500):  # more than the queue holds: the extra ones are dropped, never waited for
        o.send("answer", "x" * 100)
    deadline = time.monotonic() + 5
    while o._proc is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert o._proc is None  # the writer saw the broken pipe and stopped
    o.send("idle")
    o.close()


def test_a_stuck_overlay_process_never_blocks_sending_and_is_killed_on_close():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.PIPE,
                            text=True, encoding="ascii")
    o = Overlay(proc)
    t0 = time.monotonic()
    for _ in range(5000):  # far more than a pipe buffer: the writer blocks, send() must not
        o.send("answer", "x" * 200)
    assert time.monotonic() - t0 < 1
    o.close()
    assert proc.poll() is not None


def test_close_kills_a_process_that_will_not_stop():
    proc = mock.MagicMock()
    proc.wait.side_effect = subprocess.TimeoutExpired("overlay", 2)
    o = Overlay(proc)
    o.close()
    proc.kill.assert_called_once()
    o.close()  # twice is fine


def test_launch_is_a_silent_overlay_when_switched_off(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", mock.Mock(side_effect=AssertionError("must not start")))
    assert overlay.launch()._proc is None


def test_launch_starts_python_m_iris_overlay(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_OVERLAY", "1")
    started = {}

    def fake_popen(args, **kw):
        started.update(args=args, **kw)
        return types.SimpleNamespace(stdin=io.StringIO())

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    o = overlay.launch()
    assert o._proc is not None
    assert started["args"] == [sys.executable, "-m", "iris.overlay"]
    assert started["stdin"] == subprocess.PIPE
    assert (tmp_path / "overlay.log").exists()  # next to config.toml, for when it fails on someone's PC


def test_launch_carries_on_when_the_process_cannot_start(monkeypatch, capsys):
    monkeypatch.setenv("IRIS_OVERLAY", "1")
    monkeypatch.setattr(subprocess, "Popen", mock.Mock(side_effect=OSError("no python")))
    o = overlay.launch()
    assert o._proc is None
    o.send("listening")
    assert "overlay did not start" in capsys.readouterr().out


# --- what the voice loop sends -----------------------------------------------------------------------------------


class _Stream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_voice_session_starts_and_stops_the_overlay(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=_Stream))
    rec = Recorder()
    monkeypatch.setattr("iris.voice.launch", lambda: rec)
    s = VoiceSession(speaker=_Speaker())
    with s:
        assert s.overlay is rec
    assert rec.closed


def _session(text, **kw):
    # -1, not 0: Windows' clock ticks every ~16 ms, so after one frame no time may have passed and 0 > 0 waits forever
    s = VoiceSession(speaker=_Speaker(), max_record_seconds=-1, **kw)
    s.overlay = Recorder()
    s._transcribe = lambda audio: text  # type: ignore[method-assign]
    s._drain = lambda: None  # type: ignore[method-assign]  # keep the frames a test puts in
    s.wake = _Wake(False)
    return s


def test_a_request_sends_listening_then_what_was_heard():
    s = _session("Hey Jarvis, hoe laat is het?")
    s._frames.put(np.zeros(FRAME, dtype="float32"))
    assert s.next_command() == "hoe laat is het?"
    assert s.overlay.sent == ["listening", ("heard", "hoe laat is het?")]


def test_nothing_heard_lets_the_panel_go():
    s = _session("")
    s._frames.put(np.zeros(FRAME, dtype="float32"))
    s.next_command()
    assert s.overlay.sent == ["listening", "heard", "idle"]


def test_a_follow_up_question_sends_listening(monkeypatch):
    monkeypatch.setenv("IRIS_FOLLOW_UP", "2")
    s = _session("en morgen?")

    def loud_frames():
        while True:
            yield np.full(FRAME, 0.2, dtype="float32")

    frames = loud_frames()
    s._frames.get = lambda timeout=None: next(frames)  # type: ignore[method-assign]
    assert s.follow_up() == "en morgen?"
    assert s.overlay.sent == ["listening", ("heard", "en morgen?")]


def test_an_answer_shows_then_idles_when_spoken():
    s = _session("")
    s.speak("Het is tien over vier.")
    assert s.overlay.sent == [("answer", "Het is tien over vier."), "idle"]


def test_an_interrupted_answer_does_not_idle():
    s = _session("")
    s.speaker = _Speaker(finished=False)
    s.wake = _Wake(True)
    s._frames.put(np.zeros(FRAME, dtype="float32"))

    def speak(text, stop_event=None):
        stop_event.wait(2.0)  # talk until the wake word cuts in
        return False

    s.speaker.speak = speak  # type: ignore[method-assign]
    assert s.speak("Er was eens...") is True
    assert s.overlay.sent == [("answer", "Er was eens..."), "interrupted"]


def test_voice_mode_carries_on_with_a_dead_overlay():
    """The point of the separate process: whatever happened to it, the voice loop hears and answers as before."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.PIPE, text=True, encoding="ascii")
    proc.wait(timeout=30)
    s = _session("hoe laat is het")
    s.overlay = Overlay(proc)
    for _ in range(3):
        s._frames.put(np.zeros(FRAME, dtype="float32"))
        assert s.next_command() == "hoe laat is het"
        assert s.speak("Tien over vier.") is False
    assert s.speaker.said == ["Tien over vier."] * 3
    s.overlay.close()


# --- the window tools skip it ------------------------------------------------------------------------------------


class _Win:
    def __init__(self, title):
        self.title = title

    def window_text(self):
        return self.title


def test_window_tools_never_see_the_overlay(monkeypatch):
    wins = [_Win(overlay.TITLE), _Win("Kladblok"), _Win("Iris overlay notes.txt - Kladblok")]
    monkeypatch.setattr(tools, "_desktop", lambda: types.SimpleNamespace(windows=lambda: wins))
    assert tools.list_windows() == "Kladblok\nIris overlay notes.txt - Kladblok"
    assert tools._window("iris overlay") is wins[2]  # a file named like it is still found


# --- the overlay process -----------------------------------------------------------------------------------------


def test_read_events_passes_known_events_and_ends_with_none():
    out: queue.Queue = queue.Queue()
    overlay.read_events(io.StringIO('{"event": "listening"}\ngarbage\n{"event": "idle"}\n'), out)
    assert [out.get_nowait() for _ in range(3)] == [{"event": "listening"}, {"event": "idle"}, None]


def test_read_events_ends_on_a_broken_stream():
    class Broken:
        def __iter__(self):
            raise OSError("pipe gone")

    out: queue.Queue = queue.Queue()
    overlay.read_events(Broken(), out)  # type: ignore[arg-type]
    assert out.get_nowait() is None


class User32:
    """Records the Windows calls; GetWindowLongW says the window has a taskbar button."""

    def __init__(self, area_ok=1):
        self.calls = []
        self.area_ok = area_ok

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, *args))
            return {"GetWindowLongW": Win32.WS_EX_APPWINDOW, "GetForegroundWindow": 77,
                    "SystemParametersInfoW": self.area_ok}.get(name, 1)
        return call

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


def test_win32_keeps_the_window_out_of_the_way():
    u = User32()
    w = Win32(u, 42)
    w.no_focus()
    (_, hwnd, index, style), = u.named("SetWindowLongW")
    assert hwnd == 42 and index == Win32.GWL_EXSTYLE
    assert not style & Win32.WS_EX_APPWINDOW  # no taskbar button
    for flag in (Win32.WS_EX_NOACTIVATE, Win32.WS_EX_TOOLWINDOW, Win32.WS_EX_LAYERED):
        assert style & flag
    assert not style & Win32.WS_EX_TRANSPARENT  # the cancel button takes clicks; the see-through corners pass them on
    assert u.named("SetWindowDisplayAffinity") == [("SetWindowDisplayAffinity", 42, 0x11)]
    w.show()
    w.hide()
    assert [c[2] for c in u.named("ShowWindow")] == [Win32.SW_SHOWNOACTIVATE, Win32.SW_HIDE]
    assert u.named("SetWindowPos")[0][7] & 0x10  # SWP_NOACTIVATE
    assert w.work_area() == (0, 0, 0, 0)
    assert Win32(User32(area_ok=0), 42).work_area() is None


def test_user32_is_there_only_on_windows():
    assert (overlay._user32() is None) == (sys.platform != "win32")


def test_user32_is_none_when_windll_is_missing(monkeypatch):
    monkeypatch.setattr("ctypes.windll", None)  # not Windows
    assert overlay._user32() is None


def _fake_tk():
    tk = mock.MagicMock()
    tk.TclError = RuntimeError
    root = tk.Tk.return_value
    root.winfo_fpixels.return_value = 96.0
    root.winfo_screenwidth.return_value = 1920
    root.winfo_screenheight.return_value = 1080
    root.wm_frame.return_value = "0x2a"
    tk.Canvas.return_value.bbox.return_value = (0, 0, 300, 40)
    return tk


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_panel_sets_up_a_window_that_never_takes_focus():
    tk, u = _fake_tk(), User32()
    p = Panel(tk, queue.Queue(), user32=u)
    root = tk.Tk.return_value
    root.title.assert_called_with(overlay.TITLE)
    root.overrideredirect.assert_called_with(True)
    assert p.win is not None and p.win.hwnd == 0x2a
    assert u.named("SetWindowDisplayAffinity") and u.named("SetProcessDPIAware")
    assert u.named("SetForegroundWindow") == [("SetForegroundWindow", 77)]  # the focus goes back where it was


def test_panel_shows_a_turn_and_hides_after_idle():
    tk, u, clock = _fake_tk(), User32(), Clock()
    events: queue.Queue = queue.Queue()
    p = Panel(tk, events, clock=clock, user32=u)
    root, canvas = tk.Tk.return_value, tk.Canvas.return_value
    u.calls.clear()
    p.step()
    assert not p.shown and not u.named("ShowWindow")  # nothing to show yet

    events.put({"event": "heard", "text": "Hoe laat is het?"})
    events.put({"event": "answer", "text": "Tien over vier."})
    p.step()
    assert p.shown and u.named("ShowWindow")[-1][2] == Win32.SW_SHOWNOACTIVATE
    texts = [c.kwargs["text"] for c in canvas.create_text.call_args_list]
    assert texts == ["You: Hoe laat is het?", "Tien over vier."]
    assert root.geometry.call_args.args[0].startswith(f"{p.width}x")
    assert canvas.create_rectangle.called  # the speaking bars
    root.attributes.assert_called_with("-alpha", ALPHA)

    events.put({"event": "idle"})
    p.step()
    clock.now = HIDE_AFTER - 0.2
    p.step()
    assert 0 < root.attributes.call_args.args[1] < ALPHA  # fading
    clock.now = HIDE_AFTER + 0.1
    p.step()
    assert not p.shown and u.named("ShowWindow")[-1][2] == Win32.SW_HIDE

    events.put(None)  # Iris closed the pipe
    p.step()
    root.destroy.assert_called_once()


def test_panel_without_win32_uses_tk_to_show_and_hide():
    tk, clock = _fake_tk(), Clock()

    def attributes(name, value):
        if name == "-transparentcolor":  # not on this platform
            raise RuntimeError(name)

    tk.Tk.return_value.attributes.side_effect = attributes
    events: queue.Queue = queue.Queue()
    p = Panel(tk, events, clock=clock)
    root = tk.Tk.return_value
    assert p.win is None
    events.put({"event": "listening"})
    p.step()
    root.deiconify.assert_called()
    assert tk.Canvas.return_value.create_oval.called  # the listening dot
    events.put({"event": "idle"})
    p.step()
    clock.now = 10
    p.step()
    assert not p.shown
    root.withdraw.assert_called()


def test_panel_replaces_characters_an_old_tk_cannot_draw():
    tk = _fake_tk()
    canvas = tk.Canvas.return_value
    canvas.create_text.side_effect = [RuntimeError("character U+1f600 is above the range"), 1, 2]
    events: queue.Queue = queue.Queue()
    p = Panel(tk, events, user32=User32())
    events.put({"event": "heard", "text": "lach \U0001f600"})
    p.step()
    assert canvas.create_text.call_args_list[1].kwargs["text"] == "You: lach ?"


def test_panel_survives_a_failing_dpi_call():
    u = User32()
    u.SetProcessDPIAware = mock.Mock(side_effect=OSError)
    Panel(_fake_tk(), queue.Queue(), user32=u)


def test_main_runs_the_panel_until_stdin_ends():
    tk = _fake_tk()
    root = tk.Tk.return_value
    root.mainloop.side_effect = lambda: None
    assert overlay.main(io.StringIO('{"event": "listening"}\n'), tk=tk) == 0
    root.after.assert_called()
    root.mainloop.assert_called_once()


def test_main_says_so_without_tkinter(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "tkinter", None)
    assert overlay.main(io.StringIO("")) == 1
    assert "no tkinter" in capsys.readouterr().err


def test_main_imports_tkinter_and_asks_for_user32_when_given_no_fakes(monkeypatch):
    seen = {}

    def panel(tk, events, user32=None, out=None):
        seen["tk"], seen["user32"] = tk, user32
        return types.SimpleNamespace(run=lambda: None)

    monkeypatch.setattr(overlay, "Panel", panel)  # the Panel is a fake, so no window opens
    fake_tk = types.ModuleType("tkinter")
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    assert overlay.main(io.StringIO("")) == 0
    assert seen["tk"] is fake_tk
    assert (seen["user32"] is None) == (sys.platform != "win32")


def test_running_the_module_as_a_script_exits_with_mains_code(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "tkinter", None)  # main() gives up before any window could open
    monkeypatch.delitem(sys.modules, "iris.overlay")  # run it fresh, as `python -m iris.overlay` does
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("iris.overlay", run_name="__main__")
    assert exc.value.code == 1
    assert "no tkinter" in capsys.readouterr().err


# --- the look: a coloured outline per state ----------------------------------------------------------------------


def _rgb(colour):
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def _far(a, b):
    """How far apart two colours are."""
    return sum(abs(x - y) for x, y in zip(_rgb(a), _rgb(b), strict=True))


TIMES = [i * 0.07 for i in range(120)]  # a bit over 8 s of frames


def test_blend_mixes_two_colours():
    assert blend("#000000", "#ffffff", 0) == "#000000"
    assert blend("#000000", "#ffffff", 1) == "#ffffff"
    assert blend("#000000", "#FFFFFF", 0.5) == "#808080"
    assert blend("#102030", "#ffffff", -1) == "#102030" and blend("#102030", "#ffffff", 2) == "#ffffff"
    mid = _rgb(blend(COLOURS["listening"], BG, 0.25))
    assert all(min(a, b) <= m <= max(a, b) for m, a, b in zip(mid, _rgb(COLOURS["listening"]), _rgb(BG), strict=True))


def test_the_outline_colour_fades_between_states_instead_of_jumping():
    t = Tint()
    assert t.at(0) == COLOURS["done"]  # a panel that appears grows into its colour
    t.toward(COLOURS["listening"], 1.0)
    assert t.at(1.0) == COLOURS["done"]
    halfway = t.at(1.0 + COLOUR_FADE / 2)
    assert halfway not in (COLOURS["done"], COLOURS["listening"])
    assert t.at(1.0 + COLOUR_FADE) == t.at(9.0) == COLOURS["listening"]
    t.toward(COLOURS["listening"], 1.1)  # the same colour again does not start over
    assert t.at(1.0 + COLOUR_FADE) == COLOURS["listening"]

    t = Tint()
    t.toward(COLOURS["thinking"], 0.0)
    now = COLOUR_FADE / 2
    before = t.at(now)
    t.toward(COLOURS["speaking"], now)  # a new state in the middle of a fade
    assert t.at(now) == before  # carries on from where it was
    assert t.at(now + COLOUR_FADE) == COLOURS["speaking"]


@pytest.mark.parametrize("mode", ["listening", "thinking", "speaking", "done", "hidden"])
def test_the_outline_pulses_between_0_and_1(mode):
    values = {round(pulse(mode, t), 4) for t in TIMES}
    assert all(0 <= v <= 1 for v in values)
    assert (len(values) > 10) == (mode in ("listening", "thinking", "speaking"))  # calm once Iris is done


def test_the_outline_beats_with_the_speaking_bars():
    def bars(t):
        return sum(r for _, _, r in dots("speaking", t))

    loud, quiet = max(TIMES, key=bars), min(TIMES, key=bars)
    assert pulse("speaking", loud) > pulse("speaking", quiet) + 0.1


@pytest.mark.parametrize("mode", ["listening", "thinking", "speaking", "done"])
def test_the_glow_fades_away_from_the_edge_and_stays_in_the_margin(mode):
    base = COLOURS[mode]
    for t in TIMES:
        outer, inner = glow(base, mode, t)
        assert len(outer) == len(OUTER) and len(inner) == len(INNER)
        colours = [c for c, _ in outer] + inner
        assert KEY not in colours  # a band in the see-through colour would be a hole in the glow
        assert all(c.startswith("#") and len(c) == 7 for c in colours)
        # outermost (or deepest) first: each band is closer to the outline's colour than the one before it
        for bands in ([c for c, _ in outer], inner):
            assert all(_far(a, base) > _far(b, base) for a, b in zip(bands, bands[1:], strict=False))
        assert all(0 < w <= full for (_, w), (full, _) in zip(outer, OUTER, strict=True))
    assert Panel.MARGIN >= max(w for w, _ in OUTER) / 2  # the widest outer stroke still fits round the panel


def test_the_outline_is_one_colour_except_while_thinking():
    for mode in ("listening", "speaking", "done"):
        colours = edge(COLOURS[mode], mode, 1.0)
        assert len(colours) == EDGE_PIECES and len(set(colours)) == 1


def test_while_thinking_two_lights_travel_round_the_outline():
    def lights(t):
        colours = edge(COLOURS["thinking"], "thinking", t)
        return sorted(range(EDGE_PIECES), key=lambda i: _far(colours[i], BG))[-1]

    colours = edge(COLOURS["thinking"], "thinking", 0.0)
    brightness = [_far(c, BG) for c in colours]
    assert len(set(colours)) > 5
    first = brightness.index(max(brightness))
    opposite = (first + EDGE_PIECES // 2) % EDGE_PIECES
    assert brightness[opposite] > brightness[(first + EDGE_PIECES // 4) % EDGE_PIECES]  # a second light across
    assert lights(0.0) != lights(1.0)  # it moves


def test_rounded_rect_follows_the_edges():
    pts = rounded_rect(10, 20, 110, 70, 12)
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((10, 110, 20, 70))
    assert len(pts) == 4 * 9
    assert all(abs(math.dist(p, (98, 32)) - 12) < 1e-9 for p in pts[:9])  # the top-right corner is a real arc
    big = rounded_rect(0, 0, 40, 20, 100)  # a radius larger than the panel: a pill, not a mess
    assert all(0 - 1e-9 <= x <= 40 + 1e-9 and 0 - 1e-9 <= y <= 20 + 1e-9 for x, y in big)


def test_split_ring_cuts_the_outline_into_even_connected_pieces():
    ring = rounded_rect(0, 0, 500, 100, 20)
    pieces = split_ring(ring, 40, chords=5)
    assert len(pieces) == 40 and all(len(p) == 6 for p in pieces)
    assert all(a[-1] == b[0] for a, b in zip(pieces, pieces[1:], strict=False)) and pieces[-1][-1] == pieces[0][0]
    lengths = [sum(math.dist(a, b) for a, b in zip(p, p[1:], strict=False)) for p in pieces]
    assert max(lengths) - min(lengths) < 0.5  # about 30 px each


def _geometry(root):
    size, left, top = root.geometry.call_args.args[0].split("+")
    w, h = size.split("x")
    return int(w), int(h), int(left), int(top)


def test_panel_draws_the_glow_and_outline_in_the_see_through_margin():
    tk, events = _fake_tk(), queue.Queue()
    p = Panel(tk, events, clock=Clock())
    canvas, root = tk.Canvas.return_value, tk.Tk.return_value
    events.put({"event": "listening"})
    p.step()
    assert len(canvas.create_polygon.call_args_list) == len(OUTER) + 1 + len(INNER)  # the glow, the panel, the glow
    assert len([c for c in canvas.create_line.call_args_list if c.kwargs["tags"] == "edge"]) == EDGE_PIECES
    w, h, left, top = _geometry(root)
    m = p.margin
    assert w == p.width and h == p.height
    assert left == (1920 - w) // 2  # centred
    assert top + h - m == 1080 - 48 - 16  # the panel 16 px above the taskbar, its glow in the margin below it
    for call in canvas.create_polygon.call_args_list:
        xs, ys = call.args[0][0::2], call.args[0][1::2]
        assert m <= min(xs) and max(xs) <= w - m and m <= min(ys) and max(ys) <= h - m
    assert m >= max(width for width, _ in OUTER) * p.scale / 2  # the outer glow fits; the corners stay see-through
    canvas.tag_raise.assert_called_with("text")  # the text on top of the shapes
    assert {c.kwargs["tags"] for c in canvas.create_text.call_args_list} == {"text"}


def test_each_frame_touches_only_what_changed():
    tk, events, clock = _fake_tk(), queue.Queue(), Clock()
    p = Panel(tk, events, clock=clock)
    canvas = tk.Canvas.return_value
    events.put({"event": "listening"})
    p.step()
    assert canvas.itemconfigure.called
    canvas.reset_mock()
    clock.now = 0.6
    p.step()
    assert not canvas.create_text.called and not canvas.create_polygon.called  # the text and shapes stay
    assert [c.args for c in canvas.delete.call_args_list] == [("dots",)]
    assert canvas.itemconfigure.called  # the colours moved on
    canvas.reset_mock()
    p.step()  # the same moment again: nothing to repaint but the indicator
    assert not canvas.itemconfigure.called


def test_the_outline_is_coloured_per_piece_while_thinking_and_at_once_otherwise():
    tk, events, clock = _fake_tk(), queue.Queue(), Clock()
    p = Panel(tk, events, clock=clock)
    canvas = tk.Canvas.return_value
    events.put({"event": "listening"})
    p.step()
    assert mock.call("edge", fill=mock.ANY) in canvas.itemconfigure.call_args_list
    events.put({"event": "heard", "text": "hoe laat is het"})
    clock.now = 1.0
    p.step()
    pieces = [c for c in canvas.itemconfigure.call_args_list if c.args[0] != "edge" and "fill" in c.kwargs]
    assert len(pieces) >= EDGE_PIECES // 2
    canvas.reset_mock()
    events.put({"event": "answer", "text": "tien over vier"})
    clock.now = 2.0
    p.step()
    clock.now = 2.0 + COLOUR_FADE
    p.step()
    last = [c for c in canvas.itemconfigure.call_args_list if c.args[0] == "edge"][-1]
    assert last.kwargs["fill"] == edge(COLOURS["speaking"], "speaking", clock.now)[0]  # green, all round


def test_the_indicator_sits_level_with_the_first_line_in_the_outline_colour():
    tk, events, clock = _fake_tk(), queue.Queue(), Clock()
    canvas = tk.Canvas.return_value
    canvas.bbox.return_value = (64, 28, 300, 46)
    p = Panel(tk, events, clock=clock)
    events.put({"event": "listening"})
    clock.now = 5.0
    p.step()
    (_, y0, x1, y1), kw = canvas.create_oval.call_args.args, canvas.create_oval.call_args.kwargs
    assert (y0 + y1) / 2 == pytest.approx(37)  # the middle of the first line
    assert x1 < p.margin + Panel.INDENT * p.scale  # left of the text
    assert kw["fill"] == p.tint.at(5.0)


def test_the_indicator_is_centred_when_there_is_no_text():
    tk, events = _fake_tk(), queue.Queue()
    canvas = tk.Canvas.return_value
    p = Panel(tk, events, clock=Clock())
    events.put({"event": "answer", "text": ""})  # nothing to write: only the speaking bars
    p.step()
    assert not canvas.create_text.called
    _, y0, _, y1 = canvas.create_rectangle.call_args.args
    assert (y0 + y1) / 2 == pytest.approx(p.height / 2)


# --- the cancel button -------------------------------------------------------------------------------------------


def _binding(canvas, sequence):
    """The handler Panel bound to the cancel button's tag for one event sequence."""
    return next(c.args[2] for c in canvas.tag_bind.call_args_list if c.args[:2] == ("cancel", sequence))


def _button_items(canvas):
    return [c for c in canvas.create_oval.call_args_list + canvas.create_line.call_args_list
            if "cancel" in c.kwargs["tags"]]


@pytest.mark.parametrize("event, shown", [("listening", True), ("thinking", True), ("answer", True), ("idle", False)])
def test_the_cancel_button_shows_only_while_there_is_a_request(event, shown):
    tk, events = _fake_tk(), queue.Queue()
    p = Panel(tk, events, clock=Clock())
    canvas = tk.Canvas.return_value
    events.put({"event": "heard", "text": "hoe laat is het"})
    p.step()
    canvas.reset_mock()
    events.put({"event": event, "text": "Tien over vier."})
    p.step()
    items = _button_items(canvas)
    assert (len(items) == 3) is shown and (not items) is (not shown)  # a ring and the two lines of its X


def test_the_cancel_button_sits_right_level_with_the_first_line_and_clear_of_the_text():
    tk, events = _fake_tk(), queue.Queue()
    canvas = tk.Canvas.return_value
    canvas.bbox.return_value = (64, 28, 300, 46)
    p = Panel(tk, events, clock=Clock())
    events.put({"event": "heard", "text": "hoe laat is het"})
    p.step()
    ring = next(c for c in canvas.create_oval.call_args_list if "cancel_ring" in c.kwargs["tags"])
    x0, y0, x1, y1 = ring.args
    assert (y0 + y1) / 2 == pytest.approx(37)  # level with the first line, like the indicator
    assert (x0 + x1) / 2 == pytest.approx(p.width - p.anchor[0])  # mirrors the indicator
    text = canvas.create_text.call_args_list[0]
    assert text.args[0] + text.kwargs["width"] < x0  # the text wraps before it reaches the button
    assert x1 < p.width - p.margin  # on the panel, not in the see-through margin


def test_a_click_on_the_cancel_button_tells_iris_and_lets_the_panel_go():
    tk, events, out, clock = _fake_tk(), queue.Queue(), io.StringIO(), Clock()
    p = Panel(tk, events, clock=clock, out=out)
    canvas = tk.Canvas.return_value
    click = _binding(canvas, "<Button-1>")
    events.put({"event": "answer", "text": "Er was eens..."})
    p.step()
    canvas.reset_mock()
    click(None)
    assert out.getvalue() == CANCEL and json.loads(out.getvalue()) == {"event": "cancel"}
    assert p.view.mode == "done" and not _button_items(canvas)  # redrawn without the button, fading out
    click(None)  # a second click on a panel that is going: nothing more to cancel
    assert out.getvalue() == CANCEL
    clock.now = HIDE_AFTER + 0.1
    p.step()
    assert not p.shown


def test_a_click_with_iris_gone_still_lets_the_panel_go():
    class Broken(io.StringIO):
        def write(self, text):
            raise OSError("pipe gone")

    tk, events = _fake_tk(), queue.Queue()
    p = Panel(tk, events, clock=Clock(), out=Broken())
    events.put({"event": "listening"})
    p.step()
    p.cancel()
    assert p.view.mode == "done"
    Panel(tk, events, clock=Clock()).cancel()  # no pipe at all (pythonw): nothing to write, no error


def test_the_cancel_button_lights_up_in_the_state_colour_under_the_mouse():
    tk, events, clock = _fake_tk(), queue.Queue(), Clock()
    p = Panel(tk, events, clock=clock)
    canvas = tk.Canvas.return_value
    enter, leave = _binding(canvas, "<Enter>"), _binding(canvas, "<Leave>")
    events.put({"event": "listening"})
    p.step()
    canvas.reset_mock()
    clock.now = 5.0
    enter(None)
    ring = next(c for c in canvas.itemconfigure.call_args_list if c.args == ("cancel_ring",))
    assert ring.kwargs["outline"] == p.tint.at(5.0)
    canvas.configure.assert_called_with(cursor="hand2")
    enter(None)  # already lit: nothing to repaint
    assert len(canvas.itemconfigure.call_args_list) == 2
    events.put({"event": "heard", "text": "hoe laat is het"})
    p.step()  # drawn anew under the mouse: still lit
    ring = next(c for c in canvas.create_oval.call_args_list if "cancel_ring" in c.kwargs["tags"])
    assert ring.kwargs["outline"] == p.tint.at(5.0)
    leave(None)
    canvas.configure.assert_called_with(cursor="")
    assert not p.hover


def test_the_cancel_line_is_recognised():
    assert overlay.is_cancel(CANCEL) and overlay.is_cancel('{"event": "cancel"}')
    assert not overlay.is_cancel("garbage") and not overlay.is_cancel('{"event": "idle"}')
    assert not overlay.is_cancel('["cancel"]')


def test_the_overlay_calls_back_on_each_cancel_line():
    got, two = [], threading.Event()

    def on_cancel():
        got.append(1)
        if len(got) == 1:
            raise RuntimeError("a bug in the callback")  # printed; the next cancel still arrives
        two.set()

    back = io.StringIO(CANCEL + "garbage\n" + '{"event": "x"}\n' + CANCEL)
    proc = types.SimpleNamespace(stdin=io.StringIO(), stdout=back)
    Overlay(proc, on_cancel=on_cancel)  # type: ignore[arg-type]
    assert two.wait(5)
    assert len(got) == 2


def test_a_cancel_from_a_real_overlay_process_reaches_the_voice_loop():
    script = f"import sys; sys.stdout.write({CANCEL!r}); sys.stdout.flush(); sys.stdin.read()"
    proc = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            encoding="ascii")
    clicked = threading.Event()
    o = Overlay(proc)
    o.on_cancel = clicked.set  # set after the start, as VoiceSession does
    assert clicked.wait(30)
    o.close()
    assert proc.poll() is not None


def test_cancel_lines_without_a_callback_go_nowhere():
    proc = types.SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(CANCEL))
    Overlay(proc)  # type: ignore[arg-type]  # nothing to call: read and ignored


def test_reading_cancel_lines_survives_a_broken_pipe():
    class Broken:
        def __iter__(self):
            raise OSError("pipe gone")

    o = Overlay()
    o._read(Broken())  # type: ignore[arg-type]  # the pipe broke; nothing to do


def test_launch_gives_the_overlay_a_pipe_back(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_OVERLAY", "1")
    started = {}

    def fake_popen(args, **kw):
        started.update(kw)
        return types.SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO())

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    overlay.launch()
    assert started["stdout"] == subprocess.PIPE


def test_win32_keeps_a_click_from_activating_the_panel():
    u = User32()
    w = Win32(u, 42)
    w.no_activate_on_click()
    (_, hwnd, index, _proc), = u.named("SetWindowLongPtrW")
    assert hwnd == 42 and index == Win32.GWLP_WNDPROC
    assert w._wndproc(42, Win32.WM_MOUSEACTIVATE, 0, 0) == Win32.MA_NOACTIVATE  # the click lands, no activation
    assert not u.named("CallWindowProcW")
    w._wndproc(42, 0x0201, 0, 0)  # WM_LBUTTONDOWN and everything else: on to Tk's own window procedure
    assert u.named("CallWindowProcW")[0][1:3] == (1, 42)  # through the procedure it replaced


def test_a_panel_without_the_click_hook_still_starts():
    u = User32()
    u.SetWindowLongPtrW = mock.Mock(side_effect=OSError("no hook"))
    p = Panel(_fake_tk(), queue.Queue(), user32=u)
    assert p.win is not None and u.named("SetWindowDisplayAffinity")


def test_the_panel_hooks_the_click_on_windows():
    u = User32()
    Panel(_fake_tk(), queue.Queue(), user32=u)
    assert u.named("SetWindowLongPtrW")


def test_main_writes_cancel_to_stdout(monkeypatch):
    tk = _fake_tk()
    tk.Tk.return_value.mainloop.side_effect = lambda: None
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    made = []
    real = overlay.Panel

    def panel(*args, **kw):
        made.append(real(*args, **kw))
        return made[-1]

    monkeypatch.setattr(overlay, "Panel", panel)
    assert overlay.main(io.StringIO(""), tk=tk) == 0
    assert made[0].out is out


# --- what the voice loop does on a cancel --------------------------------------------------------------------------


def test_the_voice_session_listens_to_the_overlays_cancel(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=_Stream))
    o = Overlay()
    monkeypatch.setattr("iris.voice.launch", lambda: o)
    s = VoiceSession(speaker=_Speaker())
    with s:
        assert o.on_cancel == s.cancel


def test_a_cancel_while_speaking_stops_the_speech_at_once():
    s = _session("")
    started = threading.Event()

    def speak(text, stop_event=None):
        started.set()
        return not stop_event.wait(30)  # a long answer, until something stops it

    s.speaker.speak = speak  # type: ignore[method-assign]
    s.speak_async("Er was eens een heel lang verhaal...")
    assert started.wait(5)
    t0 = time.perf_counter()
    s.cancel()
    assert s.wait_for_speech() is True  # cut short
    assert time.perf_counter() - t0 < 10  # stopped, not played out
    assert s.cancelled.is_set() and not s._answered  # back to the wake word: no follow-up window
    assert s.overlay.sent == [("answer", "Er was eens een heel lang verhaal..."), "idle"]  # idle once, from the cancel


def test_a_cancel_while_listening_ends_the_recording_without_transcribing():
    s = _session("never")
    s._transcribe = mock.Mock(side_effect=AssertionError("must not transcribe"))  # type: ignore[method-assign]

    def click_then_frame(timeout=None):
        s.cancel()  # the click lands while Iris records
        return np.zeros(FRAME, dtype="float32")

    s._frames.get = click_then_frame  # type: ignore[method-assign]
    assert s.next_command() == ""
    assert s.overlay.sent == ["listening", "idle"]  # no "heard": the panel is going


def test_a_cancelled_confirm_question_waits_for_no_answer():
    s = _session("ja")
    started = threading.Event()

    def speak(text, stop_event=None):
        started.set()
        return not stop_event.wait(30)

    s.speaker.speak = speak  # type: ignore[method-assign]
    s.speak_async("Zal ik het bestand weggooien?")
    assert started.wait(5)
    s.cancel()
    s.wait_for_speech()
    assert s.next_command() == ""  # the agent hears no yes, and stops before its next step
    assert s.overlay.sent == [("answer", "Zal ik het bestand weggooien?"), "idle"]  # not listening again


def test_a_cancel_while_whisper_works_drops_the_text():
    s = _session("")

    def transcribe(audio):
        s.cancel()
        return "hoe laat is het"

    s._transcribe = transcribe  # type: ignore[method-assign]
    s._frames.put(np.zeros(FRAME, dtype="float32"))
    assert s.next_command() == ""
    assert "heard" not in s.overlay.sent and ("heard", "hoe laat is het") not in s.overlay.sent


def test_the_next_request_is_not_cancelled_by_an_old_click(monkeypatch):
    monkeypatch.setenv("IRIS_CHIME", "0")
    s = _session("hoe laat is het")
    s.wake = _Wake(True)
    s.cancel()  # the last request was cancelled
    s._frames.put(np.zeros(FRAME, dtype="float32"))
    s._frames.put(np.zeros(FRAME, dtype="float32"))
    assert s.next_request() == "hoe laat is het"

    class ClickWhileWaiting:
        def detect(self, frame):
            s.cancel()  # a late click that lands while Iris waits for the wake word
            return True

    s.wake = ClickWhileWaiting()
    for _ in range(3):
        s._frames.put(np.zeros(FRAME, dtype="float32"))
    assert s.next_request() == "hoe laat is het"


def test_voice_mode_carries_on_when_the_overlay_pipe_back_is_dead():
    proc = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            encoding="ascii")
    proc.wait(timeout=30)
    s = _session("hoe laat is het")
    s.overlay = Overlay(proc, on_cancel=s.cancel)
    for _ in range(3):
        s._frames.put(np.zeros(FRAME, dtype="float32"))
        assert s.next_command() == "hoe laat is het"
        assert s.speak("Tien over vier.") is False
    assert s.speaker.said == ["Tien over vier."] * 3 and not s.cancelled.is_set()
    s.overlay.close()
