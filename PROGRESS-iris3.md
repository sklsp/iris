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
- [ ] P12. every tool has a known tier
- [ ] P13. argument escalation, as a table
- [ ] P14. a declined action never runs
- [ ] P15. a never action is never even asked
- [ ] P16. screen text cannot change a tier
- [ ] P17. the trash only takes allowed paths
- [ ] P18. every tool call is journaled
- [ ] P19. README: the top for a reader in 60 seconds
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
