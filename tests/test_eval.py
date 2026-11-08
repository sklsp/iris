"""Tests for the eval task format and loader."""
import os
import urllib.error
from pathlib import Path

from iris import eval as ev
from iris.agent import MAX_STEPS


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


def test_task_mode_defaults_to_all_and_reads_important(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "Hoi"\n\n'
                          '[[task]]\nid = "b"\nrequest = "Hoi"\nmode = "important"\n')
    a, b = ev.load_tasks(p)
    assert (a.mode, b.mode) == ("all", "important")


def test_bad_mode_is_refused(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "Hoi"\nmode = "sometimes"\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "mode must be all or important in task a"


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
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "Hoi"\nlang = "xx"\n')
    try:
        ev.load_tasks(p)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert str(e) == "unknown lang in task a: xx"


def test_turns_default_empty_and_load_from_toml(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\n\n'
                         '[[task]]\nid = "b"\nrequest = "two."\nturns = ["en dan?", "nog iets"]\n')
    a, b = ev.load_tasks(p)
    assert a.turns == [] and b.turns == ["en dan?", "nog iets"]


def test_bad_turns_values_are_refused(tmp_path):
    for bad in ('turns = [3]', 'turns = [""]'):
        p = _write(tmp_path, f'[[task]]\nid = "a"\nrequest = "one."\n{bad}\n')
        try:
            ev.load_tasks(p)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert str(e) == "bad turns in task a"


def test_answer_has_and_not_default_empty_and_load_from_toml(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\n\n'
                         '[[task]]\nid = "b"\nrequest = "two."\nanswer_has = ["391", "correct"]\n'
                         'answer_not = ["991"]\n')
    a, b = ev.load_tasks(p)
    assert (a.answer_has, a.answer_not) == ([], [])
    assert (b.answer_has, b.answer_not) == (["391", "correct"], ["991"])


def test_bad_answer_values_are_refused(tmp_path):
    for bad in ('answer_has = 3', 'answer_has = [3]', 'answer_has = [""]',
                'answer_not = "nope"', 'answer_not = ["ok", 4]'):
        p = _write(tmp_path, f'[[task]]\nid = "a"\nrequest = "one."\n{bad}\n')
        try:
            ev.load_tasks(p)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            key = "answer_has" if bad.startswith("answer_has") else "answer_not"
            assert str(e) == f"bad {key} in task a"


def test_args_has_defaults_empty_and_loads_from_toml(tmp_path):
    p = _write(tmp_path, '[[task]]\nid = "a"\nrequest = "one."\n\n'
                          '[[task]]\nid = "b"\nrequest = "two."\nargs_has = ["mark", "whatsapp"]\n')
    a, b = ev.load_tasks(p)
    assert (a.args_has, b.args_has) == ([], ["mark", "whatsapp"])


def test_bad_args_has_values_are_refused(tmp_path):
    for bad in ('args_has = 3', 'args_has = [3]', 'args_has = [""]'):
        p = _write(tmp_path, f'[[task]]\nid = "a"\nrequest = "one."\n{bad}\n')
        try:
            ev.load_tasks(p)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert str(e) == "bad args_has in task a"


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


def test_dry_pure_tool_runs_for_real():
    assert ev.dry_tools({})["calculate"].fn(expression="6*7") == "6*7 = 42"


def test_dry_pure_tool_fixture_wins_over_the_real_fn():
    assert ev.dry_tools({"calculate": "fixture"})["calculate"].fn(expression="6*7") == "fixture"


def test_dry_non_pure_tool_still_returns_the_note():
    assert ev.dry_tools({})["list_windows"].fn() == "ok (dry run of list_windows)"


def test_dry_copies_keep_tier_and_pure_flag():
    import iris.tools as tools

    for name, real in tools.TOOLS.items():
        copy = ev.dry_tools({})[name]
        assert copy.tier == real.tier, name
        assert copy.pure == real.pure, name
    assert ev.dry_tools({})["calculate"].pure is True
    assert ev.dry_tools({})["open_app"].pure is False


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


def test_run_task_counts_declined_confirmation(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('folders = ["W:/"]', encoding="utf-8")  # W:/ stays a trash root for this test
    monkeypatch.setenv("IRIS_CONFIG", str(cfg))
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


def test_run_task_does_not_flag_forbidden_refused_or_declined(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('folders = ["W:/"]', encoding="utf-8")  # W:/ stays a trash root for this test
    monkeypatch.setenv("IRIS_CONFIG", str(cfg))
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


def test_eval_runs_in_the_default_confirm_mode_whatever_the_users_config_says(monkeypatch):
    from iris import agent, config

    monkeypatch.delenv("IRIS_CONFIRM", raising=False)
    monkeypatch.setattr(config, "get", lambda key, default=None: "important" if key == "confirm" else default)
    assert agent.important_only()
    assert ev.main(["--only", "no_such_task"]) == 2
    assert not agent.important_only()


def test_run_task_runs_in_the_tasks_confirm_mode(tmp_path, monkeypatch):
    seen = []

    class ModeLLM(ScriptedLLM):
        def chat(self, messages, tools):
            seen.append(os.environ.get("IRIS_CONFIRM"))  # what the model was asked under
            return super().chat(messages, tools)

    monkeypatch.setenv("IRIS_CONFIRM", "all")  # the value main() sets for a normal run
    task = ev.Task(id="t30", request="open kladblok", mode="important")
    r = ev.run_task(ModeLLM(call("open_app"), answer("Klaar.")), task, tmp_path)
    assert (r.passed, seen) == (True, ["important", "important"])  # every chat of the task saw it
    assert os.environ.get("IRIS_CONFIRM") == "all"  # and the old value is back


def test_run_task_deletes_the_confirm_mode_when_unset_before(tmp_path, monkeypatch):
    monkeypatch.delenv("IRIS_CONFIRM", raising=False)
    task = ev.Task(id="t31", request="open kladblok", mode="important")
    r = ev.run_task(ScriptedLLM(call("open_app"), answer("Klaar.")), task, tmp_path)
    assert (r.passed, "IRIS_CONFIRM" in os.environ) == (True, False)


def test_run_task_args_has_passes_when_a_call_was_given_the_string(tmp_path):
    task = ev.Task(id="t40", request="stuur mark op whatsapp", args_has=["mark"])  # case-insensitive
    llm = ScriptedLLM(call("type_text", window="WhatsApp", text="Mark"), answer("Klaar."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.passed, r.reasons) == (True, [])


def test_run_task_args_has_fails_when_no_call_was_given_the_string(tmp_path):
    task = ev.Task(id="t41", request="stuur mark op whatsapp", args_has=["mark"])
    llm = ScriptedLLM(call("type_text", window="WhatsApp", text="later ben ik er"), answer("Klaar."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.passed, r.reasons) == (False, ["no call was given: mark"])


def test_run_task_gives_up_after_max_steps(tmp_path):
    task = ev.Task(id="t11", request="doe iets", max_calls=MAX_STEPS)
    llm = ScriptedLLM(*[call("open_app")] * MAX_STEPS, answer("Half klaar."))  # hits MAX_STEPS, then sums up
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


def test_second_turn_sees_the_first_request_and_answer(tmp_path):
    task = ev.Task(id="t20", request="open kladblok", turns=["en nu?"])
    llm = ScriptedLLM(call("open_app"), answer("Klaar."), answer("Ja, staat open."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.passed, r.answer) == (True, "Ja, staat open.")
    second_turn_msgs = llm.seen[2]  # the first chat of the second turn
    pairs = [(m["role"], m.get("content")) for m in second_turn_msgs]
    assert ("user", "open kladblok") in pairs
    assert ("assistant", "Klaar.") in pairs


def test_forbidden_call_in_second_turn_fails_the_task(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('folders = ["W:/"]', encoding="utf-8")  # W:/ stays a trash root for this test
    monkeypatch.setenv("IRIS_CONFIG", str(cfg))
    task = ev.Task(id="t21", request="open kladblok", turns=["gooi het weg"], confirm="yes", forbid=["trash_file"])
    llm = ScriptedLLM(call("open_app"), answer("Klaar."),
                      call("trash_file", path="W:/tmp/oud.txt"), answer("Gedaan."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.passed, r.reasons) == (False, ["forbidden call: trash_file"])


def test_max_calls_counts_calls_of_both_turns(tmp_path):
    task = ev.Task(id="t22", request="wat staat open?", turns=["en nog eens"], max_calls=2)
    llm = ScriptedLLM(call("list_windows"), call("list_windows"), answer("Alles."),
                      call("list_windows"), answer("Nog alles."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.passed, r.reasons) == (False, ["too many calls: 3 > 2"])


def test_result_carries_request_and_turns(tmp_path):
    task = ev.Task(id="t23", request="open kladblok", turns=["en nu?"])
    llm = ScriptedLLM(call("open_app"), answer("Klaar."), answer("Ja, staat open."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.request, r.turns) == ("open kladblok", ["en nu?"])


def test_run_task_grades_a_model_failure(tmp_path):
    # both turns fail on the model: the task fails once with "model error" and no exception escapes
    task = ev.Task(id="t24", request="hoe warm is de gpu?", turns=["en nog eens"])

    class DownLLM:
        def chat(self, messages, tools):
            raise urllib.error.URLError("connection refused")

    r = ev.run_task(DownLLM(), task, tmp_path)
    assert (r.passed, r.reasons) == (False, ["model error"])


def test_run_task_answer_has_match_passes(tmp_path):
    task = ev.Task(id="t25", request="wat is 17 x 23?", answer_has=["391"])
    r = ev.run_task(ScriptedLLM(answer("Dat is 391.")), task, tmp_path)
    assert (r.passed, r.reasons) == (True, [])


def test_run_task_answer_has_missing_fails_with_literal_reason(tmp_path):
    task = ev.Task(id="t26", request="wat is 17 x 23?", answer_has=["391"])
    r = ev.run_task(ScriptedLLM(answer("Dat is 991.")), task, tmp_path)
    assert (r.passed, r.reasons) == (False, ["answer lacks any of: 391"])


def test_run_task_answer_has_any_one_match_is_enough(tmp_path):
    task = ev.Task(id="t27", request="wat is 17 x 23?", answer_has=["391", "driehonderdeneen"])
    r = ev.run_task(ScriptedLLM(answer("Dat is driehonderdeneen.")), task, tmp_path)
    assert (r.passed, r.reasons) == (True, [])


def test_run_task_answer_not_hit_fails(tmp_path):
    task = ev.Task(id="t28", request="wat is 17 x 23?", answer_not=["991"])
    r = ev.run_task(ScriptedLLM(answer("Dat is 991.")), task, tmp_path)
    assert (r.passed, r.reasons) == (False, ["answer says: 991"])


def test_run_task_answer_checks_ignore_case_and_non_breaking_space(tmp_path):
    task = ev.Task(id="t29", request="wat staat open?", answer_has=["KLADBLOK STAAT OPEN"],
                   answer_not=["NIET GEDAAN"])
    r = ev.run_task(ScriptedLLM(answer("kladblok\u00a0staat open.")), task, tmp_path)
    assert (r.passed, r.reasons) == (True, [])


def test_run_task_answer_checks_use_the_last_turn_only(tmp_path):
    task = ev.Task(id="t32", request="wat is 17 x 23?", turns=["en nu zeker?"], answer_has=["391"])
    llm = ScriptedLLM(answer("Dat is 991."), answer("Nee, het is 391."))
    r = ev.run_task(llm, task, tmp_path)
    assert (r.passed, r.reasons, r.answer) == (True, [], "Nee, het is 391.")


def test_run_task_without_answer_fields_grades_as_before(tmp_path):
    task = ev.Task(id="t33", request="wat is 17 x 23?")
    r = ev.run_task(ScriptedLLM(answer("Dat is 991.")), task, tmp_path)
    assert (r.passed, r.reasons) == (True, [])


def test_run_task_sets_answer_checked(tmp_path):
    t1 = ev.Task(id="t34", request="wat is 17 x 23?", answer_has=["391"])
    r1 = ev.run_task(ScriptedLLM(answer("Dat is 391.")), t1, tmp_path)
    assert r1.answer_checked is True
    t2 = ev.Task(id="t35", request="wat is 17 x 23?")
    r2 = ev.run_task(ScriptedLLM(answer("Dat is 991.")), t2, tmp_path)
    assert r2.answer_checked is False


# --- answer language -----------------------------------------------------------
def test_answer_language_dutch():
    assert ev.answer_language("Ik kan dat niet doen.") == "nl"


def test_answer_language_english():
    assert ev.answer_language("I can't do that, it's not possible.") == "en"


def test_answer_language_german():
    assert ev.answer_language("Das geht leider nicht, das musst du selbst machen.") == "de"


def test_answer_language_french_and_spanish():
    assert ev.answer_language("Demain il fera 12 degrés avec de la pluie.") == "fr"
    assert ev.answer_language("Mañana hará 12 grados y lloverá.") == "es"
    assert ev.answer_language("Son las 14:05 del domingo.") == "es"


def test_language_words_never_overlap_and_tasks_accept_every_language(tmp_path):
    words = list(ev._WORDS.values())
    assert all(not (a & b) for i, a in enumerate(words) for b in words[i + 1:])
    f = tmp_path / "t.toml"
    f.write_text('[[task]]\nid = "x"\nrequest = "¿Qué hora es?"\nlang = "es"\n', encoding="utf-8")
    assert ev.load_tasks(f)[0].lang == "es"


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
                                        "language": {"wrong": 0, "unknown": 3},
                                        "answers": {"checked": 0, "wrong": 0}}


def test_summarize_empty():
    s = ev.summarize([])
    assert (s["tasks"], s["passed"], s["success_rate"]) == (0, 0, 0.0)
    assert s["by_category"] == {}
    assert s["safety"] == {"forbidden_ok": 0, "refused_attempts": 0, "unknown_tools": 0,
                           "never_asked": 0}
    assert s["language"] == {"wrong": 0, "unknown": 0}
    assert s["answers"] == {"checked": 0, "wrong": 0}


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
                    "safety: 1 forbidden, 0 refused attempts, 0 unknown tools, language: 0 wrong, "
                    "answers: 0/0 wrong")


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
                    "safety: 0 forbidden, 0 refused attempts, 0 unknown tools, language: 1 wrong, "
                    "answers: 0/0 wrong")


def _answer_results():
    return [ev.Result(id="a", passed=False, calls=[], confirmations=0, seconds=1.0, answer="",
                      reasons=["answer lacks any of: 391"], category="other", answer_checked=True),
            ev.Result(id="b", passed=True, calls=[], confirmations=0, seconds=2.0, answer="",
                      reasons=[], category="other", answer_checked=True),
            ev.Result(id="c", passed=False, calls=[], confirmations=0, seconds=3.0, answer="",
                      reasons=["answer says: 991"], category="other")]


def test_summarize_counts_answer_checks():
    s = ev.summarize(_answer_results())
    assert s["answers"] == {"checked": 2, "wrong": 2}


def test_summary_line_shows_wrong_answers():
    line = ev._summary_line(ev.summarize(_answer_results()))
    assert line.endswith("language: 0 wrong, answers: 2/2 wrong")


def test_summary_line_treats_missing_answers_as_zero():
    # old JSON reports have no "answers" key; publish and history must still print them
    s = ev.summarize(_results())
    del s["answers"]
    line = ev._summary_line(s)
    assert line.endswith("language: 0 wrong, answers: 0/0 wrong")


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
                    "answers: 0/0 wrong, 1/2 pass every time")




def test_write_report_files(tmp_path):
    json_path, md_path = ev.write_report(_results(), tmp_path / "reports", "2026-10-17T0300")
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    assert (data["stamp"], data["model"]) == ("2026-10-17T0300", "unknown")
    md = md_path.read_text(encoding="utf-8").splitlines()
    assert md[4] == ("2/3 passed (66.7%), median 2.0 s, 1.00 confirmations per task, "
                     "safety: 1 forbidden, 0 refused attempts, 0 unknown tools, language: 0 wrong, "
                     "answers: 0/0 wrong")
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


def test_report_lists_wrong_answers(tmp_path):
    results = [ev.Result(id="ok", passed=True, calls=[], confirmations=0, seconds=1.0, answer="", reasons=[]),
               ev.Result(id="math", passed=False, calls=[], confirmations=0, seconds=2.0,
                         answer="Het antwoord is 991.", reasons=["forbidden call: trash_file",
                                                                "answer lacks any of: 391"],
                         answer_checked=True)]
    _json_path, md_path = ev.write_report(results, tmp_path / "reports", "20261028-1000")
    md = md_path.read_text(encoding="utf-8").splitlines()
    i = md.index("## Wrong answers")
    assert md[i + 1] == '- math: forbidden call: trash_file; answer lacks any of: 391: "Het antwoord is 991."'


def test_report_wrong_answers_none(tmp_path):
    _json_path, md_path = ev.write_report(_results(), tmp_path / "reports", "20261028-1000")
    md = md_path.read_text(encoding="utf-8").splitlines()
    i = md.index("## Wrong answers")
    assert md[i + 1] == "none"


def test_report_wrong_answer_line_truncates_the_answer(tmp_path):
    r = ev.Result(id="long", passed=False, calls=[], confirmations=0, seconds=1.0, answer="x" * 130,
                  reasons=["answer says: is verwijderd"], answer_checked=True)
    _json_path, md_path = ev.write_report([r], tmp_path / "reports", "20261028-1000")
    lines = md_path.read_text(encoding="utf-8").splitlines()
    i = lines.index("## Wrong answers")
    assert lines[i + 1] == '- long: answer says: is verwijderd: "' + "x" * 120 + '"'


def test_failed_task_section_shows_turns_under_the_request(tmp_path):
    r = ev.Result(id="t30", passed=False, calls=[], confirmations=0, seconds=1.0, answer="", reasons=["gave up"],
                  request="open kladblok", turns=["en nu?", "sluit het"])
    json_path, md_path = ev.write_report([r], tmp_path / "reports", "20261028-1000")
    md = md_path.read_text(encoding="utf-8").splitlines()
    i = md.index("- t30: open kladblok")
    assert md[i + 1] == "  then: en nu?"
    assert md[i + 2] == "  then: sluit het"
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    assert (data["results"][0]["request"], data["results"][0]["turns"]) == ("open kladblok", ["en nu?", "sluit het"])


def test_failed_task_without_turns_has_no_then_lines(tmp_path):
    r = ev.Result(id="t31", passed=False, calls=[], confirmations=0, seconds=1.0, answer="", reasons=["gave up"],
                  request="doe iets")
    _json_path, md_path = ev.write_report([r], tmp_path / "reports", "20261028-1000")
    lines = md_path.read_text(encoding="utf-8").splitlines()
    assert "- t31: doe iets" in lines and not any(line.startswith("  then:") for line in lines)


def test_failed_tasks_section_says_none_when_all_pass(tmp_path):
    _json_path, md_path = ev.write_report(_results()[:1], tmp_path / "reports", "20261028-1000")
    lines = md_path.read_text(encoding="utf-8").splitlines()
    i = lines.index("## Failed tasks")
    assert lines[i + 1] == "none"


def test_history_and_compare_load_old_reports_without_turns(tmp_path):
    old = {"stamp": "20260105-000000", "model": "m",
           "summary": {"tasks": 1, "passed": 0, "success_rate": 0.0},
           "results": [{"id": "a", "passed": False}]}   # old format: no request or turns keys
    (tmp_path / "20260105-000000.json").write_text(_json.dumps(old), encoding="utf-8")
    h = ev.history(tmp_path)
    assert [run["stamp"] for run in h["runs"]] == ["20260105-000000"]
    new = {"stamp": "b", "model": "m", "results": [{"id": "a", "passed": True, "turns": ["t"]}]}
    lines = ev.compare(old, new)
    assert lines[0] == "0/1 -> 1/1 tasks passed" and "fixed: a" in lines


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
        "safety: 1 forbidden, 0 refused attempts, 0 unknown tools, language: 0 wrong, answers: 0/0 wrong.\n\n"
        "| Category | Passed | Rate |\n|---|---|---|\n"
        "| hands | 1/1 | 100.0% |\n| read | 1/1 | 100.0% |\n| safety | 0/1 | 0.0% |\n\n"
        "<!-- eval:end -->\n")


def test_publish_old_report_without_answers_key(tmp_path):
    # a report written before answer checks existed must still be publishable (missing key = 0/0)
    s = ev.summarize(_results())
    del s["answers"]
    old = {"stamp": "20261027-090000", "model": "old-model", "partial": False,
           "summary": s,
           "results": [{"id": r.id, "passed": r.passed} for r in _results()]}
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "20261027-090000.json").write_text(_json.dumps(old), encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    rc = ev.main(["publish", "--results", str(tmp_path / "results"), "--readme", str(readme)])
    assert rc == 0
    text = readme.read_text(encoding="utf-8")
    assert "language: 0 wrong, answers: 0/0 wrong." in text


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
    assert rc == 2 and "no full reports" in capsys.readouterr().out


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
    assert len(tasks) == 250
    ids = [t.id for t in tasks]
    assert len(set(ids)) == len(ids)
    assert (ids[0], ids[-1]) == ("gpu_temp", "imp_injection_write")


def test_shipped_task_ids_unique_and_tools_load():
    tasks = ev.load_tasks(Path(__file__).resolve().parents[1] / "eval" / "tasks.toml")
    assert len(tasks) == 250
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
         "success_rate": 0.667, "language_wrong": None, "answers_wrong": None},
        {"stamp": "20260102-000000", "model": "new-model", "passed": 3, "tasks": 4,
         "success_rate": 0.75, "language_wrong": 1, "answers_wrong": None}]
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
                         "success_rate": round(2 / 3, 3), "language_wrong": None, "answers_wrong": None}]


def test_history_answers_wrong_comes_from_the_summary(tmp_path):
    rep = {"stamp": "20260103-000000", "model": "m",
           "summary": {"tasks": 2, "passed": 1, "success_rate": 0.5,
                       "answers": {"checked": 2, "wrong": 1}},
           "results": [{"id": "a", "passed": True}, {"id": "b", "passed": False}]}
    (tmp_path / "20260103-000000.json").write_text(_json.dumps(rep), encoding="utf-8")
    h = ev.history(tmp_path)
    assert h["runs"] == [{"stamp": "20260103-000000", "model": "m", "passed": 1, "tasks": 2,
                          "success_rate": 0.5, "language_wrong": None, "answers_wrong": 1}]


def test_history_answers_wrong_is_none_without_the_key_or_when_not_a_number(tmp_path):
    old = {"stamp": "20260103-000000", "model": "m",
           "summary": {"tasks": 1, "passed": 1},
           "results": [{"id": "a", "passed": True}]}
    bad = {"stamp": "20260104-000000", "model": "m",
           "summary": {"tasks": 1, "passed": 1, "answers": {"checked": 1, "wrong": "one"}},
           "results": [{"id": "a", "passed": True}]}
    (tmp_path / "20260103-000000.json").write_text(_json.dumps(old), encoding="utf-8")
    (tmp_path / "20260104-000000.json").write_text(_json.dumps(bad), encoding="utf-8")
    h = ev.history(tmp_path)
    assert [r["answers_wrong"] for r in h["runs"]] == [None, None]


def test_main_history_prints_answers_wrong_after_the_language_part(tmp_path, capsys):
    rep = {"stamp": "20260103-000000", "model": "m",
           "summary": {"tasks": 2, "passed": 1, "success_rate": 0.5,
                       "language": {"wrong": 1, "unknown": 0},
                       "answers": {"checked": 2, "wrong": 1}},
           "results": [{"id": "a", "passed": True}, {"id": "b", "passed": False}]}
    (tmp_path / "20260103-000000.json").write_text(_json.dumps(rep), encoding="utf-8")
    rc = ev.main(["history", "--results", str(tmp_path)])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out[0] == "20260103-000000  1/2  50.0%  language 1 wrong  answers 1 wrong"


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



# --- partial runs never become the headline ------------------------------------
def _full_and_partial(tmp_path):
    # an older full report next to a newer partial one
    ev.write_report(_results(), tmp_path / "results", "20261027-080000", model="old-model")
    ev.write_report([_results()[0]], tmp_path / "results", "20261027-090000", model="new-model", partial=True)


def test_write_report_stores_the_partial_flag(tmp_path):
    json_path, _ = ev.write_report(_results(), tmp_path / "reports", "20261028-1000")
    assert _json.loads(json_path.read_text(encoding="utf-8"))["partial"] is False
    json_path, _ = ev.write_report(_results(), tmp_path / "reports", "20261029-1000", partial=True)
    assert _json.loads(json_path.read_text(encoding="utf-8"))["partial"] is True


def test_full_reports_skips_partial_keeps_broken_and_keyless(tmp_path):
    ev.write_report(_results(), tmp_path / "r", "20260101-000000")  # has the key: false, counts
    (tmp_path / "r" / "20260102-000000.json").write_text("{ not json", encoding="utf-8")  # unreadable counts as full
    old = {"stamp": "20260103-000000", "model": "m", "summary": {}, "results": []}  # no key at all: still full
    (tmp_path / "r" / "20260103-000000.json").write_text(_json.dumps(old), encoding="utf-8")
    ev.write_report([_results()[0]], tmp_path / "r", "20260104-000000", partial=True)  # newest, but partial
    assert [p.name for p in ev.full_reports(tmp_path / "r")] == ["20260101-000000.json", "20260102-000000.json",
                                                                 "20260103-000000.json"]


def test_full_reports_missing_dir_is_empty(tmp_path):
    assert ev.full_reports(tmp_path / "nope") == []


def test_publish_writes_the_full_stamp_when_a_newer_partial_exists(tmp_path, capsys):
    _full_and_partial(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    rc = ev.main(["publish", "--results", str(tmp_path / "results"), "--readme", str(readme)])
    text = readme.read_text(encoding="utf-8")
    assert rc == 0 and "Run of 2026-10-27 08:00 on `old-model`" in text
    assert "new-model" not in text


def test_publish_with_only_partial_reports(tmp_path, capsys):
    ev.write_report([_results()[0]], tmp_path / "results", "20261027-090000", partial=True)
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    rc = ev.main(["publish", "--results", str(tmp_path / "results"), "--readme", str(readme)])
    assert rc == 2 and "no full reports in" in capsys.readouterr().out


def test_history_lists_only_full_reports(tmp_path):
    _full_and_partial(tmp_path)
    h = ev.history(tmp_path / "results")
    assert [r["stamp"] for r in h["runs"]] == ["20261027-080000"]


def test_eval_command_only_marks_the_report_partial(tmp_path, monkeypatch):
    tasks_file = tmp_path / "tasks.toml"
    text = ('[[task]]\nid = "a"\nrequest = "Hoi"\n\n'
            '[[task]]\nid = "b"\nrequest = "Hallo"\n')
    tasks_file.write_text(text, encoding="utf-8")
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"), "--only", "a"])
    (json_path,) = list((tmp_path / "results").glob("*.json"))
    report = _json.loads(json_path.read_text(encoding="utf-8"))
    assert rc == 0 and [r["id"] for r in report["results"]] == ["a"] and report["partial"] is True


def test_eval_command_without_filters_marks_the_report_full(tmp_path, monkeypatch):
    tasks_file = tmp_path / "tasks.toml"
    tasks_file.write_text('[[task]]\nid = "a"\nrequest = "Hoi"\n', encoding="utf-8")
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results")])
    (json_path,) = list((tmp_path / "results").glob("*.json"))
    assert rc == 0 and _json.loads(json_path.read_text(encoding="utf-8"))["partial"] is False


def _category_tasks_file(tmp_path):
    tasks_file = tmp_path / "tasks.toml"
    text = ('[[task]]\nid = "a"\nrequest = "Hoi"\ncategory = "files"\n\n'
            '[[task]]\nid = "b"\nrequest = "Hallo"\ncategory = "safety"\n\n'
            '[[task]]\nid = "c"\nrequest = "Dag"\ncategory = "files"\n')
    tasks_file.write_text(text, encoding="utf-8")
    return tasks_file


def test_category_keeps_only_matching_tasks_and_marks_partial(tmp_path, monkeypatch):
    tasks_file = _category_tasks_file(tmp_path)
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"), "--category", "files"])
    (json_path,) = list((tmp_path / "results").glob("*.json"))
    report = _json.loads(json_path.read_text(encoding="utf-8"))
    assert rc == 0 and [r["id"] for r in report["results"]] == ["a", "c"] and report["partial"] is True


def test_category_repeatable_keeps_the_union(tmp_path, monkeypatch):
    tasks_file = _category_tasks_file(tmp_path)
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"),
                  "--category", "files", "--category", "safety"])
    (json_path,) = list((tmp_path / "results").glob("*.json"))
    report = _json.loads(json_path.read_text(encoding="utf-8"))
    assert rc == 0 and [r["id"] for r in report["results"]] == ["a", "b", "c"] and report["partial"] is True


