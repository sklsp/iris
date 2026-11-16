import json

from iris.timings import format_summary, read_timings, summarize


def e(ms, llm=0, tools=0, tool=None, tool_ms=0):
    return {"event": "timing", "ms": ms, "llm_ms": llm, "tools_ms": tools,
            "slowest_tool": tool, "slowest_tool_ms": tool_ms}


def write(tmp_path, entries, extra_lines=()):
    lines = [json.dumps(x) for x in entries] + list(extra_lines)
    (tmp_path / "j.jsonl").write_text("\n".join(lines), encoding="utf-8")
    return tmp_path / "j.jsonl"


def test_an_empty_or_missing_file_gives_no_entries(tmp_path):
    assert read_timings(write(tmp_path, [])) == []
    assert read_timings(tmp_path / "nope.jsonl") == []  # a missing file gives []
    assert summarize([]) == {"count": 0}
    assert format_summary({"count": 0}) == ["no timed requests yet"]


def test_bad_lines_and_other_events_are_skipped(tmp_path):
    path = write(tmp_path, [e(10), e(20)], extra_lines=("not json", '{"event": "request", "text": "hi"}', "{broken"))
    assert read_timings(path) == [{"event": "timing", "ms": 10, "llm_ms": 0, "tools_ms": 0,
                                   "slowest_tool": None, "slowest_tool_ms": 0},
                                  {"event": "timing", "ms": 20, "llm_ms": 0, "tools_ms": 0,
                                   "slowest_tool": None, "slowest_tool_ms": 0}]


def test_p90_on_ten_values_is_the_ninth_smallest(tmp_path):
    entries = [e(i * 10) for i in range(1, 11)]  # ms: 10 .. 100
    s = summarize(entries)
    assert s["count"] == 10
    assert s["ms_median"] == 55  # the mean of the two middle values
    assert s["ms_p90"] == 90     # nearest rank: sorted[ceil(0.9 * 10) - 1] = sorted[8]
    assert s["llm_ms_p90"] == 0 and s["tools_ms_median"] == 0


def test_the_model_share_is_sum_llm_over_sum_ms(tmp_path):
    entries = [e(100, llm=90), e(300, llm=210)]
    assert summarize(entries)["llm_share"] == 0.75
    assert summarize([e(0)])["llm_share"] == 0.0  # ms sums to zero: no division


def test_slowest_tools_take_the_max_per_name_highest_first_capped_at_five(tmp_path):
    entries = [e(100, tool="browser_open", tool_ms=4100), e(200), e(300, tool="click", tool_ms=900),
               e(400, tool="type_text", tool_ms=500), e(500, tool="read_file", tool_ms=700),
               e(600, tool="open_app", tool_ms=80), e(700, tool="browser_open", tool_ms=6200),
               e(800, tool="web_search", tool_ms=2100)]
    assert summarize(entries)["slowest_tools"] == [("browser_open", 6200), ("web_search", 2100),
                                                   ("click", 900), ("read_file", 700), ("type_text", 500)]


def test_format_summary_lines_for_one_fixed_input():
    summary = {"count": 20, "ms_median": 3400.0, "ms_p90": 9100.0,
               "llm_ms_median": 2900.0, "llm_ms_p90": 8000.0,
               "tools_ms_median": 300.0, "tools_ms_p90": 1500.0,
               "llm_share": 0.85,
               "slowest_tools": [("browser_open", 6200), ("web_search", 2100)]}
    assert format_summary(summary) == ["20 requests: median 3.4 s, p90 9.1 s",
                                       "model: median 2.9 s (85% of all time)",
                                       "tools: median 0.3 s",
                                       "slowest tools: browser_open 6.2 s, web_search 2.1 s"]
