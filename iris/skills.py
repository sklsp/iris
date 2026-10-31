"""Skills Iris builds for herself when a request needs something no tool can do.

The life of a skill, each step in its own folder under skills_dir() (IRIS_SKILLS, else ~/.iris/skills):

    planned   plan.md and meta.json: the request, the name and the tier the user said yes to
    building  a coding agent (Cline by default, or config.toml `builder`) writes skill.toml, skill.py and
              test_skill.py next to the plan, then an empty DONE file
    ready     check() passed: the files are complete, the guard found nothing and the skill's own tests pass
    blocked   check() failed; problems says why, and a rebuild can try again
    approved  the user approved it: approved.json holds the hash of exactly the code they approved

Only approved skills whose files still match the approved hash are offered to the model, and they never run
inside Iris: every call starts `python -I` on skill.py with the arguments as JSON on stdin, so a skill can not
reach Iris's tiers or tools in memory. The tier comes from the plan the user confirmed, never from the builder.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from iris import config
from iris.policy import CONFIRM, SAFE, _trash_dir

NAME = re.compile(r"[a-z][a-z0-9_]{2,39}")
RUN_TIMEOUT = 120    # seconds one skill call may take
TEST_TIMEOUT = 300   # seconds the skill's own tests may take
MAX_RESULT = 20_000

# What a skill may never contain: Iris's own package or folder, or the approvals. ponytail: a text match, not a
# sandbox; the user's review is the real gate, this only catches the obvious.
_FORBIDDEN = [
    (re.compile(r"^\s*(from|import)\s+iris\b", re.M), "imports Iris itself"),
    (re.compile(r"\.iris\b"), "mentions the ~/.iris folder"),
    (re.compile(r"approved\.json|tier_lock"), "mentions Iris's approvals or tier lock"),
    (re.compile(r"site-packages"), "mentions installed packages"),
]

# A tiny host: load skill.py, call run(**args) with the JSON on stdin, print what it returns.
_HOST = ("import importlib.util as u, json, sys\n"
         "s = u.spec_from_file_location('skill', sys.argv[1]); m = u.module_from_spec(s); s.loader.exec_module(m)\n"
         "print(m.run(**json.loads(sys.stdin.read() or '{}')))\n")

PLAN = """# Skill: {name}

- Tier: {tier}
- Asked: {request}

## Plan

{plan}

## How to build it

Work only in this folder: {folder}
1. skill.toml with: description = "<one or two sentences: what it does and when to use it>",
   required = [<names>], and a [params] table with one sub-table per parameter in JSON schema style, for example
   [params.city]
   type = "string"
   description = "The city"
2. skill.py with `def run(**kwargs) -> str` taking exactly those parameters and returning a short text. It runs as a
   separate process. Only the Python standard library and packages that are already installed. Errors are returned
   as text starting with "error:", never raised. Expand ~ and %VARIABLES% in any path argument
   (os.path.expanduser and os.path.expandvars): the model often passes paths like %USERPROFILE%\Downloads.
3. test_skill.py with pytest tests that import skill.py from this folder and never touch the network, the desktop or
   files outside pytest's tmp_path: fake those with monkeypatch.
4. Run `{python} -I -m pytest -q -p no:cacheprovider test_skill.py` in this folder until it passes.
5. Create an empty file named DONE in this folder. Then stop.

