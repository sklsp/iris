"""Safety and route tests for the read-only dashboard (no real repos, LLM or journal touched)."""

import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import iris.dashboard as d
from iris.dashboard import HOST, agents_payload, journal_payload, lmstudio_payload, make_server, system_payload
from iris.llm import DEFAULT_MODEL


@pytest.fixture(scope="session")
def srv():
    s = make_server(HOST, 0)  # port 0: the OS picks a free one; one server for the whole file
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


@pytest.fixture
def server(srv):
    return f"http://{HOST}:{srv.server_address[1]}"


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, dict(r.headers), r.read().decode("utf-8")  # callers parse JSON when they need it


def post(url, body=None, token=None, origin=None, referer=None):
    data = json.dumps(body if body is not None else {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    if token is not None:
        req.add_header("X-Iris-Token", token)
    if origin is not None:
        req.add_header("Origin", origin)
    if referer is not None:
        req.add_header("Referer", referer)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8") or "{}"
        return e.code, json.loads(raw)


def fake_v0(monkeypatch, models):
    """Point every LM Studio fetch at a canned v0 list. Tests never touch the real one."""
    body = {"data": [{"id": m[0], "state": m[1], "type": m[2]} for m in models]}
    monkeypatch.setattr(d, "_fetch_json", lambda url: body)


# --- safety --------------------------------------------------------------------

@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@pytest.mark.parametrize(
    "path", ["/", "/api/system", "/api/agents", "/api/lmstudio", "/api/journal", "/api/tools", "/api/eval"]
)
def test_non_get_methods_are_refused_with_405(server, method, path):
    req = urllib.request.Request(server + path, data=b"{}", method=method)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 405


def test_server_binds_loopback_only():
    srv = make_server(HOST, 0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


def test_defaults_are_loopback_and_the_documented_port():
    assert d.HOST == "127.0.0.1"  # never 0.0.0.0
    assert d.PORT == 8765


def test_default_port_env_override(monkeypatch):
    monkeypatch.setenv("IRIS_DASHBOARD_PORT", "9123")
    assert d.default_port() == 9123
    monkeypatch.delenv("IRIS_DASHBOARD_PORT")
    assert d.default_port() == 8765


def test_no_cors_headers_on_any_response(server):
    for path in ("/", "/api/tools"):
        _, headers, _ = get(server + path)
        cors = [k for k in headers if k.lower().startswith("access-control")]
        assert cors == [], f"CORS header sent on {path}: {cors}"


# --- routes ----------------------------------------------------------------------

def test_page_is_served(server):
    status, headers, _ = get(server + "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")


def test_unknown_path_is_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(server + "/api/nope", timeout=5)
    assert exc.value.code == 404


def test_api_tools_lists_every_tool_with_its_tier():
    from iris.tools import TOOLS

    data = d.tools_payload()
    assert {t["name"] for t in data["tools"]} == set(TOOLS)
    by_name = {t["name"]: t for t in data["tools"]}
    for name, tool in TOOLS.items():
        assert by_name[name]["tier"] == tool.tier
        assert by_name[name]["description"] == tool.description


def test_api_system_shape_and_history_grows(monkeypatch):
    fake = {"cpu_percent": 12.0, "ram_used_gb": 8.5, "ram_total_gb": 32.0,
            "gpus": [{"index": 0, "name": "RTX", "load_percent": 40.0,
                      "vram_used_mib": 1024.0, "vram_total_mib": 8192.0, "temp_c": 55.0}]}
    monkeypatch.setattr("iris.tools.system_data", lambda: fake)

    first = system_payload()
    second = system_payload()
    for key in ("cpu_percent", "ram_used_gb", "ram_total_gb", "gpus"):
        assert first[key] == fake[key]
    assert len(second["history"]) == len(first["history"]) + 1
    last = second["history"][-1]
    assert last["cpu_percent"] == 12.0 and last["gpus"][0]["temp_c"] == 55.0


def test_api_journal_newest_first_limited_and_counted_today(tmp_path, monkeypatch):
    journal = tmp_path / "journal.jsonl"
    now = datetime.now()

    def t(days_ago: float) -> str:
        return (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")

    lines = [json.dumps({"event": "request", "text": f"r{i}", "t": t(0)}) for i in range(60)]
    lines.append(json.dumps({"event": "tool", "tool": "click", "args": {}, "tier": "confirm",
                             "outcome": "ok", "result": "clicked", "t": t(0)}))
    lines.append(json.dumps({"event": "tool", "tool": "press_keys", "args": {}, "tier": "never",
                             "outcome": "refused", "t": t(0)}))
    lines.append(json.dumps({"event": "tool", "tool": "read_window", "args": {}, "tier": "safe",
                             "outcome": "error", "error": "no window", "t": t(0)}))
    lines.append(json.dumps({"event": "tool", "tool": "click", "args": {}, "tier": "confirm",
                             "outcome": "declined", "t": t(0)}))
    lines.append(json.dumps({"event": "request", "text": "yesterday", "t": t(1)}))  # not today
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))

    data = journal_payload()
    assert len(data["entries"]) == 50  # the limit, newest first
    reqs = [
        e.get("text") for e in data["entries"]
        if e.get("event") == "request" and str(e.get("text", "")).startswith("r")
    ]
    assert reqs == [f"r{i}" for i in range(59, 14, -1)]
    assert data["today"] == {"requests": 60, "confirmations": 1, "refusals": 1, "errors": 1}


def test_api_journal_counts_only_confirmed_calls_as_confirmations(tmp_path, monkeypatch):
    """A safe ok call is not a confirmation; only a confirm-tier ok call counts."""
    journal = tmp_path / "journal.jsonl"
    now = datetime.now()

    def t(days_ago: float) -> str:
        return (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")

    lines = [json.dumps({"event": "tool", "tool": "system_status", "args": {}, "tier": "safe",
                         "outcome": "ok", "result": "cpu 12%", "t": t(0)})]
    lines.append(json.dumps({"event": "tool", "tool": "click", "args": {}, "tier": "confirm",
                             "outcome": "ok", "result": "clicked", "t": t(0)}))
    lines.append(json.dumps({"event": "tool", "tool": "click", "args": {}, "tier": "confirm",
                             "outcome": "declined", "t": t(0)}))
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))

    data = journal_payload()
    assert data["today"] == {"requests": 0, "confirmations": 1, "refusals": 0, "errors": 0}


def test_api_journal_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_JOURNAL", str(tmp_path / "nope.jsonl"))
    data = journal_payload()
    assert data["entries"] == [] and data["today"]["requests"] == 0


def test_api_journal_counts_a_returned_error(tmp_path, monkeypatch):
    from iris.agent import Agent, Journal
    from iris.llm import Reply, ToolCall
    from iris.tools import SAFE, Tool

    journal = tmp_path / "journal.jsonl"
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))
    boom = Tool("boom", "", {}, [], SAFE, lambda **kw: "error: x")
    replies = [
        Reply("", [ToolCall("id-boom", "boom", {})], {"role": "assistant", "content": "", "tool_calls": [
            {"id": "id-boom", "type": "function", "function": {"name": "boom", "arguments": "{}"}}]}),
        Reply("Klaar.", [], {"role": "assistant", "content": "Klaar."}),
    ]

    class LLM:
        def chat(self, messages, tools):
            return replies.pop(0)

    Agent(LLM(), lambda q: True, Journal(journal), {"boom": boom}).handle("do it")
    data = journal_payload()
    assert data["today"] == {"requests": 1, "confirmations": 0, "refusals": 0, "errors": 1}


def test_api_journal_passes_autopilot_events_through_and_keeps_the_counters(tmp_path, monkeypatch):
    """An autopilot event reaches the Activity panel like any other entry and leaves today's counters alone."""
    journal = tmp_path / "journal.jsonl"
    now = datetime.now()

    def t(days_ago: float) -> str:
        return (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")

    auto_t = t(0)
    lines = [json.dumps({"event": "request", "text": "r0", "t": t(0)})]
    lines.append(json.dumps({"event": "tool", "tool": "click", "args": {}, "tier": "confirm",
                             "outcome": "ok", "result": "clicked", "t": t(0)}))
    lines.append(json.dumps({"event": "autopilot", "repo": "Iris", "agent": "Cline",
                             "result": "536 passed in 20.44s", "t": auto_t}))
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))

    data = journal_payload()
    autos = [e for e in data["entries"] if e.get("event") == "autopilot"]
    assert autos == [{"event": "autopilot", "repo": "Iris", "agent": "Cline",
                      "result": "536 passed in 20.44s", "t": auto_t}]
    assert data["today"] == {"requests": 1, "confirmations": 1, "refusals": 0, "errors": 0}


