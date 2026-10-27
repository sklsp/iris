# Changelog

## 0.2.0

Gates:
- ruff and mypy configured in pyproject.toml; the whole suite passes both (line length 120, target py311; mypy on Python 3.12 with check_untyped_defs)
- coverage floor of 94% enforced by pytest-cov (TOTAL was 96%)
- the test suite now runs without the voice extras: only numpy is imported at module level there, and those two test files skip cleanly

CI:
- GitHub Actions workflow on windows-latest with Python 3.12 that installs the dev extras and runs ruff, mypy and pytest with coverage
- a README badge linking to the CI run plus a one-line summary of the gates

Safety tests (tests/test_safety.py):
- every tool has a known tier, its schema description starts with [tier], and required is a subset of params
- argument escalation pinned as tables: click, type_text, press_keys and trash_file, with Dutch and English never-words, mixed case and mid-word cases
- a declined confirm action never runs; a never action is refused without even asking
- screen text comes back wrapped in <untrusted_data> and cannot change a tier (injection replayed)
- trash only takes paths under the home or W:/ roots, with .. escapes and self-exclusion covered
- every tool call is journaled with its outcome; unknown tools are not journaled (gap noted in IDEAS.md)

Docs:
- README top rewritten for a reader in 60 seconds, with a mermaid flowchart of the agent loop
- README risk tier table, checked by a test so it cannot drift from the code
- DESIGN.md: why UI Automation before pixels, why tiers live in code, untrusted tool output, local model, voice pipeline, eval harness
- SECURITY.md: threat model, what stops it (each stop names its proving test), known limits
- EVAL.md: task format with a real example, dry tools and fixtures, pass criteria, reports and compare, exact commands

Fixes from items 3 to 6:
- remaining ruff errors fixed by hand: TYPE_CHECKING numpy import in voice.py (no module-level numpy), ambiguous variable names renamed, unused variables removed, B-rules fixed with behaviour tests where a fix could change behaviour
- a test that the voice module imports without numpy installed
- mypy passes clean: annotations added across dashboard, eval, voice and __main__ (python_version 3.12 to match the venv)
- check_untyped_defs enabled; only two new Any annotations for lazy third-party objects in voice.py

## 0.1.0

First working version: voice, tools with risk tiers, dashboard, eval harness
