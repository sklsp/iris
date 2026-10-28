"""Tests for `python -m iris autopilot`: argparse in autopilot_main, dispatch from __main__,
and the terminal confirm. watch is always faked; no git repos, no keys, no real journal."""
import sys

import pytest

import iris.__main__ as imain
import iris.autopilot as iauto


def _install_fake_watch(monkeypatch) -> dict:
    """Replace autopilot.watch with a recorder; return the dict of what it was called with."""
    calls = {}

    def fake_watch(repo, agent, confirm, every=60.0, sleep=None, start=None, journal=None, max_checks=None):
        calls.update({"repo": repo, "agent": agent, "every": every, "journal": journal, "confirm": confirm})
        return "started the next prompt"

    monkeypatch.setattr(iauto, "watch", fake_watch)
    return calls


def test_main_dispatches_autopilot_and_propagates_the_exit_code(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["iris", "autopilot", "--repo", "/some/repo", "--agent", "qwen"])
    seen = {}

    def fake_autopilot_main(argv):
        seen["argv"] = argv
        return 3

    monkeypatch.setattr(iauto, "autopilot_main", fake_autopilot_main)

    with pytest.raises(SystemExit) as exc:
        imain.main()

    assert exc.value.code == 3
    assert seen["argv"] == ["--repo", "/some/repo", "--agent", "qwen"]


def test_parsed_values_reach_watch(tmp_path, monkeypatch, capsys):
    calls = _install_fake_watch(monkeypatch)

    rc = iauto.autopilot_main(["--repo", "/some/repo", "--agent", "qwen", "--every", "30"])

    assert rc == 0
    assert calls["repo"] == "/some/repo"
    assert calls["agent"] == "qwen"
    assert calls["every"] == 30.0
    assert isinstance(calls["journal"], iauto.Journal)
    # the autouse conftest fixture points IRIS_JOURNAL at tmp_path; the real home is never touched
    assert calls["journal"].path == tmp_path / "iris-journal.jsonl"
    assert capsys.readouterr().out.splitlines() == ["started the next prompt"]


def test_every_defaults_to_60_when_the_flag_is_missing(monkeypatch):
    calls = _install_fake_watch(monkeypatch)

    iauto.autopilot_main(["--repo", "/some/repo", "--agent", "qwen"])

    assert calls["every"] == 60.0


def test_missing_repo_exits_2_without_calling_watch(monkeypatch):
    calls = _install_fake_watch(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        iauto.autopilot_main(["--agent", "qwen"])

    assert exc.value.code == 2
    assert calls == {}


def test_missing_agent_exits_2_without_calling_watch(monkeypatch):
    calls = _install_fake_watch(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        iauto.autopilot_main(["--repo", "/some/repo"])

    assert exc.value.code == 2
    assert calls == {}


def test_confirm_reads_input_and_applies_is_yes_like_text_mode(monkeypatch):
    calls = _install_fake_watch(monkeypatch)
    prompts: list[str] = []
    answers = iter(["yes", "no"])

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)

    iauto.autopilot_main(["--repo", "/some/repo", "--agent", "qwen"])

    assert calls["confirm"]("Start the next qwen prompt?") is True
    assert calls["confirm"]("Start the next qwen prompt?") is False
    assert prompts == ["Iris: Start the next qwen prompt? (yes/no) "] * 2
