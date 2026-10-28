"""The Iris dashboard: python -m iris dashboard serves http://127.0.0.1:8765.

If that port is taken, pass another one: python -m iris dashboard --port 8766 (or set
IRIS_DASHBOARD_PORT). The page plus these JSON routes are polled every 3 s by the browser:

    /api/system   CPU %, RAM used/total, per-GPU load, VRAM and temperature, with a
                  five-minute history for the sparklines
    /api/agents   one row per watched repo (branch, last commit and age, uncommitted
                  count, BLOCKED.md, STUCK), stuck rows first; cached 30 s
    /api/runs     one progress row per watched repo that has a PROGRESS-*.md file: item counts
                  and whether the run is done; not-done runs first, then by name; cached 30 s
    /api/lmstudio every model LM Studio knows, with its state (loaded or not) and type
                  (llm/vlm/embeddings) from the v0 API; falls back to the OpenAI-style
                  list labelled "available (state unknown)"; cached 30 s
    /api/journal  the last 50 journal entries, newest first, plus today's counters
    /api/tools    every tool with its risk tier, straight from iris.tools.TOOLS
    /api/sessions rows of Cline's ~/.cline/data/db/sessions.db: running sessions (ended_at NULL and a
                  live pid) first, then history; subagents grouped under their parent
    /api/claude   Claude Code project folders whose newest *.jsonl is under 10 minutes
                  old, folder name and age only; cached 30 s
    /api/iris/model which model Iris talks to right now and where that choice came from
                    (the IRIS_MODEL environment variable, then ~/.iris/settings.json)
    /api/eval     the newest eval report from eval/results (or IRIS_EVAL_DIR): its stamp,
                  model and summary, plus how many runs exist so far
    /api/queue    the prompts waiting in the agent queue, oldest first (read only)

POST (the only writes): /api/models/load {model}, /api/models/unload {model | "all"}
and /api/iris/model {model}, which writes the model key of ~/.iris/settings.json.
Every POST must carry the X-Iris-Token header (a random token generated at server start
and injected into the served page) and an Origin or Referer of this very dashboard;
anything else is a 403. The model id must be one LM Studio reports right now, else 400.
lms.exe runs as an argument list with no shell and a 600 s timeout; the executable
comes from IRIS_LMS_BIN or PATH. Only one action
runs at a time (a second request gets 409); every action is logged to the journal.

GET stays read-only everywhere: it never runs a tool or confirms an action, it opens
files for reading only, it binds to 127.0.0.1 so no other machine can reach it, and it
sends no CORS headers so no website can read it either. Journal text reaches the page
as JSON and is rendered with textContent, never as HTML."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

HOST = "127.0.0.1"  # loopback only; never 0.0.0.0
PORT = 8765
CACHE_SECONDS = 30      # agents and LM Studio can be slow; do not re-query on every poll
HISTORY_SECONDS = 300   # the sparklines cover five minutes
JOURNAL_LIMIT = 50

_LM_BASE = os.environ.get("IRIS_LLM_URL", "http://127.0.0.1:1234/v1")
if _LM_BASE.endswith("/v1"):
    _LM_ROOT = _LM_BASE[: -len("/v1")]
else:
    _LM_ROOT = _LM_BASE.rstrip("/")
V0_MODELS_URL = _LM_ROOT + "/api/v0/models"  # per-model state and type (the real list)
V1_MODELS_URL = _LM_ROOT + "/v1/models"      # fallback: every id, no state information

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}   # key -> (fetched_at, payload)
_history: deque[dict] = deque(maxlen=240)    # system samples for the sparklines


def journal_path() -> Path:
    return Path(os.environ.get("IRIS_JOURNAL") or (Path.home() / ".iris" / "journal.jsonl"))


# --- payloads ------------------------------------------------------------------

def system_payload() -> dict:
    """The live numbers plus the five-minute history the page draws as sparklines."""
    from iris.tools import system_data

    data = system_data()
    now = time.time()
    sample = {"t": round(now, 1), "cpu_percent": data["cpu_percent"],
              "ram_used_gb": data["ram_used_gb"], "ram_total_gb": data["ram_total_gb"],
              "gpus": [{"load_percent": g["load_percent"], "vram_used_mib": g["vram_used_mib"],
                        "vram_total_mib": g["vram_total_mib"], "temp_c": g["temp_c"]} for g in data["gpus"]]}
    with _lock:
        _history.append(sample)
        history = [h for h in _history if now - h["t"] <= HISTORY_SECONDS]
    return {**data, "history": history}


def agents_payload() -> dict:
    """One structured row per watched repo; the stuck ones come first."""
    from iris.agents import discover, load_config, repo_status

    cfg = load_config()
    rows = []
    for name, repo in discover(cfg):
        s = repo_status(repo, cfg.stuck_minutes)
        s.name = name  # the discovered label (with the worktree hash) beats the bare folder name
        rows.append({"name": s.name, "path": s.path, "branch": s.branch,
                     "last_commit": s.last_commit, "commit_age_seconds": s.commit_age,
                     "uncommitted": s.uncommitted, "blocked": s.blocked,
                     "stuck": s.stuck, "error": s.error})
    rows.sort(key=lambda r: 0 if r["stuck"] else 1)  # stuck first, stable otherwise
    return {"rows": rows}


def runs_payload() -> dict:
    """One progress row per watched repo that has a PROGRESS-*.md file.

    Each row is the run_progress dict for that repo plus its discovered name. Repos without a
    PROGRESS file are left out; not-done runs come first, then by name."""
    from iris.agents import discover, load_config
    from iris.autopilot import run_progress

    cfg = load_config()
    rows = []
    for name, repo in discover(cfg):
        progress = run_progress(repo)
        if progress["file"] is None:
            continue  # no PROGRESS file: not a run to show
        rows.append({"name": name, **progress})
    rows.sort(key=lambda r: (r["done"], r["name"]))  # not-done first, then by name
    return {"ok": True, "runs": rows}


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def lmstudio_payload() -> dict:
    """Every model LM Studio knows. The v0 API says which one is loaded and what kind it
    is; the OpenAI-style /v1 list only gives ids, so a fallback answer carries no state."""
    try:
        data = _fetch_json(V0_MODELS_URL)
        models = [{"id": str(m.get("id", "")), "state": m.get("state"), "type": m.get("type")}
                  for m in data.get("data", [])]
        return {"reachable": True, "states_known": True, "models": models}
    except Exception:  # noqa: BLE001 - no v0 endpoint (older LM Studio): try the plain list
        pass
    try:
        data = _fetch_json(V1_MODELS_URL)
        models = [{"id": str(m.get("id", "")), "state": None, "type": None}
                  for m in data.get("data", [])]
        return {"reachable": True, "states_known": False, "models": models}
    except Exception:  # noqa: BLE001 - unreachable or bad JSON both mean "not reachable"
        return {"reachable": False}


# --- model control (the only write the dashboard performs) ----------------------

def _lms_exe() -> str:
    """The lms executable, read at call time: IRIS_LMS_BIN when set, else lms on PATH.
    Empty means none was found."""
    exe = os.environ.get("IRIS_LMS_BIN", "").strip()
    if not exe:
        exe = shutil.which("lms") or ""
    return exe


LMS_TIMEOUT_SECONDS = 600   # big models can take minutes to load
WRITE_ROUTES = (
    "/api/models/load", "/api/models/unload", "/api/iris/model",
    "/api/queue/add", "/api/queue/delete", "/api/queue/move",
    "/api/cline/start",
)   # the only POST routes: model control, the prompt queue and starting a Cline session

_model_action_lock = threading.Lock()   # one load or unload at a time, full stop
_cline_start_lock = threading.Lock()    # one Cline start at a time, full stop


class ModelActionError(Exception):
    """A model action that cannot run; carries the HTTP code and a plain message."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def run_lms(args: list[str]) -> tuple[int, str]:
    """Run lms with an argument list. No shell, no user text in a command line."""
    exe = _lms_exe()
    if not exe:
        raise ModelActionError(503, "no lms executable found: set IRIS_LMS_BIN or put lms on PATH")
    proc = subprocess.run([exe] + args, capture_output=True, text=True,
                          timeout=LMS_TIMEOUT_SECONDS)
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()


