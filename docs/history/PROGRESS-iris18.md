# Iris run iris18: model failure (items F1 to F7)

- [x] F1. a model failure ends the turn, not Iris (713 passed in 22.64s)
- [x] F2. the eval grades a model failure (714 passed in 22.88s)
- [x] F3. journal_today counts model errors (716 passed in 24.67s)
- [x] F4. the dashboard shows model errors (718 passed in 24.07s)
- [x] F5. docs (718 passed in 23.36s)
- [x] F6. no dashes: scanned the 15 files changed since main; found 2 U+2014 em dashes in DESIGN.md (fixed), re-scan clean (718 passed in 22.94s)
- [x] F7. final check (718 passed in 23.16s)

## Model failure run result

Commits ahead of multi-turn: 7 (one per item, F1 to F7).

Last gate lines:
- ruff check iris tests: All checks passed!
- mypy: Success: no issues found in 11 source files
- pytest: 718 passed in 23.16s
