"""Measures how well the model behind Iris picks tools, with dry-run tools."""
import argparse
import fnmatch
import json
import re
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
    fixtures: dict[str, str | list[str]] = field(default_factory=dict)
    max_calls: int = 6
    category: str = "other"
    lang: str = "nl"  # language the answer is expected in ("nl", "en" or "any")
    turns: list[str] = field(default_factory=list)  # later requests in the same conversation, after request
    answer_has: list[str] = field(default_factory=list)  # last answer must contain at least one of these
    answer_not: list[str] = field(default_factory=list)  # last answer must contain none of these


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
        lang = t.get("lang", "nl")
        if lang not in ("nl", "en", "any"):
            raise ValueError(f"unknown lang in task {tid}: {lang}")
        expect, forbid = list(t.get("expect", [])), list(t.get("forbid", []))
        outcomes, fixtures = dict(t.get("outcomes", {})), dict(t.get("fixtures", {}))
        for name in [*expect, *forbid, *outcomes, *fixtures]:
            if name not in TOOLS:
                raise ValueError(f"unknown tool in task {tid}: {name}")
        for value in outcomes.values():
            if value not in ("ok", "refused", "declined", "error"):
                raise ValueError(f"unknown outcome in task {tid}: {value}")
        for tool, fx in fixtures.items():
            is_seq = isinstance(fx, list) and len(fx) > 0 and all(isinstance(x, str) for x in fx)
            if not (isinstance(fx, str) or is_seq):
                raise ValueError(f"bad fixture for {tool} in task {tid}")
        raw_turns = t.get("turns", [])
        if not isinstance(raw_turns, list) or not all(isinstance(x, str) and x for x in raw_turns):
            raise ValueError(f"bad turns in task {tid}")
        answer_has, answer_not = t.get("answer_has", []), t.get("answer_not", [])
        for key, value in (("answer_has", answer_has), ("answer_not", answer_not)):
            if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
                raise ValueError(f"bad {key} in task {tid}")
        tasks.append(Task(id=tid, request=req, expect=expect, forbid=forbid, confirm=confirm,
                          outcomes=outcomes, fixtures=fixtures, max_calls=int(t.get("max_calls", 6)),
                          category=category, lang=lang, turns=list(raw_turns),
                          answer_has=answer_has, answer_not=answer_not))
    return tasks


def dry_tools(fixtures: dict[str, str | list[str]]) -> dict[str, Tool]:
    """A copy of the registry where every tool returns its fixture or a dry-run note.

    A pure tool without a fixture calls the real fn instead: calculate must really calculate in the eval."""
    out = {}
    counters: dict[str, int] = {}  # fresh per call to dry_tools: two tasks never share sequence state
    for name, real in TOOLS.items():
        def fn(_name=name, _real=real, **kwargs):
            fx = fixtures.get(_name)
            if isinstance(fx, list):
                i = counters.get(_name, 0)
                counters[_name] = i + 1
                return fx[min(i, len(fx) - 1)] if fx else f"ok (dry run of {_name})"
            if fx is not None:
                return fx
            if _real.pure:
                return _real.fn(**kwargs)
            return f"ok (dry run of {_name})"
        out[name] = Tool(name=real.name, description=real.description, params=real.params,
                         required=real.required, tier=real.tier, fn=fn, tier_for=real.tier_for,
                         pure=real.pure)
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
    lang: str = "?"  # language of the answer (answer_language), "?" when unsure
    want_lang: str = "nl"  # the task's expected language ("nl", "en" or "any")
    forbid: list[str] = field(default_factory=list)  # the task's forbid list, for refused_attempts
    request: str = ""  # the task's first request, shown in the report's failed-task section
    turns: list[str] = field(default_factory=list)  # later requests of the same conversation
    answer_checked: bool = False  # the task had answer_has or answer_not checks


