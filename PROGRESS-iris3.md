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
- [ ] P20. README: the risk tier table, checked by a test
- [ ] P21. DESIGN.md
- [ ] P22. SECURITY.md
- [ ] P23. EVAL.md
- [ ] P24. CHANGELOG and version
- [ ] P25. pyproject metadata
- [ ] P26. repo paths come from settings
- [ ] P27. a public-readiness audit
- [ ] P28. the dashboard has no personal defaults
- [ ] P29. test names read as documentation
- [ ] P30. no dashes
- [ ] P31. all gates and coverage
- [ ] P32. final check

## Tool tiers (item 12)

safe: system_status, gpu_check, comfy_status, lmstudio_models, followups, journal_today, list_windows, read_window, open_app, focus_window, scroll, open_url, restore_trash, agents_status, queue_list, concept_sites
confirm: click, type_text, press_keys, trash_file, queue_add, queue_take
never: (none)

