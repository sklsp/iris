"""The connection setup of iris/browser.py: _ws_url against a faked DevToolsActivePort file and a faked
urlopen, and Connection.__init__ with no websocket module or a fake one. No real browser, socket or network."""

import json
import sys
import threading
import time
import types
import urllib.error

import pytest

from iris import browser


def _port_file(tmp_path, content):
    """A DevToolsActivePort file in the profile IRIS_BROWSER_PROFILE points at for this test."""
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "DevToolsActivePort").write_text(content, encoding="utf-8")
    return str(profile)


def _http_error(code):
    return urllib.error.HTTPError("http://127.0.0.1:9222/json/version", code, "error", {}, None)


class VersionReply:  # the /json/version answer as a context manager json.load can read
    def __init__(self, url):
        self._body = json.dumps({"webSocketDebuggerUrl": url}).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_port_file_whose_first_line_is_not_a_number_is_skipped(tmp_path, monkeypatch):
    profile = _port_file(tmp_path, "not-a-port\n/devtools/browser/x")
    monkeypatch.setenv("IRIS_BROWSER_PROFILE", profile)
    with pytest.raises(browser.BrowserError, match="does not allow remote control yet"):
        browser._ws_url()


def test_the_ws_url_comes_from_the_json_version_answer(tmp_path, monkeypatch):
    profile = _port_file(tmp_path, "9222\n/devtools/browser/abc")
    monkeypatch.setenv("IRIS_BROWSER_PROFILE", profile)
    asked = []

    def fake_urlopen(url, timeout=1):
        asked.append((url, timeout))
        return VersionReply("ws://127.0.0.1:9222/devtools/browser/abc")

    monkeypatch.setattr(browser.urllib.request, "urlopen", fake_urlopen)
    assert browser._ws_url() == "ws://127.0.0.1:9222/devtools/browser/abc"
    assert asked == [("http://127.0.0.1:9222/json/version", 1)]


def test_a_403_says_the_browser_is_waiting_for_the_allow_click(tmp_path, monkeypatch):
    profile = _port_file(tmp_path, "9222\n/devtools/browser/abc")
    monkeypatch.setenv("IRIS_BROWSER_PROFILE", profile)

    def fake_urlopen(url, timeout=1):
        raise _http_error(403)

    monkeypatch.setattr(browser.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(browser.BrowserError) as exc:
        browser._ws_url()
    assert str(exc.value) == "the browser is waiting for you to click Allow on its remote debugging question"


def test_a_404_falls_back_to_the_ws_path_from_the_port_file(tmp_path, monkeypatch):
    profile = _port_file(tmp_path, "9222\n/devtools/browser/abc")
    monkeypatch.setenv("IRIS_BROWSER_PROFILE", profile)

    def fake_urlopen(url, timeout=1):
        raise _http_error(404)

    monkeypatch.setattr(browser.urllib.request, "urlopen", fake_urlopen)
    assert browser._ws_url() == "ws://127.0.0.1:9222/devtools/browser/abc"


def test_a_stale_port_file_whose_request_fails_is_skipped(tmp_path, monkeypatch):
    profile = _port_file(tmp_path, "9222\n/devtools/browser/abc")
    monkeypatch.setenv("IRIS_BROWSER_PROFILE", profile)

    def fake_urlopen(url, timeout=1):
        raise OSError("connection refused")  # the browser wrote the file and closed since

    monkeypatch.setattr(browser.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(browser.BrowserError, match="does not allow remote control yet"):
        browser._ws_url()


def test_a_missing_websocket_module_says_to_install_the_browser_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "websocket", None)  # import websocket then raises ImportError
    with pytest.raises(browser.BrowserError) as exc:
        browser.Connection("ws://127.0.0.1:9/devtools/browser/x")
    assert str(exc.value) == "install the browser extra first: pip install -e .[browser]"


def test_the_connection_passes_the_handshake_as_timeout_and_sets_fifteen_seconds(monkeypatch):
    created = {}

    class Ws:  # records what create_connection and settimeout are told
        def __init__(self):
            self.timeouts = []

        def settimeout(self, seconds):
            self.timeouts.append(seconds)

    class WebSocketTimeoutException(Exception):
        pass

    mod = types.ModuleType("websocket")

    def create_connection(url, timeout=None, suppress_origin=False):
        created["url"], created["timeout"], created["suppress_origin"] = url, timeout, suppress_origin
        return Ws()

    mod.create_connection = create_connection
    mod.WebSocketTimeoutException = WebSocketTimeoutException
    monkeypatch.setitem(sys.modules, "websocket", mod)
    conn = browser.Connection("ws://127.0.0.1:9/devtools/browser/x", handshake=45)
    assert created == {"url": "ws://127.0.0.1:9/devtools/browser/x", "timeout": 45, "suppress_origin": True}
    assert conn.ws.timeouts == [15]
    assert conn._id == 0
    assert conn._slow == (TimeoutError, WebSocketTimeoutException)


class _WsFailure(Exception):  # stands in for the websocket library's own exception class
    pass


def _ws_module_raising(error: Exception) -> types.ModuleType:
    """A fake websocket module whose create_connection raises `error`."""
    mod = types.ModuleType("websocket")

    def create_connection(url, timeout=None, suppress_origin=False):
        raise error

    mod.create_connection = create_connection
    mod.WebSocketException = _WsFailure
    return mod


def test_a_refused_devtools_port_becomes_a_browser_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "websocket", _ws_module_raising(OSError("connection refused")))
    with pytest.raises(browser.BrowserError) as exc:
        browser.Connection("ws://127.0.0.1:9/devtools/browser/x")
    assert str(exc.value) == ("could not reach the browser's DevTools port; make sure the browser is open "
                              "and has allowed remote control")
    assert isinstance(exc.value.__cause__, OSError)