def run_task(llm, task: Task, journal_dir=None) -> Result:
    """Run one eval task through the real agent loop with dry-run tools and grade it from the journal."""
    if journal_dir is None:
        journal_dir = tempfile.mkdtemp()
    path = Path(journal_dir) / f"{task.id}.jsonl"
    agent = Agent(llm, lambda q: task.confirm == "yes", Journal(path), dry_tools(task.fixtures))
    start = time.perf_counter()
    answer_text = agent.handle(task.request)
    for turn in task.turns:
        answer_text = agent.handle(turn)
    seconds = time.perf_counter() - start

    calls, kinds, confirmations, confirmed_ok, gave_up = [], [], 0, 0, False
    model_error = False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "tool":
            calls.append((e["tool"], e["outcome"]))
            kinds.append(e.get("error_kind"))
            if e.get("tier") == CONFIRM:
                confirmations += 1
                if e["outcome"] == "ok":
                    confirmed_ok += 1
        elif e.get("event") == "gave_up":
            gave_up = True
        elif e.get("event") == "llm_error":
            model_error = True

    reasons = []
    called = {name for name, _ in calls}
    for name in task.expect:
        if name not in called:
            reasons.append(f"missing call: {name}")
    for name in task.forbid:
        # A forbidden call counts when it reached the tool: outcome ok, or an error that is not bad_args.
        if any(n == name and (o == "ok" or (o == "error" and k != "bad_args"))
               for (n, o), k in zip(calls, kinds, strict=True)):
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
    if model_error:
        # once per task, even when several turns failed on the model
        reasons.append("model error")
    if task.answer_has or task.answer_not:
        norm = _norm_answer(answer_text)
        if task.answer_has and not any(_norm_answer(s) in norm for s in task.answer_has):
            reasons.append(f"answer lacks any of: {', '.join(task.answer_has)}")
        for s in task.answer_not:
            if _norm_answer(s) in norm:
                reasons.append(f"answer says: {s}")

    return Result(id=task.id, passed=not reasons, calls=calls, confirmations=confirmations,
                  seconds=seconds, answer=answer_text, reasons=reasons, category=task.category,
                  confirm=task.confirm, confirmed_ok=confirmed_ok, lang=answer_language(answer_text),
                  want_lang=task.lang, forbid=task.forbid, request=task.request, turns=list(task.turns),
                  answer_checked=bool(task.answer_has or task.answer_not))


# Stopwords for answer_language: very common words per language that the other two do not share. A word
# spelled the same in two languages (is, open, in) goes in neither set.
_NL_WORDS = frozenset({"ik", "je", "het", "een", "niet", "dat", "op", "naar", "wil", "kan", "maar",
                       "nog", "even", "zelf", "staat", "heb", "geen", "wat", "deze", "ook", "al",
                       "dan", "er", "met", "voor", "bij"})
_EN_WORDS = frozenset({"i", "you", "the", "it's", "not", "can't", "this", "that", "to", "want",
                       "but", "still", "just", "have", "there", "what", "window", "am", "are",
                       "were", "been", "will", "would", "could"})
_DE_WORDS = frozenset({"ich", "du", "das", "nicht", "ist", "kann", "aber", "noch", "selbst",
                       "habe", "kein", "musst", "leider", "bitte", "wirklich", "es", "mir",
                       "dich", "bin", "bist", "sind", "war", "waren", "wird", "werden", "muss"})


def answer_language(text: str) -> str:
    """Which of Dutch, English or German this text is in: 'nl', 'en' or 'de'; '?' when unsure."""
    words = re.findall(r"[a-zà-ÿ']+", text.lower())
    hits = {"nl": sum(1 for w in words if w in _NL_WORDS),
            "en": sum(1 for w in words if w in _EN_WORDS),
            "de": sum(1 for w in words if w in _DE_WORDS)}
    best = max(hits.values())
    winners = [lang for lang, n in hits.items() if n == best]
    if sum(hits.values()) < 2 or len(winners) > 1:
        return "?"
    return winners[0]


def _wrong_language(r: Result) -> bool:
    """True when r answered in a language its task did not want ('any' tasks and unsure answers never count)."""
    return r.want_lang != "any" and r.lang != "?" and r.lang != r.want_lang


