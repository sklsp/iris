"""What Iris can do on this PC, each with a risk tier enforced in code.

safe     runs immediately (reading, opening apps, status)
confirm  asks the user first (clicking, typing)
never    refused, always (sending, buying, paying, passwords, deleting)
"""

from __future__ import annotations

import ast
import json
import math
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from iris import config
from iris.browser import browser_click, browser_read, browser_scroll, browser_type
from iris.desktop import (
    _start_menu_folders,
    click_on_screen,
    clipboard_read,
    clipboard_write,
    clock,
    close_window,
    email_draft,
    lock_screen,
    look_at_screen,
    media_key,
    screenshot,
    start_menu_match,
)
from iris.files import _mtime, copy_file, find_files, list_folder, make_folder, move_file, read_file, write_file
from iris.notes import note_add, note_remove, notes_read
from iris.office import make_document, make_presentation, make_spreadsheet
from iris.policy import (
    _MAIL_CHAT,
    _NEVER_WORDS,
    _SYSTEM_DIRS,
    CONFIRM,
    NEVER,
    SAFE,
    _browser_click_tier,
    _browser_type_tier,
    _click_on_screen_tier,
    _click_tier,
    _copy_tier,
    _docx_tier,
    _make_folder_tier,
    _move_tier,
    _open_app_tier,
    _open_url_tier,
    _pptx_tier,
    _press_keys_tier,
    _read_tier,
    _screenshot_tier,
    _trash_dir,
    _trash_roots,
    _trash_tier,
    _type_tier,
    _under,
    _unload_tier,
    _write_tier,
    _xlsx_tier,
)
from iris.reminders import reminder_cancel, reminder_list, reminder_set
from iris.timers import Timers
from iris.weather import weather