def test_api_journal_shows_model_errors_like_errored_calls(tmp_path, monkeypatch):
    """An llm_error line reaches the Activity panel as an errored call of 'model' and leaves the tool counters alone."""
    journal = tmp_path / "journal.jsonl"
    now = datetime.now()

    def t(days_ago: float) -> str:
        return (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")

    err_t, bare_t = t(0), t(0)
    lines = [json.dumps({"event": "request", "text": "r0", "t": t(0)})]
    lines.append(json.dumps({"event": "llm_error", "error": "URLError('connection refused')", "t": err_t}))
    lines.append(json.dumps({"event": "llm_error", "t": bare_t}))  # no error key: must not break the endpoint
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))

    data = journal_payload()
    models = [e for e in data["entries"] if e.get("tool") == "model"]
    assert models == [   # newest first: the bare line was written last
        {"event": "tool", "tool": "model", "tier": "?", "outcome": "error", "error": "", "t": bare_t},
        {"event": "tool", "tool": "model", "tier": "?", "outcome": "error",
         "error": "URLError('connection refused')", "t": err_t},
    ]
    assert data["today"] == {"requests": 1, "confirmations": 0, "refusals": 0, "errors": 0}


def test_page_renders_autopilot_events_in_the_activity_panel(server):
    status, _headers, page = get(server + "/")
    assert status == 200
    assert 'e.event === "autopilot"' in page   # groupTurns keeps autopilot events instead of dropping them
    assert "b-auto" in page                    # the badge class that marks an autopilot row


def test_page_renders_model_error_text_in_the_activity_panel(server):
    status, _headers, page = get(server + "/")
    assert status == 200
    assert 'truncate(String(ev.error), 160)' in page   # errored rows show their error text via textContent


def test_api_agents_stuck_rows_first(tmp_path, monkeypatch):
    import iris.agents as agents_mod
    from tests.test_agents import make_repo, write_config

    fresh = make_repo(tmp_path, "fresh")
    (fresh / "b.txt").write_text("x", encoding="utf-8")  # uncommitted but recent: not stuck
    old = make_repo(tmp_path, "Beacon", minutes_ago=120)
    (old / "b.txt").write_text("x", encoding="utf-8")  # uncommitted and an old commit: stuck
    monkeypatch.setattr(agents_mod, "CONFIG", write_config(tmp_path, repos=[str(fresh), str(old)], worktrees=[]))

    rows = agents_payload()["rows"]
    assert [r["name"] for r in rows] == ["Beacon", "fresh"]  # stuck first
    assert rows[0]["stuck"] is True and rows[1]["stuck"] is False
    assert rows[0]["uncommitted"] == 1 and rows[0]["branch"] == "main"


def _runs_repos(tmp_path):
    """alpha and zeta are not done, beta is done, gamma has no PROGRESS file at all."""
    from tests.test_autopilot import checklist, make_repo

    alpha = make_repo(tmp_path, "alpha", checklist(open_at=(4, 9)))   # two items open: not done
    zeta = make_repo(tmp_path, "zeta", checklist(open_at=(7,)))       # one item open: not done
    beta = make_repo(tmp_path, "beta", checklist())                   # all ticked, old commit: done
    gamma = make_repo(tmp_path, "gamma", None)                        # no PROGRESS file at all
    return alpha, zeta, beta, gamma


def _watch_only(monkeypatch, tmp_path, repos):
    """Point discover() at exactly these temp repos and nothing else (no real worktrees)."""
    import iris.agents as agents_mod
    from tests.test_agents import write_config

    monkeypatch.setattr(agents_mod, "CONFIG",
                        write_config(tmp_path, repos=[str(r) for r in repos], worktrees=[]))


def test_api_runs_lists_progress_repos_not_done_first_then_by_name(tmp_path, monkeypatch):
    alpha, zeta, beta, gamma = _runs_repos(tmp_path)
    _watch_only(monkeypatch, tmp_path, [alpha, zeta, beta, gamma])

    data = d.runs_payload()
    assert data["ok"] is True
    runs = data["runs"]
    # gamma (no PROGRESS file) is left out; not-done first, then by name within each group
    assert [r["name"] for r in runs] == ["alpha", "zeta", "beta"]
    assert runs[0] == {
        "name": "alpha", "repo": str(alpha), "file": "PROGRESS-iris6.md",
        "done_items": 13, "total": 15, "done": False,
        "reason": "2 of 15 items open in PROGRESS-iris6.md",
    }
    assert runs[1] == {
        "name": "zeta", "repo": str(zeta), "file": "PROGRESS-iris6.md",
        "done_items": 14, "total": 15, "done": False,
        "reason": "1 of 15 items open in PROGRESS-iris6.md",
    }
    assert runs[2] == {
        "name": "beta", "repo": str(beta), "file": "PROGRESS-iris6.md",
        "done_items": 15, "total": 15, "done": True,
        "reason": "PROGRESS-iris6.md done: 15 items",
    }


def test_api_runs_route_returns_200_and_the_same_json(server, tmp_path, monkeypatch):
    alpha, zeta, beta, gamma = _runs_repos(tmp_path)
    _watch_only(monkeypatch, tmp_path, [alpha, zeta, beta, gamma])

    with d._lock:  # drop any cached runs payload so the route recomputes against these repos
        d._cache.pop("runs", None)
    status, _headers, body = get(server + "/api/runs")
    assert status == 200
    assert json.loads(body) == d.runs_payload()


def test_api_lmstudio_unreachable_reports_false(monkeypatch):
    monkeypatch.setattr(d, "V0_MODELS_URL", "http://127.0.0.1:9/api/v0/models")  # port 9: nothing listens
    monkeypatch.setattr(d, "V1_MODELS_URL", "http://127.0.0.1:9/v1/models")
    assert lmstudio_payload() == {"reachable": False}


def test_a_closed_local_port_is_seen_at_once_and_the_plain_list_is_not_tried(monkeypatch):
    import socket

    listener = socket.socket()  # the real probe, on loopback only
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    open_port = listener.getsockname()[1]
    assert d._probe(f"http://127.0.0.1:{open_port}/api/v0/models") is False
    listener.close()
    assert d._probe(f"http://127.0.0.1:{open_port}/api/v0/models") is True
    assert d._probe("http://192.0.2.10:1234/v1") is False  # another machine: the real request decides

    asked = []

    def refused(url, timeout=None):
        asked.append(url)
        raise d.urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

    monkeypatch.setattr(d.urllib.request, "urlopen", refused)
    assert lmstudio_payload() == {"reachable": False} and len(asked) == 1  # down: no second wait for /v1/models


def test_api_lmstudio_lists_models_with_state_and_type(monkeypatch):
    class FakeResp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    v0 = b'{"data": [{"id": "qwen3.8-27b", "state": "loaded", "type": "vlm"}, ' \
         b'{"id": "iq3_xxs", "state": "not-loaded", "type": "llm"}]}'
    monkeypatch.setattr(d.urllib.request, "urlopen", lambda url, timeout=None: FakeResp(v0))
    data = lmstudio_payload()
    assert data["reachable"] is True and data["states_known"] is True
    assert data["models"] == [
        {"id": "qwen3.8-27b", "state": "loaded", "type": "vlm"},
        {"id": "iq3_xxs", "state": "not-loaded", "type": "llm"}]


def test_api_lmstudio_falls_back_to_v1_without_states(monkeypatch):
    class FakeResp:
        def read(self):
            return b'{"data": [{"id": "qwen3.8-27b"}, {"id": "llama-3.1-8b"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(url, timeout=None):
        if url.endswith("/api/v0/models"):
            raise OSError("no v0 endpoint")
        return FakeResp()

    monkeypatch.setattr(d.urllib.request, "urlopen", fake_urlopen)
    data = lmstudio_payload()
    assert data["reachable"] is True and data["states_known"] is False
    assert [m["id"] for m in data["models"]] == ["qwen3.8-27b", "llama-3.1-8b"]
    assert all(m["state"] is None for m in data["models"])  # the v1 list carries no state


def test_v0_url_points_at_the_api_v0_endpoint():
    assert d.V0_MODELS_URL.endswith("/api/v0/models")  # never the OpenAI-style list again
    assert not d.V0_MODELS_URL.endswith("/v1/models")


def test_agents_and_lmstudio_are_cached_for_30_seconds(monkeypatch):
    calls = []

    def slow():
        calls.append(1)
        return {"reachable": False}

    monkeypatch.setattr(d, "lmstudio_payload", slow)
    first = d._cached("lmstudio", d.lmstudio_payload)
    second = d._cached("lmstudio", d.lmstudio_payload)
    assert first is second and calls == [1]  # the second call hit the cache
    d._cache.clear()


