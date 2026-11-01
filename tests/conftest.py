import pytest


@pytest.fixture(autouse=True)
def _journal_in_tmp(tmp_path, monkeypatch):
    """Tests never touch the real ~/.iris: the journal, config.toml and the skills folder all point into tmp_path.
    A test that needs its own sets the variable again."""
    monkeypatch.setenv("IRIS_JOURNAL", str(tmp_path / "iris-journal.jsonl"))
    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "iris-config.toml"))
    monkeypatch.setenv("IRIS_SKILLS", str(tmp_path / "iris-skills"))
