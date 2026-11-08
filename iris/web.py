"""Safe web fetching for Iris: a page as plain text, standard library only.

http_get is the only door to the network in this package. It refuses local
addresses (localhost and private or reserved IP literals) so that content on
a web page can never point Iris at this machine, and it caps how much of a
page it will read.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from collections.abc import Callable
from html.parser import HTMLParser
from http.client import HTTPMessage
from typing import IO

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# A <meta ... charset=...> tag inside the first 2048 bytes of a page.
_META_CHARSET = re.compile(rb"""<meta[^>]{0,200}?charset=["']?([A-Za-z0-9_.\-]+)""", re.I)


def http_get(url: str, timeout: float = 15, max_bytes: int = 2_000_000) -> tuple[str, str, str]:
    """Fetch a web page and return (final url, content type without parameters, decoded text)."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http and https links")
    _refuse_local_host((parsed.hostname or "").lower())

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _OPENER.open(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("page too large")
        content_type_header = (response.headers.get("Content-Type") or "").strip()
        final_url = response.geturl()

    content_type = content_type_header.split(";")[0].strip()
    charset = _charset_from_header(content_type_header)
    if not charset:
        match = _META_CHARSET.search(data[:2048])
        if match:
            charset = match.group(1).decode("ascii")
    try:
        text = data.decode(charset or "utf-8", errors="replace")
    except LookupError:  # a charset name Python does not know
        text = data.decode("utf-8", errors="replace")
    return final_url, content_type, text


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another http(s) address that is not local: a public page that redirects to
    127.0.0.1 must not reach this PC's own services."""

    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str,
    ) -> urllib.request.Request | None:
        """Only http and https redirects to a non-local address are followed; anything else raises."""
        target = urllib.parse.urlsplit(newurl)
        if target.scheme not in ("http", "https"):
            raise ValueError("only http and https links")
        _refuse_local_host((target.hostname or "").lower())
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_CheckedRedirects)


def _resolve(host: str) -> list[str]:
    """Every address the system resolver gives for host, as urlopen will look it up."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]


def _local(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # an IPv6 zone (fe80::1%3) is not part of the address
    return not ip.is_global or ip.is_multicast


def _refuse_local_host(host: str) -> None:
    """Raise ValueError for a host that points at this machine or a network that is not the public internet:
    localhost, IP literals, and names (also old forms like 127.1 or 2130706433) whose addresses are not public.
    ponytail: urlopen resolves the name again, so a name whose DNS answer changes in between still passes."""
    host = host.rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("refusing a local address")
    try:
        addresses = [str(ipaddress.ip_address(host.strip("[]")))]  # an IP literal: no lookup
    except ValueError:
        addresses = []
    try:
        addresses = addresses or _resolve(host)
    except OSError:
        return  # the name does not resolve: urlopen fails on it with its own error
    if any(_local(a) for a in addresses):
        raise ValueError("refusing a local address")


def _charset_from_header(content_type_header: str) -> str | None:
    """The charset parameter of a Content-Type header, or None if there is none."""
    for part in content_type_header.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            value = part[len("charset="):].strip().strip("'\"")
            return value or None
    return None


# --- search --------------------------------------------------------------------
def search(query: str, max_results: int = 5, get: Callable[[str], tuple[str, str, str]] = http_get) -> list[dict]:
    """Search DuckDuckGo (html endpoint) and return up to max_results dicts with title, url and snippet."""
    page_url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    _, _, html_text = get(page_url)
    if "bots use DuckDuckGo too" in html_text or "anomaly-modal" in html_text:
        # its human check, not an empty result: saying so stops the model from retrying search after search
        raise ValueError("DuckDuckGo asks for a human check (it took Iris for a bot), so web search is unavailable "
                         "right now; do not retry it")
    parser = _ResultParser()
    parser.feed(html_text)
    results = parser.results[:max_results]
    for result in results:
        result["snippet"] = _collapse(result["snippet"])
    return results


def _collapse(text: str) -> str:
    """Every run of whitespace becomes a single space."""
    return " ".join(text.split())


def _is_ad(href: str) -> bool:
    """DuckDuckGo ad links point at y.js or carry an ad_domain parameter."""
    return "duckduckgo.com/y.js" in href or "ad_domain" in href


def _unwrap_href(href: str) -> str:
    """The target behind a DuckDuckGo redirect link; plain http(s) links come back unchanged."""
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
    uddg = params.get("uddg", [""])[0]
    return uddg or href


def _attr_classes(attrs: list[tuple[str, str | None]]) -> set[str]:
    for name, value in attrs:
        if name == "class" and value:
            return set(value.split())
    return set()


class _ResultParser(HTMLParser):
    """Collects result links (title, url) from html.duckduckgo.com and the snippet that follows each."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        self._open_result: dict | None = None  # last title link seen, waiting for its snippet; HTMLParser owns _pending
        self._title_tag: str | None = None
        self._title_href = ""
        self._title_parts: list[str] = []
        self._snippet_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Start collecting a result's title link or its snippet; an ad clears the pending result."""
        classes = _attr_classes(attrs)
        href = dict(attrs).get("href") or ""
        if "result__a" in classes and self._title_tag is None:
            if _is_ad(href):
                self._open_result = None  # an ad's snippet must not attach to the previous result
            else:
                self._title_tag = tag
                self._title_href = href
                self._title_parts = []
        elif "result__snippet" in classes and self._snippet_tag is None:
            self._snippet_tag = tag

    def handle_endtag(self, tag: str) -> None:
        """A closed title link becomes a result; a closed snippet ends its collection."""
        if tag == self._title_tag:
            result = {"title": _collapse("".join(self._title_parts)), "url": _unwrap_href(self._title_href),
                      "snippet": ""}
            self.results.append(result)
            self._open_result = result
            self._title_tag = None
        if tag == self._snippet_tag:
            self._snippet_tag = None

    def handle_data(self, data: str) -> None:
        """Text inside the title link goes to its parts; text inside a snippet goes to the pending result."""
        if self._title_tag is not None:
            self._title_parts.append(data)
        elif self._snippet_tag is not None and self._open_result is not None:
            self._open_result["snippet"] += data


