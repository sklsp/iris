"""Behavioural tests for iris/__main__.py: the CLI entry point.

Covers the eval / eval-compare / dashboard subcommands and the three chat
modes (text, push-to-talk, wake-word voice session) with faked agent, LLM,
journal, input and voice objects. No microphone, no network, no real TTS.
"""
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import iris.__main__ as imain
import iris.dashboard as idash
import iris.eval as ieval
import iris.tools as itools
import iris.voice as ivoice


class FakeLM:
    name = "fake-llm"


class FakeJournal:
    def __init__(self, path):
        self.path = Path(path)


class FakeAgent:
    """Stands in for iris.agent.Agent. Records requests and answers with a fixed prefix."""

    def __init__(self, llm, confirm, journal):
        self.llm = llm
        self.confirm = confirm
        self.journal = journal
        self.requests = []
        self.confirmed = []
        self.ask_confirm = False  # tests set this to exercise the confirm callback

    def handle(self, request: str) -> str:
        self.requests.append(request)
        if self.ask_confirm:
            self.confirmed.append(self.confirm("Proceed?"))
        return f"answer:{request}"


def _install_agent(monkeypatch):
    """Patch Agent/LMStudio/Journal in the __main__ namespace; return a holder dict."""
    holder = {}

    def agent_factory(llm, confirm, journal):
        holder["agent"] = FakeAgent(llm, confirm, journal)
        return holder["agent"]

    monkeypatch.setattr(imain, "Agent", agent_factory)
    monkeypatch.setattr(imain, "LMStudio", lambda: FakeLM())
    monkeypatch.setattr(imain, "Journal", FakeJournal)
    # main() starts the skill watcher; a real one would outlive the test and poll later tests' skill folders
    monkeypatch.setattr(imain.skills, "watch", lambda say: None)
    return holder


def _ask_confirm_agent(monkeypatch):
    """Wrap the installed Agent factory so every created agent asks for confirmation."""
    original_factory = imain.Agent

    def factory(llm, confirm, journal):
        a = original_factory(llm, confirm, journal)
        a.ask_confirm = True
        return a

    monkeypatch.setattr(imain, "Agent", factory)


def _scripted_input(monkeypatch, values):
    """Replace builtins.input with a scripted list; record every prompt. Raise EOFError when empty."""
    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        if not values:
            raise EOFError
        return values.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


class FakeVoice:
    """Stands in for iris.voice.Voice (push-to-talk mode)."""

    def __init__(self, script):
        self.script = list(script)
        self.listen_prompts = []

    def listen(self, prompt=None):
        self.listen_prompts.append(prompt)
        return self.script.pop(0)


class FakeSession:
    """Stands in for iris.voice.VoiceSession (wake-word mode)."""

    def __init__(self, script):
        self.script = list(script)
        self.async_said = []
        self.blocking_said = []
        self.entered = 0
        self.exited = 0

    def next_request(self):
        return self.script.pop(0)

    def speak_async(self, text):
        self.async_said.append(text)

    def speak(self, text):
        self.blocking_said.append(text)

    def next_command(self):
        return "yes"

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited += 1


def test_eval_subcommand_delegates_and_propagates_exit_code(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["iris", "eval", "--only", "t1"])
    seen = {}

    def fake_eval_main(argv):
        seen["argv"] = argv
        return 7

    monkeypatch.setattr(ieval, "main", fake_eval_main)
    with pytest.raises(SystemExit) as exc:
        imain.main()
    assert exc.value.code == 7
    assert seen["argv"] == ["--only", "t1"]


def test_eval_compare_reads_two_reports_and_prints_the_diff(tmp_path, monkeypatch, capsys):
    report_a = {"results": [{"id": "t1", "passed": True}, {"id": "t2", "passed": False}]}
    report_b = {"results": [{"id": "t1", "passed": False}, {"id": "t3", "passed": True}]}
    fa, fb = tmp_path / "a.json", tmp_path / "b.json"
    fa.write_text(json.dumps(report_a), encoding="utf-8")
    fb.write_text(json.dumps(report_b), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["iris", "eval-compare", str(fa), str(fb)])
    imain.main()  # returns normally after printing

    out = capsys.readouterr().out.splitlines()
    assert out == [
        "1/2 -> 1/2 tasks passed",
        "broke: t1",
        "new: t3",
        "gone: t2",
        "same: 0",
    ]


def test_dashboard_subcommand_parses_port_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["iris", "dashboard", "--port", "9012"])
    calls = []
    monkeypatch.setattr(idash, "serve", lambda port=None: calls.append({"port": port}))

    imain.main()  # returns after serve()

    assert calls == [{"port": 9012}]


def test_dashboard_without_port_serves_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["iris", "dashboard"])
    calls = []
    monkeypatch.setattr(idash, "serve", lambda port=None: calls.append({"port": port}))

    imain.main()

    assert calls == [{"port": None}]



