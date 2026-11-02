"""python -m iris              type requests
python -m iris --voice         wake word mode: say "hey jarvis" (IRIS_WAKEWORD), stop on silence, hear answers
python -m iris --push-to-talk  old voice mode: press Enter to stop recording
python -m iris dashboard       read-only status page at http://127.0.0.1:8765 (add --port N if taken)
python -m iris autopilot       wait for a run in --repo to finish, then ask before starting the next queued prompt
python -m iris init            write a starter ~/.iris/config.toml (asks your name and city)
python -m iris doctor          check the setup (LM Studio, model, config, plugins, voice) and say what to fix
python -m iris startup on      start voice mode at login in a minimized console (off, status)
python -m iris skill list      the skills Iris built; `skill export NAME` zips an approved one to share,
                               `skill import FILE [--tier safe]` adds a shared one for review (confirm by default)"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path

from iris import __version__, skills, tools
from iris.agent import Agent, Journal
from iris.llm import LMStudio
from iris.voice import is_yes, speak


def startup_dir() -> Path:
    """The user's Startup folder (IRIS_STARTUP_DIR overrides it for tests)."""
    return Path(os.environ.get("IRIS_STARTUP_DIR")
                or Path(os.environ.get("APPDATA", Path.home())) / "Microsoft/Windows/Start Menu/Programs/Startup")


def startup_main(argv: list[str]) -> int:
    """`iris startup on|off|status`: start voice mode at login, in a minimized console you can see and close."""
    cmd = startup_dir() / "Iris.cmd"
    what = argv[0] if argv else "status"
    if what == "on":
        cmd.parent.mkdir(parents=True, exist_ok=True)
        cmd.write_text(f'@echo off\nstart "Iris" /min "{sys.executable}" -m iris --voice\n', encoding="utf-8")
        print(f"Iris starts in voice mode at login ({cmd}). Let LM Studio start its server at login too.")
    elif what == "off":
        cmd.unlink(missing_ok=True)
        print("Iris no longer starts at login.")
    elif what == "status":
        print(f"on ({cmd})" if cmd.exists() else "off")
    else:
        print("usage: iris startup on|off|status", file=sys.stderr)
        return 2
    return 0


def skill_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="iris skill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    ex = sub.add_parser("export")
    ex.add_argument("name")
    ex.add_argument("--out", type=Path)
    im = sub.add_parser("import")
    im.add_argument("file", type=Path)
    im.add_argument("--tier", choices=["safe", "confirm"], default="confirm")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            print(skills.listing())
        elif a.cmd == "export":
            print(f"wrote {skills.export_skill(a.name, a.out)}")
        else:
            from iris.tools import TOOLS

            r = skills.import_skill(a.file, a.tier, set(TOOLS))
            print(f"imported {r['name']}: {r['status']}" + (f" ({'; '.join(r['problems'])})" if r["problems"] else ""))
            if r["status"] == "ready":
                print("Read its code on the dashboard's Skills tab, then approve it there or ask Iris to approve it.")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


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
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from iris.config import init_main

        raise SystemExit(init_main())
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from iris.doctor import main as doctor_main

        raise SystemExit(doctor_main())
    if len(sys.argv) > 1 and sys.argv[1] == "startup":
        raise SystemExit(startup_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "skill":
        raise SystemExit(skill_main(sys.argv[2:]))
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
    from iris import reminders

    reminders.start_reminders(say)  # pending reminders wait on daemon threads and speak through the same say
    skills.watch(say)  # says when a skill the coding agent built is checked and ready, or blocked

    agent = Agent(LMStudio(), confirm, Journal(Path(a.journal)))
    cancelled = getattr(session, "cancelled", None)  # set by the overlay's cancel button, see VoiceSession.cancel
    if cancelled is not None:
        agent.cancel = cancelled  # one flag: the agent checks it before every model and tool call
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
                answer = agent.handle(request)
                if cancelled is None or not cancelled.is_set():  # a cancelled request says nothing
                    say(answer)
    except KeyboardInterrupt:
        # stopped with Ctrl+C while busy: show where every thread was, so a hang can be found afterwards
        import faulthandler

        faulthandler.dump_traceback(all_threads=True)
        raise
    finally:
        if session is not None:
            session.__exit__(None, None, None)


if __name__ == "__main__":
    main()
