"""`iris doctor`: check the setup and say what to fix, one line per check. Never changes anything."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

from iris import config
from iris.registry import PLUGINS


def checks(payload: dict | None = None) -> list[tuple[bool, str]]:
    """(ok, line) per check; payload is the LM Studio model list (tests pass one in)."""
    from iris.dashboard import lmstudio_payload
    from iris.llm import BASE_URL, effective_model

    out: list[tuple[bool, str]] = []
    out.append((sys.version_info >= (3, 11), f"Python {sys.version.split()[0]} (3.11 or newer needed)"))
    out.append((sys.platform == "win32", f"platform {sys.platform} (Iris drives Windows through UI Automation)"))
    out.append((importlib.util.find_spec("pywinauto") is not None, "pywinauto installed"))
    cfg = config.load()
    out.append((config.config_path().exists(),
                f"config {config.config_path()} (optional; the defaults are used without it)"))
    unknown = [p for p in cfg["plugins"] if p not in PLUGINS]
    out.append((not unknown, f"plugins {cfg['plugins'] or 'none'}"
                + (f"; unknown: {', '.join(unknown)} (known: {', '.join(PLUGINS)})" if unknown else "")))
    p = payload if payload is not None else lmstudio_payload()
    model = effective_model()
    if not p.get("reachable"):
        out.append((False, f"LM Studio not reachable at {BASE_URL}: start it and its local server"))
    else:
        ids = [m["id"] for m in p["models"]]
        out.append((model in ids, f"model {model} " + ("available in LM Studio" if model in ids
                                                       else "not found in LM Studio: download it or set IRIS_MODEL")))
        kind = next((m.get("type") for m in p["models"] if m["id"] == model), None)
        if model in ids and p.get("states_known"):
            out.append((kind == "vlm", "vision: " + (f"{model} reads images (look_at_screen, click_on_screen)"
                        if kind == "vlm" else f"{model} can not read images, so look_at_screen and "
                                              "click_on_screen will fail; pick a vision model")))
    builder = cfg.get("builder") or []
    if builder:
        found = shutil.which(builder[0]) is not None or Path(builder[0]).exists()
        out.append((found, f"skill builder {builder[0]} "
                    + ("found" if found else "not found: fix builder in config.toml")))
    else:
        from iris.autopilot import cline_cli

        cli = cline_cli()
        found = shutil.which(cli) is not None or Path(cli).exists()
        out.append((found, f"skill builder Cline ({cli}) " + ("found" if found
                    else "not found: install the Cline CLI or set builder in config.toml (optional)")))
    voice = all(importlib.util.find_spec(m) is not None for m in ("faster_whisper", "sounddevice", "openwakeword"))
    out.append((voice, "voice extras installed" if voice else "voice extras missing: pip install -e .[voice]"))
    return out


def main() -> int:
    results = checks()
    optional = ("voice", "config", "skill builder", "vision")  # Iris runs without these; everything else must pass
    for ok, line in results:
        mark = "ok " if ok else ("-- " if line.startswith(optional) else "FIX")
        print(f"{mark} {line}")
    return 0 if all(ok for ok, line in results if not line.startswith(optional)) else 1
