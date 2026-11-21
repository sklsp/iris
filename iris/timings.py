"""Summarise the timing entries the agent writes to its journal, one per request."""

import argparse
import json
import math
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any


def read_timings(
    path: Path, last: int = 50, event: str = "timing", since: str | None = None, model: str | None = None
) -> list[dict[str, Any]]:
    """The last `last` entries with the given event of a JSON-lines journal; bad lines are skipped, no file gives [].

    Since keeps only entries whose t is on or after that date (a string compare on the first 10 characters is enough);
    model keeps only timing entries made by that model. Both apply before last counts."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:  # a torn line (a crash mid-write) is skipped, never fatal
            continue
        if isinstance(entry, dict) and entry.get("event") == event:
            if since is not None and str(entry.get("t", ""))[:10] < since:  # t is ISO, so the string compare works
                continue
            if model is not None and entry.get("model") != model:
                continue
            entries.append(entry)
    return entries[-last:] if last > 0 else []  # entries[-0:] would be all of them


def _p90(values: list[float]) -> float:
    """Nearest rank: the sorted value at index ceil(0.9 * n) - 1."""
    ordered = sorted(values)
    return ordered[math.ceil(0.9 * len(ordered)) - 1]


def summarize(entries: list[dict[str, Any]], stt: list[dict[str, Any]] | None = None,
              tts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Medians and p90s of request, model and tool time, the model's share of all time, the slowest tools - and, when
    given, the speech-recognition times that preceded those requests and the first-sound times of their answers."""
    result: dict[str, object]
    if not entries:
        result = {"count": 0}
    else:
        by_tool: dict[str, float] = {}
        for e in entries:
            name = e.get("slowest_tool")
            if name:
                by_tool[name] = max(by_tool.get(name, 0.0), e["slowest_tool_ms"])
        slowest = sorted(by_tool.items(), key=lambda pair: -pair[1])[:5]
        total_ms = sum(e["ms"] for e in entries)
        result = {
            "count": len(entries),
            "ms_median": median([e["ms"] for e in entries]),
            "ms_p90": _p90([e["ms"] for e in entries]),
            "llm_ms_median": median([e["llm_ms"] for e in entries]),
            "llm_ms_p90": _p90([e["llm_ms"] for e in entries]),
            "tools_ms_median": median([e["tools_ms"] for e in entries]),
            "tools_ms_p90": _p90([e["tools_ms"] for e in entries]),
            "llm_share": round(sum(e["llm_ms"] for e in entries) / total_ms, 2) if total_ms else 0.0,
            "slowest_tools": slowest,
        }
    if stt:  # the voice loop logs one of these before each request it hears
        result["stt_ms_median"] = median([e["ms"] for e in stt])
        result["stt_ms_p90"] = _p90([e["ms"] for e in stt])
        result["stt_count"] = len(stt)
    if tts:  # the voice loop logs one of these when an answer's first sound starts
        result["tts_ms_median"] = median([e["ms"] for e in tts])
        result["tts_ms_p90"] = _p90([e["ms"] for e in tts])
        result["tts_count"] = len(tts)
    return result


def format_summary(summary: dict[str, Any]) -> list[str]:
    """Short plain-English lines for `iris timings`; seconds with one decimal."""
    if summary["count"] == 0:
        return ["no timed requests yet"]
    n = summary["count"]
    model = f"model: median {summary['llm_ms_median'] / 1000:.1f} s ({round(summary['llm_share'] * 100)}% of all time)"
    lines = [
        f"{n} {'request' if n == 1 else 'requests'}: median {summary['ms_median'] / 1000:.1f} s, "
        f"p90 {summary['ms_p90'] / 1000:.1f} s",
        model,
        f"tools: median {summary['tools_ms_median'] / 1000:.1f} s",
    ]
    if summary.get("stt_count"):
        lines.insert(
            1,
            f"speech recognition: median {summary['stt_ms_median'] / 1000:.1f} s, "
            f"p90 {summary['stt_ms_p90'] / 1000:.1f} s",
        )
    if summary.get("tts_count"):
        lines.append(
            f"speech output: first sound median {summary['tts_ms_median'] / 1000:.1f} s, "
            f"p90 {summary['tts_ms_p90'] / 1000:.1f} s"
        )
    if summary["slowest_tools"]:
        slow = ", ".join(f"{name} {ms / 1000:.1f} s" for name, ms in summary["slowest_tools"])
        lines.append("slowest tools: " + slow)
    return lines