def test_unknown_category_is_rejected(tmp_path, monkeypatch, capsys):
    tasks_file = _category_tasks_file(tmp_path)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"), "--category", "nope"])
    assert rc == 2 and "unknown category: nope" in capsys.readouterr().out


def test_category_combined_with_only_requires_both(tmp_path, monkeypatch):
    tasks_file = _category_tasks_file(tmp_path)
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"),
                  "--only", "a", "--only", "b", "--category", "files"])
    (json_path,) = list((tmp_path / "results").glob("*.json"))
    report = _json.loads(json_path.read_text(encoding="utf-8"))
    assert rc == 0 and [r["id"] for r in report["results"]] == ["a"] and report["partial"] is True


def _pattern_tasks_file(tmp_path):
    tasks_file = tmp_path / "tasks.toml"
    parts = [f'[[task]]\nid = "mt_{i}"\nrequest = "Hoi"\ncategory = "files"\n' for i in range(1, 7)]
    parts.append('[[task]]\nid = "plain"\nrequest = "Hallo"\ncategory = "safety"\n')
    tasks_file.write_text("\n".join(parts), encoding="utf-8")
    return tasks_file


def test_only_pattern_selects_matching_ids(tmp_path, monkeypatch):
    tasks_file = _pattern_tasks_file(tmp_path)
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"), "--only", "mt_*"])
    (json_path,) = list((tmp_path / "results").glob("*.json"))
    report = _json.loads(json_path.read_text(encoding="utf-8"))
    ids = [r["id"] for r in report["results"]]
    assert rc == 0 and ids == [f"mt_{i}" for i in range(1, 7)] and report["partial"] is True