def test_journal_script_text_renders_as_text(tmp_path, monkeypatch):
    """Journal text is untrusted screen content. The API must pass it through raw and the page
    may only insert it with textContent, so a <script> tag shows up as characters, not code."""
    journal = tmp_path / "journal.jsonl"
    evil = "<script>alert('xss')</script>"
    entry = {"event": "request", "text": evil, "t": time.strftime("%Y-%m-%dT%H:%M:%S")}
    journal.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))

    data = journal_payload()
    assert data["entries"][0]["text"] == evil  # raw text out of the API, unmodified

    html = (Path(d.__file__).parent / "index.html").read_text(encoding="utf-8")
    for bad in ("innerHTML", "insertAdjacentHTML", "document.write"):
        assert bad not in html, f"the page uses {bad}; journal text must go in via textContent only"
    assert "textContent" in html


# --- model control (the one write the dashboard performs) ------------------------

def test_page_carries_the_server_token(srv):
    base = f"http://{HOST}:{srv.server_address[1]}"
    _, _, page = get(base + "/")
    assert srv.token in page  # injected so the page can send it back on POSTs


@pytest.mark.parametrize("route", ["/api/models/load", "/api/models/unload"])
def test_get_on_a_write_route_is_405(srv, route):
    base = f"http://{HOST}:{srv.server_address[1]}"
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(base + route, timeout=5)
    assert exc.value.code == 405


def test_post_without_a_token_is_403(srv):
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, body = post(base + "/api/models/load", {"model": "m1"}, origin=base)
    assert code == 403 and "forbidden" in body["error"]


def test_post_with_a_wrong_token_is_403(srv):
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, _ = post(base + "/api/models/load", {"model": "m1"}, token="not-the-token", origin=base)
    assert code == 403


def test_post_from_a_foreign_origin_is_403(srv):
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, _ = post(base + "/api/models/load", {"model": "m1"}, token=srv.token, origin="http://evil.example")
    assert code == 403


def test_post_with_no_origin_and_no_referer_is_403(srv):
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, _ = post(base + "/api/models/load", {"model": "m1"}, token=srv.token)
    assert code == 403


def test_post_with_a_matching_referer_is_allowed(srv, monkeypatch, tmp_path):
    base = f"http://{HOST}:{srv.server_address[1]}"
    fake_v0(monkeypatch, [("m1", "not-loaded", "llm")])
    calls = []
    monkeypatch.setattr(d, "run_lms", lambda args: (calls.append(args) or (0, "")))
    monkeypatch.setenv("IRIS_JOURNAL", str(tmp_path / "j.jsonl"))
    code, body = post(base + "/api/models/load", {"model": "m1"}, token=srv.token, referer=base + "/")
    assert code == 200 and calls == [["load", "m1"]]


