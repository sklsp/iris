"""Safety and route tests for the read-only dashboard (no real repos, LLM or journal touched)."""

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import iris.dashboard as d
from iris.dashboard import HOST, agents_payload, journal_payload, lmstudio_payload, make_server, system_payload


@pytest.fixture
def server():
    srv = make_server(HOST, 0)  # port 0: the OS picks a free one, so tests never collide
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://{HOST}:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, dict(r.headers), r.read().decode("utf-8")  # callers parse JSON when they need it


# --- safety --------------------------------------------------------------------

@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@pytest.mark.parametrize("path", ["/", "/api/system", "/api/agents", "/api/lmstudio", "/api/journal", "/api/tools"])
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
    reqs = [e.get("text") for e in data["entries"] if e.get("event") == "request" and str(e.get("text", "")).startswith("r")]
    assert reqs == [f"r{i}" for i in range(59, 14, -1)]
    assert data["today"] == {"requests": 60, "confirmations": 1, "refusals": 1, "errors": 1}


def test_api_journal_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_JOURNAL", str(tmp_path / "nope.jsonl"))
    data = journal_payload()
    assert data["entries"] == [] and data["today"]["requests"] == 0


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


def test_api_lmstudio_unreachable_reports_false(monkeypatch):
    monkeypatch.setattr(d, "V0_MODELS_URL", "http://127.0.0.1:9/api/v0/models")  # port 9: nothing listens
    monkeypatch.setattr(d, "V1_MODELS_URL", "http://127.0.0.1:9/v1/models")
    assert lmstudio_payload() == {"reachable": False}


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
