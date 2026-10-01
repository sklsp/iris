"""python -m iris            type requests
python -m iris --voice    speak requests (Enter stops recording), hear answers"""

from __future__ import annotations

import argparse
from pathlib import Path

from iris.agent import Agent, Journal
from iris.llm import LMStudio
from iris.voice import is_yes, speak


def main() -> None:
    ap = argparse.ArgumentParser(prog="iris", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--voice", action="store_true")
    ap.add_argument("--journal", default=str(Path.home() / ".iris" / "journal.jsonl"))
    a = ap.parse_args()
    Path(a.journal).parent.mkdir(parents=True, exist_ok=True)

    if a.voice:
        from iris.voice import Voice

        v = Voice()
        hear = lambda: v.listen()  # noqa: E731
        say = speak

        def confirm(question: str) -> bool:
            speak(question)
            return is_yes(v.listen("Say yes or no, then press Enter."))
    else:
        hear = lambda: input("\nYou: ")  # noqa: E731
        say = lambda t: print(f"Iris: {t}")  # noqa: E731

        def confirm(question: str) -> bool:
            return is_yes(input(f"Iris: {question} (yes/no) "))

    agent = Agent(LMStudio(), confirm, Journal(Path(a.journal)))
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


if __name__ == "__main__":
    main()