def _norm_answer(text: str) -> str:
    """Casefold with non-breaking spaces turned into normal ones, for answer_has/answer_not matching."""
    return text.replace("\u00a0", " ").casefold()


def _answer_wrong(r: Result) -> bool:
    """True when the last answer failed its task's answer_has or answer_not checks."""
    return any(reason.startswith(("answer lacks", "answer says")) for reason in r.reasons)


def summarize(results: list[Result], repeat: int = 1) -> dict:
    n = len(results)
    if not results:
        s = {"tasks": 0, "passed": 0, "success_rate": 0.0, "median_seconds": 0.0,
             "confirmations_per_task": 0.0, "failed": [], "by_category": {},
             "safety": {"forbidden_ok": 0, "unknown_tools": 0, "never_asked": 0, "refused_attempts": 0},
             "language": {"wrong": 0, "unknown": 0}, "answers": {"checked": 0, "wrong": 0}}
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
                     "refused_attempts": sum(1 for r in results for t, o in r.calls
                                             if o == "refused" and t in r.forbid),
                     "unknown_tools": sum(1 for r in results for _t, o in r.calls if o == "unknown"),
                     "never_asked": sum(1 for r in results if r.confirm == "no" and r.confirmed_ok > 0)},
          "language": {"wrong": sum(1 for r in results if _wrong_language(r)),
                       "unknown": sum(1 for r in results if r.want_lang != "any" and r.lang == "?")},
          "answers": {"checked": sum(1 for r in results if r.answer_checked),
                      "wrong": sum(1 for r in results if _answer_wrong(r))}}
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
    # old JSON reports have no "answers" key; a missing one prints as 0/0
    answers = s.get("answers") or {}
    line = (f"{s['passed']}/{s['tasks']} passed ({s['success_rate'] * 100:.1f}%), "
            f"median {s['median_seconds']:.1f} s, {s['confirmations_per_task']:.2f} confirmations per task, "
            f"safety: {s['safety']['forbidden_ok']} forbidden, {s['safety']['refused_attempts']} refused attempts, "
            f"{s['safety']['unknown_tools']} unknown tools, language: {s['language']['wrong']} wrong, "
            f"answers: {answers.get('wrong', 0)}/{answers.get('checked', 0)} wrong")
    if s.get("repeat", 1) > 1:
        line += f", {s['pass_all']}/{len(s['per_task'])} pass every time"
    return line


def _category_table(s: dict) -> list[str]:
    lines = ["| Category | Passed | Rate |", "|---|---|---|"]
    for cat, c in s["by_category"].items():
        lines.append(f"| {cat} | {c['passed']}/{c['tasks']} | {c['success_rate'] * 100:.1f}% |")
    return lines


def write_report(results: list[Result], out_dir, stamp: str, model: str = "", repeat: int = 1,
                 partial: bool = False) -> tuple[Path, Path]:
    """Write {stamp}.json and {stamp}.md into out_dir; returns (json path, md path).

    partial marks a filtered run over a subset of the tasks; such reports never become the headline."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    s = summarize(results, repeat=repeat)
    json_path = out_dir / f"{stamp}.json"
    json_path.write_text(json.dumps({"stamp": stamp, "model": model or "unknown", "partial": partial,
                                     "summary": s, "results": [asdict(r) for r in results]}, indent=2),
                         encoding="utf-8")
    lines = [f"# Iris eval {stamp}", "", f"Model: {model or 'unknown'}", "", _summary_line(s), ""]
    lines += _category_table(s)
    lines += ["", "| Task | Result | Calls | Seconds | Reasons |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.id} | {'pass' if r.passed else 'FAIL'} | "
                     f"{', '.join(f'{t}:{o}' for t, o in r.calls) or '-'} | {r.seconds:.1f} | "
                     f"{' ; '.join(r.reasons) or '-'} |")
    lines += ["", "## Failed tasks"]
    failed = [r for r in results if not r.passed]
    for r in failed:
        lines.append(f"- {r.id}: {r.request}")
        lines += [f"  then: {t}" for t in r.turns]
    if not failed:
        lines.append("none")
    lines += ["", "## Wrong language"]
    wrong = [r for r in results if _wrong_language(r)]
    lines += ([f"- {r.id}: expected {r.want_lang}, got {r.lang}: {r.answer[:80]}" for r in wrong] or ["none"])
    lines += ["", "## Wrong answers"]
    wrong_answers = [r for r in results if _answer_wrong(r)]
    lines += ([f'- {r.id}: {"; ".join(r.reasons)}: "{r.answer[:120]}"' for r in wrong_answers] or ["none"])
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


def _num_or(value, fallback):
    """value when it is an int/float (bools do not count), else the computed fallback."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return fallback


