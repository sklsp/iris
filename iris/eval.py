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
    category: str = "other"


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
        category = t.get("category", "other")
        if category not in ("read", "hands", "files", "safety", "skills", "agents", "other"):
            raise ValueError(f"unknown category in task {tid}: {category}")
        expect, forbid = list(t.get("expect", [])), list(t.get("forbid", []))
        outcomes, fixtures = dict(t.get("outcomes", {})), dict(t.get("fixtures", {}))
        for name in [*expect, *forbid, *outcomes, *fixtures]:
            if name not in TOOLS:
                raise ValueError(f"unknown tool in task {tid}: {name}")
        for value in outcomes.values():
            if value not in ("ok", "refused", "declined", "error"):
                raise ValueError(f"unknown outcome in task {tid}: {value}")
        tasks.append(Task(id=tid, request=req, expect=expect, forbid=forbid, confirm=confirm,
                          outcomes=outcomes, fixtures=fixtures, max_calls=int(t.get("max_calls", 6)),
                          category=category))
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
    category: str = "other"
    confirm: str = "yes"  # the task's confirm setting ("yes"/"no")
    confirmed_ok: int = 0  # CONFIRM-tier calls that ended ok


def run_task(llm, task: Task, journal_dir=None) -> Result:
    """Run one eval task through the real agent loop with dry-run tools and grade it from the journal."""
    if journal_dir is None:
        journal_dir = tempfile.mkdtemp()
    path = Path(journal_dir) / f"{task.id}.jsonl"
    agent = Agent(llm, lambda q: task.confirm == "yes", Journal(path), dry_tools(task.fixtures))
    start = time.perf_counter()
    answer_text = agent.handle(task.request)
    seconds = time.perf_counter() - start

    calls, confirmations, confirmed_ok, gave_up = [], 0, 0, False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "tool":
            calls.append((e["tool"], e["outcome"]))
            if e.get("tier") == CONFIRM:
                confirmations += 1
                if e["outcome"] == "ok":
                    confirmed_ok += 1
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
    unknown = []
    for name, outcome in calls:
        if outcome == "unknown" and name not in unknown:
            unknown.append(name)
    for name in unknown:
        reasons.append(f"unknown tool: {name}")
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
                  seconds=seconds, answer=answer_text, reasons=reasons, category=task.category,
                  confirm=task.confirm, confirmed_ok=confirmed_ok)


def summarize(results: list[Result], repeat: int = 1) -> dict:
    n = len(results)
    if not results:
        s = {"tasks": 0, "passed": 0, "success_rate": 0.0, "median_seconds": 0.0,
             "confirmations_per_task": 0.0, "failed": [], "by_category": {},
             "safety": {"forbidden_ok": 0, "unknown_tools": 0, "never_asked": 0}}
        if repeat > 1:
            s.update({"repeat": repeat, "per_task": {}, "pass_all": 0, "pass_any": 0})
        return s
    p = sum(1 for r in results if r.passed)
    by: dict[str, list[Result]] = {}
    for r in results:
        by.setdefault(r.category, []).append(r)
    s = {"tasks": n, "passed": p, "success_rate": round(p / n, 3),
         "median_seconds": round(statistics.median(r.seconds for r in results), 2),
         "confirmations_per_task": round(sum(r.confirmations for r in results) / n, 2),
         "failed": [r.id for r in results if not r.passed],
         "by_category": {c: {"tasks": len(rs), "passed": sum(1 for r in rs if r.passed),
                             "success_rate": round(sum(1 for r in rs if r.passed) / len(rs), 3)}
                         for c, rs in sorted(by.items())},
         "safety": {"forbidden_ok": sum(1 for r in results for reason in r.reasons
                                        if reason.startswith("forbidden call:")),
                     "unknown_tools": sum(1 for r in results for _t, o in r.calls if o == "unknown"),
                     "never_asked": sum(1 for r in results if r.confirm == "no" and r.confirmed_ok > 0)}}
    if repeat > 1:
        runs: dict[str, list[bool]] = {}
        for r in results:
            runs.setdefault(r.id, []).append(r.passed)
        s["repeat"] = repeat
        s["per_task"] = {tid: sum(v) for tid, v in sorted(runs.items())}
        s["pass_all"] = sum(1 for v in runs.values() if all(v))
        s["pass_any"] = sum(1 for v in runs.values() if any(v))
    return s


def _summary_line(s: dict) -> str:
    line = (f"{s['passed']}/{s['tasks']} passed ({s['success_rate'] * 100:.1f}%), "
            f"median {s['median_seconds']:.1f} s, {s['confirmations_per_task']:.2f} confirmations per task, "
            f"safety: {s['safety']['forbidden_ok']} forbidden, {s['safety']['unknown_tools']} unknown tools")
    if s.get("repeat", 1) > 1:
        line += f", {s['pass_all']}/{len(s['per_task'])} pass every time"
    return line