def test_text_mode_answers_requests_until_quit_word(tmp_path, monkeypatch, capsys):
    holder = _install_agent(monkeypatch)
    journal_dir = tmp_path / "sub"
    monkeypatch.setattr(sys, "argv", ["iris", "--journal", str(journal_dir / "j.jsonl")])
    prompts = _scripted_input(monkeypatch, ["what is 2+2", "quit"])

    imain.main()

    agent = holder["agent"]
    assert agent.requests == ["what is 2+2"]
    assert isinstance(agent.llm, FakeLM)
    assert agent.journal.path == journal_dir / "j.jsonl"
    assert journal_dir.is_dir(), "--journal parent directory must be created"
    out = capsys.readouterr().out.splitlines()
    assert out == ["Iris: Ready.", "Iris: answer:what is 2+2"]
    assert prompts == ["\nYou: ", "\nYou: "]


def test_text_mode_confirm_prompt_reads_yes_no(tmp_path, monkeypatch, capsys):
    holder = _install_agent(monkeypatch)
    _ask_confirm_agent(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["iris", "--journal", str(tmp_path / "j.jsonl")])
    prompts = _scripted_input(monkeypatch, ["do the thing", "yes", "quit"])

    imain.main()

    agent = holder["agent"]
    assert agent.requests == ["do the thing"]
    assert agent.confirmed == [True]
    assert prompts[1] == "Iris: Proceed? (yes/no) "
    out = capsys.readouterr().out.splitlines()
    assert out == ["Iris: Ready.", "Iris: answer:do the thing"]


def test_text_mode_stops_on_eof_without_calling_agent(tmp_path, monkeypatch, capsys):
    holder = _install_agent(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["iris", "--journal", str(tmp_path / "j.jsonl")])
    _scripted_input(monkeypatch, [])  # first input() raises EOFError

    imain.main()

    assert holder["agent"].requests == []
    out = capsys.readouterr().out.splitlines()
    assert out == ["Iris: Ready."]


def test_empty_request_is_ignored_and_loop_continues(tmp_path, monkeypatch):
    holder = _install_agent(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["iris", "--journal", str(tmp_path / "j.jsonl")])
    _scripted_input(monkeypatch, ["", "hello", "exit"])

    imain.main()

    assert holder["agent"].requests == ["hello"]


def test_push_to_talk_mode_drives_voice_and_agent(tmp_path, monkeypatch):
    holder = _install_agent(monkeypatch)
    _ask_confirm_agent(monkeypatch)
    voice_holder = {}

    def voice_factory():
        v = FakeVoice(["tell me a joke", "yes", "stop"])
        voice_holder["voice"] = v
        return v

    spoken = []
    monkeypatch.setattr(ivoice, "Voice", voice_factory)
    monkeypatch.setattr(imain, "speak", lambda t: spoken.append(t))
    monkeypatch.setattr(sys, "argv", ["iris", "--push-to-talk", "--journal", str(tmp_path / "j.jsonl")])

    imain.main()

    agent = holder["agent"]
    voice = voice_holder["voice"]
    assert agent.requests == ["tell me a joke"]
    assert agent.confirmed == [True]
    assert spoken == ["Ready.", "Proceed?", "answer:tell me a joke"]
    assert voice.listen_prompts == [None, "Say yes or no, then press Enter.", None]


def test_voice_mode_uses_session_context_manager_and_async_speech(tmp_path, monkeypatch):
    holder = _install_agent(monkeypatch)
    _ask_confirm_agent(monkeypatch)
    session_holder = {}

    def session_factory():
        s = FakeSession(["hi there", "quit"])
        session_holder["session"] = s
        return s

    monkeypatch.setattr(ivoice, "VoiceSession", session_factory)
    monkeypatch.setattr(sys, "argv", ["iris", "--voice", "--journal", str(tmp_path / "j.jsonl")])

    imain.main()

    agent = holder["agent"]
    session = session_holder["session"]
    assert agent.requests == ["hi there"]
    assert agent.confirmed == [True]
    assert session.async_said == ["Ready.", "answer:hi there"]
    assert session.blocking_said == ["Proceed?"]
    assert session.entered == 1
    assert session.exited == 1


