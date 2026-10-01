"""What Iris can do on this PC, each with a risk tier enforced in code.

safe     runs immediately (reading, opening apps, status)
confirm  asks Jay first (clicking, typing)
never    refused, always (sending, buying, paying, passwords, deleting)
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
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
]}
