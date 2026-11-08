"""Branches of iris/dashboard/__init__.py that tests/test_dashboard.py does not reach."""
import json
import sqlite3
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import iris.dashboard as d


def test_run_lms_without_an_executable_says_so(monkeypatch):
    monkeypatch.setattr(d, "_lms_exe", lambda: "")
    with pytest.raises(d.ModelActionError) as exc:
        d.run_lms(["load", "m"])
    assert exc.value.code == 503
    assert exc.value.message == "no lms executable found: set IRIS_LMS_BIN or put lms on PATH"


def test_run_lms_returns_the_code_and_stderr(monkeypatch):
    class Proc:
        returncode = 1
        stderr = "boom\n"
        stdout = ""

    monkeypatch.setattr(d, "_lms_exe", lambda: "/fake/lms")
    calls = []

    def fake_run(cmd, capture_output=True, text=True, timeout=600):
        calls.append((cmd, timeout))
        return Proc()

    monkeypatch.setattr("iris.dashboard.subprocess.run", fake_run)
    code, out = d.run_lms(["load", "m"])
    assert (code, out) == (1, "boom")  # the stderr is stripped and wins over stdout
    assert calls[0][0] == ["/fake/lms", "load", "m"]


def test_model_action_rejects_a_body_that_is_not_an_object():
    with pytest.raises(d.ModelActionError) as exc:
        d.model_action("load", ["not", "a", "dict"])
    assert exc.value.code == 400
    assert exc.value.message == "the body must be a JSON object"


def test_model_action_rejects_a_missing_model_id():
    with pytest.raises(d.ModelActionError) as exc:
        d.model_action("load", {})
    assert exc.value.code == 400
    assert exc.value.message == "missing model id in the request body"


def test_set_iris_model_rejects_a_body_that_is_not_an_object():
    with pytest.raises(d.ModelActionError) as exc:
        d.set_iris_model("qwen3")
    assert exc.value.code == 400
    assert exc.value.message == "the body must be a JSON object"


def test_the_journal_skips_blank_and_torn_lines(tmp_path, monkeypatch):
    j = tmp_path / "journal.jsonl"
    today = time.strftime("%Y-%m-%d")
    good = json.dumps({"t": f"{today}T08:00:00", "event": "request"})
    torn = '{"t": "' + today + 'T09:00:00", "event": "tool"'   # missing the closing brace
    j.write_text(good + "\n\n" + torn, encoding="utf-8")
    monkeypatch.setenv("IRIS_JOURNAL", str(j))
    payload = d.journal_payload()
    assert [e["event"] for e in payload["entries"]] == ["request"]
    assert payload["today"]["requests"] == 1


def test_a_time_that_is_not_iso_gives_none():
    assert d._parse_ts("not a time") is None


def test_a_database_without_the_sessions_table_gives_no_rows(tmp_path):
    db = tmp_path / "sessions.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE other (x INTEGER)")
    con.commit()
    con.close()
    assert d.load_sessions(db) == []


def test_a_pid_that_is_not_a_number_never_counts_as_alive(tmp_path):
    db = tmp_path / "sessions.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE sessions (session_id TEXT, source TEXT, pid TEXT, started_at TEXT,"
        " ended_at TEXT, exit_code INTEGER, status TEXT, interactive INTEGER, provider TEXT,"
        " model TEXT, cwd TEXT, is_subagent INTEGER, parent_session_id TEXT, updated_at TEXT)")
    con.execute("INSERT INTO sessions VALUES ('s1', 'cli', 'not-a-pid', NULL, NULL, NULL, NULL, 0,"
                " NULL, NULL, NULL, 0, NULL, NULL)")
    con.commit()
    con.close()
    rows = d.session_rows(db_path=db, now=1000.0)
    assert len(rows) == 1
    assert rows[0]["live"] is False
    assert rows[0]["status"] == "ended (no exit recorded)"


def test_a_project_folder_whose_glob_fails_is_skipped(tmp_path, monkeypatch):
    base = tmp_path / "projects"
    good = base / "-good-project"
    bad = base / "-bad-project"
    good.mkdir(parents=True)
    bad.mkdir()
    (good / "run.jsonl").write_text("{}", encoding="utf-8")

    real_glob = Path.glob

    def fake_glob(self, pattern):
        if self == bad:
            raise OSError("the folder is unreadable")
        return real_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", fake_glob)
    assert d.claude_projects(base, now=1000.0) == [{"folder": "-good-project", "age_seconds": 0.0}]


def test_results_that_are_not_a_list_give_no_wrong_answers():
    assert d._wrong_answers("nope") == []


