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
- [ ] P7. the coverage floor
- [ ] P8. the dashboard and eval under the gates
- [ ] P9. GitHub Actions CI
- [ ] P10. the suite without the voice extras
- [ ] P11. README badges and the CI line
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