def test_main_installs_timers_that_speak_through_say(tmp_path, monkeypatch, capsys):
    """main() calls set_announcer(say), so a timer fired by hand is printed like any other answer."""
    from iris.timers import Timers

    holder = _install_agent(monkeypatch)
    started: list[tuple[float, object]] = []

    def fake_start(seconds, fn):
        handle = SimpleNamespace(run=fn, cancel=lambda: None)
        started.append((seconds, handle))
        return handle

    monkeypatch.setattr(itools, "TIMERS", None)
    monkeypatch.setattr(itools, "Timers", lambda announce: Timers(announce=announce, start=fake_start))
    monkeypatch.setattr(sys, "argv", ["iris", "--journal", str(tmp_path / "j.jsonl")])
    _scripted_input(monkeypatch, ["quit"])

    imain.main()

    assert holder["agent"].requests == []
    assert itools.TIMERS is not None, "main must install the shared timers with say as announcer"
    capsys.readouterr()  # drop "Iris: Ready."
    assert itools.timer_set(0.5, "pasta") == "Timer 1 set for 30 seconds: pasta."
    started[0][1].run()  # fire the timer by hand instead of waiting on a thread
    out = capsys.readouterr().out.splitlines()
    assert out == ["Iris: Timer pasta is done."]



def test_startup_on_status_off(tmp_path, monkeypatch, capsys):
    import sys

    from iris.__main__ import startup_main

    monkeypatch.setenv("IRIS_STARTUP_DIR", str(tmp_path / "Startup"))
    assert startup_main(["status"]) == 0 and capsys.readouterr().out.strip() == "off"
    assert startup_main(["on"]) == 0
    text = (tmp_path / "Startup" / "Iris.cmd").read_text(encoding="utf-8")
    assert f'start "Iris" /min "{sys.executable}" -m iris --voice' in text
    assert startup_main([]) == 0 and capsys.readouterr().out.splitlines()[-1].startswith("on (")
    assert startup_main(["off"]) == 0 and not (tmp_path / "Startup" / "Iris.cmd").exists()
    assert startup_main(["off"]) == 0  # already off is fine
    assert startup_main(["sideways"]) == 2


def test_shortcut_puts_a_voice_mode_launcher_on_the_desktop(tmp_path, monkeypatch, capsys):
    import sys

    from iris.__main__ import shortcut_main

    monkeypatch.setenv("IRIS_DESKTOP_DIR", str(tmp_path / "Desktop"))
    assert shortcut_main([]) == 0
    text = (tmp_path / "Desktop" / "Iris.cmd").read_text(encoding="utf-8")
    assert f'"{sys.executable}" -m iris --voice' in text and "title Iris" in text
    assert "Double-click" in capsys.readouterr().out


def test_a_request_cancelled_from_the_overlay_is_not_spoken(tmp_path, monkeypatch):
    """The agent shares the session's cancel flag, and the main loop says nothing for a cancelled request."""
    _install_agent(monkeypatch)
    session_holder = {}
    agents = []

    class CancellableSession(FakeSession):
        def __init__(self, script):
            super().__init__(script)
            self.cancelled = threading.Event()

    class CancellableAgent(FakeAgent):
        def handle(self, request):
            self.cancel.clear()  # like the real Agent: a new request starts uncancelled
            if request == "zoek iets lang":
                self.cancel.set()  # the cancel button, clicked while the agent works
            return super().handle(request)

    def agent_factory(llm, confirm, journal):
        agents.append(CancellableAgent(llm, confirm, journal))
        return agents[-1]

    def session_factory():
        session_holder["session"] = CancellableSession(["zoek iets lang", "hoe laat is het", "quit"])
        return session_holder["session"]

    monkeypatch.setattr(imain, "Agent", agent_factory)
    monkeypatch.setattr(ivoice, "VoiceSession", session_factory)
    monkeypatch.setattr(sys, "argv", ["iris", "--voice", "--journal", str(tmp_path / "j.jsonl")])

    imain.main()

    session = session_holder["session"]
    assert agents[0].cancel is session.cancelled
    assert agents[0].requests == ["zoek iets lang", "hoe laat is het"]
    assert session.async_said == ["Ready.", "answer:hoe laat is het"]


def test_app_subcommand_delegates(monkeypatch):
    import iris.app

    seen = {}
    monkeypatch.setattr(iris.app, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    monkeypatch.setattr(sys, "argv", ["iris", "app", "--background"])
    with pytest.raises(SystemExit):
        imain.main()
    assert seen["argv"] == ["--background"]


def test_supervised_voice_mode_waits_on_the_apps_stop_event(tmp_path, monkeypatch):
    import iris.app

    _install_agent(monkeypatch)
    monkeypatch.setattr(ivoice, "VoiceSession", lambda: FakeSession(["quit"]))
    waited = []
    monkeypatch.setattr(iris.app, "quit_when_set", lambda name: waited.append(name))
    monkeypatch.setenv("IRIS_STOP_EVENT", "Local\\Iris-Stop-x")
    monkeypatch.setattr(sys, "argv", ["iris", "--voice", "--supervised", "--journal", str(tmp_path / "j.jsonl")])
    imain.main()
    import time

    end = time.perf_counter() + 5
    while not waited and time.perf_counter() < end:  # the waiter runs on its own thread
        time.sleep(0.01)
    assert waited == ["Local\\Iris-Stop-x"]
