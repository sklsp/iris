"""Tests for the eval task format and loader."""
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

