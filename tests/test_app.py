"""iris.app: the desktop app. Mutexes, stop events and Job Objects are real (CI runs on Windows); pywebview and the
WinForms tray are fakes, so nothing opens a window or a tray icon."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from iris import app


def unique(prefix: str) -> str:
    return f"Local\\Iris-Test-{prefix}-{uuid.uuid4().hex}"


# --- Windows primitives ---------------------------------------------------------------------------------------------

def test_mutex_is_taken_once_and_seen_by_others():
    name = unique("mutex")
    assert not app.mutex_exists(name)
    handle = app.take_mutex(name)
    assert handle
    assert app.mutex_exists(name)
    assert app.take_mutex(name) is None  # the second taker learns it is already held


def test_stop_event_wakes_the_supervised_child(monkeypatch):
    calls = []
    monkeypatch.setattr("_thread.interrupt_main", lambda: calls.append("interrupted"))
    event = app.StopEvent()
    t = threading.Thread(target=app.quit_when_set, args=(event.name,), daemon=True)
    t.start()
    time.sleep(0.05)
    assert calls == []  # waits until the app asks
    event.set()
    t.join(5)
    assert calls == ["interrupted"]
    event.close()


def test_quit_when_set_without_an_app_returns_at_once(monkeypatch):
    monkeypatch.setattr("_thread.interrupt_main", lambda: pytest.fail("must not interrupt"))
    app.quit_when_set(unique("missing"))
    app.quit_when_set("")  # IRIS_STOP_EVENT unset


def test_job_ends_the_process_in_it():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                            creationflags=subprocess.CREATE_NO_WINDOW)
    job = app.Job()
    try:
        job.add(proc)
        job.kill()
        assert proc.wait(10) == 1
    finally:
        job.close()
        if proc.poll() is None:
            proc.kill()


def test_show_running_finds_nothing_for_an_unknown_title():
    assert app.show_running(f"Iris-test-{uuid.uuid4().hex}") is False


def test_app_id_groups_the_window_under_iris():
    app._set_app_id()  # only labels this test process for the taskbar
    buf = ctypes.c_wchar_p()
    ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(buf))
    assert buf.value == app.APP_ID


# --- the voice loop -------------------------------------------------------------------------------------------------

class FakeProc:
    def __init__(self):
        self.done = threading.Event()
        self.code = 0
        self.killed = False

    def wait(self):
        self.done.wait()
        return self.code

    def kill(self):
        self.killed = True
        self.code = 1
        self.done.set()


class FakeJob:
    def __init__(self):
        self.added, self.killed, self.closed = [], False, False

    def add(self, proc):
        self.added.append(proc)

    def kill(self):
        self.killed = True

    def close(self):
        self.closed = True


class FakeEvent:
    def __init__(self, proc_holder):
        self.name = "Local\\Iris-Stop-test"
        self.holder = proc_holder
        self.closed = False

    def set(self):  # the child quits cleanly when asked
        if self.holder.get("obeys", True):
            self.holder["proc"].done.set()

    def close(self):
        self.closed = True


def make_loop(holder: dict, on_exit=None, mutex: str | None = None) -> app.VoiceLoop:
    def popen(cmd, **kw):
        holder["cmd"], holder["kw"] = cmd, kw
        holder["proc"] = FakeProc()
        return holder["proc"]

    def job():
        holder["job"] = FakeJob()
        return holder["job"]

    def event():
        holder["event"] = FakeEvent(holder)
        return holder["event"]

    return app.VoiceLoop(on_exit=on_exit, popen=popen, job=job, event=event, voice_mutex=mutex or unique("voice"))


def test_voice_loop_starts_one_supervised_child_in_a_job():
    h: dict = {}
    loop = make_loop(h)
    assert loop.start() == "Iris is listening."
    assert loop.running
    assert h["cmd"][-3:] == ["iris", "--voice", "--supervised"]
    assert h["kw"]["stdin"] == subprocess.DEVNULL  # never a pipe: see quit_when_set
    assert h["kw"]["env"]["IRIS_STOP_EVENT"] == "Local\\Iris-Stop-test"
    assert h["kw"]["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert h["job"].added == [h["proc"]]
    first = h["proc"]
    assert loop.start() == "Iris is listening." and h["proc"] is first  # a second start is a no-op
    loop.stop()
    assert not loop.running and h["job"].killed and h["job"].closed and h["event"].closed
    assert (app.log_dir() / "voice.log").exists()


def test_voice_loop_stop_kills_a_child_that_does_not_quit():
    h: dict = {"obeys": False}
    loop = make_loop(h)
    loop.start()
    loop.stop(seconds=0.05)
    assert h["proc"].killed and not loop.running


def test_stop_without_start_does_nothing():
    make_loop({}).stop()


def test_voice_loop_reports_an_unexpected_exit_with_the_last_log_lines():
    h: dict = {}
    exits = []
    loop = make_loop(h, on_exit=lambda code, lines: exits.append((code, lines)))
    loop.start()
    app.log_dir().joinpath("voice.log").write_text("loading\nboom: no microphone\n", encoding="utf-8")
    h["proc"].code = 3
    h["proc"].done.set()
    loop._watcher.join(5)
    assert exits == [(3, "loading\nboom: no microphone")]
    assert not loop.running


def test_voice_loop_refuses_while_voice_mode_runs_in_a_terminal():
    name = unique("terminal")
    held = app.take_mutex(name)
    assert held
    h: dict = {}
    assert "already running in a terminal" in make_loop(h, mutex=name).start()
    assert "proc" not in h


def test_voice_log_rotates_once_it_is_big(monkeypatch):
    monkeypatch.setattr(app, "LOG_MAX_BYTES", 10)
    log = app.log_dir() / "voice.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * 20, encoding="utf-8")
    h: dict = {}
    loop = make_loop(h)
    loop.start()
    loop.stop()
    assert (app.log_dir() / "voice.log.1").read_text(encoding="utf-8") == "x" * 20


# --- the tray -------------------------------------------------------------------------------------------------------

class Ev:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        for handler in self.handlers:
            handler(*args)


class Item:
    def __init__(self, text=""):
        self.Text = text
        self.Click = Ev()


class Menu:
    def __init__(self):
        self.Items = SimpleNamespace(added=[])
        self.Items.Add = self.Items.added.append
        self.Opening = Ev()
        self.Handle = 1

    def BeginInvoke(self, fn):
        fn()


class Icon:
    def __init__(self):
        self.DoubleClick = Ev()
        self.balloons = []
        self.Visible = False
        self.disposed = False

    def ShowBalloonTip(self, ms, title, text, kind):
        self.balloons.append(text)

    def Dispose(self):
        self.disposed = True


def fake_forms() -> SimpleNamespace:
    stop = threading.Event()

    class Thread:
        def __init__(self, fn):
            self.fn = fn

        def SetApartmentState(self, state):
            self.state = state

        def Start(self):
            threading.Thread(target=self.fn, daemon=True).start()

    return SimpleNamespace(Action=lambda fn: fn, Icon=lambda path: path, ApartmentState=SimpleNamespace(STA="sta"),
                           Thread=Thread, ThreadStart=lambda fn: fn,
                           Application=SimpleNamespace(Run=stop.wait, ExitThread=stop.set),
                           ContextMenuStrip=Menu, NotifyIcon=Icon, ToolStripMenuItem=Item,
                           ToolStripSeparator=lambda: "---", ToolTipIcon=SimpleNamespace(Info="info"))


def wait_for(cond, seconds=5.0):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_tray_menu_runs_the_app_actions_off_the_menu_thread():
    calls = []
    fake_app = SimpleNamespace(voice=SimpleNamespace(running=False), open=lambda: calls.append("open"),
                               toggle_listening=lambda: calls.append("listen"),
                               settings=lambda: calls.append("settings"), quit=lambda: calls.append("quit"))
    tray = app.Tray(fake_app, fake_forms())
    tray.start()
    assert tray.icon.Visible and tray.icon.Text == "Iris"
    items = tray.menu.Items.added
    assert [getattr(i, "Text", i) for i in items] == ["Open Iris", "Start listening", "---", "Settings", "---",
                                                      "Quit Iris"]
    fake_app.voice.running = True
    tray.menu.Opening.fire(None, None)
    assert items[1].Text == "Stop listening"
    for i in (0, 1, 3, 5):
        items[i].Click.fire(None, None)
    tray.icon.DoubleClick.fire(None, None)
    assert wait_for(lambda: sorted(calls) == ["listen", "open", "open", "quit", "settings"])
    tray.notify("hello")
    assert tray.icon.balloons == ["hello"]
    tray.close()
    assert wait_for(lambda: tray.icon.disposed and not tray.icon.Visible)


def test_tray_notify_and_close_before_start_do_nothing():
    tray = app.Tray(SimpleNamespace(), fake_forms())
    tray.notify("early")
    tray.close()


# --- the app --------------------------------------------------------------------------------------------------------

class FakeWindow:
    def __init__(self):
        self.events = SimpleNamespace(closing=Ev(), minimized=Ev(), restored=Ev(), maximized=Ev())
        self.calls = []

    def __getattr__(self, name):  # show, hide, restore, destroy
        return lambda *a: self.calls.append(name)


class FakeWebview:
    def __init__(self):
        self.window = FakeWindow()
        self.started = {}

    def create_window(self, title, url, **kw):
        self.title, self.url, self.kw = title, url, kw
        return self.window

    def start(self, func, **kw):
        self.started = kw
        func()  # the window "opens", then the user closes it at once


class FakeVoice:
    def __init__(self, message="Iris is listening."):
        self.running, self.message, self.stops = False, message, 0

    def start(self):
        self.running = self.message == "Iris is listening."
        return self.message

    def stop(self):
        self.stops += 1
        self.running = False


def test_app_run_serves_the_dashboard_in_a_window_and_cleans_up(monkeypatch):
    from iris import firstrun

    firstrun.mark_done()
    monkeypatch.setenv("IRIS_DASHBOARD_PORT", "0")
    wv, voice = FakeWebview(), FakeVoice()
    started = []
    voice.start = lambda: started.append(1) or "Iris is listening."
    a = app.App(wv, forms=fake_forms(), voice=voice)
    assert a.run() == 0
    assert wv.title == "Iris" and wv.url.startswith("http://127.0.0.1:") and wv.url.endswith("/") and wv.url == a.url
    assert wv.kw["hidden"] is False
    assert wv.started["private_mode"] is False and wv.started["icon"].endswith("iris.ico")
    assert started == [1] and voice.stops >= 1  # started on open, stopped on the way out
    assert app.ICON.exists()


def test_first_run_opens_the_setup_tab_and_waits_to_listen(monkeypatch):
    monkeypatch.setenv("IRIS_DASHBOARD_PORT", "0")
    wv, voice = FakeWebview(), FakeVoice()
    voice.start = lambda: pytest.fail("no listening before setup is finished")
    a = app.App(wv, voice=voice)
    a.run()
    assert wv.url.endswith("/#setup")


def test_the_app_tells_about_a_newer_release_through_the_tray(monkeypatch):
    from iris import update

    monkeypatch.setattr(update, "_fetch", lambda: {"tag_name": "v99.0.0", "html_url": "u"})
    a = app.App(FakeWebview(), forms=fake_forms(), voice=FakeVoice(), listen=False)
    a.tray.start()
    a.tray.start = lambda: None
    a._started()
    assert wait_for(lambda: any("v99.0.0" in b for b in a.tray.icon.balloons))


def test_app_falls_back_to_a_free_port_and_starts_hidden_in_the_background(monkeypatch):
    from iris import dashboard

    taken = dashboard.make_server(port=0)
    try:
        monkeypatch.setenv("IRIS_DASHBOARD_PORT", str(taken.server_address[1]))
        wv = FakeWebview()
        app.App(wv, voice=FakeVoice(), background=True, listen=False).run()
        assert not wv.url.endswith(f":{taken.server_address[1]}/")
        assert wv.kw["hidden"] is True
    finally:
        taken.server_close()


def test_app_tells_the_tray_when_listening_could_not_start():
    a = app.App(FakeWebview(), forms=fake_forms(), voice=FakeVoice("Voice mode is already running in a terminal."))
    a.tray.start()
    a.tray.start = lambda: None
    a._started()
    assert a.tray.icon.balloons == ["Voice mode is already running in a terminal."]


def test_closing_hides_to_the_tray_and_says_so_once():
    a = app.App(FakeWebview(), forms=fake_forms(), voice=FakeVoice())
    a.window = FakeWindow()
    a.tray.start()
    assert a._on_closing() is False and a._on_closing() is False
    assert wait_for(lambda: a.window.calls.count("hide") == 2)
    assert len(a.tray.icon.balloons) == 1 and "keeps running" in a.tray.icon.balloons[0]
    a._quitting = True
    assert a._on_closing() is True


def test_open_restores_only_a_minimized_window():
    a = app.App(FakeWebview(), voice=FakeVoice())
    a.window = FakeWindow()
    a.open()
    assert a.window.calls == ["show"]
    a._minimized = True
    a.open()
    assert a.window.calls == ["show", "show", "restore"]


def test_settings_opens_the_settings_tab():
    a = app.App(FakeWebview(), voice=FakeVoice())
    a.window = FakeWindow()
    a.settings()
    assert a.window.calls == ["show", "evaluate_js"]


def test_restart_voice_stops_then_starts_and_is_offered_to_the_dashboard(monkeypatch):
    from iris import dashboard

    monkeypatch.setenv("IRIS_DASHBOARD_PORT", "0")
    voice = FakeVoice()
    a = app.App(FakeWebview(), voice=voice, listen=False)
    seen = {}

    def start(func, **kw):
        seen["hook"] = dashboard.APP_HOOKS.get("restart_voice")

    a.webview.start = start
    a.run()
    assert seen["hook"] == a.restart_voice and "restart_voice" not in dashboard.APP_HOOKS
    assert a.restart_voice() == "Iris is listening." and voice.running and voice.stops == 2


def test_toggle_listening_and_quit():
    voice = FakeVoice()
    a = app.App(FakeWebview(), forms=fake_forms(), voice=voice)
    a.window = FakeWindow()
    a.tray.start()
    a.toggle_listening()
    assert voice.running and a.tray.icon.balloons[-1] == "Iris is listening."
    a.toggle_listening()
    assert not voice.running and "microphone is free" in a.tray.icon.balloons[-1]
    a._voice_exited(2, "last line")
    assert "exit code 2" in a.tray.icon.balloons[-1]
    a.quit()
    assert a.window.calls == ["destroy"] and voice.stops == 2
    a._voice_exited(0, "")  # after Quit: no balloon
    assert "exit code 0" not in a.tray.icon.balloons[-1]


def test_main_needs_pywebview(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "webview", None)
    assert app.main([]) == 1
    assert "pip install" in capsys.readouterr().err


def test_main_brings_the_running_app_forward(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace())
    monkeypatch.setattr(app, "take_mutex", lambda name: None)
    shown = []
    monkeypatch.setattr(app, "show_running", lambda: shown.append(True))
    assert app.main([]) == 0 and shown == [True]


def test_main_runs_the_app(monkeypatch):
    seen = {}
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace())
    monkeypatch.setattr(app, "take_mutex", lambda name: 1)
    monkeypatch.setattr(app, "_set_app_id", lambda: seen.setdefault("app_id", True))
    monkeypatch.setattr(app, "winforms", lambda: "forms")

    class FakeApp:
        def __init__(self, webview, forms, background):
            seen.update(forms=forms, background=background)

        def run(self):
            return 0

    monkeypatch.setattr(app, "App", FakeApp)
    assert app.main(["--background"]) == 0
    assert seen == {"app_id": True, "forms": "forms", "background": True}
