"""What 1.0 promises to keep (README, "Stability"): the commands and flags, the config.toml keys, the ~/.iris files
and the dashboard's routes. Adding is fine; removing one of these needs a major version, so a removal fails here."""

import re
from pathlib import Path

from iris import settings

IRIS = Path(__file__).resolve().parent.parent / "iris"

COMMANDS = {"app", "autopilot", "config", "dashboard", "doctor", "eval", "eval-compare", "init", "shortcut", "skill",
            "startup"}
FLAGS = {"--voice", "--push-to-talk", "--journal", "--version"}  # of `iris` itself; --supervised is the app's own
CONFIG_KEYS = {"name", "city", "folders", "plugins", "builder", "confirm", "tts", "piper_nl", "piper_en", "voxcpm_gpu",
               "overlay", "llm_url", "wakeword", "wake_threshold", "wake_whisper", "silence", "follow_up",
               "whisper_model", "whisper_gpu", "browser_profile", "update_check", "cline_cli", "cline_cwd",
               "leadscan_dir", "conceptkit_dir"}
ROUTES = {"/", "/api/agents", "/api/app", "/api/claude", "/api/eval", "/api/eval/history", "/api/iris/model",
          "/api/journal", "/api/lmstudio", "/api/logs", "/api/packs", "/api/queue", "/api/runs", "/api/sessions",
          "/api/settings", "/api/setup", "/api/skills", "/api/system", "/api/tools", "/api/update",
          # writes (POST, with the page token)
          "/api/app/voice/restart", "/api/cline/start", "/api/models/load", "/api/models/unload", "/api/packs/install",
          "/api/queue/add", "/api/queue/delete", "/api/queue/move", "/api/setup/server", "/api/setup/mic",
          "/api/setup/speaker", "/api/setup/done", "/api/skills/approve", "/api/skills/remove"}
HOME_FILES = {"config.toml", "journal.jsonl", "notes.md", "reminders.json", "queue.json", "settings.json", "skills",
              "trash", "logs", "setup.json", "update.json"}


def _source(name: str) -> str:
    return (IRIS / name).read_text(encoding="utf-8")


def test_every_command_and_flag_of_1_0_is_still_there():
    main = _source("__main__.py")
    commands = set(re.findall(r'sys\.argv\[1\] == "([a-z-]+)"', main))
    flags = set(re.findall(r'add_argument\("(--[a-z-]+)"', main))
    assert COMMANDS <= commands, COMMANDS - commands
    assert FLAGS <= flags, FLAGS - flags


def test_every_config_key_of_1_0_is_still_read():
    code = "".join(p.read_text(encoding="utf-8") for p in IRIS.rglob("*.py"))
    read = set(re.findall(r'config\.(?:get|setting|number)\((?:"IRIS_[A-Z_]+", )?"([a-z_]+)"', code))
    read |= {f["key"] for f in settings.FIELDS} | set(re.findall(r'raw\(\)\.get\("([a-z_]+)"', code))
    read |= set(re.findall(r'(?:data|cfg)\.get\("([a-z_]+)"', _source("config.py")))
    assert CONFIG_KEYS <= read, CONFIG_KEYS - read


def test_every_dashboard_route_of_1_0_is_still_served():
    code = _source("dashboard/__init__.py")
    served = set(re.findall(r'"(/(?:api/[a-z/_]+)?)"', code))
    assert ROUTES <= served, ROUTES - served


def test_every_file_in_the_iris_folder_of_1_0_still_has_its_name():
    code = "".join(p.read_text(encoding="utf-8") for p in IRIS.rglob("*.py"))
    named = set(re.findall(r'"([a-z_]+(?:\.[a-z]+)?)"', code))
    assert HOME_FILES <= named, HOME_FILES - named
