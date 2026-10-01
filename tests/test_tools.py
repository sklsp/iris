"""Tests for press_keys, focus_window, scroll and open_url with fakes only (no real desktop)."""

import pytest

from iris import tools


class FakeWindow:
    def __init__(self, title="Notepad"):
        self.title = title
        self.focus_calls = 0
        self.keys = []

    def window_text(self):
        return self.title

    def set_focus(self):
        self.focus_calls += 1

    def type_keys(self, keys, **kw):
        self.keys.append(keys)

    def rectangle(self):
        class R:
            left, top, right, bottom = 0, 0, 800, 600

        return R()


@pytest.fixture
def fake_window(monkeypatch):
    win = FakeWindow()
    monkeypatch.setattr(tools, "_window", lambda title: win)
    return win


# --- press_keys ---------------------------------------------------------------
def test_press_keys_tier_is_confirm(fake_window):
    assert tools.press_keys("Notepad", "^s") == "pressed '^s' in 'Notepad'"
    assert fake_window.focus_calls == 1 and fake_window.keys == ["^s"]


@pytest.mark.parametrize("app", ["Outlook - Inbox", "Gmail - Chat", "WhatsApp", "Discord", "Microsoft Teams", "Slack"])
def test_enter_in_mail_chat_is_never(app):
    assert tools._press_keys_tier({"window": app, "keys": "{ENTER}"}) == tools.NEVER


@pytest.mark.parametrize("app", ["Notepad", "VS Code", "Chrome - Docs"])
def test_enter_elsewhere_stays_confirm(app):
    assert tools._press_keys_tier({"window": app, "keys": "{ENTER}"}) == tools.CONFIRM


def test_non_enter_in_mail_chat_is_only_confirm():
    # Ctrl+S in Outlook is fine; only the send combination (Enter) is refused.
    assert tools._press_keys_tier({"window": "Outlook", "keys": "^s"}) == tools.CONFIRM


# --- focus_window -------------------------------------------------------------
def test_focus_window(fake_window):
    assert tools.focus_window("Notepad") == "focused 'Notepad'"
    assert fake_window.focus_calls == 1


# --- scroll -------------------------------------------------------------------
def test_scroll_down_sends_negative_delta(monkeypatch, fake_window):
    calls = []
    monkeypatch.setattr(tools, "_scroll_at", lambda x, y, d: calls.append((x, y, d)))
    assert tools.scroll("Notepad", "down", 2) == "scrolled down by 2 in 'Notepad'"
    assert calls == [(400, 300, -240)]


def test_scroll_up_sends_positive_delta(monkeypatch, fake_window):
    calls = []
    monkeypatch.setattr(tools, "_scroll_at", lambda x, y, d: calls.append((x, y, d)))
    tools.scroll("Notepad", "up")  # default amount 3
    assert calls == [(400, 300, 360)]


def test_scroll_rejects_bad_direction(fake_window):
    with pytest.raises(ValueError):
        tools.scroll("Notepad", "left")


# --- open_url -----------------------------------------------------------------
@pytest.mark.parametrize("url", ["file:///C:/secret.txt", "javascript:alert(1)", "data:text/html,<b>x</b>"])
def test_open_url_refuses_non_http(url, monkeypatch):
    started = []
    monkeypatch.setattr(tools.os, "startfile", lambda u: started.append(u))
    with pytest.raises(ValueError):
        tools.open_url(url)
    assert started == []


@pytest.mark.parametrize("url", ["https://example.com", "http://localhost:8080"])
def test_open_url_allows_http_and_https(url, monkeypatch):
    started = []
    monkeypatch.setattr(tools.os, "startfile", lambda u: started.append(u))
    assert tools.open_url(url) == f"opened {url}"
    assert started == [url]


# --- registration -------------------------------------------------------------
def test_new_tools_registered_with_tiers():
    t = tools.TOOLS
    assert t["press_keys"].tier_of({"window": "Notepad", "keys": "^s"}) == tools.CONFIRM
    assert t["focus_window"].tier == tools.SAFE
    assert t["scroll"].tier == tools.SAFE
    assert t["open_url"].tier == tools.SAFE
