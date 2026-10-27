- [x] P1. ruff and mypy config (299 passed in 15.04s)
```
15	E501 	[ ] line-too-long
 4	F821 	[ ] undefined-name
 4	UP037	[*] quoted-annotation
 3	E741 	[ ] ambiguous-variable-name
 3	I001 	[*] unsorted-imports
 2	UP035	[*] deprecated-import
 2	W292 	[*] missing-newline-at-end-of-file
 1	B007 	[ ] unused-loop-control-variable
 1	B905 	[ ] zip-without-explicit-strict
 1	F841 	[ ] unused-variable
Found 36 errors.
[*] 11 fixable with the `--fix` option (3 hidden fixes can be enabled with the `--unsafe-fixes` option).
```
- [x] P2. safe ruff fixes (299 passed in 14.80s)
- [x] P3. the remaining ruff errors (299 passed in 14.51s)
- [x] P4. a test that the voice module imports without numpy (300 passed in 14.30s; all optional deps already lazy, no code changed)
- [x] P5. mypy passes (Success: no issues found in 10 source files; 300 passed in 14.27s)
  - pyproject: mypy python_version 3.11 -> 3.12 to match the venv interpreter (numpy's stubs use PEP 695, which needs >=3.12); no `# type: ignore` needed anywhere
  - dashboard: _Server(ThreadingHTTPServer) subclass carries the annotated token attribute; eval: out: dict[str, bool]; voice: is_yes(str | None) with explicit None -> False, next_request/next_command narrowed to str (they never return None), _whisper: Any (lazy faster_whisper, no stubs), tuple-lambda replaced by a named function; __main__: hear/say annotated before the mode branches
- [x] P6. mypy check_untyped_defs (option stays ON; only 2 new errors, both in voice.py: _stream and _kokoro annotated Any - lazy third-party objects without stubs; 300 passed in 15.73s)
- [x] P7. the coverage floor (fail_under = 94; TOTAL was 96%, precise 95.56%; 300 passed in 15.34s)
```
Name                         Stmts   Miss  Cover
------------------------------------------------
iris\__init__.py                 0      0   100%
iris\__main__.py                75      1    99%
iris\agent.py                   64      5    92%
iris\agents.py                 135      7    95%
iris\dashboard\__init__.py     417     48    88%
iris\eval.py                   165      6    96%
iris\llm.py                     60      0   100%
iris\prompt_queue.py            76      1    99%
iris\tools.py                  323      0   100%
iris\voice.py                  239      1    99%
------------------------------------------------
TOTAL                         1554     69    96%
```
- [x] P8. the dashboard and eval under the gates (verified, nothing to fix: iris/dashboard has __init__.py; mypy -v "Found source" lists all 10 files incl. iris\dashboard\__init__.py as module iris.dashboard, ruff --show-files includes it too; 300 passed in 14.71s)
- [x] P9. GitHub Actions CI (one job test on windows-latest, py3.12, dev extras only; YAML validated with a parse check via the already-installed yaml module 6.0.3 - triggers, permissions, concurrency and all seven steps asserted; 300 passed in 14.68s)
- [x] P10. the suite without the voice extras (only numpy was imported at module level - test_voice.py and test_voice_behaviour.py; both now use `np = pytest.importorskip("numpy")` after their import blocks so ruff E402 stays quiet; normal: 300 passed in 14.27s, without extras (sitecustomize setting the six to None): 263 passed, 2 skipped in 13.38s)
- [x] P11. README badges and the CI line (badge links to the actions page; line reads `Python 3.11+ . Windows . 300 tests . 96% coverage` - 300 from the current suite, 96% TOTAL from item 7; " . " separators only)
- [x] P12. every tool has a known tier (tests/test_safety.py: 22 tools x 3 parametrized checks - tier in SAFE/CONFIRM/NEVER, schema description starts with `[<tier>]`, required subset of params; base tiers listed below for item 19)
- [x] P13. argument escalation, as a table (4 parametrized tables in tests/test_safety.py: _click_tier 8 rows, _type_tier 7, _press_keys_tier 10, _trash_tier 7; every expected tier verified against the real functions first; Dutch and English never-words, mixed case, and mid-word cases covered - "Reorder" stays confirm (no escalation), "Poster"/"Payment" escalate by documented over-refusal, so no xfail was needed)
- [x] P14. a declined action never runs (tests/test_safety.py: fake LLM asks for one CONFIRM click, then answers; confirm=False -> fn never called, tool message contains "declined", journal outcome "declined"; confirm=True -> fn called exactly once with the right args; fake tools dict via Agent(tools=...))
- [x] P15. a never action is never even asked (tests/test_safety.py: two cases - base tier NEVER and CONFIRM escalated to NEVER via _click_tier on control "Verzenden"; in both confirm is never called, fn never runs, the tool message starts with "refused" under the untrusted wrapper, journal outcome "refused")
- [x] P16. screen text cannot change a tier (tests/test_safety.py: fake SAFE read_screen returns an injection string, the scripted LLM then asks for trash_file on C:/Windows; asserts the result went back wrapped in <untrusted_data>, the journal records exactly the tier _trash_tier gives that path, confirm called/not per that tier, and the fn never ran with confirm=no)
- [x] P17. the trash only takes allowed paths (tests/test_safety.py: literal table of 8 rows for _trash_tier - home file CONFIRM, W:/ file CONFIRM, home itself NEVER, W:/ itself NEVER, C:/Windows/notepad.exe NEVER, .. escape resolving outside roots NEVER, recycle-bin path NEVER, relative path (CWD under home) CONFIRM; all expected tiers verified against the real function first; IRIS_TRASH_DIR + chdir to tmp_path for determinism)
- [x] P18. every tool call is journaled (tests/test_safety.py: one Agent run per outcome - ok/error/declined/refused each leave exactly one journal line with the literal outcome and recorded args; unknown tool returns "error: unknown tool ..." to the model but is NOT journaled - real behaviour asserted, gap noted in IDEAS.md, no code change)
- [x] P19. README: the top for a reader in 60 seconds (README.md above "## Run" replaced with title + item-11 badges, one-paragraph "What it is", five-bullet "What it shows" - local LLM tool calling, UI Automation instead of screenshots, risk tiers in code with tests, wake word and streaming TTS, eval harness - and the mermaid flowchart from the item; everything from "## Run" down untouched)
- [x] P20. README: the risk tier table, checked by a test (README.md "## Risk tiers" after the diagram: | Tool | Tier | Escalates when | with one row per tool in tools.TOOLS order; escalation text for click/type_text/press_keys/trash_file from item 13's tables, empty otherwise. tests/test_readme.py parses that table and asserts (name, tier) pairs equal tools.TOOLS exactly - so the README can never drift from the code)
- [x] P21. DESIGN.md (new file: seven sections of 3 to 8 short sentences each, all grounded in the code or PLAN.md - Why UI Automation before pixels (pywinauto named controls, MAX_CONTROLS cap, no screenshot/OCR code exists), Why tiers live in code (tier_of in agent._run, tier_for escalations, tests pin them), Untrusted tool output (<untrusted_data> wrapping + system prompt + eval traps), Why a local model (LM Studio 127.0.0.1:1234, stdlib urllib client, env/settings/default resolution), Voice (openWakeWord ONNX hey_jarvis thr 0.5, energy VAD RMS 0.01 / 1.2 s silence, long-lived mic stream + watcher thread, Kokoro chunk streaming barge-in, no re-wake after interrupt), The eval harness (56 tasks in eval/tasks.toml, dry-run fixtures, reports + compare + dashboard tab), What it does not do yet (PLAN.md Next: real-model eval number, hey iris wake word; read-only v2 skills; out-of-scope list). Linked from the README "What it shows" top list)
- [x] P22. SECURITY.md (new file, three sections: Threat model - attacker controls text on screen, window titles, file names, web pages read by a tool; does not control code. What stops it - seven lines each naming the proving test in tests/test_safety.py: tiers enforced in code before fn runs (test_every_tool_has_a_known_tier + test_never_tool_by_base_tier_is_refused_without_asking), NEVER words EN/DU case-insensitive word-start (test_click_tier, test_type_tier, test_never_tool_by_escalation_is_refused_without_asking), mail/chat Enter rule incl. mixed case and mid-string Enter (test_press_keys_tier 10 rows), trash roots home+W:/ with .. escape and self-exclusion (test_trash_tier + test_trash_only_takes_allowed_paths), untrusted_data wrapping (test_screen_text_cannot_change_a_tier replays an injection), confirm by voice - declined fn never runs, journaled (test_declined_confirm_tool_never_runs + test_confirmed_confirm_tool_runs_exactly_once), journal of every call (test_every_tool_call_is_journaled). Known limits - no xfail cases this run (P13 tables matched real behaviour); from IDEAS.md: unknown tool calls not journaled; documented over-refusal ("Poster"/"Payment"); fixed EN/DU word lists ("Versenden" does not match the verzend stem) and fixed mail/chat window list; wrapping is advisory to the model - hard protection is code-enforced tiers/roots; confirm prompt relies on Jay listening (silence = no, verified in all three __main__.py confirm variants: empty input / empty transcript -> is_yes("") -> False))
- [x] P23. EVAL.md (new file, from iris/eval.py and eval/tasks.toml only: task format with all fields and defaults as validated by load_tasks - id/request required + unique, expect, forbid, confirm yes/no default no, outcomes ok/refused/declined/error, fixtures, max_calls 6; one real example copied verbatim (the whatsapp task); dry tools return fixture or "ok (dry run of {name})", never call the real fn, tiers/tier_for copied so refusal behaves as in production (trash_system forbids trash_file despite confirm=yes because C:/Windows/Temp escalates to NEVER); pass = zero reasons from journal grading - expect called, forbid not ok, last outcome matches outcomes, calls <= max_calls, no gave_up; reports {stamp}.json + {stamp}.md into eval/results with summary fields and per-task table; compare(a,b) per task id -> broke/fixed/new/gone/same, --repeat passes only when all runs pass; exact commands from eval.py main argv handling: python -m iris eval [--tasks] [--out] [--only ID repeatable, unknown exits 2 before running] [--repeat N], and python -m iris eval-compare A.json B.json via __main__.py dispatch. No result numbers, not run)
- [x] P24. CHANGELOG and version (pyproject 0.1.0 -> 0.2.0; new CHANGELOG.md with ## 0.2.0 in plain lines grouped as gates - ruff/mypy config, coverage floor 94%, suite runs without voice extras; CI - windows-latest py3.12 workflow running ruff+mypy+pytest-cov, README badge + gate line; safety tests - known tiers per tool, escalation tables EN/DU, declined never runs, never not even asked, untrusted_data wrapping, trash roots with .. escape and self-exclusion, journal of every call incl. the unknown-tool gap; docs - README top rewrite + mermaid flowchart, risk tier table checked by test, DESIGN.md, SECURITY.md, EVAL.md; fixes from items 3 to 6 - hand ruff fixes (TYPE_CHECKING numpy import, renames, B-rules with behaviour tests), voice-imports-without-numpy test, mypy annotations across dashboard/eval/voice/__main__ at python_version 3.12, check_untyped_defs on with two Any in voice.py; and ## 0.1.0 one line "First working version: voice, tools with risk tiers, dashboard, eval harness". No other file referenced the old version)
- [x] P25. pyproject metadata (added readme = "README.md", authors = [{name = "skl"}], license = {text = "MIT"}, keywords voice agent/ui automation/local llm/windows/agents, classifiers Python 3.11 + 3.12 + Microsoft Windows + MIT, [project.urls] Repository = https://github.com/sklsp/Iris; no LICENSE file added - IDEAS.md now says Jay decides the license when Iris goes public; tomllib parse check confirms all fields)
- [x] P26. repo paths come from settings (agents.py: _default_repos() reads IRIS_REPOS at call time (';' separated, stripped) with DEFAULT_REPOS as the fallback when unset; Config.repos default_factory now uses it - a file's repos key still wins over both. tools.py: module constant _TRASH_ROOTS replaced by _trash_roots(), read at call time like IRIS_LEADSCAN_DIR - [home, W:/] when unset, else the ';' separated list with home always included; _trash_tier calls it and its docstring no longer names fixed roots. Tests: test_repos_come_from_the_env_var + test_repos_default_to_the_built_in_list_without_env (test_agents.py), test_trash_roots_come_from_the_env_var (new root CONFIRM, W:/ now NEVER, home always stays, naming home explicitly changes nothing) + test_trash_roots_default_to_home_and_w_without_env (test_safety.py). README settings table gains IRIS_REPOS and IRIS_TRASH_ROOTS rows next to the other IRIS_ vars; both added to the readme guard in test_tools.py. 414 passed in 14.51s)
- [x] P27. a public-readiness audit
- [ ] P28. the dashboard has no personal defaults
- [ ] P29. test names read as documentation
- [ ] P30. no dashes
- [ ] P31. all gates and coverage
- [ ] P32. final check

## Tool tiers (item 12)

safe: system_status, gpu_check, comfy_status, lmstudio_models, followups, journal_today, list_windows, read_window, open_app, focus_window, scroll, open_url, restore_trash, agents_status, queue_list, concept_sites
confirm: click, type_text, press_keys, trash_file, queue_add, queue_take
never: (none)