def test_a_timed_out_devtools_port_becomes_a_browser_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "websocket", _ws_module_raising(TimeoutError("timed out")))
    with pytest.raises(browser.BrowserError) as exc:
        browser.Connection("ws://127.0.0.1:9/devtools/browser/x")
    assert isinstance(exc.value.__cause__, TimeoutError)


def test_the_websocket_librarys_own_failure_becomes_a_browser_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "websocket", _ws_module_raising(_WsFailure("bad handshake")))
    with pytest.raises(browser.BrowserError) as exc:
        browser.Connection("ws://127.0.0.1:9/devtools/browser/x")
    assert isinstance(exc.value.__cause__, _WsFailure)


def test_web_search_takes_its_fallback_once_when_the_connect_fails(monkeypatch):
    from iris import tools, web

    browser._watching.clear()  # an earlier test may have left the watcher flag set
    browser._conn = None
    monkeypatch.setitem(sys.modules, "websocket", _ws_module_raising(OSError("connection refused")))
    monkeypatch.setattr(browser, "_ws_url", lambda: "ws://127.0.0.1:9/devtools/browser/x")
    calls = []

    def fake_search(query):
        calls.append(query)
        return [{"title": "A result.", "url": "https://a.example/", "snippet": "It."}]

    monkeypatch.setattr(web, "search", fake_search)
    out = tools.web_search("sinners 2025 film")
    assert calls == ["sinners 2025 film"]  # the DuckDuckGo fallback ran exactly once
    assert out.startswith("1. A result.")


class ScriptedWs:  # answers recv() with prepared replies, one per call
    def __init__(self, replies):
        self.replies = list(replies)

    def send(self, text):
        pass

    def settimeout(self, seconds):
        pass

    def recv(self):
        return self.replies.pop(0)


def _connection(monkeypatch, ws):
    """A real browser.Connection whose websocket is the scripted `ws`."""
    mod = types.ModuleType("websocket")
    mod.create_connection = lambda url, timeout=None, suppress_origin=False: ws

    class WebSocketTimeoutException(Exception):
        pass

    mod.WebSocketTimeoutException = WebSocketTimeoutException
    monkeypatch.setitem(sys.modules, "websocket", mod)
    return browser.Connection("ws://127.0.0.1:9/devtools/browser/x")


def test_a_reply_with_an_error_says_which_command_failed_and_why(monkeypatch):
    ws = ScriptedWs([json.dumps({"id": 1, "error": {"message": "no such tab"}})])
    conn = _connection(monkeypatch, ws)
    with pytest.raises(browser.BrowserError) as exc:
        conn.send("Target.activate")
    assert str(exc.value) == "Target.activate failed: no such tab"


def test_events_and_replies_for_other_ids_are_skipped_until_the_own_answer(monkeypatch):
    ws = ScriptedWs([json.dumps({"method": "Page.loadEventFired"}),
                     json.dumps({"id": 9, "result": {"other": True}}),
                     json.dumps({"id": 1, "result": {"ok": True}})])
    conn = _connection(monkeypatch, ws)
    assert conn.send("Runtime.evaluate") == {"ok": True}


