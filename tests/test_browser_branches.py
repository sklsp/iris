"""The connection setup of iris/browser.py: _ws_url against a faked DevToolsActivePort file and a faked
urlopen, and Connection.__init__ with no websocket module or a fake one. No real browser, socket or network."""

import json
import sys
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