def _known_model_ids() -> list[str]:
    """The ids LM Studio reports right now; the only ids a model action may name."""
    try:
        data = _fetch_json(V0_MODELS_URL)
        return [str(m.get("id", "")) for m in data.get("data", [])]
    except Exception:  # noqa: BLE001 - if we cannot ask, no id is acceptable
        return []


def _log_model_event(action: str, model: str, outcome: str) -> None:
    from iris.agent import Journal

    Journal(journal_path()).log(event="model", action=action, model=model, outcome=outcome)


def _fresh_lmstudio_state() -> dict:
    """The v0 state after an action; the 30 s cache is dropped so it is really fresh."""
    with _lock:
        _cache.pop("lmstudio", None)
    return lmstudio_payload()


def model_action(action: str, body: dict) -> dict:
    """Run one load or unload and return the new LM Studio state.

    action is "load" or "unload". The model must be an id from /api/v0/models at this
    moment (or "all" for an unload). Raises ModelActionError with the HTTP code."""
    if not isinstance(body, dict):
        raise ModelActionError(400, "the body must be a JSON object")
    model = body.get("model")
    if action == "unload" and model == "all":
        args, target = ["unload", "--all"], "all"
    else:
        if not isinstance(model, str) or not model:
            raise ModelActionError(400, "missing model id in the request body")
        if model not in _known_model_ids():
            raise ModelActionError(400, f"unknown model: {model}")
        args = [action, model]
        target = model

    if not _model_action_lock.acquire(blocking=False):
        raise ModelActionError(409, "another load or unload is already running")
    try:
        try:
            code, output = run_lms(args)
        except subprocess.TimeoutExpired:
            _log_model_event(action, target, "error")
            raise ModelActionError(502, f"lms timed out after {LMS_TIMEOUT_SECONDS} s") from None
        if code != 0:
            _log_model_event(action, target, "error")
            raise ModelActionError(502, output or f"lms exited with code {code}")
        _log_model_event(action, target, "ok")
    finally:
        _model_action_lock.release()
    return {"action": action, "model": target, **_fresh_lmstudio_state()}


