"""Measures how well the model behind Iris picks tools, with dry-run tools."""
import argparse
import json
import statistics
import tempfile
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from iris.agent import Agent, Journal
from iris.llm import LMStudio, effective_model
from iris.tools import CONFIRM, TOOLS, Tool


@dataclass
class Task:
    id: str
    request: str
    expect: list[str] = field(default_factory=list)
    forbid: list[str] = field(default_factory=list)
    confirm: str = "no"
    outcomes: dict[str, str] = field(default_factory=dict)
    fixtures: dict[str, str] = field(default_factory=dict)
    max_calls: int = 6


def load_tasks(path) -> list[Task]:
    """Read a TOML file of [[task]] tables and validate every task."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    tasks = []
    seen = set()
    for t in data.get("task", []):
        tid, req = t.get("id"), t.get("request")
        if not tid or not req:
            raise ValueError("task without id or request")
        if tid in seen:
            raise ValueError(f"duplicate task id: {tid}")
        seen.add(tid)
        confirm = t.get("confirm", "no")
        if confirm not in ("yes", "no"):
            raise ValueError(f"confirm must be yes or no in task {tid}")
        expect, forbid = list(t.get("expect", [])), list(t.get("forbid", []))
        outcomes, fixtures = dict(t.get("outcomes", {})), dict(t.get("fixtures", {}))
        for name in [*expect, *forbid, *outcomes, *fixtures]:
            if name not in TOOLS:
                raise ValueError(f"unknown tool in task {tid}: {name}")
        for value in outcomes.values():
            if value not in ("ok", "refused", "declined", "error"):
                raise ValueError(f"unknown outcome in task {tid}: {value}")
        tasks.append(Task(id=tid, request=req, expect=expect, forbid=forbid, confirm=confirm,
                          outcomes=outcomes, fixtures=fixtures, max_calls=int(t.get("max_calls", 6))))
    return tasks


def dry_tools(fixtures: dict[str, str]) -> dict[str, Tool]:
    """A copy of the registry where every tool returns its fixture or a dry-run note. Never calls the real fn."""
    out = {}
    for name, real in TOOLS.items():
        def fn(_name=name, **kwargs):
            return fixtures.get(_name, f"ok (dry run of {_name})")
        out[name] = Tool(name=real.name, description=real.description, params=real.params,
                         required=real.required, tier=real.tier, fn=fn, tier_for=real.tier_for)
    return out


@dataclass
class Result:
    id: str
    passed: bool
    calls: list[tuple[str, str]]  # (tool, outcome) in order
    confirmations: int
    seconds: float
    answer: str
    reasons: list[str]


def run_task(llm, task: Task, journal_dir=None) -> Result:
    """Run one eval task through the real agent loop with dry-run tools and grade it from the journal."""
    if journal_dir is None:
        journal_dir = tempfile.mkdtemp()
    path = Path(journal_dir) / f"{task.id}.jsonl"
    agent = Agent(llm, lambda q: task.confirm == "yes", Journal(path), dry_tools(task.fixtures))
    start = time.perf_counter()
    answer_text = agent.handle(task.request)
    seconds = time.perf_counter() - start

    calls, confirmations, gave_up = [], 0, False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "tool":
            calls.append((e["tool"], e["outcome"]))
            if e.get("tier") == CONFIRM:
                confirmations += 1
        elif e.get("event") == "gave_up":
            gave_up = True

    reasons = []
    called = {name for name, _ in calls}
    for name in task.expect:
        if name not in called:
            reasons.append(f"missing call: {name}")
    for name in task.forbid:
        if any(outcome == "ok" for n, outcome in calls if n == name):
            reasons.append(f"forbidden call: {name}")
    last = {}
    for name, outcome in calls:
        last[name] = outcome
    for tool_name, want in task.outcomes.items():
        got = last.get(tool_name, "none")
        if got != want:
            reasons.append(f"{tool_name} was {got}, expected {want}")
    if len(calls) > task.max_calls:
        reasons.append(f"too many calls: {len(calls)} > {task.max_calls}")
    if gave_up:
        reasons.append("gave up")

    return Result(id=task.id, passed=not reasons, calls=calls, confirmations=confirmations,
                  seconds=seconds, answer=answer_text, reasons=reasons)


def summarize(results: list[Result]) -> dict:
    n = len(results)
    if not results:
        return {"tasks": 0, "passed": 0, "success_rate": 0.0, "median_seconds": 0.0,
                "confirmations_per_task": 0.0, "failed": []}
    p = sum(1 for r in results if r.passed)
    return {"tasks": n, "passed": p, "success_rate": round(p / n, 3),
            "median_seconds": round(statistics.median(r.seconds for r in results), 2),
            "confirmations_per_task": round(sum(r.confirmations for r in results) / n, 2),
            "failed": [r.id for r in results if not r.passed]}


def _summary_line(s: dict) -> str:
    return (f"{s['passed']}/{s['tasks']} passed ({s['success_rate'] * 100:.1f}%), "
            f"median {s['median_seconds']:.1f} s, {s['confirmations_per_task']:.2f} confirmations per task")


def write_report(results: list[Result], out_dir, stamp: str, model: str = "") -> tuple[Path, Path]:
    """Write {stamp}.json and {stamp}.md into out_dir; returns (json path, md path)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    s = summarize(results)
    json_path = out_dir / f"{stamp}.json"
    json_path.write_text(json.dumps({"stamp": stamp, "model": model or "unknown",
                                     "summary": s, "results": [asdict(r) for r in results]}, indent=2),
                         encoding="utf-8")
    lines = [f"# Iris eval {stamp}", "", f"Model: {model or 'unknown'}", "", _summary_line(s), "",
             "| Task | Result | Calls | Seconds | Reasons |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.id} | {'pass' if r.passed else 'FAIL'} | "
                     f"{', '.join(f'{t}:{o}' for t, o in r.calls) or '-'} | {r.seconds:.1f} | "
                     f"{' ; '.join(r.reasons) or '-'} |")
    md_path = out_dir / f"{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="iris eval", description=__doc__)
    ap.add_argument("--tasks", default="eval/tasks.toml")
    ap.add_argument("--out", default="eval/results")
    ap.add_argument("--only", action="append", default=None, metavar="ID")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args(argv)

    tasks = load_tasks(Path(a.tasks))
    if a.only:
        known = {t.id for t in tasks}
        for tid in a.only:
            if tid not in known:
                print(f"unknown task: {tid}")
                return 2
        wanted = set(a.only)
        tasks = [t for t in tasks if t.id in wanted]

    llm = LMStudio()
    results = []
    for _ in range(a.repeat):
        for task in tasks:
            results.append(run_task(llm, task))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    _, md_path = write_report(results, a.out, stamp, model=effective_model())
    print(_summary_line(summarize(results)))
    print(f"Report: {md_path}")
    return 0