def test_an_eval_folder_whose_glob_fails_gives_no_report(tmp_path, monkeypatch):
    base = tmp_path / "results"
    base.mkdir()

    real_glob = Path.glob

    def fake_glob(self, pattern):
        if self == base:
            raise OSError("the folder is unreadable")
        return real_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", fake_glob)
    assert d.eval_payload(str(base)) == {"latest": None, "runs": 0}


def test_a_report_that_is_json_but_not_an_object(tmp_path):
    base = tmp_path / "results"
    base.mkdir()
    (base / "2026-11-04T09-00-00.json").write_text("[1, 2]", encoding="utf-8")
    assert d.eval_payload(str(base)) == {"latest": None, "runs": 1, "error": "unreadable report"}


def test_a_broken_eval_history_gives_the_unreadable_dict(monkeypatch):
    from iris import eval as iris_eval

    def fake_history(results_dir, last=10):
        raise ValueError("a report is broken")

    monkeypatch.setattr(iris_eval, "history", fake_history)
    assert d.eval_history_payload("/nowhere") == {"runs": [], "tasks": {}, "flaky": [], "skipped": 0,
                                                  "error": "unreadable reports"}


def test_an_unreadable_skill_manifest_is_skipped(tmp_path, monkeypatch):
    from iris import skills

    root = tmp_path / "skills"
    (root / "broken").mkdir(parents=True)
    (root / "broken" / "meta.json").write_text("{torn", encoding="utf-8")
    monkeypatch.setattr(skills, "skills_dir", lambda: root)
    assert d.skills_payload() == {"ok": True, "skills": []}


# --- queue and agent actions -----------------------------------------------------


def test_queue_action_rejects_a_body_that_is_not_an_object():
    with pytest.raises(d.ModelActionError) as exc:
        d.queue_action("add", "nope")
    assert exc.value.code == 400
    assert exc.value.message == "the body must be a JSON object"


def test_cline_start_rejects_a_body_that_is_not_an_object():
    with pytest.raises(d.ModelActionError) as exc:
        d.cline_start_action("nope")
    assert exc.value.code == 400
    assert exc.value.message == "the body must be a JSON object"


def test_a_broken_queue_file_comes_back_as_a_500(monkeypatch):
    from iris import prompt_queue

    def fake_add(agent, text):
        raise ValueError("the queue file is broken")

    monkeypatch.setattr(prompt_queue, "add", fake_add)
    with pytest.raises(d.ModelActionError) as exc:
        d.queue_action("add", {"agent": "a", "text": "t"})
    assert exc.value.code == 500
    assert exc.value.message == "the queue file is broken"


# --- draining a request body -----------------------------------------------------


def test_draining_a_body_with_a_non_numeric_content_length_returns_quietly():
    h = object.__new__(d.Handler)
    h.headers = {"Content-Length": "not-a-number"}
    h._drain_body()  # int() raises ValueError; the body is left alone and no error escapes


def test_draining_a_body_that_reads_empty_breaks_the_loop():
    h = object.__new__(d.Handler)
    h.headers = {"Content-Length": "10"}
    h.rfile = types.SimpleNamespace(read=lambda n: b"")
    h._drain_body()  # the first read gives no bytes; the loop breaks instead of spinning


def test_draining_a_body_whose_read_raises_never_escapes():
    def broken_read(n):
        raise OSError("the socket died")

    h = object.__new__(d.Handler)
    h.headers = {"Content-Length": "10"}
    h.rfile = types.SimpleNamespace(read=broken_read)
    h._drain_body()  # the exception is swallowed; a lying Content-Length must never kill the handler


# --- GET routes ------------------------------------------------------------------


@pytest.fixture(scope="module")
def srv():
    s = d.make_server(d.HOST, 0)  # port 0: the OS picks a free one
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


@pytest.fixture
def server(srv):
    return f"http://{d.HOST}:{srv.server_address[1]}"


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def test_the_system_route_serves_the_fake_payload(server, monkeypatch):
    monkeypatch.setattr(d, "system_payload", lambda: {"fake": "system"})
    status, body = get_json(server + "/api/system")
    assert status == 200
    assert body == {"fake": "system"}


def test_the_agents_route_serves_the_fake_payload(server, monkeypatch):
    monkeypatch.setattr(d, "_cache", {})  # a fresh cache so the fake is really called
    monkeypatch.setattr(d, "agents_payload", lambda: {"fake": "agents"})
    status, body = get_json(server + "/api/agents")
    assert status == 200
    assert body == {"fake": "agents"}