def iris_model_payload() -> dict:
    """Which model Iris talks to right now, and where that choice came from."""
    from iris.llm import resolve_model

    model, source = resolve_model()
    return {"model": model, "source": source}


def set_iris_model(body: dict) -> dict:
    """Point Iris at a model by writing the "model" key of settings.json.

    Same rules as a load or unload: the body must name an id LM Studio reports right
    now (or 400). The IRIS_MODEL environment variable still wins over this file."""
    if not isinstance(body, dict):
        raise ModelActionError(400, "the body must be a JSON object")
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise ModelActionError(400, "missing model id in the request body")
    if model not in _known_model_ids():
        raise ModelActionError(400, f"unknown model: {model}")
    from iris.llm import read_settings, settings_path

    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = read_settings()   # keep any other keys the user has in the file
    data["model"] = model
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    _log_model_event("select", model, "ok")
    return iris_model_payload()


def _authorized(self) -> bool:
    """A POST is allowed only with the right token AND a matching Origin or Referer."""
    token = self.headers.get("X-Iris-Token")
    if not token or not secrets.compare_digest(token, self.server.token):
        return False
    port = str(self.server.server_address[1])
    for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
        if self.headers.get("Origin") == origin:
            return True
        referer = self.headers.get("Referer") or ""
        if referer.startswith(origin + "/") or referer == origin:
            return True
    return False


_PAGE_TEMPLATE: bytes | None = None


def _page_template() -> bytes:
    global _PAGE_TEMPLATE
    if _PAGE_TEMPLATE is None:
        _PAGE_TEMPLATE = (Path(__file__).parent / "index.html").read_bytes()
    return _PAGE_TEMPLATE


