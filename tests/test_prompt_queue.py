"""Tests for the prompt queue; every path is in tmp_path, never the real ~/.iris folder."""

import pytest

from iris.prompt_queue import add, load, pending, queue_path, save, take


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


def test_add_requires_agent_and_text():
    with pytest.raises(ValueError, match="agent and text are required"):
        add(" ", "x")
