# Iris run iris6: honest tool errors (items E1 to E15)

Branch `errors` in W:/Iris. One commit per item, message `errors: <title>` plus the last pytest line.
Gates before every commit: ruff check, mypy, pytest with coverage floor.

- [x] E1. a returned error is journaled as an error (494 passed)
- [x] E2. the eval counts a forbidden call that reached the tool (497 passed)
- [x] E3. confirmed_ok stays honest (498 passed)
- [x] E4. existing tasks that return error text (see "Error fixtures" below)
- [x] E5. focus_gone may open the app (498 passed)
- [x] E6. click_then_dialog gets one more call (498 passed)
- [x] E7. an app that will not open (498 passed)
- [x] E8. ComfyUI is down (498 passed)
- [x] E9. a model load that fails must not unload on its own (498 passed)
- [x] E10. journal_today counts returned errors (499 passed)
- [x] E11. the dashboard counts returned errors (500 passed)
- [x] E12. docs (500 passed)
- [x] E13. README task count (500 passed)
- [x] E14. no dashes (500 passed)
- [x] E15. final check (500 passed)

## Error fixtures

E4: scanned every `fixtures` line in eval/tasks.toml for values starting with "error:" (str or list item).
One task found, zero changes needed:

- `focus_gone`: fixture `focus_window = "error: no window matches 'Spotify'"`. It has no `outcomes`
  entry for `focus_window`, so there is nothing to correct. No other task returns error text.

## No dashes

E14: scanned the 13 files from `git diff --name-only main..HEAD` for U+2014 and U+2013 with Python.
Found 3 em-dashes, all introduced by E12's docs (CHANGELOG.md line 10, DESIGN.md line 17); replaced
with a semicolon and parentheses. Rescan: 0 dashes in every file on the branch.

## Honest tool errors run result

- Commits ahead of main: 31 (this run's 15 item commits, plus the 16 eval-seq commits already on the
  branch when it was created; main is at PR #5)
- Gates, last lines: ruff `All checks passed!`; mypy `Success: no issues found in 10 source files`;
  pytest `500 passed in 17.54s` with "Required test coverage of 94.0% reached. Total coverage: 96.41%"
- New task count: 71 (was 68 at the start of this run, +3)
- New tasks: open_app_fails, comfy_down, load_fails
- Item 4 list: one existing task returned error text, zero changes needed: focus_gone with fixture
  `focus_window = "error: no window matches 'Spotify'"` and no outcomes entry for it; no other task
  returns error text.

