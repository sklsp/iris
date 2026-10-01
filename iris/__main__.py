"""python -m iris              type requests
python -m iris --voice         wake word mode: say "hey jarvis" (IRIS_WAKEWORD), stop on silence, hear answers
python -m iris --push-to-talk  old voice mode: press Enter to stop recording"""

from __future__ import annotations

import argparse
from pathlib import Path

from iris.agent import Agent, Journal
from iris.llm import LMStudio
from iris.voice import is_yes, speak


def main() -> None:
    ap = argparse.ArgumentParser(prog="iris", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--voice", action="store_true")
    ap.add_argument("--push-to-talk", action="store_true")
    ap.add_argument("--journal", default=str(Path.home() / ".iris" / "journal.jsonl"))
    a = ap.parse_args()
    Path(a.journal).parent.mkdir(parents=True, exist_ok=True)

    session = None
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
        say = speak

        def confirm(question: str) -> bool:
            speak(question)
            return is_yes(session.next_request())

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