def journal_payload() -> dict:
    """The last 50 entries, newest first, plus today's counters. Read only."""
    path = journal_path()
    entries = []
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a torn final line must never break the dashboard
    today = time.strftime("%Y-%m-%d")
    counts = {"requests": 0, "confirmations": 0, "refusals": 0, "errors": 0}
    for e in entries:
        if not str(e.get("t", "")).startswith(today):
            continue
        if e.get("event") == "request":
            counts["requests"] += 1
        elif e.get("event") == "tool":
            outcome = e.get("outcome")
            if outcome == "ok":
                counts["confirmations"] += 1
            elif outcome == "refused":
                counts["refusals"] += 1
            elif outcome == "error":
                counts["errors"] += 1
    return {"entries": entries[-JOURNAL_LIMIT:][::-1], "today": counts}


def tools_payload() -> dict:
    from iris.tools import TOOLS

    return {"tools": [{"name": t.name, "description": t.description, "tier": t.tier}
                      for t in TOOLS.values()]}


# --- sessions.db -----------------------------------------------------------------
ACTIVE_WINDOW_S = 15 * 60   # an open session idle longer than this is not counted as running

SESSIONS_DB = Path(os.environ.get("IRIS_SESSIONS_DB") or (Path.home() / ".cline" / "data" / "db" / "sessions.db"))
STALE_STATUS = "ended (no exit recorded)"   # ended_at is NULL but the pid is gone


