"""Notify-only update check against GitHub Releases. Never downloads or installs anything.

Only the desktop app checks, at most once a day, and only while `update_check` in config.toml is not false (the
first-run wizard asks). The request carries no identifiers; the answer is kept in update.json next to config.toml,
which is all the dashboard reads.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from iris import __version__, config

LATEST_URL = "https://api.github.com/repos/sklsp/iris/releases/latest"  # drafts and pre-releases never show here
RELEASES_URL = "https://github.com/sklsp/iris/releases"
CHECK_EVERY_SECONDS = 24 * 3600


def state_path() -> Path:
    """Where the last check's answer lives, next to config.toml; it is all the dashboard reads."""
    return config.config_path().parent / "update.json"


def enabled() -> bool:
    """Whether update checks are on; only an explicit false in config.toml turns them off."""
    return config.raw().get("update_check", True) is not False


def _state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def version_tuple(version: str) -> tuple[int, ...]:
    """"v0.10.1" -> (0, 10, 1); anything without numbers -> ()."""
    return tuple(int(n) for n in re.findall(r"\d+", version.split("-")[0])[:3])


def status() -> dict:
    """What the last check found; no network."""
    s = _state()
    latest = s.get("latest") or ""
    return {"enabled": enabled(), "current": __version__, "latest": latest or None,
            "newer": bool(latest) and version_tuple(latest) > version_tuple(__version__),
            "url": s.get("url") or RELEASES_URL, "checked_at": s.get("checked_at")}


def _fetch() -> dict:
    req = urllib.request.Request(LATEST_URL, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": f"Iris/{__version__}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def check(now: float | None = None, fetch: Callable[[], dict] | None = None) -> dict:
    """Ask GitHub when the last answer is a day old (and checks are on); then the status."""
    if not enabled():
        return status()
    now = time.time() if now is None else now
    s = _state()
    if s.get("checked_at") is not None and now - float(s["checked_at"]) < CHECK_EVERY_SECONDS:
        return status()
    try:
        release = (fetch or _fetch)()
    except (OSError, ValueError):
        return status()  # offline is normal; try again next start
    s.update(checked_at=now, latest=str(release.get("tag_name") or ""), url=str(release.get("html_url") or ""))
    _save(s)
    return status()


def tell_once(notify: Callable[[str], None]) -> None:
    """Check, and say a newer version exists once per version (the app's tray balloon)."""
    st = check()
    if st["newer"] and _state().get("told") != st["latest"]:
        notify(f"Iris {st['latest']} is available (you have {st['current']}). See the dashboard for the link.")
        s = _state()
        s["told"] = st["latest"]
        _save(s)
