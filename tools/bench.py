"""Measure Iris without a model: how fast it starts, what one agent turn costs besides the model, how fast the
dashboard answers, and how big the prompt is. Every run uses a scratch ~/.iris, so it never touches yours.

    python tools/bench.py            the four measurements, as text
    python tools/bench.py --json     the same as JSON, to compare two runs

1. cold start of `python -m iris --help` and `python -m iris doctor` (median of 5 runs) and the 15 slowest imports
   of each (`-X importtime`, cumulative);
2. the overhead of one agent turn without the model: a fake model asks for one dry tool and then answers, so a turn
   is two model calls, building the prompt and the tool list, parsing the call, running the tool and two journal
   writes (median of 200 turns, after 20 to warm up);
3. the dashboard: from starting `python -m iris dashboard` to its first answer, and one GET / once it runs;
4. the prompt: the system prompt plus the JSON tool schemas the model gets on every call, in characters and in
   tokens, estimated as characters / 4 (a proxy: the real tokenizer is the model's own).

The numbers depend on the machine; compare runs made on the same one.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TODAY = "Sunday 01 November 2026"  # the eval's fixed date, so the prompt is the same on every run


def scratch_env(home: Path) -> dict[str, str]:
    """An environment that points every Iris file into `home` and the model server at a closed port."""
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "PYTHONUTF8": "1",
           "IRIS_LLM_URL": "http://127.0.0.1:9/v1", "IRIS_BROWSER_PROFILE": str(home / "no-browser"),
           "IRIS_START_MENU": str(home / "start-menu"), "IRIS_CHIME": "0"}
    for var, name in (("IRIS_CONFIG", "config.toml"), ("IRIS_JOURNAL", "journal.jsonl"), ("IRIS_SKILLS", "skills"),
                      ("IRIS_SETTINGS", "settings.json"), ("IRIS_NOTES", "notes.md"),
                      ("IRIS_REMINDERS", "reminders.json"), ("IRIS_QUEUE", "queue.json"),
                      ("IRIS_TRASH_DIR", "trash")):
        env[var] = str(home / ".iris" / name)
    env.pop("IRIS_OVERLAY", None)
    return env


def cold(args: list[str], env: dict[str, str], runs: int = 5) -> dict:
    """Median and best wall time of `python -m iris <args>`, in seconds."""
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        subprocess.run([sys.executable, "-m", "iris", *args], env=env, cwd=ROOT, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        times.append(time.perf_counter() - t0)
    return {"median_s": round(statistics.median(times), 3), "best_s": round(min(times), 3)}


def slowest_imports(args: list[str], env: dict[str, str], top: int = 15) -> list[tuple[str, float]]:
    """The `top` imports with the largest cumulative time (ms) under -X importtime."""
    r = subprocess.run([sys.executable, "-X", "importtime", "-m", "iris", *args], env=env, cwd=ROOT,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                       timeout=120)
    rows = []
    for line in r.stderr.splitlines():
        if not line.startswith("import time:") or "imported package" in line:
            continue
        _self, cumulative, name = line.split(":", 1)[1].split("|")
        rows.append((name.strip(), int(cumulative) / 1000))
    return sorted(rows, key=lambda row: row[1], reverse=True)[:top]


def per_turn(home: Path, turns: int = 200, warm: int = 20) -> dict:
    """Milliseconds Iris itself spends on one request whose model is instant (see the module docstring)."""
    for key, value in scratch_env(home).items():
        os.environ[key] = value
    from iris.agent import Agent, Journal
    from iris.llm import Reply, ToolCall

    class FakeModel:
        """Asks for the clock (a dry, safe tool), then answers; costs nothing."""

        def __init__(self) -> None:
            self.n = 0

        def chat(self, messages: list[dict], tools: list[dict]) -> Reply:
            self.n += 1
            if self.n % 2:
                call = ToolCall(f"c{self.n}", "clock", {})
                return Reply("", [call], {"role": "assistant", "content": "", "tool_calls": [
                    {"id": call.id, "type": "function", "function": {"name": "clock", "arguments": "{}"}}]})
            return Reply("It is nine.", [], {"role": "assistant", "content": "It is nine."})

    agent = Agent(FakeModel(), lambda q: True, Journal(home / ".iris" / "journal.jsonl"), today=TODAY)
    times = []
    for i in range(warm + turns):
        t0 = time.perf_counter()
        agent.handle(f"what time is it, request {i}")
        if i >= warm:
            times.append((time.perf_counter() - t0) * 1000)
    return {"median_ms": round(statistics.median(times), 2), "p90_ms": round(sorted(times)[int(len(times) * 0.9)], 2),
            "turns": turns, "history_messages": len(agent.messages)}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def dashboard(env: dict[str, str]) -> dict:
    """Seconds from starting the dashboard to its first answer, and one GET / once it runs."""
    port = _free_port()
    t0 = time.perf_counter()
    proc = subprocess.Popen([sys.executable, "-m", "iris", "dashboard", "--port", str(port)], env=env, cwd=ROOT,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    first = None
    try:
        while time.perf_counter() - t0 < 60:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                    r.read()
                first = time.perf_counter() - t0
                break
            except OSError:
                time.sleep(0.02)
        warm = []
        for _ in range(20):
            t1 = time.perf_counter()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                size = len(r.read())
            warm.append((time.perf_counter() - t1) * 1000)
    finally:
        proc.terminate()
        proc.wait(10)
    return {"start_to_first_answer_s": round(first, 3) if first else None,
            "get_page_median_ms": round(statistics.median(warm), 2), "page_bytes": size}


def prompt_size(home: Path) -> dict:
    """What the model gets on every call before the conversation: the system prompt and the tool schemas."""
    for key, value in scratch_env(home).items():
        os.environ[key] = value
    from iris.agent import system_prompt
    from iris.registry import active_tools

    tools = active_tools()
    prompt = system_prompt(name="Jay", today=TODAY)
    schemas = json.dumps([t.schema() for t in tools.values()], ensure_ascii=False)
    chars = len(prompt) + len(schemas)
    return {"tools": len(tools), "system_chars": len(prompt), "schema_chars": len(schemas), "chars": chars,
            "tokens_estimate": chars // 4}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print the numbers as JSON")
    ap.add_argument("--runs", type=int, default=5, help="cold starts per command (default 5)")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="iris-bench-") as tmp:
        home = Path(tmp)
        env = scratch_env(home)
        result = {
            "python": sys.version.split()[0], "platform": sys.platform,
            "help": {**cold(["--help"], env, a.runs), "slowest_imports_ms": slowest_imports(["--help"], env)},
            "doctor": {**cold(["doctor"], env, a.runs), "slowest_imports_ms": slowest_imports(["doctor"], env)},
            "turn": per_turn(home),
            "dashboard": dashboard(env),
            "prompt": prompt_size(home),
        }
    if a.json:
        print(json.dumps(result, indent=1))
        return 0
    print(f"Python {result['python']} on {result['platform']}")
    for name in ("help", "doctor"):
        r = result[name]
        command = "--help" if name == "help" else name
        print(f"\npython -m iris {command}: median {r['median_s']} s, best {r['best_s']} s")
        for module, ms in r["slowest_imports_ms"]:
            print(f"  {ms:8.1f} ms  {module}")
    t, d, p = result["turn"], result["dashboard"], result["prompt"]
    print(f"\nOne agent turn without the model: median {t['median_ms']} ms, p90 {t['p90_ms']} ms "
          f"({t['turns']} turns; two model calls, one dry tool, two journal lines each)")
    print(f"Dashboard: first answer {d['start_to_first_answer_s']} s after start; GET / {d['get_page_median_ms']} ms "
          f"({d['page_bytes']} bytes)")
    print(f"Prompt: {p['tools']} tools; system prompt {p['system_chars']} + schemas {p['schema_chars']} = "
          f"{p['chars']} characters, about {p['tokens_estimate']} tokens (characters / 4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
