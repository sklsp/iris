"""Tests for iris.web.http_get with a fake urlopen (no network)."""

import urllib.request
from pathlib import Path

import pytest

from iris import web


class FakeResponse:
    def __init__(self, body=b"", content_type="text/html", final_url="https://example.com/"):
        self._body = body
        self.headers = {"Content-Type": content_type} if content_type else {}
        self.final_url = final_url

    def geturl(self):
        return self.final_url

    def read(self, size=None):
        return self._body if size is None else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_urlopen(monkeypatch):
    """Install a fake urllib.request.urlopen; the returned object records every call."""

    class _Fake:
        def __init__(self):
            self.calls = []
            self.response = FakeResponse()

        def install(self, response: FakeResponse):
            self.response = response

        def urlopen(self, request, timeout=None):
            self.calls.append((request, timeout))
            return self.response

    fake = _Fake()
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    return fake


# --- normal page ---------------------------------------------------------------
def test_normal_page_returns_url_type_and_text(fake_urlopen):
    body = b"<html><body>hello</body></html>"
    fake_urlopen.install(FakeResponse(body=body, content_type="text/html; charset=utf-8", final_url="https://example.com/a"))
    url, ctype, text = web.http_get("https://example.com/a")
    assert (url, ctype, text) == ("https://example.com/a", "text/html", "<html><body>hello</body></html>")


def test_sends_browser_user_agent_and_default_timeout(fake_urlopen):
    fake_urlopen.install(FakeResponse(body=b"ok"))
    web.http_get("http://example.com/")
    request, timeout = fake_urlopen.calls[0]
    user_agent = {k.lower(): v for k, v in request.headers.items()}["user-agent"]  # Request capitalizes keys
    assert user_agent.startswith("Mozilla/5.0")
    assert "Chrome" in user_agent
    assert timeout == 15


def test_custom_timeout_is_passed_on(fake_urlopen):
    fake_urlopen.install(FakeResponse(body=b"ok"))
    web.http_get("http://example.com/", timeout=7.5)
    assert fake_urlopen.calls[0][1] == 7.5


# --- charset -------------------------------------------------------------------
def test_charset_from_header(fake_urlopen):
    body = "café".encode("iso-8859-1")
    fake_urlopen.install(FakeResponse(body=body, content_type="text/html; charset=iso-8859-1"))
    _, ctype, text = web.http_get("http://example.com/latin")
    assert (ctype, text) == ("text/html", "café")


@pytest.mark.parametrize(
    "meta",
    [b'<head><meta charset="utf-8"></head>', b"<head><meta charset='iso-8859-1'></head>", b"<meta charset=utf-8>"],
)
def test_charset_from_meta_tag(fake_urlopen, meta):
    if b"iso-8859-1" in meta:
        body = meta + "café".encode("iso-8859-1")
        expected = "café"
    else:
        body = meta + "héllo".encode()
        expected = "héllo"
    fake_urlopen.install(FakeResponse(body=body, content_type="text/html"))
    _, _, text = web.http_get("http://example.com/meta")
    assert text.endswith(expected)


def test_meta_beyond_first_2048_bytes_is_not_seen(fake_urlopen):
    body = b"x" * 3000 + b'<meta charset="iso-8859-1">' + "café".encode("iso-8859-1")
    fake_urlopen.install(FakeResponse(body=body, content_type="text/html"))
    _, _, text = web.http_get("http://example.com/late-meta")
    assert text.endswith("caf\ufffd")  # fell back to utf-8 with replacement


def test_invalid_bytes_decode_with_replacement(fake_urlopen):
    fake_urlopen.install(FakeResponse(body=b"\xff\xfe broken", content_type="text/html"))
    _, _, text = web.http_get("http://example.com/broken")
    assert "\ufffd" in text


# --- refusals ------------------------------------------------------------------
@pytest.mark.parametrize("url", ["file:///C:/page.html", "ftp://example.com/page"])
def test_non_http_schemes_refused(url):
    with pytest.raises(ValueError, match="only http and https links"):
        web.http_get(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://LOCALHOST:8080/x",
        "https://sub.localhost/page",
        "http://127.0.0.1/",
        "http://192.168.1.10/share",
        "http://10.0.0.5/",
        "http://169.254.16.4/",
        "https://[::1]/",
    ],
)
def test_local_hosts_refused(url):
    with pytest.raises(ValueError, match="refusing a local address"):
        web.http_get(url)


