"""The user's settings in ~/.iris/config.toml (IRIS_CONFIG overrides the path). Stdlib only.

Every key is optional; a missing or broken file means the defaults. Re-read on every call, so an edit
applies to the next request without a restart.

    name = "Sam"                 # how Iris refers to you in its instructions
    city = "Utrecht"             # the place weather() reports on when no place is given
    plugins = ["lmstudio"]       # opt-in tool groups, see iris.registry.PLUGINS
    builder = ["claude", "-p", "{prompt}"]   # the coding agent that builds new skills; Cline when unset
    folders = ["D:/Projects"]   # extra folders file tools may search and trash_file may touch
    overlay = true               # voice mode shows what you said and Iris's answer on screen (iris.overlay)
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Callable
from pathlib import Path

DEFAULTS: dict = {"name": "the user", "plugins": [], "builder": [], "folders": []}


def config_path() -> Path:
    """Where config.toml lives; IRIS_CONFIG overrides it (tests)."""
    return Path(os.environ.get("IRIS_CONFIG") or (Path.home() / ".iris" / "config.toml"))


def raw() -> dict:
    """The file as parsed; {} when it is missing or broken (error() says why). A UTF-8 BOM, which Notepad and
    PowerShell write, is fine."""
    try:
        return tomllib.loads(config_path().read_text(encoding="utf-8-sig"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}


def error() -> str | None:
    """Why config.toml can not be read, or None when it is fine or simply not there (iris doctor shows this)."""
    path = config_path()
    if not path.exists():
        return None
    try:
        tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError:
        return "it is not UTF-8 text; save it as UTF-8"
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return str(exc)
    return None


def get(key: str, default: str = "") -> str:
    """One optional string setting, such as cline_cli; the default when it is missing or not a string."""
    value = raw().get(key)
    return value if isinstance(value, str) and value.strip() else default


def setting(env: str, key: str, default: str = "") -> str:
    """The environment variable when it is set (it always wins), else the config.toml key (a string or a number),
    else the default. For settings that were environment-only before, such as IRIS_SILENCE and silence."""
    if os.environ.get(env, "").strip():
        return os.environ[env].strip()
    value = raw().get(key)
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return default
    return str(value).strip() or default


def number(env: str, key: str, default: float) -> float:
    """A numeric setting (environment, then config.toml), or the default when it is not a number: one typo such as
    silence = "fast" must not stop Iris from starting. bad_numbers() lets iris doctor point at it."""
    try:
        value = float(setting(env, key, str(default)))
    except ValueError:
        return default
    return value if value == value and abs(value) != float("inf") else default


NUMBERS = (("IRIS_SILENCE", "silence"), ("IRIS_FOLLOW_UP", "follow_up"), ("IRIS_WAKE_THRESHOLD", "wake_threshold"),
           ("IRIS_WHISPER_GPU", "whisper_gpu"), ("IRIS_VOXCPM_GPU", "voxcpm_gpu"))


def bad_numbers() -> list[str]:
    """The numeric settings whose value is not a number, as "key = value" (from config.toml or the environment)."""
    out = []
    for env, key in NUMBERS:
        value = setting(env, key, "")
        try:
            if value:
                float(value)
        except ValueError:
            out.append(f"{key} = {value!r}")
    return out


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


TEMPLATE = """# Iris settings; every key is optional. Read again on every request, so edits apply at once.
name = {name}
{city}
# folders = ["D:/Projects"]                # where file tools may write, besides your home folder
# builder = ["claude", "-p", "{{prompt}}"]   # the coding agent that builds new skills; Cline when unset
# plugins = ["lmstudio"]                   # opt-in tool groups: lmstudio, comfyui, agents, leadscan, conceptkit
# overlay = true                           # voice mode shows what you said and the answer on screen
"""


def init_main(ask: Callable[[str], str] = input) -> int:
    """`iris init`: ask two questions and write a starter config.toml; never overwrites one."""
    path = config_path()
    if path.exists():
        print(f"{path} exists already; edit it by hand.")
        return 1
    name = ask("What should Iris call you? ").strip() or DEFAULTS["name"]
    city = ask("Your city, for the weather (Enter to skip): ").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    # json.dumps quotes a string the way TOML's basic strings want it
    text = TEMPLATE.format(name=json.dumps(name, ensure_ascii=False),
                           city=f"city = {json.dumps(city, ensure_ascii=False)}" if city else '# city = "Utrecht"')
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}. Next: iris doctor")
    return 0