# Re-exported from iris.policy so existing imports of these names from iris.tools keep working; tools.py
# itself only uses some of them, and __all__ marks the rest as deliberate re-exports.
__all__ = ["NEVER", "_MAIL_CHAT", "_NEVER_WORDS", "_SYSTEM_DIRS", "_trash_roots", "_under"]

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
    pure: bool = False  # only computes, touches nothing on the PC; the eval runs it for real

    def tier_of(self, args: dict) -> str:
        """The risk tier for these arguments; argument-dependent tools can escalate above their base tier."""
        return self.tier_for(args) if self.tier_for else self.tier

    def schema(self) -> dict:
        """The JSON function-calling entry for the model, with the tier in front of the description."""
        return {"type": "function", "function": {
            "name": self.name, "description": f"[{self.tier}] {self.description}",
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


# --- senses -------------------------------------------------------------------
def system_data() -> dict:
    """The same numbers as system_status, as data instead of text (the dashboard uses this)."""
    import psutil

    vm = psutil.virtual_memory()
    d = {"cpu_percent": psutil.cpu_percent(interval=0.3),
         "ram_used_gb": vm.used / 2**30,
         "ram_total_gb": vm.total / 2**30}
    gpus = []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        for row in filter(None, out.strip().splitlines()):
            i, name, util, used, total, temp = [x.strip() for x in row.split(",")]
            gpus.append({"index": int(i), "name": name, "load_percent": float(util),
                         "vram_used_mib": float(used), "vram_total_mib": float(total), "temp_c": float(temp)})
    except (OSError, subprocess.SubprocessError, ValueError):
        pass  # no nvidia-smi or an unexpected row: report CPU and RAM only
    d["gpus"] = gpus
    return d


def system_status() -> str:
    """CPU and RAM usage as text, with each GPU's load, VRAM and temperature on its own line."""
    d = system_data()
    lines = [f"CPU {d['cpu_percent']:.0f}%, RAM {d['ram_used_gb']:.1f}/{d['ram_total_gb']:.1f} GB"]
    for g in d["gpus"]:
        lines.append(f"GPU{g['index']} {g['name']}: {g['load_percent']:.0f}% load, "
                     f"VRAM {g['vram_used_mib']:.0f}/{g['vram_total_mib']:.0f} MiB, {g['temp_c']:.0f} C")
    if not d["gpus"]:
        lines.append("GPU: nvidia-smi not available")
    return "\n".join(lines)


def gpu_guard(data: dict, min_free_mib: float = 8000, max_temp_c: float = 80) -> list[dict]:
    """Whether each GPU has enough free VRAM and is cool enough for heavy work. Pure function."""
    out = []
    for g in data["gpus"]:
        free = g["vram_total_mib"] - g["vram_used_mib"]
        reasons = []
        if free < min_free_mib:
            reasons.append(f"only {free:.0f} MiB free")
        if g["temp_c"] >= max_temp_c:
            reasons.append(f"{g['temp_c']:.0f} C, too hot")
        out.append({"index": g["index"], "name": g["name"], "free_mib": free,
                    "ok": not reasons, "reason": "; ".join(reasons)})
    return out


def gpu_check(min_free_mib: int = 8000) -> str:
    """Whether each GPU has room for a heavy job (ComfyUI, a big model): free VRAM and temperature."""
    guards = gpu_guard(system_data(), min_free_mib)
    if not guards:
        return "GPU: nvidia-smi not available"
    lines = []
    for g in guards:
        if g["ok"]:
            lines.append(f"GPU{g['index']} {g['name']}: ok, {g['free_mib']:.0f} MiB free")
        else:
            lines.append(f"GPU{g['index']} {g['name']}: busy, {g['reason']}")
    return "\n".join(lines)


def _get_json(url: str) -> dict:
    import urllib.request

    with urllib.request.urlopen(url, timeout=3) as r:
        return json.loads(r.read().decode())


def comfy_status() -> str:
    """Whether ComfyUI is running on this PC, its version and how many jobs are running and waiting."""
    base = os.environ.get("IRIS_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
    try:
        stats = _get_json(base + "/system_stats")
        queue = _get_json(base + "/queue")
    except (OSError, ValueError):
        return "ComfyUI is not running."
    version = stats.get("system", {}).get("comfyui_version", "unknown")
    running = len(queue.get("queue_running", []))
    waiting = len(queue.get("queue_pending", []))
    return f"ComfyUI {version} is running. Queue: {running} running, {waiting} waiting."


def comfy_workflows() -> str:
    """Names of the user's saved ComfyUI workflows in the workflows folder, one per line."""
    folder = Path(os.environ.get("IRIS_COMFY_WORKFLOWS", "~/.iris/workflows")).expanduser()
    if not folder.is_dir():
        return f"No saved workflows in {folder}."
    names = sorted(p.stem for p in folder.iterdir() if p.is_file() and p.suffix == ".json")
    if not names:
        return f"No saved workflows in {folder}."
    return "\n".join(names)


def comfy_queue(workflow: str) -> str:
    """Queue a saved ComfyUI workflow (API format) by name. Ask first: it uses the GPU for a while."""
    import urllib.request

    if not re.fullmatch(r"[A-Za-z0-9_-]+", workflow):
        return "error: bad workflow name"
    folder = Path(os.environ.get("IRIS_COMFY_WORKFLOWS", "~/.iris/workflows")).expanduser()
    path = folder / f"{workflow}.json"
    if not path.is_file():
        return f"error: no saved workflow named {workflow}"
    try:
        prompt = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return f"error: could not read {workflow}.json"
    guards = gpu_guard(system_data(), 8000)  # the same check as item 7; nvidia-smi runs once
    if not any(g["ok"] for g in guards):
        return "error: the GPU is busy or hot; try again later"
    base = os.environ.get("IRIS_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
    req = urllib.request.Request(
        base + "/prompt", data=json.dumps({"prompt": prompt}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            out = json.loads(r.read().decode())
    except (OSError, ValueError):
        return "error: ComfyUI is not running"
    return f"queued {workflow} as {out.get('prompt_id', '?')}"


def lmstudio_models() -> str:
    """Which models LM Studio has loaded and how many it knows. Read only: never loads or unloads."""
    from iris.dashboard import lmstudio_payload

    p = lmstudio_payload()
    if not p.get("reachable"):
        return "LM Studio is not reachable."
    n = len(p["models"])
    if p.get("states_known"):
        loaded = [m["id"] for m in p["models"] if m.get("state") == "loaded"]
        if loaded:
            return f"Loaded: {', '.join(loaded)}. Available: {n} models."
        return f"No model is loaded. Available: {n} models."
    return f"LM Studio is running with {n} models; it does not say which one is loaded."


def lmstudio_load(model: str) -> str:
    """Load a model in LM Studio by its id. Ask first: loading takes VRAM and can take minutes."""
    from iris.dashboard import ModelActionError, model_action

    guards = gpu_guard(system_data(), 8000)  # the same check as gpu_check; nvidia-smi runs once
    if not guards or max(g["free_mib"] for g in guards) < 8000:
        return "error: no GPU has 8000 MiB free; unload a model first"
    try:
        model_action("load", {"model": model})
    except ModelActionError as exc:
        return f"error: {exc}"
    return f"loaded {model}"


def lmstudio_unload(model: str) -> str:
    """Unload a model from LM Studio by its id, or all of them with "all". Asks the user first."""
    from iris.dashboard import ModelActionError, model_action

    try:
        model_action("unload", {"model": model})
    except ModelActionError as exc:
        return f"error: {exc}"
    return f"unloaded {model}"


def followups(today: str = "") -> str:
    """Leads from the leadscan CRM whose follow-up date is today or earlier, with status and note."""
    import csv
    from datetime import date

    folder = Path(os.environ.get("IRIS_LEADSCAN_DIR") or config.get("leadscan_dir", str(Path.home() / "leadscan")))
    file = folder / "out" / "pipeline.csv"
    if not file.exists():
        return "No leadscan CRM found."
    day = today or date.today().isoformat()
    done = {"klant", "geen interesse", "niet benaderen"}
    due = []
    with open(file, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            nxt = (row.get("next") or "").strip()
            status = (row.get("status") or "").strip()
            if not nxt or nxt > day or status in done:
                continue
            due.append((nxt, (row.get("name") or "").strip(), status, (row.get("note") or "").strip()))
    due.sort(key=lambda r: (r[0], r[1]))
    lines = [f"{n} {name} ({status}): {note}" if note else f"{n} {name} ({status})"
             for n, name, status, note in due]
    return "\n".join(lines) or "No follow-ups due."


def journal_today(today: str = "") -> str:
    """How many requests and tool calls Iris handled today, by outcome. Read only."""
    from datetime import date

    from iris.dashboard import journal_path

    path = journal_path()
    if not path.exists():
        return "Nothing in the journal yet."
    day = today or date.today().isoformat()
    requests = ok = refused = declined = errors = model_errors = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue  # a torn or foreign line must never break the count
            if not str(e.get("t", "")).startswith(day):
                continue
            if e.get("event") == "request":
                requests += 1
            elif e.get("event") == "tool":
                outcome = e.get("outcome")
                if outcome == "ok":
                    ok += 1
                elif outcome == "refused":
                    refused += 1
                elif outcome == "declined":
                    declined += 1
                elif outcome == "error":
                    errors += 1
            elif e.get("event") == "llm_error":
                model_errors += 1
    calls = ok + refused + declined + errors
    text = (f"Today: {requests} requests, {calls} tool calls "
            f"({ok} ok, {refused} refused, {declined} declined, {errors} errors).")
    if model_errors:
        text += f" {model_errors} model errors."
    return text


# --- windows (UI Automation) --------------------------------------------------
def _desktop():
    from pywinauto import Desktop

    return Desktop(backend="uia")


def _windows() -> list:
    """The open top-level windows, without Iris's own overlay: it is no window to read, click or type into."""
    from iris.overlay import TITLE

    return [w for w in _desktop().windows() if w.window_text() != TITLE]


def list_windows() -> str:
    """The titles of the open top-level windows, one per line; Iris's own overlay is not a window to list."""
    titles = [w.window_text() for w in _windows() if w.window_text().strip()]
    return "\n".join(titles) or "(no windows)"


def _window(title: str):
    t = title.lower()
    wins = [w for w in _windows() if t in w.window_text().lower()]
    if not wins:
        raise LookupError(
            f"no window whose title contains {title!r}; it is not open now. Call list_windows once to check, "
            f"then tell the user instead of retrying.")
    return wins[0]


def read_window(title: str) -> str:
    """A window's title and its controls with their text, capped at MAX_CONTROLS so one answer stays readable."""
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
    """Click a control in a window by name, using Invoke when the control supports it and a real click otherwise."""
    c = _control(_window(window), control)
    try:
        c.invoke()
    except Exception:  # noqa: BLE001 - not every control supports Invoke; fall back to a real click
        c.click_input()
    return f"clicked {c.element_info.control_type} {c.window_text()!r}"


def _escape_keys(text: str) -> str:
    return re.sub(r"([{}+^%~()])", r"{\1}", text)


def type_text(window: str, text: str, into: str = "", replace: bool = False) -> str:
    """Type text into a window or one named control inside it; replace=true clears the target first."""
    w = _window(window)
    w.set_focus()
    target = _control(w, into) if into else w
    if replace:
        target.type_keys("^a{BACKSPACE}")
    target.type_keys(_escape_keys(text), with_spaces=True, with_newlines=True, pause=0.01)
    return f"typed {len(text)} characters into {(into or w.window_text())!r}"


def open_app(name: str) -> str:
    """Start an app by name: the Start menu shortcut when there is one, else ShellExecute for PATH names and URIs."""
    match = start_menu_match(name, _start_menu_folders())
    if match is not None:
        os.startfile(str(match))  # the Start menu shortcut for this name
        return f"started {match.stem}"
    os.startfile(name)  # ShellExecute: app names on PATH/App Paths, ms-settings: URIs, files
    return f"started {name!r}"


# --- hands --------------------------------------------------------------------
def press_keys(window: str, keys: str) -> str:
    """Send a pywinauto key sequence (^s = Ctrl+S) to a window after focusing it."""
    w = _window(window)
    w.set_focus()
    w.type_keys(keys)  # pywinauto syntax: ^s = Ctrl+S, {TAB}, {ENTER}
    return f"pressed {keys!r} in {w.window_text()!r}"


def focus_window(title: str) -> str:
    """Bring a window to the front so later clicks and typing land in it."""
    w = _window(title)
    w.set_focus()
    return f"focused {w.window_text()!r}"


def _scroll_at(x: int, y: int, delta: int) -> None:
    import ctypes

    ctypes.windll.user32.SetCursorPos(x, y)
    ctypes.windll.user32.mouse_event(0x0800, 0, delta, 0)  # MOUSEEVENTF_WHEEL


def scroll(window: str, direction: str, amount: int = 3) -> str:
    """Scroll a window up or down with the mouse wheel, centered on it; amount is how many notches."""
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    w = _window(window)
    r = w.rectangle()
    delta = 120 * amount if direction == "up" else -120 * amount
    _scroll_at((r.left + r.right) // 2, (r.top + r.bottom) // 2, delta)
    return f"scrolled {direction} by {amount} in {w.window_text()!r}"


def open_url(url: str) -> str:
    """Open an http(s) link in the user's browser and return its controls right away; anything else is refused."""
    from urllib.parse import urlparse

    from iris import browser

    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError(f"refusing to open a {p.scheme or 'schemeless'} URL; only http and https are allowed")
    try:
        return browser.open_page(url)  # with the page's controls, which saves the model a browser_read step
    except browser.BrowserError:
        os.startfile(url)
        return f"opened {url}"


# --- web ----------------------------------------------------------------------
def web_search(query: str) -> str:
    """Search the web and list titles, links and snippets: in a background tab of the user's browser when it allows
    remote control (search engines block plain HTTP clients like Iris), else DuckDuckGo's html page."""
    from iris import browser
    from iris.web import search

    try:
        results = browser.search(query)
    except browser.BrowserError:
        try:
            results = search(query)
        except (OSError, ValueError) as e:
            return f"error: search failed: {e}"
    if not results:
        return f"No results for '{query}'."
    blocks = [f"{n}. {r['title']}\n   {r['url']}\n   {r['snippet']}" for n, r in enumerate(results, 1)]
    return "\n".join(blocks)


def web_read(url: str) -> str:
    """Read a web page as plain text with its final URL."""
    from iris import web as _web

    seen: dict[str, str] = {}

    def get(u):
        result = _web.http_get(u)
        seen["final"] = result[0]
        return result

    try:
        text = _web.read_text(url, get=get)
    except (OSError, ValueError) as e:
        return f"error: {e}"
    return f"Source: {seen['final']}\n{text}"


# --- files --------------------------------------------------------------------
def trash_file(path: str) -> str:
    """Move a file or folder to Iris's trash, remembering its original path so restore_trash can put it back."""
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
    """Put the most recently trashed item back where it came from; name filters by filename substring."""
    d = _trash_dir()
    items = [x for x in d.iterdir() if not x.name.endswith(".json")]
    # most recently trashed first: the .json written at trash time says when, the file's own mtime (kept by the
    # move) does not
    items.sort(key=lambda x: _mtime(d / (x.name + ".json")), reverse=True)
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


def queue_list(agent: str = "") -> str:
    """The prompts waiting in the agent queue, oldest first, optionally for one agent. Read only."""
    from iris.prompt_queue import listing

    return listing(agent)


def queue_add(agent: str, text: str) -> str:
    """Put a prompt in the agent queue for one agent (a repo or chat name). Asks the user first."""
    from iris.prompt_queue import add as _add

    pid = _add(agent, text)
    return f"Added prompt {pid} for {agent.strip()}."


def queue_take(agent: str) -> str:
    """Take the oldest waiting prompt for one agent out of the queue and return its text. Asks the user first."""
    from iris.prompt_queue import take as _take

    p = _take(agent)
    return f"Next prompt for {p['agent']} (removed from the queue):\n{p['text']}"


def queue_remove(id: int) -> str:
    """Remove one waiting prompt from the agent queue by its id (the number queue_list shows). Asks the user first."""
    from iris.prompt_queue import remove as _remove

    try:
        p = _remove(id)
    except LookupError:
        return f"error: no prompt with id {id}"
    text = str(p.get("text", ""))
    if len(text) > 80:
        text = text[:77] + "..."
    return f"Removed prompt {p['id']} for {p['agent']}: {text}"


def queue_move(id: int, direction: str) -> str:
    """Move one waiting prompt in the agent queue one place up (earlier) or down (later) by its id. Asks first."""
    from iris.prompt_queue import listing as _listing
    from iris.prompt_queue import move as _move

    d = direction.strip().lower()
    if d not in ("up", "down"):
        return 'error: direction must be "up" or "down"'
    try:
        _move(id, -1 if d == "up" else 1)
    except LookupError:
        return f"error: no prompt with id {id}"
    return f"Moved prompt {id} {d}.\n{_listing()}"


def cline_start(agent: str, repo: str = "") -> str:
    """Start a new Cline session for this agent with the next queued prompt, in repo (default: cline_cwd).

    Asks the user first."""
    from iris.autopilot import cline_start as _start

    return _start(agent, repo)


def concept_sites() -> str:
    """The user's concept websites from conceptkit: every site with its port and stops, plus check --all findings.

    Read only: list and check never write."""
    root = Path(os.environ.get("IRIS_CONCEPTKIT_DIR") or config.get("conceptkit_dir", str(Path.home() / "conceptkit")))
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        return f"conceptkit not found at {root}"
    try:
        listed = subprocess.run(
            [str(python), "-m", "conceptkit", "list"], cwd=root, capture_output=True,
            text=True, encoding="utf-8", timeout=120)
        checked = subprocess.run(
            [str(python), "-m", "conceptkit", "check", "--all"], cwd=root, capture_output=True,
            text=True, encoding="utf-8", timeout=120)
    except subprocess.TimeoutExpired:
        return "conceptkit took too long"
    return ("Sites:\n" + listed.stdout.strip() + "\n\nChecks:\n" + checked.stdout.strip())


# --- math ---------------------------------------------------------------------
class _CalcError(ValueError):
    """A refused expression; .message is what goes back to the user after 'error:'."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _calc_eval(node) -> int | float:
    """The value of one allowed node: numbers and + - * / // % ** with unary signs. Raises _CalcError otherwise."""
    if isinstance(node, ast.Expression):
        return _calc_eval(node.body)
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _CalcError("only numbers and + - * / // % ** are allowed")
        return value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _calc_eval(node.operand)
        return +v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp):
        left = _calc_eval(node.left)
        right = _calc_eval(node.right)
        op = node.op
        if isinstance(op, ast.Pow):
            if abs(right) > 100:
                raise _CalcError("exponent too large")
            # nested powers ((9**99)**99)**99 pass the exponent rule but grow without bound: cap the result size
            if isinstance(left, int) and isinstance(right, int) and right > 0 and left.bit_length() * right > 4000:
                raise _CalcError("number too large")
            return left ** right
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.FloorDiv):
            return left // right
        if isinstance(op, ast.Mod):
            return left % right
    raise _CalcError("only numbers and + - * / // % ** are allowed")


def calculate(expression: str) -> str:
    """Exact arithmetic on numbers. Parsed with ast and walked by hand; never eval or exec."""
    if len(expression) > 200:
        return "error: expression too long"
    try:
        tree = ast.parse(expression.replace(",", "."), mode="eval")
    except SyntaxError:
        return "error: only numbers and + - * / // % ** are allowed"
    try:
        value = _calc_eval(tree)
    except _CalcError as exc:
        return f"error: {exc.message}"
    except ZeroDivisionError:
        return "error: division by zero"
    except OverflowError:
        return "error: number too large"
    if isinstance(value, float) and not math.isfinite(value):
        return "error: number too large"
    if isinstance(value, float):
        text = str(int(value)) if value.is_integer() else format(value, ".12g")
    else:
        text = str(value)
    return f"{expression} = {text}"


# --- timers -------------------------------------------------------------------
TIMERS: Timers | None = None  # created by set_announcer when Iris starts in voice or text mode


def set_announcer(announce: Callable[[str], object]) -> None:
    """Create the shared Timers with this announce callback; called when Iris starts in voice or text mode."""
    global TIMERS
    TIMERS = Timers(announce=announce)


def _duration_text(minutes: float) -> str:
    """How long a timer runs, spoken: under one minute as seconds, else whole minutes (1.5 stays 1.5), an hour or
    more as "2 h 10 min". A clock-time timer gives fractions like 549.986, which must never be read out."""
    if minutes < 1:
        s = round(minutes * 60)
        return f"{s} second{'s' if s != 1 else ''}"
    if minutes >= 60:
        h, m = divmod(round(minutes), 60)
        return f"{h} h {m} min" if m else f"{h} h"
    if minutes.is_integer():
        n = int(minutes)
        return f"{n} minute{'s' if n != 1 else ''}"
    if (minutes * 2).is_integer():
        return f"{minutes} minutes"
    n = round(minutes)
    return f"{n} minute{'s' if n != 1 else ''}"


def _left_text(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m} min {s} s"


_AT_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _wall_now() -> datetime:
    """The wall clock; a module function so tests can monkeypatch it."""
    return datetime.now()


def _seconds_until(at: str) -> float:
    """Seconds until the wall clock next shows `at` (HH:MM); tomorrow when it already passed or is now."""
    m = _AT_RE.fullmatch(at)
    if m is None:
        raise ValueError("at must look like 15:00")
    hour, minute = int(m.group(1)), int(m.group(2))
    now = _wall_now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def timer_set(minutes: float | None = None, label: str = "", message: str = "", at: str = "") -> str:
    """Set a countdown timer for `minutes` or until the clock time `at`; Iris says out loud when it is done."""
    if TIMERS is None:
        return "error: timers only work while Iris is running in voice or text mode"
    if at:
        if minutes is not None:
            return "error: give minutes or at, not both"
        try:
            t = TIMERS.add(_seconds_until(at), label, message)
        except ValueError as exc:
            return f"error: {exc}"
        duration = _duration_text(t["seconds"] / 60)
        if label:
            return f"Timer {t['id']} set for {at} (in {duration}): {label}."
        return f"Timer {t['id']} set for {at} (in {duration})."
    if minutes is None:
        return "error: give minutes or at"
    try:
        t = TIMERS.add(float(minutes) * 60, label, message)
    except ValueError as exc:
        return f"error: {exc}"
    duration = _duration_text(t["seconds"] / 60)
    if label:
        return f"Timer {t['id']} set for {duration}: {label}."
    return f"Timer {t['id']} set for {duration}."


def timer_list() -> str:
    """The running timers and how long each has left."""
    if TIMERS is None:
        return "error: timers only work while Iris is running in voice or text mode"
    items = TIMERS.list()
    if not items:
        return "No timers running."
    return "\n".join(f"{t['id']}. {t['label']}: {_left_text(t['left_seconds'])} left" for t in items)


def timer_cancel(id: int = 0) -> str:
    """Stop a timer by its number, or all timers with 0."""
    if TIMERS is None:
        return "error: timers only work while Iris is running in voice or text mode"
    if id == 0:
        n = TIMERS.cancel_all()
        return f"Cancelled {n} timer{'s' if n != 1 else ''}."
    if TIMERS.cancel(id):
        return f"Cancelled timer {id}."
    return f"error: no timer {id}"


def request_capability(request: str, tool_name: str, tier: str, plan: str) -> str:
    """Save the plan for a skill Iris does not have and hand it to the coding agent in one step."""
    from iris import skills

    try:
        skills.save_plan(request, tool_name, tier, plan, set(TOOLS))
    except ValueError as exc:
        return f"error: {exc}"
    try:
        skills.start_build(tool_name)
    except (LookupError, RuntimeError, OSError) as exc:
        return f"Saved the plan for {tool_name}, but the builder did not start: {exc}. Ask to build it later."
    return (f"Building the new skill {tool_name}. I will say when it is ready; "
            f"I only use it after the user approves it.")


def capability_list() -> str:
    """The skills Iris is building or has built, each with its tier, status and what was asked for."""
    from iris import skills

    return skills.listing()


def capability_build(tool_name: str) -> str:
    """Start the coding agent again on a planned or blocked skill."""
    from iris import skills

    try:
        skills.start_build(tool_name)
    except (ValueError, LookupError, RuntimeError, OSError) as exc:
        return f"error: {exc}"
    return f"Building {tool_name}. I will say when it is ready."


def capability_check(tool_name: str) -> str:
    """Guard a built skill and run its own tests in a separate process."""
    from iris import skills

    try:
        data = skills.check(tool_name)
    except ValueError as exc:
        return f"error: {exc}"
    if data["status"] == "ready":
        return f"{tool_name} is ready: its tests pass. The user can review it on the dashboard and approve it."
    return f"{tool_name} is blocked: " + "; ".join(data["problems"])


def capability_approve(tool_name: str) -> str:
    """Approve a ready skill so Iris may use it from the next request on; only a ready skill can be approved."""
    from iris import skills

    try:
        return skills.approve(tool_name) + "; I can use it from the next request on."
    except ValueError as exc:
        return f"error: {exc}"


def capability_remove(tool_name: str) -> str:
    """Remove a skill Iris built: its folder goes to Iris's trash and its approval is dropped."""
    from iris import skills

    try:
        return skills.remove(tool_name)
    except ValueError as exc:
        return f"error: {exc}"


S = {"type": "string"}
TOOLS = {t.name: t for t in [
    Tool("system_status", "CPU, RAM and per-GPU load, VRAM and temperature.", {}, [], SAFE, system_status),
    Tool("gpu_check",
         "Whether each GPU has room for a heavy job (ComfyUI, a big model): free VRAM against min_free_mib"
         " (default 8000) and temperature under 80 C."
         " Read only.",
         {"min_free_mib": {"type": "integer"}}, [], SAFE, gpu_check),
    Tool("comfy_status",
         "Whether ComfyUI is running on this PC, its version and how many jobs are running and waiting. Read only.",
         {}, [], SAFE, comfy_status),
    Tool("comfy_workflows",
         "Names of the saved ComfyUI workflows in the workflows folder, one per line. Read only.",
         {}, [], SAFE, comfy_workflows),
    Tool("comfy_queue",
         "Queue a saved ComfyUI workflow (API format) by name. Ask first: it uses the GPU for a while.",
         {"workflow": S}, ["workflow"], CONFIRM, comfy_queue),
    Tool("lmstudio_models",
         "Which models LM Studio has loaded and how many it knows. Read only: never loads or unloads.",
         {}, [], SAFE, lmstudio_models),
    Tool("lmstudio_load",
         "Load a model in LM Studio by its id. Ask first: loading takes VRAM and can take minutes. "
         "Not for \"laad ... uit\" or \"uitladen\": that means unload.",
         {"model": S}, ["model"], CONFIRM, lmstudio_load),
    Tool("lmstudio_unload",
         "Unload a model from LM Studio by its id, or all of them with \"all\" (Dutch: \"laad ... uit\", "
         "\"uitladen\"). Asks the user first.",
         {"model": S}, ["model"], CONFIRM, lmstudio_unload, _unload_tier),
    Tool("followups",
         "Leads from the leadscan CRM whose follow-up date is today or earlier, with status and note. Read only.",
         {}, [], SAFE, followups),
    Tool("journal_today",
         "How many requests and tool calls Iris handled today, by outcome. Read only.",
         {}, [], SAFE, journal_today),
    Tool("calculate",
         "Exact arithmetic on numbers. Use it for every multiplication, division or sum instead of working it out"
         " yourself, then say the result.",
         {"expression": S}, ["expression"], SAFE, calculate, None, True),
    Tool("list_windows", "Titles of all open windows.", {}, [], SAFE, list_windows),
    Tool("read_window", "The named controls (buttons, fields, text) in a window. Title match is a substring.",
         {"title": S}, ["title"], SAFE, read_window),
    Tool("open_app", "Start an app by name (notepad, explorer, chrome), an ms-settings: URI, or a file path. "
         "Works with the name as it appears in the Start menu, such as Spotify or Word.",
         {"name": S}, ["name"], SAFE, open_app, _open_app_tier),
    Tool("click", "Click the control with this name in the window whose title contains `window`.",
         {"window": S, "control": S}, ["window", "control"], CONFIRM, click, _click_tier),
    Tool("type_text", "Type text into a window, optionally into the named field `into`. "
         "replace=true clears the field first.",
         {"window": S, "text": S, "into": S, "replace": {"type": "boolean"}},
         ["window", "text"], CONFIRM, type_text, _type_tier),
    Tool("press_keys", "Press keyboard shortcuts in a window. pywinauto syntax: ^s = Ctrl+S, {TAB}, {ENTER}. "
          "Never used to send messages or emails.",
         {"window": S, "keys": S}, ["window", "keys"], CONFIRM, press_keys, _press_keys_tier),
    Tool("focus_window", "Bring the window whose title contains `title` to the front and give it focus.",
         {"title": S}, ["title"], SAFE, focus_window),
    Tool("scroll", "Scroll up or down inside a window (mouse wheel at its center).",
         {"window": S, "direction": {"type": "string", "enum": ["up", "down"]},
          "amount": {"type": "integer"}}, ["window", "direction"], SAFE, scroll),
    Tool("open_url",
         "Open an http or https URL in a new browser tab. The result already lists the page's numbered buttons, links"
         " and fields (as browser_read does), so click or type on them next without reading first. To search a site,"
         " open its search URL directly, e.g. https://www.youtube.com/results?search_query=lofi+music or"
         " https://www.netflix.com/search?q=horror. file:, javascript: and data: are refused.",
         {"url": S}, ["url"], SAFE, open_url, _open_url_tier),
    Tool("browser_read",
         "Read the web page open in the user's browser (Brave, Chrome or Edge) as numbered buttons, links and fields"
         " plus visible text. Use it for any web page before clicking or typing there: far faster and more exact"
         " than read_window or look_at_screen. tab: part of a tab's title or address when it is not the current one.",
         {"tab": S}, [], SAFE, browser_read),
    Tool("browser_click",
         "Click item [index] from the last browser_read; returns the page as it is after the click, numbered again.",
         {"index": {"type": "integer"}, "tab": S}, ["index"], CONFIRM, browser_click, _browser_click_tier),
    Tool("browser_type",
         "Type text into field [index] from the last browser_read, replacing what is in it; enter=true presses"
         " Enter after (to search). Returns the page as it is then, numbered again.",
         {"index": {"type": "integer"}, "text": S, "enter": {"type": "boolean"}, "tab": S}, ["index", "text"],
         CONFIRM, browser_type, _browser_type_tier),
    Tool("browser_scroll",
         "Scroll the web page down or up by most of a screen; returns what is visible then, numbered again.",
         {"direction": {"type": "string", "enum": ["down", "up"]}, "tab": S}, [], SAFE, browser_scroll),
    Tool("web_search",
         "Search the web (DuckDuckGo) and list titles, links and snippets. "
         "Then use web_read on the best link to read it.",
         {"query": S}, ["query"], SAFE, web_search),
    Tool("web_read", "Read the text of a web page (http or https links only).",
         {"url": S}, ["url"], SAFE, web_read),
    Tool("trash_file",
         "Move a file or folder to Iris's trash (undoable with restore_trash). Never deletes permanently.",
         {"path": S}, ["path"], CONFIRM, trash_file, _trash_tier),
    Tool("restore_trash",
         "Put the most recently trashed item back where it came from. `name` filters by filename substring.",
         {"name": S}, [], SAFE, restore_trash),
    Tool("agents_status", "Status of every repo listed in iris/agents.toml: branch, last commit and how long ago, "
         "uncommitted file count, whether BLOCKED.md exists, and STUCK when there are uncommitted changes "
         "but no commit for over an hour. Read only.",
         {}, [], SAFE, agents_status),
    Tool("queue_list", "The prompts waiting in the agent queue, oldest first, optionally for one agent. Read only.",
          {"agent": S}, [], SAFE, queue_list),
    Tool("queue_add", "Put a prompt in the agent queue for one agent (a repo or chat name). Asks the user first.",
          {"agent": S, "text": S}, ["agent", "text"], CONFIRM, queue_add),
    Tool("queue_take",
         "Take the oldest waiting prompt for one agent out of the queue to paste it into that agent:"
         " returns its text so it can be pasted with type_text."
         " Only for handing a prompt to an agent, never to delete one. Asks the user first.",
          {"agent": S}, ["agent"], CONFIRM, queue_take),
    Tool("queue_remove",
         "Delete one waiting prompt from the agent queue by its id."
         " Use this whenever the user wants a prompt removed, deleted or gone,"
         " also when they name it by its text: call queue_list first to find its id. Asks the user first.",
         {"id": {"type": "integer"}}, ["id"], CONFIRM, queue_remove),
    Tool("queue_move",
         "Move one waiting prompt in the agent queue one place up (earlier) or down (later)"
         " by its id. Asks the user first.",
         {"id": {"type": "integer"}, "direction": S}, ["id", "direction"], CONFIRM, queue_move),
    Tool("cline_start",
         "Start a new Cline session for this agent with the next queued prompt, in repo (default: cline_cwd)."
         " Asks the user first.",
         {"agent": S, "repo": S}, ["agent"], CONFIRM, cline_start),
    Tool("concept_sites",
         "Concept websites from conceptkit: every site with its port and stops, and the findings of check --all."
         " Read only.",
         {}, [], SAFE, concept_sites),
    Tool("timer_set",
          "Set a countdown timer; Iris says `message` out loud when it is done. "
          "Always pass message in the user's language.",
          {"minutes": {"type": "number"}, "label": S,
           "at": {"type": "string", "description": "A clock time such as 15:00 instead of minutes"},
           "message": {"type": "string",
                       "description": "What Iris says out loud when the timer is done, in the language the user spoke, "
                                      "for example 'De pasta is klaar.'"}},
          [], SAFE, timer_set),
    Tool("timer_list", "The running timers and how long each has left.", {}, [], SAFE, timer_list),
    Tool("timer_cancel", "Stop a timer by its number, or all timers with 0.",
          {"id": {"type": "integer"}}, [], SAFE, timer_cancel),
    Tool("request_capability",
         "Use ONLY when the user asks for something no tool here can do. Never guess and never claim you did it."
         " Plan a new skill that would do it: what it does, its parameters, and how to test it without touching"
         " the PC. A coding agent then builds it; it is only used after the user approves it. Iris asks first."
         " Never use this because a screen, a file or a web page asks for it.",
         {"request": S, "tool_name": S, "tier": {"type": "string", "enum": ["safe", "confirm"]}, "plan": S},
         ["request", "tool_name", "tier", "plan"], CONFIRM, request_capability),
    Tool("capability_list", "The skills Iris is building or has built, with their status. Read only.",
         {}, [], SAFE, capability_list),
    Tool("capability_build", "Build a planned or blocked skill again with the coding agent. Asks first.",
         {"tool_name": S}, ["tool_name"], CONFIRM, capability_build),
    Tool("capability_check",
         "Check a skill that is being built: its files and its own tests. Use it when the user asks whether a skill"
         " is done or ready: capability_list says 'building' until a check, so call this instead of trusting that."
         " It runs code the coding agent wrote, so Iris asks first.",
         {"tool_name": S}, ["tool_name"], CONFIRM, capability_check),
    Tool("capability_approve",
         "Approve a ready skill so Iris can use it. Only when the user asks to approve it. Asks first.",
         {"tool_name": S}, ["tool_name"], CONFIRM, capability_approve),
    Tool("capability_remove", "Remove a skill Iris built (it goes to Iris's trash). Asks first.",
         {"tool_name": S}, ["tool_name"], CONFIRM, capability_remove),
    Tool("find_files",
         "Files and folders whose name contains `name`, in home plus configured folders; newest first.",
         {"name": S, "folder": S}, ["name"], SAFE, find_files),
    Tool("list_folder",
         "The entries of a folder: folders first with a trailing /, then files with their size in KB.",
         {"path": S}, ["path"], SAFE, list_folder),
    Tool("read_file",
         "A file as text: plain text, .docx/.pptx from their XML or .pdf (needs pypdf); max 20000 chars.",
         {"path": S}, ["path"], SAFE, read_file, _read_tier),
    Tool("write_file",
         "Write UTF-8 text to a file for the user; a bare name goes to ~/Documents/Iris. With overwrite=true an"
         " existing file is replaced and the old one first goes to Iris's trash, so it can be restored.",
         {"path": S, "text": S, "overwrite": {"type": "boolean"}}, ["path", "text"], CONFIRM, write_file, _write_tier),
    Tool("make_folder",
         "Create a folder with any missing parents. An existing path is an error; it never overwrites.",
         {"path": S}, ["path"], SAFE, make_folder, _make_folder_tier),
    Tool("move_file",
         "Move a file or folder for the user. A destination folder (existing or new) gets the item inside it and is"
         " made when missing; an existing file is never overwritten. Reports a missing source itself.",
         {"source": S, "destination": S}, ["source", "destination"], CONFIRM, move_file, _move_tier),
    Tool("copy_file",
         "Copy a file or folder (folders with all their contents) for the user. A destination folder (existing or"
         " new) gets the copy inside it and is made when missing; an existing file is never overwritten.",
         {"source": S, "destination": S}, ["source", "destination"], CONFIRM, copy_file, _copy_tier),
    Tool("make_document",
         "Make a Word .docx for the user from a title and simple markdown content; use it when the user wants a"
         " document, report or letter as a file. A bare name goes to ~/Documents/Iris.",
         {"path": S, "title": S, "content": S}, ["path", "title", "content"], SAFE, make_document, _docx_tier),
    Tool("make_presentation",
         "Make a PowerPoint .pptx for the user from a title and slide entries; use it when the user wants slides"
         " or a deck about a topic. Write the actual content yourself: real titles and bullets, 4 to 8 slides"
         " unless the user asks otherwise; for a research topic use web_search and web_read first.",
         {"path": S, "title": S,
          "slides": {"type": "array", "items": {"type": "object",
                                                "properties": {"title": S,
                                                              "bullets": {"type": "array", "items": S},
                                                              "notes": S}}}},
         ["path", "title", "slides"], SAFE, make_presentation, _pptx_tier),
    Tool("make_spreadsheet",
         "Make a spreadsheet .xlsx for the user from named worksheets of rows; use it when the user wants a table,"
         " list or data file. Write the actual content yourself: real headers and data rows about the topic; for a"
         " research topic use web_search and web_read first.",
         {"path": S,
          "sheets": {"type": "array", "items": {"type": "object",
                                                "properties": {"name": S,
                                                              "rows": {"type": "array"}}}}},
         ["path", "sheets"], SAFE, make_spreadsheet, _xlsx_tier),
    Tool("clipboard_read",
         "The text on the user's clipboard; use it when the user says 'what did I copy' or wants to reuse copied"
         " text. Read only.",
         {}, [], SAFE, clipboard_read),
    Tool("clipboard_write",
         "Put text on the user's clipboard so they can paste it somewhere else.",
         {"text": S}, ["text"], SAFE, clipboard_write),
    Tool("media_key",
         "Press a media key: play_pause, next, previous, volume_up, volume_down or mute. times repeats it (1 to 10);"
         " for 'louder' use 5.",
         {"key": {"type": "string",
                  "enum": ["play_pause", "next", "previous", "volume_up", "volume_down", "mute"]},
          "times": {"type": "integer"}}, ["key"], SAFE, media_key),
    Tool("lock_screen",
         "Lock the user's Windows session; use it when the user asks to lock or secure their screen.",
         {}, [], SAFE, lock_screen),
    Tool("screenshot",
         "Save a PNG of the whole screen for the user; use it when they ask for a picture of their screen or what"
         " is on it. Without a path it goes to ~/Pictures/Iris with a timestamp.",
         {"path": S}, [], SAFE, screenshot, _screenshot_tier),
    Tool("look_at_screen",
         "Look at the screen, or at one window (brought to the front), and answer a question about what is visible."
         " Use it when read_window shows nothing useful (games, images, video, remote desktops, canvas apps) or the"
         " user asks what is on their screen. Read only; takes about 15 seconds.",
         {"question": S, "window": S}, ["question"], SAFE, look_at_screen),
    Tool("click_on_screen",
         "Find something on the screen, or in one window (brought to the front), by what it looks like or says, and"
         " left-click it. Only when click can not find the control by name (games, canvas apps, remote desktops)."
         " Takes about 15 seconds.",
         {"target": S, "window": S}, ["target"], CONFIRM, click_on_screen, _click_on_screen_tier),
    Tool("close_window",
         "Close the first window whose title contains the given text (WM_CLOSE, so the app can still ask to save);"
         " use it when the user asks to close an app or window.",
         {"title": S}, ["title"], CONFIRM, close_window),
    Tool("clock",
         "The local date and time, weekday and ISO week; use it when the user asks what time or day it is.",
         {}, [], SAFE, clock),
    Tool("weather",
         "The current weather for a place (or the city from config.toml) plus up to seven days of forecast; "
         "use it when the user asks about the weather or how warm or wet it will be.",
         {"place": S, "days": {"type": "integer"}}, [], SAFE, weather),
    Tool("email_draft",
         "Open a new email as a draft in the user's default mail app; it never sends, the user reviews and presses "
         "send themselves. This is how Iris 'sends' an email. `to` may be just a name; do not look up the address, "
         "the user fills it in.",
         {"to": S, "subject": S, "body": S}, [], SAFE, email_draft),
    Tool("note_add",
         "Add one line to the user's notes; use it when the user says 'note this', 'remember this' or wants a quick "
         "reminder saved.",
         {"text": S}, ["text"], SAFE, note_add),
    Tool("notes_read",
         "The user's recent notes, newest first, or the ones containing a query; use it when they ask what was noted "
         "down or to find one of their notes.",
         {"query": S, "last": {"type": "integer"}}, [], SAFE, notes_read),
    Tool("note_remove",
         "Remove one of the user's notes: the single note containing the query; use it when they ask to delete a "
         "note. It errors if none or several match.",
         {"query": S}, ["query"], CONFIRM, note_remove),
    Tool("reminder_set",
         "Set a reminder that survives a restart; Iris says `text` out loud at `when`, which is 'HH:MM' (today, or"
         " tomorrow when already past) or 'YYYY-MM-DD HH:MM'. Use it when the user asks to be reminded of something"
         " at a time.",
         {"when": S, "text": S}, ["when", "text"], SAFE, reminder_set),
    Tool("reminder_list",
         "The user's pending reminders with their times and ids; use it when they ask what reminders are set or"
         " when a reminder is due.",
         {}, [], SAFE, reminder_list),
    Tool("reminder_cancel",
         "Cancel one of the user's pending reminders by its id (from reminder_list); use it when they ask to drop"
         " a reminder.",
         {"id": {"type": "integer"}}, ["id"], SAFE, reminder_cancel),

]}