# --- size limit -----------------------------------------------------------------
def test_page_larger_than_max_bytes_refused(fake_urlopen):
    fake_urlopen.install(FakeResponse(body=b"0123456789ABCDEF"))
    with pytest.raises(ValueError, match="page too large"):
        web.http_get("http://example.com/big", max_bytes=10)


def test_page_of_exactly_max_bytes_is_fine(fake_urlopen):
    fake_urlopen.install(FakeResponse(body=b"0123456789"))
    _, _, text = web.http_get("http://example.com/exact", max_bytes=10)
    assert text == "0123456789"


# --- search --------------------------------------------------------------------
FIXTURE = Path(__file__).parent / "fixtures" / "ddg_results.html"


def _fixture_get(url):
    assert url.startswith("https://html.duckduckgo.com/html/?q=")
    return ("https://html.duckduckgo.com/html/", "text/html", FIXTURE.read_text(encoding="utf-8"))


def test_search_returns_three_results_in_order_with_redirects_unwrapped():
    results = web.search("test query", get=_fixture_get)
    assert [r["url"] for r in results] == [
        "https://www.example.com/first",
        "https://www.example.org/second",
        "https://www.example.com/third",
    ]
    assert [r["title"] for r in results] == ["First example page", "Second example page", "Third example page"]


def test_search_snippets_collapse_whitespace_and_drop_markup():
    results = web.search("test query", get=_fixture_get)
    assert results[0]["snippet"] == "The first result about example topics, with a bold word inside."
    assert results[1]["snippet"] == "The second result, a plain snippet without any markup at all."
    assert results[2]["snippet"] == "The third result with odd spacing and a line break."


def test_search_max_results():
    results = web.search("test query", max_results=2, get=_fixture_get)
    assert len(results) == 2
    assert [r["title"] for r in results] == ["First example page", "Second example page"]


def test_search_no_results():
    def get(url):
        return url, "text/html", "<html><body>no matches</body></html>"

    assert web.search("nothing here", get=get) == []


def test_search_plain_http_href_is_used_as_is():
    html = '<a class="result__a" href="https://www.example.com/direct">Direct</a>'

    def get(url):
        return url, "text/html", html

    assert web.search("direct", get=get)[0]["url"] == "https://www.example.com/direct"


def test_search_encodes_the_query():
    seen = []

    def get(url):
        seen.append(url)
        return url, "text/html", ""

    web.search("a b & c", get=get)
    assert seen == ["https://html.duckduckgo.com/html/?q=a+b+%26+c"]



# --- web_search tool -----------------------------------------------------------
def test_web_search_tool_lists_numbered_results(monkeypatch):
    from iris import tools

    monkeypatch.setattr(web, "search", lambda query: [
        {"title": "First example page", "url": "https://www.example.com/first", "snippet": "The first result."},
        {"title": "Second example page", "url": "https://www.example.org/second", "snippet": "The second result."},
    ])
    assert tools.web_search("test query") == (
        "1. First example page\n   https://www.example.com/first\n   The first result.\n"
        "2. Second example page\n   https://www.example.org/second\n   The second result."
    )


def test_web_search_tool_no_results(monkeypatch):
    from iris import tools

    monkeypatch.setattr(web, "search", lambda query: [])
    assert tools.web_search("nothing") == "No results for 'nothing'."


@pytest.mark.parametrize("exc", [OSError("boom"), ValueError("page too large")])
def test_web_search_tool_error_text(monkeypatch, exc):
    from iris import tools

    def boom(query):
        raise exc

    monkeypatch.setattr(web, "search", boom)
    assert tools.web_search("test query") == f"error: search failed: {exc}"


def test_web_search_is_a_safe_tool():
    from iris.tools import SAFE, TOOLS

    tool = TOOLS["web_search"]
    assert tool.tier == SAFE
    assert tool.required == ["query"]

