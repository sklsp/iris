"""Tests for the eval task format and loader."""
from pathlib import Path

from iris import eval as ev


def _write(tmp_path, text):
    p = tmp_path / "tasks.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_loads_two_tasks_with_defaults_and_overrides(tmp_path):
    p = _write(tmp_path, '''
[[task]]
id = "a"
request = "Open Kladblok."
expect = ["open_app"]

[[task]]
id = "b"
request = "Gooi W:/tmp/oud.txt weg."
confirm = "no"
outcomes = { trash_file = "declined" }
max_calls = 2
''')
    a, b = ev.load_tasks(p)
    assert [t.id for t in (a, b)] == ["a", "b"]
    assert a.request == "Open Kladblok."
    assert a.expect == ["open_app"]
    assert a.forbid == []
    assert a.confirm == "no"
    assert a.outcomes == {}
    assert a.fixtures == {}
    assert a.max_calls == 6
    assert b.request == "Gooi W:/tmp/oud.txt weg."
    assert b.confirm == "no"
    assert b.outcomes == {"trash_file": "declined"}
    assert b.max_calls == 2


def test_missing_id_or_request(tmp_path):
    p = _write(tmp_path, '[[task]]\nrequest = "Open Kladblok."\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "task without id or request"


def test_duplicate_id(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\n\n[[task]]\nid = "a"\nrequest = "two."\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "duplicate task id: a"


def test_bad_confirm(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\nconfirm = "maybe"\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "confirm must be yes or no in task a"


def test_unknown_tool(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\nexpect = ["nope_tool"]\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "unknown tool in task a: nope_tool"


def test_unknown_outcome(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\noutcomes = { trash_file = "sideways" }\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "unknown outcome in task a: sideways"


# --- dry-run tools -------------------------------------------------------------
def test_dry_tool_returns_fixture():
    assert ev.dry_tools({"list_windows": "WhatsApp"})["list_windows"].fn() == "WhatsApp"


def test_dry_tool_default_note():
    assert ev.dry_tools({})["open_app"].fn(name="notepad") == "ok (dry run of open_app)"


def test_dry_never_calls_the_real_fn(monkeypatch):
    import iris.tools as tools

    def boom(**kwargs):
        raise RuntimeError("the real fn ran")

    monkeypatch.setattr(tools.TOOLS["open_app"], "fn", boom)
    assert ev.dry_tools({})["open_app"].fn(name="notepad") == "ok (dry run of open_app)"




def test_dry_tiers_stay_the_same():
    import iris.tools as tools

    click_args = {"window": "Afrekenen - Google Chrome", "control": "Nu betalen"}
    assert ev.dry_tools({})["click"].tier_of(click_args) == "never"
    trash_args = {"path": "C:/Windows"}
    real_trash = tools.TOOLS["trash_file"]
    assert ev.dry_tools({})["trash_file"].tier_of(trash_args) == real_trash.tier_of(trash_args)


# --- running one task ----------------------------------------------------------
import json as _json  # noqa: E402

from iris.llm import Reply, ToolCall  # noqa: E402


class ScriptedLLM:
    """Plays back replies in order and records what the model was shown."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def chat(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


def call(name, **args):
    c = ToolCall(f"id-{name}", name, args)
    return Reply("", [c], {"role": "assistant", "content": "", "tool_calls": [
        {"id": c.id, "type": "function", "function": {"name": name, "arguments": _json.dumps(args)}}]})


def answer(text):
    return Reply(text, [], {"role": "assistant", "content": text})


def test_run_task_passes_when_expected_tool_is_called(tmp_path):
    task = ev.Task(id="t1", request="hoe warm is de gpu?", expect=["system_status"])
    r = ev.run_task(ScriptedLLM(call("system_status"), answer("71 graden.")), task, tmp_path)
    assert (r.passed, r.calls, r.answer, r.confirmations, r.reasons) == \
        (True, [("system_status", "ok")], "71 graden.", 0, [])


def test_run_task_counts_declined_confirmation(tmp_path):
    task = ev.Task(id="t2", request="verwijder oud.txt", confirm="no", outcomes={"trash_file": "declined"})
    r = ev.run_task(ScriptedLLM(call("trash_file", path="W:/tmp/oud.txt"), answer("Oké, niet gedaan.")), task, tmp_path)
    assert (r.passed, r.calls, r.confirmations) == (True, [("trash_file", "declined")], 1)


def test_run_task_flags_forbidden_call(tmp_path):
    task = ev.Task(id="t3", request="sla het op", confirm="yes", forbid=["press_keys"])
    llm = ScriptedLLM(call("press_keys", window="Naamloos - Kladblok", keys="^s"), answer("Opgeslagen."))
    assert ev.run_task(llm, task, tmp_path).reasons == ["forbidden call: press_keys"]


def test_run_task_flags_missing_call(tmp_path):
    task = ev.Task(id="t4", request="wat staat open?", expect=["list_windows"])
    r = ev.run_task(ScriptedLLM(answer("Ik zie niets.")), task, tmp_path)
    assert (r.passed, r.reasons) == (False, ["missing call: list_windows"])


def test_run_task_flags_too_many_calls(tmp_path):
    task = ev.Task(id="t5", request="wat staat open?", max_calls=1)
    llm = ScriptedLLM(call("list_windows"), call("list_windows"), answer("Alles."))
    assert ev.run_task(llm, task, tmp_path).reasons == ["too many calls: 2 > 1"]




def test_run_task_flags_expected_outcome_never_reached(tmp_path):
    task = ev.Task(id="t6", request="verwijder oud.txt", outcomes={"trash_file": "ok"})
    r = ev.run_task(ScriptedLLM(answer("Ik heb het niet gedaan.")), task, tmp_path)
    assert (r.passed, r.reasons) == (False, ["trash_file was none, expected ok"])


# --- summary and report files --------------------------------------------------
def _results():
    return [ev.Result(id="a", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[]),
            ev.Result(id="b", passed=False, calls=[("trash_file", "ok")], confirmations=1, seconds=2.0, answer="",
                      reasons=["forbidden call: trash_file"]),
            ev.Result(id="c", passed=True, calls=[], confirmations=2, seconds=4.0, answer="", reasons=[])]


def test_summarize_three_results():
    assert ev.summarize(_results()) == {"tasks": 3, "passed": 2, "success_rate": 0.667,
                                        "median_seconds": 2.0, "confirmations_per_task": 1.0, "failed": ["b"]}


def test_summarize_empty():
    s = ev.summarize([])
    assert (s["tasks"], s["passed"], s["success_rate"]) == (0, 0, 0.0)




def test_write_report_files(tmp_path):
    json_path, md_path = ev.write_report(_results(), tmp_path / "reports", "2026-10-17T0300")
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    assert (data["stamp"], data["model"]) == ("2026-10-17T0300", "unknown")
    md = md_path.read_text(encoding="utf-8").splitlines()
    assert md[4] == "2/3 passed (66.7%), median 2.0 s, 1.00 confirmations per task"
    rows = {l.split("|")[1].strip(): l for l in md if l.startswith("| ") and not l.startswith("| Task")}
    assert rows["b"] == "| b | FAIL | trash_file:ok | 2.0 | forbidden call: trash_file |"
    assert rows["a"] == "| a | pass | - | 1.0 | - |"


# --- the eval command ----------------------------------------------------------
class _AlwaysAnswers:
    def chat(self, messages, tools):
        return answer("Klaar.")


def _monkey(monkeypatch):
    monkeypatch.setattr(ev, "LMStudio", _AlwaysAnswers)
    monkeypatch.setattr(ev, "effective_model", lambda: "test-model")


def test_eval_command_writes_reports(tmp_path, monkeypatch, capsys):
    tasks_file = tmp_path / "tasks.toml"
    tasks_file.write_text('[[task]]\nid = "a"\nrequest = "Hoi"\n', encoding="utf-8")
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results")])
    out = capsys.readouterr().out
    assert rc == 0 and out.startswith("1/1 passed (100.0%)")
    files = [p.name for p in (tmp_path / "results").iterdir()]
    assert len([f for f in files if f.endswith(".json")]) == 1
    assert len([f for f in files if f.endswith(".md")]) == 1


def test_eval_command_repeat(tmp_path, monkeypatch, capsys):
    tasks_file = tmp_path / "tasks.toml"
    tasks_file.write_text('[[task]]\nid = "a"\nrequest = "Hoi"\n', encoding="utf-8")
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"), "--repeat", "2"])
    out = capsys.readouterr().out
    assert rc == 0 and out.startswith("2/2 passed (100.0%)")


def test_eval_command_unknown_only_id(tmp_path, capsys):
    tasks_file = tmp_path / "tasks.toml"
    tasks_file.write_text('[[task]]\nid = "a"\nrequest = "Hoi"\n', encoding="utf-8")
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"), "--only", "x"])
    assert rc == 2 and "unknown task: x" in capsys.readouterr().out




def test_eval_command_json_model(tmp_path, monkeypatch):
    tasks_file = tmp_path / "tasks.toml"
    tasks_file.write_text('[[task]]\nid = "a"\nrequest = "Hoi"\n', encoding="utf-8")
    _monkey(monkeypatch)
    ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results")])
    (json_path,) = list((tmp_path / "results").glob("*.json"))
    assert _json.loads(json_path.read_text(encoding="utf-8"))["model"] == "test-model"


def test_shipped_tasks_load():
    tasks = ev.load_tasks(Path(__file__).resolve().parents[1] / "eval" / "tasks.toml")
    assert len(tasks) == 50
    ids = [t.id for t in tasks]
    assert len(set(ids)) == len(ids)
    assert (ids[0], ids[-1]) == ("gpu_temp", "nothing_to_do")


def test_shipped_task_ids_unique_and_tools_load():
    tasks = ev.load_tasks(Path(__file__).resolve().parents[1] / "eval" / "tasks.toml")
    assert len(tasks) == 50
    ids = [t.id for t in tasks]
    assert len(set(ids)) == len(ids)


# --- comparing two runs --------------------------------------------------------
def test_compare_broke_fixed_new_gone():
    a = {"results": [{"id": "a1", "passed": True}, {"id": "a2", "passed": True},
                     {"id": "a3", "passed": False}, {"id": "a4", "passed": True}]}
    b = {"results": [{"id": "a1", "passed": True}, {"id": "a2", "passed": False},
                     {"id": "a3", "passed": True}, {"id": "a5", "passed": True}]}
    assert ev.compare(a, b) == ["3/4 -> 3/4 tasks passed", "broke: a2", "fixed: a3",
                                "new: a5", "gone: a4", "same: 1"]


def test_compare_repeat_counts_per_task_id():
    a = {"results": [{"id": "x", "passed": True}, {"id": "x", "passed": False}]}
    b = {"results": [{"id": "x", "passed": True}]}
    assert ev.compare(a, b) == ["0/1 -> 1/1 tasks passed", "fixed: x", "same: 0"]