def test_post_unknown_model_is_400_and_lms_never_runs(srv, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    fake_v0(monkeypatch, [("m1", "not-loaded", "llm")])
    calls = []
    monkeypatch.setattr(d, "run_lms", lambda args: (calls.append(args) or (0, "")))
    code, body = post(base + "/api/models/load", {"model": "nope"}, token=srv.token, origin=base)
    assert code == 400 and "unknown model" in body["error"]
    assert calls == []


def test_post_load_runs_lms_with_an_argument_list_and_logs_it(srv, monkeypatch, tmp_path):
    base = f"http://{HOST}:{srv.server_address[1]}"
    fake_v0(monkeypatch, [("m1", "not-loaded", "llm")])
    calls = []

    def fake_run_lms(args):
        assert isinstance(args, list) and all(isinstance(a, str) for a in args)  # no shell string
        calls.append(args)
        return (0, "")

    monkeypatch.setattr(d, "run_lms", fake_run_lms)
    journal = tmp_path / "j.jsonl"
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))

    code, body = post(base + "/api/models/load", {"model": "m1"}, token=srv.token, origin=base)
    assert code == 200 and calls == [["load", "m1"]]
    assert body["action"] == "load" and body["model"] == "m1"
    assert body["reachable"] is True and body["states_known"] is True  # the new v0 state comes back

    last = json.loads(journal.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["event"] == "model" and last["action"] == "load"
    assert last["model"] == "m1" and last["outcome"] == "ok"


def test_post_unload_all_skips_model_validation(srv, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    fake_v0(monkeypatch, [])  # even with an empty list, "all" is a valid unload target
    calls = []
    monkeypatch.setattr(d, "run_lms", lambda args: (calls.append(args) or (0, "")))
    code, body = post(base + "/api/models/unload", {"model": "all"}, token=srv.token, origin=base)
    assert code == 200 and calls == [["unload", "--all"]] and body["model"] == "all"


def test_second_concurrent_load_is_409(srv, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    fake_v0(monkeypatch, [("m1", "not-loaded", "llm")])

    def slow_lms(args):
        time.sleep(0.8)  # long enough that the second request is sure to overlap
        return (0, "")

    monkeypatch.setattr(d, "run_lms", slow_lms)
    results = []

    def fire():
        code, _ = post(base + "/api/models/load", {"model": "m1"}, token=srv.token, origin=base)
        results.append(code)

    threads = [threading.Thread(target=fire), threading.Thread(target=fire)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 409]


def test_lms_failure_is_reported_plainly_and_logged(srv, monkeypatch, tmp_path):
    base = f"http://{HOST}:{srv.server_address[1]}"
    fake_v0(monkeypatch, [("m1", "not-loaded", "llm")])
    monkeypatch.setattr(d, "run_lms", lambda args: (1, "boom: not enough VRAM"))
    journal = tmp_path / "j.jsonl"
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))

    code, body = post(base + "/api/models/load", {"model": "m1"}, token=srv.token, origin=base)
    assert code == 502 and "boom: not enough VRAM" in body["error"]
    last = json.loads(journal.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["outcome"] == "error"


def test_lms_timeout_is_reported(srv, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    fake_v0(monkeypatch, [("m1", "not-loaded", "llm")])

    def timeout_lms(args):
        raise subprocess.TimeoutExpired(cmd="lms", timeout=d.LMS_TIMEOUT_SECONDS)

    monkeypatch.setattr(d, "run_lms", timeout_lms)
    code, body = post(base + "/api/models/load", {"model": "m1"}, token=srv.token, origin=base)
    assert code == 502 and "timed out" in body["error"]


def test_lms_exe_comes_from_the_env_var(monkeypatch):
    monkeypatch.setenv("IRIS_LMS_BIN", "C:/fake/lms.exe")
    assert d._lms_exe() == "C:/fake/lms.exe"


def test_lms_exe_falls_back_to_path_when_unset(monkeypatch):
    monkeypatch.delenv("IRIS_LMS_BIN", raising=False)
    monkeypatch.setattr(d.shutil, "which", lambda name: "C:/on/path/lms.exe")
    assert d._lms_exe() == "C:/on/path/lms.exe"


def test_lms_exe_is_empty_when_neither_env_var_nor_path(monkeypatch):
    monkeypatch.delenv("IRIS_LMS_BIN", raising=False)
    monkeypatch.setattr(d.shutil, "which", lambda name: None)
    assert d._lms_exe() == ""


def test_model_action_without_an_lms_executable_is_503(srv, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    fake_v0(monkeypatch, [("m1", "not-loaded", "llm")])
    monkeypatch.delenv("IRIS_LMS_BIN", raising=False)
    monkeypatch.setattr(d.shutil, "which", lambda name: None)
    code, body = post(base + "/api/models/load", {"model": "m1"}, token=srv.token, origin=base)
    assert code == 503 and "IRIS_LMS_BIN" in body["error"]


def test_post_to_any_other_route_stays_405(srv):
    base = f"http://{HOST}:{srv.server_address[1]}"
    for path in ("/", "/api/system", "/api/journal"):
        code, _ = post(base + path, {"model": "m1"}, token=srv.token, origin=base)
        assert code == 405, f"POST {path} must be a 405"


# --- iris model selection (~/.iris/settings.json) ---------------------------------

def _clean_model_env(monkeypatch, tmp_path):
    """Point settings and journal at temp files so tests never touch the real ones."""
    monkeypatch.delenv("IRIS_MODEL", raising=False)
    monkeypatch.setenv("IRIS_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("IRIS_JOURNAL", str(tmp_path / "journal.jsonl"))


def test_iris_model_get_reports_the_default_when_nothing_is_set(srv, tmp_path, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    _clean_model_env(monkeypatch, tmp_path)
    status, _, raw = get(base + "/api/iris/model")   # a read route: no token needed
    assert status == 200 and json.loads(raw) == {"model": DEFAULT_MODEL, "source": "default"}


def test_iris_model_get_reads_the_settings_file(srv, tmp_path, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    _clean_model_env(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"model": "m1", "other": 5}), encoding="utf-8")
    status, _, raw = get(base + "/api/iris/model")
    assert status == 200 and json.loads(raw) == {"model": "m1", "source": "settings"}


def test_iris_model_get_prefers_the_environment_variable(srv, tmp_path, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    _clean_model_env(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"model": "m1"}), encoding="utf-8")
    monkeypatch.setenv("IRIS_MODEL", "m9")
    status, _, raw = get(base + "/api/iris/model")
    assert status == 200 and json.loads(raw) == {"model": "m9", "source": "env"}


def test_iris_model_get_survives_a_broken_settings_file(srv, tmp_path, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    _clean_model_env(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text("not json at all", encoding="utf-8")
    status, _, raw = get(base + "/api/iris/model")
    assert status == 200 and json.loads(raw)["source"] == "default"


def test_iris_model_post_writes_the_file_keeps_other_keys_and_logs_it(srv, tmp_path, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    _clean_model_env(monkeypatch, tmp_path)
    fake_v0(monkeypatch, [("m1", "not-loaded", "llm")])
    (tmp_path / "settings.json").write_text(json.dumps({"other": 5}), encoding="utf-8")

    code, body = post(base + "/api/iris/model", {"model": "m1"}, token=srv.token, origin=base)
    assert code == 200 and body == {"model": "m1", "source": "settings"}

    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved == {"other": 5, "model": "m1"}   # any other keys the user has are kept

    last = json.loads((tmp_path / "journal.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["event"] == "model" and last["action"] == "select"
    assert last["model"] == "m1" and last["outcome"] == "ok"


def test_iris_model_post_unknown_model_is_400_and_writes_nothing(srv, tmp_path, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    _clean_model_env(monkeypatch, tmp_path)
    fake_v0(monkeypatch, [("m1", "not-loaded", "llm")])

    code, body = post(base + "/api/iris/model", {"model": "nope"}, token=srv.token, origin=base)
    assert code == 400 and "unknown model" in body["error"]
    assert not (tmp_path / "settings.json").exists()


@pytest.mark.parametrize("body", [{}, {"model": ""}, {"model": 5}])
def test_iris_model_post_missing_or_bad_id_is_400(srv, tmp_path, monkeypatch, body):
    base = f"http://{HOST}:{srv.server_address[1]}"
    _clean_model_env(monkeypatch, tmp_path)
    fake_v0(monkeypatch, [("m1", "not-loaded", "llm")])

    code, b = post(base + "/api/iris/model", body, token=srv.token, origin=base)
    assert code == 400


def test_iris_model_post_needs_the_token_and_origin(srv, tmp_path, monkeypatch):
    base = f"http://{HOST}:{srv.server_address[1]}"
    _clean_model_env(monkeypatch, tmp_path)
    fake_v0(monkeypatch, [("m1", "not-loaded", "llm")])

    code, body = post(base + "/api/iris/model", {"model": "m1"}, origin=base)   # no token
    assert code == 403 and "forbidden" in body["error"]
    code, _ = post(base + "/api/iris/model", {"model": "m1"}, token=srv.token, origin="http://evil.example")
    assert code == 403


def test_resolve_model_re_resolves_on_every_call(tmp_path, monkeypatch):
    """The choice is read on every request: a file edit applies without a restart."""
    from iris.llm import resolve_model

    settings = tmp_path / "settings.json"
    monkeypatch.delenv("IRIS_MODEL", raising=False)
    monkeypatch.setenv("IRIS_SETTINGS", str(settings))

    assert resolve_model() == (DEFAULT_MODEL, "default")   # nothing set yet
    settings.write_text(json.dumps({"model": "m1"}), encoding="utf-8")
    assert resolve_model() == ("m1", "settings")           # the file is re-read on every call
    monkeypatch.setenv("IRIS_MODEL", "m9")
    assert resolve_model() == ("m9", "env")                # the environment variable still wins



# --- sessions.db (item 5) ---------------------------------------------------------

SESSIONS_SQL = """
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY, source TEXT, pid INTEGER, started_at TEXT, ended_at TEXT,
    exit_code INTEGER, status TEXT, interactive INTEGER, provider TEXT, model TEXT, cwd TEXT,
    workspace_root TEXT, is_subagent INTEGER, parent_session_id TEXT, updated_at TEXT)
"""


def make_sessions_db(path: Path, rows: list[tuple]) -> Path:
    """A temporary sessions.db with the real schema; tests never touch ~/.iris."""
    con = sqlite3.connect(str(path))
    try:
        con.execute(SESSIONS_SQL)
        con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()
    return path


def _recent():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 60)) + "Z"


def sess_row(sid="s1", *, ended=None, status="completed", pid=4242, model="qwen3.8-27b",
             provider="lmstudio", cwd=r"W:\work\proj", subagent=0, parent=None,
             updated="2026-10-06T12:00:00"):
    return (sid, "cli", pid, "2026-10-06T11:00:00", ended, 0 if ended else None, status,
            1, provider, model, cwd, r"W:\work", subagent, parent, updated)


def alive_pids(monkeypatch, pids):
    """psutil.pid_exists as a set lookup, so the tests never depend on real processes."""
    monkeypatch.setattr(d.psutil, "pid_exists", lambda pid: pid in pids)


def test_live_session_is_running(tmp_path, monkeypatch):
    db = make_sessions_db(tmp_path / "s.db", [sess_row(ended=None, updated=_recent())])
    alive_pids(monkeypatch, {4242})
    rows = d.session_rows(db)
    assert len(rows) == 1
    assert rows[0]["live"] is True and rows[0]["status"] == "running"


def test_stale_row_with_dead_pid_never_shows_as_running(tmp_path, monkeypatch):
    """The case that confused before: ended_at NULL but the pid is gone."""
    db = make_sessions_db(tmp_path / "s.db", [sess_row(ended=None)])
    alive_pids(monkeypatch, set())   # 4242 is not in the set: dead
    rows = d.session_rows(db)
    assert len(rows) == 1
    assert rows[0]["live"] is False
    assert rows[0]["status"] == "ended (no exit recorded)"


def test_ended_status_is_shown_as_is(tmp_path, monkeypatch):
    db = make_sessions_db(tmp_path / "s.db", [
        sess_row("a", ended="2026-10-06T11:30:00", status="completed"),
        sess_row("b", ended="2026-10-06T11:45:00", status="idle"),
        sess_row("c", ended="2026-10-06T11:50:00", status="paused"),   # unknown value, still as-is
    ])
    alive_pids(monkeypatch, set())
    rows = d.session_rows(db)
    assert {r["session_id"]: r["status"] for r in rows} == {"a": "completed", "b": "idle", "c": "paused"}


def test_subagents_group_under_their_parent(tmp_path, monkeypatch):
    db = make_sessions_db(tmp_path / "s.db", [
        sess_row("parent"),
        sess_row("kid1", subagent=1, parent="parent"),
        sess_row("kid2", subagent=1, parent="parent"),
        sess_row("orphan", subagent=1, parent="ghost"),   # no such parent: listed on its own
    ])
    alive_pids(monkeypatch, set())
    rows = d.session_rows(db)
    assert [r["session_id"] for r in rows] == ["parent", "orphan"]
    assert [c["session_id"] for c in rows[0]["children"]] == ["kid1", "kid2"]


def test_missing_db_file_means_no_sessions(tmp_path):
    assert d.session_rows(tmp_path / "nope.db") == []


def test_session_row_shows_folder_model_provider_and_age(tmp_path, monkeypatch):
    db = make_sessions_db(tmp_path / "s.db", [sess_row(cwd=r"W:\work\proj", model="m9", provider="anthropic")])
    alive_pids(monkeypatch, set())
    now = d._parse_ts("2026-10-06T12:05:00")   # five minutes after updated_at
    r = d.session_rows(db, now=now)[0]
    assert r["folder"] == "proj"
    assert r["model"] == "m9" and r["provider"] == "anthropic"
    assert 295 <= r["age_seconds"] <= 305


def test_api_sessions_route(tmp_path, srv, monkeypatch):
    db = make_sessions_db(tmp_path / "s.db", [sess_row(ended=None, updated=_recent())])
    alive_pids(monkeypatch, {4242})
    monkeypatch.setattr(d, "SESSIONS_DB", db)
    code, _headers, text = get(f"http://{HOST}:{srv.server_address[1]}/api/sessions")
    body = json.loads(text)
    assert code == 200 and body["ok"] is True
    assert body["rows"][0]["status"] == "running"




# --- Claude Code projects (item 6) --------------------------------------------------


def make_claude_dir(base: Path, folders: dict[str, list[tuple[str, float]]]) -> Path:
    """folders maps a project folder name to [(jsonl file name, age in seconds)]."""
    now = time.time()
    for name, files in folders.items():
        d = base / name
        d.mkdir(parents=True)
        for fname, age in files:
            f = d / fname
            f.write_text("never read\n", encoding="utf-8")   # content is irrelevant; only the mtime matters
            os.utime(f, (now - age, now - age))
    return base


def test_claude_projects_lists_only_fresh_jsonl_folders(tmp_path):
    base = make_claude_dir(tmp_path / "projects", {
        "fresh": [("a.jsonl", 60)],             # one minute old: active
        "borderline_old": [("b.jsonl", 700)],   # over ten minutes: not listed
        "no_jsonl": [],                          # no .jsonl at all: not listed
    })
    (base / "stray.txt").write_text("x", encoding="utf-8")   # a file, not a folder: ignored
    rows = d.claude_projects(base)
    assert [r["folder"] for r in rows] == ["fresh"]
    assert 50 <= rows[0]["age_seconds"] <= 70


def test_claude_project_uses_the_newest_jsonl(tmp_path):
    base = make_claude_dir(tmp_path / "projects", {
        "mixed": [("old.jsonl", 3600), ("new.jsonl", 120)],   # the newest file decides: active
    })
    rows = d.claude_projects(base)
    assert len(rows) == 1 and 110 <= rows[0]["age_seconds"] <= 130


def test_claude_missing_dir_means_empty(tmp_path):
    assert d.claude_projects(tmp_path / "nope") == []


def test_api_claude_route(tmp_path, srv, monkeypatch):
    base = make_claude_dir(tmp_path / "projects", {"fresh": [("a.jsonl", 30)]})
    monkeypatch.setattr(d, "CLAUDE_PROJECTS", base)
    with d._lock:   # the route caches for 30 s; drop any stale entry first
        d._cache.pop("claude", None)
    code, _headers, text = get(f"http://{HOST}:{srv.server_address[1]}/api/claude")
    body = json.loads(text)
    assert code == 200 and body["ok"] is True
    assert [r["folder"] for r in body["rows"]] == ["fresh"]


def test_open_but_quiet_session_is_not_running(tmp_path, monkeypatch):
    """Cline desktop sessions share the app pid: open but idle for an hour is not running."""
    db = make_sessions_db(tmp_path / "s.db", [sess_row(ended=None, status="idle", updated="2026-10-06T12:00:00")])
    alive_pids(monkeypatch, {4242})
    rows = d.session_rows(db)
    assert rows[0]["live"] is False and rows[0]["status"] == "idle"


# --- eval reports -----------------------------------------------------------------

def _report(stamp):
    """A minimal valid report: the same keys iris.eval.write_report always writes."""
    return json.dumps({"stamp": stamp, "model": "test-model",
                       "summary": {"passed": 1, "tasks": 2}, "results": []})


def _new_report(stamp):
    """A report from a run after item 15 and 16: the summary has by_category and safety."""
    return json.dumps({"stamp": stamp, "model": "test-model",
                       "summary": {"passed": 1, "tasks": 2,
                                   "by_category": {"files": {"tasks": 2, "passed": 1, "success_rate": 0.5}},
                                   "safety": {"forbidden_ok": 0, "unknown_tools": 1, "never_asked": 0}},
                       "results": []})


def _answer_report(stamp):
    """A report from the current eval: the summary has language and answers, and two of its
    results failed their answer checks."""
    return json.dumps({
        "stamp": stamp, "model": "test-model",
        "summary": {"passed": 1, "tasks": 3,
                    "by_category": {}, "safety": {"forbidden_ok": 0, "unknown_tools": 0, "never_asked": 0},
                    "language": {"wrong": 2, "unknown": 0},
                    "answers": {"checked": 3, "wrong": 2}},
        "results": [
            {"id": "calc-17x23", "passed": False, "answer": "991",
             "reasons": ["answer says 991, expected 391"]},
            {"id": "greet-nl", "passed": False, "answer": "Hello there!",
             "reasons": ["missing call: say", "answer lacks groetse"]},
            {"id": "files-list", "passed": True, "answer": "", "reasons": []},
        ],
    })


def test_eval_payload_latest_and_runs(tmp_path, monkeypatch):
    (tmp_path / "20261012-090000.json").write_text(_report("20261012-090000"), encoding="utf-8")
    (tmp_path / "20261013-090000.json").write_text(_report("20261013-090000"), encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    p = d.eval_payload()
    assert p["latest"]["stamp"] == "20261013-090000"   # newest by name: stamps sort by time
    assert p["runs"] == 2


def test_eval_payload_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    assert d.eval_payload() == {"latest": None, "runs": 0}


def test_eval_payload_unreadable_newest(tmp_path, monkeypatch):
    (tmp_path / "20261013-090000.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    assert d.eval_payload() == {"latest": None, "runs": 1, "error": "unreadable report"}


def test_eval_payload_new_report_has_category_and_safety(tmp_path, monkeypatch):
    (tmp_path / "20261013-090000.json").write_text(_new_report("20261013-090000"), encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    p = d.eval_payload()
    assert p["latest"]["by_category"] == {"files": {"tasks": 2, "passed": 1, "success_rate": 0.5}}
    assert p["latest"]["safety"] == {"forbidden_ok": 0, "unknown_tools": 1, "never_asked": 0}


def test_eval_payload_old_report_has_no_new_keys(tmp_path, monkeypatch):
    (tmp_path / "20261013-090000.json").write_text(_report("20261013-090000"), encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    p = d.eval_payload()   # an old result without the keys still works, no error
    assert "by_category" not in p["latest"] and "safety" not in p["latest"]


def test_eval_payload_new_report_has_language_answers_and_wrong_answers(tmp_path, monkeypatch):
    (tmp_path / "20261013-090000.json").write_text(_answer_report("20261013-090000"), encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    p = d.eval_payload()
    assert p["latest"]["language"] == {"wrong": 2, "unknown": 0}
    assert p["latest"]["answers"] == {"checked": 3, "wrong": 2}
    assert p["latest"]["wrong_answers"] == [
        {"id": "calc-17x23", "reasons": ["answer says 991, expected 391"], "answer": "991"},
        {"id": "greet-nl", "reasons": ["answer lacks groetse"], "answer": "Hello there!"},
    ]


def test_eval_payload_old_report_has_no_language_or_answers(tmp_path, monkeypatch):
    (tmp_path / "20261013-090000.json").write_text(_report("20261013-090000"), encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    p = d.eval_payload()   # an old result without the keys still works, no error
    assert "language" not in p["latest"] and "answers" not in p["latest"]
    assert p["latest"]["wrong_answers"] == []


def test_eval_payload_wrong_answers_ignores_non_dict_results(tmp_path, monkeypatch):
    report = json.loads(_answer_report("20261013-090000"))
    report["results"].insert(0, "not a dict")
    (tmp_path / "20261013-090000.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    p = d.eval_payload()   # a broken entry must not take the dashboard down
    assert [w["id"] for w in p["latest"]["wrong_answers"]] == ["calc-17x23", "greet-nl"]


def test_eval_payload_wrong_answers_is_capped_at_twenty(tmp_path, monkeypatch):
    report = json.loads(_answer_report("20261013-090000"))
    for i in range(25):
        report["results"].append({"id": f"t{i}", "passed": False, "answer": "x",
                                  "reasons": ["answer says x"]})
    (tmp_path / "20261013-090000.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    p = d.eval_payload()
    assert len(p["latest"]["wrong_answers"]) == 20


def test_api_eval_route(server, tmp_path, monkeypatch):
    (tmp_path / "20261013-090000.json").write_text(_report("20261013-090000"), encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    code, _headers, text = get(server + "/api/eval")
    body = json.loads(text)
    assert code == 200 and body["latest"]["stamp"] == "20261013-090000" and body["runs"] == 1


def test_api_eval_skips_a_newer_partial_report(server, tmp_path, monkeypatch):
    (tmp_path / "20261013-090000.json").write_text(_report("20261013-090000"), encoding="utf-8")
    partial = json.dumps({"stamp": "20261014-090000", "model": "test-model", "partial": True,
                          "summary": {"passed": 1, "tasks": 1}, "results": []})
    (tmp_path / "20261014-090000.json").write_text(partial, encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    code, _headers, text = get(server + "/api/eval")
    body = json.loads(text)
    assert code == 200 and body["latest"]["stamp"] == "20261013-090000" and body["runs"] == 1


def _history_report(stamp, passed, tasks, language=None):
    """A report with one result per task; the first `passed` of them pass."""
    summary = {"passed": passed, "tasks": tasks, "success_rate": round(passed / tasks, 3)}
    if language is not None:
        summary["language"] = {"wrong": language, "unknown": 0}
    results = [{"id": f"t{i}", "passed": i < passed} for i in range(tasks)]
    return json.dumps({"stamp": stamp, "model": "test-model", "summary": summary, "results": results})


def test_eval_history_payload_reads_the_newest_runs(tmp_path, monkeypatch):
    (tmp_path / "20261013-090000.json").write_text(_history_report("20261013-090000", 1, 2), encoding="utf-8")
    (tmp_path / "20261014-090000.json").write_text("{not json", encoding="utf-8")
    report_c = _history_report("20261015-090000", 2, 2, language=1)
    (tmp_path / "20261015-090000.json").write_text(report_c, encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    p = d.eval_history_payload()
    assert [r["stamp"] for r in p["runs"]] == ["20261013-090000", "20261015-090000"]
    assert p["runs"][0]["language_wrong"] is None and p["runs"][1]["language_wrong"] == 1
    assert p["tasks"]["t0"] == {"passed": 2, "total": 2, "per_run": ["1/1", "1/1"]}
    assert p["tasks"]["t1"] == {"passed": 1, "total": 2, "per_run": ["0/1", "1/1"]}
    assert p["flaky"] == ["t1"] and p["skipped"] == 1


def test_eval_history_payload_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    assert d.eval_history_payload() == {"runs": [], "tasks": {}, "flaky": [], "skipped": 0}


def test_api_eval_history_route(server, tmp_path, monkeypatch):
    (tmp_path / "20261015-090000.json").write_text(_history_report("20261015-090000", 1, 2), encoding="utf-8")
    monkeypatch.setenv("IRIS_EVAL_DIR", str(tmp_path))
    d._cache.clear()   # a previous test may have cached this route's payload
    code, _headers, text = get(server + "/api/eval/history")
    body = json.loads(text)
    assert code == 200 and body["runs"][0]["stamp"] == "20261015-090000"
    assert body["tasks"]["t1"] == {"passed": 0, "total": 1, "per_run": ["0/1"]}
    assert body["flaky"] == ["t1"] and "error" not in body


def test_page_contains_eval_tab_and_empty_state(server):
    status, _headers, page = get(server + "/")
    assert status == 200
    assert "/api/eval" in page
    assert "No eval run yet. Run: python -m iris eval" in page


def test_page_contains_eval_history_panels(server):
    status, _headers, page = get(server + "/")
    assert status == 200
    for needle in ("/api/eval/history", "History", "Flaky tasks"):
        assert needle in page


# --- prompt queue (item 2) ---------------------------------------------------------


def test_queue_payload_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "q.json"))
    assert d.queue_payload() == {"ok": True, "prompts": []}


def test_queue_payload_lists_pending_prompts(tmp_path, monkeypatch):
    from iris import prompt_queue

    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "q.json"))
    prompt_queue.add("leadscan", "Run de tests.", now="2026-10-14 09:00")
    assert d.queue_payload()["prompts"] == [
        {"id": 1, "agent": "leadscan", "text": "Run de tests.", "added": "2026-10-14 09:00"}]


def test_queue_payload_broken_file(tmp_path, monkeypatch):
    (tmp_path / "q.json").write_text("{oops", encoding="utf-8")
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "q.json"))
    assert d.queue_payload() == {"ok": False, "prompts": [], "error": "the queue file is broken"}


def test_api_queue_route(server, tmp_path, monkeypatch):
    from iris import prompt_queue

    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "q.json"))
    prompt_queue.add("leadscan", "Run de tests.", now="2026-10-14 09:00")
    code, _headers, text = get(server + "/api/queue")
    body = json.loads(text)
    assert code == 200 and body["ok"] is True
    assert body["prompts"] == [{"id": 1, "agent": "leadscan", "text": "Run de tests.",
                                "added": "2026-10-14 09:00"}]


# --- prompt queue writes (item 4) --------------------------------------------------


def _journal_queue_events(path):
    """The {"event": "queue"} lines from a journal file, oldest first."""
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("event") == "queue":
            events.append(entry)
    return events


def test_queue_add_ok_returns_new_state_and_logs(srv, tmp_path, monkeypatch):
    queue, journal = tmp_path / "q.json", tmp_path / "journal.jsonl"
    monkeypatch.setenv("IRIS_QUEUE", str(queue))
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))
    base = f"http://{HOST}:{srv.server_address[1]}"

    code, body = post(base + "/api/queue/add", {"agent": "leadscan", "text": "Run de tests."},
                      token=srv.token, origin=base)
    assert code == 200 and body["ok"] is True
    p = body["prompts"][0]
    assert p["id"] == 1 and p["agent"] == "leadscan" and p["text"] == "Run de tests."
    assert isinstance(p["added"], str) and p["added"]

    events = _journal_queue_events(journal)   # the journal line exists after add
    assert events[-1]["action"] == "add" and events[-1]["agent"] == "leadscan" and events[-1]["id"] == 1


def test_queue_delete_ok_returns_new_state_and_logs(srv, tmp_path, monkeypatch):
    from iris import prompt_queue

    queue, journal = tmp_path / "q.json", tmp_path / "journal.jsonl"
    monkeypatch.setenv("IRIS_QUEUE", str(queue))
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))
    base = f"http://{HOST}:{srv.server_address[1]}"

    prompt_queue.add("leadscan", "first")   # id 1
    prompt_queue.add("cline", "second")     # id 2
    code, body = post(base + "/api/queue/delete", {"id": 1}, token=srv.token, origin=base)
    assert code == 200 and body["ok"] is True
    assert [p["id"] for p in body["prompts"]] == [2]

    events = _journal_queue_events(journal)
    assert events[-1]["action"] == "delete" and events[-1]["agent"] == "leadscan" and events[-1]["id"] == 1


def test_queue_move_ok_returns_new_state_and_logs(srv, tmp_path, monkeypatch):
    from iris import prompt_queue

    queue, journal = tmp_path / "q.json", tmp_path / "journal.jsonl"
    monkeypatch.setenv("IRIS_QUEUE", str(queue))
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))
    base = f"http://{HOST}:{srv.server_address[1]}"

    prompt_queue.add("leadscan", "first")   # id 1
    prompt_queue.add("cline", "second")     # id 2
    code, body = post(base + "/api/queue/move", {"id": 1, "step": 1}, token=srv.token, origin=base)
    assert code == 200 and body["ok"] is True
    assert [p["id"] for p in body["prompts"]] == [2, 1]   # the two prompts swapped

    events = _journal_queue_events(journal)
    assert events[-1]["action"] == "move" and events[-1]["id"] == 1


# Driven by WRITE_ROUTES itself, so a write route added later is covered without a new test.
@pytest.mark.parametrize("route", d.WRITE_ROUTES)
@pytest.mark.parametrize("auth", ["no token", "wrong token", "foreign origin", "no origin"])
def test_every_write_route_refuses_without_the_token_and_origin(srv, route, auth):
    base = f"http://{HOST}:{srv.server_address[1]}"
    token, origin = {"no token": (None, base), "wrong token": ("not-the-token", base),
                     "foreign origin": (srv.token, "http://evil.example"), "no origin": (srv.token, None)}[auth]
    code, _ = post(base + route, {"model": "m1", "agent": "a", "text": "t", "id": 1, "step": 1},
                   token=token, origin=origin)
    assert code == 403


@pytest.mark.parametrize("route", ["/api/queue/add", "/api/queue/delete", "/api/queue/move"])
def test_queue_write_without_a_token_is_403(srv, route):
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, body = post(base + route, {"agent": "a", "text": "t"}, origin=base)   # no token
    assert code == 403 and "forbidden" in body["error"]


@pytest.mark.parametrize("route", ["/api/queue/add", "/api/queue/delete", "/api/queue/move"])
def test_queue_write_from_a_foreign_origin_is_403(srv, route):
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, _ = post(base + route, {"agent": "a", "text": "t"}, token=srv.token, origin="http://evil.example")
    assert code == 403


def test_queue_write_with_bad_json_is_400(srv):
    base = f"http://{HOST}:{srv.server_address[1]}"
    req = urllib.request.Request(base + "/api/queue/add", data=b"{not json", method="POST",
                                 headers={"Content-Type": "application/json"})
    req.add_header("X-Iris-Token", srv.token)   # auth passes, then the body fails to parse
    req.add_header("Origin", base)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 400


@pytest.mark.parametrize("agent", ["", "   ", "x" * 41])
def test_queue_add_bad_agent_is_400(srv, tmp_path, monkeypatch, agent):
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "q.json"))
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, body = post(base + "/api/queue/add", {"agent": agent, "text": "ok"}, token=srv.token, origin=base)
    assert code == 400 and "agent" in body["error"]


@pytest.mark.parametrize("text", ["", "   ", "x" * 8001])
def test_queue_add_bad_text_is_400(srv, tmp_path, monkeypatch, text):
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "q.json"))
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, body = post(base + "/api/queue/add", {"agent": "leadscan", "text": text}, token=srv.token, origin=base)
    assert code == 400 and "text" in body["error"]


def test_queue_delete_bad_id_is_400(srv, tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "q.json"))
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, body = post(base + "/api/queue/delete", {"id": "nope"}, token=srv.token, origin=base)
    assert code == 400 and "id" in body["error"]


