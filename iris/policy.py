"""The escalation rules for Iris's tools, in their own protected module.

A self-built branch must edit iris/tools.py to add a tool, so the rules that decide when an action
escalates cannot live there: this file is in PROTECTED (iris/capabilities.py) and a built branch may
not touch it. tools.py imports every name back from here, so both modules agree on one policy.

safe     runs immediately (reading, opening apps, status)
confirm  asks Jay first (clicking, typing)
never    refused, always (sending, buying, paying, passwords, deleting)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SAFE, CONFIRM, NEVER = "safe", "confirm", "never"

# Control names that mean an irreversible or outward-facing action, in English and Dutch. Matched as word
# starts so Dutch inflections count too (verzenden, betalen). ponytail: over-refuses names like "Poster";
# per-app allowlists if that gets in the way.
_NEVER_WORDS = re.compile(
    r"\b(send|submit|post|publish|buy|order|pay|checkout|purchase|delete|remove|password|sign ?in|log ?in|"
    r"verzend|verstu|plaats|koop|kopen|bestel|betaal|betalen|afreken|verwijder|wachtwoord|inlog|aanmeld)",
    re.I,
)

# Window titles that mean "pressing Enter sends a message or email": never automated.
_MAIL_CHAT = re.compile(r"(outlook|gmail|whatsapp|discord|\bteams\b|slack)", re.I)


def _unload_tier(args: dict) -> str:
    from iris.llm import effective_model  # read at call time so a model switch applies at once

    model = args.get("model")
    if model == "all" or model == effective_model():
        return NEVER
    return CONFIRM


def _click_tier(args: dict) -> str:
    return NEVER if _NEVER_WORDS.search(str(args.get("control", ""))) else CONFIRM


def _type_tier(args: dict) -> str:
    return NEVER if _NEVER_WORDS.search(str(args.get("into", ""))) else CONFIRM


def _open_url_tier(args: dict) -> str:
    from urllib.parse import urlparse

    scheme = urlparse(str(args.get("url", ""))).scheme.lower()
    return SAFE if scheme in ("http", "https") else NEVER


def _press_keys_tier(args: dict) -> str:
    keys = str(args.get("keys", "")).upper()
    if "{ENTER}" not in keys:
        return CONFIRM
    # Enter in a mail/chat window sends the message, so that combination is never automated.
    return NEVER if _MAIL_CHAT.search(str(args.get("window", ""))) else CONFIRM


def _trash_dir() -> Path:
    d = Path(os.environ.get("IRIS_TRASH_DIR") or (Path.home() / ".iris" / "trash"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _trash_roots() -> list[Path]:
    """Trash roots from IRIS_TRASH_ROOTS (';' separated); the home folder is always included."""
    raw = os.environ.get("IRIS_TRASH_ROOTS")
    if not raw:
        return [Path.home(), Path("W:/")]
    roots = [Path(s.strip()) for s in raw.split(";") if s.strip()]
    if Path.home() not in roots:
        roots.insert(0, Path.home())
    return roots


_SYSTEM_DIRS = [Path(os.environ.get("SystemRoot", "C:/Windows")), Path("C:/Program Files"),
                Path("C:/Program Files (x86)"), Path("C:/ProgramData")]


def _under(p: Path, root: Path) -> bool:
    try:
        return p.is_relative_to(root.resolve())
    except OSError:
        return False


def _trash_tier(args: dict) -> str:
    """Only files under the trash roots may be trashed; never a root itself, system dirs, or the trash."""
    p = Path(str(args.get("path", ""))).expanduser().resolve()
    roots = [r.resolve() for r in _trash_roots()]
    if p in roots or not any(_under(p, r) for r in roots):
        return NEVER
    if any(_under(p, s) for s in _SYSTEM_DIRS) or _under(p, _trash_dir()):
        return NEVER
    return CONFIRM
