"""Safe web fetching for Iris: a page as plain text, standard library only.

http_get is the only door to the network in this package. It refuses local
addresses (localhost and private or reserved IP literals) so that content on
a web page can never point Iris at this machine, and it caps how much of a
page it will read.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

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
    with urllib.request.urlopen(request, timeout=timeout) as response:
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
    text = data.decode(charset or "utf-8", errors="replace")
    return final_url, content_type, text


def _refuse_local_host(host: str) -> None:
    """Raise ValueError for localhost and IP literals that point at this machine or its network."""
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("refusing a local address")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # not an IP literal; name resolution happens in urlopen, outside this check
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
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
def search(query: str, max_results: int = 5, get=http_get) -> list[dict]:
    """Search DuckDuckGo (html endpoint) and return up to max_results dicts with title, url and snippet."""
    page_url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    _, _, html_text = get(page_url)
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
        self._pending: dict | None = None  # last title link seen, waiting for its snippet
        self._title_tag: str | None = None
        self._title_href = ""
        self._title_parts: list[str] = []
        self._snippet_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = _attr_classes(attrs)
        href = dict(attrs).get("href") or ""
        if "result__a" in classes and self._title_tag is None:
            if _is_ad(href):
                self._pending = None  # an ad's snippet must not attach to the previous result
            else:
                self._title_tag = tag
                self._title_href = href
                self._title_parts = []
        elif "result__snippet" in classes and self._snippet_tag is None:
            self._snippet_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self._title_tag:
            result = {"title": _collapse("".join(self._title_parts)), "url": _unwrap_href(self._title_href),
                      "snippet": ""}
            self.results.append(result)
            self._pending = result
            self._title_tag = None
        if tag == self._snippet_tag:
            self._snippet_tag = None

    def handle_data(self, data: str) -> None:
        if self._title_tag is not None:
            self._title_parts.append(data)
        elif self._snippet_tag is not None and self._pending is not None:
            self._pending["snippet"] += data
