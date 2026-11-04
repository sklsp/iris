"""Branch tests for the last uncovered lines of iris/eval.py (dry_tools, history, --failed-from)."""
import json

from iris import browser
from iris import eval as ev


def test_a_browser_fixture_is_remembered_so_later_clicks_see_its_labels():
    """A string fixture on a browser_* tool is passed to browser.remember before it is returned."""
    fx = 'Tab: Shop (https://shop.example)\n[1] button "Buy now"'

    tools = ev.dry_tools({"browser_read": fx})
    out = tools["browser_read"].fn()

    assert out == fx  # the fixture itself comes back, unchanged
    assert browser.item(1) == {"role": "button", "name": "Buy now", "url": "https://shop.example", "type": ""}


def test_history_falls_back_to_computed_numbers_when_the_summary_is_not_a_dict(tmp_path):
    report = tmp_path / "run.json"
    report.write_text(json.dumps({
        "stamp": "20261104-120000",
        "model": "qwen3",
        "results": [
            {"id": "a", "passed": True},
            {"id": "b", "passed": False},
        ],
        "summary": "not a dict",  # the report is broken; history must not crash on it
    }), encoding="utf-8")

    out = ev.history(tmp_path)

    assert out["runs"] == [{"stamp": "20261104-120000", "model": "qwen3", "passed": 1, "tasks": 2,
                            "success_rate": 0.5, "language_wrong": None, "answers_wrong": None}]
    assert out["skipped"] == 0
    assert out["flaky"] == ["b"]


def test_failed_from_skips_results_whose_id_is_not_a_string(tmp_path, monkeypatch, capsys):
    tasks = tmp_path / "tasks.toml"
    tasks.write_text('[[task]]\nid = "a"\nrequest = "Do a."\n', encoding="utf-8")
    failed = tmp_path / "failed.json"
    failed.write_text(json.dumps({"results": [
        {"id": 42, "passed": False},   # an id that is not a string: skipped, never matched
        {"id": None, "passed": False},
    ]}), encoding="utf-8")
    monkeypatch.setenv("IRIS_CONFIRM", "all")  # main() setdefaults this; keep the real env untouched

    code = ev.main(["run", "--tasks", str(tasks), "--failed-from", str(failed)])

    assert code == 0
    assert "nothing failed in failed.json" in capsys.readouterr().out
