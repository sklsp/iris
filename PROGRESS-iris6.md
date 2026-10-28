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
- [ ] E11. the dashboard counts returned errors
- [ ] E12. docs
- [ ] E13. README task count
- [ ] E14. no dashes
- [ ] E15. final check

## Error fixtures

E4: scanned every `fixtures` line in eval/tasks.toml for values starting with "error:" (str or list item).
One task found, zero changes needed:

- `focus_gone`: fixture `focus_window = "error: no window matches 'Spotify'"`. It has no `outcomes`
  entry for `focus_window`, so there is nothing to correct. No other task returns error text.

