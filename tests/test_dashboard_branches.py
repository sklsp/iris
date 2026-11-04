"""Branches of iris/dashboard/__init__.py that tests/test_dashboard.py does not reach."""
import json
import sqlite3
import time
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
