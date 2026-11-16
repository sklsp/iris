import json
import os

from iris.agent import Journal


def lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_below_the_limit_nothing_moves(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    j.MAX_BYTES = 200
    j.path.write_text("x" * 99 + "\n", encoding="utf-8")
    j.log(event="request", text="hi")
    assert not (tmp_path / "journal.jsonl.1").exists()
    assert len(lines(j.path)) == 2


def test_at_the_limit_the_old_content_moves_and_a_fresh_file_starts(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    j.MAX_BYTES = 100
    j.path.write_text("x" * 100 + "\n", encoding="utf-8")
    j.log(event="request", text="hi")
    assert lines(tmp_path / "journal.jsonl.1") == ["x" * 100]
    fresh = [json.loads(line) for line in lines(j.path)]
    assert len(fresh) == 1 and fresh[0]["event"] == "request"


def test_a_second_rotation_replaces_the_old_file(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    j.MAX_BYTES = 10
    j.path.write_text("first line\n", encoding="utf-8")
    j.log(event="one")
    assert lines(tmp_path / "journal.jsonl.1") == ["first line"]
    j.path.write_text("second line\n", encoding="utf-8")
    j.log(event="two")
    assert lines(tmp_path / "journal.jsonl.1") == ["second line"]


def test_a_failing_move_still_writes_the_entry(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    j = Journal(tmp_path / "journal.jsonl")
    j.MAX_BYTES = 10
    j.path.write_text("x" * 20 + "\n", encoding="utf-8")
    j.log(event="request", text="hi")
    assert not (tmp_path / "journal.jsonl.1").exists()
    assert len(lines(j.path)) == 2  # the entry went to the current file; nothing was lost