def test_queue_move_bad_id_is_400(srv, tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "q.json"))
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, body = post(base + "/api/queue/move", {"id": None, "step": 1}, token=srv.token, origin=base)
    assert code == 400 and "id" in body["error"]


@pytest.mark.parametrize("step", [0, 2, -2, "1"])
def test_queue_move_bad_step_is_400(srv, tmp_path, monkeypatch, step):
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "q.json"))
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, body = post(base + "/api/queue/move", {"id": 1, "step": step}, token=srv.token, origin=base)
    assert code == 400 and "step" in body["error"]


def test_queue_delete_unknown_id_is_404(srv, tmp_path, monkeypatch):
    from iris import prompt_queue

    queue = tmp_path / "q.json"
    monkeypatch.setenv("IRIS_QUEUE", str(queue))
    base = f"http://{HOST}:{srv.server_address[1]}"
    prompt_queue.add("leadscan", "first")   # id 1 exists; 99 does not
    code, body = post(base + "/api/queue/delete", {"id": 99}, token=srv.token, origin=base)
    assert code == 404 and "no prompt" in body["error"]


def test_queue_move_unknown_id_is_404(srv, tmp_path, monkeypatch):
    from iris import prompt_queue

    queue = tmp_path / "q.json"
    monkeypatch.setenv("IRIS_QUEUE", str(queue))
    base = f"http://{HOST}:{srv.server_address[1]}"
    prompt_queue.add("leadscan", "first")   # id 1 exists; 99 does not
    code, body = post(base + "/api/queue/move", {"id": 99, "step": 1}, token=srv.token, origin=base)
    assert code == 404 and "no prompt" in body["error"]


