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
    monkeypatch.setattr(web, "_OPENER", type("Opener", (), {"open": staticmethod(fake.urlopen)})())
    return fake


@pytest.mark.parametrize("target", ["http://127.0.0.1:8765/api/journal", "http://localhost/", "file:///C:/x"])
def test_a_redirect_to_a_local_address_is_refused(target):
    handler = web._CheckedRedirects()
    req = urllib.request.Request("https://example.com/")
    with pytest.raises(ValueError):
        handler.redirect_request(req, None, 302, "Found", {}, target)


def test_a_redirect_to_a_public_address_is_followed():
    req = urllib.request.Request("https://example.com/")
    new = web._CheckedRedirects().redirect_request(req, None, 302, "Found", {}, "https://example.org/page")
    assert new is not None and new.full_url == "https://example.org/page"


def test_an_unknown_charset_falls_back_to_utf8(fake_urlopen):
    fake_urlopen.install(FakeResponse("héllo".encode(), content_type="text/html; charset=x-made-up"))
    assert web.http_get("https://example.com/")[2] == "héllo"


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
        "http://localhost./",
        "http://100.64.0.1/",
        "http://[ff02::1]/",
        "http://0.0.0.0:8765/",
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


def test_search_says_so_when_duckduckgo_asks_for_a_human_check():
    def get(url):
        return url, "text/html", "<html><body>Unfortunately, bots use DuckDuckGo too. Select all squares</body></html>"

    with pytest.raises(ValueError, match="do not retry"):
        web.search("F1 winner", get=get)


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


# --- read_text -----------------------------------------------------------------
ARTICLE = (Path(__file__).resolve().parent / "fixtures" / "article.html").read_text(encoding="utf-8")


def test_read_text_full_article():
    def get(url):
        return url, "text/html; charset=utf-8", ARTICLE

    out = web.read_text("http://example.com/article", get=get)
    assert out == (
        "Title: The Lighthouse Keeper's Log\n"
        "The Lighthouse Keeper's Log\n"
        "For one hundred years the light on Gull Point burned every night, and the keeper wrote down "
        "everything he saw.\n"
        "What the log says\n"
        "The entries are short. Weather, ships, oil levels, and once in a while a line about the gulls.\n"
        "Fog on the 3rd, two ships passed close to the shoal\n"
        "Lamp trimmed at dusk, wick replaced on the 9th\n"
        "A pod of dolphins followed the ferry for a mile\n"
        "The weather table\n"
        "Date\n"
        "Wind\n"
        "Sea state\n"
        "3 March\n"
        "NW 6\n"
        "Rough\n"
        "9 March\n"
        "E 3\n"
        "Moderate\n"
        "A sample entry\n"
        "14 March. Clear at dawn. One trawler in, two out. Oil low - ordered more by letter."
    )


def test_read_text_skips_nav_footer_script_style():
    def get(url):
        return url, "text/html", ARTICLE

    out = web.read_text("http://example.com/article", get=get)
    for gone in ("Home", "About", "Contact", "Harbour Notes", "Search the archive",
                 "Related:", "visitor number 48213", "font-family", "Privacy", "Terms"):
        assert gone not in out, f"{gone!r} should have been skipped"


def test_read_text_title_first_and_order():
    def get(url):
        return url, "text/html", ARTICLE

    lines = web.read_text("http://example.com/article", get=get).splitlines()
    assert lines[0] == "Title: The Lighthouse Keeper's Log"
    positions = [lines.index(x) for x in (
        "The Lighthouse Keeper's Log", "What the log says", "Fog on the 3rd, two ships passed close to the shoal",
        "The weather table", "A sample entry")]
    assert positions == sorted(positions)


def test_read_text_truncates_over_max_chars():
    def get(url):
        return url, "text/html", "<html><head><title>t</title></head><body>" + "".join(
            f"<p>line {i}</p>" for i in range(500)) + "</body></html>"

    out = web.read_text("http://example.com/long", max_chars=100, get=get)
    assert len(out) == 100 + len("\n[truncated]")
    assert out.endswith("\n[truncated]")


def test_read_text_at_max_chars_is_not_truncated():
    def get(url):
        return url, "text/html", "<html><body><p>abcde</p></body></html>"

    assert web.read_text("http://example.com/exact", max_chars=5, get=get) == "abcde"


def test_read_text_drops_a_line_that_repeats_the_previous_one():
    def get(url):
        return url, "text/html", ("<html><head><title>T</title></head>"
                                  "<body><p>Same</p><p>Same</p><p>Next</p></body></html>")

    assert web.read_text("http://example.com/dup", get=get) == "Title: T\nSame\nNext"


def test_read_text_plain_is_returned_as_is():
    def get(url):
        return url, "text/plain; charset=utf-8", "hello\nworld  twice"

    assert web.read_text("http://example.com/txt", get=get) == "hello\nworld  twice"


def test_read_text_refuses_a_pdf():
    def get(url):
        return url, "application/pdf", "%PDF-1.4"

    with pytest.raises(ValueError, match="not a web page \\(application/pdf\\)"):
        web.read_text("http://example.com/doc.pdf", get=get)


# --- web_read tool -------------------------------------------------------------
def test_web_read_tool_prefixes_source(monkeypatch):
    from iris import tools

    def fake(url, max_bytes=1024 * 1024, timeout=15.0):
        return ("http://example.com/real", "text/html",
                "<html><head><title>T</title></head><body><p>Body text.</p></body></html>")

    monkeypatch.setattr(web, "http_get", fake)
    assert tools.web_read("http://example.com/start") == "Source: http://example.com/real\nTitle: T\nBody text."


@pytest.mark.parametrize("exc", [OSError("connection refused"), ValueError("not a web page (application/pdf)")])
def test_web_read_tool_error_text(monkeypatch, exc):
    from iris import tools

    def boom(url, max_bytes=1024 * 1024, timeout=15.0):
        raise exc

    monkeypatch.setattr(web, "http_get", boom)
    assert tools.web_read("http://example.com/doc.pdf") == f"error: {exc}"


def test_web_read_is_a_safe_tool():
    from iris.tools import SAFE, TOOLS

    tool = TOOLS["web_read"]
    assert tool.tier == SAFE
    assert tool.required == ["url"]



@pytest.mark.parametrize("host,address", [("127.1", "127.0.0.1"), ("2130706433", "127.0.0.1"),
                                          ("0x7f000001", "127.0.0.1"), ("0", "0.0.0.0"),
                                          ("rebind.example", "192.168.1.1"), ("v6.example", "fe80::1%3")])
def test_a_name_or_old_form_that_resolves_locally_is_refused(monkeypatch, fake_urlopen, host, address):
    monkeypatch.setattr(web, "_resolve", lambda h: ["93.184.215.14", address] if h == host else [])
    with pytest.raises(ValueError, match="refusing a local address"):
        web.http_get(f"http://{host}:8765/api/journal")
    assert fake_urlopen.calls == []


def test_a_name_that_does_not_resolve_is_left_to_urlopen(monkeypatch, fake_urlopen):
    def nothing(host):
        raise OSError("no such name")

    monkeypatch.setattr(web, "_resolve", nothing)
    web.http_get("https://example.com/")
    assert len(fake_urlopen.calls) == 1