def full_reports(results_dir) -> list[Path]:
    """The *.json reports in results_dir, sorted by name, without the partial ones.

    A report whose JSON says "partial": true was a filtered run and never counts; a report
    without the key, or one that cannot be read as JSON, counts as full (history() copes
    with broken files)."""
    d = Path(results_dir)
    if not d.is_dir():
        return []
    out: list[Path] = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out.append(f)
            continue
        if isinstance(data, dict) and data.get("partial") is True:
            continue
        out.append(f)
    return out


def history(results_dir, last: int = 10) -> dict:
    """Read-only trend over the newest full reports in results_dir; never writes anything there.

    Partial (filtered) runs are not listed. A report whose "results" is not a list is skipped
    (counted in "skipped"); summary numbers that are not int/float fall back to values computed from results."""
    out: dict = {"runs": [], "tasks": {}, "flaky": [], "skipped": 0}
    d = Path(results_dir)
    if not d.is_dir():
        return out
    files = full_reports(results_dir)[-last:] if last > 0 else []
    per_task: dict[str, list[tuple[int, int]]] = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out["skipped"] += 1
            continue
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            out["skipped"] += 1
            continue
        results = [r for r in data["results"] if isinstance(r, dict) and "id" in r]
        s = data.get("summary")
        if not isinstance(s, dict):
            s = {}
        passed = _num_or(s.get("passed"), sum(1 for r in results if r.get("passed")))
        tasks_n = _num_or(s.get("tasks"), len(results))
        rate = _num_or(s.get("success_rate"), round(passed / tasks_n, 3) if tasks_n else 0.0)
        lang = s.get("language")
        ans = s.get("answers")
        awrong = ans.get("wrong") if isinstance(ans, dict) else None
        answers_wrong = int(awrong) if isinstance(awrong, (int, float)) and not isinstance(awrong, bool) else None
        out["runs"].append({"stamp": data.get("stamp", f.stem), "model": data.get("model", ""),
                            "passed": passed, "tasks": tasks_n, "success_rate": rate,
                            "language_wrong": lang.get("wrong") if isinstance(lang, dict) else None,
                            "answers_wrong": answers_wrong})
        by: dict[str, list[bool]] = {}
        for r in results:
            by.setdefault(r["id"], []).append(bool(r.get("passed")))
        for tid, flags in sorted(by.items()):
            per_task.setdefault(tid, []).append((sum(flags), len(flags)))
    for tid, entries in sorted(per_task.items()):
        p = sum(x[0] for x in entries)
        t = sum(x[1] for x in entries)
        out["tasks"][tid] = {"passed": p, "total": t, "per_run": [f"{a}/{b}" for a, b in entries]}
    flaky: list[tuple[float, str]] = []
    for tid, v in out["tasks"].items():
        if v["passed"] < v["total"]:
            flaky.append((v["passed"] / v["total"], tid))
    out["flaky"] = [tid for _, tid in sorted(flaky)]
    return out


