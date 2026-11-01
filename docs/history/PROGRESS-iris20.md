# Iris eval filters run (night-iris20)

- [x] E1. partial runs never become the headline (732 passed in 22.98s)
- [x] E2. the dashboard shows the newest full run (733 passed in 24.34s)
- [x] E3. --category (737 passed in 23.13s)
- [x] E4. --only takes patterns (739 passed in 23.23s)
- [x] E5. --failed-from (743 passed in 23.54s)
- [x] E6. docs (743 passed in 24.33s)
- [x] E7. no dashes (scan of the 7 files changed since honest-counters found U+2014 only in EVAL.md:103, fixed; 743 passed in 23.45s)
- [x] E8. final check (743 passed in 23.95s)

## Eval filters run result

Commits ahead of honest-counters (7):

- 53d627f E1: partial runs never become the headline
- 6df2512 E2: the dashboard shows the newest full run
- cc8bd18 E3: --category
- 7adb7ba E4: --only takes patterns
- 20db06e E5: --failed-from
- ef75c22 E6: docs
- c0aedd0 E7: no dashes

Gates, last run:

- ruff: All checks passed!
- mypy: Success: no issues found in 11 source files
- pytest: 743 passed in 23.95s
