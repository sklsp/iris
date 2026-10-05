"""What Iris can do on this PC, each with a risk tier enforced in code.

safe     runs immediately (reading, opening apps, status)
confirm  asks Jay first (clicking, typing)
never    refused, always (sending, buying, paying, passwords, deleting)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SAFE, CONFIRM, NEVER = "safe", "confirm", "never"

# Control names that mean an irreversible or outward-facing action, in English and Dutch. Matched as word
# starts so Dutch inflections count too (verzenden, betalen). ponytail: over-refuses names like "Poster";
# per-app allowlists if that gets in the way.
_NEVER_WORDS = re.compile(
    r"\b(send|submit|post|publish|buy|order|pay|checkout|purchase|delete|remove|password|sign ?in|log ?in|"
    r"verzend|verstu|plaats|koop|kopen|bestel|betaal|betalen|afreken|verwijder|wachtwoord|inlog|aanmeld)",
    re.I,
)
MAX_CONTROLS = 120


@dataclass
class Tool:
    name: str
    description: str
    params: dict  # JSON schema "properties"
    required: list[str]
    tier: str
    fn: Callable[..., str]
    tier_for: Callable[[dict], str] | None = None  # argument-dependent escalation

    def tier_of(self, args: dict) -> str:
        return self.tier_for(args) if self.tier_for else self.tier

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": f"[{self.tier}] {self.description}",
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


# --- senses -------------------------------------------------------------------
def system_status() -> str:
    import psutil

    vm = psutil.virtual_memory()
    lines = [f"CPU {psutil.cpu_percent(interval=0.3):.0f}%, RAM {vm.used / 2**30:.1f}/{vm.total / 2**30:.1f} GB"]
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        for row in filter(None, out.strip().splitlines()):
            i, name, util, used, total, temp = [x.strip() for x in row.split(",")]
            lines.append(f"GPU{i} {name}: {util}% load, VRAM {used}/{total} MiB, {temp} C")
    except (OSError, subprocess.SubprocessError, ValueError):
        lines.append("GPU: nvidia-smi not available")
    return "\n".join(lines)


# --- windows (UI Automation) --------------------------------------------------
def _desktop():
    from pywinauto import Desktop

    return Desktop(backend="uia")


def list_windows() -> str:
    titles = [w.window_text() for w in _desktop().windows() if w.window_text().strip()]
    return "\n".join(titles) or "(no windows)"


def _window(title: str):
    t = title.lower()
    wins = [w for w in _desktop().windows() if t in w.window_text().lower()]
    if not wins:
        raise LookupError(f"no window whose title contains {title!r}")
    return wins[0]


def read_window(title: str) -> str:
    w = _window(title)
    out = [f"Window: {w.window_text()}"]
    for c in w.descendants():
        name, kind = c.window_text().strip(), c.element_info.control_type
        if kind in ("Edit", "Document"):
            value = _value(c)
            out.append(f"{kind}: {name[:40]} = {value[:400]!r}" if value else f"{kind}: {name[:80]} (empty)")
        elif name:
            out.append(f"{kind}: {name[:80]}")
        if len(out) > MAX_CONTROLS:
            out.append("... (truncated)")
            break
    return "\n".join(out)


def _value(c) -> str:
    try:
        return c.iface_value.CurrentValue or ""
    except Exception:  # noqa: BLE001 - controls without the Value pattern
        try:
            return c.window_text()
        except Exception:  # noqa: BLE001
            return ""


def _control(w, name: str):
    n = name.lower()
    cands = [c for c in w.descendants() if c.window_text().strip()]
    exact = [c for c in cands if c.window_text().strip().lower() == n]
    hits = exact or [c for c in cands if n in c.window_text().lower()]
    if not hits:
        raise LookupError(f"no control named {name!r} in {w.window_text()!r}")
    return hits[0]


def click(window: str, control: str) -> str:
    c = _control(_window(window), control)
    try:
        c.invoke()
    except Exception:  # noqa: BLE001 - not every control supports Invoke; fall back to a real click
        c.click_input()
    return f"clicked {c.element_info.control_type} {c.window_text()!r}"


def _escape_keys(text: str) -> str:
    return re.sub(r"([{}+^%~()])", r"{\1}", text)


def type_text(window: str, text: str, into: str = "", replace: bool = False) -> str:
    w = _window(window)
    w.set_focus()
    target = _control(w, into) if into else w
    if replace:
        target.type_keys("^a{BACKSPACE}")
    target.type_keys(_escape_keys(text), with_spaces=True, with_newlines=True, pause=0.01)
    return f"typed {len(text)} characters into {(into or w.window_text())!r}"


def open_app(name: str) -> str:
    os.startfile(name)  # ShellExecute: app names on PATH/App Paths, ms-settings: URIs, files
    return f"started {name!r}"


def _click_tier(args: dict) -> str:
    return NEVER if _NEVER_WORDS.search(str(args.get("control", ""))) else CONFIRM


def _type_tier(args: dict) -> str:
    return NEVER if _NEVER_WORDS.search(str(args.get("into", ""))) else CONFIRM


# --- hands --------------------------------------------------------------------
# Window titles that mean "pressing Enter sends a message or email": never automated.
_MAIL_CHAT = re.compile(r"(outlook|gmail|whatsapp|discord|\bteams\b|slack)", re.I)


def press_keys(window: str, keys: str) -> str:
    w = _window(window)
    w.set_focus()
    w.type_keys(keys)  # pywinauto syntax: ^s = Ctrl+S, {TAB}, {ENTER}
    return f"pressed {keys!r} in {w.window_text()!r}"


def _press_keys_tier(args: dict) -> str:
    keys = str(args.get("keys", "")).upper()
    if "{ENTER}" not in keys:
        return CONFIRM
    # Enter in a mail/chat window sends the message, so that combination is never automated.
    return NEVER if _MAIL_CHAT.search(str(args.get("window", ""))) else CONFIRM


def focus_window(title: str) -> str:
    w = _window(title)
    w.set_focus()
    return f"focused {w.window_text()!r}"


def _scroll_at(x: int, y: int, delta: int) -> None:
    import ctypes

    ctypes.windll.user32.SetCursorPos(x, y)
    ctypes.windll.user32.mouse_event(0x0800, 0, delta, 0)  # MOUSEEVENTF_WHEEL


def scroll(window: str, direction: str, amount: int = 3) -> str:
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    w = _window(window)
    r = w.rectangle()
    delta = 120 * amount if direction == "up" else -120 * amount
    _scroll_at((r.left + r.right) // 2, (r.top + r.bottom) // 2, delta)
    return f"scrolled {direction} by {amount} in {w.window_text()!r}"


def open_url(url: str) -> str:
    from urllib.parse import urlparse

    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError(f"refusing to open a {p.scheme or 'schemeless'} URL; only http and https are allowed")
    os.startfile(url)
    return f"opened {url}"


# --- files --------------------------------------------------------------------
def _trash_dir() -> Path:
    d = Path(os.environ.get("IRIS_TRASH_DIR") or (Path.home() / ".iris" / "trash"))
    d.mkdir(parents=True, exist_ok=True)
    return d


_TRASH_ROOTS = [Path.home(), Path("W:/")]
_SYSTEM_DIRS = [Path(os.environ.get("SystemRoot", "C:/Windows")), Path("C:/Program Files"),
                Path("C:/Program Files (x86)"), Path("C:/ProgramData")]


def _under(p: Path, root: Path) -> bool:
    try:
        return p.is_relative_to(root.resolve())
    except OSError:
        return False


def _trash_tier(args: dict) -> str:
    """Only files under the home folder or W:/ may be trashed; never a root itself, system dirs, or the trash."""
    p = Path(str(args.get("path", ""))).expanduser().resolve()
    roots = [r.resolve() for r in _TRASH_ROOTS]
    if p in roots or not any(_under(p, r) for r in roots):
        return NEVER
    if any(_under(p, s) for s in _SYSTEM_DIRS) or _under(p, _trash_dir()):
        return NEVER
    return CONFIRM


def trash_file(path: str) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise LookupError(f"no such file or folder: {p}")
    dest = _trash_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}_{p.name}"
    n = 1
    while dest.exists():
        dest = _trash_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}_{n}_{p.name}"
        n += 1
    shutil.move(str(p), str(dest))
    (dest.parent / (dest.name + ".json")).write_text(json.dumps({"original": str(p)}), encoding="utf-8")
    return f"moved {p} to the trash; say 'restore' and I'll put it back"


def restore_trash(name: str = "") -> str:
    d = _trash_dir()
    items = [x for x in d.iterdir() if not x.name.endswith(".json")]
    items.sort(key=lambda x: x.stat().st_mtime, reverse=True)  # most recently trashed first
    hits = [x for x in items if name.lower() in x.name.lower()] if name else list(items)
    if not hits:
        raise LookupError(f"nothing matching {name!r} in the trash")
    item = hits[0]
    meta = json.loads((d / (item.name + ".json")).read_text(encoding="utf-8"))
    original = Path(meta["original"])
    if original.exists():
        raise FileExistsError(f"{original} already exists; restore it from the trash manually")
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(item), str(original))
    (d / (item.name + ".json")).unlink()
    return f"restored {item.name} to {original}"


# --- the agent manager (read only) -----------------------------------------------
def agents_status() -> str:
    """Branch, last commit and its age, uncommitted file count and BLOCKED.md for every agent repo.

    Read only: iris/agents.py only runs git rev-parse, log and status, never anything that writes."""
    from iris.agents import agents_status as _status

    return _status()


S = {"type": "string"}
TOOLS = {t.name: t for t in [
    Tool("system_status", "CPU, RAM and per-GPU load, VRAM and temperature.", {}, [], SAFE, system_status),
    Tool("list_windows", "Titles of all open windows.", {}, [], SAFE, list_windows),
    Tool("read_window", "The named controls (buttons, fields, text) in a window. Title match is a substring.",
         {"title": S}, ["title"], SAFE, read_window),
    Tool("open_app", "Start an app by name (notepad, explorer, chrome), an ms-settings: URI, or a file path.",
         {"name": S}, ["name"], SAFE, open_app),
    Tool("click", "Click the control with this name in the window whose title contains `window`.",
         {"window": S, "control": S}, ["window", "control"], CONFIRM, click, _click_tier),
    Tool("type_text", "Type text into a window, optionally into the named field `into`. "
         "replace=true clears the field first.",
         {"window": S, "text": S, "into": S, "replace": {"type": "boolean"}}, ["window", "text"], CONFIRM, type_text, _type_tier),
    Tool("press_keys", "Press keyboard shortcuts in a window. pywinauto syntax: ^s = Ctrl+S, {TAB}, {ENTER}. "
          "Never used to send messages or emails.",
         {"window": S, "keys": S}, ["window", "keys"], CONFIRM, press_keys, _press_keys_tier),
    Tool("focus_window", "Bring the window whose title contains `title` to the front and give it focus.",
         {"title": S}, ["title"], SAFE, focus_window),
    Tool("scroll", "Scroll up or down inside a window (mouse wheel at its center).",
         {"window": S, "direction": {"type": "string", "enum": ["up", "down"]},
          "amount": {"type": "integer"}}, ["window", "direction"], SAFE, scroll),
    Tool("open_url", "Open an http or https URL in the default browser. file:, javascript: and data: are refused.",
         {"url": S}, ["url"], SAFE, open_url),
    Tool("trash_file", "Move a file or folder to Iris's trash (undoable with restore_trash). Never deletes permanently.",
         {"path": S}, ["path"], CONFIRM, trash_file, _trash_tier),
    Tool("restore_trash", "Put the most recently trashed item back where it came from. `name` filters by filename substring.",
         {"name": S}, [], SAFE, restore_trash),
    Tool("agents_status", "Status of every repo listed in iris/agents.toml: branch, last commit and how long ago, "
         "uncommitted file count, whether BLOCKED.md exists, and STUCK when there are uncommitted changes "
         "but no commit for over an hour. Read only.",
         {}, [], SAFE, agents_status),
]}
