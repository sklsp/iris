# Iris review fixes run (night-iris16)

Branch: `review-fixes` in W:/Iris. One commit per item, gates pass before every commit.

- [x] V1. never retry blind (F1) (663 passed in 22.80s)
- [x] V2. honest wording about double starts (F1, F5) (663 passed in 22.94s)
- [x] V3. SECURITY.md cites real tests (F2) (664 passed in 23.62s)
- [x] V4. a broken report never breaks history (F4) (667 passed in 23.45s)
- [x] V5. count refused attempts (F3) (668 passed in 22.83s)
- [x] V6. no dashes (668 passed in 22.71s)
- [ ] V7. final check

## V6 dash scan (reliable-start..HEAD)

Scanned the 12 files in `git diff --name-only reliable-start..HEAD` (CHANGELOG.md, EVAL.md, PROGRESS-iris16.md, README.md, SECURITY.md, iris/autopilot.py, iris/dashboard/index.html, iris/eval.py, tests/test_autopilot.py, tests/test_dashboard.py, tests/test_eval.py, tests/test_safety.py) for U+2014 and U+2013 with Python: 0 lines contain either character. No files needed fixing.
