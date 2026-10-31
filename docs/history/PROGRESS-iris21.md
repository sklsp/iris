# Iris answer checks (night iris21)

Branch: `answer-checks` in W:/Iris. One commit per item, message `A<n>: <title>`, body the last pytest line. Gates before every commit: ruff check, mypy, pytest with coverage floor.

- [x] A1. answer checks (753 passed in 23.11s)
- [x] A2. the summary counts wrong answers (758 passed in 25.17s)
- [x] A3. the report lists wrong answers (761 passed in 24.46s)
- [x] A4. docs (761 passed in 24.27s)
- [x] A5. no dashes (scan of eval-filters..HEAD found 1 em dash, fixed in EVAL.md; 761 passed in 24.16s)
- [x] A6. final check (761 passed in 24.26s)

## Answer checks run result

Six commits ahead of `eval-filters`: 75f9443 A1 answer checks, c59fae7 A2 the summary counts wrong answers, c204de7 A3 the report lists wrong answers, ad4ca32 A4 docs, a11b19e A5 no dashes, and this commit A6 final check.

Last gate lines:
- ruff: All checks passed!
- mypy: Success: no issues found in 11 source files
- pytest: 761 passed in 24.26s
