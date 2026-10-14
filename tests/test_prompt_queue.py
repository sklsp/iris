"""Tests for the prompt queue; every path is in tmp_path, never the real ~/.iris folder."""

import pytest

from iris.prompt_queue import load, queue_path, save


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
