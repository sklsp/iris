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
from collections.abc import Callable
from dataclasses import dataclass
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
    """Names of Jay's saved ComfyUI workflows in the workflows folder, one per line."""
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
    """Unload a model from LM Studio by its id, or all of them with "all". Asks Jay first."""
    from iris.dashboard import ModelActionError, model_action

    try:
        model_action("unload", {"model": model})
    except ModelActionError as exc:
        return f"error: {exc}"
    return f"unloaded {model}"


def _unload_tier(args: dict) -> str:
    from iris.llm import effective_model  # read at call time so a model switch applies at once

    model = args.get("model")
    if model == "all" or model == effective_model():
        return NEVER
    return CONFIRM


def followups(today: str = "") -> str:
    """Leads from Jay's leadscan CRM whose follow-up date is today or earlier, with status and note."""
    import csv
    from datetime import date

    folder = Path(os.environ.get("IRIS_LEADSCAN_DIR", "W:/leadscan"))
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
    requests = ok = refused = declined = errors = 0
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
    calls = ok + refused + declined + errors
    return (f"Today: {requests} requests, {calls} tool calls "
            f"({ok} ok, {refused} refused, {declined} declined, {errors} errors).")


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
        raise LookupError(
            f"no window whose title contains {title!r}; it is not open now. Call list_windows once to check, "
            f"then tell Jay instead of retrying.")
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


def _open_url_tier(args: dict) -> str:
    from urllib.parse import urlparse

    scheme = urlparse(str(args.get("url", ""))).scheme.lower()
    return SAFE if scheme in ("http", "https") else NEVER


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


def queue_list(agent: str = "") -> str:
    """The prompts waiting in Jay's agent queue, oldest first, optionally for one agent. Read only."""
    from iris.prompt_queue import listing

    return listing(agent)


def queue_add(agent: str, text: str) -> str:
    """Put a prompt in Jay's agent queue for one agent (a repo or chat name). Asks Jay first."""
    from iris.prompt_queue import add as _add

    pid = _add(agent, text)
    return f"Added prompt {pid} for {agent.strip()}."


def queue_take(agent: str) -> str:
    """Take the oldest waiting prompt for one agent out of the queue and return its text. Asks Jay first."""
    from iris.prompt_queue import take as _take

    p = _take(agent)
    return f"Next prompt for {p['agent']} (removed from the queue):\n{p['text']}"


def queue_remove(id: int) -> str:
    """Remove one waiting prompt from Jay's agent queue by its id (the number queue_list shows). Asks Jay first."""
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
    """Move one waiting prompt in Jay's agent queue one place up (earlier) or down (later) by its id. Asks Jay first."""
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
    """Start a new Cline session for this agent with the next queued prompt, in repo (default W:/Iris).

    Asks Jay first."""
    from iris.autopilot import cline_start as _start

    return _start(agent, repo)


def concept_sites() -> str:
    """Jay's concept websites from conceptkit: every site with its port and stops, plus check --all findings.

    Read only: list and check never write."""
    root = Path(os.environ.get("IRIS_CONCEPTKIT_DIR") or "W:/conceptkit")
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
         "Load a model in LM Studio by its id. Ask first: loading takes VRAM and can take minutes.",
         {"model": S}, ["model"], CONFIRM, lmstudio_load),
    Tool("lmstudio_unload",
         "Unload a model from LM Studio by its id, or all of them with \"all\". Asks Jay first.",
         {"model": S}, ["model"], CONFIRM, lmstudio_unload, _unload_tier),
    Tool("followups",
         "Leads from Jay's leadscan CRM whose follow-up date is today or earlier, with status and note. Read only.",
         {}, [], SAFE, followups),
    Tool("journal_today",
         "How many requests and tool calls Iris handled today, by outcome. Read only.",
         {}, [], SAFE, journal_today),
    Tool("list_windows", "Titles of all open windows.", {}, [], SAFE, list_windows),
    Tool("read_window", "The named controls (buttons, fields, text) in a window. Title match is a substring.",
         {"title": S}, ["title"], SAFE, read_window),
    Tool("open_app", "Start an app by name (notepad, explorer, chrome), an ms-settings: URI, or a file path.",
         {"name": S}, ["name"], SAFE, open_app),
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
    Tool("open_url", "Open an http or https URL in the default browser. file:, javascript: and data: are refused.",
         {"url": S}, ["url"], SAFE, open_url, _open_url_tier),
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
    Tool("queue_list", "The prompts waiting in Jay's agent queue, oldest first, optionally for one agent. Read only.",
          {"agent": S}, [], SAFE, queue_list),
    Tool("queue_add", "Put a prompt in Jay's agent queue for one agent (a repo or chat name). Asks Jay first.",
          {"agent": S, "text": S}, ["agent", "text"], CONFIRM, queue_add),
    Tool("queue_take",
         "Take the oldest waiting prompt for one agent out of the queue to paste it into that agent:"
         " returns its text so it can be pasted with type_text."
         " Only for handing a prompt to an agent, never to delete one. Asks Jay first.",
          {"agent": S}, ["agent"], CONFIRM, queue_take),
    Tool("queue_remove",
         "Delete one waiting prompt from Jay's agent queue by its id."
         " Use this whenever Jay wants a prompt removed, deleted or gone,"
         " also when he names it by its text: call queue_list first to find its id. Asks Jay first.",
         {"id": {"type": "integer"}}, ["id"], CONFIRM, queue_remove),
    Tool("queue_move",
         "Move one waiting prompt in Jay's agent queue one place up (earlier) or down (later)"
         " by its id. Asks Jay first.",
         {"id": {"type": "integer"}, "direction": S}, ["id", "direction"], CONFIRM, queue_move),
    Tool("cline_start",
         "Start a new Cline session for this agent with the next queued prompt, in repo (default W:/Iris)."
         " Asks Jay first.",
         {"agent": S, "repo": S}, ["agent"], CONFIRM, cline_start),
    Tool("concept_sites",
         "Jay's concept websites from conceptkit: every site with its port and stops, and the findings of check --all."
         " Read only.",
         {}, [], SAFE, concept_sites),
]}