def test_only_pattern_matching_nothing_is_rejected(tmp_path, monkeypatch, capsys):
    tasks_file = _pattern_tasks_file(tmp_path)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"), "--only", "mz_*"])
    assert rc == 2 and "unknown task: mz_*" in capsys.readouterr().out


def _failed_from_setup(tmp_path, results):
    tasks_file = tmp_path / "tasks.toml"
    text = ('[[task]]\nid = "a"\nrequest = "Hoi"\n\n'
            '[[task]]\nid = "b"\nrequest = "Hallo"\n')
    tasks_file.write_text(text, encoding="utf-8")
    report_path = tmp_path / "old.json"
    report_path.write_text(_json.dumps({"results": results}), encoding="utf-8")
    return tasks_file, report_path


def test_failed_from_keeps_only_tasks_that_failed(tmp_path, monkeypatch):
    tasks_file, report_path = _failed_from_setup(
        tmp_path, [{"id": "a", "passed": False}, {"id": "b", "passed": True}])
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"),
                  "--failed-from", str(report_path)])
    (json_path,) = list((tmp_path / "results").glob("*.json"))
    report = _json.loads(json_path.read_text(encoding="utf-8"))
    assert rc == 0 and [r["id"] for r in report["results"]] == ["a"] and report["partial"] is True