Never import or read Iris itself, never touch anything outside this folder, no network calls in tests,
no em dashes or en dashes.
"""


def skills_dir() -> Path:
    return Path(os.environ.get("IRIS_SKILLS") or (Path.home() / ".iris" / "skills"))


def _folder(name: str) -> Path:
    if not NAME.fullmatch(name):
        raise ValueError(f"invalid skill name {name!r}")
    return skills_dir() / name


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def meta(name: str) -> dict:
    path = _folder(name) / "meta.json"
    if not path.exists():
        raise ValueError(f"no skill named {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_meta(name: str, data: dict) -> None:
    _write(_folder(name) / "meta.json", json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def save_plan(request: str, name: str, tier: str, plan: str, taken: set[str]) -> Path:
    """Create the skill folder with plan.md and meta.json (status planned); taken holds the existing tool names."""
    folder = _folder(name)
    if name in taken:
        raise ValueError(f"{name} already exists")
    if tier not in (SAFE, CONFIRM):
        raise ValueError("tier must be 'safe' or 'confirm'")
    if not request.strip() or not plan.strip():
        raise ValueError("the request and the plan may not be empty")
    if len(plan) > 4000:
        raise ValueError("plan is too long (max 4000 chars)")
    if folder.exists():
        raise ValueError(f"a skill named {name} exists")
    folder.mkdir(parents=True)
    _write(folder / "plan.md", PLAN.format(name=name, tier=tier, request=request, plan=plan, folder=folder,
                                           python=sys.executable))
    _save_meta(name, {"name": name, "tier": tier, "request": request, "status": "planned",
                      "created": datetime.now().isoformat(timespec="seconds")})
    return folder


def listing() -> str:
    d = skills_dir()
    rows = []
    for m in sorted(d.glob("*/meta.json")) if d.is_dir() else []:
        data = json.loads(m.read_text(encoding="utf-8"))
        status = "approved" if _approved_ok(data["name"]) else data["status"]
        rows.append(f"{data['name']} ({data['tier']}): {status}, asked: {str(data['request'])[:60]}")
    return "\n".join(rows) or "No skills yet."


def build_prompt(name: str) -> str:
    folder = _folder(name)
    return (f"Read {folder / 'plan.md'} and build the skill exactly as it says, working only in {folder}. "
            f"Do NOT call attempt_completion until the DONE file exists.")


def start_build(name: str, start: Callable[[str, str], str] | None = None) -> str:
    """Hand the plan to the coding agent. config.toml `builder` is a command list with "{prompt}" in it; without
    one Iris starts a Cline session. A planned or blocked skill can be (re)built; old build files go first."""
    data = meta(name)
    if data["status"] not in ("planned", "blocked"):
        raise ValueError(f"{name} is {data['status']}, not planned or blocked")
    folder = _folder(name)
    for f in ("skill.toml", "skill.py", "test_skill.py", "DONE", "check.txt"):
        (folder / f).unlink(missing_ok=True)
    prompt = build_prompt(name)
    builder = config.load().get("builder") or []
    if start is None and builder:
        argv = [a.replace("{prompt}", prompt) for a in builder]
        subprocess.Popen(argv, cwd=folder, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        result = f"started {argv[0]}"
    else:
        if start is None:
            from iris.autopilot import start_in_hub  # local: autopilot imports the agent

            start = start_in_hub
        result = start(prompt, str(folder))
    data.update(status="building", started=datetime.now().isoformat(timespec="seconds"), builder=result)
    data.pop("problems", None)
    _save_meta(name, data)
    return result


def _spec(folder: Path) -> tuple[dict, list[str]]:
    """skill.toml as (spec, problems)."""
    try:
        spec = tomllib.loads((folder / "skill.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return {}, [f"skill.toml is missing or broken: {exc}"]
    problems = []
    if not isinstance(spec.get("description"), str) or not spec["description"].strip():
        problems.append("skill.toml has no description")
    params, required = spec.get("params", {}), spec.get("required", [])
    if not isinstance(params, dict) or not all(isinstance(v, dict) for v in params.values()):
        problems.append("skill.toml params must be a table of tables")
    elif not isinstance(required, list) or not set(required) <= set(params):
        problems.append("skill.toml required must list parameter names")
    return spec, problems


def guard(folder: Path) -> list[str]:
    problems = []
    for f in ("skill.py", "test_skill.py"):
        text = (folder / f).read_text(encoding="utf-8") if (folder / f).exists() else ""
        if not text:
            problems.append(f"{f} is missing")
        problems += [f"{f} {why}" for rx, why in _FORBIDDEN if rx.search(text)]
    return problems


def check(name: str, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> dict:
    """Guard the files, run the skill's own tests in a separate process, and mark it ready or blocked."""
    data = meta(name)
    if data["status"] not in ("building", "ready", "blocked"):
        raise ValueError(f"{name} has not been built")
    folder = _folder(name)
    _, problems = _spec(folder)
    problems += guard(folder)
    output = ""
    if not problems:
        try:
            r = run([sys.executable, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_skill.py"],
                    cwd=folder, capture_output=True, text=True, timeout=TEST_TIMEOUT)
            output = (r.stdout + r.stderr).strip()
            if r.returncode != 0:
                last = [line for line in output.splitlines() if line.strip()]
                problems.append(f"tests failed: {last[-1] if last else 'no output'}")
        except subprocess.TimeoutExpired:
            problems.append("tests timed out")
    _write(folder / "check.txt", output + "\n")
    data.update(status="blocked" if problems else "ready", problems=problems,
                checked=datetime.now().isoformat(timespec="seconds"))
    _save_meta(name, data)
    return data


def _digest(name: str, tier: str) -> str:
    folder = _folder(name)
    h = hashlib.sha256(tier.encode())
    for f in ("skill.toml", "skill.py"):
        h.update(b"\0" + (folder / f).read_bytes())
    return h.hexdigest()


def _approvals() -> dict:
    path = skills_dir() / "approved.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _approved_ok(name: str) -> bool:
    entry = _approvals().get(name)
    try:
        return bool(entry) and entry == _digest(name, meta(name)["tier"])
    except (OSError, ValueError):
        return False


def approve(name: str) -> str:
    data = meta(name)
    if data["status"] != "ready":
        raise ValueError(f"{name} is {data['status']}; only a ready skill can be approved")
    approvals = _approvals()
    approvals[name] = _digest(name, data["tier"])
    _write(skills_dir() / "approved.json", json.dumps(approvals, indent=1) + "\n")
    return f"approved {name}"


def remove(name: str) -> str:
    """Move the skill's folder to Iris's trash and drop its approval."""
    folder = _folder(name)
    if not folder.exists():
        raise ValueError(f"no skill named {name}")
    approvals = _approvals()
    if approvals.pop(name, None) is not None:
        _write(skills_dir() / "approved.json", json.dumps(approvals, indent=1) + "\n")
    shutil.move(str(folder), str(_trash_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}_skill_{name}"))
    return f"removed {name}; it is in Iris's trash"


def run_skill(name: str, args: dict, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> str:
    folder = _folder(name)
    try:
        r = run([sys.executable, "-I", "-c", _HOST, str(folder / "skill.py")], input=json.dumps(args),
                cwd=folder, capture_output=True, text=True, timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"error: {name} took longer than {RUN_TIMEOUT} seconds"
    if r.returncode != 0:
        last = [line for line in r.stderr.splitlines() if line.strip()]
        return f"error: {name} failed: {last[-1] if last else 'no output'}"
    out = r.stdout.strip()
    return out[:MAX_RESULT] + ("... (truncated)" if len(out) > MAX_RESULT else "")


def approved_tools(taken: set[str]) -> list[dict]:
    """(name, tier, schema bits) of every approved skill whose files still match; a skill named like a core tool,
    or one changed after approval, is left out."""
    d = skills_dir()
    out = []
    for m in sorted(d.glob("*/meta.json")) if d.is_dir() else []:
        name = m.parent.name
        if name in taken or not NAME.fullmatch(name) or not _approved_ok(name):
            continue
        spec, problems = _spec(m.parent)
        if problems:
            continue
        out.append({"name": name, "tier": meta(name)["tier"], "description": spec["description"],
                    "params": spec.get("params", {}), "required": spec.get("required", [])})
    return out


def finished_builds() -> list[str]:
    d = skills_dir()
    return [m.parent.name for m in sorted(d.glob("*/meta.json")) if d.is_dir()
            and (m.parent / "DONE").exists() and meta(m.parent.name)["status"] == "building"]


def watch(announce: Callable[[str], object], interval: float = 30, stop: threading.Event | None = None,
          check_fn: Callable[[str], dict] = check) -> threading.Thread:
    """A daemon thread that checks every finished build and says how it went."""
    stop = stop or threading.Event()

    def loop() -> None:
        while not stop.is_set():
            for name in finished_builds():
                try:
                    result = check_fn(name)
                except Exception as exc:  # noqa: BLE001 - one bad build must never stop the watcher
                    _save_meta(name, {**meta(name), "status": "blocked", "problems": [f"check failed: {exc}"]})
                    announce(f"I could not check the new skill {name}: {exc}")
                    continue
                if result["status"] == "ready":
                    announce(f"The new skill {name} is built and its tests pass. Review it on the dashboard, "
                             f"or ask me to approve {name}.")
                else:
                    announce(f"The new skill {name} is blocked: {'; '.join(result['problems'])[:200]}")
            stop.wait(interval)

    t = threading.Thread(target=loop, daemon=True, name="iris-skill-watch")
    t.start()
    return t