@pytest.mark.parametrize("route", ["/api/queue/add", "/api/queue/delete", "/api/queue/move"])
def test_get_on_a_queue_write_route_is_405(srv, route):
    base = f"http://{HOST}:{srv.server_address[1]}"
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(base + route, timeout=5)
    assert exc.value.code == 405


# --- queue tab (item 3) -----------------------------------------------------------


def test_page_contains_queue_tab_and_empty_state(server):
    status, _headers, page = get(server + "/")
    assert status == 200
    assert "/api/queue" in page
    assert 'data-tab="queue"' in page
    assert "The queue is empty." in page


# --- cline start (item 5) ---------------------------------------------------------


def _journal_autopilot_events(path):
    """The {"event": "autopilot"} lines from a journal file, oldest first."""
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("event") == "autopilot":
            events.append(entry)
    return events


def test_cline_start_ok_returns_result_and_queue(srv, tmp_path, monkeypatch):
    from iris import autopilot as ap
    from iris import prompt_queue

    queue, journal = tmp_path / "q.json", tmp_path / "journal.jsonl"
    monkeypatch.setenv("IRIS_QUEUE", str(queue))
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))
    calls: list[tuple[str, str]] = []

    def fake(agent, repo=""):
        calls.append((agent, repo))
        return "started session abc"

    monkeypatch.setattr(ap, "cline_start", fake)
    prompt_queue.add("leadscan", "first")   # id 1; the fake does not take it, so it stays
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, body = post(base + "/api/cline/start", {"agent": "leadscan"}, token=srv.token, origin=base)
    assert code == 200 and body["ok"] is True
    assert body["result"] == "started session abc"
    assert [p["id"] for p in body["queue"]] == [1]   # the new queue state comes back
    assert calls == [("leadscan", "")]               # looked up on the module, called with (agent, repo)

    events = _journal_autopilot_events(journal)      # a success is journaled too
    assert events[-1]["event"] == "autopilot" and events[-1]["result"] == "started session abc"


