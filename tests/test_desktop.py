"""Tests for iris.desktop: clipboard, media keys and locking the screen, with fakes only (no real desktop)."""

import subprocess

from iris import desktop, tools


class _User32:
    def __init__(self):
        self.events = []
        self.locks = 0

    def keybd_event(self, vk, scan, flags, extra):
        self.events.append((vk, flags))

    def LockWorkStation(self):
        self.locks += 1


class _Windll:
    def __init__(self):
        self.user32 = _User32()


def _fake_windll(monkeypatch):
    w = _Windll()
    monkeypatch.setattr(desktop, "windll", w)
    return w


# --- clipboard_read ------------------------------------------------------------------

def test_clipboard_read_returns_the_text(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout="hello world\n", stderr="")

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    assert desktop.clipboard_read() == "hello world"
    cmd, kw = calls[0]
    assert cmd == ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"]
    assert kw["timeout"] == 10


def test_clipboard_read_empty_says_the_clipboard_is_empty(monkeypatch):
    monkeypatch.setattr(desktop.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(None, 0, stdout="", stderr=""))
    assert desktop.clipboard_read() == "The clipboard is empty."


def test_clipboard_read_truncates_to_20000_characters(monkeypatch):
    monkeypatch.setattr(desktop.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(None, 0, stdout="x" * 25000 + "\n", stderr=""))
    assert desktop.clipboard_read() == "x" * 20000


def test_clipboard_read_timeout_is_an_error(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("powershell", 10)

    monkeypatch.setattr(desktop.subprocess, "run", boom)
    assert desktop.clipboard_read() == "error: reading the clipboard timed out"


# --- clipboard_write -----------------------------------------------------------------

def test_clipboard_write_pipes_text_through_stdin(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    assert desktop.clipboard_write("secret text") == "put 11 characters on the clipboard"
    cmd, kw = calls[0]
    assert cmd == ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"]
    assert kw["input"] == "secret text"
    assert "secret text" not in " ".join(cmd)


def test_clipboard_write_failure_is_an_error(monkeypatch):
    monkeypatch.setattr(desktop.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(None, 1, stdout="", stderr="x"))
    assert desktop.clipboard_write("t") == "error: could not write the clipboard"


# --- media_key -----------------------------------------------------------------------

def test_media_key_presses_the_virtual_key_down_and_up(monkeypatch):
    w = _fake_windll(monkeypatch)
    assert desktop.media_key("play_pause") == "pressed play_pause 1 times"
    assert w.user32.events == [(0xB3, 0), (0xB3, 2)]


def test_media_key_repeats_times_times(monkeypatch):
    w = _fake_windll(monkeypatch)
    assert desktop.media_key("volume_up", 5) == "pressed volume_up 5 times"
    assert w.user32.events == [(0xAF, 0), (0xAF, 2)] * 5


def test_media_key_covers_all_six_keys(monkeypatch):
    expected = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1,
                "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}
    for name, vk in expected.items():
        w = _fake_windll(monkeypatch)
        assert desktop.media_key(name) == f"pressed {name} 1 times"
        assert w.user32.events == [(vk, 0), (vk, 2)]


def test_media_key_unknown_key_is_an_error(monkeypatch):
    w = _fake_windll(monkeypatch)
    assert desktop.media_key("stop") == "error: unknown media key"
    assert w.user32.events == []


def test_media_key_times_out_of_range_is_an_error(monkeypatch):
    w = _fake_windll(monkeypatch)
    assert desktop.media_key("mute", 0) == "error: times must be between 1 and 10"
    assert desktop.media_key("mute", 11) == "error: times must be between 1 and 10"
    assert w.user32.events == []


# --- lock_screen ---------------------------------------------------------------------

def test_lock_screen_calls_lock_work_station(monkeypatch):
    w = _fake_windll(monkeypatch)
    assert desktop.lock_screen() == "the screen is locked"
    assert w.user32.locks == 1


# --- tiers (all plain SAFE, pinned literally) ----------------------------------------

def test_desktop_tools_are_safe(tmp_path):
    for name in ("clipboard_read", "clipboard_write", "media_key", "lock_screen"):
        assert tools.TOOLS[name].tier_of({}) == tools.SAFE