def test_a_request_waits_for_the_watcher_to_connect_and_gets_its_connection(monkeypatch):
    monkeypatch.setattr(browser, "WATCH_WAIT", 5.0)  # generous: a loaded CI runner can start the thread late
    browser._watching.set()
    fake = object()

    def connect_later():
        time.sleep(0.1)
        browser._conn = fake

    thread = threading.Thread(target=connect_later)
    thread.start()
    try:
        assert browser._connect() is fake
    finally:
        thread.join()


def test_a_request_without_the_watcher_connects_itself_and_clears_stale_sessions(monkeypatch):
    made = []

    class FakeConn:
        def __init__(self, url, handshake=60):
            made.append(url)

    monkeypatch.setattr(browser, "_ws_url", lambda: "ws://127.0.0.1:9/devtools/browser/x")
    monkeypatch.setattr(browser, "Connection", FakeConn)
    browser._sessions["A"] = "s-A"  # stale sessions from a previous connection
    conn = browser._connect()
    assert isinstance(conn, FakeConn) and made == ["ws://127.0.0.1:9/devtools/browser/x"]
    assert browser._sessions == {}


def test_the_watcher_keeps_its_pause_when_a_check_says_busy(monkeypatch):
    pauses = []
    stop = threading.Event()

    def fake_sleep(seconds):
        pauses.append(seconds)
        if len(pauses) >= 2:
            stop.set()

    monkeypatch.setattr(browser.time, "sleep", fake_sleep)

    class BusyConn:
        def send(self, method, params=None, session=None, wait=15):
            raise browser.BrowserError("the browser did not answer Browser.getVersion within 5 s")

    monkeypatch.setattr(browser, "_conn", BusyConn())
    thread = browser.watch(every=7, stop=stop)
    thread.join(timeout=5)
    assert pauses == [7, 7]  # a busy tab is looked at again soon, the pause does not grow


def test_the_watcher_doubles_its_pause_to_thirty_seconds_after_a_broken_connect(monkeypatch):
    pauses = []
    stop = threading.Event()

    def fake_sleep(seconds):
        pauses.append(seconds)
        if len(pauses) >= 2:
            stop.set()

    monkeypatch.setattr(browser.time, "sleep", fake_sleep)
    monkeypatch.setattr(browser, "_ws_url", lambda: "ws://127.0.0.1:9/devtools/browser/x")

    def broken_connect(url, handshake=60):
        raise OSError("socket broken")  # the question was refused or left open

    monkeypatch.setattr(browser, "Connection", broken_connect)
    thread = browser.watch(every=5, stop=stop)
    thread.join(timeout=5)
    assert pauses == [30, 60]  # max(5*2, 30), then doubled again while the connect keeps failing


def test_a_dropped_socket_connects_once_more_and_retries_the_command(monkeypatch):
    conns = []

    class FlakyConn:
        def __init__(self, url, handshake=60):
            self.dead = len(conns) == 0  # the first connection is already dropped
            conns.append(self)

        def send(self, method, params=None, session=None, wait=15):
            if self.dead:
                raise OSError("socket closed")
            return {"ok": True}

    monkeypatch.setattr(browser, "_ws_url", lambda: "ws://127.0.0.1:9/devtools/browser/x")
    monkeypatch.setattr(browser, "Connection", FlakyConn)
    assert browser._call("Target.getTargets") == {"ok": True}
    assert len(conns) == 2


def test_a_script_that_throws_on_the_page_says_what_it_threw(monkeypatch):
    def fake_call(method, params=None, session=None, wait=15):
        return {"exceptionDetails": {"text": "Uncaught TypeError: els is not defined"}}

    monkeypatch.setattr(browser, "_call", fake_call)
    with pytest.raises(browser.BrowserError) as exc:
        browser._eval("s-A", "window.__iris = els")
    assert str(exc.value) == "the page refused the script: Uncaught TypeError: els is not defined"


class Clock:  # a perf_counter that advances one second per call, so wait loops end fast
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        t = self.t
        self.t += 1.0
        return t


L = chr(10)  # a line break, for building page text without escape sequences


