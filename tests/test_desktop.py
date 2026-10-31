"""Tests for iris.desktop: clipboard, media keys, locking the screen, screenshots, closing windows and the clock.

Fakes only (no real desktop).
"""

import subprocess
from datetime import datetime

import pytest

from iris import agent, desktop, tools


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


# --- screenshot --------------------------------------------------------------------

def test_screenshot_passes_the_path_as_an_argument(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    target = tmp_path / "shot.png"
    assert desktop.screenshot(str(target)) == f"saved {target}"
    cmd, kw = calls[0]
    assert cmd[:3] == ["powershell", "-NoProfile", "-Command"]
    script = cmd[3]
    assert cmd[-1] == str(target)  # the path is an argument to the script, not pasted into its text
    assert "CopyFromScreen" in script and "VirtualScreen" in script
    assert target.name not in script
    assert kw["timeout"] == 30


def test_screenshot_default_goes_to_pictures_iris_with_a_timestamp(tmp_path, monkeypatch):
    class _Path(desktop.Path):
        @classmethod
        def home(cls):
            return tmp_path

    monkeypatch.setattr(desktop, "Path", _Path)
    monkeypatch.setattr(tools, "_wall_now", lambda: datetime(2026, 10, 31, 14, 5))
    monkeypatch.setattr(desktop.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(None, 0, stdout="", stderr=""))
    assert desktop.screenshot() == f"saved {tmp_path / 'Pictures' / 'Iris' / 'screenshot-20261031-140500.png'}"


def test_screenshot_failure_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(None, 1, stdout="", stderr="x"))
    assert desktop.screenshot(str(tmp_path / "shot.png")) == "error: could not save a screenshot"