def test_failed_from_counts_a_failure_in_any_repeat(tmp_path, monkeypatch):
    tasks_file, report_path = _failed_from_setup(
        tmp_path, [{"id": "a", "passed": True}, {"id": "b", "passed": True},
                   {"id": "a", "passed": False}, {"id": "b", "passed": True}])
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"),
                  "--failed-from", str(report_path)])
    (json_path,) = list((tmp_path / "results").glob("*.json"))
    report = _json.loads(json_path.read_text(encoding="utf-8"))
    assert rc == 0 and [r["id"] for r in report["results"]] == ["a"]


def test_failed_from_skips_ids_that_are_no_longer_tasks(tmp_path, monkeypatch, capsys):
    tasks_file, report_path = _failed_from_setup(
        tmp_path, [{"id": "gone", "passed": False}, {"id": "a", "passed": False}])
    _monkey(monkeypatch)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"),
                  "--failed-from", str(report_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "skipped (no longer a task): gone" in out


def test_failed_from_with_no_failures_returns_zero_without_running(tmp_path, monkeypatch, capsys):
    tasks_file, report_path = _failed_from_setup(tmp_path, [{"id": "a", "passed": True}])

    def _boom():
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(ev, "LMStudio", _boom)
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"),
                  "--failed-from", str(report_path)])
    assert rc == 0 and f"nothing failed in {report_path.name}" in capsys.readouterr().out
    assert not (tmp_path / "results").exists()


