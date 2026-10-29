# Iris review fixes run (night-iris16)

Branch: `review-fixes` in W:/Iris. One commit per item, gates pass before every commit.

- [x] V1. never retry blind (F1) (663 passed in 22.80s)
- [x] V2. honest wording about double starts (F1, F5) (663 passed in 22.94s)
- [x] V3. SECURITY.md cites real tests (F2) (664 passed in 23.62s)
- [x] V4. a broken report never breaks history (F4) (667 passed in 23.45s)
- [x] V5. count refused attempts (F3) (668 passed in 22.83s)
- [x] V6. no dashes (668 passed in 22.71s)
- [x] V7. final check (668 passed in 23.16s)

## Review fixes run result

Commits ahead of reliable-start: 7 (V1 to V7).

Gates, last line of each (final run before the V7 commit):
- ruff check iris tests: All checks passed!
- mypy: Success: no issues found in 11 source files
- pytest -q --cov=iris: 668 passed in 23.16s (coverage floor holds, total 96.75%)

Per finding:
- F1 (double start): V1 makes cline_sessions return None when the history read fails and _poll report whether any read succeeded; the retry dispatch only happens when the history was readable and showed no new session after about 15 seconds. V2 rewords README.md and CHANGELOG.md to match: a session that registers later than that could still be started twice.
- F2 (SECURITY.md citation): V3 cites the two real test names and adds an ftp:// row to OPEN_URL_TIER in tests/test_safety.py, so the ftp claim is proven.
- F3 (refused attempts invisible): V5 does not change what passes or fails; summarize() gains safety.refused_attempts, counting calls with outcome refused on tools in their task's own forbid list (Result now carries that list), and _summary_line shows "K refused attempts" right after "N forbidden". EVAL.md documents that a policy refusal does not fail the task.
- F4 (broken report crashes history): V4 makes history() skip reports whose results is not a list (counted in skipped) and fall back to values computed from results when summary numbers are null or non-numeric, so one broken file no longer crashes the CLI or blanks the dashboard History panel.
- F5 (start button copy): V2 changes the Start button to "starting... (can take a few minutes)" and updates CHANGELOG.md to match the documented worst case of about four minutes.

## V6 dash scan (reliable-start..HEAD)

Scanned the 12 files in `git diff --name-only reliable-start..HEAD` (CHANGELOG.md, EVAL.md, PROGRESS-iris16.md, README.md, SECURITY.md, iris/autopilot.py, iris/dashboard/index.html, iris/eval.py, tests/test_autopilot.py, tests/test_dashboard.py, tests/test_eval.py, tests/test_safety.py) for U+2014 and U+2013 with Python: 0 lines contain either character. No files needed fixing.
