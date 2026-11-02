"""Web pages through the DevTools Protocol, against a fake browser: no real browser, socket or network."""

import json
import time
import urllib.error

import pytest

from iris import browser, tools

SNAP = {"title": "Home - Netflix", "url": "https://www.netflix.com/browse",
        "items": [{"role": "link", "name": "Home", "value": None, "type": ""},
                  {"role": "button", "name": "Zoeken", "value": None, "type": ""},
                  {"role": "textbox", "name": "Titels, mensen, genres", "value": "", "type": "search"},
                  {"role": "button", "name": "Nu betalen", "value": None, "type": ""},
                  {"role": "textbox", "name": "Wachtwoord", "value": "", "type": "password"}],
        "text": "Populair op Netflix Weapons Sinners"}


class FakeBrowser:
    def __init__(self, point="[100, 200]", snap=SNAP):
        self.point, self.snap, self.calls, self.frozen = point, snap, [], set()
        self.targets = [{"targetId": "A", "type": "page", "title": "Home - Netflix", "url": SNAP["url"]},
                        {"targetId": "B", "type": "page", "title": "YouTube", "url": "https://www.youtube.com/"},
                        {"targetId": "C", "type": "page", "title": "DevTools", "url": "devtools://x"}]

    def send(self, method, params=None, session=None, wait=15):
        self.calls.append((method, params or {}, session))
        if session in self.frozen:
            raise browser.BrowserError(f"the browser did not answer {method} within {wait:g} s")
        if method == "Target.getTargets":
            return {"targetInfos": self.targets}
        if method == "Target.attachToTarget":
            return {"sessionId": "s-" + params["targetId"]}
        if method == "Runtime.evaluate":
            expr = params["expression"]
            if expr.startswith("(document.visibilityState"):
                return {"result": {"value": 2 if session == "s-A" else 1}}
            if "window.__iris = els" in expr:
                return {"result": {"value": json.dumps(self.snap)}}
            if "elementFromPoint" in expr:
                return {"result": {"value": self.point}}
            if expr == "document.readyState":
                return {"result": {"value": "complete"}}
            if "JSON.stringify({human" in expr:
                return {"result": {"value": json.dumps(self.search)}}
            return {"result": {}}
        if method == "Target.createTarget":
            return {"targetId": "S"}
        return {}

    search = {"human": False, "results": [{"title": "Uitslag GP Mexico", "url": "https://f1.example/mexico",
                                           "snippet": "Verstappen won."}]}


@pytest.fixture
def fake(monkeypatch):
    b = FakeBrowser()
    monkeypatch.setattr(browser, "_connect", lambda: b)
    monkeypatch.setattr(browser, "_settle", lambda session: None)
    browser._sessions.clear()
    browser._last.clear()
    return b


def test_read_numbers_the_visible_controls_of_the_tab_in_front(fake):
    out = browser.browser_read()
    assert out.splitlines()[:4] == ['Tab: Home - Netflix (https://www.netflix.com/browse)', '[1] link "Home"',
                                    '[2] button "Zoeken"', '[3] textbox "Titels, mensen, genres" (empty)']
    assert "Page text: Populair op Netflix" in out
    assert browser.item(5)["type"] == "password" and browser.host(2) == "www.netflix.com"
    snaps = [c for c in fake.calls if c[0] == "Runtime.evaluate" and "window.__iris = els" in c[1]["expression"]]
    assert snaps[0][2] == "s-A"  # the tab that is visible and focused, never the DevTools one


def test_a_tab_can_be_picked_by_title_or_address(fake):
    browser.browser_read("youtube")
    assert [c[2] for c in fake.calls if c[0] == "Runtime.evaluate"][-1] == "s-B"
    with pytest.raises(browser.BrowserError, match="no browser tab"):
        browser.browser_read("spotify")


def test_a_frozen_tab_is_skipped_not_waited_on(fake):
    fake.frozen.add("s-A")
    assert browser.browser_read().startswith("Tab: Home - Netflix")  # the snapshot itself is faked
    assert "A" not in browser._sessions and "B" in browser._sessions
    fake.frozen.add("s-B")
    browser._sessions.clear()
    with pytest.raises(browser.BrowserError, match="no browser tab answered"):
        browser.browser_read()


