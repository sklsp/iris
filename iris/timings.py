"""Summarise the timing entries the agent writes to its journal, one per request."""
import argparse
import json
import math
from pathlib import Path
from statistics import median


def read_timings(path: Path, last: int = 50, event: str = "timing") -> list[dict]:
    """The last `last` entries with the given event of a JSON-lines journal; bad lines are skipped, no file gives []."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:  # a torn line (a crash mid-write) is skipped, never fatal
            continue
        if isinstance(entry, dict) and entry.get("event") == event:
            entries.append(entry)
    return entries[-last:] if last > 0 else []  # entries[-0:] would be all of them


def _p90(values: list[float]) -> float:
    """Nearest rank: the sorted value at index ceil(0.9 * n) - 1."""
    ordered = sorted(values)
    return ordered[math.ceil(0.9 * len(ordered)) - 1]


def summarize(entries: list[dict], stt: list[dict] | None = None, tts: list[dict] | None = None) -> dict:
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


def format_summary(summary: dict) -> list[str]:
    """Short plain-English lines for `iris timings`; seconds with one decimal."""
    if summary["count"] == 0:
        return ["no timed requests yet"]
    n = summary["count"]
    model = f"model: median {summary['llm_ms_median'] / 1000:.1f} s ({round(summary['llm_share'] * 100)}% of all time)"
    lines = [f"{n} {'request' if n == 1 else 'requests'}: median {summary['ms_median'] / 1000:.1f} s, "
             f"p90 {summary['ms_p90'] / 1000:.1f} s",
             model,
             f"tools: median {summary['tools_ms_median'] / 1000:.1f} s"]
    if summary.get("stt_count"):
        lines.insert(1, f"speech recognition: median {summary['stt_ms_median'] / 1000:.1f} s, "
                        f"p90 {summary['stt_ms_p90'] / 1000:.1f} s")
    if summary.get("tts_count"):
        lines.append(f"speech output: first sound median {summary['tts_ms_median'] / 1000:.1f} s, "
                     f"p90 {summary['tts_ms_p90'] / 1000:.1f} s")
    if summary["slowest_tools"]:
        slow = ", ".join(f"{name} {ms / 1000:.1f} s" for name, ms in summary["slowest_tools"])
        lines.append("slowest tools: " + slow)
    return lines


def timings_main(argv: list[str]) -> int:
    """`iris timings [--last N] [--journal PATH]`: how long recent requests took, from the journal."""
    from iris.dashboard import journal_path  # IRIS_JOURNAL, the same file the dashboard reads

    ap = argparse.ArgumentParser(prog="iris timings")
    ap.add_argument("--last", type=int, default=50)
    ap.add_argument("--journal", type=Path, default=journal_path())
    a = ap.parse_args(argv)
    stt = read_timings(a.journal, last=a.last, event="stt")  # the voice loop's speech-recognition times
    tts = read_timings(a.journal, last=a.last, event="tts")  # when each answer's first sound started
    for line in format_summary(summarize(read_timings(a.journal, last=a.last), stt=stt, tts=tts)):
        print(line)
    return 0
