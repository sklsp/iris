# Iris policy refusals run (night-iris11)

- [x] P1. queue_remove versus queue_take (620 passed in 23.37s)
- [x] P2. open_url refuses in the policy layer (628 passed in 22.91s)
- [x] P3. a window that is not there says so (629 passed in 21.66s)
- [x] P4. eval tasks for refused URLs (629 passed in 23.63s)
- [x] P5. docs (629 passed in 22.28s)
- [x] P6. no dashes (8 files changed since queue-voice scanned with Python; 0 U+2014/U+2013 found)
- [x] P7. final check (629 passed in 22.64s)

## Policy refusals run result

- Commits ahead of queue-voice: 7 (P1 to P7; this commit included)
- Gates, last lines: ruff "All checks passed!"; mypy "Success: no issues found in 11 source files"; pytest "629 passed in 22.64s"
- New eval task ids: file_url, data_url