def test_a_slow_answer_keeps_the_socket_so_the_browser_does_not_ask_allow_again(monkeypatch):
    class SlowSocket:
        def send(self, data):
            pass

        def settimeout(self, s):
            pass

        def recv(self):
            raise TimeoutError("timed out")

    c = object.__new__(browser.Connection)
    c.ws, c._id, c._slow, c._lock = SlowSocket(), 0, (TimeoutError,), browser.threading.Lock()
    monkeypatch.setattr(browser, "_conn", c)
    monkeypatch.setattr(browser, "_ws_url", lambda: pytest.fail("reconnected"))
    with pytest.raises(browser.BrowserError, match="did not answer Runtime.evaluate within 2 s"):
        browser._call("Runtime.evaluate", {}, "s", 2)
    assert browser._conn is c


def test_click_presses_the_mouse_on_the_element_and_returns_the_new_page(fake):
    browser.browser_read()
    out = browser.browser_click(2)
    mouse = [c[1] for c in fake.calls if c[0] == "Input.dispatchMouseEvent"]
    assert [m["type"] for m in mouse] == ["mouseMoved", "mousePressed", "mouseReleased"]
    assert (mouse[1]["x"], mouse[1]["y"]) == (100.0, 200.0)
    assert out.startswith('clicked [2] button "Zoeken"') and "[1] link" in out


def test_click_refuses_a_stale_or_covered_item(fake):
    browser.browser_read()
    fake.point = "stale"
    with pytest.raises(browser.BrowserError, match="no longer on the page"):
        browser.browser_click(2)
    fake.point = "covered"
    with pytest.raises(browser.BrowserError, match="covered"):
        browser.browser_click(2)
    assert not [c for c in fake.calls if c[0] == "Input.dispatchMouseEvent"]


def test_type_replaces_the_text_and_can_press_enter(fake):
    browser.browser_read()
    out = browser.browser_type(3, "horror", enter=True)
    assert ("Input.insertText", {"text": "horror"}, "s-A") in fake.calls
    keys = [c[1]["type"] for c in fake.calls if c[0] == "Input.dispatchKeyEvent"]
    assert keys == ["keyDown", "keyUp"] and out.startswith("typed 6 characters into [3] and pressed Enter")


def test_scroll(fake):
    assert browser.browser_scroll("down").startswith("scrolled down")
    assert browser.browser_scroll("sideways") == "error: direction must be down or up"


def test_tiers_follow_the_label_the_page_shows(fake):
    browser.browser_read()
    click, type_ = tools.TOOLS["browser_click"], tools.TOOLS["browser_type"]
    assert click.tier_of({"index": 2}) == tools.CONFIRM
    assert click.tier_of({"index": 4}) == tools.NEVER  # "Nu betalen"
    assert click.tier_of({"index": 99}) == tools.CONFIRM  # unknown: asks
    assert type_.tier_of({"index": 3, "text": "x"}) == tools.CONFIRM
    assert type_.tier_of({"index": 5, "text": "x"}) == tools.NEVER  # a password field
    browser.remember('Tab: Inbox (https://mail.google.com/mail/u/0/)\n[1] textbox "Bericht"')
    assert type_.tier_of({"index": 1, "text": "hoi", "enter": True}) == tools.NEVER  # Enter sends a mail
    assert type_.tier_of({"index": 1, "text": "hoi"}) == tools.CONFIRM


