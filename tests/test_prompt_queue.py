"""Tests for the prompt queue; every path is in tmp_path, never the real ~/.iris folder."""

import pytest

from iris.prompt_queue import add, listing, load, move, pending, queue_path, remove, save, summary, take
from iris.tools import SAFE, TOOLS


def test_load_of_a_missing_file_gives_an_empty_queue(tmp_path):
    assert load(tmp_path / "q.json") == {"next_id": 1, "prompts": []}


def test_save_then_load_gives_the_same_dict(tmp_path):
    p = tmp_path / "sub" / "q.json"
    data = {"next_id": 3, "prompts": [{"id": 1, "text": "open the dashboard"}]}
    save(data, p)
    assert load(p) == data


def test_save_leaves_no_tmp_file_behind(tmp_path):
    p = tmp_path / "q.json"
    save({"next_id": 1, "prompts": []}, p)
    assert not (tmp_path / "q.json.tmp").exists()


def test_broken_json_raises_value_error(tmp_path):
    p = tmp_path / "q.json"
    p.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match=r"^the queue file is broken: "):
        load(p)


def test_load_rejects_a_file_whose_shape_is_wrong(tmp_path):
    p = tmp_path / "q.json"
    p.write_text('{"prompts": [], "next_id": "one"}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"^the queue file is broken: "):
        load(p)


def test_queue_path_uses_the_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "q.json"))
    assert queue_path() == tmp_path / "q.json"


def test_add_pending_and_take(tmp_path):
    path = tmp_path / "q" / "queue.json"
    assert add("leadscan", "Run de tests en vertel me het resultaat.", now="2026-10-14 09:00", path=path) == 1
    assert add("Iris", "Lees PLAN.md.", now="2026-10-14 09:05", path=path) == 2
    assert add("leadscan", "Commit je werk.", now="2026-10-14 09:10", path=path) == 3
    assert [p["id"] for p in pending("LEADSCAN", path)] == [1, 3]
    assert take("leadscan", path) == {
        "id": 1,
        "agent": "leadscan",
        "text": "Run de tests en vertel me het resultaat.",
        "added": "2026-10-14 09:00",
    }
    data = load(path)
    assert data["next_id"] == 4
    assert [p["id"] for p in data["prompts"]] == [2, 3]


def test_take_without_a_prompt_raises_lookup_error(tmp_path):
    path = tmp_path / "q" / "queue.json"
    with pytest.raises(LookupError, match=r"^no prompts waiting for nobody$"):
        take("nobody", path)


def test_remove_returns_the_prompt_and_deletes_it(tmp_path):
    path = tmp_path / "q" / "queue.json"
    add("leadscan", "Run de tests.", now="2026-10-14 09:00", path=path)
    add("Iris", "Lees PLAN.md.", now="2026-10-14 09:05", path=path)
    assert remove(1, path) == {
        "id": 1,
        "agent": "leadscan",
        "text": "Run de tests.",
        "added": "2026-10-14 09:00",
    }
    data = load(path)
    assert [p["id"] for p in data["prompts"]] == [2]
    assert data["next_id"] == 3


def test_remove_of_an_unknown_id_raises_lookup_error(tmp_path):
    path = tmp_path / "q" / "queue.json"
    add("leadscan", "Run de tests.", now="2026-10-14 09:00", path=path)
    with pytest.raises(LookupError, match=r"^no prompt with id 7$"):
        remove(7, path)


def test_move_up_and_down_swaps_with_the_neighbour(tmp_path):
    path = tmp_path / "q" / "queue.json"
    add("leadscan", "first", now="2026-10-14 09:00", path=path)
    add("Iris", "second", now="2026-10-14 09:05", path=path)
    add("leadscan", "third", now="2026-10-14 09:10", path=path)
    move(3, -1, path)
    data = load(path)
    assert [(p["id"], p["text"]) for p in data["prompts"]] == [
        (1, "first"),
        (3, "third"),
        (2, "second"),
    ]
    move(3, 1, path)
    data = load(path)
    assert [(p["id"], p["text"]) for p in data["prompts"]] == [
        (1, "first"),
        (2, "second"),
        (3, "third"),
    ]


