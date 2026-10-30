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
