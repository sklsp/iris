import json

from iris.timings import format_summary, read_timings, summarize, timings_main


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


def test_timings_main_prints_the_summary_lines(tmp_path, capsys):
    path = write(tmp_path, [e(1000, llm=900), e(2000, llm=1500, tools=600, tool="click", tool_ms=600)])
    assert timings_main(["--journal", str(path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "2 requests: median 1.5 s, p90 2.0 s",
        "model: median 1.2 s (80% of all time)",
        "tools: median 0.3 s",
        "slowest tools: click 0.6 s"]


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
    summary = {"count": 2, "ms_median": 1500.0, "ms_p90": 2000.0,
               "llm_ms_median": 1200.0, "llm_ms_p90": 1500.0,
               "tools_ms_median": 300.0, "tools_ms_p90": 600.0,
               "llm_share": 0.8, "slowest_tools": [],
               "stt_count": 3, "stt_ms_median": 640.0, "stt_ms_p90": 700.0}

    assert format_summary(summary) == ["2 requests: median 1.5 s, p90 2.0 s",
                                       "speech recognition: median 0.6 s, p90 0.7 s",
                                       "model: median 1.2 s (80% of all time)",
                                       "tools: median 0.3 s"]


def test_timings_main_prints_the_speech_line_from_a_journal_with_both(tmp_path, capsys):
    path = write(tmp_path, [e(1000, llm=900), e(2000, llm=1500, tools=600, tool="click", tool_ms=600)],
                 extra_lines=(json.dumps(s(500)), json.dumps(s(640)), json.dumps(s(700))))

    assert timings_main(["--journal", str(path)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "2 requests: median 1.5 s, p90 2.0 s",
        "speech recognition: median 0.6 s, p90 0.7 s",
        "model: median 1.2 s (80% of all time)",
        "tools: median 0.3 s",
        "slowest tools: click 0.6 s"]


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
    summary = {"count": 2, "ms_median": 1500.0, "ms_p90": 2000.0,
               "llm_ms_median": 1200.0, "llm_ms_p90": 1500.0,
               "tools_ms_median": 300.0, "tools_ms_p90": 600.0,
               "llm_share": 0.8, "slowest_tools": [("click", 600)],
               "stt_count": 3, "stt_ms_median": 640.0, "stt_ms_p90": 700.0,
               "tts_count": 2, "tts_ms_median": 400.0, "tts_ms_p90": 900.0}

    assert format_summary(summary) == ["2 requests: median 1.5 s, p90 2.0 s",
                                       "speech recognition: median 0.6 s, p90 0.7 s",
                                       "model: median 1.2 s (80% of all time)",
                                       "tools: median 0.3 s",
                                       "speech output: first sound median 0.4 s, p90 0.9 s",
                                       "slowest tools: click 0.6 s"]


def test_timings_main_prints_the_speech_output_line_from_a_journal_with_all_three(tmp_path, capsys):
    path = write(tmp_path, [e(1000, llm=900), e(2000, llm=1500, tools=600, tool="click", tool_ms=600)],
                 extra_lines=(json.dumps(s(500)), json.dumps(s(640)), json.dumps(s(700)),
                              json.dumps(t(300)), json.dumps(t(400)), json.dumps(t(900))))

    assert timings_main(["--journal", str(path)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "2 requests: median 1.5 s, p90 2.0 s",
        "speech recognition: median 0.6 s, p90 0.7 s",
        "model: median 1.2 s (80% of all time)",
        "tools: median 0.3 s",
        "speech output: first sound median 0.4 s, p90 0.9 s",
        "slowest tools: click 0.6 s"]

