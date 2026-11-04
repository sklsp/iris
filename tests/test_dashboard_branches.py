"""Branches of iris/dashboard/__init__.py that tests/test_dashboard.py does not reach."""
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
