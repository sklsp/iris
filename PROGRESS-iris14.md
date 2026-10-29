# Iris reliable start run (night-iris14)

- [x] A1. poll the history before deciding (657 passed in 22.68s)
- [x] A2. retry the dispatch once (660 passed in 23.22s)
- [x] A3. the dashboard waits long enough (661 passed in 22.42s)
- [x] A4. docs (661 passed in 22.78s)
- [x] A5. no dashes (661 passed in 22.94s)
- [x] A6. final check (661 passed in 22.78s)

## Reliable start run result

Commits ahead of eval-history (5 before this commit, 6 with it):
- b857e72 A1: poll the history before deciding
- 9d9baa6 A2: retry the dispatch once
- 41350ee A3: the dashboard waits long enough
- 2d0cf71 A4: docs
- 0a99018 A5: no dashes

Gates (last line of each, run before this commit):
- ruff check iris tests: All checks passed!
- mypy: Success: no issues found in 11 source files
- pytest -q --cov=iris: 661 passed in 22.78s

