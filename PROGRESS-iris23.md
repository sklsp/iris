# Iris eval tab run (night-iris23)

- [x] T1. the eval payload carries the whole summary (790 passed in 24.91s)
- [x] T2. the Eval tab shows language, answers and refused attempts (790 passed in 22.88s)
- [x] T3. history shows wrong answers (793 passed in 24.77s)
- [x] T4. docs (793 passed in 23.49s)
- [x] T5. no dashes (793 passed in 24.04s)
- [x] T6. final check (793 passed in 23.61s)

T5 scan (`git diff --name-only calculator..HEAD`): scanned all 7 changed files for U+2014 and U+2013; none found.

## Eval tab run result

Commits ahead of `calculator`:
- 3318d47 T1: the eval payload carries the whole summary
- d8f47f9 T2: the Eval tab shows language, answers and refused attempts
- ace002c T3: history shows wrong answers
- 05ffa37 T4: docs
- 4ead05a T5: no dashes

Last gate lines (final run):
- ruff: All checks passed!
- mypy: Success: no issues found in 11 source files
- pytest: 793 passed in 23.61s
