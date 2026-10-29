# Iris queue voice run (night-iris10)

Branch: `queue-voice`. Items 1 to 9, one commit each. Gates before every commit: ruff check, mypy, pytest with coverage floor.

## Checklist

- [x] R1. queue_remove tool (610 passed in 23.72s)
- [x] R2. queue_move tool (618 passed in 21.84s)
- [x] R3. eval tasks for queue_remove and queue_move (618 passed in 22.66s)
- [x] R4. the run row fits a narrow window (618 passed in 22.34s)
- [x] R5. the start button says why it waits (618 passed in 22.89s)
- [x] R6. page tests for R4 and R5 (619 passed in 22.52s)
- [x] R7. docs (619 passed in 23.23s)
- [x] R8. no dashes (619 passed in 22.13s)
- [ ] R9. final check

R8 result: `git diff --name-only main..HEAD` lists 9 files; a Python scan of all of them for U+2014 and U+2013 found none (CHANGELOG.md, PROGRESS-iris10.md, README.md, eval/tasks.toml, iris/dashboard/index.html, iris/tools.py, tests/test_dashboard.py, tests/test_eval.py, tests/test_tools.py).