def test_cline_start_error_result_is_409_and_logged(srv, tmp_path, monkeypatch):
    import iris.autopilot as ap

    journal = tmp_path / "journal.jsonl"
    monkeypatch.setenv("IRIS_JOURNAL", str(journal))
    monkeypatch.setattr(ap, "cline_start", lambda a, r="": "error: no prompts waiting for leadscan")
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, body = post(base + "/api/cline/start", {"agent": "leadscan"}, token=srv.token, origin=base)
    assert code == 409 and body["error"] == "no prompts waiting for leadscan"

    events = _journal_autopilot_events(journal)      # the error result is journaled as well
    assert len(events) == 1
    e = events[0]
    assert (e["event"], e["source"]) == ("autopilot", "dashboard")
    assert (e["agent"], e["repo"], e["answer"]) == ("leadscan", "", True)
    assert e["result"] == "error: no prompts waiting for leadscan"


@pytest.mark.parametrize("body", [{}, {"agent": ""}, {"agent": "   "}, {"agent": "x" * 41}])
def test_cline_start_bad_agent_is_400(srv, body):
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, b = post(base + "/api/cline/start", body, token=srv.token, origin=base)
    assert code == 400 and "agent" in b["error"]


def test_cline_start_without_a_token_is_403(srv):
    base = f"http://{HOST}:{srv.server_address[1]}"
    code, body = post(base + "/api/cline/start", {"agent": "leadscan"}, origin=base)   # no token
    assert code == 403 and "forbidden" in body["error"]


def test_cline_start_while_one_runs_is_409(srv, monkeypatch):
    import iris.autopilot as ap

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(ap, "cline_start", lambda a, r="": calls.append((a, r)) or "started")
    base = f"http://{HOST}:{srv.server_address[1]}"
    with d._cline_start_lock:   # hold the lock for the whole request
        code, body = post(base + "/api/cline/start", {"agent": "leadscan"}, token=srv.token, origin=base)
    assert code == 409 and "already running" in body["error"]
    assert calls == []   # cline_start was never reached while the lock is held


def test_get_on_cline_start_is_405(srv):
    base = f"http://{HOST}:{srv.server_address[1]}"
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(base + "/api/cline/start", timeout=5)
    assert exc.value.code == 405


# --- page: the new autopilot UI (item 10) ---------------------------------------


def test_page_has_the_ids_and_routes_the_autopilot_ui_needs(server):
    status, _headers, page = get(server + "/")
    assert status == 200
    for needle in ("/api/runs", "/api/cline/start", "/api/queue/add", "/api/queue/delete",
                   "/api/queue/move", "X-Iris-Token", "Autopilot"):
        assert needle in page, f"the page is missing {needle!r}"


def test_page_has_no_innerhtml_assignment_from_a_variable(server):
    # On main the regex below finds no hit at all (the page never uses innerHTML), so we
    # assert it stays zero rather than merely "did not grow".
    status, _headers, page = get(server + "/")
    assert status == 200
    hits = re.findall(r'''innerHTML\s*=\s*[^\\"'\`]''', page)
    assert hits == [], f"the page assigns innerHTML from a variable: {hits!r}"


def test_page_run_row_wraps_narrow_and_button_says_why(server):
    status, _headers, page = get(server + "/")
    assert status == 200
    assert "@media (max-width: 700px)" in page   # R4: the run row wraps instead of squashing
    assert "refreshBtn();" in page               # still called once right after the click listener
    start = page.index("function refreshBtn")    # and its body uses r.reason for the disabled title
    fn = page[start:page.index("\n    }", start)]
    assert "r.reason" in fn, "refreshBtn must use r.reason for the tooltip"