def _parse_ts(value) -> float | None:
    """An ISO-8601 timestamp to epoch seconds; None when missing or unparseable."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def load_sessions(db_path: Path | str | None = None) -> list[dict]:
    """The raw rows of sessions.db, opened read-only. A missing or unreadable file means no data."""
    path = Path(db_path) if db_path else SESSIONS_DB
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
    except (sqlite3.Error, OSError):
        return []
    try:
        cur = con.execute(
            "SELECT session_id, source, pid, started_at, ended_at, exit_code, status,"
            " interactive, provider, model, cwd, is_subagent, parent_session_id, updated_at"
            " FROM sessions")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    except (sqlite3.Error, OSError):
        return []   # no db yet or a different schema: the tab shows empty, not an error page
    finally:
        con.close()


def session_rows(db_path: Path | str | None = None, now: float | None = None) -> list[dict]:
    """Sessions shaped for display.

    A session is running only when ended_at IS NULL and its pid still exists (psutil).
    A row with ended_at NULL but a dead pid is stale: it shows as "ended (no exit
    recorded)" in the history, never as running. Subagents are grouped under their
    parent_session_id instead of listed separately; an orphan subagent stays on its own."""
    now = time.time() if now is None else now
    rows = load_sessions(db_path)
    by_id = {r.get("session_id"): r for r in rows}
    shaped: list[dict] = []
    for r in rows:
        ended_ts = _parse_ts(r.get("ended_at"))
        updated_ts = _parse_ts(r.get("updated_at")) or now
        try:
            pid_alive = bool(int(r["pid"])) and psutil.pid_exists(int(r["pid"]))
        except (TypeError, ValueError):
            pid_alive = False
        # Cline's desktop sessions all share the app's pid, so a live pid only means "the app is
        # open". A session is running when it is open AND was updated in the last 15 minutes.
        open_ = r.get("ended_at") is None and pid_alive
        live = open_ and now - updated_ts < ACTIVE_WINDOW_S
        if live:
            status = "running"
        elif open_:
            status = str(r.get("status") or "idle")
        elif ended_ts is None:
            status = STALE_STATUS
        else:
            status = str(r.get("status") or "-")   # idle, completed, anything else: as-is
        cwd = r.get("cwd") or ""
        shaped.append({"session_id": r.get("session_id") or "", "source": r.get("source") or "",
                       "model": r.get("model") or "", "provider": r.get("provider") or "",
                       "folder": Path(cwd).name if cwd else "-", "status": status,
                       "live": live, "age_seconds": max(0.0, now - updated_ts),
                       "_updated": updated_ts})
    top: list[dict] = []
    for s in shaped:
        raw = by_id.get(s["session_id"])
        if raw and int(raw.get("is_subagent") or 0) == 1 and raw.get("parent_session_id"):
            parent = next((t for t in top if t["session_id"] == raw["parent_session_id"]), None)
            if parent is not None:
                s.pop("_updated", None)
                parent.setdefault("children", []).append(s)
                continue
        s.pop("_updated", None)
        top.append(s)
    for t in top:   # newest first within each group, running sessions before history
        kids = t.get("children")
        if kids:
            kids.sort(key=lambda c: c["age_seconds"])
    top.sort(key=lambda t: (0 if t["live"] else 1, t["age_seconds"]))
    return top


# --- Claude Code projects ----------------------------------------------------------

CLAUDE_PROJECTS = Path(os.environ.get("IRIS_CLAUDE_DIR") or (Path.home() / ".claude" / "projects"))
CLAUDE_FRESH_SECONDS = 10 * 60   # a project counts as active while its newest .jsonl is under 10 minutes old


def claude_projects(projects_dir: Path | str | None = None, now: float | None = None) -> list[dict]:
    """Claude Code project folders whose newest *.jsonl is under 10 minutes old.

    Folder name and age only; the .jsonl files are stat'ed for their mtime, never opened
    or read."""
    base = Path(projects_dir) if projects_dir else CLAUDE_PROJECTS
    now = time.time() if now is None else now
    out: list[dict] = []
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return []   # no ~/.claude/projects yet: nothing to show, not an error
    for d in entries:
        if not d.is_dir():
            continue
        try:
            files = [f for f in d.glob("*.jsonl") if f.is_file()]
        except OSError:
            continue
        if not files:
            continue
        mtime = max(f.stat().st_mtime for f in files)   # the newest file decides
        age = now - mtime
        if age < CLAUDE_FRESH_SECONDS:
            out.append({"folder": d.name, "age_seconds": max(0.0, age)})
    return sorted(out, key=lambda r: r["age_seconds"])


def _cached(key: str, fn) -> dict:
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (now, value)
    return value


# --- eval reports ---------------------------------------------------------------

def eval_payload(results_dir=None) -> dict:
    """The newest eval report from results_dir / IRIS_EVAL_DIR, plus how many runs exist.

    The newest file is decided by name alone: the stamps sort by time."""
    if results_dir is None:
        results_dir = os.environ.get("IRIS_EVAL_DIR") or str(Path(__file__).resolve().parents[2] / "eval" / "results")
    base = Path(results_dir)
    try:
        files = sorted(f for f in base.glob("*.json") if f.is_file())
    except OSError:
        return {"latest": None, "runs": 0}
    if not files:
        return {"latest": None, "runs": 0}
    newest = files[-1]
    try:
        report = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"latest": None, "runs": len(files), "error": "unreadable report"}
    if not isinstance(report, dict):
        return {"latest": None, "runs": len(files), "error": "unreadable report"}
    latest = {"stamp": report.get("stamp"), "model": report.get("model"),
              "summary": report.get("summary")}
    summary = latest["summary"]
    if isinstance(summary, dict):   # new reports carry these in the summary; old ones still work
        for key in ("by_category", "safety"):
            if key in summary:
                latest[key] = summary[key]
    return {"latest": latest, "runs": len(files)}


# --- prompt queue ---------------------------------------------------------------


def queue_payload() -> dict:
    """The prompts waiting in the agent queue, oldest first (read only)."""
    from iris import prompt_queue

    try:
        return {"ok": True, "prompts": prompt_queue.pending()}
    except ValueError:  # a broken queue file must not take the dashboard down
        return {"ok": False, "prompts": [], "error": "the queue file is broken"}


def _log_queue_event(action: str, agent: str | None = None, prompt_id: int | None = None) -> None:
    """One journal line per queue change; the same writer the model routes use."""
    from iris.agent import Journal

    Journal(journal_path()).log(event="queue", action=action, agent=agent, id=prompt_id)


def queue_action(action: str, body: dict) -> dict:
    """Run one add, delete or move on the prompt queue and return the new queue state.

    action is "add" ({agent, text}), "delete" ({id}) or "move" ({id, step}). Raises
    ModelActionError with the HTTP code; a broken queue file comes back as a 500."""
    from iris import prompt_queue

    if not isinstance(body, dict):
        raise ModelActionError(400, "the body must be a JSON object")
    try:
        if action == "add":
            agent = str(body.get("agent", "")).strip()
            text = str(body.get("text", "")).strip()
            if not 1 <= len(agent) <= 40:
                raise ModelActionError(400, "agent must be 1 to 40 characters")
            if not 1 <= len(text) <= 8000:
                raise ModelActionError(400, "text must be 1 to 8000 characters")
            prompt_id = prompt_queue.add(agent, text)
            _log_queue_event("add", agent=agent, prompt_id=prompt_id)
        elif action == "delete":
            raw_id = body.get("id")
            if not isinstance(raw_id, int):
                raise ModelActionError(400, "id must be an integer")
            try:
                removed = prompt_queue.remove(raw_id)
            except LookupError:
                raise ModelActionError(404, f"no prompt with id {raw_id}") from None
            _log_queue_event("delete", agent=str(removed.get("agent", "")), prompt_id=raw_id)
        elif action == "move":
            raw_id = body.get("id")
            step = body.get("step")
            if not isinstance(raw_id, int):
                raise ModelActionError(400, "id must be an integer")
            if step not in (-1, 1):
                raise ModelActionError(400, "step must be -1 or 1")
            try:
                prompt_queue.move(raw_id, step)
            except LookupError:
                raise ModelActionError(404, f"no prompt with id {raw_id}") from None
            _log_queue_event("move", agent=None, prompt_id=raw_id)
        else:  # pragma: no cover - do_POST only routes the three known actions here
            raise ModelActionError(400, "unknown queue action")
    except ValueError as exc:  # a broken queue file must not take the dashboard down
        raise ModelActionError(500, str(exc)) from None
    return queue_payload()


# --- autopilot start -------------------------------------------------------------


def _log_autopilot_event(repo: str, agent: str, result: str) -> None:
    """One journal line per dashboard start; the same writer the model routes use."""
    from iris.agent import Journal

    Journal(journal_path()).log(
        event="autopilot", source="dashboard", repo=repo, agent=agent, answer=True, result=result
    )


def cline_start_action(body: dict) -> dict:
    """Start the agent's next queued prompt as a Cline session, one at a time.

    Body is {agent, repo}: agent required (1 to 40 chars), repo optional. Calls
    iris.autopilot.cline_start(agent, repo), looked up on the module so tests can
    monkeypatch it. A result starting with "error:" comes back as a 409; otherwise
    200 with the new queue state. Either way an autopilot event is journaled."""
    from iris import autopilot

    if not isinstance(body, dict):
        raise ModelActionError(400, "the body must be a JSON object")
    agent = str(body.get("agent", "")).strip()
    if not 1 <= len(agent) <= 40:
        raise ModelActionError(400, "agent must be 1 to 40 characters")
    repo_raw = body.get("repo")
    repo = "" if repo_raw is None else str(repo_raw).strip()

    if not _cline_start_lock.acquire(blocking=False):
        raise ModelActionError(409, "a start is already running")
    try:
        result = autopilot.cline_start(agent, repo)
    finally:
        _cline_start_lock.release()

    _log_autopilot_event(repo, agent, result)
    if result.startswith("error:"):
        raise ModelActionError(409, result.removeprefix("error: "))
    return {"ok": True, "result": result, "queue": queue_payload()["prompts"]}


# --- the server -----------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "IrisDashboard/2.0"
    timeout = 30   # a stuck client must never pin a handler thread forever

    def _drain_body(self, cap: int = 65536) -> None:
        """Read and discard the request body so closing the socket sends FIN, not RST.

        On Windows, closing a socket with unread data resets the connection, which can
        destroy an error response before the client has read it."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        try:
            while length > 0:
                chunk = self.rfile.read(min(length, cap))
                if not chunk:
                    break
                length -= len(chunk)
        except Exception:  # noqa: BLE001 - a lying Content-Length must never kill the handler
            pass

    def do_GET(self):  # GET stays read-only; the write routes refuse it with a 405
        path = self.path.split("?", 1)[0]
        if path == "/":
            token = getattr(self.server, "token", "")
            page = _page_template().replace(b"__IRIS_TOKEN__", token.encode())
            self._send(200, page, "text/html; charset=utf-8")
        elif path == "/api/iris/model":
            self._json(iris_model_payload())   # readable via GET; writes go through the POST below
        elif path in WRITE_ROUTES:
            self._json({"error": "write routes only accept POST"}, code=405)
        elif path == "/api/system":
            self._json(system_payload())
        elif path == "/api/agents":
            self._json(_cached("agents", agents_payload))
        elif path == "/api/runs":
            self._json(_cached("runs", runs_payload))
        elif path == "/api/lmstudio":
            self._json(_cached("lmstudio", lmstudio_payload))
        elif path == "/api/sessions":
            self._json({"ok": True, "rows": session_rows()})   # local sqlite: fresh on every poll
        elif path == "/api/claude":
            self._json({"ok": True, "rows": _cached("claude", lambda: claude_projects())})
        elif path == "/api/journal":
            self._json(journal_payload())
        elif path == "/api/tools":
            self._json(tools_payload())
        elif path == "/api/eval":
            self._json(eval_payload())
        elif path == "/api/queue":
            self._json(queue_payload())
        else:
            self._json({"error": "not found"}, code=404)

    def do_POST(self):  # the write routes and nothing else
        path = self.path.split("?", 1)[0]
        if path not in WRITE_ROUTES:
            self._drain_body()
            self._json({"error": "read only: this route does not accept POST"}, code=405)
            return
        if not _authorized(self):
            self._drain_body()
            self._json({"error": "forbidden: wrong token or origin"}, code=403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError):
            self._json({"error": "bad JSON body"}, code=400)
            return
        try:
            if path == "/api/iris/model":
                payload = set_iris_model(body)
            elif path in ("/api/queue/add", "/api/queue/delete", "/api/queue/move"):
                action = path.rsplit("/", 1)[1]   # "add" | "delete" | "move"
                payload = queue_action(action, body)
            elif path == "/api/cline/start":
                payload = cline_start_action(body)
            else:
                action = "load" if path.endswith("/load") else "unload"
                payload = model_action(action, body)
        except ModelActionError as exc:
            self._json({"error": exc.message}, code=exc.code)
            return
        self._json(payload)

    def _method_not_allowed(self):  # PUT, DELETE, PATCH, OPTIONS and HEAD alike
        self._drain_body()
        self._json({"error": "read only: use GET, or POST on a write route"}, code=405)

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _method_not_allowed

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload), "application/json")

    def _send(self, code: int, body: str | bytes, ctype: str) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # no CORS headers on purpose: other websites must not be able to read this
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # keep the console quiet while it runs
        pass


class _Server(ThreadingHTTPServer):
    token: str = ""  # set by make_server; injected into the page and required on every POST


def make_server(host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    """A dashboard server on `host`:`port`. Port 0 lets the OS pick a free one (tests)."""
    server = _Server((host, port), Handler)
    server.token = secrets.token_urlsafe(32)   # injected into the page; required on every POST
    return server


def default_port() -> int:
    try:
        return int(os.environ.get("IRIS_DASHBOARD_PORT") or PORT)
    except ValueError:
        return PORT


def serve(host: str = HOST, port: int | None = None) -> None:
    if port is None:
        port = default_port()
    try:
        server = make_server(host, port)
    except OSError as exc:  # something else already owns the port (it happens on this machine)
        raise SystemExit(f"cannot bind {host}:{port} ({exc}). Another program is using it; "
                         f"try: python -m iris dashboard --port 8766") from None
    print(f"Iris dashboard: http://{host}:{server.server_address[1]}  (model control needs the page token)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