def test_no_open_tab_says_so(monkeypatch):
    def fake_call(method, params=None, session=None, wait=15):
        return {"targetInfos": [{"targetId": "D", "type": "page", "title": "DevTools", "url": "devtools://x"},
                                {"targetId": "W", "type": "service_worker", "title": "", "url": ""}]}

    monkeypatch.setattr(browser, "_call", fake_call)
    with pytest.raises(browser.BrowserError, match="the browser has no open tab"):
        browser._tab()


def test_a_named_tab_that_does_not_answer_raises(monkeypatch):
    def fake_call(method, params=None, session=None, wait=15):
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": "A", "type": "page", "title": "Netflix",
                                     "url": "https://www.netflix.com/"}]}
        if method == "Target.attachToTarget":
            return {"sessionId": "s-A"}
        raise browser.BrowserError("the browser did not answer Runtime.evaluate within 2 s")

    monkeypatch.setattr(browser, "_call", fake_call)
    with pytest.raises(browser.BrowserError, match="did not answer"):
        browser._tab("Netflix")


def test_a_frozen_background_tab_is_skipped_and_its_session_dropped(monkeypatch):
    def fake_call(method, params=None, session=None, wait=15):
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": "B", "type": "page", "title": "Frozen",
                                     "url": "https://frozen.example/"},
                                    {"targetId": "A", "type": "page", "title": "Netflix",
                                     "url": "https://www.netflix.com/"}]}
        if method == "Target.attachToTarget":
            return {"sessionId": "s-" + params["targetId"]}
        if session == "s-B":
            raise browser.BrowserError("the browser did not answer Runtime.evaluate within 2 s")
        return {"result": {"value": 2}}

    monkeypatch.setattr(browser, "_call", fake_call)
    assert browser._tab() == "s-A"
    assert browser._sessions == {"A": "s-A"}  # the frozen tab's session is dropped


def test_a_reused_tab_is_attached_and_waited_for(monkeypatch):
    url = "https://www.netflix.com/search?q=sinners"
    old = "https://www.netflix.com/browse"
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    monkeypatch.setattr(browser.time, "perf_counter", Clock())

    evals = [old,            # location.href before navigating
             "loading",      # readyState: not complete yet
             "complete", old,  # still the old page
             "complete", url,  # on the new page now, but the read fails once
             "complete", url]  # and this time it reads

    def fake_eval(session, expression, wait=15):
        if expression == "location.href":
            return evals.pop(0)
        if "readyState" in expression:
            return evals.pop(0)
        raise AssertionError(expression)

    monkeypatch.setattr(browser, "_eval", fake_eval)
    reads = []

    def fake_read(session):
        reads.append(session)
        if len(reads) == 1:
            raise browser.BrowserError("still navigating")
        return ("Tab: Netflix (https://www.netflix.com/search?q=sinners)" + L +
                '[1] link "a"' + L + '[2] link "b"' + L + '[3] link "c"')

    monkeypatch.setattr(browser, "_read", fake_read)

    def fake_call(method, params=None, session=None, wait=15):
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": "S", "type": "page", "title": "Netflix", "url": old}]}
        if method == "Target.attachToTarget":
            assert params["targetId"] == "S"  # the reused tab lost its session and gets attached again
            return {"sessionId": "s-S"}
        return {}

    monkeypatch.setattr(browser, "_call", fake_call)
    browser._opened["www.netflix.com"] = "S"
    out = browser.open_page(url, wait=8)
    assert out.startswith("opened https://www.netflix.com/search?q=sinners" + L + L)
    assert '[3] link "c"' in out


def test_a_page_that_never_finishes_loading_is_reported_as_not_read_yet(monkeypatch):
    url = "https://slow.example/page"
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    monkeypatch.setattr(browser.time, "perf_counter", Clock())

    def fake_eval(session, expression, wait=15):
        if "readyState" in expression:
            return "loading"  # never complete
        raise AssertionError(expression)

    monkeypatch.setattr(browser, "_eval", fake_eval)

    def fake_read(session):
        raise browser.BrowserError("still navigating")  # the one last read fails too

    monkeypatch.setattr(browser, "_read", fake_read)

    def fake_call(method, params=None, session=None, wait=15):
        if method == "Target.createTarget":
            return {"targetId": "N"}
        if method == "Target.attachToTarget":
            return {"sessionId": "s-N"}
        raise AssertionError(method)

    monkeypatch.setattr(browser, "_call", fake_call)
    expected = f"opened {url} (could not read it yet: still navigating; call browser_read)"
    assert browser.open_page(url, wait=2) == expected