def test_the_lmstudio_route_serves_the_fake_payload(server, monkeypatch):
    monkeypatch.setattr(d, "_cache", {})
    monkeypatch.setattr(d, "lmstudio_payload", lambda: {"fake": "lmstudio"})
    status, body = get_json(server + "/api/lmstudio")
    assert status == 200
    assert body == {"fake": "lmstudio"}


def test_the_journal_route_serves_the_fake_payload(server, monkeypatch):
    monkeypatch.setattr(d, "journal_payload", lambda: {"fake": "journal"})
    status, body = get_json(server + "/api/journal")
    assert status == 200
    assert body == {"fake": "journal"}


# --- serve() and default_port ----------------------------------------------------


class _FakeServer:
    """A stand-in for make_server's result whose serve_forever stops with Ctrl+C."""

    def __init__(self):
        self.server_address = ("127.0.0.1", 8765)
        self.closed = 0

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        self.closed += 1


def test_serve_on_a_taken_port_exits_with_how_to_pick_another(monkeypatch):
    def taken(host, port):
        raise OSError("address already in use")

    monkeypatch.setattr(d, "make_server", taken)
    with pytest.raises(SystemExit) as exc:
        d.serve()
    assert str(exc.value).startswith("cannot bind 127.0.0.1:")
    assert "python -m iris dashboard --port 8766" in str(exc.value)


def test_serve_stops_on_ctrl_c_and_closes_the_server_once(monkeypatch, capsys):
    fake = _FakeServer()
    monkeypatch.setattr(d, "make_server", lambda host, port: fake)

    d.serve()  # returns after the KeyboardInterrupt from serve_forever

    assert "Iris dashboard: http://127.0.0.1:8765" in capsys.readouterr().out
    assert fake.closed == 1


def test_default_port_falls_back_to_the_builtin_one_when_the_env_is_not_a_number(monkeypatch):
    monkeypatch.setenv("IRIS_DASHBOARD_PORT", "abc")
    assert d.default_port() == 8765


# --- LM Studio model lists -------------------------------------------------------


def test_a_server_without_the_v0_api_lists_models_without_states(monkeypatch):
    # the server answers but has no v0 API (Ollama, llama.cpp, an older LM Studio)
    # so the plain list is used; it gives ids only and carries no state
    def fake_fetch(url):
        if url == d.V0_MODELS_URL:
            raise urllib.error.HTTPError(url, 404, "nf", None, None)
        return {"data": [{"id": "m1"}]}

    monkeypatch.setattr(d, "_fetch_json", fake_fetch)
    payload = d.lmstudio_payload()
    assert payload["reachable"] is True
    assert payload["states_known"] is False
    assert [m["id"] for m in payload["models"]] == ["m1"]


def test_a_refused_connection_on_v0_is_not_reachable(monkeypatch):
    # nothing answers at all: a refused connection on v0 means the plain list
    # would fail the same way, so it is never asked
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        raise urllib.error.URLError(ConnectionRefusedError())

    monkeypatch.setattr(d, "_fetch_json", fake_fetch)
    assert d.lmstudio_payload() == {"reachable": False}
    assert fetched == [d.V0_MODELS_URL]  # the v1 URL was never fetched


# --- a POST with a lying Content-Length ------------------------------------------


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


def test_a_content_length_that_is_not_a_number_is_refused(srv):
    # int() raises ValueError on "abc"; the handler answers 400 like a negative
    # length instead of dropping the connection
    status, data = _raw_post(srv, "/api/settings", b"{}", {"Content-Length": "abc"})
    assert status == 400
    assert "JSON object" in data["error"]


# --- lmstudio_payload when nothing answers ---------------------------------------


def test_nothing_listening_on_the_port_is_not_reachable_and_fetches_nothing(monkeypatch):
    # the probe says the port is closed, so no HTTP request may be made at all
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return {}

    monkeypatch.setattr(d, "_nothing_listens", lambda url, timeout=0.5: True)
    monkeypatch.setattr(d, "_fetch_json", fake_fetch)
    assert d.lmstudio_payload() == {"reachable": False}
    assert fetched == []  # neither the v0 nor the plain list was ever asked


def test_bad_v0_json_and_a_failing_v1_list_is_not_reachable(monkeypatch):
    # bad JSON on v0 falls through to the plain list; when that fails too, nothing is reachable
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        if url == d.V0_MODELS_URL:
            raise ValueError("bad json")
        raise OSError("the connection died")

    monkeypatch.setattr(d, "_fetch_json", fake_fetch)  # _nothing_listens already says the port is open (conftest)
    assert d.lmstudio_payload() == {"reachable": False}
    assert fetched == [d.V0_MODELS_URL, d.V1_MODELS_URL]  # v0 first, then the fallback

