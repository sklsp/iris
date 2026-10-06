"""The Iris dashboard: python -m iris dashboard serves http://127.0.0.1:8765.

If that port is taken, pass another one: python -m iris dashboard --port 8766 (or set
IRIS_DASHBOARD_PORT). The page plus five JSON routes are polled every 3 s by the browser:

    /api/system   CPU %, RAM used/total, per-GPU load, VRAM and temperature, with a
                  five-minute history for the sparklines
    /api/agents   one row per watched repo (branch, last commit and age, uncommitted
                  count, BLOCKED.md, STUCK), stuck rows first; cached 30 s
    /api/lmstudio the loaded model(s) from LM Studio, or reachable: false; cached 30 s
    /api/journal  the last 50 journal entries, newest first, plus today's counters
    /api/tools    every tool with its risk tier, straight from iris.tools.TOOLS

Read only by construction. The handler implements GET and nothing else (every other
method is a 405), it never runs a tool or confirms an action, it opens files for
reading only, it binds to 127.0.0.1 so no other machine can reach it, and it sends no
CORS headers so no website can read it either. Journal text reaches the page as JSON
and is rendered with textContent, never as HTML."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"  # loopback only; never 0.0.0.0
PORT = 8765
CACHE_SECONDS = 30      # agents and LM Studio can be slow; do not re-query on every poll
HISTORY_SECONDS = 300   # the sparklines cover five minutes
JOURNAL_LIMIT = 50

LM_STUDIO_URL = os.environ.get("IRIS_LLM_URL", "http://127.0.0.1:1234/v1") + "/models"

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


def lmstudio_payload() -> dict:
    try:
        with urllib.request.urlopen(LM_STUDIO_URL, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"reachable": True, "models": [str(m.get("id", "")) for m in data.get("data", [])]}
    except Exception:  # noqa: BLE001 - unreachable or bad JSON both mean "not reachable"
        return {"reachable": False}


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


# --- the server -----------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "IrisDashboard/1.0"

    def do_GET(self):  # the only method that does anything at all
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, _page_bytes(), "text/html; charset=utf-8")
        elif path == "/api/system":
            self._json(system_payload())
        elif path == "/api/agents":
            self._json(_cached("agents", agents_payload))
        elif path == "/api/lmstudio":
            self._json(_cached("lmstudio", lmstudio_payload))
        elif path == "/api/journal":
            self._json(journal_payload())
        elif path == "/api/tools":
            self._json(tools_payload())
        else:
            self._json({"error": "not found"}, code=404)

    def _method_not_allowed(self):  # POST, PUT, DELETE, PATCH, OPTIONS and HEAD alike
        self._json({"error": "read only: GET is the only method"}, code=405)

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _method_not_allowed

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


def _page_bytes() -> bytes:
    return (Path(__file__).parent / "index.html").read_bytes()


def make_server(host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    """A dashboard server on `host`:`port`. Port 0 lets the OS pick a free one (tests)."""
    return ThreadingHTTPServer((host, port), Handler)


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
    print(f"Iris dashboard: http://{host}:{server.server_address[1]}  (read only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