def print_history(results_dir, last: int) -> int:
    """Print the trend over the newest saved runs (read only)."""
    if not Path(results_dir).is_dir():
        print("no eval results")
        return 1
    h = history(results_dir, last=last)
    for r in h["runs"]:
        rate = _num_or(r.get("success_rate"), round(r["passed"] / r["tasks"], 3) if r["tasks"] else 0.0)
        line = f"{r['stamp']}  {r['passed']}/{r['tasks']}  {rate * 100:.1f}%"
        if r["language_wrong"] is not None:
            line += f"  language {r['language_wrong']} wrong"
        if r["answers_wrong"] is not None:
            line += f"  answers {r['answers_wrong']} wrong"
        print(line)
    print()
    print(f"Flaky tasks (last {last} runs):")
    for tid in h["flaky"]:
        t = h["tasks"][tid]
        print(f"{tid:<24} {t['passed']}/{t['total']}  {' '.join(t['per_run'])}")
    if not h["flaky"]:
        print("none")
    return 0


def publish(results_dir, readme_path) -> int:
    """Replace the eval block in the README with the newest full report from results_dir."""
    files = full_reports(results_dir)
    if not files:
        print(f"no full reports in {results_dir}")
        return 2
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    s = data["summary"]
    st = data["stamp"]  # YYYYMMDD-HHMMSS
    when = f"{st[:4]}-{st[4:6]}-{st[6:8]} {st[9:11]}:{st[11:13]}"
    # blank lines around the table: GitHub only renders a table that does not touch a paragraph
    block = [f"Run of {when} on `{data['model']}`: {_summary_line(s)}.", ""] + _category_table(s) + [""]

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
    ap.add_argument("command", nargs="?", default="run", choices=("run", "publish", "history"))
    ap.add_argument("--tasks", default="eval/tasks.toml")
    ap.add_argument("--out", default="eval/results")
    ap.add_argument("--only", action="append", default=None, metavar="ID")
    ap.add_argument("--category", action="append", default=None, metavar="NAME")
    ap.add_argument("--failed-from", default=None, metavar="PATH")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--results", default="eval/results")
    ap.add_argument("--readme", default="README.md")
    ap.add_argument("--last", type=int, default=10)
    a = ap.parse_args(argv)

    if a.command == "publish":
        return publish(a.results, a.readme)

    if a.command == "history":
        return print_history(a.results, a.last)

    tasks = load_tasks(Path(a.tasks))
    if a.only:
        ids = [t.id for t in tasks]
        for pat in a.only:
            if not any(fnmatch.fnmatchcase(tid, pat) for tid in ids):
                print(f"unknown task: {pat}")
                return 2
    if a.category:
        known_cats = {t.category for t in tasks}
        for cat in a.category:
            if cat not in known_cats:
                print(f"unknown category: {cat}")
                return 2
    failed_ids = None
    if a.failed_from:
        failed_path = Path(a.failed_from)
        try:
            old = json.loads(failed_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"cannot read {failed_path}")
            return 2
        known_ids = {t.id for t in tasks}
        failed_ids, gone = [], []
        results = old.get("results") if isinstance(old, dict) else None
        for r in results if isinstance(results, list) else []:  # a --repeat report lists each id several times
            rid = r.get("id") if isinstance(r, dict) else None
            if not isinstance(rid, str):
                continue
            if rid not in known_ids:
                if rid not in gone:
                    gone.append(rid)
                    print(f"skipped (no longer a task): {rid}")
            elif not r.get("passed") and rid not in failed_ids:
                failed_ids.append(rid)
        if not failed_ids:
            print(f"nothing failed in {failed_path.name}")
            return 0
    partial = bool(a.only or a.category or failed_ids is not None)
    if partial:
        patterns = a.only if a.only else None
        wanted_cats = set(a.category) if a.category else None
        keep = set(failed_ids) if failed_ids is not None else None
        tasks = [t for t in tasks
                 if (patterns is None or any(fnmatch.fnmatchcase(t.id, p) for p in patterns))
                 and (wanted_cats is None or t.category in wanted_cats)
                 and (keep is None or t.id in keep)]

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
    _, md_path = write_report(results, a.out, stamp, model=effective_model(), repeat=a.repeat, partial=partial)
    print(_summary_line(s))
    print(f"Report: {md_path}")
    return 0
