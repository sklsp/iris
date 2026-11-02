"""The voice overlay: its event and state logic, the pipe from the voice loop, and the thin Tk part with a fake tkinter.
No window opens: Panel gets a MagicMock for tkinter and a recorder for user32."""

import io
import json
import queue
import subprocess
import sys
import time
import types
from unittest import mock

import numpy as np
import pytest

from iris import overlay, tools
from iris.overlay import ALPHA, HIDE_AFTER, Overlay, Panel, View, Win32, dots, parse, tail
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
    s = VoiceSession(speaker=_Speaker(), max_record_seconds=0, **kw)
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
    for flag in (Win32.WS_EX_NOACTIVATE, Win32.WS_EX_TOOLWINDOW, Win32.WS_EX_TRANSPARENT, Win32.WS_EX_LAYERED):
        assert style & flag
    assert u.named("SetWindowDisplayAffinity") == [("SetWindowDisplayAffinity", 42, 0x11)]
    w.show()
    w.hide()
    assert [c[2] for c in u.named("ShowWindow")] == [Win32.SW_SHOWNOACTIVATE, Win32.SW_HIDE]
    assert u.named("SetWindowPos")[0][7] & 0x10  # SWP_NOACTIVATE
    assert w.work_area() == (0, 0, 0, 0)
    assert Win32(User32(area_ok=0), 42).work_area() is None


def test_user32_is_there_only_on_windows():
    assert (overlay._user32() is None) == (sys.platform != "win32")


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
