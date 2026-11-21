import json

import pytest

from iris.timings import (
    format_model_stats,
    format_summary,
    format_tool_stats,
    model_stats,
    read_timings,
    summarize,
    timings_main,
    tool_stats,
)


def e(ms, llm=0, tools=0, tool=None, tool_ms=0):
    return {
        "event": "timing",
        "ms": ms,
        "llm_ms": llm,
        "tools_ms": tools,
        "slowest_tool": tool,
        "slowest_tool_ms": tool_ms,
    }


def tt(tool, ms):
    return {"event": "tool_timing", "tool": tool, "ms": ms}


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
    assert read_timings(path) == [
        {"event": "timing", "ms": 10, "llm_ms": 0, "tools_ms": 0, "slowest_tool": None, "slowest_tool_ms": 0},
        {"event": "timing", "ms": 20, "llm_ms": 0, "tools_ms": 0, "slowest_tool": None, "slowest_tool_ms": 0},
    ]


def test_p90_on_ten_values_is_the_ninth_smallest(tmp_path):
    entries = [e(i * 10) for i in range(1, 11)]  # ms: 10 .. 100
    s = summarize(entries)
    assert s["count"] == 10
    assert s["ms_median"] == 55  # the mean of the two middle values
    assert s["ms_p90"] == 90  # nearest rank: sorted[ceil(0.9 * 10) - 1] = sorted[8]
    assert s["llm_ms_p90"] == 0 and s["tools_ms_median"] == 0


def test_the_model_share_is_sum_llm_over_sum_ms(tmp_path):
    entries = [e(100, llm=90), e(300, llm=210)]
    assert summarize(entries)["llm_share"] == 0.75
    assert summarize([e(0)])["llm_share"] == 0.0  # ms sums to zero: no division


def test_slowest_tools_take_the_max_per_name_highest_first_capped_at_five(tmp_path):
    entries = [
        e(100, tool="browser_open", tool_ms=4100),
        e(200),
        e(300, tool="click", tool_ms=900),
        e(400, tool="type_text", tool_ms=500),
        e(500, tool="read_file", tool_ms=700),
        e(600, tool="open_app", tool_ms=80),
        e(700, tool="browser_open", tool_ms=6200),
        e(800, tool="web_search", tool_ms=2100),
    ]
    assert summarize(entries)["slowest_tools"] == [
        ("browser_open", 6200),
        ("web_search", 2100),
        ("click", 900),
        ("read_file", 700),
        ("type_text", 500),
    ]


def test_format_summary_lines_for_one_fixed_input():
    summary = {
        "count": 20,
        "ms_median": 3400.0,
        "ms_p90": 9100.0,
        "llm_ms_median": 2900.0,
        "llm_ms_p90": 8000.0,
        "tools_ms_median": 300.0,
        "tools_ms_p90": 1500.0,
        "llm_share": 0.85,
        "slowest_tools": [("browser_open", 6200), ("web_search", 2100)],
    }
    assert format_summary(summary) == [
        "20 requests: median 3.4 s, p90 9.1 s",
        "model: median 2.9 s (85% of all time)",
        "tools: median 0.3 s",
        "slowest tools: browser_open 6.2 s, web_search 2.1 s",
    ]


