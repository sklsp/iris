"""Someone on 0.4.0, 0.5.0 or 0.6.1 installs 1.0: their config, notes, reminders, prompt queue, journal, model
choice and approved skills must all still read. tests/fixtures/upgrade/<version>/ holds files in each release's
format (checked against `git show v<version>:iris/...`); no format changed, so no migration code is needed."""

import shutil
from pathlib import Path

import pytest

from iris import config, notes, prompt_queue, registry, reminders, skills, update
from iris.dashboard import journal_payload
from iris.llm import read_settings

FIXTURES = Path(__file__).parent / "fixtures" / "upgrade"


@pytest.fixture(params=["0.4.0", "0.5.0", "0.6.1"])
def home(request, tmp_path, monkeypatch):
    home = tmp_path / ".iris"
    shutil.copytree(FIXTURES / request.param, home)
    for env, name in (("IRIS_CONFIG", "config.toml"), ("IRIS_NOTES", "notes.md"), ("IRIS_REMINDERS", "reminders.json"),
                      ("IRIS_QUEUE", "queue.json"), ("IRIS_JOURNAL", "journal.jsonl"),
                      ("IRIS_SETTINGS", "settings.json"), ("IRIS_SKILLS", "skills")):
        monkeypatch.setenv(env, str(home / name))
    monkeypatch.delenv("IRIS_MODEL", raising=False)
    return request.param


def test_config_keeps_its_keys(home):
    cfg = config.load()
    assert cfg["name"] == "Jay" and config.get("city") == "Rotterdam" and config.error() is None
    assert cfg["plugins"]


def test_notes_reminders_and_the_queue_read(home):
    assert notes.find_notes("tandarts") == ["bel de tandarts"]
    assert [r["text"] for r in reminders._read()] == ["tandarts", "belasting"]
    queue = prompt_queue.load()
    assert queue["next_id"] == 3 and [p["text"] for p in queue["prompts"]] == ["fix the tests", "update the README"]


def test_the_journal_and_the_model_choice_read(home):
    entries = journal_payload()["entries"]
    assert any(e.get("text") == "Het is negen uur." for e in entries)
    assert read_settings()["model"] == "qwen3.8-27b"


def test_an_approved_skill_stays_approved_at_its_tier(home):
    tool = registry.active_tools()["say_hello"]
    assert tool.tier == "confirm" and skills._approved_ok("say_hello")


def test_a_new_reminder_keeps_the_old_ones(home):
    from datetime import datetime, timedelta

    reminders.reminder_set(f"{datetime.now() + timedelta(days=30):%Y-%m-%d} 10:00", "nieuw")
    assert [r["text"] for r in reminders._read()] == ["tandarts", "belasting", "nieuw"]


def test_the_update_and_setup_state_of_0_6_1(tmp_path, monkeypatch):
    """0.4.0 and 0.5.0 had no setup.json or update.json: the Setup tab opens once there, as intended."""
    from iris import firstrun

    shutil.copytree(FIXTURES / "0.6.1", tmp_path / ".iris")
    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / ".iris" / "config.toml"))
    assert firstrun.done() and update.enabled() is False  # update_check = false is kept
    assert update.status()["latest"] == "v0.6.1"