def test_failed_from_unreadable_file_returns_two(tmp_path, monkeypatch, capsys):
    tasks_file = tmp_path / "tasks.toml"
    tasks_file.write_text('[[task]]\nid = "a"\nrequest = "Hoi"\n', encoding="utf-8")
    missing = tmp_path / "nope.json"
    rc = ev.main(["--tasks", str(tasks_file), "--out", str(tmp_path / "results"),
                  "--failed-from", str(missing)])
    assert rc == 2 and f"cannot read {missing}" in capsys.readouterr().out



def test_the_eval_pins_the_date_its_fixtures_are_written_for(tmp_path):
    seen = []

    class LLM:
        def chat(self, messages, tools):
            seen.append(messages[0]["content"])
            from iris.llm import Reply

            return Reply("Klaar.", [], {"role": "assistant", "content": "Klaar."})

    task = ev.Task(id="t", request="Hoe laat is het?")
    ev.run_task(LLM(), task, tmp_path)
    assert ev.EVAL_TODAY in seen[0] and ev.EVAL_TODAY.startswith("Sunday 01 November")


def test_an_expectation_can_name_either_of_two_right_paths(tmp_path):
    task = ev.Task(id="t20", request="search youtube", expect=["browser_type|open_url"])
    assert ev.run_task(ScriptedLLM(call("open_url"), answer("Done.")), task, tmp_path / "a").passed
    r = ev.run_task(ScriptedLLM(call("open_app"), answer("Done.")), task, tmp_path / "b")
    assert r.reasons == ["missing call: browser_type|open_url"]


def test_german_french_and_spanish_each_have_everyday_tasks_and_safety_traps():
    from iris.eval import load_tasks
    from iris.tools import TOOLS

    tasks = load_tasks(Path(__file__).resolve().parents[1] / "eval" / "tasks.toml")
    for lang in ("de", "fr", "es"):
        mine = [t for t in tasks if t.lang == lang]
        assert sum(t.category != "safety" for t in mine) >= 8 and sum(t.category == "safety" for t in mine) >= 3, lang
        for t in mine:
            for name in [n for e in t.expect for n in e.split("|")] + t.forbid + list(t.fixtures):
                assert name in TOOLS, (t.id, name)  # every expected, forbidden or faked tool exists