def tool_stats(entries: list[dict[str, Any]]) -> list[tuple[str, int, float]]:
    """(tool, calls, median ms) per tool from its tool_timing entries; slowest first, then name."""
    by_tool: dict[str, list[float]] = {}
    for e in entries:
        by_tool.setdefault(e["tool"], []).append(float(e["ms"]))
    return sorted(((name, len(ms), median(ms)) for name, ms in by_tool.items()), key=lambda row: (-row[2], row[0]))


def format_tool_stats(stats: list[tuple[str, int, float]]) -> list[str]:
    """One line per tool from tool_stats; seconds with one decimal."""
    if not stats:
        return ["no timed tool calls yet"]
    return [
        f"{name}: {calls} {'call' if calls == 1 else 'calls'}, median {ms / 1000:.1f} s" for name, calls, ms in stats
    ]


def model_stats(entries: list[dict[str, Any]]) -> list[tuple[str, int, float, float]]:
    """(model, requests, median ms, the model's share of all time in that group) per model from timing entries;
    slowest median first, then name. Entries without a model are grouped as unknown."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        groups.setdefault(e.get("model") or "unknown", []).append(e)
    rows: list[tuple[str, int, float, float]] = []
    for name, es in groups.items():
        total_ms = sum(x["ms"] for x in es)
        share = round(sum(x["llm_ms"] for x in es) / total_ms, 2) if total_ms else 0.0
        rows.append((name, len(es), median([x["ms"] for x in es]), share))
    return sorted(rows, key=lambda row: (-row[2], row[0]))


def format_model_stats(stats: list[tuple[str, int, float, float]]) -> list[str]:
    """One line per model from model_stats; seconds with one decimal."""
    if not stats:
        return ["no timed requests yet"]
    return [
        f"{name}: {n} requests, median {ms / 1000:.1f} s (model {round(share * 100)}%)" for name, n, ms, share in stats
    ]


def _since_date(value: str) -> str:
    """The --since date as YYYY-MM-DD; argparse rejects anything else, so a typo never filters silently."""
    parts = value.split("-")
    if (
        len(parts) != 3
        or not (len(parts[0]) == 4 and len(parts[1]) == 2 and len(parts[2]) == 2)
        or not all(p.isdigit() for p in parts)
    ):
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}")
    try:
        date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a real calendar date: {value!r}") from None
    return value


def timings_main(argv: list[str]) -> int:
    """`iris timings [--last N] [--journal PATH] [--json] [--by-tool] [--by-model] [--since DATE] [--model NAME]`:
    how long recent requests took, from the journal."""
    from iris.dashboard import journal_path  # IRIS_JOURNAL, the same file the dashboard reads

    ap = argparse.ArgumentParser(prog="iris timings")
    ap.add_argument("--last", type=int, default=50)
    ap.add_argument("--journal", type=Path, default=journal_path())
    ap.add_argument("--json", action="store_true")  # machine-readable summary for scripts and bug reports
    ap.add_argument("--by-tool", action="store_true")  # per-tool medians from the tool_timing entries
    ap.add_argument("--by-model", action="store_true")  # one line per model, so models can be compared side by side
    ap.add_argument(
        "--since",
        type=_since_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="keep only entries on or after this date; applies to every entry kind",
    )
    ap.add_argument(
        "--model",
        default=None,
        metavar="NAME",
        help="keep only timing entries made by that model (stt, tts and tool_timing have no model)",
    )
    a = ap.parse_args(argv)
    if a.by_tool:
        stats = tool_stats(read_timings(a.journal, last=a.last, event="tool_timing", since=a.since))
        if a.json:
            print(
                json.dumps(
                    [{"tool": name, "calls": calls, "median_ms": ms} for name, calls, ms in stats],
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            for line in format_tool_stats(stats):
                print(line)
        return 0
    if a.by_model:
        model_rows = model_stats(read_timings(a.journal, last=a.last, since=a.since, model=a.model))
        if a.json:
            print(
                json.dumps(
                    [
                        {"model": name, "requests": n, "median_ms": ms, "llm_share": share}
                        for name, n, ms, share in model_rows
                    ],
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            for line in format_model_stats(model_rows):
                print(line)
        return 0
    stt = read_timings(a.journal, last=a.last, event="stt", since=a.since)  # the voice loop's speech-recognition times
    tts = read_timings(a.journal, last=a.last, event="tts", since=a.since)  # when each answer's first sound started
    summary = summarize(read_timings(a.journal, last=a.last, since=a.since, model=a.model), stt=stt, tts=tts)
    if a.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        for line in format_summary(summary):
            print(line)
    return 0