# --- read_text -----------------------------------------------------------------
# No "form": ASP.NET pages wrap the whole body in one. No "option": it often has no end tag, which would skip the
# rest of the page, and "select" already skips its options.
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "template",
                        "select", "button"})
_BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "p", "li", "td", "th", "pre"})


class _TextExtractor(HTMLParser):
    """Collects a page's readable text: the title, then headings, paragraphs, list items, table cells and pre blocks."""

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.lines: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._block_tag: str | None = None
        self._block_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Start skipping at a skip tag, or start collecting the title or a block of text."""
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title" and not self._in_title:
            self._in_title = True
            self._title_parts = []
        elif tag in _BLOCK_TAGS:
            self._flush_block()
            self._block_tag = tag
            self._block_parts = []

    def handle_endtag(self, tag: str) -> None:
        """Leave a skip tag, or finish the title or the current block of text."""
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title" and self._in_title:
            self.title = _collapse("".join(self._title_parts))
            self._in_title = False
        elif tag == self._block_tag:
            self._flush_block()

    def handle_data(self, data: str) -> None:
        """Text goes to the title or the current block, unless we are inside a skipped tag."""
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        elif self._block_tag is not None:
            self._block_parts.append(data)

    def _flush_block(self) -> None:
        line = _collapse("".join(self._block_parts))
        self._block_tag = None
        self._block_parts = []
        if line:
            self.lines.append(line)


def read_text(url: str, max_chars: int = 6000, get: Callable[[str], tuple[str, str, str]] = http_get) -> str:
    """Fetch a page and return its readable text.

    The title comes first as "Title: ...", then headings (h1 to h4), paragraphs, list items, table cells and pre
    blocks in document order, each on its own line. Everything inside script, style, noscript, svg, nav, header,
    footer, aside, template, select and button is skipped; form content is kept because ASP.NET pages wrap
    the whole body in one <form>. text/plain comes back as is; any other type raises ValueError.
    """
    _, content_type, body = get(url)
    media = content_type.split(";")[0].strip().lower()
    if media == "text/plain":
        out = body
    elif media in ("text/html", "application/xhtml+xml"):
        extractor = _TextExtractor()
        extractor.feed(body)
        extractor.close()
        lines: list[str] = []
        for line in ([f"Title: {extractor.title}"] if extractor.title else []) + extractor.lines:
            if not line or (lines and line == lines[-1]):
                continue
            lines.append(line)
        out = "\n".join(lines)
    else:
        raise ValueError(f"not a web page ({content_type})")
    if len(out) > max_chars:
        out = out[:max_chars] + "\n[truncated]"
    return out
