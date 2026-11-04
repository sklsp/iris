"""`iris app`: Iris as a desktop app. A window with the dashboard, a tray icon and the voice loop as one supervised
child process. See docs/desktop-app.md.

    iris app                the window, the tray icon, and voice mode started
    iris app --background   the tray icon only (start at login); the window opens from the tray

Needs the `app` extra (pywebview on Edge WebView2). One instance per user: a second start brings the first window
forward. Closing the window keeps Iris in the tray; Quit in the tray menu stops everything.

The voice loop is `python -m iris --voice --supervised` inside a Windows Job Object: the overlay and speech workers
it starts are in the job too, so nothing outlives the app, also when the app itself crashes. Its output goes to
logs/voice.log next to config.toml; the dashboard's Logs tab shows it.
"""

from __future__ import annotations

import ctypes
import os
import secrets
import subprocess
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from iris import config

TITLE = "Iris"
APP_ID = "sklsp.Iris"                  # taskbar grouping and the Start menu shortcut
APP_MUTEX = "Local\\Iris-App"          # one app per user; the installer's AppMutex uses the same name
VOICE_MUTEX = "Local\\Iris-Voice"      # held by every `iris --voice`, also one started in a terminal
ICON = Path(__file__).parent / "assets" / "iris.ico"
LOG_MAX_BYTES = 1_000_000              # voice.log moves to voice.log.1 at the next start once it is this big
STOP_SECONDS = 5.0                     # a clean stop gets this long before the job is ended


def log_dir() -> Path:
    """The logs folder next to config.toml (~/.iris/logs)."""
    return config.config_path().parent / "logs"


# --- Windows: named mutexes, the window of a running app, Job Objects ---------------------------------------------

def _kernel32() -> Any:
    k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k.CreateMutexW.restype = k.OpenMutexW.restype = k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.CreateEventW.restype = k.OpenEventW.restype = wintypes.HANDLE
    k.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.SetEvent.argtypes = [wintypes.HANDLE]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


def take_mutex(name: str) -> int | None:
    """Create the named mutex and keep it for the life of this process; None when another process has it already."""
    k = _kernel32()
    handle = k.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        k.CloseHandle(handle)
        return None
    return handle


def mutex_exists(name: str) -> bool:
    """Whether another process is holding the named mutex, without taking it."""
    k = _kernel32()
    handle = k.OpenMutexW(0x00100000, False, name)  # SYNCHRONIZE
    if handle:
        k.CloseHandle(handle)
    return bool(handle)


def quit_when_set(name: str) -> None:
    """`iris --voice --supervised`: wait for the app's stop event, then quit the way Ctrl+C does, so the session
    closes the mic, the overlay and the speech worker. An event, not stdin: a thread blocked reading a pipe makes
    every DLL that checks stdin while it loads (numpy's does) wait for that read."""
    import _thread

    k = _kernel32()
    handle = k.OpenEventW(0x00100000, False, name)  # SYNCHRONIZE
    if not handle:
        return  # no app is waiting on us; the Job Object still ends this process with the app
    k.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
    k.CloseHandle(handle)
    _thread.interrupt_main()


def show_running(title: str = TITLE) -> bool:
    """Bring the window of the app that is already running forward (also when it is hidden in the tray).

    Matches the title and a WinForms window class, so a console titled Iris (`iris shortcut`) is never picked."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd: int, _lparam: int) -> bool:
        text, cls = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, text, 256)
        user32.GetClassNameW(hwnd, cls, 256)
        if text.value == title and cls.value.startswith("WindowsForms10."):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(each, 0)
    for hwnd in found:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE: shows a hidden or minimized window
        user32.SetForegroundWindow(hwnd)  # allowed: the user just started this process
    return bool(found)


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("reads", "writes", "others", "read_bytes", "write_bytes",
                                                   "other_bytes")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class Job:
    """A Job Object that ends every process in it when it is closed: on stop, and by Windows when Iris dies."""

    def __init__(self) -> None:
        self._k = _kernel32()
        self.handle = self._k.CreateJobObjectW(None, None)
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self._k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))  # Extended

    def add(self, proc: subprocess.Popen) -> None:
        """Put a process in the job so it dies with it; safe because the child starts no processes of its own yet."""
        # ponytail: the child runs a moment before it joins; it starts no process of its own that early
        self._k.AssignProcessToJobObject(self.handle, int(proc._handle))  # type: ignore[attr-defined]

    def kill(self) -> None:
        """End every process in the job at once."""
        self._k.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        """Close the job's handle; with KILL_ON_JOB_CLOSE set, that ends whatever is still in it."""
        self._k.CloseHandle(self.handle)


