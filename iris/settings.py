"""The Settings tab: every config.toml option with an explanation, validation and a writer that keeps the file's
comments and any key it does not know. config.toml stays the source of truth; the tab is one way to edit it.

Environment variables still win over the file (IRIS_SILENCE over silence, ...); the tab says so where one is set.
Voice settings apply when voice mode starts again; the rest on the next request.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

from iris import config

WAKE_WORDS = ("hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy", "iris")  # openWakeWord built-ins, or the name
TTS_ENGINES = ("sapi", "piper", "voxcpm", "kokoro")
WHISPER_MODELS = ("", "tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")  # "" = automatic

# key, type, label, help, extra. Types: text, number, int, bool, choice, list (one per line), plugins, gpu.
# restart: the voice loop reads it when it starts. env: the variable that wins over the file.
FIELDS: list[dict] = [
    {"key": "name", "type": "text", "group": "You", "label": "Your name", "default": "the user",
     "help": "How Iris refers to you in its instructions."},
    {"key": "city", "type": "text", "group": "You", "label": "City", "default": "",
     "help": "The place the weather is for when you do not name one."},
    {"key": "folders", "type": "list", "group": "You", "label": "Extra folders", "default": [],
     "help": "Folders the file tools may search and write in, besides your home folder. One per line."},
    {"key": "confirm", "type": "choice", "group": "Safety", "label": "Ask before", "default": "all",
     "choices": ["all", "important"],
     "help": "all: Iris asks before every action that changes something. important: only before paying, sending, "
             "deleting for good and private files. Refused actions stay refused either way.", "env": "IRIS_CONFIRM"},
    {"key": "wakeword", "type": "choice", "group": "Listening", "label": "Wake word", "default": "hey_jarvis",
     "choices": list(WAKE_WORDS), "restart": True, "env": "IRIS_WAKEWORD",
     "help": "hey_jarvis and the other names are openWakeWord models (CC BY-NC-SA: non-commercial use). iris wakes "
             "on the spoken name through Whisper tiny: no model license, a little more CPU."},
    {"key": "wake_threshold", "type": "number", "group": "Listening", "label": "Wake word sensitivity",
     "default": 0.5, "min": 0.05, "max": 0.99, "restart": True, "env": "IRIS_WAKE_THRESHOLD",
     "help": "Lower wakes more easily (and more often by mistake); higher needs a clearer wake word."},
    {"key": "silence", "type": "number", "group": "Listening", "label": "Silence that ends a request (s)",
     "default": 1.8, "min": 0.3, "max": 10, "restart": True, "env": "IRIS_SILENCE",
     "help": "How long you may pause before Iris takes what you said. Raise it if you get cut off."},
    {"key": "follow_up", "type": "number", "group": "Listening", "label": "Follow-up window (s)",
     "default": 8, "min": 0, "max": 60, "restart": True, "env": "IRIS_FOLLOW_UP",
     "help": "After an answer, start talking within this time to ask more without the wake word. 0 turns it off."},
    {"key": "whisper_model", "type": "choice", "group": "Listening", "label": "Speech recognition model",
     "default": "", "choices": list(WHISPER_MODELS), "restart": True, "env": "IRIS_WHISPER",
     "help": "Empty picks for you: large-v3-turbo on an NVIDIA GPU, small on the CPU. Bigger is better and slower."},
    {"key": "whisper_gpu", "type": "gpu", "group": "Listening", "label": "GPU for speech recognition",
     "default": "", "restart": True, "env": "IRIS_WHISPER_GPU",
     "help": "Which NVIDIA GPU Whisper uses, in nvidia-smi order. Empty: the first one."},
    {"key": "tts", "type": "choice", "group": "Speech", "label": "Voice engine", "default": "sapi",
     "choices": list(TTS_ENGINES), "restart": True, "env": "IRIS_TTS",
     "help": "sapi: Windows voices, always there, English only. piper: natural Dutch and English voices. voxcpm: one "
             "natural voice for both languages, needs about 6 GB of GPU memory. kokoro: optional English voices."},
    {"key": "piper_nl", "type": "text", "group": "Speech", "label": "Piper Dutch voice",
     "default": "nl_NL-ronnie-medium", "restart": True, "env": "IRIS_PIPER_NL",
     "help": "A Piper voice name for Dutch answers. Check a voice's model card before you pick another."},
    {"key": "piper_en", "type": "text", "group": "Speech", "label": "Piper English voice",
     "default": "en_US-joe-medium", "restart": True, "env": "IRIS_PIPER_EN",
     "help": "A Piper voice name for English answers."},
    {"key": "voxcpm_gpu", "type": "gpu", "group": "Speech", "label": "GPU for VoxCPM2", "default": 0,
     "restart": True, "env": "IRIS_VOXCPM_GPU",
     "help": "Give it a card with about 4 GB truly free, not the one the language model fills."},
    {"key": "overlay", "type": "bool", "group": "Speech", "label": "Show the overlay", "default": False,
     "restart": True, "env": "IRIS_OVERLAY",
     "help": "Voice mode shows what you said and the answer on screen, with a button to cancel. It never takes "
             "the focus from the window you work in."},
    {"key": "llm_url", "type": "text", "group": "Model", "label": "Model server",
     "default": "http://127.0.0.1:1234/v1", "restart": True, "env": "IRIS_LLM_URL",
     "help": "Any OpenAI-compatible server. LM Studio: http://127.0.0.1:1234/v1, Ollama: http://127.0.0.1:11434/v1, "
             "llama.cpp: http://127.0.0.1:8080/v1. Loading and unloading models works with LM Studio only."},
    {"key": "browser_profile", "type": "text", "group": "Apps", "label": "Browser profile folder", "default": "",
     "env": "IRIS_BROWSER_PROFILE",
     "help": "The User Data folder of the Brave, Chrome or Edge Iris reads web pages in. Empty: Iris finds it."},
    {"key": "update_check", "type": "bool", "group": "Apps", "label": "Check for updates", "default": True,
     "help": "The desktop app asks GitHub once a day whether a newer Iris exists and tells you. It never downloads "
             "or installs anything, and sends no information about you."},
    {"key": "plugins", "type": "plugins", "group": "Apps", "label": "Plugins", "default": [],
     "help": "Extra tool groups for particular setups. They keep their risk tiers."},
]
BY_KEY = {f["key"]: f for f in FIELDS}
READ_ONLY = ("builder",)  # a command line: edit it in the file, never from a web page


def _gpus() -> list[dict]:
    try:
        from iris.tools import system_data

        return [{"index": g["index"], "name": g["name"]} for g in system_data()["gpus"]]
    except Exception:  # noqa: BLE001 - no nvidia-smi is a normal PC, not an error
        return []


def payload() -> dict:
    """Every field with its current value from the file, which ones an environment variable overrides, and the GPUs."""
    from iris.registry import PLUGINS

    data = config.raw()
    fields = []
    for f in FIELDS:
        row = {**f, "value": data.get(f["key"], f["default"]), "set": f["key"] in data}
        if f.get("env") and os.environ.get(f["env"], "").strip():
            row["overridden_by"] = f"{f['env']}={os.environ[f['env']]}"
        if f["type"] == "plugins":
            row["choices"] = sorted(PLUGINS)
        fields.append(row)
    return {"ok": True, "path": str(config.config_path()), "fields": fields, "gpus": _gpus(),
            "read_only": {k: data[k] for k in READ_ONLY if k in data}}


def effective() -> list[tuple[str, str, str]]:
    """One row per setting in FIELDS order: the key, its value now and where it comes from."""
    data = config.raw()
    rows = []
    for f in FIELDS:
        env = f.get("env")
        if env and os.environ.get(env, "").strip():
            rows.append((f["key"], os.environ[env].strip(), f"environment {env}"))
        elif f["key"] in data:
            rows.append((f["key"], toml_value(data[f["key"]]), "config.toml"))
        else:
            rows.append((f["key"], toml_value(f["default"]), "default"))
    return rows


def extras() -> list[tuple[str, str]]:
    """Keys in config.toml that are not on the Settings tab, sorted, with their TOML value."""
    data = config.raw()
    return [(k, toml_value(data[k])) for k in sorted(data) if k not in BY_KEY]


def config_main(argv: list[str]) -> int:
    """`iris config`: every setting, its value and where it comes from (file, default or environment)."""
    print(f"config file: {config.config_path()}")
    for key, value, source in effective():
        print(f"{key} = {value}  ({source})")
    extra = extras()
    if extra:
        print("extra keys in config.toml (not on the Settings tab):")
        for key, value in extra:
            print(f"{key} = {value}")
    return 0


def validate(values: dict) -> tuple[dict, dict]:
    """(clean values, errors by key). A value of None or "" for an optional key removes it from the file."""
    from iris.registry import PLUGINS

    clean: dict = {}
    errors: dict = {}
    for key, value in values.items():
        f = BY_KEY.get(key)
        if f is None:
            errors[key] = "not a setting the app can change" if key in READ_ONLY else "unknown setting"
            continue
        kind = f["type"]
        try:
            if value is None or (isinstance(value, str) and not value.strip() and kind != "bool"):
                clean[key] = None
            elif kind == "bool":
                if not isinstance(value, bool):
                    raise ValueError("must be on or off")
                clean[key] = value
            elif kind in ("number", "int", "gpu"):
                number = float(value)
                if kind in ("int", "gpu"):
                    if number != int(number) or number < 0:
                        raise ValueError("must be a whole number, 0 or more")
                    number = int(number)
                if "min" in f and not f["min"] <= number <= f["max"]:
                    raise ValueError(f"must be between {f['min']} and {f['max']}")
                clean[key] = number
            elif kind == "choice":
                if value not in f["choices"] and value != config.raw().get(key):  # a hand-edited value may stay
                    if key == "wakeword" and str(value).endswith(".onnx") and Path(value).expanduser().is_file():
                        clean[key] = value  # your own openWakeWord model
                        continue
                    raise ValueError(f"must be one of: {', '.join(c or '(automatic)' for c in f['choices'])}")
                clean[key] = value
            elif kind in ("list", "plugins"):
                items = value.splitlines() if isinstance(value, str) else value
                if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
                    raise ValueError("must be a list of text")
                items = [i.strip() for i in items if i.strip()]
                if kind == "plugins":
                    unknown = [p for p in items if p not in PLUGINS]
                    if unknown:
                        raise ValueError(f"unknown plugin {', '.join(unknown)}; known: {', '.join(sorted(PLUGINS))}")
                else:
                    missing = [i for i in items if not Path(i).expanduser().is_dir()]
                    if missing:
                        raise ValueError(f"not a folder: {', '.join(missing)}")
                clean[key] = items
            else:  # text
                if not isinstance(value, str) or "\n" in value:
                    raise ValueError("must be one line of text")
                value = value.strip()
                if key == "llm_url" and not re.match(r"https?://[^\s/]+", value):
                    raise ValueError("must start with http:// or https://, like http://127.0.0.1:1234/v1")
                if key == "browser_profile" and not Path(value).expanduser().is_dir():
                    raise ValueError("not a folder")
                clean[key] = value
        except (TypeError, ValueError) as exc:
            errors[key] = str(exc) if str(exc) and not str(exc).startswith("could not convert") else "must be a number"
    return clean, errors


def toml_value(value: object) -> str:
    """One TOML value. json.dumps quotes a string the way TOML's basic strings want it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def write(updates: dict, path: Path | None = None) -> None:
    """Set (or with None, remove) top-level keys in config.toml. Comment lines, other keys and [tables] stay as they
    are; a replaced key loses a comment on its own line. Written aside, checked, then swapped in."""
    path = path or config.config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    for key, value in updates.items():
        end_of_top = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
        start = next((i for i in range(end_of_top) if re.match(rf"\s*{re.escape(key)}\s*=", lines[i])), None)
        new = [] if value is None else [f"{key} = {toml_value(value)}"]
        if start is None:
            at = end_of_top
            while at > 0 and not lines[at - 1].strip():  # after the last key, before the blank lines of a [table]
                at -= 1
            lines[at:at] = new
            continue
        end = start + 1  # a value may span lines (an array): take lines until they parse on their own
        while end < end_of_top:
            try:
                tomllib.loads("\n".join(lines[start:end]))
                break
            except tomllib.TOMLDecodeError:
                end += 1
        lines[start:end] = new
    out = "\n".join(lines) + "\n"
    parsed = tomllib.loads(out)  # never write a file Iris could not read back
    for key, value in updates.items():
        if parsed.get(key) != value:
            raise ValueError(f"{key} did not round-trip")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(out, encoding="utf-8")
    os.replace(tmp, path)


def save(values: dict) -> dict:
    """Validate and write; the reply says which keys need voice mode to start again."""
    clean, errors = validate(values)
    if errors:
        return {"ok": False, "errors": errors}
    data = config.raw()
    changed = {k: v for k, v in clean.items() if data.get(k) != v and not (v is None and k not in data)}
    if changed:
        try:
            write(changed)
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            return {"ok": False, "errors": {"_file": f"config.toml could not be written: {exc}"}}
    if "llm_url" in changed:
        from iris import dashboard

        dashboard.set_lm_base(changed["llm_url"] or BY_KEY["llm_url"]["default"])
    return {"ok": True, "changed": sorted(changed),
            "restart": sorted(k for k in changed if BY_KEY[k].get("restart"))}
