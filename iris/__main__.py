"""python -m iris              type requests
python -m iris --voice         wake word mode: say "hey jarvis" (IRIS_WAKEWORD), stop on silence, hear answers
python -m iris --push-to-talk  old voice mode: press Enter to stop recording
python -m iris dashboard       read-only status page at http://127.0.0.1:8765 (add --port N if taken)
python -m iris autopilot       wait for a run in --repo to finish, then ask before starting the next queued prompt
python -m iris doctor          check the setup (LM Studio, model, config, plugins, voice) and say what to fix"""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from iris import __version__, skills, tools
from iris.agent import Agent, Journal
from iris.llm import LMStudio
from iris.voice import is_yes, speak


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "eval":
        from iris.eval import main as eval_main

        raise SystemExit(eval_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "eval-compare":
        import json as _json

        from iris.eval import compare

        a = _json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        b = _json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
        for line in compare(a, b):
            print(line)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "dashboard":
        from iris.dashboard import serve

        port = None
        args = sys.argv[2:]
        for i, a in enumerate(args):
            if a == "--port" and i + 1 < len(args):
                port = int(args[i + 1])
        serve(port=port)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from iris.doctor import main as doctor_main

        raise SystemExit(doctor_main())
    if len(sys.argv) > 1 and sys.argv[1] == "autopilot":
        from iris.autopilot import autopilot_main

        raise SystemExit(autopilot_main(sys.argv[2:]))
    ap = argparse.ArgumentParser(prog="iris", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--version", action="version", version=f"iris {__version__}")
    ap.add_argument("--voice", action="store_true")
    ap.add_argument("--push-to-talk", action="store_true")
    ap.add_argument("--journal", default=str(Path.home() / ".iris" / "journal.jsonl"))
    a = ap.parse_args()
    Path(a.journal).parent.mkdir(parents=True, exist_ok=True)

    session = None
    hear: Callable[[], str]
    say: Callable[[str], bool | threading.Thread | None]  # the three modes return different things; none is used
    if not (a.voice or a.push_to_talk):
        hear = lambda: input("\nYou: ")  # noqa: E731
        say = lambda t: print(f"Iris: {t}")  # noqa: E731

        def confirm(question: str) -> bool:
            return is_yes(input(f"Iris: {question} (yes/no) "))
    elif a.push_to_talk:
        from iris.voice import Voice

        v = Voice()
        hear = v.listen
        say = speak

        def confirm(question: str) -> bool:
            speak(question)
            return is_yes(v.listen("Say yes or no, then press Enter."))
    else:
        from iris.voice import VoiceSession

        session = VoiceSession()
        hear = session.next_request
        say = session.speak_async  # non-blocking: Iris talks while the next request is processed

        def confirm(question: str) -> bool:
            session.speak(question)
            return is_yes(session.next_command())

    tools.set_announcer(say)  # timers speak through the same say as everything else
    skills.watch(say)  # says when a skill the coding agent built is checked and ready, or blocked

    agent = Agent(LMStudio(), confirm, Journal(Path(a.journal)))
    try:
        if session is not None:
            session.__enter__()  # keep the mic open so a wake word can interrupt speech later
        say("Ready.")
        while True:
            try:
                request = hear().strip()
            except (EOFError, KeyboardInterrupt):
                break
            if request.lower() in {"quit", "exit", "stop", "stoppen"}:
                break
            if request:
                say(agent.handle(request))
    finally:
        if session is not None:
            session.__exit__(None, None, None)


if __name__ == "__main__":
    main()
