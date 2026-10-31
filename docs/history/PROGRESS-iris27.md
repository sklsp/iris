# Iris run iris27: browser hands (items W1 to W7)

- [x] W1. a safe HTTP fetch (863 passed in 23.80s)
- [x] W2. web_search (877 passed in 24.72s)
- [x] W3. web_read (891 passed in 25.32s)
- [x] W4. web tasks in the eval (891 passed in 24.29s)
- [x] W5. docs (891 passed in 25.23s)
- [x] W6. no dashes (0 U+2014/U+2013 across all 10 files changed since timer-language; 891 passed in 24.41s)
- [x] W7. final check (891 passed in 25.07s)

## Browser hands run result

Commits ahead of timer-language at this point (this commit, W7, is the seventh):

| Commit | Item |
|---|---|
| 9e4fc92 | W1: a safe HTTP fetch |
| ddf794a | W2: web_search |
| 128552e | W3: web_read |
| 850cd63 | W4: web tasks in the eval |
| ac15c6d | W5: docs |
| 45515f2 | W6: no dashes |

Last line of each gate, run before this commit:

- ruff check iris tests: All checks passed!
- mypy: Success: no issues found in 13 source files
- pytest -q --cov=iris: 891 passed in 25.07s (total coverage 97.13%, floor 94%)
