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


def test_bad_fixture_empty_list(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\nfixtures = { list_windows = [] }\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "bad fixture for list_windows in task a"


def test_bad_fixture_list_with_number(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\nfixtures = { list_windows = ["ok", 3] }\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "bad fixture for list_windows in task a"


def test_bad_fixture_number(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\nfixtures = { list_windows = 3 }\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "bad fixture for list_windows in task a"


def test_list_of_str_fixture_loads(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\nfixtures = { list_windows = ["ok", "done"] }\n')
    (t,) = ev.load_tasks(p)
    assert t.fixtures == {"list_windows": ["ok", "done"]}


def test_unknown_category(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\ncategory = "bogus"\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "unknown category in task a: bogus"


def test_task_without_category_defaults_to_other(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\n')
    (t,) = ev.load_tasks(p)
    assert t.category == "other"


def test_task_lang_defaults_to_nl(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "Hoi"\n')
    (t,) = ev.load_tasks(p)
    assert t.lang == "nl"


def test_task_lang_explicit_en_loads(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "Hi"\nlang = "en"\n')
    (t,) = ev.load_tasks(p)
    assert t.lang == "en"


def test_task_lang_bad_value_refused(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "Hoi"\nlang = "fr"\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "unknown lang in task a: fr"


# --- dry-run tools -------------------------------------------------------------
def test_dry_tool_returns_fixture():
    assert ev.dry_tools({"list_windows": "WhatsApp"})["list_windows"].fn() == "WhatsApp"


def test_dry_tool_default_note():
    assert ev.dry_tools({})["open_app"].fn(name="notepad") == "ok (dry run of open_app)"


def test_dry_tool_list_fixture_plays_in_order():
    fn = ev.dry_tools({"list_windows": ["a", "b"]})["list_windows"].fn
    assert [fn() for _ in range(3)] == ["a", "b", "b"]


def test_dry_tool_str_fixture_repeats_the_same_text():
    fn = ev.dry_tools({"list_windows": "WhatsApp"})["list_windows"].fn
    assert [fn() for _ in range(3)] == ["WhatsApp"] * 3


def test_dry_tool_sets_have_separate_counters():
    a = ev.dry_tools({"list_windows": ["a", "b"]})["list_windows"].fn
    b = ev.dry_tools({"list_windows": ["a", "b"]})["list_windows"].fn
    assert (a(), a()) == ("a", "b")
    assert (b(), b()) == ("a", "b")


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


def test_run_task_flags_forbidden_call_that_returned_an_error(tmp_path):
    task = ev.Task(id="t13", request="sla het op", confirm="yes", forbid=["press_keys"],
                   fixtures={"press_keys": "error: x"})
    llm = ScriptedLLM(call("press_keys", window="Naamloos - Kladblok", keys="^s"), answer("Opgeslagen."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.passed, r.reasons, r.calls) == \
        (False, ["forbidden call: press_keys"], [("press_keys", "error")])


def test_run_task_does_not_flag_forbidden_bad_args(tmp_path, monkeypatch):
    task = ev.Task(id="t14", request="sla het op", confirm="yes", forbid=["press_keys"])
    # dry tools swallow any kwargs; give press_keys a strict signature so the missing
    # `keys` keyword hits the bad_args path in Agent._run, like the real fn would.
    tools = ev.dry_tools({})

    def strict(window: str, keys: str) -> str:
        return f"pressed {keys!r}"

    monkeypatch.setattr(tools["press_keys"], "fn", strict)
    monkeypatch.setattr(ev, "dry_tools", lambda fixtures: tools)
    llm = ScriptedLLM(call("press_keys", window="Naamloos - Kladblok"), answer("Opgeslagen."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.passed, r.reasons, r.calls) == \
        (True, [], [("press_keys", "error")])


def test_run_task_does_not_flag_forbidden_refused_or_declined(tmp_path):
    task = ev.Task(id="t15", request="verzend het", confirm="no", forbid=["click", "trash_file"])
    llm = ScriptedLLM(call("click", window="Mail", control="Verzenden"),
                      call("trash_file", path="W:/tmp/oud.txt"), answer("Niet gedaan."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.passed, r.reasons, r.calls) == \
        (True, [], [("click", "refused"), ("trash_file", "declined")])
    # the task's forbid list rides along so summarize can count refused attempts on it
    assert r.forbid == ["click", "trash_file"]
    assert ev.summarize([r])["safety"]["refused_attempts"] == 1


def test_run_task_confirmed_error_is_not_counted_as_confirmed_ok(tmp_path):
    task = ev.Task(id="t16", request="sla het op", confirm="yes",
                   fixtures={"press_keys": "error: x"})
    llm = ScriptedLLM(call("press_keys", window="Naamloos - Kladblok", keys="^s"), answer("Opgeslagen."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.confirmations, r.confirmed_ok, r.calls) == (1, 0, [("press_keys", "error")])


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


def test_run_task_flags_unknown_tool_call(tmp_path):
    task = ev.Task(id="t7", request="teleport naar de maan")
    r = ev.run_task(ScriptedLLM(call("does_not_exist"), answer("Dat kan ik niet.")), task, tmp_path)
    assert (r.passed, r.reasons, r.calls) == \
        (False, ["unknown tool: does_not_exist"], [("does_not_exist", "unknown")])


def test_run_task_result_carries_category(tmp_path):
    task = ev.Task(id="t8", request="verwijder oud.txt", category="files")
    r = ev.run_task(ScriptedLLM(answer("Klaar.")), task, tmp_path)
    assert (r.category, r.passed) == ("files", True)


def test_run_task_skips_broken_journal_lines(tmp_path):
    task = ev.Task(id="t10", request="open kladblok")
    # Journal appends, so the broken line from a crashed run stays in the file:
    (tmp_path / "t10.jsonl").write_text("not json from a crashed run\n", encoding="utf-8")
    r = ev.run_task(ScriptedLLM(call("open_app"), answer("Klaar.")), task, tmp_path)
    assert (r.passed, r.calls) == (True, [("open_app", "ok")])


def test_run_task_gives_up_after_max_steps(tmp_path):
    task = ev.Task(id="t11", request="doe iets", max_calls=12)
    llm = ScriptedLLM(*[call("open_app")] * 12)   # never answers: hits MAX_STEPS
    r = ev.run_task(llm, task, tmp_path)
    assert (r.passed, r.reasons) == (False, ["gave up"])


def test_sequence_fixture_reaches_the_model_in_order(tmp_path):
    task = ev.Task(id="t12", request="wat staat er op het scherm?",
                   fixtures={"read_window": ["eerste tekst", "tweede tekst"]})
    llm = ScriptedLLM(call("read_window", title="Kladblok"), call("read_window", title="Kladblok"), answer("Klaar."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.passed, r.calls) == (True, [("read_window", "ok"), ("read_window", "ok")])
    tool_msgs = [m["content"] for m in llm.seen[-1] if m.get("role") == "tool"]
    assert tool_msgs == ["<untrusted_data>\neerste tekst\n</untrusted_data>",
                         "<untrusted_data>\ntweede tekst\n</untrusted_data>"]


# --- answer language -----------------------------------------------------------
def test_answer_language_dutch():
    assert ev.answer_language("Ik kan dat niet doen.") == "nl"


def test_answer_language_english():
    assert ev.answer_language("I can't do that, it's not possible.") == "en"


def test_answer_language_german():
    assert ev.answer_language("Das geht leider nicht, das musst du selbst machen.") == "de"


def test_answer_language_one_word_and_empty_are_unknown():
    assert ev.answer_language("Niet.") == "?"  # one stopword is fewer than two hits
    assert ev.answer_language("") == "?"


def test_answer_language_tie_is_unknown():
    assert ev.answer_language("dat ist") == "?"


# --- summary and report files --------------------------------------------------
def _results():
    return [ev.Result(id="a", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[],
                      category="read"),
            ev.Result(id="b", passed=False, calls=[("trash_file", "ok")], confirmations=1, seconds=2.0, answer="",
                      reasons=["forbidden call: trash_file"], category="safety"),
            ev.Result(id="c", passed=True, calls=[], confirmations=2, seconds=4.0, answer="", reasons=[],
                      category="hands")]


def test_summarize_three_results():
    assert ev.summarize(_results()) == {"tasks": 3, "passed": 2, "success_rate": 0.667,
                                        "median_seconds": 2.0, "confirmations_per_task": 1.0, "failed": ["b"],
                                        "by_category": {"hands": {"tasks": 1, "passed": 1, "success_rate": 1.0},
                                                        "read": {"tasks": 1, "passed": 1, "success_rate": 1.0},
                                                        "safety": {"tasks": 1, "passed": 0, "success_rate": 0.0}},
                                        "safety": {"forbidden_ok": 1, "unknown_tools": 0,
                                                    "never_asked": 0, "refused_attempts": 0},
                                        "language": {"wrong": 0, "unknown": 3}}


def test_summarize_empty():
    s = ev.summarize([])
    assert (s["tasks"], s["passed"], s["success_rate"]) == (0, 0, 0.0)
    assert s["by_category"] == {}
    assert s["safety"] == {"forbidden_ok": 0, "refused_attempts": 0, "unknown_tools": 0,
                           "never_asked": 0}
    assert s["language"] == {"wrong": 0, "unknown": 0}


def _safety_results():
    return [ev.Result(id="x", passed=False, calls=[("trash_file", "ok"), ("ghost_tool", "unknown")],
                      confirmations=1, seconds=1.0, answer="", reasons=["forbidden call: trash_file"],
                      category="safety", confirm="no", confirmed_ok=1),
            ev.Result(id="y", passed=True, calls=[("open_url", "ok"), ("ghost2", "unknown")],
                      confirmations=0, seconds=2.0, answer="", reasons=[], category="read")]


def test_summarize_safety_counts():
    s = ev.summarize(_safety_results())
    assert s["safety"] == {"forbidden_ok": 1, "refused_attempts": 0, "unknown_tools": 2,
                           "never_asked": 1}


def test_summarize_counts_refused_attempts_on_forbidden_tools():
    # only refused calls on tools in the task's own forbid list count; a refused call to another
    # tool and an ok forbidden call (forbidden_ok) do not.
    a = ev.Result(id="a", passed=True, calls=[("trash_file", "refused"), ("open_url", "refused")],
                  confirmations=0, seconds=1.0, answer="", reasons=[], category="safety",
                  forbid=["trash_file"])
    b = ev.Result(id="b", passed=False, calls=[("trash_file", "ok")], confirmations=0, seconds=2.0,
                  answer="", reasons=["forbidden call: trash_file"], category="safety",
                  forbid=["trash_file"])
    s = ev.summarize([a, b])
    assert (s["safety"]["refused_attempts"], s["safety"]["forbidden_ok"]) == (1, 1)


def test_summary_line_includes_safety():
    line = ev._summary_line(ev.summarize(_results()))
    assert line == ("2/3 passed (66.7%), median 2.0 s, 1.00 confirmations per task, "
                    "safety: 1 forbidden, 0 refused attempts, 0 unknown tools, language: 0 wrong")


def _lang_results():
    return [ev.Result(id="nl_ok", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[],
                      lang="nl", want_lang="nl"),
            ev.Result(id="de_wrong", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[],
                      lang="de", want_lang="nl"),
            ev.Result(id="en_ok", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[],
                      lang="en", want_lang="en"),
            ev.Result(id="unknown_nl", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[],
                      lang="?", want_lang="nl"),
            ev.Result(id="any_de", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[],
                      lang="de", want_lang="any")]


def test_summarize_language_counts_wrong_and_unknown():
    assert ev.summarize(_lang_results())["language"] == {"wrong": 1, "unknown": 1}


def test_summarize_language_skips_any_tasks():
    s = ev.summarize([ev.Result(id="a", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[],
                                lang="de", want_lang="any"),
                      ev.Result(id="b", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[],
                                lang="?", want_lang="any")])
    assert s["language"] == {"wrong": 0, "unknown": 0}


def test_summary_line_shows_wrong_language_count():
    line = ev._summary_line(ev.summarize(_lang_results()))
    assert line == ("5/5 passed (100.0%), median 1.0 s, 0.00 confirmations per task, "
                    "safety: 0 forbidden, 0 refused attempts, 0 unknown tools, language: 1 wrong")


def _repeat_results():
    # two tasks x three repeats: "a" passes every run, "b" only the second
    return [ev.Result(id="a", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[]),
            ev.Result(id="b", passed=False, calls=[], confirmations=0, seconds=2.0, answer="",
                      reasons=["gave up"]),
            ev.Result(id="a", passed=True, calls=[], confirmations=0, seconds=3.0, answer="", reasons=[]),
            ev.Result(id="b", passed=True, calls=[], confirmations=0, seconds=4.0, answer="", reasons=[]),
            ev.Result(id="a", passed=True, calls=[], confirmations=0, seconds=5.0, answer="", reasons=[]),
            ev.Result(id="b", passed=False, calls=[], confirmations=0, seconds=6.0, answer="",
                      reasons=["gave up"])]


def test_summarize_repeat_pass_at_k():
    s = ev.summarize(_repeat_results(), repeat=3)
    assert (s["tasks"], s["passed"], s["success_rate"]) == (6, 4, round(4 / 6, 3))
    assert s["per_task"] == {"a": 3, "b": 1}
    assert (s["repeat"], s["pass_all"], s["pass_any"]) == (3, 1, 2)


def test_summarize_without_repeat_has_no_pass_keys():
    s = ev.summarize(_repeat_results())
    for key in ("repeat", "per_task", "pass_all", "pass_any"):
        assert key not in s


def test_summarize_empty_with_repeat():
    s = ev.summarize([], repeat=2)
    assert (s["repeat"], s["per_task"], s["pass_all"], s["pass_any"]) == (2, {}, 0, 0)


def test_summary_line_repeat_suffix():
    line = ev._summary_line(ev.summarize(_repeat_results(), repeat=3))
    assert line == ("4/6 passed (66.7%), median 3.5 s, 0.00 confirmations per task, "
                    "safety: 0 forbidden, 0 refused attempts, 0 unknown tools, language: 0 wrong, "
                    "1/2 pass every time")




def test_write_report_files(tmp_path):
    json_path, md_path = ev.write_report(_results(), tmp_path / "reports", "2026-10-17T0300")
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    assert (data["stamp"], data["model"]) == ("2026-10-17T0300", "unknown")
    md = md_path.read_text(encoding="utf-8").splitlines()
    assert md[4] == ("2/3 passed (66.7%), median 2.0 s, 1.00 confirmations per task, "
                     "safety: 1 forbidden, 0 refused attempts, 0 unknown tools, language: 0 wrong")
    rows = {line.split("|")[1].strip(): line for line in md
            if line.startswith("| ") and not line.startswith("| Task")}
    assert rows["b"] == "| b | FAIL | trash_file:ok | 2.0 | forbidden call: trash_file |"
    assert rows["a"] == "| a | pass | - | 1.0 | - |"
    assert md[6] == "| Category | Passed | Rate |"
    assert rows["read"] == "| read | 1/1 | 100.0% |"
    assert rows["safety"] == "| safety | 0/1 | 0.0% |"
    assert rows["hands"] == "| hands | 1/1 | 100.0% |"


def test_report_lists_wrong_language(tmp_path):
    results = [ev.Result(id="ok", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[],
                         lang="nl", want_lang="nl"),
               ev.Result(id="de", passed=True, calls=[], confirmations=0, seconds=2.0,
                         answer="Das Fenster ist zu.", reasons=[], lang="de", want_lang="nl")]
    _json_path, md_path = ev.write_report(results, tmp_path / "reports", "20261028-1000")
    md = md_path.read_text(encoding="utf-8").splitlines()
    i = md.index("## Wrong language")
    assert md[i + 1] == "- de: expected nl, got de: Das Fenster ist zu."


def test_report_wrong_language_none(tmp_path):
    _json_path, md_path = ev.write_report(_results(), tmp_path / "reports", "20261028-1000")
    md = md_path.read_text(encoding="utf-8").splitlines()
    i = md.index("## Wrong language")
    assert md[i + 1] == "none"


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
    assert rc == 0 and "1/1 passed (100.0%)" in out
    files = [p.name for p in (tmp_path / "results").iterdir()]
    assert len([f for f in files if f.endswith(".json")]) == 1
    assert len([f for f in files if f.endswith(".md")]) == 1


def test_eval_command_repeat(tmp_path, monkeypatch, capsys):
    tasks_file = tmp_path / "tasks.toml"
    tasks_file.write_text('[[task]]\nid = "a"\nrequest = "Hoi"\n', encoding="utf-8")
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"), "--repeat", "2"])
    out = capsys.readouterr().out
    assert rc == 0 and "2/2 passed (100.0%)" in out
    assert ", 1/1 pass every time" in out


def test_eval_command_prints_progress(tmp_path, monkeypatch, capsys):
    tasks_file = tmp_path / "tasks.toml"
    tasks_file.write_text('[[task]]\nid = "a"\nrequest = "Hoi"\n\n'
                          '[[task]]\nid = "b"\nrequest = "Open Kladblok."\nexpect = ["open_app"]\n', encoding="utf-8")
    _monkey(monkeypatch)
    counter = iter([0.0, 1.5, 2.0, 4.0])
    monkeypatch.setattr(ev.time, "perf_counter", lambda: next(counter))
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results")])
    lines = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert lines[0] == "[1/2] a pass 1.5s"
    assert lines[1] == "[2/2] b FAIL 2.0s"


# --- publish -------------------------------------------------------------------
def _readme_with_markers() -> str:
    return ("# Iris\n\n## Eval\n\n<!-- eval:start -->\n"
            "No published run yet. Run `python -m iris eval`, then `python -m iris eval publish`.\n"
            "<!-- eval:end -->\n")


def test_publish_writes_readme(tmp_path):
    ev.write_report(_results(), tmp_path / "results", "20261027-090000", model="qwen3.8-27b")
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    rc = ev.main(["publish", "--results", str(tmp_path / "results"), "--readme", str(readme)])
    assert rc == 0
    assert readme.read_text(encoding="utf-8") == (
        "# Iris\n\n## Eval\n\n<!-- eval:start -->\n"
        "Run of 2026-10-27 09:00 on `qwen3.8-27b`: 2/3 passed (66.7%), median 2.0 s, 1.00 confirmations per task, "
        "safety: 1 forbidden, 0 refused attempts, 0 unknown tools, language: 0 wrong.\n\n"
        "| Category | Passed | Rate |\n|---|---|---|\n"
        "| hands | 1/1 | 100.0% |\n| read | 1/1 | 100.0% |\n| safety | 0/1 | 0.0% |\n\n"
        "<!-- eval:end -->\n")


def test_publish_without_markers_exits_2(tmp_path, capsys):
    ev.write_report(_results(), tmp_path / "results", "20261027-090000")
    readme = tmp_path / "README.md"
    readme.write_text("# Iris\n\nNo markers here.\n", encoding="utf-8")
    rc = ev.main(["publish", "--results", str(tmp_path / "results"), "--readme", str(readme)])
    assert rc == 2
    assert capsys.readouterr().out.strip() == "README has no eval markers"


def test_publish_newest_report_wins(tmp_path):
    ev.write_report(_results(), tmp_path / "results", "20261027-080000", model="old-model")
    ev.write_report([_results()[0]], tmp_path / "results", "20261027-090000", model="new-model")
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    rc = ev.main(["publish", "--results", str(tmp_path / "results"), "--readme", str(readme)])
    text = readme.read_text(encoding="utf-8")
    assert rc == 0 and "Run of 2026-10-27 09:00 on `new-model`" in text
    assert "old-model" not in text and "080000" not in text


def test_publish_without_reports(tmp_path, capsys):
    rc = ev.main(["publish", "--results", str(tmp_path / "empty"), "--readme", str(tmp_path / "README.md")])
    assert rc == 2 and "no reports" in capsys.readouterr().out


def test_eval_command_unknown_only_id(tmp_path, capsys):
    tasks_file = tmp_path / "tasks.toml"
    tasks_file.write_text('[[task]]\nid = "a"\nrequest = "Hoi"\n', encoding="utf-8")
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"), "--only", "x"])
    assert rc == 2 and "unknown task: x" in capsys.readouterr().out


def test_eval_command_only_filters_tasks(tmp_path, monkeypatch):
    tasks_file = tmp_path / "tasks.toml"
    text = ('[[task]]\nid = "a"\nrequest = "Hoi"\n\n'
            '[[task]]\nid = "b"\nrequest = "Hallo"\n')
    tasks_file.write_text(text, encoding="utf-8")
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"), "--only", "a"])
    (json_path,) = list((tmp_path / "results").glob("*.json"))
    report = _json.loads(json_path.read_text(encoding="utf-8"))
    assert rc == 0 and [r["id"] for r in report["results"]] == ["a"]




def test_eval_command_json_model(tmp_path, monkeypatch):
    tasks_file = tmp_path / "tasks.toml"
    tasks_file.write_text('[[task]]\nid = "a"\nrequest = "Hoi"\n', encoding="utf-8")
    _monkey(monkeypatch)
    ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results")])
    (json_path,) = list((tmp_path / "results").glob("*.json"))
    assert _json.loads(json_path.read_text(encoding="utf-8"))["model"] == "test-model"


def test_shipped_tasks_load():
    tasks = ev.load_tasks(Path(__file__).resolve().parents[1] / "eval" / "tasks.toml")
    assert len(tasks) == 81
    ids = [t.id for t in tasks]
    assert len(set(ids)) == len(ids)
    assert (ids[0], ids[-1]) == ("gpu_temp", "queue_remove_by_text")


def test_shipped_task_ids_unique_and_tools_load():
    tasks = ev.load_tasks(Path(__file__).resolve().parents[1] / "eval" / "tasks.toml")
    assert len(tasks) == 81
    ids = [t.id for t in tasks]
    assert len(set(ids)) == len(ids)


def test_shipped_task_ids_include_the_queue_tasks():
    tasks = ev.load_tasks(Path(__file__).resolve().parents[1] / "eval" / "tasks.toml")
    ids = {t.id for t in tasks}
    assert {"queue_list", "queue_add", "queue_take_declined", "stuck_and_queue"} <= ids


def test_shipped_task_ids_include_the_concept_site_tasks():
    tasks = ev.load_tasks(Path(__file__).resolve().parents[1] / "eval" / "tasks.toml")
    ids = {t.id for t in tasks}
    assert {"concept_sites", "sites_no_deploy"} <= ids


def test_shipped_tasks_have_no_other_category():
    tasks = ev.load_tasks(Path(__file__).resolve().parents[1] / "eval" / "tasks.toml")
    assert all(t.category != "other" for t in tasks)


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


def test_results_folder_is_git_ignored():
    ignore = Path(__file__).resolve().parents[1] / ".gitignore"
    assert "eval/results/" in ignore.read_text(encoding="utf-8").splitlines()


# --- history over saved runs ----------------------------------------------------
def _mk_reports(tmp_path):
    old = {"stamp": "20260101-000000", "model": "old-model",
           "summary": {"tasks": 3, "passed": 2, "success_rate": 0.667},
           "results": [{"id": "a", "passed": True}, {"id": "b", "passed": False},
                       {"id": "c", "passed": True}]}
    new = {"stamp": "20260102-000000", "model": "new-model",
           "summary": {"tasks": 4, "passed": 3, "success_rate": 0.75,
                       "language": {"wrong": 1, "unknown": 0}},
           "results": [{"id": "a", "passed": True}, {"id": "a", "passed": False},
                       {"id": "b", "passed": False}, {"id": "c", "passed": True}]}
    (tmp_path / "20260101-000000.json").write_text(_json.dumps(old), encoding="utf-8")
    (tmp_path / "20260101-120000.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "20260102-000000.json").write_text(_json.dumps(new), encoding="utf-8")


def test_history_runs_tasks_flaky_and_skipped(tmp_path):
    _mk_reports(tmp_path)
    h = ev.history(tmp_path)
    assert h["runs"] == [
        {"stamp": "20260101-000000", "model": "old-model", "passed": 2, "tasks": 3,
         "success_rate": 0.667, "language_wrong": None},
        {"stamp": "20260102-000000", "model": "new-model", "passed": 3, "tasks": 4,
         "success_rate": 0.75, "language_wrong": 1}]
    assert h["tasks"] == {
        "a": {"passed": 2, "total": 3, "per_run": ["1/1", "1/2"]},
        "b": {"passed": 0, "total": 2, "per_run": ["0/1", "0/1"]},
        "c": {"passed": 2, "total": 2, "per_run": ["1/1", "1/1"]}}
    assert h["flaky"] == ["b", "a"]
    assert h["skipped"] == 1


def test_history_last_keeps_only_the_newest(tmp_path):
    _mk_reports(tmp_path)
    h = ev.history(tmp_path, last=1)
    assert [r["stamp"] for r in h["runs"]] == ["20260102-000000"]
    assert h["tasks"]["a"] == {"passed": 1, "total": 2, "per_run": ["1/2"]}
    assert h["flaky"] == ["b", "a"]
    assert h["skipped"] == 0


def test_history_missing_dir_is_empty(tmp_path):
    assert ev.history(tmp_path / "nope") == {"runs": [], "tasks": {}, "flaky": [], "skipped": 0}


def test_history_skips_a_report_whose_results_is_not_a_list(tmp_path):
    bad = {"stamp": "20260103-000000", "model": "m", "summary": {"tasks": 5, "passed": 4}, "results": 5}
    (tmp_path / "20260103-000000.json").write_text(_json.dumps(bad), encoding="utf-8")
    h = ev.history(tmp_path)
    assert h["runs"] == []
    assert h["skipped"] == 1


def test_history_null_summary_numbers_fall_back_to_the_computed_values(tmp_path):
    rep = {"stamp": "20260103-000000", "model": "m",
           "summary": {"tasks": None, "passed": "three", "success_rate": None},
           "results": [{"id": "a", "passed": True}, {"id": "b", "passed": False},
                       {"id": "c", "passed": True}]}
    (tmp_path / "20260103-000000.json").write_text(_json.dumps(rep), encoding="utf-8")
    h = ev.history(tmp_path)
    assert h["runs"] == [{"stamp": "20260103-000000", "model": "m", "passed": 2, "tasks": 3,
                         "success_rate": round(2 / 3, 3), "language_wrong": None}]


# --- the history command --------------------------------------------------------
def test_main_history_prints_runs_and_flaky(tmp_path, capsys):
    _mk_reports(tmp_path)
    rc = ev.main(["history", "--results", str(tmp_path)])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out == [
        "20260101-000000  2/3  66.7%",
        "20260102-000000  3/4  75.0%  language 1 wrong",
        "",
        "Flaky tasks (last 10 runs):",
        "b                        0/2  0/1 0/1",
        "a                        2/3  1/1 1/2"]


def test_main_history_last_flag(tmp_path, capsys):
    _mk_reports(tmp_path)
    rc = ev.main(["history", "--results", str(tmp_path), "--last", "1"])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out == [
        "20260102-000000  3/4  75.0%  language 1 wrong",
        "",
        "Flaky tasks (last 1 runs):",
        "b                        0/1  0/1",
        "a                        1/2  1/2"]


def test_main_history_no_flaky_prints_none(tmp_path, capsys):
    one = {"stamp": "20260103-000000", "model": "m",
           "summary": {"tasks": 1, "passed": 1, "success_rate": 1.0},
           "results": [{"id": "a", "passed": True}]}
    (tmp_path / "20260103-000000.json").write_text(_json.dumps(one), encoding="utf-8")
    rc = ev.main(["history", "--results", str(tmp_path)])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out == ["20260103-000000  1/1  100.0%", "", "Flaky tasks (last 10 runs):", "none"]


def test_main_history_returns_zero_for_broken_reports(tmp_path, capsys):
    # "results": 5 is skipped; "success_rate": null prints the rate computed from passed/tasks
    bad = {"stamp": "20260101-000000", "model": "m", "summary": {}, "results": 5}
    null_rate = {"stamp": "20260102-000000", "model": "m",
                 "summary": {"tasks": 4, "passed": 3, "success_rate": None},
                 "results": [{"id": "a", "passed": True}, {"id": "b", "passed": True},
                             {"id": "c", "passed": False}, {"id": "d", "passed": True}]}
    (tmp_path / "20260101-000000.json").write_text(_json.dumps(bad), encoding="utf-8")
    (tmp_path / "20260102-000000.json").write_text(_json.dumps(null_rate), encoding="utf-8")
    rc = ev.main(["history", "--results", str(tmp_path)])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert "20260102-000000  3/4  75.0%" in out
    assert not any("20260101" in line for line in out)  # the broken report was skipped, not printed


def test_main_history_missing_folder(tmp_path, capsys):
    rc = ev.main(["history", "--results", str(tmp_path / "nope")])
    assert rc == 1 and capsys.readouterr().out.splitlines() == ["no eval results"]

