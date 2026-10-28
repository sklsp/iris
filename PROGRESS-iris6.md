# Iris run iris6: honest tool errors (items E1 to E15)

Branch `errors` in W:/Iris. One commit per item, message `errors: <title>` plus the last pytest line.
Gates before every commit: ruff check, mypy, pytest with coverage floor.

- [x] E1. a returned error is journaled as an error (494 passed)
- [x] E2. the eval counts a forbidden call that reached the tool (497 passed)
- [x] E3. confirmed_ok stays honest (498 passed)
- [ ] E4. existing tasks that return error text
- [ ] E5. focus_gone may open the app
- [ ] E6. click_then_dialog gets one more call
- [ ] E7. an app that will not open
- [ ] E8. ComfyUI is down
- [ ] E9. a model load that fails must not unload on its own
- [ ] E10. journal_today counts returned errors
- [ ] E11. the dashboard counts returned errors
- [ ] E12. docs
- [ ] E13. README task count
- [ ] E14. no dashes
- [ ] E15. final check
