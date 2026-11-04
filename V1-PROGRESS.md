# Iris 1.0.0: progress of the v1 run

Working file for the 0.6.1 to 1.0.0 run on branch `v1`. Deleted in the final commit; its content moves to
docs/v1-test-plan.md. A resumed session reads this first.

## Phases

| Phase | Branch | State |
|---|---|---|
| 0 Orientation, backlog | v1 | done (this file) |
| 1 Annotations, mypy disallow_untyped_defs | v1-p1-annotations | merged |
| 2 Correctness (P0, then P1), upgrade path | v1-p2-bugs | merged |
| 3 Speed (bench first) | v1-p3-speed | merged |
| 4 Skills, eval coverage | v1-p4-skills | merged |
| 5 Robustness, stability promise, a11y | v1-p5-robust | open (#49) |
| 6 Code quality (dead code, ruff rules) | v1-p6-quality | open (#50) |
| 7 Docs | v1-p7-docs | open (#51) |
| 8 CI and release plumbing | v1-p8-release | open (#52) |
| 9 Test plan, final PR | v1-p9-plan | open (#53) |

## Merged

(one line per merged PR: "✅ P<n> (#PR, tests N, coverage X%)")

- ✅ P1 (https://github.com/sklsp/iris/pull/44, tests 2020, coverage 99.46%)
- ✅ P2 (https://github.com/sklsp/iris/pull/45, tests 2157, coverage 99.25%): 34 commits; backlog items 1-10, 12-16, 18-20, 22, 24, 25, 30-38, 44, 46-48, 50, 54, 68 done
- ✅ P3 (https://github.com/sklsp/iris/pull/46, tests 2165, coverage 99.20%): bench; doctor 4.49 to 0.83 s, turn 5.12 to 1.68 ms on windows-latest; items 56, 58-61, 63, 64 done
- ✅ P4 (https://github.com/sklsp/iris/pull/48, tests 2167, coverage 99.20%): flaky-task descriptions, actionable errors, 21 new DE/FR/ES tasks (236); items 66, 67 done

## Starting numbers (main at 2c5ac8f, v0.6.1)

- 2020 tests, coverage 99.49% (CI), ruff clean, mypy clean; 72 tools; 215 eval tasks.
- Eval 629 to 633 of 645 (97.5 to 98.1%), 0 forbidden, 0/135 wrong answers.
- On this Linux box: 1976 tests pass with a local windll shim (never committed); 44 fail or error only because
  they need Windows behaviour (paths, os.startfile). CI on windows-latest decides.

## Backlog

Ranked: P0 = wrong behaviour, safety or data loss; P1 = robustness; P2 = speed; P3 = polish. `[model]` = changes
text the model sees, so the eval must judge it. Each item names the test that proves the fix. Items marked JAY touch
policy.py, tier_lock.json or one of Jay's open decisions: they are not done, they go to the test plan.

### P0

1. skills.py:113,209,260 The builder writes the skill folder that holds meta.json, and check() and approve() trust
   its `tier` and `status`: a builder can turn the confirm tier the user agreed to into safe, or mark a skill ready
   without guard and tests. Keep the agreed tier and the checked digest outside the skill folder. Test: a fake
   builder rewrites meta.json; the approved tool stays confirm and an unchecked skill cannot be approved.
2. browser.py:289 remember() re-parses the item list from text with `"(.*?)"`, so a label with a double quote is
   cut and policy sees "Next" instead of 'Next" Pay now'. Fill `_last` from the snapshot items. Test: that label
   makes browser_click NEVER.
3. browser.py:392-404 `_last` is the last list read in any tab, but a click acts on the tab in front now: the tier
   and question describe item N of tab A while the click lands on item N of tab B. Remember the tab and refuse when
   it changed. Test: read B, read A, click in B is refused with "read the page again". [model] (new error text)
4. tools.py:403 click matches a control by substring, but policy checks the name the model gave: control="Sen"
   passes as confirm and clicks "Send". Re-check the found control's own name against the NEVER words and refuse
   (stricter only, no policy change). Test: a fake "Send" control, click "Sen" is refused. [model]
5. agent.py:168 A tool result that contains `</untrusted_data>` closes the wrapper and the rest reads as trusted.
   Escape the closing tag inside results. Test: one wrapper per tool message whatever the result holds. [model]
6. dashboard/__init__.py:811 The Host header is never checked: a DNS-rebinding page can read every GET route,
   including the page with the token, the journal and the settings. Answer 421 to a non-loopback Host. Test.
7. web.py:76 The local-address refusal misses 127.1, 2130706433, 0x7f000001, 0, "localhost." and non-global ranges
   (100.64/10, multicast). Check the resolved addresses with is_global. Test: parametrized hosts refused.
8. tools.py:1174 reminder_cancel's schema says `id`, its function takes `rid`: cancelling a reminder always fails
   for real (the eval's dry tools hide it). Test: every tool's required parameters bind to its function. [model]
9. tools.py:551 restore_trash skips every trash entry ending in .json, so a trashed .json file (also the old copy
   that write_file overwrite keeps) can never be restored. Test: trash and restore x.json.
10. files.py:167 write_file(overwrite=True) on an existing folder trashes the whole folder. Test: error, folder
    untouched. [model]
11. tools.py:253 lmstudio_load refuses on a PC without nvidia-smi ("unload a model first"). Test: gpus=[] loads.
12. tools.py:725 calculate shows complex numbers and rounded huge divisions as exact results. Test:
    "(-1)**0.5" is an error, "10**100/3" is a float. [model]
13. voice.py:504 speak_async has no lock while timers, reminders and the skill watcher call it from other threads:
    two speakers and two wake watchers at once, and a watcher can take frames from a recording. Test: two threads,
    at most one active speak.
14. voice.py:861 Kokoro calls sd.play without blocking, so each chunk cuts off the one before. Test: a fake play
    that records cuts.

### P1 (robustness)

15. agent.py:222 + llm.py:92 Non-object tool arguments (null, a list, a JSON object instead of a string) and a tier
    function that raises (make_document path="") end Agent.handle and the voice loop. Test: each returns an error
    to the model. [model]
16. agent.py:150 http.client.IncompleteRead and other non-OSError failures from the model server end the session;
    HTTP 400 (context too long) is reported as "can't reach the model". Test. [model] (spoken text)
17. agent.py:252 A journal write that fails after the tool ran is reported to the model as a tool error, so it may
    run the tool again. Test: the result reaches the model unchanged.
18. skills.py:260,368,383 A meta.json without "tier" makes active_tools raise on every request; a broken one kills
    the skill watcher. Test.
19. skills.py:226,338 Skills run under `python -I` with the locale encoding: a skill that prints "→" fails. Pass
    `-X utf8` and decode UTF-8. Test.
20. autopilot.py:206 take() removes the oldest prompt at that moment, not the one started. Test by id.
21. autopilot.py:61,119 + skills.py:167 `["cline", ...]` misses npm's cline.cmd on Windows (doctor finds it with
    shutil.which). Resolve with shutil.which. Test.
22. voice.py:360 The session never stops the VoxCPM worker or Piper; a worker still loading keeps the GPU after
    Iris exits (outside the app's Job Object). Add Speaker.close. Test.
23. voice.py:375 wait_for_speech gives up after 60 s without stopping the speech, so the next answer overlaps it.
    Test.
24. voice.py:337 A missing or busy microphone gives a raw PortAudio traceback and leaves speech workers running.
    Test: one-line message.
25. voice.py:410,479 Waiting for audio frames has no timeout: an unplugged microphone hangs Iris and blocks Ctrl+C.
    Test.
26. voice.py:416 The chime's blocking play has no time limit. Test.
27. voice.py:294 Push-to-talk with no stdin (EOFError) waits forever. Test.
28. voice.py:820-835 One Piper or VoxCPM failure switches the engine for the rest of the run, also while VoxCPM is
    still loading. Test.
29. voice.py:560 Piper's stdin is UTF-8 but the child may read it in the locale code page. Test: PYTHONIOENCODING.
30. voice.py:142 A missing custom .onnx wake model calls download_models([]), which downloads every model (an
    auto-download nobody asked for). Test: FileNotFoundError, no download.
31. overlay.py:508 An exception in step() stops the overlay's event loop, so it never sees EOF and stays on screen
    after Iris dies. Test.
32. voice.py:35,37 silence = "abc" makes every command crash at import, doctor and config included. Test.
33. config.py:32 A BOM, broken TOML or non-UTF-8 config.toml is silently all defaults; Settings cannot save a BOM
    file; doctor says "ok". Read utf-8-sig, report the error in doctor. Test.
34. notes.py:26,79 A bad date line or a non-UTF-8 notes.md raises inside the confirm question and ends the loop.
    Test.
35. reminders.py:66 An unreadable reminders.json reads as empty and the next reminder overwrites it (data loss).
    Keep the file aside like prompt_queue does. Test.
36. reminders.py:38 A reminder more than 49.7 days ahead overflows threading.Timer on Windows. Test.
37. dashboard/__init__.py:880 POST bodies that are not objects, nested too deep, too big, negative or missing
    Content-Length drop the connection or hang 30 s. Answer 400/413. Test.
38. settings.py:168 int(inf) raises OverflowError past the validator. Test.
39. index.html:1414,1425,1483 A list key holding a string leaves Settings stuck; a wrong-typed number shows empty
    and the next Save removes its line. Save only changed keys. Test (settings.validate side).
40. index.html:1530 The Setup tab never probes a custom URL, so a custom server cannot be set in the wizard.
41. packs.py:261,170 Pack installs have no time limit and no --upgrade: a hung pip blocks every pack, a rerun after
    an interrupted install says Done. Test.
42. packs.py:55 Packs read the Whisper model and Piper voices from import time, not the current settings. Test.
43. app.py:306 If the tray icon fails, closing the window hides Iris with no way back. Test.
44. update.py:75 A corrupt update.json, a list instead of an object or IncompleteRead kill the update thread. Test.
45. files.py:226 copy_file of a folder into its own subfolder recurses without end. Test. [model]
46. files.py:92,78 read_file on a folder and list_folder on a file say "does not exist". Test. [model]
47. files.py:103 UTF-16 files (PowerShell `>`) read as garbage. Honour the BOM. Test.
48. files.py:80 One unreadable entry fails the whole list_folder. Test.
49. browser.py:73,170 A failed DevTools connection is not a BrowserError: a second 60 s wait and Allow prompt, and
    web_search skips its fallback. Test.
50. browser.py:452 A search whose results mention "captcha" is treated as a bot check. Test.
51. web.py:185 "form" in the skipped tags empties ASP.NET pages. Test.
52. tools.py:412 click falls back to click_input when invoke raised after it acted: a double click. Test.
53. tools.py:728 calculate leaks "Exceeds the limit (4300 digits)". Test. [model]
54. __main__.py:168 --journal ignores IRIS_JOURNAL, which the dashboard reads. Test.
55. llm.py:92 arguments given as a JSON object raise TypeError. Test (with 15).

### P2 (speed)

56. Bench first (tools/bench.py): cold start of --help and doctor, per-turn overhead with a fake model, dashboard
    first response, prompt plus tool tokens (characters / 4).
57. agent.py:134 config.toml parsed three times per request and once per tool call; registry.py:24 rescans and
    hashes every approved skill per request; llm.py:44 settings.json per model call. Cache by mtime.
58. agent.py:204 Once over budget every request drops one old turn, which changes the cached prefix every turn;
    the size is recomputed per dropped turn. Trim with headroom, count the tool schemas, journal the trim.
59. agent.py:145 Nothing limits the current turn: big tool results can overflow 65k mid-turn. Cap each result with
    a "[truncated, N more]" marker. [model]
60. agent.py:193 _out_of_steps sends tools=[], which breaks the prompt cache at its largest. [model]
61. Uncapped outputs: list_windows, followups, concept_sites, comfy_workflows, queue_move; silent caps in
    browser (120 items, 1500 chars), clipboard, email body. Mark them. [model]
62. tools.py:112 GPU-only paths wait 0.3 s on cpu_percent.
63. index.html:1394 The page polls 8 routes every 3 s on every tab and while hidden; /api/system blocks 0.3 s.
    Pause when hidden, poll only what the tab shows.
64. dashboard/__init__.py:513 _cached fetches without a lock; packs.py:126 nvidia-smi three times per poll.
65. Lazy imports for --help, doctor and the dashboard (measure first).

### P3 (polish)

66. Tool texts that do not say what to do next: tools.py:185,208,216,219,229,238,281,405,441,491,507,528,562,656,
    714; agent.py:251,261. [model]
67. tools.py:986 web_search says DuckDuckGo but searches Brave in the browser; tools.py:963 open_url says a new tab
    but reuses Iris's tab. [model]
68. update.py:48 version_tuple: 1.0.0 equals 1.0.0rc1, so an rc user never hears of 1.0.0. Test.
69. dashboard: HEAD returns a body; the token page has no Cache-Control: no-store.
70. Docs that do not match: DESIGN.md:73 (56 tasks), voice.py docstrings (1.2 s, SAPI and Kokoro only),
    dashboard docstring routes, web.py:3 and browser.py:10 network claims, index.html:423 "nothing is sent".
71. Dead code: tools.__all__ re-exports of policy names nobody uses through tools; overlay "thinking" event and
    on_cancel parameter; index.html sysHist and .pane.hidden.
72. Accessibility: #565c66 text at 2.6:1, input borders 1.3:1, outline:none on one input, unlabeled queue and
    setup controls, tabs without role/aria-selected, no aria-live on status lines.
73. Doctor misses: config parse errors and types, pyttsx3 for SAPI, kokoro, the app extra, wake model files.

### Eval flakes (Phase 4)

- scroll: scroll's description never says to use browser_scroll for web pages; browser_scroll's never names the
  browsers; the real scroll result says nothing about what moved. [model]
- browser_search: web_search's "Search the web" wins for "search on Netflix"; focus_window invites an extra step
  that browser tools do not need. [model]
- save_as: JAY (open decision), left as it is.

### JAY: needs policy.py, tier_lock.json or an open decision (not done here)

- policy.py:105 Only the literal `{ENTER}` counts in a mail or chat window; `~`, `{ENTER 2}` and `{VK_RETURN}` also
  press Enter. The check uses the window name the model gave, not the window matched.
- policy.py:58 type_text("WhatsApp", "hoi\n") sends: with_newlines turns the newline into Enter.
- policy.py:123 id_ecdsa, id_dsa, .npmrc, .pypirc and .netrc are safe to read.
- policy.py:97 open_url is safe for local addresses (http://127.0.0.1:8765/api/journal) and for any query string.
- Open decisions: hard_no_tools, timer_at, save_as, sites_no_deploy, cline_not_done, hard_email_send.

## Benchmark (tools/bench.py on windows-latest)

Baseline at 3b32128, before phase 3 (CI run of https://github.com/sklsp/iris/pull/47): --help 0.302 s, doctor
4.491 s (no model server), one agent turn 5.12 ms, dashboard first answer 0.645 s, GET / 0.97 ms, prompt 57 tools,
21807 characters (about 5451 tokens).

After phase 3 (35b6fcb): --help 0.308 s, doctor 0.834 s, one turn 1.68 ms, dashboard first answer 0.67 s, prompt unchanged.

## Open questions for Jay

- lmstudio_load refuses on a PC without nvidia-smi ('no GPU has 8000 MiB free; unload a model first'); test_load_without_any_gpu_returns_the_literal_error pins it. Options: keep (plugin is for an NVIDIA setup) or let LM Studio decide when there is no GPU to measure. Recommendation: let LM Studio decide, with an AMD/CPU note.
- A journal write that fails after a tool ran (file locked, disk full) is reported to the model as a tool error, so it may run the tool again (agent.py _run). Options: (a) return the tool's real result and say aloud that the journal could not be written; (b) stop the turn when the journal can not be written (nothing runs unlogged). Recommendation: (b), it keeps PLAN.md principle 4.
- Cline from npm is cline.cmd on Windows: `subprocess.run(["cline", ...])` does not find it (doctor does, with shutil.which), so autopilot and the skill builder fail with "Cline CLI not found" on a default npm install. Not fixed on purpose: running a .cmd with the prompt as an argument goes through cmd.exe parsing, so a prompt with & or " could run commands (the BatBadBut class). Options: (a) document that cline_cli in config.toml must point at the real node entry (node.exe plus cline's script); (b) resolve cline.cmd to the node script it wraps and run node directly; (c) pass the prompt through a file instead of argv. Recommendation: (c) if the Cline CLI accepts a prompt file, else (a).
- Delete the branch v1-bench-base (and the merged v1-p* branches if you like): this session's git proxy does not allow branch deletion.