def test_timings_main_prints_the_summary_lines(tmp_path, capsys):
    path = write(tmp_path, [e(1000, llm=900), e(2000, llm=1500, tools=600, tool="click", tool_ms=600)])
    assert timings_main(["--journal", str(path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "2 requests: median 1.5 s, p90 2.0 s",
        "model: median 1.2 s (80% of all time)",
        "tools: median 0.3 s",
        "slowest tools: click 0.6 s",
    ]


def test_timings_main_last_uses_only_the_last_entries(tmp_path, capsys):
    path = write(tmp_path, [e(100), e(200), e(300), e(500)])
    assert timings_main(["--last", "2", "--journal", str(path)]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first == "2 requests: median 0.4 s, p90 0.5 s"  # the last two entries only (300 and 500)


def test_timings_main_missing_journal_says_no_timed_requests(tmp_path, capsys):
    assert timings_main(["--journal", str(tmp_path / "missing.jsonl")]) == 0
    assert capsys.readouterr().out.splitlines() == ["no timed requests yet"]


def test_read_timings_with_last_zero_reads_nothing(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text('{"event": "timing", "ms": 100, "llm_ms": 80, "tools_ms": 0}\n', encoding="utf-8")
    assert read_timings(path, last=0) == []


def s(ms):
    return {"event": "stt", "ms": ms}


def test_read_timings_reads_stt_entries_with_the_event_keyword(tmp_path):
    path = write(tmp_path, [e(1000), e(2000)], extra_lines=(json.dumps(s(640)), json.dumps(s(700))))
    assert read_timings(path) == [e(1000), e(2000)]  # the default is unchanged: timing entries only
    assert read_timings(path, event="stt") == [{"event": "stt", "ms": 640}, {"event": "stt", "ms": 700}]


def test_summarize_with_stt_entries_adds_their_medians_and_count():
    entries = [e(1000, llm=900), e(2000, llm=1500)]
    stt = [s(500), s(640), s(700)]

    summary = summarize(entries, stt=stt)

    assert summary["count"] == 2
    assert summary["stt_count"] == 3
    assert summary["stt_ms_median"] == 640
    assert summary["stt_ms_p90"] == 700  # nearest rank: sorted[ceil(0.9 * 3) - 1] = sorted[2]


def test_summarize_without_stt_has_no_stt_keys():
    summary = summarize([e(1000, llm=900)])

    assert "stt_count" not in summary and "stt_ms_median" not in summary and "stt_ms_p90" not in summary


def test_format_summary_puts_the_speech_line_right_after_the_first():
    summary = {
        "count": 2,
        "ms_median": 1500.0,
        "ms_p90": 2000.0,
        "llm_ms_median": 1200.0,
        "llm_ms_p90": 1500.0,
        "tools_ms_median": 300.0,
        "tools_ms_p90": 600.0,
        "llm_share": 0.8,
        "slowest_tools": [],
        "stt_count": 3,
        "stt_ms_median": 640.0,
        "stt_ms_p90": 700.0,
    }

    assert format_summary(summary) == [
        "2 requests: median 1.5 s, p90 2.0 s",
        "speech recognition: median 0.6 s, p90 0.7 s",
        "model: median 1.2 s (80% of all time)",
        "tools: median 0.3 s",
    ]


def test_timings_main_prints_the_speech_line_from_a_journal_with_both(tmp_path, capsys):
    path = write(
        tmp_path,
        [e(1000, llm=900), e(2000, llm=1500, tools=600, tool="click", tool_ms=600)],
        extra_lines=(json.dumps(s(500)), json.dumps(s(640)), json.dumps(s(700))),
    )

    assert timings_main(["--journal", str(path)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "2 requests: median 1.5 s, p90 2.0 s",
        "speech recognition: median 0.6 s, p90 0.7 s",
        "model: median 1.2 s (80% of all time)",
        "tools: median 0.3 s",
        "slowest tools: click 0.6 s",
    ]


def t(ms):
    return {"event": "tts", "ms": ms}


def test_read_timings_reads_tts_entries_with_the_event_keyword(tmp_path):
    path = write(tmp_path, [e(1000)], extra_lines=(json.dumps(t(420)), json.dumps(t(900))))
    assert read_timings(path) == [e(1000)]  # the default is unchanged: timing entries only
    assert read_timings(path, event="tts") == [{"event": "tts", "ms": 420}, {"event": "tts", "ms": 900}]


def test_summarize_with_tts_entries_adds_their_medians_and_count():
    entries = [e(1000, llm=900), e(2000, llm=1500)]
    tts = [t(420), t(900), t(300)]

    summary = summarize(entries, tts=tts)

    assert summary["count"] == 2
    assert summary["tts_count"] == 3
    assert summary["tts_ms_median"] == 420
    assert summary["tts_ms_p90"] == 900  # nearest rank: sorted[ceil(0.9 * 3) - 1] = sorted[2]


def test_summarize_without_tts_has_no_tts_keys():
    summary = summarize([e(1000, llm=900)])

    assert "tts_count" not in summary and "tts_ms_median" not in summary and "tts_ms_p90" not in summary


def test_format_summary_puts_the_speech_output_line_after_tools_before_slowest():
    summary = {
        "count": 2,
        "ms_median": 1500.0,
        "ms_p90": 2000.0,
        "llm_ms_median": 1200.0,
        "llm_ms_p90": 1500.0,
        "tools_ms_median": 300.0,
        "tools_ms_p90": 600.0,
        "llm_share": 0.8,
        "slowest_tools": [("click", 600)],
        "stt_count": 3,
        "stt_ms_median": 640.0,
        "stt_ms_p90": 700.0,
        "tts_count": 2,
        "tts_ms_median": 400.0,
        "tts_ms_p90": 900.0,
    }

    assert format_summary(summary) == [
        "2 requests: median 1.5 s, p90 2.0 s",
        "speech recognition: median 0.6 s, p90 0.7 s",
        "model: median 1.2 s (80% of all time)",
        "tools: median 0.3 s",
        "speech output: first sound median 0.4 s, p90 0.9 s",
        "slowest tools: click 0.6 s",
    ]


def test_timings_main_prints_the_speech_output_line_from_a_journal_with_all_three(tmp_path, capsys):
    path = write(
        tmp_path,
        [e(1000, llm=900), e(2000, llm=1500, tools=600, tool="click", tool_ms=600)],
        extra_lines=(
            json.dumps(s(500)),
            json.dumps(s(640)),
            json.dumps(s(700)),
            json.dumps(t(300)),
            json.dumps(t(400)),
            json.dumps(t(900)),
        ),
    )

    assert timings_main(["--journal", str(path)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "2 requests: median 1.5 s, p90 2.0 s",
        "speech recognition: median 0.6 s, p90 0.7 s",
        "model: median 1.2 s (80% of all time)",
        "tools: median 0.3 s",
        "speech output: first sound median 0.4 s, p90 0.9 s",
        "slowest tools: click 0.6 s",
    ]


def test_timings_main_json_prints_the_summary_dict(tmp_path, capsys):
    path = write(
        tmp_path,
        [e(1000, llm=900), e(2000, llm=1500, tools=600, tool="click", tool_ms=600)],
        extra_lines=(
            json.dumps(s(500)),
            json.dumps(s(640)),
            json.dumps(s(700)),
            json.dumps(t(300)),
            json.dumps(t(400)),
            json.dumps(t(900)),
        ),
    )

    assert timings_main(["--json", "--journal", str(path)]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "count": 2,
        "ms_median": 1500.0,
        "ms_p90": 2000,
        "llm_ms_median": 1200.0,
        "llm_ms_p90": 1500,
        "tools_ms_median": 300.0,
        "tools_ms_p90": 600,
        "llm_share": 0.8,
        "slowest_tools": [["click", 600]],
        "stt_count": 3,
        "stt_ms_median": 640,
        "stt_ms_p90": 700,
        "tts_count": 3,
        "tts_ms_median": 400,
        "tts_ms_p90": 900,
    }


def test_timings_main_json_on_a_missing_journal_prints_count_zero(tmp_path, capsys):
    assert timings_main(["--json", "--journal", str(tmp_path / "missing.jsonl")]) == 0

    out = capsys.readouterr().out
    assert json.loads(out) == {"count": 0}
    assert out == '{\n  "count": 0\n}\n'  # the same indent=2, sort_keys=True formatting as a real summary


def test_tool_stats_groups_by_tool_and_sorts_median_desc_then_name():
    entries = [
        tt("browser_open", 2300),
        tt("click", 500),
        tt("type_text", 900),
        tt("browser_open", 100),
        tt("type_text", 800),
        tt("web_search", 1200),
    ]

    assert tool_stats(entries) == [
        ("browser_open", 2, 1200.0),
        ("web_search", 1, 1200.0),
        ("type_text", 2, 850.0),
        ("click", 1, 500.0),
    ]


def test_tool_stats_on_no_entries_is_empty():
    assert tool_stats([]) == []


def test_format_tool_stats_lines_with_the_singular_form():
    stats = [("browser_open", 12, 2300.0), ("click", 1, 120.0)]

    assert format_tool_stats(stats) == ["browser_open: 12 calls, median 2.3 s", "click: 1 call, median 0.1 s"]


def test_format_tool_stats_empty_says_no_timed_tool_calls_yet():
    assert format_tool_stats([]) == ["no timed tool calls yet"]


def test_timings_main_by_tool_prints_one_line_per_tool(tmp_path, capsys):
    path = write(tmp_path, [e(1000)], extra_lines=(json.dumps(tt("browser_open", 2300)), json.dumps(tt("click", 500))))

    assert timings_main(["--by-tool", "--journal", str(path)]) == 0

    # the plain "timing" entry is ignored: only tool_timing entries count
    assert capsys.readouterr().out.splitlines() == ["browser_open: 1 call, median 2.3 s", "click: 1 call, median 0.5 s"]


def test_timings_main_by_tool_respects_last(tmp_path, capsys):
    path = write(tmp_path, [tt("click", 500), tt("click", 1500)])

    assert timings_main(["--by-tool", "--last", "1", "--journal", str(path)]) == 0

    assert capsys.readouterr().out.splitlines() == ["click: 1 call, median 1.5 s"]


def test_timings_main_by_tool_on_a_missing_journal_says_no_timed_tool_calls(tmp_path, capsys):
    assert timings_main(["--by-tool", "--journal", str(tmp_path / "missing.jsonl")]) == 0

    assert capsys.readouterr().out.splitlines() == ["no timed tool calls yet"]


def test_timings_main_by_tool_json_prints_the_objects(tmp_path, capsys):
    path = write(tmp_path, [tt("browser_open", 2300), tt("browser_open", 100), tt("click", 500)])

    assert timings_main(["--by-tool", "--json", "--journal", str(path)]) == 0

    assert json.loads(capsys.readouterr().out) == [
        {"tool": "browser_open", "calls": 2, "median_ms": 1200.0},
        {"tool": "click", "calls": 1, "median_ms": 500.0},
    ]


def test_since_keeps_entries_on_or_after_the_date(tmp_path, capsys):
    old = {
        "event": "timing",
        "ms": 1000,
        "llm_ms": 900,
        "tools_ms": 0,
        "slowest_tool": None,
        "slowest_tool_ms": 0,
        "t": "2026-11-17T23:59:59",
    }
    boundary = {
        "event": "timing",
        "ms": 2000,
        "llm_ms": 1600,
        "tools_ms": 0,
        "slowest_tool": None,
        "slowest_tool_ms": 0,
        "t": "2026-11-18T00:00:00",
    }
    new = {
        "event": "timing",
        "ms": 4000,
        "llm_ms": 3200,
        "tools_ms": 0,
        "slowest_tool": None,
        "slowest_tool_ms": 0,
        "t": "2026-11-18T09:30:00",
    }
    stt_old = {"event": "stt", "ms": 500, "t": "2026-11-17T10:00:01"}
    path = write(tmp_path, [old, boundary, new], extra_lines=(json.dumps(stt_old),))

    assert timings_main(["--since", "2026-11-18", "--journal", str(path)]) == 0

    # the old request and the old stt entry are dropped; an entry exactly on the date is kept
    assert capsys.readouterr().out.splitlines() == [
        "2 requests: median 3.0 s, p90 4.0 s",
        "model: median 2.4 s (80% of all time)",
        "tools: median 0.0 s",
    ]


def test_since_filters_before_last_counts(tmp_path):
    path = write(
        tmp_path,
        [
            {"event": "timing", "ms": 100, "t": "2026-11-17T08:00:00"},
            {"event": "timing", "ms": 200, "t": "2026-11-18T08:00:00"},
            {"event": "timing", "ms": 300, "t": "2026-11-19T08:00:00"},
        ],
    )

    assert read_timings(path, last=2, since="2026-11-18") == [
        {"event": "timing", "ms": 200, "t": "2026-11-18T08:00:00"},
        {"event": "timing", "ms": 300, "t": "2026-11-19T08:00:00"},
    ]


def test_since_applies_to_tool_timing_entries_too(tmp_path, capsys):
    path = write(
        tmp_path,
        [],
        extra_lines=(
            json.dumps({**tt("click", 500), "t": "2026-11-17T10:00:00"}),
            json.dumps({**tt("click", 1500), "t": "2026-11-18T10:00:00"}),
        ),
    )

    assert timings_main(["--by-tool", "--since", "2026-11-18", "--journal", str(path)]) == 0

    assert capsys.readouterr().out.splitlines() == ["click: 1 call, median 1.5 s"]


def test_a_bad_since_date_is_rejected_by_argparse(tmp_path, capsys):
    for bad in ("18-11-2026", "2026-13-40"):
        with pytest.raises(SystemExit) as exc:
            timings_main(["--since", bad, "--journal", str(tmp_path / "j.jsonl")])
        assert exc.value.code == 2

    err = capsys.readouterr().err
    assert "expected YYYY-MM-DD" in err and "not a real calendar date" in err


def test_model_keeps_only_timing_entries_of_that_model(tmp_path, capsys):
    qwen = {
        "event": "timing",
        "ms": 2000,
        "llm_ms": 1600,
        "tools_ms": 0,
        "slowest_tool": None,
        "slowest_tool_ms": 0,
        "model": "qwen-test",
    }
    gpt = {
        "event": "timing",
        "ms": 4000,
        "llm_ms": 3200,
        "tools_ms": 0,
        "slowest_tool": None,
        "slowest_tool_ms": 0,
        "model": "gpt-test",
    }
    stt = {"event": "stt", "ms": 500}
    path = write(tmp_path, [qwen, gpt], extra_lines=(json.dumps(stt),))

    assert timings_main(["--model", "qwen-test", "--journal", str(path)]) == 0

    # the other model's request is dropped; stt entries have no model and are kept
    assert capsys.readouterr().out.splitlines() == [
        "1 request: median 2.0 s, p90 2.0 s",
        "speech recognition: median 0.5 s, p90 0.5 s",
        "model: median 1.6 s (80% of all time)",
        "tools: median 0.0 s",
    ]


def test_model_with_json_keeps_only_that_models_entries(tmp_path, capsys):
    qwen = {
        "event": "timing",
        "ms": 2000,
        "llm_ms": 1600,
        "tools_ms": 0,
        "slowest_tool": None,
        "slowest_tool_ms": 0,
        "model": "qwen-test",
    }
    gpt = {
        "event": "timing",
        "ms": 4000,
        "llm_ms": 3200,
        "tools_ms": 0,
        "slowest_tool": None,
        "slowest_tool_ms": 0,
        "model": "gpt-test",
    }
    path = write(tmp_path, [qwen, gpt])

    assert timings_main(["--model", "qwen-test", "--json", "--journal", str(path)]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "count": 1,
        "ms_median": 2000,
        "ms_p90": 2000,
        "llm_ms_median": 1600,
        "llm_ms_p90": 1600,
        "tools_ms_median": 0,
        "tools_ms_p90": 0,
        "llm_share": 0.8,
        "slowest_tools": [],
    }


def test_since_with_json_filters_the_summary(tmp_path, capsys):
    old = {
        "event": "timing",
        "ms": 1000,
        "llm_ms": 900,
        "tools_ms": 0,
        "slowest_tool": None,
        "slowest_tool_ms": 0,
        "t": "2026-11-17T23:59:59",
    }
    new = {
        "event": "timing",
        "ms": 4000,
        "llm_ms": 3200,
        "tools_ms": 0,
        "slowest_tool": None,
        "slowest_tool_ms": 0,
        "t": "2026-11-18T09:30:00",
    }
    path = write(tmp_path, [old, new])

    assert timings_main(["--since", "2026-11-18", "--json", "--journal", str(path)]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "count": 1,
        "ms_median": 4000,
        "ms_p90": 4000,
        "llm_ms_median": 3200,
        "llm_ms_p90": 3200,
        "tools_ms_median": 0,
        "tools_ms_p90": 0,
        "llm_share": 0.8,
        "slowest_tools": [],
    }


def test_model_stats_groups_per_model_with_their_medians_and_shares():
    entries = [
        {"event": "timing", "ms": 3000, "llm_ms": 2700, "model": "slow-m"},
        {"event": "timing", "ms": 5000, "llm_ms": 4500, "model": "slow-m"},
        {"event": "timing", "ms": 1000, "llm_ms": 800, "model": "fast-m"},
        {"event": "timing", "ms": 2000, "llm_ms": 1500},
    ]  # no model key: grouped as unknown

    assert model_stats(entries) == [("slow-m", 2, 4000.0, 0.9), ("unknown", 1, 2000, 0.75), ("fast-m", 1, 1000, 0.8)]


def test_format_model_stats_one_line_per_model_slowest_first():
    stats = [("slow-m", 2, 4000.0, 0.9), ("unknown", 1, 2000.0, 0.75), ("fast-m", 1, 1000.0, 0.8)]

    assert format_model_stats(stats) == [
        "slow-m: 2 requests, median 4.0 s (model 90%)",
        "unknown: 1 requests, median 2.0 s (model 75%)",
        "fast-m: 1 requests, median 1.0 s (model 80%)",
    ]


def test_format_model_stats_empty_says_no_timed_requests_yet():
    assert format_model_stats([]) == ["no timed requests yet"]


def test_by_model_prints_one_line_per_model_slowest_first(tmp_path, capsys):
    path = write(
        tmp_path,
        [
            {"event": "timing", "ms": 3000, "llm_ms": 2700, "model": "slow-m"},
            {"event": "timing", "ms": 5000, "llm_ms": 4500, "model": "slow-m"},
            {"event": "timing", "ms": 1000, "llm_ms": 800, "model": "fast-m"},
            {"event": "timing", "ms": 2000, "llm_ms": 1500},
        ],
    )

    assert timings_main(["--by-model", "--journal", str(path)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "slow-m: 2 requests, median 4.0 s (model 90%)",
        "unknown: 1 requests, median 2.0 s (model 75%)",
        "fast-m: 1 requests, median 1.0 s (model 80%)",
    ]


def test_by_model_json_prints_the_objects(tmp_path, capsys):
    path = write(
        tmp_path,
        [
            {"event": "timing", "ms": 3000, "llm_ms": 2700, "model": "slow-m"},
            {"event": "timing", "ms": 5000, "llm_ms": 4500, "model": "slow-m"},
            {"event": "timing", "ms": 1000, "llm_ms": 800, "model": "fast-m"},
        ],
    )

    assert timings_main(["--by-model", "--json", "--journal", str(path)]) == 0

    assert json.loads(capsys.readouterr().out) == [
        {"model": "slow-m", "requests": 2, "median_ms": 4000.0, "llm_share": 0.9},
        {"model": "fast-m", "requests": 1, "median_ms": 1000, "llm_share": 0.8},
    ]
