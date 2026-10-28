# Iris 9 progress: dashboard autopilot (items D1 to D14)

- [x] D1. remove and move queued prompts (560 passed in 21.33s)
- [x] D2. run progress for one repo (563 passed in 22.12s)
- [x] D3. GET /api/runs (565 passed in 24.07s)
- [x] D4. POST /api/queue/add, /delete and /move (592 passed in 26.30s)
- [x] D5. POST /api/cline/start (601 passed in 26.09s)
- [x] D6. the Autopilot tab: runs (601 passed in 23.21s)
- [x] D7. the Autopilot tab: start the next prompt (601 passed in 23.54s)
- [x] D8. the Autopilot tab: the queue (601 passed in 24.70s)
- [x] D9. tell Jay when a run is done (601 passed in 23.93s)
- [x] D10. a page test for the new parts (603 passed in 24.13s)
- [x] D11. the autopilot CLI and the page agree (603 passed in 24.68s)
- [x] D12. docs (603 passed in 24.13s)
- [x] D13. no dashes (603 passed in 24.43s)
  scan of git diff --name-only main..HEAD (11 files): 0 lines with U+2014 or U+2013; nothing to fix.
- [x] D14. final check (603 passed in 23.85s)

## Autopilot dashboard run result

Commits ahead of main: 14 on `dashboard-autopilot`, one per item (this commit is the 14th). The other
13, oldest first:

- 5c34610 D1 remove and move queued prompts
- 414d030 D2 run progress for one repo
- 7d14333 D3 GET /api/runs
- 82f559d D4 POST /api/queue/add, /delete and /move
- b3844be D5 POST /api/cline/start
- 3ce1da0 D6 the Autopilot tab shows runs
- fbcfc62 D7 the Autopilot tab: start the next prompt
- b92c535 D8 the Autopilot tab: the queue
- a393361 D9 tell Jay when a run is done (top banner, 30s poll, notifications)
- c460a97 D10 page test for the new autopilot UI
- 0582b1c D11 tag watch journal events with source=cli so the page can tell CLI from dashboard
- 64bf4f2 D12 docs
- c09b2b0 D13 no dashes

Gates, last line of each (run before this commit):
- ruff: All checks passed!
- mypy: Success: no issues found in 11 source files
- pytest: 603 passed in 23.85s (coverage floor holds; total coverage 96.66%)

New routes on the loopback dashboard (127.0.0.1:8765):
- GET /api/runs : one row per watched repo with its run progress and whether it is done
- POST /api/queue/add, /api/queue/delete, /api/queue/move : edit the prompt queue from the tab
- POST /api/cline/start : start the agent's next queued prompt through the Cline CLI

The four write routes need the page token (X-Iris-Token) plus a matching loopback Origin or Referer,
exactly like the model routes; every other method gets 405. A start additionally needs a click on its
button and a confirm dialog, so it never happens on its own.

What to click through in the browser:
1. Open http://127.0.0.1:8765 and click the Autopilot tab (the last tab in the bar).
2. Each watched repo shows as a run row: done or running, with progress; not-done runs first.
3. Below the runs is the queue: add a prompt, move one up or down, delete one; changes save at once.
4. On a done run click "Start next prompt"; read the confirm dialog and click OK to start it (Cancel does nothing).
5. Leave the page open while a run finishes: a done banner appears at the top with an "Open Autopilot" button, and if you ticked "Notify me" and allowed browser notifications you also get one.