class StopEvent:
    """A named event the app sets to ask `iris --voice --supervised` to quit (see quit_when_set)."""

    def __init__(self) -> None:
        self._k = _kernel32()
        self.name = f"Local\\Iris-Stop-{os.getpid()}-{secrets.token_hex(4)}"
        self.handle = self._k.CreateEventW(None, True, False, self.name)  # manual reset, not set

    def set(self) -> None:
        """Signal the supervised voice loop to quit cleanly."""
        self._k.SetEvent(self.handle)

    def close(self) -> None:
        """Close the event's handle."""
        self._k.CloseHandle(self.handle)


# --- the supervised voice loop ---------------------------------------------------------------------------------------

class VoiceLoop:
    """`python -m iris --voice --supervised` as one child. Setting its stop event asks for a clean stop (the session
    closes the mic, overlay and speech worker); after STOP_SECONDS the job ends whatever is left."""

    def __init__(self, on_exit: Callable[[int, str], None] | None = None, popen: Callable = subprocess.Popen,
                 job: Callable[[], Any] = Job, event: Callable[[], Any] = StopEvent,
                 voice_mutex: str = VOICE_MUTEX, cmd: list[str] | None = None) -> None:
        self.on_exit = on_exit  # (exit code, last log lines) when it stops without being asked to
        self._popen, self._job_factory, self._event_factory = popen, job, event
        self.voice_mutex = voice_mutex
        self.cmd = cmd or [sys.executable, "-m", "iris", "--voice", "--supervised"]
        self.proc: subprocess.Popen | None = None
        self._event: Any = None
        self._watcher: threading.Thread | None = None
        self._stopping = False
        self._lock = threading.Lock()

    @property
    def log_path(self) -> Path:
        """Where the voice loop writes its output: logs/voice.log next to config.toml."""
        return log_dir() / "voice.log"

    @property
    def running(self) -> bool:
        """Whether the voice loop is started."""
        return self.proc is not None

    def start(self) -> str:
        """Start listening; the sentence to show the user."""
        with self._lock:
            if self.proc is not None:
                return "Iris is listening."
            if mutex_exists(self.voice_mutex):
                return "Voice mode is already running in a terminal. Stop it there, then start listening here."
            path = self.log_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))
            event = self._event_factory()
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "IRIS_STOP_EVENT": event.name}
            with path.open("ab") as log:  # the child keeps its own handle
                proc = self._popen(self.cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            job = self._job_factory()
            job.add(proc)
            self.proc, self._event, self._stopping = proc, event, False
            self._watcher = threading.Thread(target=self._watch, args=(proc, job, event), name="iris-voice-watch",
                                             daemon=True)
            self._watcher.start()
            return "Iris is listening."

    def stop(self, seconds: float = STOP_SECONDS) -> None:
        """Ask the voice loop to stop and wait up to seconds for it; kill it if it does not go."""
        with self._lock:
            proc, event, watcher = self.proc, self._event, self._watcher
            self._stopping = True
        if proc is None or watcher is None:
            return
        event.set()
        watcher.join(seconds)
        if watcher.is_alive():
            proc.kill()  # the watcher then ends the rest of the job
            watcher.join(seconds)

    def _watch(self, proc: subprocess.Popen, job: Any, event: Any) -> None:
        code = proc.wait()
        job.kill()  # whatever the voice loop left behind (a speech worker, the overlay)
        job.close()
        event.close()
        with self._lock:
            asked = self._stopping
            if self.proc is proc:
                self.proc = None
        if not asked and self.on_exit is not None:
            from iris.dashboard import tail_lines

            self.on_exit(code, "\n".join(tail_lines(self.log_path, 5)))


# --- the tray icon ---------------------------------------------------------------------------------------------------

def winforms() -> SimpleNamespace:  # pragma: no cover - needs .NET through pythonnet; tests pass a fake
    """The WinForms classes the tray needs. pywebview loads pythonnet and WinForms on Windows anyway."""
    import clr  # type: ignore[import-not-found]

    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System import Action  # type: ignore[import-not-found]
    from System.Drawing import Icon  # type: ignore[import-not-found]
    from System.Threading import ApartmentState, Thread, ThreadStart  # type: ignore[import-not-found]
    from System.Windows.Forms import (  # type: ignore[import-not-found]
        Application,
        ContextMenuStrip,
        NotifyIcon,
        ToolStripMenuItem,
        ToolStripSeparator,
        ToolTipIcon,
    )

    return SimpleNamespace(Action=Action, Icon=Icon, ApartmentState=ApartmentState, Thread=Thread,
                           ThreadStart=ThreadStart, Application=Application, ContextMenuStrip=ContextMenuStrip,
                           NotifyIcon=NotifyIcon, ToolStripMenuItem=ToolStripMenuItem,
                           ToolStripSeparator=ToolStripSeparator, ToolTipIcon=ToolTipIcon)


class Tray:
    """A NotifyIcon with Open, Start/Stop listening, Settings and Quit, on its own STA thread with its own message
    loop. Menu clicks hand the work to a Python thread, so a slow stop never freezes the menu."""

    def __init__(self, app: App, forms: SimpleNamespace) -> None:
        self.app, self.f = app, forms
        self.icon: Any = None
        self.menu: Any = None
        self.ready = threading.Event()

    def start(self) -> None:
        """Start the tray's own STA thread and wait up to ten seconds for it to show the icon."""
        t = self.f.Thread(self.f.ThreadStart(self._run))
        t.SetApartmentState(self.f.ApartmentState.STA)
        t.IsBackground = True
        t.Start()
        self.ready.wait(10)

    def _later(self, fn: Callable[[], object]) -> Callable[[object, object], None]:
        return lambda _sender, _args: threading.Thread(target=fn, daemon=True).start()

    def _item(self, text: str, fn: Callable[[], object]) -> Any:
        item = self.f.ToolStripMenuItem(text)
        item.Click += self._later(fn)
        return item

    def _run(self) -> None:
        f = self.f
        self.icon = f.NotifyIcon()
        self.icon.Icon = f.Icon(str(ICON))
        self.icon.Text = TITLE
        self.menu = f.ContextMenuStrip()
        listen = self._item("Start listening", self.app.toggle_listening)
        for entry in (self._item("Open Iris", self.app.open), listen, f.ToolStripSeparator(),
                      self._item("Settings", self.app.settings), f.ToolStripSeparator(),
                      self._item("Quit Iris", self.app.quit)):
            self.menu.Items.Add(entry)

        def opening(_sender: object, _args: object) -> None:
            listen.Text = "Stop listening" if self.app.voice.running else "Start listening"

        self.menu.Opening += opening
        self.icon.DoubleClick += self._later(self.app.open)
        self.icon.ContextMenuStrip = self.menu
        _ = self.menu.Handle  # create the handle now, so close() can BeginInvoke on this thread
        self.icon.Visible = True
        self.ready.set()
        f.Application.Run()
        self.icon.Visible = False
        self.icon.Dispose()

    def notify(self, text: str) -> None:
        """A balloon next to the clock: never takes focus from the window Iris types into."""
        if self.icon is not None:
            self.icon.ShowBalloonTip(5000, TITLE, text[:250], self.f.ToolTipIcon.Info)

    def close(self) -> None:
        """Ask the tray's message loop to exit, on its own thread."""
        if self.menu is not None:
            self.menu.BeginInvoke(self.f.Action(self.f.Application.ExitThread))


# --- the app ---------------------------------------------------------------------------------------------------------

class App:
    """The window, the tray, the dashboard server and the voice loop, started and stopped together."""

    def __init__(self, webview: Any, forms: SimpleNamespace | None = None, voice: VoiceLoop | None = None,
                 background: bool = False, listen: bool = True) -> None:
        self.webview, self.background, self.listen = webview, background, listen
        self.voice = voice or VoiceLoop(on_exit=self._voice_exited)
        self.tray = Tray(self, forms) if forms is not None else None
        self.window: Any = None
        self.server: Any = None
        self.url = ""
        self._quitting = False
        self._told_tray = False
        self._minimized = False

    def run(self) -> int:
        """Start the dashboard server, the window and voice mode; stop everything on Quit (closing only hides it)."""
        from iris import dashboard

        # models the app downloads (Whisper, VoxCPM2) live under ~/.iris, so uninstall can remove them on request
        os.environ.setdefault("HF_HOME", str(config.config_path().parent / "models"))

        try:
            self.server = dashboard.make_server(port=dashboard.default_port())
        except OSError:  # `iris dashboard` or another program has the port: any free one will do
            self.server = dashboard.make_server(port=0)
        threading.Thread(target=self.server.serve_forever, name="iris-dashboard", daemon=True).start()
        dashboard.APP_HOOKS["restart_voice"] = self.restart_voice
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        from iris import firstrun

        if not firstrun.done():  # the wizard first; listening starts when it is finished
            self.url += "#setup"
            self.listen = False
        self.window = self.webview.create_window(TITLE, self.url, width=1280, height=860, min_size=(900, 600),
                                                 hidden=self.background, background_color="#0d0f12")
        self.window.events.closing += self._on_closing
        self.window.events.minimized += lambda: setattr(self, "_minimized", True)
        self.window.events.restored += lambda: setattr(self, "_minimized", False)
        self.window.events.maximized += lambda: setattr(self, "_minimized", False)
        storage = str(config.config_path().parent / "webview")  # keeps the page's own small preferences
        try:
            self.webview.start(self._started, private_mode=False, storage_path=storage, icon=str(ICON))
        finally:
            self._quitting = True
            self.voice.stop()
            if self.tray is not None:
                self.tray.close()
            self.server.shutdown()
            self.server.server_close()
            dashboard.APP_HOOKS.pop("restart_voice", None)
        return 0

    def _started(self) -> None:
        """Runs once the window exists (pywebview calls it on its own thread)."""
        if self.tray is not None:
            self.tray.start()
            from iris import update

            threading.Thread(target=update.tell_once, args=(self.tray.notify,), name="iris-update",
                             daemon=True).start()
        if self.listen:
            message = self.voice.start()
            if message != "Iris is listening." and self.tray is not None:
                self.tray.notify(message)

    def _on_closing(self) -> bool:
        """The close button hides the window to the tray; only Quit ends the app."""
        if self._quitting:
            return True
        threading.Thread(target=self.window.hide, daemon=True).start()
        if not self._told_tray and self.tray is not None:
            self._told_tray = True
            self.tray.notify("Iris keeps running here. Right-click the icon to stop listening or quit.")
        return False

    def _voice_exited(self, code: int, last_lines: str) -> None:
        if self.tray is not None and not self._quitting:
            self.tray.notify(f"Voice mode stopped (exit code {code}). See the Logs tab.\n{last_lines}")

    def open(self) -> None:
        """Show the window again, restoring it if it was minimized."""
        self.window.show()
        if self._minimized:
            self.window.restore()

    def settings(self) -> None:
        """Open the window on its Settings tab."""
        self.open()
        self.window.evaluate_js("location.hash = 'settings'")

    def restart_voice(self) -> str:
        """Stop and start voice mode, so settings it reads at start apply (the Settings tab's button)."""
        self.voice.stop()
        return self.voice.start()

    def toggle_listening(self) -> None:
        """Start or stop listening from the tray, and say what happened in a balloon."""
        if self.voice.running:
            self.voice.stop()
            if self.tray is not None:
                self.tray.notify("Iris stopped listening. The microphone is free.")
        else:
            message = self.voice.start()
            if self.tray is not None:
                self.tray.notify(message)

    def quit(self) -> None:
        """Stop the voice loop and destroy the window, which ends run() and closes the tray and server."""
        self._quitting = True
        self.voice.stop()
        self.window.destroy()  # webview.start returns; run() closes the tray and the server


def _set_app_id() -> None:
    """Group the window under Iris on the taskbar instead of under python."""
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)  # type: ignore[attr-defined]


def log_when_windowless() -> Path | None:
    """Started as Iris.exe (pythonw) from the Start menu, the app has no console: sys.stdout and sys.stderr are None
    and an error would vanish. Send them to logs/app.log instead, which the Logs tab shows."""
    if sys.stdout is not None and sys.stderr is not None:
        return None
    path = log_dir() / "app.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
        os.replace(path, path.with_name(path.name + ".1"))
    stream = path.open("a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream
    return path


def main(argv: list[str]) -> int:
    """Start the app: log to a file when there is no console, and bring an already running Iris forward instead."""
    log_when_windowless()
    background = "--background" in argv
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        print('The desktop app needs pywebview: pip install -e ".[app]"', file=sys.stderr)
        return 1
    if take_mutex(APP_MUTEX) is None:
        show_running()
        return 0
    _set_app_id()
    return App(webview, winforms(), background=background).run()