def test_the_connection_comes_from_devtoolsactiveport(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_BROWSER_PROFILE", str(tmp_path))
    with pytest.raises(browser.BrowserError, match="brave://inspect"):
        browser._ws_url()
    (tmp_path / "DevToolsActivePort").write_text("9555\n/devtools/browser/abc\n", encoding="utf-8")

    def http(code):
        def urlopen(url, timeout=None):
            raise urllib.error.HTTPError(url, code, "x", None, None)
        return urlopen

    monkeypatch.setattr(browser.urllib.request, "urlopen", http(404))
    assert browser._ws_url() == "ws://127.0.0.1:9555/devtools/browser/abc"
    monkeypatch.setattr(browser.urllib.request, "urlopen", http(403))
    with pytest.raises(browser.BrowserError, match="click Allow"):
        browser._ws_url()


def test_one_yes_covers_the_site_and_the_question_names_the_button(fake, tmp_path):
    from iris.agent import Agent, Journal
    from iris.llm import Reply, ToolCall

    browser.browser_read()
    calls = [ToolCall("1", "browser_click", {"index": 2}), ToolCall("2", "browser_type", {"index": 3, "text": "a"})]
    replies = iter([Reply("", calls, {"role": "assistant", "content": ""}),
                    Reply("Klaar.", [], {"role": "assistant", "content": ""})])

    class LLM:
        def chat(self, messages, tools_):
            return next(replies)

    asked = []
    a = Agent(LLM(), lambda q: asked.append(q) or True, Journal(tmp_path / "j.jsonl"), dict(tools.TOOLS))
    a.handle("Zoek een horrorfilm op Netflix voor mij")
    assert asked == ["Klik op 'Zoeken' op www.netflix.com? Een ja geldt voor alle klikken en typen in "
                     "www.netflix.com tijdens deze opdracht."]


def test_search_runs_in_a_background_tab_that_is_closed_after(fake, monkeypatch):
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    assert browser.search("f1 winnaar") == fake.search["results"]
    create = [c[1] for c in fake.calls if c[0] == "Target.createTarget"][0]
    assert create["background"] is True and "q=f1+winnaar" in create["url"]
    assert ("Target.closeTarget", {"targetId": "S"}, None) in fake.calls


def test_search_stops_at_a_human_check_and_still_closes_the_tab(fake, monkeypatch):
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    fake.search = {"human": True, "results": []}
    with pytest.raises(browser.BrowserError, match="human check"):
        browser.search("f1")
    assert ("Target.closeTarget", {"targetId": "S"}, None) in fake.calls


def test_web_search_uses_the_browser_and_falls_back_to_duckduckgo(fake, monkeypatch):
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    assert "1. Uitslag GP Mexico\n   https://f1.example/mexico\n   Verstappen won." in tools.web_search("f1")

    def no_browser(query):
        raise browser.BrowserError("not allowed")

    monkeypatch.setattr(browser, "search", no_browser)
    monkeypatch.setattr("iris.web.search", lambda q: [{"title": "DDG", "url": "https://d.example", "snippet": "s"}])
    assert tools.web_search("f1").startswith("1. DDG")
    monkeypatch.setattr("iris.web.search", lambda q: (_ for _ in ()).throw(ValueError("human check")))
    assert tools.web_search("f1") == "error: search failed: human check"


def test_open_url_returns_the_new_page_so_no_read_step_is_needed(fake, monkeypatch):
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    out = tools.open_url("https://www.netflix.com/search?q=horror")
    assert out.startswith("opened https://www.netflix.com/search?q=horror\n\nTab: Home - Netflix")
    assert '[2] button "Zoeken"' in out and browser.item(2)["name"] == "Zoeken"  # the tiers know the page too
    create = [c[1] for c in fake.calls if c[0] == "Target.createTarget"][0]
    assert create == {"url": "https://www.netflix.com/search?q=horror"}  # in front, not a background tab


def test_open_url_without_browser_access_opens_the_default_browser(monkeypatch):
    opened = []

    def no_browser(url):
        raise browser.BrowserError("not allowed")

    monkeypatch.setattr(browser, "open_page", no_browser)
    monkeypatch.setattr(tools.os, "startfile", opened.append, raising=False)
    assert tools.open_url("https://example.com/") == "opened https://example.com/"
    assert opened == ["https://example.com/"]
    with pytest.raises(ValueError, match="only http and https"):
        tools.open_url("file:///C:/x")


def test_in_voice_mode_a_request_waits_briefly_for_the_watcher_instead_of_a_minute(monkeypatch):
    monkeypatch.setattr(browser, "_watching", browser.threading.Event())
    browser._watching.set()
    monkeypatch.setattr(browser, "WATCH_WAIT", 0.2)
    monkeypatch.setattr(browser, "_ws_url", lambda: pytest.fail("a request must not connect itself"))
    with pytest.raises(browser.BrowserError, match="click Allow"):
        browser.browser_read()


def test_the_watcher_connects_says_the_question_is_waiting_and_reconnects_after_a_restart(monkeypatch):
    import threading

    made, said, stop = [], [], threading.Event()

    class Slow:  # a handshake that waits for the user's click
        def __init__(self, url, handshake=60):
            time.sleep(0.15)
            self.alive = True
            made.append(self)

        def send(self, method, params=None, session=None, wait=15):
            if not self.alive:
                raise OSError("socket closed")  # the browser was closed
            return {}

    monkeypatch.setattr(browser, "_watching", threading.Event())
    monkeypatch.setattr(browser, "_ws_url", lambda: "ws://127.0.0.1:1/devtools/browser/x")
    monkeypatch.setattr(browser, "Connection", Slow)
    browser.watch(said.append, every=0.02, ask_after=0.05, stop=stop)
    deadline = time.perf_counter() + 5
    while not made and time.perf_counter() < deadline:
        time.sleep(0.01)
    while browser._conn is None and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert browser._conn is made[0] and said == [browser.ASKS]
    made[0].alive = False  # Brave restarts: the watcher notices and connects again
    while len(made) < 2 and time.perf_counter() < deadline:
        time.sleep(0.01)
    stop.set()
    assert len(made) == 2