def test_page_start_button_warns_that_it_can_take_a_few_minutes(server):
    # a start can now take up to about 4 minutes (two CLI calls with their 120 s timeout plus polls)
    status, _headers, page = get(server + "/")
    assert status == 200
    assert "starting... (can take a few minutes)" in page



# --- skills: the review page -----------------------------------------------------

def _ready_skill(name="say_hello"):
    from iris import skills, tools

    folder = skills.save_plan("Zeg hallo", name, "confirm", "A run(who).", set(tools.TOOLS))
    skills.start_build(name, start=lambda p, cwd: "started fake")
    (folder / "skill.toml").write_text('description = "Greets."\n[params.who]\ntype = "string"\n', encoding="utf-8")
    (folder / "skill.py").write_text("def run(who):\n    return 'hi ' + who\n", encoding="utf-8")
    skills._save_meta(name, {**skills.meta(name), "status": "ready"})
    skills._save_trust(name, checked=skills._digest(name, "confirm"))  # what a passing check() records


def test_the_skills_page_shows_the_code_and_approves_only_with_the_token(server, srv):
    from iris import registry

    _ready_skill()
    status, _, body = get(server + "/api/skills")
    (row,) = json.loads(body)["skills"]
    assert (status, row["name"], row["tier"], row["status"]) == (200, "say_hello", "confirm", "ready")
    assert row["source"] == ""  # built here; an imported skill names its zip so the page warns about it
    assert row["files"]["skill.py"] == "def run(who):\n    return 'hi ' + who\n" and "plan.md" in row["files"]
    origin = server.replace("localhost", "127.0.0.1")
    assert post(server + "/api/skills/approve", {"name": "say_hello"})[0] == 403
    assert post(server + "/api/skills/approve", {"name": "say_hello"}, token="wrong", origin=origin)[0] == 403
    status, data = post(server + "/api/skills/approve", {"name": "say_hello"}, token=srv.token, origin=origin)
    assert status == 200 and data["skills"][0]["status"] == "approved"
    assert "say_hello" in registry.active_tools()
    status, data = post(server + "/api/skills/approve", {"name": "nope_x"}, token=srv.token, origin=origin)
    assert status == 400 and data["error"] == "no skill named nope_x"
    status, data = post(server + "/api/skills/remove", {"name": "say_hello"}, token=srv.token, origin=origin)
    assert status == 200 and data["skills"] == []
    assert "say_hello" not in registry.active_tools()


def test_the_skills_page_is_empty_without_skills(server):
    assert json.loads(get(server + "/api/skills")[2]) == {"ok": True, "skills": []}


def test_tools_payload_marks_plugin_tools_off_and_lists_approved_skills(tmp_path, monkeypatch):
    from iris import skills

    cfg = tmp_path / "c.toml"
    cfg.write_text('plugins = ["comfyui"]', encoding="utf-8")
    monkeypatch.setenv("IRIS_CONFIG", str(cfg))
    _ready_skill()
    skills.approve("say_hello")
    by_name = {t["name"]: t for t in d.tools_payload()["tools"]}
    assert by_name["comfy_status"]["active"] is True and by_name["followups"]["active"] is False
    assert by_name["calculate"]["active"] is True and by_name["calculate"]["kind"] == "core"
    assert (by_name["say_hello"]["kind"], by_name["say_hello"]["tier"]) == ("skill", "confirm")


def test_logs_payload_tails_each_log(tmp_path, monkeypatch):
    import iris.voice
    from iris import config
    from iris import dashboard as d

    monkeypatch.setattr(iris.voice, "VOXCPM_DIR", tmp_path / "voxcpm")  # never this PC's real worker.log

    voice_log = config.config_path().parent / "logs" / "voice.log"
    voice_log.parent.mkdir(parents=True, exist_ok=True)
    voice_log.write_text("".join(f"line {i}\n" for i in range(300)), encoding="utf-8")
    payload = d.logs_payload()
    by_name = {log["name"]: log for log in payload["logs"]}
    assert by_name["Voice mode"]["lines"][-1] == "line 299" and len(by_name["Voice mode"]["lines"]) == 200
    assert by_name["Overlay"] == {"name": "Overlay", "path": str(config.config_path().parent / "overlay.log"),
                                  "exists": False, "lines": []}
    assert by_name["VoxCPM2 speech"]["path"] == str(tmp_path / "voxcpm" / "worker.log")
    assert by_name["App"]["path"] == str(config.config_path().parent / "logs" / "app.log")
    assert by_name["Voice packs"]["path"] == str(config.config_path().parent / "logs" / "packs.log")


def test_tail_lines_reads_only_the_end_of_a_big_file(tmp_path, monkeypatch):
    from iris import dashboard as d

    monkeypatch.setattr(d, "LOG_TAIL_BYTES", 20)
    f = tmp_path / "big.log"
    f.write_bytes(b"first line\n" + b"x" * 100 + b"\nlast \xff line\n")
    assert d.tail_lines(f) == ["last � line"]  # the cut-off x's are not shown as a line


def test_logs_route_is_read_only(server):
    with urllib.request.urlopen(server + "/api/logs", timeout=5) as r:
        assert json.loads(r.read())["ok"] is True


def test_a_rebound_host_name_gets_nothing(server, srv):
    """DNS rebinding: a site that points its own name at 127.0.0.1 sends its name as Host. It must not read the page
    (with its token) or any route, and writes stay refused too."""
    import http.client

    port = int(server.rsplit(":", 1)[1])
    for method, path in (("GET", "/"), ("GET", "/api/journal"), ("GET", "/api/settings"), ("POST", "/api/settings")):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body="{}" if method == "POST" else None,
                     headers={"Host": f"evil.example:{port}", "X-Iris-Token": srv.token,
                              "Origin": f"http://127.0.0.1:{port}"})
        r = conn.getresponse()
        body = r.read().decode("utf-8")
        conn.close()
        assert r.status == 421 and srv.token not in body, (method, path)
    for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"LOCALHOST:{port}"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/", headers={"Host": host})
        assert conn.getresponse().status == 200
        conn.close()


def _raw_post(srv, path, body: bytes, headers=None):
    import http.client

    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest("POST", path)
    for k, v in {"X-Iris-Token": srv.token, "Origin": f"http://127.0.0.1:{port}", **(headers or {})}.items():
        conn.putheader(k, v)
    if "Content-Length" not in (headers or {}) and "Transfer-Encoding" not in (headers or {}):
        conn.putheader("Content-Length", str(len(body)))
    conn.endheaders(body)
    r = conn.getresponse()
    out = r.status, json.loads(r.read().decode("utf-8") or "{}")
    conn.close()
    return out


@pytest.mark.parametrize("path", ["/api/settings", "/api/skills/approve", "/api/packs/install", "/api/setup/server",
                                  "/api/queue/add", "/api/models/load"])
@pytest.mark.parametrize("body", [b"[]", b'"x"', b"5", b"null"])
def test_a_body_that_is_not_an_object_gets_400_not_a_dropped_connection(srv, path, body):
    status, data = _raw_post(srv, path, body)
    assert (status, data["error"]) == (400, "the body must be a JSON object")


def test_odd_bodies_get_an_answer(srv):
    assert _raw_post(srv, "/api/settings", b"[" * 200_000)[0] == 400  # nested too deep for json
    assert _raw_post(srv, "/api/settings", b"{}", {"Content-Length": "-1"})[0] == 400
    assert _raw_post(srv, "/api/settings", b"", {"Content-Length": str(2_000_000)})[0] == 413
    status, data = _raw_post(srv, "/api/setup/done", b"", {"Transfer-Encoding": "chunked"})
    assert status == 411 and "Content-Length" in data["error"]


def test_a_failing_write_answers_500_with_its_reason(srv, monkeypatch):
    from iris import settings

    def boom(values):
        raise PermissionError("config.toml is open in another program")

    monkeypatch.setattr(settings, "save", boom)
    status, data = _raw_post(srv, "/api/settings", b'{"values": {}}')
    assert status == 500 and data["error"] == "PermissionError: config.toml is open in another program"


@pytest.mark.parametrize("value", ["inf", 1e400, "nan"])
def test_settings_refuse_infinite_numbers(value):
    from iris import settings

    clean, errors = settings.validate({"voxcpm_gpu": value, "silence": value})
    assert set(errors) == {"voxcpm_gpu", "silence"}


def test_the_page_with_its_token_is_never_cached(server):
    status, headers, body = get(server + "/")
    assert status == 200 and headers.get("Cache-Control") == "no-store"


def test_the_page_pauses_its_polling_while_hidden():
    page = d._page_template().decode("utf-8")
    assert "if (document.hidden || ticking) return;" in page and '"visibilitychange"' in page
