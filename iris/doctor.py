"""`iris doctor`: check the setup and say what to fix, one line per check. Never changes anything."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

from iris import config
from iris.registry import PLUGINS

# Iris runs without these; everything else must pass
OPTIONAL = ("voice", "config", "skill builder", "vision", "hearing", "speech", "browser")


def checks(payload: dict | None = None) -> list[tuple[bool, str]]:
    """(ok, line) per check; payload is the LM Studio model list (tests pass one in)."""
    from iris.dashboard import lmstudio_payload
    from iris.llm import effective_model

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
        url = config.setting("IRIS_LLM_URL", "llm_url", "http://127.0.0.1:1234/v1")
        out.append((False, f"model server not reachable at {url}: start LM Studio (or your own server) "
                           "and its local server, or set llm_url"))
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
    if voice:
        from iris import voice as v

        gpu = v._cuda_available()
        out.append((gpu, "hearing: Whisper large-v3-turbo on the GPU" if gpu
                    else "hearing: Whisper small on the CPU; pip install -e .[gpu] for a better, faster model on an "
                         "NVIDIA GPU"))
        engine = os.environ.get("IRIS_TTS") or config.get("tts", "sapi")
        if engine == "voxcpm":
            have = v.VOXCPM_PYTHON.exists()
            out.append((have, f"speech: VoxCPM2 through {v.VOXCPM_PYTHON}" if have else
                        f"speech: VoxCPM2 is not installed at {v.VOXCPM_PYTHON} (see Speech out in the README); "
                        "Piper speaks instead"))
        elif engine == "piper":
            missing = [n for n in v.PIPER_VOICES.values() if not (v.PIPER_DIR / f"{n}.onnx").exists()]
            piper = importlib.util.find_spec("piper") is not None or (v.piper_runtime() / "piper").is_dir()
            out.append((piper and not missing, "speech: Piper " + (", ".join(v.PIPER_VOICES.values()) if piper and
                        not missing else ("not installed: pip install -e .[piper]" if not piper else
                                          f"voices missing: python -m piper.download_voices {' '.join(missing)} "
                                          f"--data-dir {v.PIPER_DIR}"))))
        else:
            out.append((False, f'speech: {engine}; tts = "piper" in config.toml for natural Dutch and English voices'))
    from iris import browser

    ws = importlib.util.find_spec("websocket") is not None
    port = next((p for p in browser._profiles() if (p / "DevToolsActivePort").exists()), None)
    out.append((ws and port is not None, "browser: " + (f"remote control allowed ({port.parent.name})" if ws and port
                else "pip install -e .[browser]" if not ws else
                "open brave://inspect/#remote-debugging and tick 'Allow remote debugging' for fast web pages")))
    return out


def main() -> int:
    results = checks()
    for ok, line in results:
        mark = "ok " if ok else ("-- " if line.startswith(OPTIONAL) else "FIX")
        print(f"{mark} {line}")
    return 0 if all(ok for ok, line in results if not line.startswith(OPTIONAL)) else 1
