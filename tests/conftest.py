import pytest


@pytest.fixture(autouse=True)
def _journal_in_tmp(tmp_path, monkeypatch):
    """Tests never write to the real ~/.iris/journal.jsonl; a test that needs its own journal sets IRIS_JOURNAL."""
    monkeypatch.setenv("IRIS_JOURNAL", str(tmp_path / "iris-journal.jsonl"))
