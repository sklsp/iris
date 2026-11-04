"""Tests for iris.desktop: clipboard, media keys, locking the screen, screenshots, closing windows and the clock.

Fakes only (no real desktop).
"""

import subprocess
from datetime import datetime
from pathlib import Path

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
    assert cmd == ["powershell", "-NoProfile", "-Command", desktop._UTF8 + "Get-Clipboard -Raw"]
    assert (kw["timeout"], kw["encoding"]) == (10, "utf-8")  # é and ë survive the console code page


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


def test_clipboard_read_os_error_is_an_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no powershell here")

    monkeypatch.setattr(desktop.subprocess, "run", boom)
    assert desktop.clipboard_read() == "error: could not read the clipboard"


# --- clipboard_write -----------------------------------------------------------------

def test_clipboard_write_passes_the_text_through_the_environment(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    assert desktop.clipboard_write("geheim café") == "put 11 characters on the clipboard"
    cmd, kw = calls[0]
    assert cmd == ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $env:IRIS_CLIP"]
    assert kw["env"]["IRIS_CLIP"] == "geheim café"  # Unicode, unlike stdin through the console code page
    assert "geheim" not in " ".join(cmd)
    assert desktop.clipboard_write("x" * 32001) == "error: at most 32000 characters fit on the clipboard this way"


def test_clipboard_write_failure_is_an_error(monkeypatch):
    monkeypatch.setattr(desktop.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(None, 1, stdout="", stderr="x"))
    assert desktop.clipboard_write("t") == "error: could not write the clipboard"


def test_clipboard_write_timeout_is_an_error(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("powershell", 10)

    monkeypatch.setattr(desktop.subprocess, "run", boom)
    assert desktop.clipboard_write("t") == "error: writing to the clipboard timed out"


def test_clipboard_write_os_error_is_an_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no powershell here")

    monkeypatch.setattr(desktop.subprocess, "run", boom)
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

def test_screenshot_passes_the_path_through_the_environment_never_the_script(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    target = tmp_path / "shot'; Remove-Item x; '.png"  # a path that would run as PowerShell if pasted in
    assert desktop.screenshot(str(target)) == f"saved {target}"
    cmd, kw = calls[0]
    assert cmd == ["powershell", "-NoProfile", "-Command", desktop._SCREENSHOT_PS]
    assert kw["env"]["IRIS_SHOT_PATH"] == str(target)
    assert "$env:IRIS_SHOT_PATH" in cmd[3] and "CopyFromScreen" in cmd[3] and "Remove-Item" not in cmd[3]


def test_screenshot_tier_follows_the_new_file_rules(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Pictures").mkdir(parents=True)
    (home / "Pictures" / "old.png").write_text("x", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    t = tools.TOOLS["screenshot"]
    assert t.tier_of({}) == tools.SAFE
    assert t.tier_of({"path": str(home / "Pictures" / "new.png")}) == tools.SAFE
    assert t.tier_of({"path": str(home / "Pictures" / "old")}) == tools.CONFIRM  # old means old.png
    assert t.tier_of({"path": "C:/Windows/shot.png"}) == tools.NEVER
    assert t.tier_of({"path": str(tmp_path / "elsewhere.png")}) == tools.NEVER


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


# --- look_at_screen and click_on_screen -------------------------------------------------

class _Mouse:
    def __init__(self):
        self.calls = []

    def SetProcessDPIAware(self):
        self.calls.append("dpi")

    def GetSystemMetrics(self, i):
        return {76: -1920, 77: 0, 78: 3840, 79: 1080}[i]

    def SetCursorPos(self, x, y):
        self.calls.append(("pos", x, y))

    def mouse_event(self, flags, *rest):
        self.calls.append(("mouse", flags))


def _fake_mouse(monkeypatch):
    m = _Mouse()
    monkeypatch.setattr(desktop, "windll", type("W", (), {"user32": m})())
    return m


def test_area_is_every_screen_or_the_window_brought_forward(monkeypatch):
    _fake_mouse(monkeypatch)
    assert desktop._area("") == (-1920, 0, 3840, 1080)

    class Rect:
        left, top = 10, 20

        def __init__(self, w, h):
            self.w, self.h = w, h

        def width(self):
            return self.w

        def height(self):
            return self.h

    class Win:
        def __init__(self, rect):
            self.rect, self.focused = rect, False

        def set_focus(self):
            self.focused = True

        def rectangle(self):
            return self.rect

        def window_text(self):
            return "Paint"

    win = Win(Rect(300, 200))
    monkeypatch.setattr(tools, "_window", lambda title: win)
    assert desktop._area("paint") == (10, 20, 300, 200) and win.focused
    monkeypatch.setattr(tools, "_window", lambda title: Win(Rect(0, 0)))
    assert desktop.look_at_screen("what?", "paint") == "error: 'Paint' is minimized; there is nothing to see"


def test_capture_passes_the_rectangle_and_path_through_the_environment(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        Path(kw["env"]["IRIS_SHOT_PATH"]).write_bytes(b"PNG")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    assert desktop._capture((-1920, 0, 3840, 1080)) == b"PNG"
    assert seen["IRIS_SHOT_RECT"] == "-1920,0,3840,1080"
    assert "$env:IRIS_SHOT_RECT" in desktop._LOOK_PS and "1600" in desktop._LOOK_PS
    monkeypatch.setattr(desktop.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"))
    assert desktop._capture((0, 0, 1, 1)) == b""

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(desktop.subprocess, "run", slow)
    assert desktop._capture((0, 0, 1, 1)) == b""


def test_look_at_screen_asks_the_model_and_reports_failures(monkeypatch):
    import urllib.error

    _fake_mouse(monkeypatch)
    monkeypatch.setattr(desktop, "_capture", lambda area: b"PNG")
    monkeypatch.setattr(desktop.llm, "see", lambda png, q: f"saw {png!r} for {q}")
    assert desktop.look_at_screen("what is open?") == "saw b'PNG' for what is open?"
    monkeypatch.setattr(desktop.llm, "see", lambda png, q: "")
    assert desktop.look_at_screen("x") == "error: the model saw nothing it could describe"

    def http(png, q):
        raise urllib.error.HTTPError("u", 400, "bad", None, None)

    def down(png, q):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(desktop.llm, "see", http)
    assert "may not read images" in desktop.look_at_screen("x")
    monkeypatch.setattr(desktop.llm, "see", down)
    assert desktop.look_at_screen("x") == ("error: could not reach the model to look at the screen; "
                                           "is the model server running?")
    monkeypatch.setattr(desktop, "_capture", lambda area: b"")
    assert desktop.look_at_screen("x") == "error: could not capture the screen"


def test_click_on_screen_maps_the_models_0_to_1000_point_onto_the_area(monkeypatch):
    m = _fake_mouse(monkeypatch)
    monkeypatch.setattr(desktop, "_area", lambda window: (100, 200, 1000, 800))
    monkeypatch.setattr(desktop, "_capture", lambda area: b"PNG")
    monkeypatch.setattr(desktop.llm, "locate", lambda png, target: "500,250")
    assert desktop.click_on_screen("the OK button").startswith("clicked 'the OK button' at (600, 400)")
    assert m.calls == [("pos", 600, 400), ("mouse", 2), ("mouse", 4)]
    monkeypatch.setattr(desktop.llm, "locate", lambda png, target: "")
    assert desktop.click_on_screen("a unicorn") == "error: could not find 'a unicorn' on the screen"
    monkeypatch.setattr(desktop, "_capture", lambda area: b"")
    assert desktop.click_on_screen("x") == "error: could not capture the screen"
    assert len(m.calls) == 3  # no click without a point

    def gone(window):
        raise LookupError("no window")

    monkeypatch.setattr(desktop, "_area", gone)
    assert desktop.click_on_screen("x", "nope") == "error: no window"


def test_vision_tools_tiers_and_confirm_text():
    assert tools.TOOLS["look_at_screen"].tier_of({"question": "x"}) == tools.SAFE
    assert tools.TOOLS["click_on_screen"].tier_of({"target": "x"}) == tools.CONFIRM
    assert agent._describe("click_on_screen", {"target": "the Play button", "window": "Steam"}) == \
        "Click the Play button on Steam?"
    assert agent._describe("click_on_screen", {"target": "OK"}) == "Click OK on the screen?"
    info = {"target": "the Info tab in LM Studio", "window": "LM Studio"}
    assert agent._describe("click_on_screen", info) == "Click the Info tab in LM Studio?"


def test_click_on_screen_never_for_a_send_or_pay_target():
    t = tools.TOOLS["click_on_screen"]
    assert t.tier_of({"target": "the Send button"}) == tools.NEVER
    assert t.tier_of({"target": "Betalen"}) == tools.NEVER
    assert t.tier_of({"target": "the Play button"}) == tools.CONFIRM