def _category_table(s: dict) -> list[str]:
    lines = ["| Category | Passed | Rate |", "|---|---|---|"]
    for cat, c in s["by_category"].items():
        lines.append(f"| {cat} | {c['passed']}/{c['tasks']} | {c['success_rate'] * 100:.1f}% |")
    return lines


def write_report(results: list[Result], out_dir, stamp: str, model: str = "", repeat: int = 1) -> tuple[Path, Path]:
    """Write {stamp}.json and {stamp}.md into out_dir; returns (json path, md path)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    s = summarize(results, repeat=repeat)
    json_path = out_dir / f"{stamp}.json"
    json_path.write_text(json.dumps({"stamp": stamp, "model": model or "unknown",
                                     "summary": s, "results": [asdict(r) for r in results]}, indent=2),
                         encoding="utf-8")
    lines = [f"# Iris eval {stamp}", "", f"Model: {model or 'unknown'}", "", _summary_line(s), ""]
    lines += _category_table(s)
    lines += ["", "| Task | Result | Calls | Seconds | Reasons |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.id} | {'pass' if r.passed else 'FAIL'} | "
                     f"{', '.join(f'{t}:{o}' for t, o in r.calls) or '-'} | {r.seconds:.1f} | "
                     f"{' ; '.join(r.reasons) or '-'} |")
    md_path = out_dir / f"{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def _pass_by_id(results) -> dict[str, bool]:
    """id -> passed; with --repeat a task has several results and it passes only when all of them did."""
    out: dict[str, bool] = {}
    for r in results:
        out[r["id"]] = out.get(r["id"], True) and r["passed"]
    return out


def compare(a: dict, b: dict) -> list[str]:
    """Compare two loaded report JSONs (item 20), per task id."""
    pa, pb = _pass_by_id(a["results"]), _pass_by_id(b["results"])
    ids = [i for i in pa] + [i for i in pb if i not in pa]
    lines = [f"{sum(pa.values())}/{len(pa)} -> {sum(pb.values())}/{len(pb)} tasks passed"]
    broke = [i for i in ids if i in pa and i in pb and pa[i] and not pb[i]]
    fixed = [i for i in ids if i in pa and i in pb and not pa[i] and pb[i]]
    new = [i for i in ids if i not in pa]
    gone = [i for i in ids if i not in pb]
    lines += ([f"broke: {i}" for i in broke] + [f"fixed: {i}" for i in fixed]
              + [f"new: {i}" for i in new] + [f"gone: {i}" for i in gone])
    same = sum(1 for i in ids if i in pa and i in pb and pa[i] == pb[i])
    lines.append(f"same: {same}")
    return lines


def publish(results_dir, readme_path) -> int:
    """Replace the eval block in the README with the newest report from results_dir."""
    files = sorted(Path(results_dir).glob("*.json"))
    if not files:
        print(f"no reports in {results_dir}")
        return 2
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    s = data["summary"]
    block = [f"Date: {data['stamp']}", f"Model: {data['model']}", _summary_line(s)] + _category_table(s)

    text = Path(readme_path).read_text(encoding="utf-8")
    start, end = "<!-- eval:start -->", "<!-- eval:end -->"
    i, j = text.find(start), text.find(end)
    if i < 0 or j < 0 or j < i:
        print("README has no eval markers")
        return 2
    nl = text.find("\n", i + len(start))
    head_end = nl if nl != -1 else len(text)
    Path(readme_path).write_text(text[:head_end] + "\n" + "\n".join(block) + "\n" + text[j:], encoding="utf-8")
    print(f"Published {files[-1].name} to {readme_path}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="iris eval", description=__doc__)
    ap.add_argument("command", nargs="?", default="run", choices=("run", "publish"))
    ap.add_argument("--tasks", default="eval/tasks.toml")
    ap.add_argument("--out", default="eval/results")
    ap.add_argument("--only", action="append", default=None, metavar="ID")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--results", default="eval/results")
    ap.add_argument("--readme", default="README.md")
    a = ap.parse_args(argv)

    if a.command == "publish":
        return publish(a.results, a.readme)

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
    total = len(tasks) * a.repeat
    results = []
    for _ in range(a.repeat):
        for task in tasks:
            r = run_task(llm, task)
            results.append(r)
            print(f"[{len(results)}/{total}] {r.id} {'pass' if r.passed else 'FAIL'} {r.seconds:.1f}s", flush=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    s = summarize(results, repeat=a.repeat)
    _, md_path = write_report(results, a.out, stamp, model=effective_model(), repeat=a.repeat)
    print(_summary_line(s))
    print(f"Report: {md_path}")
    return 0
