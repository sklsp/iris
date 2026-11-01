"""The user's settings in ~/.iris/config.toml (IRIS_CONFIG overrides the path). Stdlib only.

Every key is optional; a missing or broken file means the defaults. Re-read on every call, so an edit
applies to the next request without a restart.

    name = "Sam"                 # how Iris refers to you in its instructions
    city = "Utrecht"             # the place weather() reports on when no place is given
    plugins = ["lmstudio"]       # opt-in tool groups, see iris.registry.PLUGINS
    builder = ["claude", "-p", "{prompt}"]   # the coding agent that builds new skills; Cline when unset
    folders = ["D:/Projects"]   # extra folders file tools may search and trash_file may touch
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

DEFAULTS: dict = {"name": "the user", "plugins": [], "builder": [], "folders": []}


def config_path() -> Path:
    return Path(os.environ.get("IRIS_CONFIG") or (Path.home() / ".iris" / "config.toml"))


def raw() -> dict:
    """The file as parsed; {} when it is missing or broken."""
    try:
        return tomllib.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}


def get(key: str, default: str = "") -> str:
    """One optional string setting, such as cline_cli; the default when it is missing or not a string."""
    value = raw().get(key)
    return value if isinstance(value, str) and value.strip() else default


def load() -> dict:
    """DEFAULTS overlaid with the file's keys; a key with the wrong type keeps its default."""
    out = dict(DEFAULTS)
    data = raw()
    if isinstance(data.get("name"), str) and data["name"].strip():
        out["name"] = data["name"].strip()
    if isinstance(data.get("plugins"), list):
        out["plugins"] = [p for p in data["plugins"] if isinstance(p, str)]
    if isinstance(data.get("builder"), list) and all(isinstance(a, str) for a in data["builder"]):
        out["builder"] = data["builder"]
    if isinstance(data.get("folders"), list):
        out["folders"] = [f for f in data["folders"] if isinstance(f, str)]
    return out


def folders() -> list[Path]:
    """The home folder first, then every configured folder, expanded and without duplicates."""
    out = [Path.home()]
    for f in load().get("folders", []):
        p = Path(f).expanduser()
        if p not in out:
            out.append(p)
    return out