def test_move_at_both_edges_changes_nothing(tmp_path):
    path = tmp_path / "q" / "queue.json"
    add("leadscan", "first", now="2026-10-14 09:00", path=path)
    add("Iris", "second", now="2026-10-14 09:05", path=path)
    move(1, -1, path)
    assert [p["id"] for p in load(path)["prompts"]] == [1, 2]
    move(2, 1, path)
    assert [p["id"] for p in load(path)["prompts"]] == [1, 2]


def test_move_with_a_bad_step_raises_value_error(tmp_path):
    path = tmp_path / "q" / "queue.json"
    add("leadscan", "first", now="2026-10-14 09:00", path=path)
    with pytest.raises(ValueError, match=r"^step must be -1 or 1$"):
        move(1, 2, path)


def test_move_of_an_unknown_id_raises_lookup_error(tmp_path):
    path = tmp_path / "q" / "queue.json"
    add("leadscan", "first", now="2026-10-14 09:00", path=path)
    with pytest.raises(LookupError, match=r"^no prompt with id 9$"):
        move(9, -1, path)


def test_move_and_remove_never_change_the_ids(tmp_path):
    path = tmp_path / "q" / "queue.json"
    for i in range(4):
        add("leadscan", f"prompt {i}", now=f"2026-10-14 09:{i:02d}", path=path)
    move(3, -1, path)
    remove(2, path)
    data = load(path)
    assert [p["id"] for p in data["prompts"]] == [1, 3, 4]
    texts = {p["id"]: p["text"] for p in data["prompts"]}
    assert texts[3] == "prompt 2" and texts[4] == "prompt 3"


def test_add_requires_agent_and_text():
    with pytest.raises(ValueError, match="agent and text are required"):
        add(" ", "x")


def test_listing_and_queue_list_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "queue.json"))
    assert listing() == "The queue is empty."
    add("leadscan", "Run de tests en vertel me het resultaat.", now="2026-10-14 09:00")
    add("Iris", "Lees PLAN.md.", now="2026-10-14 09:05")
    add("leadscan", "Commit je werk.", now="2026-10-14 09:10")
    add("Iris", "x" * 100, now="2026-10-14 09:15")
    text = listing()
    assert text.startswith("4 prompts waiting:")
    assert ("4. Iris: " + "x" * 77 + "...") in text.splitlines()
    assert listing("nobody") == "No prompts waiting for nobody."
    assert TOOLS["queue_list"].tier == SAFE
    assert TOOLS["queue_list"].fn() == listing()


def test_queue_add_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "queue.json"))
    from iris.tools import CONFIRM

    assert TOOLS["queue_add"].tier == CONFIRM
    assert TOOLS["queue_add"].fn("leadscan", "Run de tests.") == "Added prompt 1 for leadscan."
    [p] = pending("leadscan")
    assert p["text"] == "Run de tests." and p["agent"] == "leadscan"


def test_queue_take_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "queue.json"))
    from iris.tools import CONFIRM

    assert TOOLS["queue_take"].tier == CONFIRM
    add("leadscan", "Run de tests.")
    assert TOOLS["queue_take"].fn("LEADSCAN") == (
        "Next prompt for leadscan (removed from the queue):\nRun de tests."
    )
    assert listing() == "The queue is empty."
    with pytest.raises(LookupError):
        TOOLS["queue_take"].fn("leadscan")


def test_summary_counts_prompts_per_agent_in_order_of_first_appearance(tmp_path):
    path = tmp_path / "q" / "queue.json"
    assert summary(path) == ""  # no file yet: an empty queue, not a crash
    add("leadscan", "Run de tests en vertel me het resultaat.", now="2026-10-14 09:00", path=path)
    add("Iris", "Lees PLAN.md.", now="2026-10-14 09:05", path=path)
    add("leadscan", "Commit je werk.", now="2026-10-14 09:10", path=path)
    assert summary(path) == "Queue: 3 prompts waiting (leadscan 2, Iris 1)."


def test_summary_of_one_prompt(tmp_path):
    path = tmp_path / "q" / "queue.json"
    add("leadscan", "Run de tests.", now="2026-10-14 09:00", path=path)
    assert summary(path) == "Queue: 1 prompt waiting (leadscan 1)."
