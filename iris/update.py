"""Notify-only update check against GitHub Releases. Never downloads or installs anything.

Only the desktop app checks, at most once a day, and only while `update_check` in config.toml is not false (the
first-run wizard asks). The request carries no identifiers; the answer is kept in update.json next to config.toml,
which is all the dashboard reads.
"""

from __future__ import annotations

import http.client
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


_STAGES = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "rc": 3, "c": 3, "post": 10}


def version_key(version: str) -> tuple[int, ...]:
    """A key that orders versions the way releases go: 0.7 == 0.7.0 < 1.0.0rc1 < 1.0.0 < 1.0.1; dev, alpha, beta and
    rc come before the release they lead to, post after it."""
    text = version.strip().lower().lstrip("v")
    release = [int(n) for n in re.findall(r"\d+", re.split(r"[^\d.]", text, maxsplit=1)[0])[:3]]
    release += [0] * (3 - len(release))
    rest = text.lstrip("0123456789.")  # what follows the release numbers: rc1, -beta.2, .dev3
    stage = re.search(r"(dev|alpha|beta|post|rc|a|b|c)\.?(\d*)", rest)
    if stage is None:
        return (*release, 9, 0)
    return (*release, _STAGES[stage[1]], int(stage[2] or 0))


def newer(latest: str, current: str) -> bool:
    """Whether release `latest` comes after `current`; a tag without a version number never does."""
    return bool(version_tuple(latest)) and version_key(latest) > version_key(current)


def status() -> dict:
    """What the last check found; no network."""
    s = _state()
    latest = s.get("latest") or ""
    return {"enabled": enabled(), "current": __version__, "latest": latest or None,
            "newer": bool(latest) and newer(latest, __version__),
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
    try:
        if s.get("checked_at") is not None and now - float(s["checked_at"]) < CHECK_EVERY_SECONDS:
            return status()
    except (TypeError, ValueError):
        pass  # a hand-edited or broken update.json: ask again
    try:
        release = (fetch or _fetch)()
    except (OSError, ValueError, http.client.HTTPException):
        return status()  # offline (or rate-limited: HTTPError is an OSError) is normal; try again next start
    if not isinstance(release, dict):
        return status()  # not a release: leave the last answer as it was
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
