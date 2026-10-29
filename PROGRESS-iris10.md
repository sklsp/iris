# Iris queue voice run (night-iris10)

Branch: `queue-voice`. Items 1 to 9, one commit each. Gates before every commit: ruff check, mypy, pytest with coverage floor.

## Checklist

- [x] R1. queue_remove tool (610 passed in 23.72s)
- [x] R2. queue_move tool (618 passed in 21.84s)
- [x] R3. eval tasks for queue_remove and queue_move (618 passed in 22.66s)
- [x] R4. the run row fits a narrow window (618 passed in 22.34s)
- [ ] R5. the start button says why it waits
- [ ] R6. page tests for R4 and R5
- [ ] R7. docs
- [ ] R8. no dashes
- [ ] R9. final check
