"""The user's settings in ~/.iris/config.toml (IRIS_CONFIG overrides the path). Stdlib only.

Every key is optional; a missing or broken file means the defaults. Re-read on every call, so an edit
applies to the next request without a restart.

    name = "Jay"                 # how Iris refers to you in its instructions
    plugins = ["lmstudio"]       # opt-in tool groups, see iris.registry.PLUGINS
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

DEFAULTS: dict = {"name": "the user", "plugins": []}


def config_path() -> Path:
    return Path(os.environ.get("IRIS_CONFIG") or (Path.home() / ".iris" / "config.toml"))


def load() -> dict:
    """DEFAULTS overlaid with the file's keys; a key with the wrong type keeps its default."""
    out = dict(DEFAULTS)
    try:
        data = tomllib.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return out
    if isinstance(data.get("name"), str) and data["name"].strip():
        out["name"] = data["name"].strip()
    if isinstance(data.get("plugins"), list):
        out["plugins"] = [p for p in data["plugins"] if isinstance(p, str)]
    return out
