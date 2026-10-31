# Iris self-extension B run (night-iris29)

- [x] B1. the policy module (1042 passed in 25.48s)
- [x] B2. a build happens in its own worktree (1052 passed in 25.80s)
- [x] B3. capability_build (1062 passed in 24.67s)
- [x] B4. capability_check (1078 passed in 25.75s)
- [x] B5. plans tell the builder where it works (1079 passed in 25.60s)
- [x] B6. eval tasks (1079 passed in 25.13s)
- [x] B7. docs (1079 passed in 25.21s)
- [x] B8. no dashes (1079 passed in 26.38s)
- [x] B9. final check (1079 passed in 25.34s)

## Self-extension B run result

Commits ahead of self-extend:

```
e8bbb15 B8: no dashes
ac987b8 B7: docs
f57fc3b B6: eval tasks
c1a683f B5: plans tell the builder where it works
c63607f Add capability_check: guard and gate a self-build branch before Jay reviews it.
e06106d B3: capability_build
772696d B2: a build happens in its own worktree
b8966c5 B1: the policy module
```

Gate last lines (final run):

- ruff: All checks passed!
- mypy: Success: no issues found in 15 source files
- pytest: 1079 passed in 25.34s (coverage floor holds, total 97.16%)
