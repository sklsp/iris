"""Tests for iris/update.py's _fetch: the real request, with urlopen faked so no network is touched."""

import io

from iris import __version__, update

_REAL_FETCH = update._fetch  # captured at import time; conftest swaps in an offline stand-in per test


def test_fetch_asks_github_with_the_iris_user_agent_and_a_ten_second_timeout(monkeypatch):
    monkeypatch.setattr(update, "_fetch", _REAL_FETCH)  # undo the conftest offline stand-in for this test
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["req"] = req
        seen["timeout"] = timeout
        return io.BytesIO(b'{"tag_name": "v9.9.9"}')  # json.load reads it; BytesIO works in a with block

    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen)
    assert update._fetch() == {"tag_name": "v9.9.9"}
    assert seen["req"].full_url == update.LATEST_URL
    assert seen["req"].get_header("User-agent") == f"Iris/{__version__}"
    assert seen["timeout"] == 10