def test_a_result_look_that_fails_is_tried_again(monkeypatch):
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    monkeypatch.setattr(browser.time, "perf_counter", Clock())

    looks = [None,  # the first look raises BrowserError (still navigating)
             "not json yet",  # the second look is not JSON yet (ValueError)
             '{"human": false, "results": [{"title": "Sinners (2025)", '
             '"url": "https://www.imdb.com/title/tt31496718/", "snippet": "A film."}]}']

    def fake_eval(session, expression, wait=15):
        if not looks:
            raise AssertionError("looked more times than scripted")
        look = looks.pop(0)
        if look is None:
            raise browser.BrowserError("still navigating")
        return look

    monkeypatch.setattr(browser, "_eval", fake_eval)

    def fake_call(method, params=None, session=None, wait=15):
        if method == "Target.createTarget":
            assert params["background"] is True  # the search runs in a background tab
            return {"targetId": "T"}
        if method == "Target.attachToTarget":
            return {"sessionId": "s-T"}
        if method == "Target.closeTarget":
            return {}
        raise AssertionError(method)

    monkeypatch.setattr(browser, "_call", fake_call)
    results = browser.search("sinners 2025 film", wait=8)
    assert results == [{"title": "Sinners (2025)", "url": "https://www.imdb.com/title/tt31496718/",
                        "snippet": "A film."}]


def test_a_human_check_is_not_solved(monkeypatch):
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    monkeypatch.setattr(browser.time, "perf_counter", Clock())

    def fake_eval(session, expression, wait=15):
        return '{"human": true, "results": []}'

    monkeypatch.setattr(browser, "_eval", fake_eval)
    closed = []

    def fake_call(method, params=None, session=None, wait=15):
        if method == "Target.createTarget":
            return {"targetId": "T"}
        if method == "Target.attachToTarget":
            return {"sessionId": "s-T"}
        if method == "Target.closeTarget":
            closed.append(params["targetId"])
            return {}
        raise AssertionError(method)

    monkeypatch.setattr(browser, "_call", fake_call)
    with pytest.raises(browser.BrowserError) as exc:
        browser.search("anything")
    assert str(exc.value) == "the search engine asks for a human check; Iris does not solve those"
    assert closed == ["T"]  # the background tab is still closed


def test_a_background_tab_that_will_not_close_is_let_go(monkeypatch):
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    monkeypatch.setattr(browser.time, "perf_counter", Clock())

    def fake_eval(session, expression, wait=15):
        return '{"human": false, "results": [{"title": "Sinners (2025)", ' \
               '"url": "https://www.imdb.com/title/tt31496718/", "snippet": "A film."}]}'

    monkeypatch.setattr(browser, "_eval", fake_eval)

    def fake_call(method, params=None, session=None, wait=15):
        if method == "Target.createTarget":
            return {"targetId": "T"}
        if method == "Target.attachToTarget":
            return {"sessionId": "s-T"}
        if method == "Target.closeTarget":
            raise browser.BrowserError("the tab is already gone")  # and that is ignored
        raise AssertionError(method)

    monkeypatch.setattr(browser, "_call", fake_call)
    results = browser.search("sinners 2025 film")
    assert results[0]["title"] == "Sinners (2025)"


# --- _format / item / _settle ---------------------------------------------------
def test_format_says_nothing_visible_when_the_page_has_no_items():
    out = browser._format({"title": "T", "url": "https://x.example/"})
    assert out == ("Tab: T (https://x.example/)" + L +
                   "(no buttons, links or fields visible; scroll, or wait for the page to load)")


def test_item_with_a_non_number_returns_an_empty_dict():
    assert browser.item("third") == {}  # ValueError from int()
    assert browser.item(None) == {}     # TypeError from int()


def test_settle_pauses_then_polls_while_the_page_is_still_loading(monkeypatch):
    sleeps = []
    clock = [0.0]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(browser.time, "sleep", fake_sleep)
    monkeypatch.setattr(browser.time, "monotonic", lambda: clock[0])
    states = iter(["loading", "loading", "complete"])
    monkeypatch.setattr(browser, "_eval", lambda session, expression, wait=15: next(states))
    browser._settle("s-1")
    assert sleeps == [0.4, 0.1, 0.1]