def test_screenshot_timeout_is_an_error(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("powershell", 30)

    monkeypatch.setattr(desktop.subprocess, "run", boom)
    assert desktop.screenshot(str(tmp_path / "shot.png")) == "error: could not save a screenshot"


# --- close_window --------------------------------------------------------------------

class _Win:
    def __init__(self, title):
        self.title = title
        self.closed = 0

    def window_text(self):
        return self.title

    def close(self):
        self.closed += 1


def test_close_window_closes_the_first_matching_window(monkeypatch):
    win = _Win("Notepad - report.txt")
    monkeypatch.setattr(tools, "_window", lambda title: win)
    assert desktop.close_window("notepad") == "closed 'Notepad - report.txt'"
    assert win.closed == 1


def test_close_window_without_a_match_raises_lookup_error(monkeypatch):
    def boom(title):
        raise LookupError(f"no window whose title contains {title!r}")

    monkeypatch.setattr(tools, "_window", boom)
    with pytest.raises(LookupError):
        desktop.close_window("nope")


def test_describe_asks_before_closing_a_window():
    assert (agent._describe("close_window", {"title": "Notepad"})
            == "Close the window Notepad? Unsaved work may ask to be saved.")


# --- clock ---------------------------------------------------------------------------

def test_clock_formats_weekday_date_time_and_iso_week(monkeypatch):
    monkeypatch.setattr(tools, "_wall_now", lambda: datetime(2026, 1, 5, 9, 5))
    assert desktop.clock() == "Monday 5 January 2026, 09:05, week 2"


# --- _start_menu_folders -----------------------------------------------------------

def test_start_menu_folders_come_from_the_env_var(tmp_path, monkeypatch):
    a = tmp_path / "a"
    b = tmp_path / "b"
    monkeypatch.setenv("IRIS_START_MENU", f"{a}; {b} ;")
    assert desktop._start_menu_folders() == [a, b]


def test_start_menu_folders_default_to_the_two_real_windows_ones(tmp_path, monkeypatch):
    monkeypatch.delenv("IRIS_START_MENU")
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("AppData", str(tmp_path / "ad"))
    assert desktop._start_menu_folders() == [
        tmp_path / "pd" / "Microsoft/Windows/Start Menu/Programs",
        tmp_path / "ad" / "Microsoft/Windows/Start Menu/Programs",
    ]


# --- start_menu_match ----------------------------------------------------------------

def _menu(tmp_path, *names):
    menu = tmp_path / "menu"
    menu.mkdir()
    for n in names:
        (menu / n).write_bytes(b"\x40")  # the .lnk content is irrelevant to the match
    return menu


def test_start_menu_match_exact_is_case_insensitive(tmp_path):
    menu = _menu(tmp_path, "Spotify.lnk", "Word.lnk")
    assert desktop.start_menu_match("sPoTiFy", [menu]) == menu / "Spotify.lnk"


def test_start_menu_match_starts_with_when_there_is_no_exact(tmp_path):
    menu = _menu(tmp_path, "Word Online.lnk")
    assert desktop.start_menu_match("word", [menu]) == menu / "Word Online.lnk"


def test_start_menu_match_contains_as_a_last_resort(tmp_path):
    menu = _menu(tmp_path, "Microsoft Word.lnk")
    assert desktop.start_menu_match("word", [menu]) == menu / "Microsoft Word.lnk"


def test_start_menu_match_exact_beats_starts_with_and_contains(tmp_path):
    menu = _menu(tmp_path, "WordPad.lnk", "Word.lnk", "Microsoft Word.lnk")
    assert desktop.start_menu_match("word", [menu]) == menu / "Word.lnk"


def test_start_menu_match_shortest_stem_wins_within_a_rank(tmp_path):
    menu = _menu(tmp_path, "Words.lnk", "Word Online.lnk")
    assert desktop.start_menu_match("word", [menu]) == menu / "Words.lnk"


def test_start_menu_match_searches_subfolders_of_every_folder(tmp_path):
    sub = tmp_path / "a" / "Accessories"
    sub.mkdir(parents=True)
    (sub / "Notepad.lnk").write_bytes(b"\x40")
    assert desktop.start_menu_match("notepad", [tmp_path / "missing", tmp_path / "b", tmp_path / "a"]) == \
        sub / "Notepad.lnk"


def test_start_menu_match_ignores_files_that_are_not_lnk(tmp_path):
    menu = _menu(tmp_path, "Spotify.txt", "spotify.url")
    assert desktop.start_menu_match("spotify", [menu]) is None


def test_start_menu_match_no_match_is_none(tmp_path):
    menu = _menu(tmp_path, "Word.lnk")
    assert desktop.start_menu_match("excel", [menu]) is None


def test_start_menu_match_missing_folder_is_none(tmp_path):
    assert desktop.start_menu_match("word", [tmp_path / "nope"]) is None


@pytest.mark.parametrize("name", ["ms-settings:", "C:/x/y.lnk", "C:\\x\\y.lnk", "notepad.exe", "NOTEPAD.EXE", ""])
def test_start_menu_match_skips_paths_uris_and_exes(tmp_path, name):
    menu = _menu(tmp_path, "ms-settings.lnk", "notepad.exe.lnk")  # both would match if the search ran
    assert desktop.start_menu_match(name, [menu]) is None


# --- open_app with a Start menu shortcut ---------------------------------------------

def test_open_app_starts_the_start_menu_shortcut(tmp_path, monkeypatch):
    menu = _menu(tmp_path, "Spotify.lnk")
    monkeypatch.setenv("IRIS_START_MENU", str(menu))
    started = []
    monkeypatch.setattr(tools.os, "startfile", lambda name: started.append(name))
    assert tools.open_app("spotify") == "started Spotify"
    assert started == [str(menu / "Spotify.lnk")]


def test_open_app_falls_back_to_shell_execute_without_a_match(tmp_path, monkeypatch):
    menu = tmp_path / "menu"
    menu.mkdir()  # empty: no shortcut for this name
    monkeypatch.setenv("IRIS_START_MENU", str(menu))
    started = []
    monkeypatch.setattr(tools.os, "startfile", lambda name: started.append(name))
    assert tools.open_app("notepad") == "started 'notepad'"
    assert started == ["notepad"]


def test_open_app_description_mentions_the_start_menu():
    d = tools.TOOLS["open_app"].description
    assert "Works with the name as it appears in the Start menu, such as Spotify or Word." in d


# --- tiers for the new tools (pinned literally) --------------------------------------

def test_screenshot_and_clock_are_safe():
    assert tools.TOOLS["screenshot"].tier_of({}) == tools.SAFE
    assert tools.TOOLS["clock"].tier_of({}) == tools.SAFE


def test_close_window_is_confirm():
    assert tools.TOOLS["close_window"].tier_of({"title": "Notepad"}) == tools.CONFIRM


# --- email_draft ---------------------------------------------------------------------

def test_email_draft_opens_a_mailto_url(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(desktop.os, "startfile", lambda url: opened.append(url))
    out = desktop.email_draft(to="jay@example.com", subject="Hello there", body="Line one\nLine two")
    assert out == "opened a draft to jay@example.com; check it and press send yourself"
    assert opened == ["mailto:jay%40example.com?subject=Hello%20there&body=Line%20one%0ALine%20two"]


def test_email_draft_description_never_sends():
    d = tools.TOOLS["email_draft"].description
    assert "never sends" in d


def test_email_draft_cuts_the_body_at_1500_characters(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop.os, "startfile", lambda url: opened.append(url))
    desktop.email_draft(to="j@x.com", subject="", body="a" * 2000)
    assert opened[0].endswith("body=" + "a" * 1500 + "...")


def test_email_draft_keeps_a_body_of_exactly_1500_whole(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop.os, "startfile", lambda url: opened.append(url))
    desktop.email_draft(to="j@x.com", subject="", body="a" * 1500)
    assert opened[0].endswith("body=" + "a" * 1500)


def test_email_draft_without_a_recipient_is_an_error():
    assert desktop.email_draft() == "error: say who the email is for"


def test_email_draft_is_safe():
    assert tools.TOOLS["email_draft"].tier_of({}) == tools.SAFE
